# CLAUDE.md — Hướng dẫn cho Claude Code

> ⚠️ **Quy ước bắt buộc:** Mỗi khi trạng thái dự án thay đổi (hoàn thành/đổi một mục), **cập nhật SONG SONG cả [PROJECT.md](PROJECT.md) và CLAUDE.md**. PROJECT.md là tài liệu trạng thái chi tiết (DONE/IN DEV); CLAUDE.md là bản tóm tắt định hướng cho agent. Đừng để hai file lệch nhau.

---

## Dự án là gì

**Enc²Health** — phân tích dữ liệu y tế nhạy cảm (PHI) **tự thích nghi** trên Cloud-Native DBMS, kiến trúc **D Hybrid Adaptive**: truy vấn chạy ở **Software mode** (DTE/ORE trên ciphertext MongoDB) hoặc **TEE mode** (SGX Enclave khi có phần cứng; Gramine simulation khi không có), tự **fallback** TEE→Software khi EPC bão hòa (>80%). Mô hình đe dọa: cloud admin honest-but-curious quan sát được RAM/OS. Attestation hiện là signed simulation (HMAC + freshness), chưa phải SGX quote/DCAP thật.

Môn **NT219.Q2.ANTT**. 3 thành viên: Long (mã hóa & KMS), Lan (TEE/SGX & observability), Nam (Query Router & tích hợp).

## Kiến trúc & services

| Service | Cổng | Chủ | File chính |
|---|---|---|---|
| Query Router (FastAPI) | 8000 | Nam | `router/main.py` |
| ECALL Task Pool (TEE sim) | 9091 | Lan | `enclave/ecall_pool.py` *(không trong git)* |
| HashiCorp Vault | 8200 | Long | `crypto/vault/` |
| KMS API | 8001 | Long | `crypto/kms_api/main.py` |
| Prometheus / Grafana / exporter | 9090 / 3000 / 8002 | Lan | `enclave/monitoring/` *(không trong git)* |
| MongoDB | 27017 | Long | data: `crypto/data/generate_ehr.py` |

Luồng: `Client+JWT → Router (RBAC/ABAC → route → cost → adaptive) → {TEE: Pool/DuckDB/AES-GCM aggregate hoặc ECC PII decrypt | SOFTWARE: MongoDB DTE/ORE} → Vault (DEK + keypair theo khoa)`.

## Bố cục repo (lưu ý cái gì KHÔNG trong git)

```
router/      # Nam — main, query_router, cost_model, rbac, auth, probing,
             #       adaptive, ecall_client, software_executor, resource_monitor
common/      # auth.py — JWT HS256 dùng chung
crypto/      # Long — crypto/{dte,ore,gcm,asym}.py, vault/, kms_api/, data/generate_ehr.py
tests/       # benchmark, benchmark_concurrent, leakage, attack_bipartite, test_e2e, test_router, test_adaptive
scripts/     # smoke_test_local.sh, generate_mtls_certs.sh, generate_jwt.py
docs/        # SMOKE_RUN.md, LIMITATIONS.md (threat model/giới hạn), EVALUATION.md (kết quả)
enclave/     # ❌ Lan — ĐÃ gitignore (quá nặng), chia sẻ qua Google Drive. Tồn tại local nhưng KHÔNG push.
```
Lớn & gitignore: `enclave/`, `*.archive`, `*.zip` (dump/bundle sinh lại được).

## Lệnh hay dùng

```bash
# Demo E2E có lời dẫn (cần MongoDB + data; KHÔNG cần pool) — artifact giải thích/bảo vệ
python3 scripts/demo_e2e.py
# Demo cơ chế tự thích nghi: đợt dịch → fallback → phục hồi (KHÔNG cần Mongo/pool)
python3 scripts/demo_adaptive.py
# Demo ABAC: bác sĩ chỉ xem khoa mình, chống lách (cần MongoDB + data)
python3 scripts/demo_abac.py

# Smoke E2E mTLS local (1 lệnh — khuyến nghị)
make smoke-local

# Chạy router thủ công
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 uvicorn router.main:app --port 8000

# Tests
python3 -m pytest tests/test_router.py tests/test_e2e.py -v   # test_e2e tự skip nếu stack chưa live
python3 tests/benchmark.py && python3 tests/benchmark_concurrent.py
python3 tests/leakage.py && python3 tests/attack_bipartite.py && python3 tests/attack_ore.py

# Sinh dữ liệu EHR mã hóa vào MongoDB (cần MongoDB chạy)
python3 crypto/data/generate_ehr.py
```

## Quy ước & cạm bẫy cần nhớ

- **JWT bắt buộc:** mọi `/query` (Router lẫn Pool) cần `Authorization: Bearer <JWT>` (HS256, env `AUTH_JWT_SECRET`). Role lấy từ claim, KHÔNG tin `role` trong body.
- **mTLS qua env:** `T8_SSL_CA`, `ROUTER_CLIENT_CERT/KEY`, `ECALL_POOL_URL=https://...`. Không set thì chạy HTTP trần.
- **TEE ciphertext push:** `ROUTER_TEE_PUSH_CIPHERTEXT=1` để Router gom `vien_phi_enc` từ Mongo và đẩy vào Pool (mặc định tắt). `C_SOFT_METRICS_PATH` override file số liệu C_soft.
- **Cột ciphertext MongoDB:** `ma_benh_enc` (DTE, equality), `tuoi_enc` (ORE, range `$gte/$lte`), `vien_phi_enc` (AES-GCM, giải mã để aggregate).
- **Mã bệnh ICD-10:** luồng chính dùng ICD-10 (`E11`, `I10`, ...). Alias legacy `DTE00x` chỉ bật khi set `ALLOW_LEGACY_DTE_CODES=1`.
- **Duplicate enclave test file:** chỉ giữ một bản `test_ecall_pool.py`; bản copy handoff đã xoá để tránh lỗi collect.
- **RBAC/ABAC** (`router/rbac.py` + `router/abac.py`): admin=full · doctor=không `sum_vien_phi` (403) · admin_staff=xem viện phí, che `ma_benh` · researcher=`[MASKED]` vien_phi/ma_benh. **ABAC dept-scoping:** JWT có claim `dept` → bác sĩ chỉ xem khoa mình (Router tiêm `khoa_phong` vào filter, client không nới rộng). `ABAC_REQUIRE_DEPT=1` để strict.
- **Stack Python:** FastAPI + pymongo + cryptography + pyope + hvac + PyJWT (xem `requirements.txt`).
- **Shell:** môi trường Windows + WSL (kali); dùng Bash tool cho script POSIX.

## Trạng thái hiện tại (tóm tắt — chi tiết ở PROJECT.md)

- ✅ Router: routing (mở rộng operator: count_distinct→TEE, group_by/join/equality→SOFTWARE), **RBAC+ABAC (dept-scoping, 4 role)**, JWT, **cost model dùng số record thật + C_soft số liệu thật của Long + `compare_costs`**, probing+resource_monitor (RSS/EPC thật), **adaptive fallback có hysteresis (80%/60%) + núm mô phỏng áp lực (`/adaptive/simulate`) + status endpoint áp dụng hysteresis**, software_executor query MongoDB ciphertext thật, **Router-side đẩy ciphertext sang TEE** (`fetch_vien_phi_ciphertexts` + `ecall.query(ciphertexts=...)`, bật bằng `ROUTER_TEE_PUSH_CIPHERTEXT=1`), smoke mTLS pass.
- ✅ Crypto/KMS: DTE/ORE/GCM/ECC, Vault, KMS API, generate_ehr.
- ✅ Enclave (local, qua Drive): Gramine manifest ký SGX, DuckDB, AES-NI bench, Prometheus/Grafana.
- ✅ **Mắt xích đã hoàn thành (phía Lan):** Pool đã nhận `ciphertexts`, giải mã AES-GCM trong enclave và thực thi aggregate trên DuckDB in-memory (không còn phụ thuộc `MOCK_PATIENT_DATA` cho luồng này). Attestation hiện là signed simulation; Vault runtime đã ưu tiên AppRole + Vault Transit unwrap DEK, còn local key fallback chỉ là cờ dev. (Xem PROJECT.md §4, §5.5.)
- ✅ **Luồng PII thật đã nối:** `get_patient`/`lookup_patient` luôn route TEE; Router lấy `pii_enc` + `dept` từ Mongo, Pool lấy private key theo khoa từ Vault (local fallback chỉ khi bật cờ dev), giải mã ECC P-384 trong Pool, Router áp RBAC/ABAC mask (`doctor/admin` full, `admin_staff` che CMND/địa chỉ, `researcher` masked). Generator tạo `pii_enc` gom JSON PII và tự sửa keypair khoa bị lệch public/private khi reseed.
- 🎉 **E2E 3 bên VERIFY chạy thật (2026-05-31):** `tests/test_e2e.py` **10/10 PASSED** trên stack live (Mongo + Vault + Pool + Router, `ROUTER_TEE_PUSH_CIPHERTEXT=1`, cùng `AUTH_JWT_SECRET`). Bao gồm 7 test aggregate cũ + 3 test PII mới: doctor đúng khoa thấy PII, researcher masked, doctor sai khoa 403. Pool log xác nhận `DEK source: vault` và `Loaded private key from Vault` cho `/query/pii`. Cách chạy: bật Mongo/Vault → `EHR_FORCE_RECREATE=1 generate_ehr.py` → `bash crypto/vault/setup_vault.sh` → Pool(`T8_ALLOW_LOCAL_KEY_FALLBACK=0`) → Router(`ROUTER_TEE_PUSH_CIPHERTEXT=1`) → `AUTH_JWT_SECRET=... pytest tests/test_e2e.py -v`.

> Khi sửa code làm thay đổi các mục trên: nhớ cập nhật **cả PROJECT.md và CLAUDE.md**.

## Recent runs (2026-05-31)

- `tests/test_router.py`: 33/33 passed after PII route/RBAC/fetch tests ✅
- `tests/test_e2e.py`: 10/10 passed against live Mongo + Vault + Pool + Router stack ✅
- `tests/test_e2e.py` (Vault verify run): 7/7 PASSED with `T8_ALLOW_LOCAL_KEY_FALLBACK=0`; Pool log shows `DEK source: vault` ✅
- `tests/test_e2e.py` (full stack: MongoDB thật + Vault): 7/7 PASSED, `T8_ALLOW_LOCAL_KEY_FALLBACK=0` ✅
- `tests/leakage.py`: executed, `leakage_results.json` generated ✅
- `tests/attack_bipartite.py`: upgraded to reproducible rank-linkage evaluation (`attack_results.json` + optional `attack_chart.png`) ✅
- `make smoke-local`: local end-to-end mTLS smoke pass ✅
- `scripts/demo_e2e.py`: AVG (E11, >60) = 8,541,261 VND ✅
- `scripts/demo_abac.py`: ABAC dept-scoping verified ✅
- `scripts/demo_adaptive.py`: Adaptive fallback hysteresis behavior verified (80%/60%) ✅
- `tests/test_adaptive.py`: live adaptive endpoint verified via `/adaptive/simulate` fallback/restore ✅
- `tests/test_e2e.py` (PII decrypt path): 3/3 PII cases PASSED live — doctor sees PII, researcher masked, wrong-dept 403; Pool loaded private keys from Vault ✅

Note: these runs were executed locally (non-Docker) with MongoDB seeded from `crypto/data/generate_ehr.py`.
