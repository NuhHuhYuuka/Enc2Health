# Demo 3 kịch bản trên 3 VM qua Tailscale HTTPS

## 1. Phân vai mới

| Máy | Dịch vụ | Cổng |
|---|---|---|
| Long / VM1 | MongoDB, sinh dữ liệu EHR mã hóa | `27017` |
| Lan / VM2 | Vault + AppRole, Enclave/ECALL Pool | `8200`, `9091` |
| Nam / VM3 | Authentication Server ES256, Gateway/Router, UI | `8080`, `8000` |

Luồng tổng quát:

```text
Browser
  -> Authentication Nam :8080 (phát JWT ES256)
  -> Gateway Nam :8000 (verify public key + ABAC)
  -> MongoDB Long :27017
  -> Enclave Lan :9091
  -> Vault Lan :8200
```

Giá trị demo hiện tại:

```bash
export LONG_IP=100.127.127.122
export LAN_IP=100.119.174.95
export NAM_IP=100.67.221.119
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net
export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net
```

HTTPS phải dùng domain `.ts.net`, không dùng IP `100.x` trong URL.

## 2. Chuẩn bị chung

Trên cả ba máy:

```bash
cd ~/Enc2Health
source .venv/bin/activate
tailscale status
sudo ufw disable
```

Lan và Nam lấy chứng thư HTTPS:

```bash
# Chạy trên Lan
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net
sudo tailscale cert "$LAN_DOMAIN"
sudo chown "$USER:$USER" "$LAN_DOMAIN.crt" "$LAN_DOMAIN.key"

# Chạy trên Nam
export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net
sudo tailscale cert "$NAM_DOMAIN"
sudo chown "$USER:$USER" "$NAM_DOMAIN.crt" "$NAM_DOMAIN.key"
```

Chỉ trên Nam, tạo cặp khóa ký JWT ES256:

```bash
python3 crypto/data/generate_jwt_keys.py
chmod 600 crypto/data/keys/jwt_es256_private.pem
```

Chép **chỉ public key** sang Lan:

```bash
ssh lan@100.119.174.95 'mkdir -p ~/Enc2Health/crypto/data/keys'
scp crypto/data/keys/jwt_es256_public.pem \
  lan@100.119.174.95:~/Enc2Health/crypto/data/keys/jwt_es256_public.pem
```

## 3. Long / VM1: chỉ chạy MongoDB và seed dữ liệu

```bash
cd ~/Enc2Health
source .venv/bin/activate

sudo systemctl enable --now docker
sudo docker rm -f enc2health-mongo 2>/dev/null || true
sudo docker run -d --name enc2health-mongo \
  -p 0.0.0.0:27017:27017 \
  --restart unless-stopped mongo:7

FORCE_RECREATE=1 EHR_FORCE_RECREATE=1 \
MONGO_URI=mongodb://127.0.0.1:27017 \
python crypto/data/generate_ehr.py

sudo docker exec enc2health-mongo \
  mongosh --quiet --eval 'db.adminCommand({ping:1})'
```

Long không chạy Vault.

## 4. Lan / VM2: chạy Vault + AppRole

```bash
cd ~/Enc2Health
source .venv/bin/activate

pkill -f 'vault server -dev' 2>/dev/null || true
nohup vault server -dev \
  -dev-listen-address="0.0.0.0:8200" \
  -dev-root-token-id="enc2health-root-token" \
  > /tmp/vault.log 2>&1 &
sleep 3

export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=enc2health-root-token
bash crypto/vault/setup_vault.sh

export VAULT_ROLE_ID="$(cat /tmp/enc2health-vault-role-id)"
export VAULT_SECRET_ID="$(cat /tmp/enc2health-vault-secret-id)"
vault status
```

`setup_vault.sh` nạp các khóa `gcm_dek`, DTE, ORE, SSE, private key ECC P-384
theo khoa và tạo AppRole `enc2health-enclave`.

## 5. Lan / VM2: chạy Enclave Pool

```bash
cd ~/Enc2Health
source .venv/bin/activate

export LONG_IP=100.127.127.122
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net
export VAULT_ROLE_ID="$(cat /tmp/enc2health-vault-role-id)"
export VAULT_SECRET_ID="$(cat /tmp/enc2health-vault-secret-id)"

AUTH_JWT_PUBLIC_KEY="$PWD/crypto/data/keys/jwt_es256_public.pem" \
AUTH_JWT_ALLOW_KEYGEN=0 \
AUTH_JWT_ISS=enc2health \
AUTH_JWT_AUD=enc2health_services \
T8_POOL_HOST=0.0.0.0 \
T8_POOL_DATA_MODE=mongo \
T8_STRICT_MODE=1 \
T8_ALLOW_LOCAL_KEY_FALLBACK=0 \
T8_SSL_CERT="$LAN_DOMAIN.crt" \
T8_SSL_KEY="$LAN_DOMAIN.key" \
T8_SSL_CA="$PWD/certs/ca.crt" \
SOFTWARE_MONGO_TIMEOUT_MS=5000 \
MONGO_URI="mongodb://$LONG_IP:27017" \
VAULT_ADDR=http://127.0.0.1:8200 \
VAULT_ROLE_ID="$VAULT_ROLE_ID" \
VAULT_SECRET_ID="$VAULT_SECRET_ID" \
nohup .venv/bin/python enclave/ecall_pool.py > /tmp/pool.log 2>&1 &

sleep 5
tail -30 /tmp/pool.log
curl "https://$LAN_DOMAIN:9091/health" \
  --cert certs/client.crt --key certs/client.key
```

Log phải có `Loaded key from Vault` và không được có `local dev fallback`.

## 6. Nam / VM3: chạy Authentication Server ES256

```bash
cd ~/Enc2Health
source .venv/bin/activate

export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net

AUTH_JWT_PRIVATE_KEY="$PWD/crypto/data/keys/jwt_es256_private.pem" \
AUTH_JWT_PUBLIC_KEY="$PWD/crypto/data/keys/jwt_es256_public.pem" \
AUTH_JWT_ISS=enc2health \
AUTH_JWT_AUD=enc2health_services \
nohup .venv/bin/uvicorn iam.main:app \
  --host 0.0.0.0 --port 8080 \
  --ssl-certfile "$NAM_DOMAIN.crt" \
  --ssl-keyfile "$NAM_DOMAIN.key" \
  > /tmp/iam.log 2>&1 &

sleep 3
curl "https://$NAM_DOMAIN:8080/health"
```

Lấy service JWT cho Router gọi Enclave:

```bash
export ROUTER_SERVICE_JWT="$(
  curl -s "https://$NAM_DOMAIN:8080/token" \
    -H 'Content-Type: application/json' \
    -d '{"username":"router-service","role":"service"}' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
)"
```

## 7. Nam / VM3: chạy Gateway/Router + UI

Router lấy DTE/SSE/GCM key từ Vault trên Lan. Điều này cũng làm cho Software
Fallback đúng mô tả: `gcm_dek` được tải vào RAM tiến trình Gateway ngoài TEE.

Với Vault dev mode, dùng token root cho Nam để demo. Khi triển khai thật cần một
AppRole riêng, policy tối thiểu cho Gateway.

```bash
cd ~/Enc2Health
source .venv/bin/activate

export LONG_IP=100.127.127.122
export LAN_IP=100.119.174.95
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net
export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net

AUTH_JWT_PUBLIC_KEY="$PWD/crypto/data/keys/jwt_es256_public.pem" \
AUTH_JWT_ALLOW_KEYGEN=0 \
AUTH_JWT_ISS=enc2health \
AUTH_JWT_AUD=enc2health_services \
AUTH_JWT="$ROUTER_SERVICE_JWT" \
IAM_URL="https://$NAM_DOMAIN:8080" \
ABAC_REQUIRE_DEPT=1 \
ABAC_REQUIRE_PURPOSE=1 \
ROUTER_TEE_PUSH_CIPHERTEXT=1 \
ENC2HEALTH_DEV_UI=1 \
SOFTWARE_STRICT_MODE=1 \
SOFTWARE_KEY_SOURCE=vault \
SOFTWARE_MONGO_TIMEOUT_MS=5000 \
MONGO_URI="mongodb://$LONG_IP:27017" \
ECALL_POOL_URL="https://$LAN_DOMAIN:9091" \
ROUTER_CLIENT_CERT="$PWD/certs/client.crt" \
ROUTER_CLIENT_KEY="$PWD/certs/client.key" \
VAULT_ADDR="http://$LAN_IP:8200" \
VAULT_TOKEN=enc2health-root-token \
nohup .venv/bin/uvicorn router.main:app \
  --host 0.0.0.0 --port 8000 \
  --ssl-certfile "$NAM_DOMAIN.crt" \
  --ssl-keyfile "$NAM_DOMAIN.key" \
  > /tmp/router.log 2>&1 &

sleep 5
tail -30 /tmp/router.log
curl "https://$NAM_DOMAIN:8000/nodes"
```

Mở UI:

```text
https://yuwkaa-vmware-1.tail4cc053.ts.net:8000/ui
```

## 8. Kịch bản 1: Lookup Patient bằng CCCD

Trên UI:

1. Chọn `NV Hành chính` (`admin_staff`) và đăng nhập.
2. Vào `Tìm bệnh nhân`.
3. Nhập CCCD `05641395376`.
4. Bấm tra cứu.

Kết quả cần chỉ ra:

- Auth Server phát JWT `ES256` chứa `job_title=admin_staff`,
  `clearance=administrative`, `purpose=patient_admission`.
- Gateway verify bằng public key, mã hóa DTE CCCD và query MongoDB Long.
- Gateway gửi `pii_enc` và `dept=Tim_mach` tới Enclave Lan.
- Enclave đăng nhập Vault bằng AppRole, lấy private key ECC P-384 khoa Tim mạch.
- PII được giải mã trong Pool; Gateway mask `tom_tat_benh_an` và
  `phac_do_dieu_tri` thành `[MASKED]`.

Theo dõi log:

```bash
# Nam
tail -f /tmp/iam.log /tmp/router.log

# Lan
tail -f /tmp/pool.log /tmp/vault.log
```

## 9. Kịch bản 2A: AVG Billing ở Normal TEE Mode

Đăng nhập UI với:

```text
Role: doctor
Dept: Noi
Query: avg_vien_phi
Mã bệnh: I01
```

Đặt EPC mô phỏng ở 50%:

```bash
curl -X POST \
  "https://$NAM_DOMAIN:8000/adaptive/simulate?pressure=0.50"
```

Chạy truy vấn. Kết quả phải có:

```text
mode = tee
abac_scope.khoa_phong = Noi
ciphertext_pushed > 0
```

Luồng:

```text
Gateway Nam -> Mongo Long -> N vien_phi_enc
Gateway Nam -> Enclave Lan -> Vault Lan -> DuckDB AVG
Enclave Lan -> Gateway Nam -> một kết quả trung bình
```

## 10. Kịch bản 2B: Software Fallback

Ép EPC lên 86%:

```bash
curl -X POST \
  "https://$NAM_DOMAIN:8000/adaptive/simulate?pressure=0.86"
```

Chạy lại đúng truy vấn AVG. UI phải hiện:

```text
Fallback · Software
```

Response phải có:

```text
requested_mode = tee
mode = software
```

Trong mode này Gateway Nam dùng `gcm_dek` đã tải từ Vault Lan, giải mã các
`vien_phi_enc` trong RAM host và tính AVG ngoài Enclave. Đây là đánh đổi
availability-security và UI hiển thị cảnh báo màu đỏ/cam.

Khôi phục TEE:

```bash
curl -X POST \
  "https://$NAM_DOMAIN:8000/adaptive/simulate?pressure=0.50"
```

## 11. Kịch bản 3: SSE Search

Trên UI:

1. Đăng nhập `doctor`, chọn khoa `Nhi`.
2. Vào tìm kiếm từ khóa.
3. Nhập `ho khan`.
4. Thực hiện tìm kiếm.

Hoặc gọi API:

```bash
export DOCTOR_JWT="$(
  curl -s "https://$NAM_DOMAIN:8080/token" \
    -H 'Content-Type: application/json' \
    -d '{"username":"doctor-nhi","role":"doctor","dept":"Nhi"}' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
)"

curl "https://$NAM_DOMAIN:8000/search" \
  -H "Authorization: Bearer $DOCTOR_JWT" \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"ho khan","limit":20}'
```

Gateway tạo HMAC token, MongoDB trả `postings_enc`, Gateway giải mã posting list
và lọc theo `dept=Nhi` do ABAC tiêm vào. Generator khóa cứng ít nhất một hồ sơ
khoa Nhi chứa cụm `ho khan`, nên sau khi seed lại kết quả không được bằng 0.

## 12. Demo CLI xuyên ba máy

```bash
AUTH_URL="https://$NAM_DOMAIN:8080" \
ROUTER_URL="https://$NAM_DOMAIN:8000" \
python3 scripts/demo_nodes.py avg_vien_phi I01 0 doctor
```

## 13. Checklist trước khi quay

Long:

```bash
sudo docker ps
sudo docker exec enc2health-mongo \
  mongosh --quiet --eval 'db.adminCommand({ping:1})'
```

Lan:

```bash
vault status
tail -20 /tmp/vault.log
tail -30 /tmp/pool.log
```

Nam:

```bash
curl "https://$NAM_DOMAIN:8080/health"
curl "https://$NAM_DOMAIN:8000/nodes"
tail -20 /tmp/iam.log
tail -30 /tmp/router.log
```

Không chiếu private key JWT, Vault token, AppRole `secret_id` hoặc DEK lên màn
hình khi quay.
