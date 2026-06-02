# Enc²Health — Triển khai PHÂN TÁN 3 VM (mỗi máy 1 node)

> Yêu cầu thầy: hệ chạy **phân tán nhiều máy** (không gom 1 localhost). Dùng **3 VM
> Ubuntu** (VMware, NAT) — mỗi thành viên "sở hữu" 1 node, các node gọi nhau qua mạng.

```
   VM3 — Nam (Router)            VM1 — Long (MongoDB)        VM2 — Lan (Enclave)
 ┌──────────────────┐  lấy     ┌──────────────────┐        ┌──────────────────┐
 │ Query Router      │ciphertext│ MongoDB :27017   │        │ ECALL Pool :9091 │
 │ :8000  (+ /ui)    │─────────▶│ (ciphertext)     │        │ giải mã + DuckDB │
 │ = giao diện Client│          │ [+ Vault :8200]  │        │ trong TEE        │
 │                   │── đẩy ciphertext ──────────────────▶│                  │
 │                   │◀──────── 1 con số ─────────────────  │                  │
 └──────────────────┘          └──────────────────┘        └──────────────────┘
```

| VM | Người | Node chạy | Cổng mở |
|---|---|---|---|
| **VM1** | **Long** | MongoDB *(+ Vault tuỳ chọn)* | 27017 *(8200)* |
| **VM2** | **Lan** | ECALL Pool / Enclave (TEE) | 9091 |
| **VM3** | **Nam** | Query Router + UI (Client mở browser ở đây) | 8000 |

> **Client** không phải VM riêng — là **trình duyệt** mở `http://<IP-VM3>:8000/ui`.

---

## 0. Thử nhanh TRÊN 1 MÁY trước (localhost) — kiểm tra logic OK đã

Trước khi dựng VM, chạy cả 3 node trên 1 máy để chắc hệ chạy đúng:
```bash
# (cần Mongo :27017 — docker run -d -p 27017:27017 mongo:7 ; rồi seed:)
FORCE_RECREATE=1 python3 crypto/data/generate_ehr.py
# T.1 — Pool:
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 T8_ALLOW_LOCAL_KEY_FALLBACK=1 python3 enclave/ecall_pool.py
# T.2 — Router:
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 ROUTER_TEE_PUSH_CIPHERTEXT=1 ENC2HEALTH_DEV_UI=1 uvicorn router.main:app --port 8000
# T.3 — kiểm tra:
curl http://localhost:8000/nodes
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 python3 scripts/demo_nodes.py
```
Chạy ngon ở localhost rồi → mới sang 3 VM.

---

## 1. Tạo 3 VM + lấy IP

1. Tạo **3 VM Ubuntu Server** (mỗi VM: 2GB RAM, 10GB disk), network để **NAT**.
2. Trong **mỗi VM**, lấy IP:
   ```bash
   hostname -I        # vd VM1=192.168.226.10  VM2=192.168.226.11  VM3=192.168.226.12
   sudo ufw disable   # tắt firewall cho demo (cho các VM gọi nhau khỏi vướng)
   ```
3. Ghi lại 3 IP. Dưới đây mình ký hiệu: **LONG_IP**, **LAN_IP**, **NAM_IP**.

### Cài chung trên CẢ 3 VM
```bash
sudo apt-get update -y && sudo apt-get install -y git python3-venv python3-pip curl docker.io
git clone --depth 1 https://github.com/NuhHuhYuuka/Enc2Health.git && cd Enc2Health
python3 -m venv .venv && . .venv/bin/activate
pip install -U pip && pip install -r requirements.txt duckdb requests
```
> 3 VM clone cùng repo → **dùng chung bộ khóa** `crypto/data/keys/` → Long mã hóa thì Lan/Nam giải mã/đọc được.

---

## 2. VM1 — LONG (MongoDB)

```bash
cd ~/Enc2Health && . .venv/bin/activate
sudo systemctl enable --now docker
sudo docker rm -f enc2health-mongo 2>/dev/null || true
sudo docker run -d --name enc2health-mongo -p 0.0.0.0:27017:27017 --restart unless-stopped mongo:7

# Sinh 10.000 hồ sơ mã hóa (dùng khóa chung trong repo)
FORCE_RECREATE=1 EHR_FORCE_RECREATE=1 python crypto/data/generate_ehr.py
echo "MongoDB sẵn sàng tại $(hostname -I | awk '{print $1}'):27017"
```

> **(Tuỳ chọn) Vault** — muốn node Vault hiện xanh trong /nodes:
> ```bash
> sudo docker run -d --name enc2health-vault --cap-add=IPC_LOCK \
>   -p 0.0.0.0:8200:8200 -e VAULT_DEV_ROOT_TOKEN_ID=enc2health-root-token hashicorp/vault
> ```

---

## 3. VM2 — LAN (ECALL Pool / Enclave TEE)

> Thay `LONG_IP` bằng IP thật của VM1.
```bash
cd ~/Enc2Health && . .venv/bin/activate
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
T8_POOL_HOST=0.0.0.0 \
T8_ALLOW_LOCAL_KEY_FALLBACK=1 \
MONGO_URI=mongodb://LONG_IP:27017 \
nohup .venv/bin/python enclave/ecall_pool.py > /tmp/pool.log 2>&1 &
sleep 4 && tail -5 /tmp/pool.log
echo "Pool sẵn sàng tại $(hostname -I | awk '{print $1}'):9091"
```

---

## 4. VM3 — NAM (Query Router + UI) — chạy SAU CÙNG (cần IP của VM1 & VM2)

> Thay `LONG_IP`, `LAN_IP` bằng IP thật của VM1, VM2.
```bash
cd ~/Enc2Health && . .venv/bin/activate
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
ROUTER_TEE_PUSH_CIPHERTEXT=1 \
ENC2HEALTH_DEV_UI=1 \
MONGO_URI=mongodb://LONG_IP:27017 \
ECALL_POOL_URL=http://LAN_IP:9091 \
VAULT_ADDR=http://LONG_IP:8200 \
nohup .venv/bin/uvicorn router.main:app --host 0.0.0.0 --port 8000 > /tmp/router.log 2>&1 &
sleep 5 && tail -5 /tmp/router.log
echo "Router + UI tại $(hostname -I | awk '{print $1}'):8000"
```

---

## 5. Kiểm tra & DEMO

Chạy trên VM3 (Nam) hoặc trên laptop host:
```bash
# 1) Router (VM3) có gọi được Mongo (VM1) + Pool (VM2) qua mạng không:
curl http://NAM_IP:8000/nodes        # mong đợi mọi node "status":"ok"

# 2) Giao diện web — MỞ TRÊN TRÌNH DUYỆT (đây là "Client"):
http://NAM_IP:8000/ui

# 3) Soi tương tác giữa các MÁY trên CLI:
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 .venv/bin/python scripts/demo_nodes.py
```

**Kịch bản nói với thầy:**
1. `/nodes` → **3 node ở 3 VM (3 IP khác nhau)** đều `ok` → Router (VM3) *gọi được* Mongo (VM1) + Pool (VM2) **qua mạng**.
2. `demo_nodes.py` → in **đường đi truy vấn băng qua 3 máy**: Router(Nam) → Mongo(Long) lấy ciphertext → Pool(Lan) giải mã trong TEE → trả 1 con số. CSP chỉ thấy ciphertext.
3. Mở `/ui` đăng nhập bác sĩ → phân tích → app y tế thật trên hạ tầng phân tán.
4. (Chứng minh node độc lập) Tắt Pool ở VM2 → `curl .../nodes` thấy enclave `down`.

---

## 6. HTTPS (tuỳ chọn — nếu muốn "+ https")
```bash
# trên VM3, sinh cert rồi chạy Router kèm SSL:
bash scripts/generate_mtls_certs.sh
# thêm vào lệnh uvicorn:  --ssl-certfile certs/server.crt --ssl-keyfile certs/server.key
# → https://NAM_IP:8000/ui  (browser cảnh báo self-signed → Proceed; giải thích cert tự ký cho demo)
```

## 7. Lưu ý (nói rõ khi bảo vệ)
- Đây là **demo**: secret/khóa dev trong repo, Mongo không auth → mạng NAT nội bộ giữa 3 VM, không expose internet. **Không phải cấu hình production** (xem `docs/LIMITATIONS.md`).
- `AUTH_JWT_SECRET` phải **giống hệt** trên cả 3 VM, nếu không Router↔Pool sẽ 401.
- Khóa AES-GCM dùng **local fallback** cho gọn; production unwrap DEK qua Vault.
- Node nào `down`: kiểm tra `sudo ufw disable` đã chạy chưa + service đã lên (`tail /tmp/*.log`) + IP điền đúng chưa + `ping LONG_IP` từ VM3 có thông không.
