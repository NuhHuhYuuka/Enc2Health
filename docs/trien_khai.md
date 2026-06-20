# Kịch bản triển khai 3 VM qua Tailscale HTTPS (ES256 + IAM + ABAC)

## Phân vai

| Máy | Người | Chạy gì |
|---|---|---|
| VM1 | Long | MongoDB + sinh dữ liệu EHR mã hóa |
| VM2 | Lan | ECALL Pool / Enclave TEE HTTPS + HashiCorp Vault |
| VM3 | Nam | IAM (Auth Server, ký JWT ES256) + Query Router HTTPS + UI |

Khác bản cũ:
- Xác thực dùng **JWT ES256 (ECDSA P-256)**, do node **IAM** ký; Router/Pool chỉ **verify** bằng public key (không còn `AUTH_JWT_SECRET` HS256).
- Phân quyền dùng **một engine ABAC duy nhất** (RBAC đã gộp vào ABAC).
- **Vault chuyển sang máy Lan** (đặt cạnh Enclave để cấp DEK).

## Biến cần ghi lại

```text
LONG_TAILSCALE_IP = 100.127.127.122
LAN_TAILSCALE_IP  = 100.119.174.95
NAM_TAILSCALE_IP  = 100.67.221.119
LAN_DOMAIN        = lan-bravo-15-b5dd.tail4cc053.ts.net
NAM_DOMAIN        = yuwkaa-vmware-1.tail4cc053.ts.net
```

Lưu ý: HTTPS Tailscale phải truy cập bằng domain `.ts.net`, không dùng IP `100.x.y.z` cho URL HTTPS.

## 0. Kiểm tra Tailscale trên cả 3 VM

Long, Lan, Nam đều chạy:

```bash
tailscale status
tailscale ip -4
tailscale cert
sudo ufw disable
```

Ghi lại:

```text
Long: LONG_TAILSCALE_IP = 100.127.127.122
Lan:  LAN_TAILSCALE_IP  = 100.119.174.95
Nam:  NAM_TAILSCALE_IP  = 100.67.221.119
Lan:  LAN_DOMAIN        = lan-bravo-15-b5dd.tail4cc053.ts.net
Nam:  NAM_DOMAIN        = yuwkaa-vmware-1.tail4cc053.ts.net
```

## 0.1. Lan / VM2 - xin cert HTTPS Tailscale

```bash
cd ~/Enc2Health
source .venv/bin/activate

export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net

tailscale cert "$LAN_DOMAIN"
ls -l "$LAN_DOMAIN.crt" "$LAN_DOMAIN.key"
```

Nếu lỗi quyền ghi file:

```bash
sudo tailscale cert "$LAN_DOMAIN"
sudo chown "$USER:$USER" "$LAN_DOMAIN.crt" "$LAN_DOMAIN.key"
ls -l "$LAN_DOMAIN.crt" "$LAN_DOMAIN.key"
```

## 0.2. Nam / VM3 - xin cert HTTPS Tailscale

```bash
cd ~/Enc2Health
source .venv/bin/activate

export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net

tailscale cert "$NAM_DOMAIN"
ls -l "$NAM_DOMAIN.crt" "$NAM_DOMAIN.key"
```

Nếu lỗi quyền ghi file:

```bash
sudo tailscale cert "$NAM_DOMAIN"
sudo chown "$USER:$USER" "$NAM_DOMAIN.crt" "$NAM_DOMAIN.key"
ls -l "$NAM_DOMAIN.crt" "$NAM_DOMAIN.key"
```

## 0.3. Sinh và đồng bộ khóa JWT ES256

JWT ES256 = 1 cặp khóa private/public. Sinh **một lần ở Nam**, rồi đưa **public key** sang Lan.
Không để mỗi máy tự sinh (sẽ ra khóa khác nhau, verify fail 401).

Nam / VM3:

```bash
cd ~/Enc2Health
source .venv/bin/activate

python3 crypto/data/generate_jwt_keys.py
ls -l crypto/data/keys/jwt_es256_private.pem crypto/data/keys/jwt_es256_public.pem
```

Đưa public key sang Lan (chọn 1 cách):

```bash
# Cách A - scp qua Tailscale
scp crypto/data/keys/jwt_es256_public.pem \
  "$USER@100.119.174.95:~/Enc2Health/crypto/data/keys/"

# Cách B - commit & push, rồi Lan git pull (như nhóm vẫn đồng bộ key)
```

Long (chỉ chạy Mongo) không cần khóa JWT.

## 1. Long / VM1 - chạy MongoDB và sinh dữ liệu mã hóa

```bash
cd ~/Enc2Health
source .venv/bin/activate

# 1.1. Khởi chạy MongoDB
sudo systemctl enable --now docker
sudo docker rm -f enc2health-mongo 2>/dev/null || true
sudo docker run -d --name enc2health-mongo \
  -p 0.0.0.0:27017:27017 \
  --restart unless-stopped mongo:7

# 1.2. Sinh dữ liệu EHR giả lập đã mã hóa
FORCE_RECREATE=1 EHR_FORCE_RECREATE=1 python crypto/data/generate_ehr.py

echo "MongoDB Long ready at $(tailscale ip -4):27017"
```

Kiểm tra Long:

```bash
sudo docker ps
tailscale ip -4
```

Tuỳ chọn, xem thử dữ liệu trong Mongo:

```bash
sudo docker exec -it enc2health-mongo mongosh
```

Trong `mongosh`:

```javascript
use enc2health
db.patient_records.findOne()
exit
```

## 2. Lan / VM2 - chạy Vault và Enclave ECALL Pool bằng HTTPS

Thay biến:

```text
LONG_TAILSCALE_IP = IP Tailscale của Long
LAN_DOMAIN        = domain Tailscale của Lan
```

```bash
cd ~/Enc2Health
source .venv/bin/activate

export LONG_TAILSCALE_IP=100.127.127.122
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net

# 2.1. Khởi chạy Vault dev mode
pkill -f 'vault server -dev' 2>/dev/null || true
nohup vault server -dev \
  -dev-listen-address="0.0.0.0:8200" \
  -dev-root-token-id="enc2health-root-token" > /tmp/vault.log 2>&1 &
sleep 3

# 2.2. Setup Vault secrets (DEK + keypair khoa)
export VAULT_ADDR='http://127.0.0.1:8200'
export VAULT_TOKEN='enc2health-root-token'
bash crypto/vault/setup_vault.sh

# 2.3. Khởi chạy Enclave ECALL Pool (HTTPS)
AUTH_JWT_ALLOW_KEYGEN=0 \
T8_POOL_HOST=0.0.0.0 \
T8_ALLOW_LOCAL_KEY_FALLBACK=0 \
T8_SSL_CERT="$LAN_DOMAIN.crt" \
T8_SSL_KEY="$LAN_DOMAIN.key" \
MONGO_URI="mongodb://$LONG_TAILSCALE_IP:27017" \
VAULT_ADDR="http://127.0.0.1:8200" \
VAULT_TOKEN=enc2health-root-token \
nohup .venv/bin/python enclave/ecall_pool.py > /tmp/pool.log 2>&1 &

sleep 4
tail -20 /tmp/pool.log

echo "Vault Lan ready at $(tailscale ip -4):8200"
echo "Pool Lan ready at https://$LAN_DOMAIN:9091"
```

Kiểm tra Lan:

```bash
vault status
tail -20 /tmp/pool.log
curl "https://$LAN_DOMAIN:9091/health"
```

> `AUTH_JWT_ALLOW_KEYGEN=0`: node verify-only, không tự sinh khóa JWT (phải đã có public key ở mục 0.3).
> Pool log cần hiện `DEK source: vault` (vì `T8_ALLOW_LOCAL_KEY_FALLBACK=0`). Nếu kẹt, tạm đổi `=1`.

## 3. Nam / VM3 - chạy IAM rồi Query Router + UI bằng HTTPS

Thay biến:

```text
LONG_TAILSCALE_IP = IP Tailscale của Long
LAN_TAILSCALE_IP  = IP Tailscale của Lan
LAN_DOMAIN        = domain Tailscale của Lan
NAM_DOMAIN        = yuwkaa-vmware-1.tail4cc053.ts.net
```

```bash
cd ~/Enc2Health
source .venv/bin/activate

export LONG_TAILSCALE_IP=100.127.127.122
export LAN_TAILSCALE_IP=100.119.174.95
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net
export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net

# 3.1. Node IAM (Auth Server) - node DUY NHẤT ký JWT ES256
nohup .venv/bin/uvicorn iam.main:app \
  --host 127.0.0.1 --port 8080 > /tmp/iam.log 2>&1 &
sleep 3
curl -s http://127.0.0.1:8080/health

# 3.2. Query Router + UI (chỉ VERIFY token, IAM_URL trỏ tới IAM)
IAM_URL=http://127.0.0.1:8080 \
ROUTER_TEE_PUSH_CIPHERTEXT=1 \
ENC2HEALTH_DEV_UI=1 \
SOFTWARE_MONGO_TIMEOUT_MS=3000 \
MONGO_URI="mongodb://$LONG_TAILSCALE_IP:27017" \
ECALL_POOL_URL="https://$LAN_DOMAIN:9091" \
VAULT_ADDR="http://$LAN_TAILSCALE_IP:8200" \
VAULT_TOKEN=enc2health-root-token \
nohup .venv/bin/uvicorn router.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --ssl-certfile "$NAM_DOMAIN.crt" \
  --ssl-keyfile "$NAM_DOMAIN.key" \
  > /tmp/router.log 2>&1 &

sleep 5
tail -20 /tmp/router.log

echo "IAM Nam ready at 127.0.0.1:8080"
echo "Router + UI Nam ready at https://$NAM_DOMAIN:8000"
```

Kiểm tra Nam:

```bash
tail -20 /tmp/iam.log
tail -20 /tmp/router.log
curl "https://$NAM_DOMAIN:8000/nodes"
```

## 4. Nam / VM3 - kiểm tra các node

```bash
curl "https://$NAM_DOMAIN:8000/nodes"
```

Kết quả mong đợi:

```text
IAM ok
MongoDB ok
Vault ok
Enclave/Pool ok
Router ok
```

## 5. Nam / VM3 - demo luồng truy vấn qua 3 máy

```bash
cd ~/Enc2Health
source .venv/bin/activate

export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net

ROUTER_URL="https://$NAM_DOMAIN:8000" \
python3 scripts/demo_nodes.py avg_vien_phi I01 60 admin
```

Luồng cần quay:

```text
Client -> IAM Nam (cấp JWT ES256) -> Router Nam HTTPS -> Mongo Long -> Enclave Lan HTTPS -> Vault Lan -> Router Nam -> Client
```

## 6. Nam hoặc laptop host - mở UI HTTPS

Mở trình duyệt:

```text
https://yuwkaa-vmware-1.tail4cc053.ts.net:8000/ui
```

Thao tác trên UI:

```text
Role: admin
Query: avg_vien_phi
Filter mã bệnh: I01 hoặc E11
Tuổi: >= 60
```

## 7. Nam / VM3 - demo xác thực JWT ES256

```bash
export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net

# 7.1. Không token -> 401
curl -s -o /dev/null -w "%{http_code}\n" -X POST "https://$LAN_DOMAIN:9091/query" -d '{}'

# 7.2. Token giả -> 401
curl -s -X POST "https://$LAN_DOMAIN:9091/query" \
  -H "Authorization: Bearer fake.token.x" -d '{}'

# 7.3. Lấy token thật từ IAM rồi gọi Router -> 200
TOK=$(curl -s -X POST http://127.0.0.1:8080/token \
  -H 'Content-Type: application/json' \
  -d '{"role":"doctor","dept":"Tim_mach"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST "https://$NAM_DOMAIN:8000/query" \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"query_type":"count","filters":{}}'
```

## 8. Nam / VM3 - demo phân quyền ABAC

```bash
cd ~/Enc2Health
source .venv/bin/activate

python3 scripts/demo_abac.py
```

## 9. Nam / VM3 - demo Adaptive Fallback

```bash
cd ~/Enc2Health
source .venv/bin/activate

python3 scripts/demo_adaptive.py
```

## 10. Lan + Nam - chứng minh node độc lập

Lan / VM2 tắt Enclave:

```bash
pkill -f enclave/ecall_pool.py
```

Nam / VM3 kiểm tra lại node:

```bash
export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net
curl "https://$NAM_DOMAIN:8000/nodes"
```

Kết quả mong đợi:

```text
Enclave/Pool down
IAM ok
MongoDB ok
Vault ok
Router ok
```

Lan / VM2 bật lại Enclave:

```bash
cd ~/Enc2Health
source .venv/bin/activate

export LONG_TAILSCALE_IP=100.127.127.122
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net

AUTH_JWT_ALLOW_KEYGEN=0 \
T8_POOL_HOST=0.0.0.0 \
T8_ALLOW_LOCAL_KEY_FALLBACK=0 \
T8_SSL_CERT="$LAN_DOMAIN.crt" \
T8_SSL_KEY="$LAN_DOMAIN.key" \
MONGO_URI="mongodb://$LONG_TAILSCALE_IP:27017" \
VAULT_ADDR="http://127.0.0.1:8200" \
VAULT_TOKEN=enc2health-root-token \
nohup .venv/bin/python enclave/ecall_pool.py > /tmp/pool.log 2>&1 &

sleep 4
tail -20 /tmp/pool.log
```

## 11. Checklist trước khi quay

Long / VM1:

```bash
tailscale ip -4
sudo docker ps
```

Lan / VM2:

```bash
ls -l crypto/data/keys/jwt_es256_public.pem
ls -l "$LAN_DOMAIN.crt" "$LAN_DOMAIN.key"
vault status
tail -20 /tmp/pool.log
curl "https://$LAN_DOMAIN:9091/health"
```

Nam / VM3:

```bash
export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net
ls -l crypto/data/keys/jwt_es256_private.pem
ls -l "$NAM_DOMAIN.crt" "$NAM_DOMAIN.key"
curl -s http://127.0.0.1:8080/health
tail -20 /tmp/router.log
curl "https://$NAM_DOMAIN:8000/nodes"
```

Lệnh demo chính:

```bash
export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net
ROUTER_URL="https://$NAM_DOMAIN:8000" \
python3 scripts/demo_nodes.py avg_vien_phi I01 60 admin
```
