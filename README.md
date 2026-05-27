<div align="center">

  <h1>🔐 Enc2Health</h1>

  <p>
    <strong>Hybrid Adaptive Encrypted Query Processing cho Cloud-Native DBMS</strong><br/>
    Viết bằng Python / FastAPI · Gramine SGX2 Simulation · HashiCorp Vault
  </p>

  <p>
    <img src="https://img.shields.io/badge/Python-3.14-3776AB?style=flat-square&logo=python" alt="Python"/>
    <img src="https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat-square&logo=fastapi" alt="FastAPI"/>
    <img src="https://img.shields.io/badge/SGX-Gramine%20Simulation-blue?style=flat-square" alt="SGX"/>
    <img src="https://img.shields.io/badge/KMS-HashiCorp%20Vault-black?style=flat-square&logo=vault" alt="Vault"/>
    <img src="https://img.shields.io/badge/Tests-18%20passed-success?style=flat-square&logo=pytest" alt="Tests"/>
    <img src="https://img.shields.io/badge/NT219-Q2.ANTT-orange?style=flat-square" alt="NT219"/>
  </p>

  <img src="enc2health_benchmark.png" alt="Benchmark Results" width="80%"/>

</div>

---

## Tổng quan kiến trúc

Hệ thống truy vấn dữ liệu y tế mã hóa theo **Kiến trúc D — Hybrid Adaptive**:

```
Client (doctor / admin / researcher)
        ↓
   [RBAC/ABAC Middleware]      ← kiểm tra quyền, mask field nhạy cảm
        ↓
   [Query Router + Cost Model] ← phân loại toán tử SQL
        ↓
   ┌─────────────┬──────────────┐
   │             │              │
Software Mode  TEE Mode      Fallback
(DTE/ORE)    (SGX Enclave)  (EPC > 80%)
=, JOIN       SUM, AVG
GROUP BY      COUNT DISTINCT
```

| Operator | Mode | Lý do |
|---|---|---|
| `=`, `JOIN`, `GROUP BY` | Software (DTE/ORE) | Equality-preserving, nhanh |
| `SUM`, `AVG`, `COUNT DISTINCT` | TEE Enclave | Cần tính toán bảo mật |
| EPC > 80% | Fallback → Software | Tự adaptive switching |

---

## Kết quả benchmark

| Mode | avg latency | p99 | QPS |
|---|---|---|---|
| SOFTWARE (count) | 5.7ms | 35.9ms | 174 |
| TEE (sum_vien_phi) | 10.6ms | 17.2ms | 94 |
| TEE (avg_vien_phi) | 11.0ms | 13.4ms | 91 |
| Hybrid + RBAC masking | 11.8ms | 53.5ms | 84 |

> TEE overhead ~2x so với Software — chấp nhận được cho dữ liệu y tế nhạy cảm.

---

## Kết quả leakage & attack

<div align="center">
  <img src="attack_chart.png" alt="Attack Results" width="60%"/>
</div>

| Mode | Value Recovery Rate | Row Recovery Rate |
|---|---|---|
| DTE Software | **50%** ← dễ bị tấn công | **75%** |
| TEE Enclave | **0%** ← không thể tấn công | **0%** |
| Hybrid Adaptive | ~15% (chỉ equality leak) | ~20% |

---

## Yêu cầu hệ thống

| Thành phần | Yêu cầu |
|---|---|
| OS | Ubuntu 22.04 (VMware / native / WSL2) |
| Python | 3.10+ |
| RAM | 4 GB tối thiểu |
| Disk | 2 GB |

---

## Cài đặt nhanh

```bash
git clone https://github.com/NuhHuhYuuka/Enc2Health.git
cd Enc2Health
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn httpx pydantic pytest \
            scipy numpy matplotlib pytest-asyncio cryptography
```

---

## Chạy hệ thống

```bash
# Terminal 1 — ECALL Task Pool (Lan · TEE Simulation)
cd enclave && source ../.venv/bin/activate
python3 ecall_pool.py
# Service chạy tại http://localhost:9091

# Terminal 2 — Query Router (Nam)
cd .. && source .venv/bin/activate
uvicorn router.main:app --host 0.0.0.0 --port 8000 --reload
# Service chạy tại http://localhost:8000
```

Swagger UI: **http://localhost:8000/docs**

---

## Ví dụ query

```bash
# Admin query SUM → TEE mode
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query_type":"sum_vien_phi","filters":{},"role":"admin"}'

# Researcher query AVG → TEE + RBAC mask
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query_type":"avg_vien_phi","filters":{"ma_benh":"E11"},"role":"researcher"}'

# Doctor query COUNT → Software mode
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query_type":"count","filters":{},"role":"doctor"}'
```

---

## Chạy tests & benchmark

```bash
# Unit tests — 11/11 passed
python3 -m pytest tests/test_router.py -v

# E2E tests — 7/7 passed
python3 -m pytest tests/test_e2e.py -v

# Benchmark 3 chế độ (SOFTWARE / TEE / Hybrid)
python3 tests/benchmark.py

# Bipartite Matching Attack (DTE frequency leakage)
python3 tests/attack_bipartite.py

# Vẽ biểu đồ tổng hợp
python3 tests/plot_results.py
```

---

## Cấu trúc project

```
enc2health/
├── router/
│   ├── query_router.py      # T1  — phân loại SQL operator → Software/TEE
│   ├── cost_model.py        # T2  — Cost Model C_soft vs C_TEE
│   ├── rbac.py              # T4  — RBAC/ABAC middleware (doctor/admin/researcher)
│   ├── ecall_client.py      # T9  — HTTP client kết nối ECALL pool
│   └── main.py              # tích hợp FastAPI router service
├── enclave/
│   └── ecall_pool.py        # Lan T8 — ECALL Task Pool (Gramine simulation)
├── tests/
│   ├── test_router.py       # T3  — unit tests 11/11
│   ├── test_e2e.py          # T10 — E2E tests 7/7
│   ├── benchmark.py         # T11 — benchmark 3 chế độ
│   ├── leakage.py           # T13 — q-leakage entropy analysis
│   ├── attack_bipartite.py  # T13 — Bipartite Matching Attack
│   └── plot_results.py      # T14 — vẽ biểu đồ trade-off
├── benchmark_results.json   # kết quả benchmark thô
├── leakage_results.json     # kết quả leakage analysis
├── attack_results.json      # kết quả tấn công
├── enc2health_benchmark.png # biểu đồ latency/QPS/leakage
└── attack_chart.png         # biểu đồ attack recovery rate
```

---

## RBAC Policy

| Role | sum_vien_phi | avg_vien_phi | count | Xem vien_phi | Xem ma_benh |
|---|---|---|---|---|---|
| `admin` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `doctor` | ❌ | ✅ | ✅ | ✅ | ✅ |
| `researcher` | ❌ | ✅ | ✅ | ❌ masked | ❌ masked |

---

## Thứ tự khởi động

```
1. ECALL Task Pool  (port 9091)  ← Lan
2. Query Router     (port 8000)  ← Nam
3. Client / curl / Swagger UI
```

---

## Thành viên nhóm

| Tên | MSSV | Phụ trách |
|---|---|---|
| Nguyễn Hoàng Long | 24521005 | Tầng mã hóa & KMS — DTE/ORE, HashiCorp Vault |
| Lâm Tú Lan | 24520943 | TEE/SGX Enclave & Observability — Gramine, DuckDB, Prometheus |
| Nguyễn Lê Thành Nam | 24521113 | Query Router, Adaptive Logic & Tích hợp |

---

<div align="center">
  <p><strong>NT219.Q2.ANTT</strong> · Kiến trúc D — Hybrid Adaptive </p>
</div>
