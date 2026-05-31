# 🔐 Enc²Health — Bản đồ dự án (PROJECT.md)

> **Đề tài:** Phân tích dữ liệu y tế nhạy cảm (PHI) tự thích nghi trên Cloud-Native DBMS
> **Kiến trúc:** D — Hybrid Adaptive (Software DTE/ORE ⇄ TEE SGX Enclave, fallback tự động khi EPC bão hòa)
> **Môn:** NT219.Q2.ANTT
> **Mô hình đe dọa:** honest-but-curious cloud admin / CSP quan sát được RAM & OS-level
> **Cập nhật:** 2026-05-31

Tài liệu này liệt kê **toàn bộ** nội dung đồ án, chia rõ **✅ ĐÃ DONE** và **🚧 IN DEV**, kèm phần đánh giá *cái gì còn thiếu* và *ý tưởng làm tiếp*.

---

## 0. TL;DR — Trạng thái thực tế

| Trụ cột | Người | Chạy độc lập | Tích hợp runtime |
|---|---|---|---|
| Tầng mã hóa & KMS (DTE/ORE/GCM/ECC, Vault) | Long | ✅ Done | 🚧 Chưa nối vào luồng query thật |
| Query Router + Adaptive Logic | Nam | ✅ Done | ✅ SOFTWARE query thật + cost model số liệu thật + operator mở rộng + Router-side đẩy ciphertext sang TEE (chờ Pool nhận) |
| TEE Enclave + Observability (Gramine, DuckDB, Prometheus) | Lan | ✅ Done | ✅ Enclave giải mã AES-GCM từ ciphertext (Pool) + DuckDB in-memory; RA-TLS/attestation còn pending |

> **Khoảng trống lớn nhất (hiện tại):** Router đã query thật ở SOFTWARE mode và luồng mTLS local đã chạy pass. Phần TEE đã được nối dây cơ bản: Router có thể đẩy `vien_phi_enc` → Pool nhận ciphertexts, Pool giải mã AES-GCM và chạy aggregate trên DuckDB in-memory. Việc còn lại là chuẩn hoá RA-TLS/attestation và production Vault integration.

---

## 1. Kiến trúc tổng thể

```
Client (doctor / admin / researcher) + JWT
        ↓  POST /query
┌──────────────────────────────────────────────────────────────┐
│  Query Router (FastAPI :8000) — Nam                            │
│   1. Auth: JWT Bearer (HS256) → role                           │
│   2. RBAC/ABAC: check quyền + mask field nhạy cảm              │
│   3. QueryRouter: phân loại operator → SOFTWARE | TEE          │
│   4. Cost Model: ước lượng C_soft vs C_TEE (RSS profile)       │
│   5. Adaptive Controller: nếu EPC > 80% → fallback TEE→SOFT    │
└──────────────────────────────────────────────────────────────┘
     │ TEE mode                          │ SOFTWARE mode / fallback
     ▼                                   ▼
┌─────────────────────────┐     ┌──────────────────────────────┐
│ ECALL Task Pool (:9091) │     │ MongoDB (DTE/ORE ciphertext)  │
│   — Lan (Gramine sim)   │     │   — Long (FLE columns)        │
│   DuckDB AVG/SUM        │     │   equality / range trên cipher│
│   AES-GCM decrypt       │     └──────────────────────────────┘
└─────────────────────────┘
     │ RA-TLS (stub) + DEK
     ▼
┌─────────────────────────┐
│ HashiCorp Vault (:8200) │  Envelope Encryption, keypair/khoa, DEK
│   — Long                │
└─────────────────────────┘

Observability: Prometheus (:9090) + Grafana dashboard + exporter (:8002) — Lan
```

---

## 2. Ánh xạ kịch bản → hiện thực

| Yêu cầu trong kịch bản | Hiện thực trong code | Trạng thái |
|---|---|---|
| PII mã hóa bất đối xứng RSA-4096 / ECC P-384, client-side | `crypto/crypto/asym.py` (ECIES P-384 + RSA-4096 OAEP) | ✅ |
| Private key chỉ giải mã trong Enclave | `asym.ecc_decrypt` / `rsa_decrypt` (gọi từ enclave context) | ⚠️ có hàm, chưa nối luồng |
| Lâm sàng (mã bệnh, khoa) — DTE AES-SIV, search trên ciphertext | `crypto/crypto/dte.py` (AES-SIV-256, per-field key) | ✅ |
| Range (tuổi, ngày) — ORE/OPE | `crypto/crypto/ore.py` (Boldyreva OPE qua `pyope`) | ✅ |
| Chỉ số XN & viện phí — AES-GCM-256, giải mã trong SGX | `crypto/crypto/gcm.py` + `enclave/enclave_service.py` | ✅ |
| KMS Envelope Encryption (DEK bọc bởi MK) | `crypto/vault/*` + `setup_vault.sh` + `key_rotation.py` | ✅ (Vault transit rotation có) |
| RBAC/ABAC qua JWT, chính sách theo thuộc tính y tế | `router/rbac.py` + `router/abac.py` + `common/auth.py` | ✅ RBAC role + ABAC dept-scoping (bác sĩ chỉ xem khoa mình) |
| Software Mode trên MongoDB FLE (= / range) | `generate_ehr.py` tạo data; Router dùng `software_executor.py` query thật | ✅ |
| TEE Mode chạy DuckDB trong SGX (SUM/AVG) | Router gom ciphertext → đẩy Pool; `enclave/ecall_pool.py` (mock) + `enclave_service.py` (DuckDB real) | ⚠️ Router-side xong; Pool cần nhận `ciphertexts` (Lan) |
| Self-adaptive: probe EPC, fallback khi >80% | `router/probing.py` + `router/adaptive.py` + `router/resource_monitor.py` | ✅ logic + đọc RSS/EPC thật từ `/proc` |
| Router lấy DEK từ Vault sau RA-TLS | `ecall_client.py` + `vault_client.py` | ⚠️ Attestation đang ở chế độ signed simulation (HMAC + freshness), chưa phải SGX quote/DCAP thật |
| Đánh giá hiệu năng overhead < 2x | `tests/benchmark.py` (TEE ~2x Software) | ✅ |
| q-leakage đo rò rỉ khi fallback | `tests/leakage.py` + `tests/attack_bipartite.py` | ✅ tách được TEE/RBAC masked vs software fallback raw |
| Grafana real-time | `enclave/monitoring/` (Prometheus + Grafana + exporter) | ✅ |
| Tăng tốc giải mã PHI bằng AES-NI / OpenSSL | `enclave/T4/` (OpenSSL microbench AES-NI) | ✅ benchmark |

---

## 3. ✅ ĐÃ DONE (chi tiết theo trụ cột)

### 3.1. Tầng mã hóa & KMS — Long (`crypto/`)
- ✅ **DTE (AES-SIV-256)** — `crypto/crypto/dte.py`: mã hóa tất định, per-field key, associated-data isolation, self-test pass.
- ✅ **ORE/OPE (Boldyreva)** — `crypto/crypto/ore.py`: range query trên tuổi (0–150) & ngày, bảo toàn thứ tự, self-test pass.
- ✅ **AES-GCM-256** — `crypto/crypto/gcm.py`: mã hóa viện phí / kết quả XN, `decrypt_float`/`decrypt_json`.
- ✅ **Asymmetric** — `crypto/crypto/asym.py`: ECIES P-384 (ECDH+HKDF+AES-GCM) và RSA-4096 OAEP; sinh keypair cho 6 khoa.
- ✅ **Sinh dữ liệu EHR** — `crypto/data/generate_ehr.py`: 10.000 hồ sơ giả (Faker vi_VN) → MongoDB với cột mã hóa đầy đủ (PII/DTE/ORE/GCM) + index; có demo equality & range query trên ciphertext.
- ✅ **HashiCorp Vault** — `crypto/vault/`: `vault_client.py` (KV-v2, AppRole + token), `setup_vault.sh` (upload keypair/khoa + DEK + policy), `key_rotation.py`, `audit_logger.py`.
- ✅ **KMS API** — `crypto/kms_api/main.py`: FastAPI :8001, endpoint `/dek/{name}`, `/pubkey/{dept}`, `/departments`, Prometheus metrics.
- ✅ **Cost model số liệu C_soft** — `crypto/benchmark/c_soft_metrics.json` + `measure.py`.
- ✅ **FHIR schema** — `crypto/schema/fhir_schema.py`.

### 3.2. Query Router & Adaptive — Nam (`router/`, `common/`)
- ✅ **QueryRouter (T1)** — `router/query_router.py`: phân loại `sum/avg/count_distinct → TEE`; `count/=/join/group_by/range → SOFTWARE` (đủ operator theo kịch bản).
- ✅ **Cost Model (T2)** — `router/cost_model.py`: TEE nội suy từ RSS profile thật của Lan; **C_soft đọc số liệu thật từ `crypto/benchmark/c_soft_metrics.json`** (Long); `compare_costs(n)` so sánh 2 mode + `cheaper_mode`. `main.py` dùng **n_records thật** (bỏ hard-code 1000).
- ✅ **RBAC/ABAC (T4)** — `router/rbac.py` + `router/abac.py`: 4 role (admin/doctor/admin_staff/researcher), column-level masking (`vien_phi`, `ma_benh`), **ABAC dept-scoping** — bác sĩ chỉ xem bệnh nhân khoa mình (thuộc tính `dept` trong JWT → Router tiêm filter `khoa_phong_enc` DTE, client không nới rộng được). Demo: `scripts/demo_abac.py`.
- ✅ **Auth JWT** — `common/auth.py` + `router/auth.py`: HS256 Bearer, iss/aud, `generate_test_jwt`; fallback `INTERNAL_AUTH_TOKEN`.
- ✅ **Probing (T5)** — `router/probing.py`: thread probe mỗi 5s, lock baseline sau 3 lần, phát hiện latency ≥ 2× baseline.
- ✅ **Adaptive Controller (T6)** — `router/adaptive.py`: state machine NORMAL ⇄ FALLBACK với **hysteresis** (fallback ≥80%, restore ≤60% chống flapping), núm mô phỏng áp lực (`set_simulated_pressure` + `/adaptive/simulate`), switch log có pressure/nguồn, endpoint `/adaptive`.
- ✅ **ECALL client (T9)** — `router/ecall_client.py`: HTTP client tới pool, JWT Bearer tự sinh, hỗ trợ mTLS cert + `/attest` verification (MRENCLAVE + chữ ký HMAC + freshness), fallback `httpx`→`requests` khi mTLS lỗi; **`query(..., ciphertexts=...)` đẩy kèm bản mã vào enclave** (TEE path).
- ✅ **Software Executor** — `router/software_executor.py`: SOFTWARE/fallback path query MongoDB ciphertext thật — mã hóa điều kiện bằng DTE (`ma_benh`) + ORE (`tuoi` range), giải mã viện phí AES-GCM để SUM/AVG/COUNT. **Strict mode:** `SOFTWARE_STRICT_MODE=1` sẽ fail-fast, không rơi về dữ liệu giả. `fetch_vien_phi_ciphertexts(filters)` gom bản mã cho Router đẩy sang enclave.
- ✅ **Resource Monitor** — `router/resource_monitor.py`: đọc RSS thật từ `/proc/<pid>/status`, dò EPC từ `/proc/<pid>/smaps` (best-effort), ghi snapshot JSON; thay cho công thức nội suy RSS cũ.
- ✅ **Main service** — `router/main.py`: FastAPI :8000, `/query` `/health` `/adaptive` `/metrics`, tích hợp đủ T1/T2/T4/T6/T9; SOFTWARE mode gọi `software_executor` (đã bỏ placeholder), JWT Bearer bắt buộc.
- ✅ **Smoke run mTLS local** — `Makefile` (`make smoke-local`), `scripts/smoke_test_local.sh`, `scripts/generate_jwt.py`, `docs/SMOKE_RUN.md`: chạy 1 lệnh dựng cả Pool+Router qua TLS, smoke pass.
- ✅ **Demo E2E có lời dẫn** — `scripts/demo_e2e.py`: chạy truy vấn "AVG viện phí E11 trên 60 tuổi" trên 10k record mã hóa thật, in từng bước (CSP thấy ciphertext → enclave giải mã → bác sĩ nhận 1 số → researcher bị mask). Dùng làm artifact giải thích + bảo vệ đồ án.
- ✅ **Demo Adaptive có lời dẫn** — `scripts/demo_adaptive.py`: mô phỏng đợt dịch → EPC bão hòa → router tự fallback AVG xuống Software → phục hồi (có hysteresis + switch log). Không cần MongoDB/Pool.
- ✅ **Demo ABAC có lời dẫn** — `scripts/demo_abac.py`: chứng minh dept-scoping trên data thật (bác sĩ Tim_mach chỉ thấy 1700 BN khoa mình vs admin 10000; chống lách filter; admin_staff che chẩn đoán; researcher mask).

### 3.3. TEE Enclave & Observability — Lan (`enclave/`)
> ⚠️ Folder `enclave/` đã được gỡ khỏi git (quá nặng) và `.gitignore`; Lan chia sẻ qua Google Drive. Các đường dẫn dưới đây trỏ tới cây thư mục local của Lan, không có trên GitHub.
- ✅ **ECALL Task Pool (T8)** — `enclave/ecall_pool.py`: FastAPI :9091, ThreadPool 8 workers, index theo ma_benh/tuoi/khoa, JWT bắt buộc, `/attest` signed simulation + strict mode (`T8_STRICT_MODE`).
- ✅ **Enclave service (DuckDB)** — `enclave/enclave_service.py`: DuckDB in-memory + AES-GCM decrypt (register-safe, zero-fill).
- ✅ **Gramine SGX manifest** — `enclave/T1_gramine/duckdb_real.manifest(.sgx)`: trusted_files có SHA-256, enclave 128M, binary DuckDB ký thật.
- ✅ **T3 — AES-GCM C benchmark** — `enclave/T3/T3_aes_gcm_benchmark.c` (register vs RAM mode).
- ✅ **T4 — OpenSSL AES-NI microbench** — `enclave/T4/` + `T4_results/` (rsa4096, ecdsap384, aes256gcm; host vs enclave).
- ✅ **Spectre/side-channel mitigation** — `enclave/spectre_mitig.py`, `enclave/aes_register_safe.py`.
- ✅ **mTLS / RA-TLS scaffolding** — `enclave/certs/` (CA, server, client, vault, enclave certs), `scripts/generate_mtls_certs.sh`.
- ✅ **Observability (T10)** — `enclave/monitoring/`: Prometheus config, exporter (epc_pressure, asym_latency, aes_throughput, key_retrieve), Grafana dashboard JSON + provisioning, docker-compose.
- ✅ **Vault key sender/receiver** — `enclave/vault_key_sender.py`, `enclave/key_receiver.py`, `enclave/T2_mock_vault.py`.
- ✅ **HIPAA audit log** — `enclave/hipaa_audit.log`.

### 3.4. Tests & Benchmark (chung)
- ✅ **Unit tests (T3)** — `tests/test_router.py` (Router/RBAC/Cost Model).
- ✅ **E2E tests (T10)** — `tests/test_e2e.py` (7 ca: full flow, mask, 403, software mode, filter) — verify đối chiếu MongoDB thật (DTE+ORE+GCM), tự `skip` khi stack chưa live.
- ✅ **EPC saturation (T7)** — `tests/test_adaptive.py` (20 thread đồng thời).
- ✅ **Benchmark 3 mode (T11)** — `tests/benchmark.py` → `benchmark_results.json` (TEE ~2× Software).
- ✅ **Concurrent clients (T12)** — `tests/benchmark_concurrent.py` (1→5→10→20→50, 0% error) → `concurrent_results.json`.
- ✅ **q-leakage entropy (T13)** — `tests/leakage.py` → `leakage_results.json`.
- ✅ **Bipartite Matching Attack (T13)** — `tests/attack_bipartite.py`: rank-linkage evaluation có metric phục hồi (`exact_recovery_rate`, `within_2_years_rate`, `mae`) → `attack_results.json` + `attack_chart.png` (nếu có matplotlib).
- ✅ **Plot tổng hợp (T14)** — `tests/plot_results.py` → `enc2health_benchmark.png`.

---

## 4. 🚧 IN DEV / Còn thiếu

### 4.1. 🔴 Tích hợp E2E thật (ưu tiên cao nhất)
- ✅ **Router SOFTWARE/Fallback đã query MongoDB thật.** Đã tích hợp `software_executor.py` vào `router/main.py`; không còn placeholder `{"result": 0.0, ...}`.
- ✅ **Enclave đã nhận ciphertext và thực thi decrypt trong Pool (Lan).** Router có thể gom `vien_phi_enc` và đẩy batch ciphertexts; `ecall_pool.py` giờ giải mã AES-GCM bằng `enclave_service.decrypt_aes_gcm` và ingest vào DuckDB in-memory.
- ✅ **DuckDB đã được tích hợp vào Pool.** `ecall_pool` thực thi `SUM`/`AVG` trên bảng tạm chứa giá trị đã giải mã.
- ⚠️ **Vault runtime:** Pool cố gắng lấy DEK từ Vault nếu có (fallback về local key files khi không có Vault token). Việc chuẩn hoá Vault production integration và RA-TLS/attestation vẫn chưa hoàn tất.

### 4.2. 🟠 Bảo mật / Attestation
- 🚧 **RA-TLS là stub.** `/attest` trả `mock-sgx-quote`, `mock-mrenclave`. → Cần quote thật (gramine-sgx + DCAP) hoặc ít nhất giải thích rõ "simulation" trong báo cáo.
- ⚠️ **mTLS đã chạy pass ở local/compose smoke, nhưng chưa là mặc định cho mọi mode chạy tay.** Cần chuẩn hóa để TLS bật mặc định giữa Router ↔ Pool ↔ Vault trong profile production.
- ✅ **q-leakage đã phân biệt TEE vs Fallback.** `tests/leakage.py` giờ ghi riêng `TEE_mode_researcher_masked` và `SOFTWARE_fallback_researcher_raw`, nên `leakage_results.json` đã cho thấy output exposure tăng rõ khi fallback.

### 4.3. 🟡 Tính nhất quán & dữ liệu
- ✅ **Mã bệnh ICD-10 đã đồng bộ mặc định.** Alias legacy `DTE00x` chỉ bật khi set `ALLOW_LEGACY_DTE_CODES=1`.
- ✅ **Probe RSS/EPC thật (best-effort).** `router/resource_monitor.py` đọc RSS thật từ `/proc/<pid>/status`, cố gắng nhận diện EPC từ `/proc/<pid>/smaps`, và `router/probing.py` đẩy snapshot ra `/metrics` / file JSON.
- 🚧 **DBMS chưa thống nhất.** Kịch bản + README + `generate_ehr.py` = **MongoDB**; ảnh topology ghi **Postgres/CockroachDB**. → Chốt MongoDB, sửa lại ảnh topology cho khớp.
- ✅ **Fallback đã dùng đúng cột ORE/DTE.** Khi Adaptive chuyển TEE→SOFTWARE, `main.py` gọi `software_executor` query cột DTE (`ma_benh_enc`) + ORE (`tuoi_enc`) trên MongoDB. Với `SOFTWARE_STRICT_MODE=1`, lỗi hạ tầng sẽ fail-fast thay vì rơi về mock.

### 4.4. 🟢 Phần chưa có / nâng cao (nice-to-have)
- 🚧 **HL7 FHIR ingest thật** — hiện chỉ có schema, chưa parse FHIR Document.
- 🚧 **Client-Side Encryption demo (HIS/EMR app)** — kịch bản nói mã hóa tại client; hiện `generate_ehr.py` mã hóa ở phía server-script.
- 🚧 **Kịch bản dịch bệnh E2E demo** — script mô phỏng "đợt dịch" → EPC bão hòa → fallback → phục hồi, quay video/screenshot cho báo cáo.
- 🚧 **MongoDB FLE đúng nghĩa** — hiện là "FLE-style" (tự mã hóa cột), chưa dùng driver-level Client-Side FLE của MongoDB.
- 🚧 **CI / script khởi động một lệnh** — hiện phải chạy tay 2–3 terminal; nên có `docker-compose` hoặc `make up` cho cả stack (Mongo + Vault + Pool + Router + Prometheus + Grafana).

---

## 5. 💡 Đề xuất việc làm tiếp (theo thứ tự ưu tiên)

### Sprint 1 — "Nối dây" E2E thật (xương sống của đồ án)
1. ✅ **`router/software_executor.py`** — đã nối MongoDB ciphertext path cho SOFTWARE mode.
2. ✅ **Nối Enclave với dữ liệu thật** — Router fetch các `vien_phi_enc` (ciphertext) → POST vào `ecall_pool` → pool giải mã AES-GCM bằng `enclave_service.decrypt_aes_gcm` → ingest vào DuckDB in-memory → `AVG/SUM` (implemented in `enclave/ecall_pool.py`).
3. ✅ **Load key vào pool lúc startup** — Pool cố gắng lấy DEK từ Vault nếu có, và fallback về local key files; `/health` phản ánh `keys_loaded`.
4. **Thống nhất ICD-10** trong cả mock lẫn data thật. ✅ `test_e2e.py` đã được viết lại: verify kết quả Router so với truy vấn MongoDB thật (DTE+ORE+GCM), tự `skip` khi stack chưa chạy — không còn hard-code `n_records == 5`.

→ *Định nghĩa "xong": chạy đúng truy vấn mẫu trong kịch bản — "AVG viện phí bệnh nhân E11 trên 60 tuổi" — từ client thật, qua Mongo (DTE+ORE) → Enclave (GCM+DuckDB) → ra 1 con số, CSP chỉ thấy ciphertext.*

### Sprint 2 — Đánh giá an ninh đúng trọng tâm
5. **Q-leakage đã được tách theo mode**: đo access-pattern leakage (DTE equality histogram, ORE order relations) và output exposure riêng cho TEE masked vs Software-fallback raw. Đây là phần đánh giá an ninh chính.
6. **Probe RSS/EPC thật** — đọc RSS qua psutil hoặc Gramine EPC counter; nối vào `epc_pressure` gauge của exporter.
7. **Kịch bản "đợt dịch" demo** — script bơm tải → quan sát fallback tự động trên Grafana → phục hồi; lưu screenshot/log cho báo cáo.

### Sprint 3 — Hoàn thiện & trình bày
8. **Bật mTLS mặc định** giữa các service; nâng `/attest` lên RA-TLS thật nếu có phần cứng SGX, hoặc ghi rõ giới hạn "Gramine simulation".
9. **`docker-compose` toàn stack** + `make up` một lệnh.
10. **Sửa ảnh topology** cho khớp MongoDB; cập nhật README + PROJECT.md.
11. **Báo cáo cuối**: gom benchmark (overhead <2×), concurrent (50 client 0% lỗi), attack (DTE 50% vs TEE 0%), leakage fallback.

---

## 5.5. 🧭 Router (Nam) — trạng thái & việc làm tiếp

**Đã xong (chạy thật):** routing operator, cost model, RBAC + mask, JWT, probing + resource_monitor (RSS/EPC thật), adaptive fallback, ecall_client (mTLS+JWT), **software_executor query MongoDB ciphertext thật**, smoke mTLS local pass.

**Đã hoàn thành đợt này (2026-05-30):**
1. ✅ **TEE path — Router → Pool end-to-end (basic):** `software_executor.fetch_vien_phi_ciphertexts(filters)` gom bản mã `vien_phi_enc` từ MongoDB; `main.py` (khi bật `ROUTER_TEE_PUSH_CIPHERTEXT=1`) gửi batch ciphertexts qua `ecall_client.query(..., ciphertexts=...)`. `ecall_pool` hiện nhận `ciphertexts`, giải mã AES-GCM và trả aggregate (DuckDB in-memory). RA-TLS/attestation vẫn pending.
2. ✅ **Cost model dùng số record THẬT + so sánh 2 mode.** Bỏ `n_records=1000` hard-code; `main.py` lấy `n_records` thật từ kết quả rồi gọi `compare_costs(n)` → trả `cost_estimate` gồm cả C_soft, C_TEE, `cheaper_mode`, `tee_over_software_ratio`. (Routing vẫn theo operator + saturation để giữ ràng buộc bảo mật; cost dùng cho observability/justify.)
3. ✅ **C_soft nối số liệu thật của Long.** `cost_model.py` đọc `crypto/benchmark/c_soft_metrics.json` (AES-SIV/OPE encrypt avg) thay cho `C_SOFT_BASE_MS=0.5`; fallback hằng số nếu thiếu file.
4. ✅ **Mở rộng routing operator.** `query_router.py` thêm `count_distinct→TEE`, `group_by/join/equality/range→SOFTWARE` đúng kịch bản (`=, JOIN, GROUP BY, COUNT → Software`; `SUM, AVG, COUNT DISTINCT → TEE`).

**Còn lại phía Router:**
5. ✅ **Cơ chế tự thích nghi — demo + kiểm thử được.** Thêm núm mô phỏng áp lực EPC (`set_simulated_pressure`, endpoint `POST /adaptive/simulate?pressure=`), **hysteresis** (fallback ≥80%, restore ≤60% → chống flapping), `scripts/demo_adaptive.py` (kịch bản đợt dịch→fallback→phục hồi, có switch log), và 4 unit test tất định cho state machine (không cần pool). Status `/adaptive` giờ trả `pressure_ratio`, `epc_threshold`, `restore_threshold`, `saturated`, `pressure_source`.
6. ⚠️ **Cost-driven routing (tùy chọn).** Hiện cost chỉ surface để quan sát; nếu muốn để cost *thực sự điều khiển* fallback (proactive) thì cần thống nhất với ràng buộc bảo mật — toán tử nhạy cảm (AVG/SUM) không được tự ý hạ xuống Software vì rò rỉ.

---

## 6. Cách chạy nhanh (hiện tại)

```bash
# One-command local smoke (khuyến nghị)
REGEN_CERTS=1 KEEP_ALIVE=0 make smoke-local

# Hoặc chạy tay 2 terminal (nếu cần)
# Terminal 1 — ECALL Task Pool (TLS)
T8_SSL_CERT=certs/server.crt T8_SSL_KEY=certs/server.key T8_SSL_CA=certs/ca.crt python3 enclave/ecall_pool.py

# Terminal 2 — Query Router
AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 \
ECALL_POOL_URL=https://127.0.0.1:9091 \
ROUTER_CLIENT_CERT=certs/client.crt ROUTER_CLIENT_KEY=certs/client.key T8_SSL_CA=certs/ca.crt \
uvicorn router.main:app --host 0.0.0.0 --port 8000

# (tùy chọn) Vault + KMS + MongoDB + Prometheus/Grafana — xem README §Chạy hệ thống

# Tests
python3 -m pytest tests/test_router.py tests/test_e2e.py -v
python3 tests/benchmark.py && python3 tests/benchmark_concurrent.py
python3 tests/leakage.py && python3 tests/attack_bipartite.py
```

---

## 7. Thành viên

| Tên | MSSV | Phụ trách |
|---|---|---|
| Nguyễn Hoàng Long | 24521005 | Tầng mã hóa & KMS — DTE/ORE/GCM/ECC, HashiCorp Vault, MongoDB |
| Lâm Tú Lan | 24520943 | TEE/SGX Enclave & Observability — Gramine, DuckDB, Prometheus/Grafana |
| Nguyễn Lê Thành Nam | 24521113 | Query Router, Adaptive Logic & Tích hợp E2E |

---

*PROJECT.md — bản đồ trạng thái đồ án Enc²Health. Cập nhật mỗi khi hoàn thành một mục IN DEV.*

## RECENT RUNS (2026-05-31)

- `tests/test_router.py`: 30/30 passed ✅
- `tests/test_e2e.py`: 7/7 passed against live stack ✅
- `tests/leakage.py`: executed, `leakage_results.json` generated ✅
- `tests/attack_bipartite.py`: rank-linkage evaluation generated `attack_results.json` ✅
- `scripts/demo_e2e.py`: AVG (E11, >60) = 8,541,261 VND ✅
- `scripts/demo_abac.py`: ABAC dept-scoping verified ✅
- `scripts/demo_adaptive.py`: Adaptive fallback hysteresis behavior verified (80%/60%) ✅

These runs were executed locally (non-Docker) with MongoDB seeded from `crypto/data/generate_ehr.py`.
