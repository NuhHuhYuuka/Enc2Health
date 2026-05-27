# Enc2Health — Hybrid Adaptive Encrypted Query

**NT219.Q2.ANTT · Kiến trúc D**

## Cài đặt nhanh

```bash
git clone https://github.com/NuhHuhYuuka/Enc2Health.git
cd Enc2Health
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn httpx pydantic pytest scipy numpy matplotlib pytest-asyncio
```

## Chạy hệ thống

```bash
# Terminal 1 — ECALL pool (Lan)
cd enclave && python3 ecall_pool.py

# Terminal 2 — Router (Nam)
uvicorn router.main:app --host 0.0.0.0 --port 8000

# Swagger UI
http://localhost:8000/docs
```

## Chạy tests

```bash
python3 -m pytest tests/ -v
python3 tests/benchmark.py
python3 tests/attack_bipartite.py
python3 tests/plot_results.py
```

## Thành viên

| Tên | MSSV | Phụ trách |
|-----|------|-----------|
| Nguyễn Hoàng Long | 24521005 | Tầng mã hóa & KMS |
| Lâm Tú Lan | 24520943 | TEE/SGX Enclave & Observability |
| Nguyễn Lê Thành Nam | 24521113 | Query Router, Adaptive Logic & Tích hợp |
