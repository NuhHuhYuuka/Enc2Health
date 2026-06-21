# Hướng dẫn triển khai và quay video 3 kịch bản Enc²Health

Tài liệu này chỉ gồm thao tác kỹ thuật và checklist hình ảnh cần quay, không gồm
lời thoại.

## 1. Mức độ hoàn thành hiện tại

| Kịch bản | Trạng thái | Ghi chú chính |
|---|---|---|
| Lookup Patient bằng CCCD | Chạy được | JWT ES256, ABAC, DTE CCCD, ECIES P-384, Vault AppRole và mask cột đã có |
| AVG Billing - Normal TEE | Chạy được | Router đẩy `N` ciphertext sang Pool; Pool giải mã và dùng DuckDB in-memory tính AVG |
| AVG Billing - Software Fallback | Chạy được | Router giải mã và tính AVG ngoài Enclave; khóa đã được lấy từ Vault khi Router khởi động |
| SSE Search `ho khan` | Chạy được | Thực thi dưới dạng AND-search của hai HMAC token `ho` và `khan` |

Các giới hạn phải hiểu đúng khi quay:

- Pool hiện là triển khai TEE/Gramine mô phỏng. Chỉ gọi là RAM được SGX bảo vệ
  nếu máy Lan thực sự chạy SGX hardware và Gramine SGX.
- Số bản ghi AVG là `N` lấy từ MongoDB, không cố định đúng 200.
- Khi `SOFTWARE_KEY_SOURCE=vault`, Router tải khóa runtime từ Vault lúc khởi
  động. Khi fallback, Router sử dụng `gcm_dek` đang ở RAM của tiến trình ngoài
  Enclave.
- Tìm `ho khan` không dùng một token cho toàn cụm. Router thử token cụm trước;
  nếu không có, Router tạo token cho `ho` và `khan`, truy vấn bằng `$in`, giải mã
  posting lists rồi lấy giao hai tập kết quả.

## 2. Phân vai ba máy

| Máy | Dịch vụ | Cổng |
|---|---|---|
| Long / VM1 | MongoDB và seed EHR mã hóa | `27017` |
| Lan / VM2 | Vault + AppRole + Enclave/ECALL Pool | `8200`, `9091` |
| Nam / VM3 | IAM ES256 + Gateway/Router + UI | `8080`, `8000` |

Giá trị mạng hiện tại:

```bash
export LONG_IP=100.127.127.122
export LAN_IP=100.119.174.95
export NAM_IP=100.67.221.119
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net
export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net
```

Luồng tổng quát:

```text
Browser
  -> IAM Nam :8080
  -> Gateway Nam :8000
  -> MongoDB Long :27017
  -> Enclave Pool Lan :9091
  -> Vault Lan :8200
```

## 3. Đồng bộ phiên bản trước khi triển khai

Trên Long, Lan và Nam:

```bash
cd ~/Enc2Health
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
tailscale status
sudo ufw disable
```

Kiểm tra ba máy đang dùng cùng commit:

```bash
git rev-parse --short HEAD
```

Kết quả trên cả ba máy phải giống nhau.

## 4. Chuẩn bị HTTPS và mTLS

### 4.1. Lan lấy chứng thư HTTPS

```bash
cd ~/Enc2Health
source .venv/bin/activate
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net

sudo tailscale cert "$LAN_DOMAIN"
sudo chown "$USER:$USER" "$LAN_DOMAIN.crt" "$LAN_DOMAIN.key"
ls -l "$LAN_DOMAIN.crt" "$LAN_DOMAIN.key"
```

### 4.2. Nam lấy chứng thư HTTPS

```bash
cd ~/Enc2Health
source .venv/bin/activate
export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net

sudo tailscale cert "$NAM_DOMAIN"
sudo chown "$USER:$USER" "$NAM_DOMAIN.crt" "$NAM_DOMAIN.key"
ls -l "$NAM_DOMAIN.crt" "$NAM_DOMAIN.key"
```

### 4.3. Kiểm tra chứng thư client mTLS

Repo phải có:

```bash
ls -l certs/ca.crt certs/client.crt certs/client.key
```

Pool dùng `certs/ca.crt` để yêu cầu và kiểm tra client certificate. Router dùng
`certs/client.crt` và `certs/client.key` khi gọi Pool.

## 5. Chuẩn bị JWT ES256

Chỉ trên Nam:

```bash
cd ~/Enc2Health
source .venv/bin/activate

python3 crypto/data/generate_jwt_keys.py
chmod 600 crypto/data/keys/jwt_es256_private.pem
ls -l \
  crypto/data/keys/jwt_es256_private.pem \
  crypto/data/keys/jwt_es256_public.pem
```

Chép **chỉ public key** sang Lan:

```bash
scp crypto/data/keys/jwt_es256_public.pem \
  lan@100.119.174.95:~/Enc2Health/crypto/data/keys/jwt_es256_public.pem
```

Trên Lan:

```bash
ls -l ~/Enc2Health/crypto/data/keys/jwt_es256_public.pem
test ! -f ~/Enc2Health/crypto/data/keys/jwt_es256_private.pem
```

Lệnh `test` không in gì và trả exit code 0 là đúng: Lan không giữ private key
JWT.

## 6. Long / VM1: MongoDB và dữ liệu mã hóa

### 6.1. Khởi động MongoDB

```bash
cd ~/Enc2Health
source .venv/bin/activate

sudo systemctl enable --now docker
sudo docker rm -f enc2health-mongo 2>/dev/null || true
sudo docker run -d --name enc2health-mongo \
  -p 0.0.0.0:27017:27017 \
  --restart unless-stopped mongo:7
```

### 6.2. Seed lại dữ liệu

```bash
EHR_FORCE_RECREATE=1 \
MONGO_URI=mongodb://127.0.0.1:27017 \
python crypto/data/generate_ehr.py
```

Generator khóa cứng các dữ liệu phục vụ video:

- CCCD `05641395376` thuộc `dept=Tim_mach`.
- Ít nhất một hồ sơ khoa `Nhi` chứa cụm `ho khan`.
- Ít nhất một hồ sơ khoa `Noi` có mã bệnh `I01`.

### 6.3. Kiểm tra MongoDB

```bash
sudo docker exec enc2health-mongo \
  mongosh --quiet --eval 'db.adminCommand({ping:1})'

sudo docker exec enc2health-mongo \
  mongosh enc2health --quiet --eval \
  'printjson({
    patients: db.patient_records.countDocuments({}),
    sse_tokens: db.sse_index.countDocuments({}),
    cmnd_index: db.patient_records.getIndexes().some(x => x.name === "cmnd_dte_1"),
    sse_index: db.sse_index.getIndexes().some(x => x.name === "token_1")
  })'
```

Giữ terminal Long mở:

```bash
sudo docker logs -f enc2health-mongo
```

## 7. Lan / VM2: Vault và AppRole

### 7.1. Khởi động Vault

```bash
cd ~/Enc2Health
source .venv/bin/activate

pkill -f 'vault server -dev' 2>/dev/null || true
nohup vault server -dev \
  -dev-listen-address="0.0.0.0:8200" \
  -dev-root-token-id="enc2health-root-token" \
  > /tmp/vault.log 2>&1 &
sleep 3
```

### 7.2. Nạp khóa và tạo AppRole

```bash
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=enc2health-root-token

bash crypto/vault/setup_vault.sh

export VAULT_ROLE_ID="$(cat /tmp/enc2health-vault-role-id)"
export VAULT_SECRET_ID="$(cat /tmp/enc2health-vault-secret-id)"
```

Kiểm tra:

```bash
vault status
vault kv list enc2health/keypairs
vault kv list enc2health/dek
vault read auth/approle/role/enc2health-enclave/role-id
```

Không quay hoặc hiển thị giá trị `VAULT_SECRET_ID`.

## 8. Lan / VM2: Enclave Pool HTTPS + mTLS + AppRole

```bash
cd ~/Enc2Health
source .venv/bin/activate

export LONG_IP=100.127.127.122
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net
export VAULT_ROLE_ID="$(cat /tmp/enc2health-vault-role-id)"
export VAULT_SECRET_ID="$(cat /tmp/enc2health-vault-secret-id)"
unset VAULT_TOKEN

pkill -f enclave/ecall_pool.py 2>/dev/null || true

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
tail -40 /tmp/pool.log
```

Log bắt buộc có:

```text
Loaded key from Vault
DEK source: vault
Strict mode: True
TLS enabled
```

Log không được có:

```text
local dev file
explicit fallback
```

### 8.1. Chứng minh mTLS

Không có client certificate phải thất bại:

```bash
curl "https://$LAN_DOMAIN:9091/health"
```

Có client certificate phải thành công:

```bash
curl "https://$LAN_DOMAIN:9091/health" \
  --cert certs/client.crt \
  --key certs/client.key
```

Giữ terminal log Lan mở khi quay:

```bash
tail -f /tmp/pool.log /tmp/vault.log
```

## 9. Nam / VM3: IAM ES256

### 9.1. Khởi động IAM

```bash
cd ~/Enc2Health
source .venv/bin/activate
export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net

pkill -f 'uvicorn iam.main:app' 2>/dev/null || true

AUTH_JWT_PRIVATE_KEY="$PWD/crypto/data/keys/jwt_es256_private.pem" \
AUTH_JWT_PUBLIC_KEY="$PWD/crypto/data/keys/jwt_es256_public.pem" \
AUTH_JWT_ALLOW_KEYGEN=0 \
AUTH_JWT_ISS=enc2health \
AUTH_JWT_AUD=enc2health_services \
nohup .venv/bin/uvicorn iam.main:app \
  --host 0.0.0.0 \
  --port 8080 \
  --ssl-certfile "$NAM_DOMAIN.crt" \
  --ssl-keyfile "$NAM_DOMAIN.key" \
  > /tmp/iam.log 2>&1 &

sleep 3
curl "https://$NAM_DOMAIN:8080/health"
```

Kết quả phải có:

```json
{"status":"ok","alg":"ES256"}
```

### 9.2. Lấy service JWT cho Router gọi Pool

```bash
export ROUTER_SERVICE_JWT="$(
  curl -s "https://$NAM_DOMAIN:8080/token" \
    -H 'Content-Type: application/json' \
    -d '{"role":"service","sub":"router-service"}' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
)"

test -n "$ROUTER_SERVICE_JWT" && echo "Service JWT ready"
```

Không in toàn bộ token lên video.

## 10. Nam / VM3: Gateway/Router và UI

```bash
cd ~/Enc2Health
source .venv/bin/activate

export LONG_IP=100.127.127.122
export LAN_IP=100.119.174.95
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net
export NAM_DOMAIN=yuwkaa-vmware-1.tail4cc053.ts.net

pkill -f 'uvicorn router.main:app' 2>/dev/null || true

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
  --host 0.0.0.0 \
  --port 8000 \
  --ssl-certfile "$NAM_DOMAIN.crt" \
  --ssl-keyfile "$NAM_DOMAIN.key" \
  > /tmp/router.log 2>&1 &

sleep 5
tail -40 /tmp/router.log
curl "https://$NAM_DOMAIN:8000/nodes"
```

Mọi node phải có `status: ok`:

- Authentication Server / IAM.
- Query Router.
- MongoDB.
- Enclave Pool.
- Vault.

Mở UI:

```text
https://yuwkaa-vmware-1.tail4cc053.ts.net:8000/ui
```

## 11. Bố trí màn hình quay

Chuẩn bị bốn vùng:

1. Trình duyệt hiển thị UI trên Nam.
2. Terminal Nam chạy lệnh API và xem `/tmp/iam.log`, `/tmp/router.log`.
3. Terminal Lan xem `/tmp/pool.log`, `/tmp/vault.log`.
4. Terminal Long xem MongoDB hoặc `mongosh`.

Trước khi quay:

```bash
# Nam
curl "https://$NAM_DOMAIN:8000/nodes"

# Lan
tail -20 /tmp/pool.log

# Long
sudo docker ps
```

## 12. Kịch bản 1: Lookup Patient bằng CCCD

### 12.1. Chuẩn bị trạng thái

Trên Nam, đặt adaptive về bình thường:

```bash
curl -X POST \
  "https://$NAM_DOMAIN:8000/adaptive/simulate?pressure=0.50"
```

Mở log:

```bash
# Nam
tail -f /tmp/iam.log /tmp/router.log

# Lan
tail -f /tmp/pool.log /tmp/vault.log
```

### 12.2. Kiểm tra JWT ES256 và claim ABAC

Trên Nam:

```bash
export STAFF_JWT="$(
  curl -s "https://$NAM_DOMAIN:8080/token" \
    -H 'Content-Type: application/json' \
    -d '{
      "role":"admin_staff",
      "sub":"video-admin-staff",
      "job_title":"admin_staff",
      "clearance":"administrative",
      "purpose":"patient_admission"
    }' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
)"
```

Hiển thị header và payload, không hiển thị chữ ký:

```bash
JWT="$STAFF_JWT" python3 - <<'PY'
import base64, json, os
header, payload, _ = os.environ["JWT"].split(".")
decode = lambda value: json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
print("HEADER")
print(json.dumps(decode(header), indent=2, ensure_ascii=False))
print("PAYLOAD")
print(json.dumps(decode(payload), indent=2, ensure_ascii=False))
PY
```

Phải thấy:

```text
alg = ES256
role = admin_staff
job_title = admin_staff
clearance = administrative
purpose = patient_admission
```

### 12.3. Thực hiện trên UI

1. Chọn role `NV Hành chính`.
2. Bấm đăng nhập.
3. Mở mục tra cứu bệnh nhân/CCCD.
4. Nhập `05641395376`.
5. Bấm tra cứu.

Kết quả cần quay:

- Badge `TEE · Enclave`.
- CCCD và thông tin hành chính được hiển thị.
- Khoa điều trị là `Tim_mach`.
- `tom_tat_benh_an` hiển thị `Đã ẩn`.
- `phac_do_dieu_tri` hiển thị `Đã ẩn`.

Terminal Lan phải xuất hiện dòng tương tự:

```text
Loaded private key from Vault for dept=Tim_mach
```

### 12.4. Gọi API để lưu bằng chứng response

```bash
curl -s "https://$NAM_DOMAIN:8000/query" \
  -H "Authorization: Bearer $STAFF_JWT" \
  -H 'Content-Type: application/json' \
  -d '{
    "query_type":"lookup_patient",
    "filters":{"cmnd":"05641395376"}
  }' |
python3 -m json.tool
```

Kiểm tra:

```text
mode = tee
result.dept = Tim_mach
result.pii.tom_tat_benh_an = [MASKED]
result.pii.phac_do_dieu_tri = [MASKED]
```

### 12.5. Chứng minh MongoDB chỉ giữ ciphertext

Trên Nam, tính `cmnd_dte` bằng khóa lấy từ Vault:

```bash
export VAULT_ADDR="http://$LAN_IP:8200"
export VAULT_TOKEN=enc2health-root-token

export CMND_DTE="$(
  .venv/bin/python - <<'PY'
from crypto.crypto.dte import DTECipher
from crypto.vault.vault_client import get_dek
print(DTECipher(get_dek("dte_cmnd")).encrypt("05641395376", b"field:cmnd"))
PY
)"

echo "${CMND_DTE:0:40}..."
```

Trên Long, nhận giá trị `CMND_DTE` từ Nam rồi chạy:

```bash
export CMND_DTE='DAN_GIA_TRI_DAY_DU_TU_MAY_NAM_VAO_DAY'

sudo docker exec enc2health-mongo \
  mongosh enc2health --quiet --eval \
  'printjson(db.patient_records.findOne(
    {cmnd_dte: "'"$CMND_DTE"'"},
    {_id:0, patient_id:1, cmnd_dte:1, pii_enc:1, dept:1}
  ))'
```

Màn hình Long phải chỉ thấy `cmnd_dte`, `pii_enc` và `dept`, không thấy PII rõ.

## 13. Kịch bản 2A: AVG Billing ở Normal TEE Mode

### 13.1. Tạo JWT bác sĩ khoa Nội

Trên Nam:

```bash
export DOCTOR_NOI_JWT="$(
  curl -s "https://$NAM_DOMAIN:8080/token" \
    -H 'Content-Type: application/json' \
    -d '{"role":"doctor","sub":"doctor-noi","dept":"Noi"}' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
)"
```

### 13.2. Đặt áp lực EPC 50%

```bash
curl -s -X POST \
  "https://$NAM_DOMAIN:8000/adaptive/simulate?pressure=0.50" |
python3 -m json.tool
```

Phải thấy:

```text
system_mode = normal
pressure_ratio = 0.5
epc_threshold = 0.8
```

### 13.3. Thực hiện trên UI

1. Chọn role `doctor`.
2. Chọn khoa `Noi`.
3. Đăng nhập.
4. Chọn truy vấn `avg_vien_phi`.
5. Nhập mã bệnh `I01`.
6. Để trống tuổi nếu muốn lấy toàn bộ bệnh nhân phù hợp.
7. Bấm chạy truy vấn.

Kết quả cần quay:

- Badge `TEE · Enclave`.
- `Giới hạn khoa (ABAC) = Nội`.
- `Số bệnh nhân = N`, với `N > 0`.
- Security panel hiển thị số ciphertext đã đẩy.
- Chỉ một giá trị AVG được trả về.

### 13.4. Gọi API để hiện đầy đủ metadata

```bash
curl -s "https://$NAM_DOMAIN:8000/query" \
  -H "Authorization: Bearer $DOCTOR_NOI_JWT" \
  -H 'Content-Type: application/json' \
  -d '{
    "query_type":"avg_vien_phi",
    "filters":{"ma_benh":"I01"}
  }' |
python3 -m json.tool
```

Phải thấy:

```text
requested_mode = tee
mode = tee
abac_scope.khoa_phong = Noi
ciphertext_pushed = N
result.n_records = N
```

`ciphertext_pushed` và `result.n_records` phải bằng nhau.

Terminal Lan phải xuất hiện request `POST /query` và xử lý thành công. Pool dùng
DuckDB in-memory khi package `duckdb` có sẵn.

## 14. Kịch bản 2B: Software Fallback Mode

### 14.1. Đặt áp lực EPC 86%

```bash
curl -s -X POST \
  "https://$NAM_DOMAIN:8000/adaptive/simulate?pressure=0.86" |
python3 -m json.tool
```

Phải thấy:

```text
system_mode = fallback
pressure_ratio = 0.86
saturated = true
```

### 14.2. Thực hiện lại cùng truy vấn trên UI

Giữ nguyên:

```text
Role = doctor
Dept = Noi
Query = avg_vien_phi
Mã bệnh = I01
```

Bấm chạy lại.

Kết quả cần quay:

- Badge đỏ `Software Fallback Mode`.
- Cảnh báo dữ liệu được giải mã tạm thời trong RAM host ngoài Enclave.
- Kết quả AVG vẫn được trả về.
- `Giới hạn khoa (ABAC) = Nội`.

Trong lần fallback này, terminal Lan không có request `POST /query` mới cho AVG;
việc giải mã và tính toán diễn ra trên Nam.

### 14.3. Gọi API để chứng minh chuyển mode

```bash
curl -s "https://$NAM_DOMAIN:8000/query" \
  -H "Authorization: Bearer $DOCTOR_NOI_JWT" \
  -H 'Content-Type: application/json' \
  -d '{
    "query_type":"avg_vien_phi",
    "filters":{"ma_benh":"I01"}
  }' |
python3 -m json.tool
```

Phải thấy:

```text
requested_mode = tee
mode = software
abac_scope.khoa_phong = Noi
result.n_records = N
```

`ciphertext_pushed` không xuất hiện trong response fallback.

### 14.4. Khôi phục TEE

```bash
curl -s -X POST \
  "https://$NAM_DOMAIN:8000/adaptive/simulate?pressure=0.50" |
python3 -m json.tool
```

Phải thấy `system_mode = normal`.

## 15. Kịch bản 3: SSE Search `ho khan`

### 15.1. Tạo JWT bác sĩ khoa Nhi

```bash
export DOCTOR_NHI_JWT="$(
  curl -s "https://$NAM_DOMAIN:8080/token" \
    -H 'Content-Type: application/json' \
    -d '{"role":"doctor","sub":"doctor-nhi","dept":"Nhi"}' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
)"
```

### 15.2. Thực hiện trên UI

1. Chọn role `doctor`.
2. Chọn khoa `Nhi`.
3. Đăng nhập.
4. Mở mục tìm kiếm triệu chứng.
5. Nhập `ho khan`.
6. Bấm tìm kiếm.

Kết quả cần quay:

- Badge `SSE · Software`.
- Từ khóa `ho khan`.
- Số bệnh nhân khớp lớn hơn 0.
- Danh sách chỉ chứa bệnh nhân thuộc khoa `Nhi`.
- Token hiển thị dạng phrase search nhiều token.

### 15.3. Gọi API

```bash
curl -s "https://$NAM_DOMAIN:8000/search" \
  -H "Authorization: Bearer $DOCTOR_NHI_JWT" \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"ho khan","limit":20}' |
python3 -m json.tool
```

Phải thấy:

```text
mode = software
abac_scope.khoa_phong = Nhi
result.count > 0
result.postings[*].dept = Nhi
```

Với cụm `ho khan`, `result.token` có thể là `null` vì Router đang dùng hai token
thành phần. Đây là hành vi đúng của implementation hiện tại.

### 15.4. Chứng minh MongoDB chỉ giữ token và posting list mã hóa

Trên Nam, tính hai token bằng khóa SSE lấy từ Vault:

```bash
export VAULT_ADDR="http://$LAN_IP:8200"
export VAULT_TOKEN=enc2health-root-token

.venv/bin/python - <<'PY'
from crypto.crypto.sse import StaticSSECipher
from crypto.vault.vault_client import get_dek
sse = StaticSSECipher(get_dek("sse_key"))
for keyword in ("ho", "khan"):
    print(keyword, sse.token(keyword))
PY
```

Trên Long, dùng hai token vừa in:

```bash
sudo docker exec -it enc2health-mongo mongosh enc2health
```

Trong `mongosh`:

```javascript
db.sse_index.find(
  {token: {$in: ["TOKEN_HO", "TOKEN_KHAN"]}},
  {_id: 0, token: 1, postings_enc: 1, n_records: 1}
).pretty()
```

Màn hình Long chỉ được thấy HMAC token, `postings_enc` và số lượng; không thấy
Patient ID rõ.

## 16. Thứ tự quay video đề xuất

1. Quay `git rev-parse --short HEAD` trên ba máy.
2. Quay `/nodes` cho thấy mọi dịch vụ `ok`.
3. Quay kiểm tra mTLS: không cert thất bại, có cert thành công.
4. Quay Kịch bản 1 trên UI, JWT decoded và log Pool lấy private key Tim_mach.
5. Quay Kịch bản 2A ở 50%, response `mode=tee`.
6. Quay Kịch bản 2B ở 86%, response `mode=software` và cảnh báo đỏ.
7. Quay khôi phục về 50%.
8. Quay Kịch bản 3 với `ho khan`, response và dữ liệu mã hóa trong MongoDB.
9. Quay lại `/nodes` xác nhận hệ thống vẫn hoạt động.

## 17. Checklist trước khi bấm quay

Long:

```bash
sudo docker ps
sudo docker exec enc2health-mongo \
  mongosh --quiet --eval 'db.adminCommand({ping:1})'
```

Lan:

```bash
vault status
test -s /tmp/enc2health-vault-role-id
test -s /tmp/enc2health-vault-secret-id
tail -30 /tmp/pool.log
```

Nam:

```bash
curl "https://$NAM_DOMAIN:8080/health"
curl "https://$NAM_DOMAIN:8000/nodes"
curl "https://$NAM_DOMAIN:8000/adaptive"
```

Không hiển thị trong video:

- JWT đầy đủ gồm phần chữ ký.
- `jwt_es256_private.pem`.
- `VAULT_SECRET_ID`.
- Vault root token ngoài các lệnh demo đã chuẩn bị.
- `client.key`.
