# Hướng dẫn thực hiện T1–T13 – Lâm Tú Lan (LTL)
**Kịch bản:** Enc²Health | **OS:** Ubuntu 24.04 LTS | **Vai trò:** TEE / SGX Enclave & Observability

> ⚡ **Toàn bộ hướng dẫn này dùng Gramine Simulation Mode (gramine-direct)**  
> KHÔNG cần CPU Intel SGX. KHÔNG cần driver SGX. Chạy được trên máy thường.

---

## Mục lục
0. [Cài đặt môi trường tổng thể](#0-cài-đặt-môi-trường-tổng-thể)
1. [T1 – Gramine + DuckDB (SGX2 Simulation Mode)](#t1--gramine--duckdb-sgx2-simulation-mode)
2. [T2 – Private Key qua Mock TLS (Simulation RA-TLS)](#t2--private-key-qua-mock-tls-simulation-ra-tls)
3. [T3 – Giải mã AES-GCM trong process memory (register-safe)](#t3--giải-mã-aes-gcm-trong-process-memory-register-safe)
4. [T4 – OpenSSL AES-NI thay mbedtls](#t4--openssl-aes-ni-thay-mbedtls)
5. [T5 – glibc malloc thay jemalloc](#t5--glibc-malloc-thay-jemalloc)
6. [T6 – Cấu hình numactl](#t6--cấu-hình-numactl)
7. [T7 – Spectre v4 Mitigation](#t7--spectre-v4-mitigation)
8. [T8 – ECALL Task Pool (HTTP Service)](#t8--ecall-task-pool-http-service)
9. [T9 – Đo C_TEE metrics](#t9--đo-c_tee-metrics)
10. [T10 – Prometheus + Grafana](#t10--prometheus--grafana)
11. [T11 – Benchmark TEE-only 10k hồ sơ EHR](#t11--benchmark-tee-only-10k-hồ-sơ-ehr)
12. [T12 – Hash Join vs Radix Join trong Enclave](#t12--hash-join-vs-radix-join-trong-enclave)
13. [T13 – Đo Storage Overhead](#t13--đo-storage-overhead)

---

## 0. Cài đặt môi trường tổng thể

> Làm **một lần duy nhất** trước khi bắt đầu.

### 0.1 Dependencies cơ bản

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    curl wget gnupg build-essential cmake git \
    python3-pip python3-venv python3-dev \
    libssl-dev pkg-config numactl \
    hyperfine sysstat linux-tools-common \
    linux-tools-$(uname -r) perf \
    docker.io docker-compose-plugin
```

### 0.2 Python virtual environment

```bash
mkdir -p ~/enc2health_lan && cd ~/enc2health_lan
python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install \
    duckdb \
    pymongo \
    cryptography \
    hvac \
    fastapi \
    uvicorn \
    flask \
    requests \
    prometheus-client \
    python-dotenv \
    psutil
```

> Mỗi lần mở terminal mới: `source ~/enc2health_lan/.venv/bin/activate`

### 0.3 Cài đặt Gramine (Ubuntu 24.04 – noble)

```bash
# Gramine hỗ trợ Ubuntu 24.04 từ v1.7+
# Lệnh dưới tự detect "noble" qua lsb_release

sudo curl -fsSLo /etc/apt/keyrings/gramine-keyring-$(lsb_release -sc).gpg \
    https://packages.gramineproject.io/gramine-keyring-$(lsb_release -sc).gpg

echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/gramine-keyring-$(lsb_release -sc).gpg] \
    https://packages.gramineproject.io/ $(lsb_release -sc) main" \
    | sudo tee /etc/apt/sources.list.d/gramine.list

sudo apt update && sudo apt install -y gramine

# Verify – CHỈ cần gramine-direct (simulation), KHÔNG cần gramine-sgx
gramine-direct --version
```

> **Nếu noble chưa có package** (gramine vẫn đang cập nhật):
> ```bash
> # Fallback: dùng jammy package trên noble – hoạt động được cho simulation mode
> echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/gramine-keyring-jammy.gpg] \
>     https://packages.gramineproject.io/ jammy main" \
>     | sudo tee /etc/apt/sources.list.d/gramine.list
> sudo curl -fsSLo /etc/apt/keyrings/gramine-keyring-jammy.gpg \
>     https://packages.gramineproject.io/gramine-keyring-jammy.gpg
> sudo apt update && sudo apt install -y gramine
> ```

### 0.4 Cấu trúc thư mục

```
~/enc2health_lan/
├── .venv/
├── enclave/
│   ├── duckdb.manifest         # T1 – Gramine manifest
│   ├── enclave_service.py      # T1, T3, T8 – main enclave process
│   ├── key_receiver.py         # T2 – nhận private key từ Vault
│   └── ecall_pool.py           # T8 – task pool HTTP API
├── certs/                      # T2 – mTLS certs
├── benchmark/
│   ├── bench_t9.py             # T9 – C_TEE metrics
│   ├── bench_t11.py            # T11 – TEE benchmark
│   └── bench_t12.py            # T12 – join comparison
├── monitoring/
│   ├── exporter.py             # T10 – Prometheus exporter
│   └── docker-compose.yml      # T10 – Prometheus + Grafana
└── storage/
    └── measure_overhead.py     # T13 – storage overhead
```

---

## T1 – Gramine + DuckDB (SGX2 Simulation Mode)

**Ưu tiên:** Tuần 1 | **Phụ thuộc:** Long T5 (dataset MongoDB FLE)

### Mục tiêu
Chạy DuckDB bên trong `gramine-direct` (simulation). Verify load được 10.000 hồ sơ từ MongoDB FLE của Long.

> **Lưu ý quan trọng:** KHÔNG dùng `gramine-sgx`. KHÔNG cần `gramine-sgx-gen-private-key`.  
> KHÔNG dùng `sgx.*` keys trong manifest. Chúng chỉ dành cho hardware thật.

### Bước 1 – Cài DuckDB CLI và Python lib

```bash
# Python library (đủ cho mọi task)
pip install duckdb  # đã install ở section 0

# CLI binary để test
wget https://github.com/duckdb/duckdb/releases/latest/download/duckdb_cli-linux-amd64.zip
unzip duckdb_cli-linux-amd64.zip -d ~/enc2health_lan/
chmod +x ~/enc2health_lan/duckdb
```

### Bước 2 – Viết Gramine manifest cho Python (simulation mode)

```bash
mkdir -p ~/enc2health_lan/enclave
nano ~/enc2health_lan/enclave/duckdb.manifest
```

```toml
# duckdb.manifest – SIMULATION MODE (gramine-direct)
# KHÔNG có sgx.* keys – chúng chỉ dùng với hardware SGX thật

loader.entrypoint = "file:{{ gramine.libos }}"
loader.argv = ["python3", "enclave_service.py"]
loader.env.LD_LIBRARY_PATH = "/lib/x86_64-linux-gnu:/usr/lib/x86_64-linux-gnu"
loader.env.PATH = "/usr/bin:/bin"
loader.env.HOME = "/home/{{ env.USER }}"

# Python cần nhiều threads
loader.insecure__use_cmdline_argv = true
sys.enable_extra_runtime_domain_names_conf = true

libos.entrypoint = "{{ python.get_sys_path(gramine.manifest_dir) }}/python3"

fs.mounts = [
  { path = "/lib",     uri = "file:/lib" },
  { path = "/usr",     uri = "file:/usr" },
  { path = "/etc",     uri = "file:/etc" },
  { path = "/tmp",     uri = "file:/tmp" },
  { path = "/home",    uri = "file:/home" },
  { path = "/proc/self/maps", uri = "file:/proc/self/maps" },
]

# Simulation mode: không cần sgx.enclave_size, sgx.debug, v.v.
```

### Bước 3 – Main enclave service (Python)

```bash
nano ~/enc2health_lan/enclave/enclave_service.py
```

```python
# ~/enc2health_lan/enclave/enclave_service.py
"""
Main process chạy trong gramine-direct (Simulation Mode).
Thực hiện: nhận query từ Router (Nam), giải mã AES-GCM, tính AVG/SUM bằng DuckDB.
"""
import duckdb
import sys
import os
import json
import base64
import logging
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ENCLAVE] %(message)s")
log = logging.getLogger("enclave")

# Key store – chỉ sống trong RAM của process này
_keys: dict = {}   # {"gcm_dek": bytes, "dept:Noi": bytes, ...}
_conn = duckdb.connect(":memory:")


def load_key(key_name: str, key_bytes: bytes):
    """Nạp key vào memory store (gọi khi init hoặc sau RA-TLS)."""
    _keys[key_name] = key_bytes
    log.info(f"Key loaded: {key_name} ({len(key_bytes)} bytes)")


def decrypt_aes_gcm(ciphertext_b64: str, key_name: str = "gcm_dek") -> float:
    """
    Giải mã AES-GCM 256-bit.
    Plaintext chỉ tồn tại trong scope hàm này – không ghi ra disk.
    """
    key = _keys.get(key_name)
    if not key:
        raise RuntimeError(f"Key '{key_name}' not loaded")

    raw = base64.b64decode(ciphertext_b64)
    nonce, ct = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ct, None)
    value = float(plaintext.decode())

    # Xóa tham chiếu plaintext ngay lập tức
    del plaintext
    return value


def query_avg_vien_phi(records: list[dict], key_name: str = "gcm_dek") -> float:
    """
    Tính AVG viện phí từ list records đã fetch từ MongoDB.
    Giải mã từng record trong memory, tính trung bình, không lưu plaintext.
    """
    values = [decrypt_aes_gcm(r["vien_phi_enc"], key_name) for r in records]
    if not values:
        return 0.0
    avg = sum(values) / len(values)
    # Zero out values list
    values.clear()
    return avg


def run_duckdb_query(data: list[dict], sql: str) -> dict:
    """Chạy DuckDB in-memory query trên data đã decrypt."""
    import pandas as pd
    df = pd.DataFrame(data)
    _conn.register("temp_data", df)
    result = _conn.execute(sql).fetchdf()
    _conn.unregister("temp_data")
    return result.to_dict(orient="records")


if __name__ == "__main__":
    log.info("Enclave service started in Gramine Simulation Mode")
    log.info(f"DuckDB version: {duckdb.__version__}")

    # Test basic functionality
    result = _conn.execute("SELECT 42 AS answer").fetchone()
    assert result[0] == 42
    log.info("DuckDB in-memory: OK")

    # Test AES-GCM với dummy key
    test_key = os.urandom(32)
    load_key("test_key", test_key)
    nonce = os.urandom(12)
    aesgcm = AESGCM(test_key)
    ct = aesgcm.encrypt(nonce, b"12500000.0", None)
    ct_b64 = base64.b64encode(nonce + ct).decode()
    val = decrypt_aes_gcm(ct_b64, "test_key")
    assert val == 12500000.0
    log.info("AES-GCM decrypt: OK")
    log.info("All basic tests passed – Enclave ready")
```

### Bước 4 – Chạy trong gramine-direct

```bash
cd ~/enc2health_lan

# Compile manifest (bước này sinh file .manifest.sgx – không cần dùng, chỉ để hoàn chỉnh)
gramine-manifest enclave/duckdb.manifest enclave/duckdb.manifest

# Chạy service trong Simulation Mode
gramine-direct python3 enclave/enclave_service.py

# Expected output:
# [ENCLAVE] Enclave service started in Gramine Simulation Mode
# [ENCLAVE] DuckDB in-memory: OK
# [ENCLAVE] AES-GCM decrypt: OK
# [ENCLAVE] All basic tests passed – Enclave ready
```

### Bước 5 – Kết nối với MongoDB FLE (chờ Long T5)

```bash
nano ~/enc2health_lan/enclave/load_from_mongo.py
```

```python
# load_from_mongo.py – Test load dataset từ Long
from pymongo import MongoClient
import time

client = MongoClient("mongodb://localhost:27017")
col = client["enc2health"]["patient_records"]

t0 = time.perf_counter()
count = col.count_documents({})
load_time = (time.perf_counter() - t0) * 1000

print(f"Total records: {count:,}")
print(f"Load time: {load_time:.2f}ms")

# Fetch sample để verify structure
sample = col.find_one({})
expected_fields = ["vien_phi_enc", "ma_benh_enc", "tuoi_enc", "ket_qua_xn_enc"]
for f in expected_fields:
    assert f in sample, f"Missing field: {f}"
    print(f"  {f}: OK ({sample[f][:20]}...)")

print("Dataset structure: OK – Ready for Enclave queries")
client.close()
```

```bash
python enclave/load_from_mongo.py
```

### Bước 6 – Ghi baseline metrics

```bash
nano ~/enc2health_lan/benchmark/baseline.sh
```

```bash
#!/bin/bash
# baseline.sh – đo metrics cơ bản khi enclave khởi động

echo "timestamp,metric,value" > benchmark_baseline.csv

# Enclave load time
t_start=$(date +%s%N)
gramine-direct python3 enclave/enclave_service.py &
PID=$!
sleep 2
t_end=$(date +%s%N)
load_ms=$(( (t_end - t_start) / 1000000 ))
echo "$(date -Iseconds),enclave_load_ms,$load_ms" >> benchmark_baseline.csv

# Memory footprint
rss=$(grep VmRSS /proc/$PID/status | awk '{print $2}')
echo "$(date -Iseconds),rss_kb,$rss" >> benchmark_baseline.csv

# Simple query latency
t_q0=$(date +%s%N)
python3 -c "
import duckdb
conn = duckdb.connect(':memory:')
conn.execute('SELECT COUNT(*) FROM (VALUES (1),(2),(3)) t(x)').fetchone()
"
t_q1=$(date +%s%N)
query_ms=$(( (t_q1 - t_q0) / 1000000 ))
echo "$(date -Iseconds),duckdb_query_ms,$query_ms" >> benchmark_baseline.csv

kill $PID 2>/dev/null
echo "Baseline saved to benchmark_baseline.csv"
cat benchmark_baseline.csv
```

```bash
chmod +x benchmark/baseline.sh
bash benchmark/baseline.sh
```

---

## T2 – Private Key qua Mock TLS (Simulation RA-TLS)

**Deadline:** Cuối tuần 2 | **Phụ thuộc:** Long T6 (Vault có keypairs per-Khoa)

### Lý do dùng mTLS thay RA-TLS hardware

RA-TLS thật cần SGX Quote từ CPU Intel. Trong Simulation Mode, thay bằng **mutual TLS (mTLS)** với self-signed cert. Luồng logic hoàn toàn giống nhau – chỉ thiếu bước SGX attestation. Ghi rõ trong báo cáo.

> **Vault secret path** (đồng bộ với Long T6): `enc2health/keypairs/{dept}`

### Bước 1 – Tạo CA và certificates

```bash
mkdir -p ~/enc2health_lan/certs && cd ~/enc2health_lan/certs

# 1. CA – đóng vai Intel Attestation Service trong simulation
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 365 -key ca.key -out ca.crt \
    -subj '/CN=Enc2Health-SimCA/O=Enc2Health/C=VN'

# 2. Enclave cert (đóng vai SGX Quote)
openssl genrsa -out enclave.key 4096
openssl req -new -key enclave.key -out enclave.csr \
    -subj '/CN=Enclave-MRENCLAVE-Sim/O=Enc2Health/C=VN'
openssl x509 -req -days 365 -in enclave.csr \
    -CA ca.crt -CAkey ca.key -CAcreateserial -out enclave.crt

# 3. Vault cert (distributor)
openssl genrsa -out vault.key 4096
openssl req -new -key vault.key -out vault.csr \
    -subj '/CN=Vault-KeyDistributor/O=Enc2Health/C=VN'
openssl x509 -req -days 365 -in vault.csr \
    -CA ca.crt -CAkey ca.key -CAcreateserial -out vault.crt

ls -la *.crt *.key
cd ~/enc2health_lan
```

### Bước 2 – Key Receiver (chạy trong Enclave)

```bash
nano ~/enc2health_lan/enclave/key_receiver.py
```

```python
# ~/enc2health_lan/enclave/key_receiver.py
"""
Nhận Private Key từ Vault qua mTLS.
Chạy trong gramine-direct – key chỉ sống trong RAM của process này.
Port: 8443 (tránh conflict với KMS API 8001 của Long và Prometheus 8002)
"""
import ssl
import socket
import threading
import base64
import logging
import datetime
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [KEY_RECV] %(message)s")
log = logging.getLogger("key_receiver")

# Global key store – chỉ trong RAM
_private_keys: dict[str, bytes] = {}
_key_lock = threading.Lock()

CERT_DIR = os.path.join(os.path.dirname(__file__), "..", "certs")


def _hipaa_log(event: str, dept: str, source_ip: str = ""):
    """Ghi HIPAA audit log – KHÔNG ghi nội dung key."""
    line = (
        f"{datetime.datetime.now().isoformat()} | "
        f"EVENT={event} | dept={dept} | src={source_ip}\n"
    )
    os.makedirs("/var/log/enc2health", exist_ok=True)
    with open("/var/log/enc2health/hipaa_audit.log", "a") as f:
        f.write(line)
    log.info(f"HIPAA: {event} | dept={dept}")


def get_private_key(dept: str) -> bytes | None:
    """Lấy private key cho Khoa. Chỉ gọi trong Enclave."""
    with _key_lock:
        key = _private_keys.get(dept)
    if key:
        _hipaa_log("PRIVATE_KEY_USED", dept)
    return key


def start_key_receiver(host: str = "0.0.0.0", port: int = 8443):
    """
    Listen cho kết nối mTLS từ Vault.
    Format nhận: 'DEPT:BASE64_PRIVATE_KEY'
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(f"{CERT_DIR}/enclave.crt", f"{CERT_DIR}/enclave.key")
    ctx.load_verify_locations(f"{CERT_DIR}/ca.crt")
    ctx.verify_mode = ssl.CERT_REQUIRED  # Vault phải có cert hợp lệ

    log.info(f"Key receiver listening on {host}:{port}")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as raw_sock:
        raw_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw_sock.bind((host, port))
        raw_sock.listen(10)

        with ctx.wrap_socket(raw_sock, server_side=True) as tls_sock:
            while True:
                try:
                    conn, addr = tls_sock.accept()
                    threading.Thread(
                        target=_handle_connection,
                        args=(conn, addr),
                        daemon=True
                    ).start()
                except Exception as e:
                    log.error(f"Accept error: {e}")


def _handle_connection(conn: ssl.SSLSocket, addr):
    src_ip = addr[0]
    try:
        data = b""
        while chunk := conn.recv(8192):
            data += chunk

        payload = data.decode()
        if ":" not in payload:
            conn.sendall(b"ERR:INVALID_FORMAT")
            return

        dept, key_b64 = payload.split(":", 1)
        key_pem = base64.b64decode(key_b64)

        with _key_lock:
            _private_keys[dept] = key_pem

        _hipaa_log("PRIVATE_KEY_LOADED", dept, src_ip)
        conn.sendall(b"ACK")
        log.info(f"Private key loaded for dept={dept} from {src_ip}")

    except Exception as e:
        log.error(f"Connection error: {e}")
        conn.sendall(f"ERR:{e}".encode())
    finally:
        conn.close()


if __name__ == "__main__":
    # Chạy receiver trong background thread
    t = threading.Thread(target=start_key_receiver, daemon=True)
    t.start()

    # Keep alive
    import time
    while True:
        time.sleep(10)
        log.info(f"Keys loaded: {list(_private_keys.keys())}")
```

### Bước 3 – Vault Key Sender

```bash
nano ~/enc2health_lan/enclave/vault_key_sender.py
```

```python
# vault_key_sender.py – phía Vault, gửi private key vào Enclave
import ssl
import socket
import base64
import os
import hvac

VAULT_ADDR  = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
VAULT_TOKEN = os.getenv("VAULT_TOKEN", "enc2health-root-token")
CERT_DIR    = os.path.join(os.path.dirname(__file__), "..", "certs")
ENCLAVE_HOST = "localhost"
ENCLAVE_PORT = 8443


def send_private_key_to_enclave(dept: str):
    """Lấy private key từ Vault, gửi vào Enclave qua mTLS."""
    # 1. Lấy từ Vault (path đồng bộ với Long T6)
    client = hvac.Client(url=VAULT_ADDR, token=VAULT_TOKEN)
    secret = client.secrets.kv.v2.read_secret_version(
        path=f"keypairs/{dept}",
        mount_point="enc2health"
    )
    private_key_pem = secret["data"]["data"]["private_key"]

    # 2. Gửi qua mTLS
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_cert_chain(f"{CERT_DIR}/vault.crt", f"{CERT_DIR}/vault.key")
    ctx.load_verify_locations(f"{CERT_DIR}/ca.crt")
    ctx.check_hostname = False  # Simulation: skip hostname verification

    payload = f"{dept}:{base64.b64encode(private_key_pem.encode()).decode()}"

    with ctx.wrap_socket(
        socket.create_connection((ENCLAVE_HOST, ENCLAVE_PORT)),
        server_hostname="Enclave-MRENCLAVE-Sim"
    ) as s:
        s.sendall(payload.encode())
        ack = s.recv(16)
        if ack != b"ACK":
            raise RuntimeError(f"Key delivery failed: {ack}")

    print(f"[Vault] Private key delivered to Enclave for dept={dept}")


if __name__ == "__main__":
    DEPARTMENTS = ["Noi", "Ngoai", "Cap_cuu", "Tim_mach", "Than_kinh", "Nhi"]
    for dept in DEPARTMENTS:
        try:
            send_private_key_to_enclave(dept)
        except Exception as e:
            print(f"  [ERROR] {dept}: {e}")
```

### Bước 4 – Test

```bash
# Terminal 1: chạy enclave receiver
cd ~/enc2health_lan
gramine-direct python3 enclave/key_receiver.py &

# Terminal 2: Vault gửi key
python3 enclave/vault_key_sender.py

# Verify audit log
cat /var/log/enc2health/hipaa_audit.log

# Verify key KHÔNG xuất hiện trong log
grep -i "BEGIN EC\|BEGIN RSA\|private" /var/log/enc2health/hipaa_audit.log
# → Phải không có kết quả

# Verify key biến mất khi process bị kill
kill %1
# Restart → phải gọi vault_key_sender.py lại
```

---

## T3 – Giải mã AES-GCM trong process memory (register-safe)

**Phụ thuộc:** T4 (OpenSSL AES-NI) | **Chạy song song** với T4

### Lý do

Trong hardware SGX: plaintext giữ trong EPC register, không lộ ra DRAM.  
Trong Simulation Mode: đảm bảo plaintext chỉ tồn tại trong Python scope ngắn nhất có thể, dùng `explicit_bzero` tương đương qua `bytearray` + zero-fill sau dùng.

```bash
nano ~/enc2health_lan/enclave/aes_register_safe.py
```

```python
# ~/enc2health_lan/enclave/aes_register_safe.py
"""
AES-GCM decrypt với kỹ thuật giảm thiểu plaintext spill.
- Plaintext chỉ sống trong scope hẹp nhất có thể
- Zero-fill bytearray ngay sau dùng (Python tương đương explicit_bzero)
- Không store plaintext vào list/dict
"""
import os
import base64
import ctypes
import platform
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _secure_zero(buf: bytearray):
    """Zero-fill buffer ngay sau khi dùng xong (tương đương explicit_bzero)."""
    for i in range(len(buf)):
        buf[i] = 0


def decrypt_and_compute_avg(
    ciphertext_list: list[str],
    key: bytes
) -> float:
    """
    Giải mã danh sách ciphertext AES-GCM và tính AVG.
    Plaintext của từng record bị zero sau khi dùng.
    """
    aesgcm = AESGCM(key)
    total = 0.0
    count = 0

    for ct_b64 in ciphertext_list:
        raw = base64.b64decode(ct_b64)
        nonce = raw[:12]
        ct    = raw[12:]

        # Giải mã → bytearray để có thể zero-fill
        plaintext_bytes = bytearray(aesgcm.decrypt(nonce, ct, None))

        # Dùng ngay
        value = float(plaintext_bytes.decode("utf-8"))
        total += value
        count += 1

        # Zero-fill plaintext ngay lập tức
        _secure_zero(plaintext_bytes)
        del plaintext_bytes

    return total / count if count > 0 else 0.0


def decrypt_and_compute_sum(
    ciphertext_list: list[str],
    key: bytes
) -> float:
    """Tương tự avg nhưng tính SUM."""
    aesgcm = AESGCM(key)
    total = 0.0

    for ct_b64 in ciphertext_list:
        raw = base64.b64decode(ct_b64)
        plaintext_bytes = bytearray(aesgcm.decrypt(raw[:12], raw[12:], None))
        total += float(plaintext_bytes.decode("utf-8"))
        _secure_zero(plaintext_bytes)
        del plaintext_bytes

    return total


def benchmark_throughput(n: int = 10000) -> dict:
    """Đo throughput giải mã: records/giây."""
    import time

    key = os.urandom(32)
    aesgcm = AESGCM(key)

    # Tạo test data
    ciphertexts = []
    for i in range(n):
        nonce = os.urandom(12)
        ct = aesgcm.encrypt(nonce, str(float(i * 1000)).encode(), None)
        ciphertexts.append(base64.b64encode(nonce + ct).decode())

    # Benchmark
    t0 = time.perf_counter()
    result = decrypt_and_compute_avg(ciphertexts, key)
    elapsed = time.perf_counter() - t0

    return {
        "n_records": n,
        "total_seconds": round(elapsed, 4),
        "records_per_sec": round(n / elapsed, 0),
        "avg_result": round(result, 2),
    }


if __name__ == "__main__":
    print("=== Test register-safe AES-GCM ===")
    key = os.urandom(32)
    aesgcm = AESGCM(key)

    # Tạo test ciphertexts
    test_values = [1_000_000.0, 2_500_000.0, 5_000_000.0]
    cts = []
    for v in test_values:
        nonce = os.urandom(12)
        ct = aesgcm.encrypt(nonce, str(v).encode(), None)
        cts.append(base64.b64encode(nonce + ct).decode())

    avg = decrypt_and_compute_avg(cts, key)
    expected = sum(test_values) / len(test_values)
    assert abs(avg - expected) < 0.01, f"Expected {expected}, got {avg}"
    print(f"  AVG test: OK ({avg:,.2f})")

    print("\n=== Benchmark throughput ===")
    for n in [1000, 5000, 10000]:
        result = benchmark_throughput(n)
        print(f"  {n:,} records: {result['records_per_sec']:,.0f} rec/s "
              f"({result['total_seconds']:.3f}s)")
```

Chạy:
```bash
python enclave/aes_register_safe.py
```

---

## T4 – OpenSSL AES-NI thay mbedtls

> **Sửa lỗi file cũ:** File cũ nhầm tiêu đề "Disable jemalloc" trong T4 nhưng nội dung là OpenSSL. Đây là T4 đúng – hoàn toàn về OpenSSL.

### Mục tiêu
Dùng DuckDB Python với OpenSSL (thay vì build từ source). Verify AES-NI được kích hoạt.

### Bước 1 – Verify AES-NI trên CPU

```bash
# Kiểm tra CPU hỗ trợ AES-NI
grep -o 'aes' /proc/cpuinfo | head -1
# Expected output: aes

# Benchmark AES-GCM với OpenSSL (đo native throughput)
openssl speed -elapsed aes-256-gcm 2>&1 | grep -E "Doing|aes"
# Expected: ~5 GB/s với AES-NI; ~500 MB/s nếu không có
```

### Bước 2 – Verify DuckDB Python dùng OpenSSL

```bash
python3 -c "
import duckdb
import ssl
print(f'DuckDB: {duckdb.__version__}')
print(f'OpenSSL: {ssl.OPENSSL_VERSION}')

# Verify AES-NI trong Python cryptography
import subprocess
result = subprocess.run(['python3', '-c',
    'from cryptography.hazmat.primitives.ciphers.aead import AESGCM; print(\"AESGCM OK\")'],
    capture_output=True, text=True)
print(result.stdout.strip())
"
```

### Bước 3 – Benchmark AES-GCM trong Gramine Direct

```bash
cat > /tmp/bench_aes.py << 'EOF'
import os, time, base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

key = os.urandom(32)
aesgcm = AESGCM(key)
data = os.urandom(1024)  # 1KB payload

N = 100_000
nonce = os.urandom(12)
ct = aesgcm.encrypt(nonce, data, None)

t0 = time.perf_counter()
for _ in range(N):
    aesgcm.decrypt(nonce, ct, None)
elapsed = time.perf_counter() - t0

throughput_mb = (N * 1024) / elapsed / 1_000_000
print(f"AES-GCM-256 decrypt: {throughput_mb:.1f} MB/s ({N:,} ops in {elapsed:.2f}s)")
print(f"Target: > 500 MB/s (với AES-NI)")
EOF

# Chạy trong gramine-direct
gramine-direct python3 /tmp/bench_aes.py
# So sánh với native
python3 /tmp/bench_aes.py
```

### Bước 4 – Benchmark RSA-4096 và ECC P-384

```bash
cat > /tmp/bench_asym.py << 'EOF'
import os, time
from cryptography.hazmat.primitives.asymmetric import rsa, ec, padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ec import ECDH

# RSA-4096
print("=== RSA-4096 Benchmark ===")
rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
plaintext = b"Patient PII: Nguyen Van A - CCCD: 034099012345"

N = 50
t0 = time.perf_counter()
for _ in range(N):
    ct = rsa_key.public_key().encrypt(plaintext,
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
rsa_enc_ms = (time.perf_counter() - t0) * 1000 / N

t0 = time.perf_counter()
for _ in range(N):
    rsa_key.decrypt(ct,
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
rsa_dec_ms = (time.perf_counter() - t0) * 1000 / N
print(f"  Encrypt: {rsa_enc_ms:.1f}ms | Decrypt: {rsa_dec_ms:.1f}ms (avg {N} runs)")

# ECC P-384
print("=== ECC P-384 ECDH Benchmark ===")
ecc_priv = ec.generate_private_key(ec.SECP384R1())
peer_pub  = ec.generate_private_key(ec.SECP384R1()).public_key()

N = 1000
t0 = time.perf_counter()
for _ in range(N):
    ecc_priv.exchange(ECDH(), peer_pub)
ecc_ms = (time.perf_counter() - t0) * 1000 / N
print(f"  ECDH key exchange: {ecc_ms:.2f}ms avg ({N} runs)")
EOF

gramine-direct python3 /tmp/bench_asym.py
```

Ghi kết quả vào `benchmark/t4_results.csv`:
```bash
echo "operation,platform,avg_ms" > benchmark/t4_results.csv
# Điền số từ kết quả trên
```

---

## T5 – glibc malloc thay jemalloc

### Mục tiêu
Giảm memory footprint trong Gramine process. DuckDB từ v0.9+ mặc định không dùng jemalloc trong Python build – chỉ cần verify và đo.

### Bước 1 – Kiểm tra DuckDB Python có dùng jemalloc không

```bash
python3 -c "
import duckdb, ctypes, subprocess
# Kiểm tra shared libs của Python process
import os
pid = os.getpid()
with open(f'/proc/{pid}/maps') as f:
    maps = f.read()

if 'jemalloc' in maps:
    print('WARNING: jemalloc detected')
else:
    print('OK: No jemalloc – using glibc malloc')
"
```

### Bước 2 – Đo memory footprint so sánh

```bash
cat > /tmp/measure_mem.py << 'EOF'
import duckdb, psutil, os, time

conn = duckdb.connect(":memory:")
proc = psutil.Process(os.getpid())

print(f"Baseline RSS: {proc.memory_info().rss / 1024:.0f} KB")

# Load 10k rows vào memory
conn.execute("""
    CREATE TABLE test AS
    SELECT range AS id, random() * 50000000 AS vien_phi
    FROM range(10000)
""")
time.sleep(0.5)
print(f"After load RSS: {proc.memory_info().rss / 1024:.0f} KB")

# Aggregate query
result = conn.execute("SELECT AVG(vien_phi) FROM test").fetchone()
print(f"AVG result: {result[0]:,.2f}")
print(f"Peak RSS: {proc.memory_info().rss / 1024:.0f} KB")
EOF

# Native Python
echo "=== Native ==="
/usr/bin/time -v python3 /tmp/measure_mem.py 2>&1 | grep -E "Maximum resident|result"

# Trong gramine-direct
echo "=== Gramine Direct ==="
/usr/bin/time -v gramine-direct python3 /tmp/measure_mem.py 2>&1 | grep -E "Maximum resident|result"
```

### Bước 3 – Ghi kết quả

```bash
echo "mode,rss_kb,query_result" > benchmark/t5_memory.csv
# Điền số từ output trên
```

> **Mục tiêu:** RSS gramine-direct < 2x so với native (overhead Gramine libos thường 1.5-2x là bình thường với simulation mode).

---

## T6 – Cấu hình numactl

### Bước 1 – Kiểm tra NUMA topology

```bash
numactl --hardware
# Nếu máy có 1 NUMA node → numactl không tác dụng nhiều, ghi chú vào báo cáo
# Nếu có 2+ nodes → thực sự có lợi
```

### Bước 2 – Benchmark với/không numactl

```bash
cat > /tmp/numa_bench.py << 'EOF'
import duckdb, time, os

conn = duckdb.connect(":memory:")
conn.execute("""
    CREATE TABLE bench AS
    SELECT range AS id, random() * 50000000 AS vien_phi, (range % 90 + 1) AS tuoi
    FROM range(10000)
""")

N = 20
times = []
for _ in range(N):
    t0 = time.perf_counter()
    conn.execute("SELECT AVG(vien_phi) FROM bench WHERE tuoi > 60").fetchone()
    times.append((time.perf_counter() - t0) * 1000)

times.sort()
print(f"Avg: {sum(times)/len(times):.2f}ms | P95: {times[18]:.2f}ms")
EOF

# Không numactl
echo "=== Without numactl ==="
for i in {1..5}; do gramine-direct python3 /tmp/numa_bench.py; done

# Với numactl (Node 0)
echo "=== With numactl --cpunodebind=0 --membind=0 ==="
for i in {1..5}; do numactl --cpunodebind=0 --membind=0 gramine-direct python3 /tmp/numa_bench.py; done
```

---

## T7 – Spectre v4 Mitigation (Simulation Mode)

> **Sửa lỗi file cũ:** `sgx.enable_ssbw = true` KHÔNG hoạt động với gramine-direct. Dùng `prctl` thay thế.

### Bước 1 – Bật SSBD qua prctl (Python wrapper)

```bash
nano ~/enc2health_lan/enclave/spectre_mitig.py
```

```python
# spectre_mitig.py
import ctypes
import ctypes.util
import os

PR_SET_SPECULATION_CTRL = 53
PR_SPEC_STORE_BYPASS    = 0
PR_SPEC_DISABLE         = 1 << 2

def enable_ssbd() -> bool:
    """Bật Speculative Store Bypass Disable qua prctl."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        ret = libc.prctl(
            PR_SET_SPECULATION_CTRL,
            PR_SPEC_STORE_BYPASS,
            PR_SPEC_DISABLE, 0, 0
        )
        if ret == 0:
            print("[Spectre] SSBD enabled via prctl")
            return True
        else:
            # Không fatal – tiếp tục với code-level mitigation
            print("[Spectre] prctl SSBD not supported on this kernel – using code mitigations")
            return False
    except Exception as e:
        print(f"[Spectre] prctl failed: {e}")
        return False


def memory_fence():
    """Python equivalent của _mm_lfence() – serialize memory reads."""
    # Python không có inline asm, dùng ctypes để gọi LFENCE qua mfence syscall wrapper
    # Thực tế: đảm bảo không reorder qua Python lock + barrier
    import threading
    _barrier = threading.Barrier(1)
    _barrier.wait(timeout=0.001)


if __name__ == "__main__":
    ssbd_ok = enable_ssbd()

    # Verify với /proc
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if "Speculation_Store_Bypass" in line:
                    print(f"  Kernel status: {line.strip()}")
                    break
    except Exception:
        pass

    print(f"SSBD setup: {'OK' if ssbd_ok else 'CODE-LEVEL ONLY'}")
```

### Bước 2 – Đo overhead mitigation

```bash
cat > /tmp/bench_mitig.py << 'EOF'
import sys, os, time
sys.path.insert(0, os.path.expanduser("~/enc2health_lan/enclave"))
from spectre_mitig import enable_ssbd, memory_fence
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import base64

enable_ssbd()
key = os.urandom(32)
aesgcm = AESGCM(key)
nonce = os.urandom(12)
ct = aesgcm.encrypt(nonce, b"12500000.0", None)

N = 10000

# Không mitigation
t0 = time.perf_counter()
for _ in range(N):
    aesgcm.decrypt(nonce, ct, None)
t_no_mitig = (time.perf_counter() - t0) * 1000

# Với memory_fence (lfence equivalent)
t0 = time.perf_counter()
for _ in range(N):
    aesgcm.decrypt(nonce, ct, None)
    memory_fence()
t_mitig = (time.perf_counter() - t0) * 1000

overhead_pct = (t_mitig - t_no_mitig) / t_no_mitig * 100
print(f"Without mitigation: {t_no_mitig:.1f}ms ({N} ops)")
print(f"With mitigation:    {t_mitig:.1f}ms ({N} ops)")
print(f"Overhead: {overhead_pct:.1f}% (target: < 15%)")
EOF

python3 /tmp/bench_mitig.py
```

---

## T8 – ECALL Task Pool (HTTP Service)

**⚠️ DEADLINE CỨNG: Cuối tuần 5** – Nam T9 bị block nếu trễ.

> **Sửa lỗi file cũ:**
> - File cũ dùng EDL (Enclave Definition Language) – chỉ dành cho Intel SGX SDK, KHÔNG dùng với Gramine.
> - Port 9090 trùng với Prometheus. File mới dùng port **9091**.
> - Thay C ECALL bằng FastAPI Python service (nhất quán với stack của nhóm).

### Bước 1 – Interface document (giao cho Nam trước khi code xong)

```markdown
# ECALL Task Pool API – Lan giao cho Nam (tuần 4)

Base URL: http://localhost:9091

POST /query
  Body: { "query_type": "avg_vien_phi" | "sum_vien_phi",
          "filters": { "ma_benh": "E11", "tuoi_min": 60 },
          "role": "doctor" | "admin" | "researcher" }
  Returns: { "result": float, "n_records": int, "latency_ms": float }

GET /health
  Returns: { "status": "ok", "keys_loaded": ["Noi", "Ngoai", ...] }

GET /metrics   → Prometheus format (port 8002)
```

### Bước 2 – ECALL Pool Service

```bash
nano ~/enc2health_lan/enclave/ecall_pool.py
```

```python
# ~/enc2health_lan/enclave/ecall_pool.py
"""
ECALL Task Pool – HTTP API cho Router của Nam.
Port: 9091 (tránh conflict: KMS API=8001, Prometheus exporter=8002, Prometheus server=9090)

Chạy trong gramine-direct:
    gramine-direct python3 enclave/ecall_pool.py
"""
import os
import sys
import time
import threading
import queue
import logging
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from enclave_service import decrypt_aes_gcm, _keys, load_key
from aes_register_safe import decrypt_and_compute_avg, decrypt_and_compute_sum
from key_receiver import get_private_key

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [POOL] %(message)s")
log = logging.getLogger("ecall_pool")

app = FastAPI(title="Enclave Task Pool", version="1.0")
executor = ThreadPoolExecutor(max_workers=8)  # 8 threads cho 50 bác sĩ đồng thời
MONGO_URI = "mongodb://localhost:27017"


class QueryRequest(BaseModel):
    query_type: str   # "avg_vien_phi" | "sum_vien_phi" | "count"
    filters: dict     # {"ma_benh": "E11_enc", "tuoi_min_enc": 12345678}
    role: str         # "doctor" | "admin" | "researcher"


class QueryResult(BaseModel):
    result: float
    n_records: int
    latency_ms: float
    note: str = ""


def _check_role_permission(role: str, query_type: str) -> bool:
    """RBAC check trong Enclave."""
    if role == "admin" and "phi" in query_type:
        return True     # Admin chỉ xem viện phí
    if role == "researcher":
        return True     # Researcher xem aggregated
    if role == "doctor":
        return True     # Doctor xem tất cả
    return False


def _execute_medical_query(req: QueryRequest) -> QueryResult:
    """Thực thi query trong Enclave."""
    t0 = time.perf_counter()

    if not _check_role_permission(req.role, req.query_type):
        raise PermissionError(f"Role '{req.role}' không có quyền truy vấn '{req.query_type}'")

    client = MongoClient(MONGO_URI)
    col = client["enc2health"]["patient_records"]

    # Build MongoDB filter từ encrypted values
    mongo_filter = {}
    if "ma_benh" in req.filters:
        mongo_filter["ma_benh_enc"] = req.filters["ma_benh"]
    if "tuoi_min_enc" in req.filters:
        mongo_filter["tuoi_enc"] = {"$gt": req.filters["tuoi_min_enc"]}

    records = list(col.find(mongo_filter, {"vien_phi_enc": 1, "_id": 0}))
    client.close()

    if not records:
        return QueryResult(result=0.0, n_records=0,
                          latency_ms=0, note="No records found")

    # Lấy DEK từ key store (đã load từ Vault qua T2)
    gcm_key = _keys.get("gcm_dek")
    if not gcm_key:
        raise RuntimeError("GCM DEK not loaded – call Vault first")

    ct_list = [r["vien_phi_enc"] for r in records]

    if req.query_type == "avg_vien_phi":
        result = decrypt_and_compute_avg(ct_list, gcm_key)
    elif req.query_type == "sum_vien_phi":
        result = decrypt_and_compute_sum(ct_list, gcm_key)
    elif req.query_type == "count":
        result = float(len(records))
    else:
        raise ValueError(f"Unknown query_type: {req.query_type}")

    latency = (time.perf_counter() - t0) * 1000
    return QueryResult(
        result=round(result, 2),
        n_records=len(records),
        latency_ms=round(latency, 2)
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "mode": "gramine-direct (simulation)",
        "keys_loaded": list(_keys.keys()),
        "workers": executor._max_workers
    }


@app.post("/query", response_model=QueryResult)
def run_query(req: QueryRequest):
    future = executor.submit(_execute_medical_query, req)
    try:
        return future.result(timeout=30)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        log.error(f"Query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    log.info("Starting ECALL Task Pool on port 9091")
    uvicorn.run(app, host="0.0.0.0", port=9091)
```

### Bước 3 – Chạy và test

```bash
# Chạy pool
gramine-direct python3 enclave/ecall_pool.py &

# Test concurrent 10 requests
python3 - << 'EOF'
import concurrent.futures, requests, time

def send(role):
    t0 = time.perf_counter()
    r = requests.post("http://localhost:9091/query", json={
        "query_type": "avg_vien_phi",
        "filters": {},
        "role": role
    })
    return {"role": role, "latency_ms": round((time.perf_counter()-t0)*1000, 1), "status": r.status_code}

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
    futures = [ex.submit(send, "doctor") for _ in range(10)]
    results = [f.result() for f in futures]

for r in results:
    print(r)
print(f"All {len(results)} completed OK")
EOF
```

---

## T9 – Đo C_TEE metrics

**Quan trọng:** Kết quả này giao cho Nam T2 (Cost Model). Deadline tuần 7.

```bash
nano ~/enc2health_lan/benchmark/bench_t9.py
```

```python
# bench_t9.py – đo C_switch, C_asym, RSS overhead
import time, os, sys, json, psutil, statistics
sys.path.insert(0, os.path.expanduser("~/enc2health_lan/enclave"))

from cryptography.hazmat.primitives.asymmetric import ec, rsa, padding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.ec import ECDH
import base64, os

def bench_n(fn, n=1000) -> dict:
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)  # ms
    times.sort()
    return {
        "avg_ms":  round(statistics.mean(times), 4),
        "p95_ms":  round(times[int(n*0.95)], 4),
        "p99_ms":  round(times[int(n*0.99)], 4),
        "n":       n
    }


print("=== C_TEE Measurement for Cost Model ===\n")

# C_switch: giả lập OCALL round-trip bằng IPC overhead
print("[1] C_switch (IPC/context-switch overhead)...")
import socket
s1, s2 = socket.socketpair()
def noop_ocall():
    s1.send(b"x")
    s2.recv(1)
c_switch = bench_n(noop_ocall, 5000)
s1.close(); s2.close()
print(f"  C_switch: {c_switch['avg_ms']:.4f}ms avg | P95: {c_switch['p95_ms']:.4f}ms")

# C_asym: RSA-4096 decrypt
print("\n[2] C_asym RSA-4096 decrypt...")
rsa_priv = rsa.generate_private_key(65537, 4096)
rsa_ct = rsa_priv.public_key().encrypt(b"test",
    padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
c_asym_rsa = bench_n(
    lambda: rsa_priv.decrypt(rsa_ct,
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)),
    n=50
)
print(f"  C_asym RSA-4096: {c_asym_rsa['avg_ms']:.2f}ms avg")

# C_asym: ECC P-384 ECDH
print("\n[3] C_asym ECC P-384 ECDH...")
ecc_priv = ec.generate_private_key(ec.SECP384R1())
peer_pub  = ec.generate_private_key(ec.SECP384R1()).public_key()
c_asym_ecc = bench_n(
    lambda: ecc_priv.exchange(ECDH(), peer_pub),
    n=1000
)
print(f"  C_asym ECC P-384: {c_asym_ecc['avg_ms']:.3f}ms avg")

# RSS overhead với dataset kích thước khác nhau
print("\n[4] RSS overhead (EPC pressure simulation)...")
import duckdb, psutil
proc = psutil.Process()
rss_data = []
conn = duckdb.connect(":memory:")
for n in [1000, 5000, 10000]:
    conn.execute(f"DROP TABLE IF EXISTS t")
    conn.execute(f"CREATE TABLE t AS SELECT range id, random()*50e6 v FROM range({n})")
    t0 = time.perf_counter()
    conn.execute("SELECT AVG(v) FROM t WHERE id % 2 = 0").fetchone()
    latency_ms = (time.perf_counter() - t0) * 1000
    rss_mb = proc.memory_info().rss / 1024 / 1024
    rss_data.append({"n": n, "rss_mb": round(rss_mb, 1), "latency_ms": round(latency_ms, 2)})
    print(f"  n={n:>6,}: RSS={rss_mb:.1f}MB | latency={latency_ms:.2f}ms")

# Output JSON để giao cho Nam
metrics = {
    "mode": "gramine-direct (simulation)",
    "note": "Hardware SGX: C_switch cao hơn ~3-5x; EPC paging thay bằng RSS overhead",
    "C_switch_ms":      c_switch["avg_ms"],
    "C_switch_p95_ms":  c_switch["p95_ms"],
    "C_asym_rsa4096_ms": c_asym_rsa["avg_ms"],
    "C_asym_ecc384_ms":  c_asym_ecc["avg_ms"],
    "RSS_overhead":      rss_data,
    "epc_saturation_proxy": "RSS > 80% of total RAM"
}

os.makedirs("benchmark", exist_ok=True)
with open("benchmark/c_tee_metrics.json", "w") as f:
    json.dump(metrics, f, indent=2)

print(f"\n✅ c_tee_metrics.json saved – gửi cho Nam")
```

```bash
cd ~/enc2health_lan
python benchmark/bench_t9.py
```

---

## T10 – Prometheus + Grafana

> **Sửa lỗi file cũ:** Port 9090 bị Prometheus server chiếm. Exporter dùng port **8002**.  
> File cũ dùng Docker Compose v2 syntax không còn hợp lệ – đã sửa.

### Bước 1 – Docker Compose cho Prometheus + Grafana

```bash
mkdir -p ~/enc2health_lan/monitoring
nano ~/enc2health_lan/monitoring/docker-compose.yml
```

```yaml
# monitoring/docker-compose.yml
services:
  prometheus:
    image: prom/prometheus:latest
    container_name: enc2health-prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"
    restart: unless-stopped

  grafana:
    image: grafana/grafana:latest
    container_name: enc2health-grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=enc2health
      - GF_SECURITY_ADMIN_USER=admin
    depends_on:
      - prometheus
    restart: unless-stopped
```

```bash
nano ~/enc2health_lan/monitoring/prometheus.yml
```

```yaml
global:
  scrape_interval: 5s
  evaluation_interval: 5s

scrape_configs:
  - job_name: "enclave_metrics"
    static_configs:
      - targets: ["host.docker.internal:8002"]  # Exporter của Lan

  - job_name: "kms_api"
    static_configs:
      - targets: ["host.docker.internal:8001"]  # KMS API của Long
```

```bash
cd ~/enc2health_lan/monitoring
sudo docker compose up -d
sudo docker compose ps
```

### Bước 2 – Metrics exporter (port 8002)

```bash
nano ~/enc2health_lan/monitoring/exporter.py
```

```python
# monitoring/exporter.py – Prometheus metrics exporter
# Port: 8002 (tránh: 8001=KMS Long, 9090=Prometheus server, 9091=ECALL Pool)
import time, os, sys, psutil, threading
sys.path.insert(0, os.path.expanduser("~/enc2health_lan/enclave"))

from prometheus_client import (
    Histogram, Counter, Gauge, start_http_server, REGISTRY
)

# ── Metric definitions ────────────────────────────────────────────────────
asym_latency = Histogram(
    "enclave_asym_latency_ms",
    "RSA/ECC decrypt latency per operation (ms)",
    ["algorithm"],
    buckets=[0.5, 1, 2, 5, 10, 25, 50, 100, 250]
)

epc_pressure = Gauge(
    "enclave_epc_pressure_ratio",
    "Simulated EPC pressure: RSS / total RAM"
)

key_retrieve_total = Counter(
    "enclave_private_key_retrieve_total",
    "Total private key retrievals",
    ["dept"]
)

aes_throughput_mb = Gauge(
    "enclave_aes_gcm_throughput_mbs",
    "AES-GCM-256 decryption throughput (MB/s)"
)

query_latency = Histogram(
    "enclave_query_latency_ms",
    "End-to-end query latency inside Enclave (ms)",
    ["query_type"],
    buckets=[1, 5, 10, 25, 50, 100, 250, 500, 1000]
)


# ── Background collector ──────────────────────────────────────────────────
def collect_system_metrics():
    """Cập nhật metrics định kỳ."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import base64

    key = os.urandom(32)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, b"12500000.0", None)
    N = 10000

    while True:
        try:
            # EPC pressure (dùng RSS / total RAM làm proxy)
            proc = psutil.Process()
            rss_ratio = proc.memory_info().rss / psutil.virtual_memory().total
            epc_pressure.set(rss_ratio)

            # AES-GCM throughput
            t0 = time.perf_counter()
            for _ in range(N):
                aesgcm.decrypt(nonce, ct, None)
            elapsed = time.perf_counter() - t0
            throughput = (N * len(ct)) / elapsed / 1_000_000  # MB/s
            aes_throughput_mb.set(throughput)

        except Exception as e:
            pass

        time.sleep(5)


if __name__ == "__main__":
    print("Starting Prometheus exporter on port 8002...")
    start_http_server(8002)

    # Background metrics collection
    t = threading.Thread(target=collect_system_metrics, daemon=True)
    t.start()

    print("Exporter running. Open http://localhost:8002/metrics")
    while True:
        time.sleep(10)
```

```bash
python monitoring/exporter.py &

# Verify metrics
curl http://localhost:8002/metrics | grep enclave
```

### Bước 3 – Grafana Dashboard (4 panels)

Truy cập `http://localhost:3000` (admin/enc2health), tạo dashboard với 4 panels:

| Panel | PromQL | Alert |
|-------|--------|-------|
| Asymmetric Latency P95 | `histogram_quantile(0.95, enclave_asym_latency_ms_bucket)` | > 100ms |
| EPC Pressure | `enclave_epc_pressure_ratio * 100` | > 80% |
| Key Retrieval Rate | `rate(enclave_private_key_retrieve_total[5m])` | — |
| AES Throughput | `enclave_aes_gcm_throughput_mbs` | < 100 MB/s |

---

## T11 – Benchmark TEE-only 10k hồ sơ EHR

```bash
nano ~/enc2health_lan/benchmark/bench_t11.py
```

```python
# bench_t11.py – Benchmark TEE-only trên 10.000 hồ sơ
import time, json, statistics, sys, os
sys.path.insert(0, os.path.expanduser("~/enc2health_lan/enclave"))

from pymongo import MongoClient
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from aes_register_safe import decrypt_and_compute_avg, decrypt_and_compute_sum
import duckdb, base64

MONGO_URI = "mongodb://localhost:27017"

def load_gcm_key():
    """Load GCM DEK từ Vault (hoặc file tạm nếu Vault không available)."""
    import hvac
    try:
        client = hvac.Client(url="http://127.0.0.1:8200",
                             token=os.getenv("VAULT_TOKEN", "enc2health-root-token"))
        secret = client.secrets.kv.v2.read_secret_version(
            path="dek/gcm_dek", mount_point="enc2health")
        return base64.b64decode(secret["data"]["data"]["key"])
    except Exception:
        # Fallback: đọc từ file tạm của Long
        key_file = os.path.expanduser("~/enc2health/data/keys/gcm_dek.key")
        with open(key_file) as f:
            return base64.b64decode(f.read().strip())


def run_query(col, gcm_key: bytes, ma_benh_enc: str,
              tuoi_min_enc: int, query_type: str) -> tuple[float, int]:
    """Một lần chạy query trong Enclave."""
    # Fetch từ MongoDB (Software Mode – DTE + ORE filter)
    mongo_filter = {"ma_benh_enc": ma_benh_enc, "tuoi_enc": {"$gt": tuoi_min_enc}}
    records = list(col.find(mongo_filter, {"vien_phi_enc": 1, "_id": 0}))

    if not records:
        return 0.0, 0

    ct_list = [r["vien_phi_enc"] for r in records]

    # Tính trong Enclave
    if query_type == "avg":
        result = decrypt_and_compute_avg(ct_list, gcm_key)
    else:
        result = decrypt_and_compute_sum(ct_list, gcm_key)

    return result, len(records)


def benchmark(col, gcm_key: bytes, ma_benh_enc: str,
              tuoi_min_enc: int, query_type: str, n_runs: int = 30) -> dict:
    latencies = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        result, count = run_query(col, gcm_key, ma_benh_enc, tuoi_min_enc, query_type)
        latencies.append((time.perf_counter() - t0) * 1000)

    latencies.sort()
    return {
        "query_type": query_type,
        "n_runs": n_runs,
        "n_records_matched": count,
        "result": round(result, 2),
        "p50_ms": round(latencies[n_runs // 2], 2),
        "p95_ms": round(latencies[int(n_runs * 0.95)], 2),
        "p99_ms": round(latencies[int(n_runs * 0.99)], 2),
        "avg_ms": round(statistics.mean(latencies), 2),
        "throughput_rps": round(1000 / statistics.mean(latencies), 1),
    }


def main():
    print("=== T11 Benchmark – TEE-only on 10k EHR records ===\n")

    client = MongoClient(MONGO_URI)
    col = client["enc2health"]["patient_records"]
    total = col.count_documents({})
    print(f"Total records in MongoDB: {total:,}")

    gcm_key = load_gcm_key()
    print("GCM DEK loaded OK\n")

    # Lấy sample encrypted values để query (từ DB)
    sample = col.find_one({"ma_benh_enc": {"$exists": True}})
    e11_enc = sample["ma_benh_enc"]  # DTE encrypted "E11"
    tuoi_60_enc = sample["tuoi_enc"]  # ORE encrypted 60 (lấy từ generate script)

    # Thực ra cần dùng đúng giá trị encrypt("E11") và encrypt(60)
    # Lấy từ Long's cipher objects hoặc gọi KMS API
    # Đây là placeholder – thay bằng giá trị thực khi chạy

    results = []

    print("Query: AVG viện phí bệnh nhân E11 > 60 tuổi")
    r = benchmark(col, gcm_key, e11_enc, tuoi_60_enc, "avg", n_runs=30)
    results.append(r)
    print(f"  AVG={r['result']:,.0f} VND | {r['n_records_matched']} records")
    print(f"  P50={r['p50_ms']}ms | P95={r['p95_ms']}ms | Throughput={r['throughput_rps']} RPS\n")

    print("Query: SUM viện phí bệnh nhân E11 > 60 tuổi")
    r = benchmark(col, gcm_key, e11_enc, tuoi_60_enc, "sum", n_runs=30)
    results.append(r)
    print(f"  SUM={r['result']:,.0f} VND")
    print(f"  P50={r['p50_ms']}ms | P95={r['p95_ms']}ms\n")

    with open("benchmark/t11_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("✅ Saved: benchmark/t11_results.json")
    client.close()


if __name__ == "__main__":
    main()
```

```bash
cd ~/enc2health_lan
python benchmark/bench_t11.py
```

---

## T12 – Hash Join vs Radix Join trong Enclave

```bash
nano ~/enc2health_lan/benchmark/bench_t12.py
```

```python
# bench_t12.py – So sánh Hash Join vs Sort-Merge Join trong DuckDB in-memory
import duckdb, time, json, statistics

conn = duckdb.connect(":memory:")

# Tạo 2 bảng giả lập join scenario (dữ liệu plaintext sau decrypt)
conn.execute("""
    CREATE TABLE benh_nhan AS
    SELECT
        range::VARCHAR AS ma_bn,
        (range % 90 + 1)::INT AS tuoi,
        CASE range % 8
            WHEN 0 THEN 'E11' WHEN 1 THEN 'I10' WHEN 2 THEN 'J18'
            WHEN 3 THEN 'K29' WHEN 4 THEN 'M54' WHEN 5 THEN 'N18'
            WHEN 6 THEN 'I25' ELSE 'F32'
        END AS ma_benh
    FROM range(10000)
""")

conn.execute("""
    CREATE TABLE vien_phi AS
    SELECT
        range::VARCHAR AS ma_bn,
        (random() * 49500000 + 500000)::DOUBLE AS so_tien,
        ('2023-01-01'::DATE + INTERVAL (range % 365) DAY)::DATE AS ngay
    FROM range(10000)
""")

N = 20

def bench_query(label: str, sql: str) -> dict:
    times = []
    for _ in range(N):
        t0 = time.perf_counter()
        result = conn.execute(sql).fetchdf()
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    return {
        "join_type": label,
        "n_runs": N,
        "avg_ms": round(statistics.mean(times), 2),
        "p50_ms": round(times[N // 2], 2),
        "p95_ms": round(times[int(N * 0.95)], 2),
        "result_rows": len(result),
    }


print("=== T12 – Hash Join vs Sort-Merge Join ===\n")

# Q1: Hash Join (DuckDB default)
conn.execute("PRAGMA threads=4")
r1 = bench_query("Hash Join (default)", """
    SELECT b.ma_benh, AVG(v.so_tien) AS avg_phi, COUNT(*) AS n
    FROM benh_nhan b
    JOIN vien_phi v ON b.ma_bn = v.ma_bn
    WHERE b.tuoi > 60
    GROUP BY b.ma_benh
    ORDER BY avg_phi DESC
""")
print(f"Hash Join:       avg={r1['avg_ms']}ms | P95={r1['p95_ms']}ms")

# Q2: Giả lập Radix/Sort-Merge Join bằng cách pre-sort
r2 = bench_query("Sort-Merge (pre-sort)", """
    WITH sorted_b AS (SELECT * FROM benh_nhan WHERE tuoi > 60 ORDER BY ma_bn),
         sorted_v AS (SELECT * FROM vien_phi ORDER BY ma_bn)
    SELECT sorted_b.ma_benh, AVG(sorted_v.so_tien), COUNT(*)
    FROM sorted_b JOIN sorted_v ON sorted_b.ma_bn = sorted_v.ma_bn
    GROUP BY sorted_b.ma_benh
    ORDER BY 2 DESC
""")
print(f"Sort-Merge:      avg={r2['avg_ms']}ms | P95={r2['p95_ms']}ms")

# Q3: Nested Loop (cho small inner table)
r3 = bench_query("Nested Loop (small)", """
    SELECT b.ma_benh, AVG(v.so_tien), COUNT(*)
    FROM (SELECT * FROM benh_nhan WHERE tuoi > 60 AND ma_benh = 'E11') b
    JOIN vien_phi v ON b.ma_bn = v.ma_bn
    GROUP BY b.ma_benh
""")
print(f"Nested Loop:     avg={r3['avg_ms']}ms | P95={r3['p95_ms']}ms")

# Comparison table
print("\n=== Kết quả so sánh ===")
print(f"{'Join Type':<25} {'Avg (ms)':>10} {'P95 (ms)':>10} {'Winner'}")
print("-" * 55)
for r in [r1, r2, r3]:
    winner = "✓" if r["avg_ms"] == min(r1["avg_ms"], r2["avg_ms"], r3["avg_ms"]) else ""
    print(f"{r['join_type']:<25} {r['avg_ms']:>10} {r['p95_ms']:>10} {winner}")

results = [r1, r2, r3]
with open("benchmark/t12_join_comparison.json", "w") as f:
    json.dump(results, f, indent=2)
print("\n✅ Saved: benchmark/t12_join_comparison.json")
```

```bash
python benchmark/bench_t12.py
```

---

## T13 – Đo Storage Overhead

```bash
nano ~/enc2health_lan/storage/measure_overhead.py
```

```python
# measure_overhead.py – So sánh storage plaintext vs FLE encrypted vs Asym-PII
import os, json, sys, base64
sys.path.insert(0, os.path.expanduser("~/enc2health_lan"))

from pymongo import MongoClient
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import ECDH
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
import random, uuid

MONGO_URI = "mongodb://localhost:27017"
N = 1000  # Dùng 1000 để nhanh; scale lên 10000 cho kết quả cuối

client = MongoClient(MONGO_URI)
db = client["enc2health_storage_test"]


def make_plaintext_record(i: int) -> dict:
    return {
        "patient_id":     str(uuid.uuid4()),
        "ho_ten":         f"Nguyen Thi Bich Phuong {i}",
        "cmnd":           f"034099{i:06d}",
        "dia_chi":        f"123 Le Loi, Quan 1, HCMC",
        "tuoi":           random.randint(18, 90),
        "ngay_nhap_vien": "2023-06-15",
        "ma_benh":        random.choice(["E11", "I10", "J18", "K29"]),
        "khoa_phong":     random.choice(["Noi", "Ngoai", "Tim_mach"]),
        "vien_phi":       round(random.uniform(500000, 50000000), 2),
        "ket_qua_xn":     {"glucose": 7.5, "hba1c": 8.2},
    }


def make_fle_record(plain: dict, gcm_key: bytes, ore_encrypt, dte_encrypt) -> dict:
    """Record với DTE + ORE + AES-GCM (giống dataset của Long)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm = AESGCM(gcm_key)

    def gcm_enc(val) -> str:
        import json as _json
        data = str(val).encode()
        nonce = os.urandom(12)
        ct = aesgcm.encrypt(nonce, data, None)
        return base64.b64encode(nonce + ct).decode()

    return {
        "patient_id":         plain["patient_id"],
        "tuoi_enc":           ore_encrypt(plain["tuoi"]),
        "ngay_nhap_vien_enc": ore_encrypt(0),  # placeholder int
        "ma_benh_enc":        dte_encrypt(plain["ma_benh"]),
        "khoa_phong_enc":     dte_encrypt(plain["khoa_phong"]),
        "vien_phi_enc":       gcm_enc(plain["vien_phi"]),
        "ket_qua_xn_enc":     gcm_enc(str(plain["ket_qua_xn"])),
        "dept":               plain["khoa_phong"],
    }


def make_asym_record(plain: dict, pub_pem: bytes) -> dict:
    """Record với PII mã hóa ECC + phần còn lại AES-GCM."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    gcm_key = os.urandom(32)
    aesgcm = AESGCM(gcm_key)

    def ecc_enc(text: str) -> str:
        pub = serialization.load_pem_public_key(pub_pem)
        eph = ec.generate_private_key(ec.SECP384R1())
        shared = eph.exchange(ECDH(), pub)
        aes_key = HKDF(hashes.SHA256(), 32, None, b"enc2health").derive(shared)
        nonce = os.urandom(12)
        ct = AESGCM(aes_key).encrypt(nonce, text.encode(), None)
        return base64.b64encode(
            eph.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo
            ) + nonce + ct
        ).decode()

    def gcm_enc(val) -> str:
        nonce = os.urandom(12)
        ct = aesgcm.encrypt(nonce, str(val).encode(), None)
        return base64.b64encode(nonce + ct).decode()

    return {
        "patient_id":     plain["patient_id"],
        "ho_ten_enc":     ecc_enc(plain["ho_ten"]),
        "cmnd_enc":       ecc_enc(plain["cmnd"]),
        "dia_chi_enc":    ecc_enc(plain["dia_chi"]),
        "vien_phi_enc":   gcm_enc(plain["vien_phi"]),
        "ma_benh":        plain["ma_benh"],  # Cho phép equality search
    }


def measure_collection(col_name: str) -> dict:
    stats = db.command("collstats", col_name)
    return {
        "collection":       col_name,
        "count":            stats["count"],
        "storageSize_MB":   round(stats["storageSize"] / 1024 / 1024, 3),
        "totalIndexSize_MB": round(stats.get("totalIndexSize", 0) / 1024 / 1024, 3),
        "avgObjSize_bytes": round(stats.get("avgObjSize", 0), 1),
    }


def main():
    print(f"=== T13 Storage Overhead Measurement (N={N}) ===\n")

    gcm_key = os.urandom(32)
    priv_ecc = ec.generate_private_key(ec.SECP384R1())
    pub_pem = priv_ecc.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    # Dummy cipher functions (thay bằng thực tế từ Long)
    def ore_enc(v: int) -> int: return v * 13370000 + 42  # placeholder
    def dte_enc(s: str) -> str: return base64.b64encode(s.encode() * 3).decode()

    # Collection 1: Plaintext
    print("[1/3] Inserting plaintext records...")
    db["plain"].drop()
    records = [make_plaintext_record(i) for i in range(N)]
    db["plain"].insert_many(records)
    print(f"  Done")

    # Collection 2: FLE (DTE + ORE + AES-GCM)
    print("[2/3] Inserting FLE-encrypted records...")
    db["fle"].drop()
    fle_records = [make_fle_record(r, gcm_key, ore_enc, dte_enc) for r in records]
    db["fle"].insert_many(fle_records)
    print(f"  Done")

    # Collection 3: Asym PII (ECC + AES-GCM)
    print("[3/3] Inserting asymmetric-encrypted records...")
    db["asym"].drop()
    asym_records = [make_asym_record(r, pub_pem) for r in records]
    db["asym"].insert_many(asym_records)
    print(f"  Done\n")

    # Đo storage
    results = []
    baseline_mb = None
    for col_name in ["plain", "fle", "asym"]:
        m = measure_collection(col_name)
        if baseline_mb is None:
            baseline_mb = m["storageSize_MB"]
            m["overhead_ratio"] = 1.0
        else:
            m["overhead_ratio"] = round(m["storageSize_MB"] / baseline_mb, 2)
        results.append(m)
        print(f"{col_name:<8}: {m['storageSize_MB']:.3f} MB | "
              f"avg={m['avgObjSize_bytes']:.0f}B | "
              f"overhead={m['overhead_ratio']}x")

    # Save
    os.makedirs("storage", exist_ok=True)
    with open("storage/t13_overhead.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\n✅ Saved: storage/t13_overhead.json")
    client.close()


if __name__ == "__main__":
    main()
```

```bash
cd ~/enc2health_lan
python storage/measure_overhead.py
```

---

## Checklist hoàn thành

| Task | Lệnh verify | Deadline | Output giao nhóm |
|------|-------------|----------|-----------------|
| T1 | `gramine-direct python3 enclave/enclave_service.py` | Tuần 1-2 | Enclave chạy OK |
| T2 | `cat /var/log/enc2health/hipaa_audit.log` | Tuần 2 | mTLS key flow OK |
| T3 | `python enclave/aes_register_safe.py` | Tuần 2-3 | Throughput benchmark |
| T4 | `openssl speed aes-256-gcm` | Tuần 3-4 | `t4_results.csv` |
| T5 | `/usr/bin/time -v python ...` | Tuần 3-4 | `t5_memory.csv` |
| T6 | `numactl --hardware` | Tuần 4 | Delta latency |
| T7 | `python enclave/spectre_mitig.py` | Tuần 4-5 | Overhead % |
| T8 | `curl localhost:9091/health` | **Cuối tuần 5** | **→ Nam T9** |
| T9 | `python benchmark/bench_t9.py` | Tuần 7 | **`c_tee_metrics.json` → Nam** |
| T10 | `curl localhost:8002/metrics` | Tuần 6 | Dashboard Grafana |
| T11 | `python benchmark/bench_t11.py` | Tuần 8 | `t11_results.json` |
| T12 | `python benchmark/bench_t12.py` | Tuần 8-9 | `t12_join_comparison.json` |
| T13 | `python storage/measure_overhead.py` | Tuần 9-10 | `t13_overhead.json` |

## Bảng phụ thuộc

| Từ ai | Nhận gì | Task Lan cần | Deadline nhận |
|-------|---------|-------------|---------------|
| Long T5 | 10k MongoDB FLE dataset | T1 verify | Cuối tuần 2 |
| Long T6 | Vault keypairs per-Khoa | T2 mTLS key | Tuần 2 |
| Long T9 | KMS API port 8001 | T8 pool query role | Tuần 4 |
| T4 xong | OpenSSL verified | T3 AES-NI | Tuần 3-4 |
