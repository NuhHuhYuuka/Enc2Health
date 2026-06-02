# Kịch bản demo 3 VM

## Phân vai

| Máy | Người | Chạy gì |
|---|---|---|
| VM1 | Long | MongoDB + sinh dữ liệu EHR mã hóa |
| VM2 | Lan | ECALL Pool / Enclave TEE |
| VM3 | Nam | Query Router + UI + demo client |

Ký hiệu IP:

```text
VM1 Long = LONG_IP
VM2 Lan  = LAN_IP
VM3 Nam  = NAM_IP
```

Thay `LONG_IP`, `LAN_IP`, `NAM_IP` bằng IP thật khi quay.

## 0. Kiểm tra IP trên cả 3 VM

Long, Lan, Nam đều chạy:

```bash
hostname -I
sudo ufw disable
```

## 1. Long / VM1 - chạy MongoDB, Vault và sinh dữ liệu mã hóa

```bash
cd ~/Enc2Health
source .venv/bin/activate

# 1.1 Khởi chạy MongoDB
sudo systemctl enable --now docker
sudo docker rm -f enc2health-mongo 2>/dev/null || true
sudo docker run -d --name enc2health-mongo \
  -p 0.0.0.0:27017:27017 \
  --restart unless-stopped mongo:7

# 1.2 Sinh dữ liệu EHR giả lập (đã mã hóa)
FORCE_RECREATE=1 EHR_FORCE_RECREATE=1 python crypto/data/generate_ehr.py

# 1.3 Khởi chạy Vault server ở chế độ dev mode (lắng nghe mọi IP để Lan và Nam kết nối)
nohup vault server -dev \
  -dev-listen-address="0.0.0.0:8200" \
  -dev-root-token-id="enc2health-root-token" > /tmp/vault.log 2>&1 &

sleep 3

# Cấu hình biến môi trường tạm để setup Vault
export VAULT_ADDR='http://127.0.0.1:8200'
export VAULT_TOKEN='enc2health-root-token'

# 1.4 Thiết lập secrets engine, upload keypairs và DEK lên Vault
bash crypto/vault/setup_vault.sh

echo "MongoDB Long ready at $(hostname -I | awk '{print $1}'):27017"
echo "Vault Long ready at $(hostname -I | awk '{print $1}'):8200"
```

Kiểm tra container:

```bash
sudo docker ps
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

## 2. Lan / VM2 - chạy Enclave ECALL Pool

Thay `LONG_IP` bằng IP của VM1.

```bash
cd ~/Enc2Health
source .venv/bin/activate

# Khởi chạy Pool kết nối tới Mongo và Vault của Long
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
T8_POOL_HOST=0.0.0.0 \
T8_ALLOW_LOCAL_KEY_FALLBACK=0 \
MONGO_URI=mongodb://LONG_IP:27017 \
VAULT_ADDR=http://LONG_IP:8200 \
VAULT_TOKEN=enc2health-root-token \
nohup .venv/bin/python enclave/ecall_pool.py > /tmp/pool.log 2>&1 &

sleep 4
# Kiểm tra log xem Pool đã lấy DEK từ Vault thành công chưa (không fallback local keys)
tail -20 /tmp/pool.log

echo "Pool Lan ready at $(hostname -I | awk '{print $1}'):9091"
```

Kiểm tra Pool:

```bash
tail -20 /tmp/pool.log
```

## 3. Nam / VM3 - chạy Query Router + UI

Thay `LONG_IP`, `LAN_IP` bằng IP của VM1, VM2.

```bash
cd ~/Enc2Health
source .venv/bin/activate

AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
ROUTER_TEE_PUSH_CIPHERTEXT=1 \
ENC2HEALTH_DEV_UI=1 \
MONGO_URI=mongodb://LONG_IP:27017 \
ECALL_POOL_URL=http://LAN_IP:9091 \
VAULT_ADDR=http://LONG_IP:8200 \
nohup .venv/bin/uvicorn router.main:app \
  --host 0.0.0.0 \
  --port 8000 > /tmp/router.log 2>&1 &

sleep 5
tail -5 /tmp/router.log

echo "Router + UI Nam ready at $(hostname -I | awk '{print $1}'):8000"
```

Kiểm tra Router:

```bash
tail -20 /tmp/router.log
```

## 4. Nam / VM3 - kiểm tra 3 node

Nếu chạy trên VM3:

```bash
curl http://localhost:8000/nodes
```

Nếu chạy từ laptop host hoặc VM khác:

```bash
curl http://NAM_IP:8000/nodes
```

Kết quả mong đợi: MongoDB, Enclave/Pool, Router đều `ok`.

## 5. Nam / VM3 - demo luồng truy vấn qua 3 máy

Thay `NAM_IP` bằng IP VM3.

```bash
cd ~/Enc2Health
source .venv/bin/activate

ROUTER_URL=http://NAM_IP:8000 \
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
python3 scripts/demo_nodes.py avg_vien_phi I01 60 admin
```

Luồng cần quay:

```text
Client -> Router Nam -> Mongo Long -> Enclave Lan -> Router Nam -> Client
```

## 6. Nam hoặc laptop host - mở UI

Mở trình duyệt:

```text
http://NAM_IP:8000/ui
```

Thao tác trên UI:

```text
Role: admin
Query: avg_vien_phi
Filter mã bệnh: I01 hoặc E11
Tuổi: >= 60
```

## 7. Nam / VM3 - demo phân quyền RBAC/ABAC

```bash
cd ~/Enc2Health
source .venv/bin/activate

python3 scripts/demo_abac.py
```

## 8. Nam / VM3 - demo Adaptive Fallback

```bash
cd ~/Enc2Health
source .venv/bin/activate

python3 scripts/demo_adaptive.py
```

## 9. Lan + Nam - chứng minh node độc lập

Lan / VM2 tắt Enclave:

```bash
pkill -f enclave/ecall_pool.py
```

Nam / VM3 kiểm tra lại node:

```bash
curl http://localhost:8000/nodes
```

Kết quả mong đợi: Enclave/Pool `down` (trạng thái hiển thị down trên UI/Console), MongoDB và Vault vẫn `ok`.

Lan / VM2 bật lại Enclave (giữ kết nối tới Vault của Long):

```bash
cd ~/Enc2Health
source .venv/bin/activate

AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
T8_POOL_HOST=0.0.0.0 \
T8_ALLOW_LOCAL_KEY_FALLBACK=0 \
MONGO_URI=mongodb://LONG_IP:27017 \
VAULT_ADDR=http://LONG_IP:8200 \
VAULT_TOKEN=enc2health-root-token \
nohup .venv/bin/python enclave/ecall_pool.py > /tmp/pool.log 2>&1 &

sleep 4
tail -20 /tmp/pool.log
```

Nam / VM3 kiểm tra lại:

```bash
curl http://localhost:8000/nodes
```

## 10. Checklist trước khi quay

Long / VM1:

```bash
hostname -I
sudo docker ps
vault status # Kiểm tra Vault hoạt động bình thường
```

Lan / VM2:

```bash
hostname -I
tail -20 /tmp/pool.log # Đảm bảo log ghi nhận "Loaded key from Vault"
```

Nam / VM3:

```bash
hostname -I
tail -5 /tmp/router.log
curl http://localhost:8000/nodes # Toàn bộ 3 node Mongo, Pool, Vault đều ok
```

Lệnh demo chính:

```bash
ROUTER_URL=http://NAM_IP:8000 \
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
python3 scripts/demo_nodes.py avg_vien_phi I01 60 admin
```
