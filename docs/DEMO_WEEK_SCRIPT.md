# Kịch bản demo 3 VM qua Tailscale HTTPS

## Phân vai

| Máy | Người | Chạy gì |
|---|---|---|
| VM1 | Long | MongoDB + Vault + sinh dữ liệu EHR mã hóa |
| VM2 | Lan | ECALL Pool / Enclave TEE HTTPS |
| VM3 | Nam | Query Router HTTPS + UI + demo client |

## Biến cần ghi lại

```text
LONG_TAILSCALE_IP = 100.127.127.122
LAN_TAILSCALE_IP  = 100.119.174.95
NAM_TAILSCALE_IP  = 100.67.221.119
LAN_DOMAIN        = domain Tailscale của Lan, dạng xxx.tailxxxx.ts.net
NAM_DOMAIN        = domain Tailscale của Nam, dạng xxx.tailxxxx.ts.net
```

Giá trị hiện có:

```text
LONG_TAILSCALE_IP = 100.127.127.122
LAN_TAILSCALE_IP  = 100.119.174.95
NAM_TAILSCALE_IP  = 100.67.221.119
LAN_DOMAIN        = lan-bravo-15-b5dd.tail4cc053.ts.net
NAM_DOMAIN        = lấy bằng lệnh `tailscale cert` trên máy Nam
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

`tailscale cert` sẽ báo domain cần dùng:

```text
For domain, use "ten-may.tailxxxx.ts.net".
```

Ghi lại:

```text
Long: LONG_TAILSCALE_IP = 100.127.127.122
Lan:  LAN_TAILSCALE_IP  = 100.119.174.95
Nam:  NAM_TAILSCALE_IP  = 100.67.221.119
Lan:  LAN_DOMAIN
Nam:  NAM_DOMAIN
```

## 0.1. Lan / VM2 - xin cert HTTPS Tailscale

Thay `LAN_DOMAIN` bằng domain thật của Lan.

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

Thay `NAM_DOMAIN` bằng domain thật của Nam.

```bash
cd ~/Enc2Health
source .venv/bin/activate

export NAM_DOMAIN=<domain-cua-Nam>.tail4cc053.ts.net

tailscale cert "$NAM_DOMAIN"
ls -l "$NAM_DOMAIN.crt" "$NAM_DOMAIN.key"
```

Nếu lỗi quyền ghi file:

```bash
sudo tailscale cert "$NAM_DOMAIN"
sudo chown "$USER:$USER" "$NAM_DOMAIN.crt" "$NAM_DOMAIN.key"
ls -l "$NAM_DOMAIN.crt" "$NAM_DOMAIN.key"
```

## 1. Long / VM1 - chạy MongoDB, Vault và sinh dữ liệu mã hóa

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

# 1.3. Khởi chạy Vault dev mode cho Lan/Nam kết nối qua Tailscale
pkill -f 'vault server -dev' 2>/dev/null || true
nohup vault server -dev \
  -dev-listen-address="0.0.0.0:8200" \
  -dev-root-token-id="enc2health-root-token" > /tmp/vault.log 2>&1 &

sleep 3

# 1.4. Setup Vault secrets
export VAULT_ADDR='http://127.0.0.1:8200'
export VAULT_TOKEN='enc2health-root-token'
bash crypto/vault/setup_vault.sh

echo "MongoDB Long ready at $(tailscale ip -4):27017"
echo "Vault Long ready at $(tailscale ip -4):8200"
```

Kiểm tra Long:

```bash
sudo docker ps
vault status
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

## 2. Lan / VM2 - chạy Enclave ECALL Pool bằng HTTPS

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

AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
T8_POOL_HOST=0.0.0.0 \
T8_ALLOW_LOCAL_KEY_FALLBACK=0 \
T8_SSL_CERT="$LAN_DOMAIN.crt" \
T8_SSL_KEY="$LAN_DOMAIN.key" \
MONGO_URI="mongodb://$LONG_TAILSCALE_IP:27017" \
VAULT_ADDR="http://$LONG_TAILSCALE_IP:8200" \
VAULT_TOKEN=enc2health-root-token \
nohup .venv/bin/python enclave/ecall_pool.py > /tmp/pool.log 2>&1 &

sleep 4
tail -20 /tmp/pool.log

echo "Pool Lan ready at https://$LAN_DOMAIN:9091"
```

Kiểm tra Pool:

```bash
tail -20 /tmp/pool.log
curl "https://$LAN_DOMAIN:9091/health"
```

Nếu `/health` không có:

```bash
curl "https://$LAN_DOMAIN:9091/docs"
```

## 3. Nam / VM3 - chạy Query Router + UI bằng HTTPS

Thay biến:

```text
LONG_TAILSCALE_IP = IP Tailscale của Long
LAN_DOMAIN        = domain Tailscale của Lan
NAM_DOMAIN        = domain Tailscale của Nam
```

```bash
cd ~/Enc2Health
source .venv/bin/activate

export LONG_TAILSCALE_IP=100.127.127.122
export LAN_DOMAIN=lan-bravo-15-b5dd.tail4cc053.ts.net
export NAM_DOMAIN=<domain-cua-Nam>.tail4cc053.ts.net

AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
ROUTER_TEE_PUSH_CIPHERTEXT=1 \
ENC2HEALTH_DEV_UI=1 \
MONGO_URI="mongodb://$LONG_TAILSCALE_IP:27017" \
ECALL_POOL_URL="https://$LAN_DOMAIN:9091" \
VAULT_ADDR="http://$LONG_TAILSCALE_IP:8200" \
VAULT_TOKEN=enc2health-root-token \
nohup .venv/bin/uvicorn router.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --ssl-certfile "$NAM_DOMAIN.crt" \
  --ssl-keyfile "$NAM_DOMAIN.key" \
  > /tmp/router.log 2>&1 &

sleep 5
tail -20 /tmp/router.log

echo "Router + UI Nam ready at https://$NAM_DOMAIN:8000"
```

Kiểm tra Router:

```bash
tail -20 /tmp/router.log
curl "https://$NAM_DOMAIN:8000/nodes"
```

## 4. Nam / VM3 - kiểm tra 3 node

```bash
curl "https://$NAM_DOMAIN:8000/nodes"
```

Kết quả mong đợi:

```text
MongoDB ok
Vault ok
Enclave/Pool ok
Router ok
```

## 5. Nam / VM3 - demo luồng truy vấn qua 3 máy

```bash
cd ~/Enc2Health
source .venv/bin/activate

export NAM_DOMAIN=<domain-cua-Nam>.tail4cc053.ts.net

ROUTER_URL="https://$NAM_DOMAIN:8000" \
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
python3 scripts/demo_nodes.py avg_vien_phi I01 60 admin
```

Luồng cần quay:

```text
Client -> Router Nam HTTPS -> Mongo Long -> Enclave Lan HTTPS -> Router Nam -> Client
```

## 6. Nam hoặc laptop host - mở UI HTTPS

Mở trình duyệt:

```text
https://NAM_DOMAIN:8000/ui
```

Ví dụ:

```text
https://nam-xxx.tail4cc053.ts.net:8000/ui
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
curl "https://$NAM_DOMAIN:8000/nodes"
```

Kết quả mong đợi:

```text
Enclave/Pool down
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

AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
T8_POOL_HOST=0.0.0.0 \
T8_ALLOW_LOCAL_KEY_FALLBACK=0 \
T8_SSL_CERT="$LAN_DOMAIN.crt" \
T8_SSL_KEY="$LAN_DOMAIN.key" \
MONGO_URI="mongodb://$LONG_TAILSCALE_IP:27017" \
VAULT_ADDR="http://$LONG_TAILSCALE_IP:8200" \
VAULT_TOKEN=enc2health-root-token \
nohup .venv/bin/python enclave/ecall_pool.py > /tmp/pool.log 2>&1 &

sleep 4
tail -20 /tmp/pool.log
```

Nam / VM3 kiểm tra lại:

```bash
curl "https://$NAM_DOMAIN:8000/nodes"
```

## 10. Checklist trước khi quay

Long / VM1:

```bash
tailscale ip -4
sudo docker ps
vault status
tail -10 /tmp/vault.log
```

Lan / VM2:

```bash
tailscale cert
ls -l "$LAN_DOMAIN.crt" "$LAN_DOMAIN.key"
tail -20 /tmp/pool.log
curl "https://$LAN_DOMAIN:9091/health"
```

Nam / VM3:

```bash
tailscale cert
ls -l "$NAM_DOMAIN.crt" "$NAM_DOMAIN.key"
tail -20 /tmp/router.log
curl "https://$NAM_DOMAIN:8000/nodes"
```

Lệnh demo chính:

```bash
ROUTER_URL="https://$NAM_DOMAIN:8000" \
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
python3 scripts/demo_nodes.py avg_vien_phi I01 60 admin
```
