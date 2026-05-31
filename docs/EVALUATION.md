# Enc²Health — Tổng hợp Kết quả Đánh giá

> Số liệu THẬT từ các lần chạy (local, MongoDB seed từ `generate_ehr.py`, 10k record).
> Dùng cho báo cáo/bảo vệ. Đi kèm `docs/LIMITATIONS.md` (ranh giới thật/mô phỏng).
> Cập nhật: 2026-05-31.

---

## 1. Tích hợp end-to-end (tính đúng đắn)

| Hạng mục | Kết quả |
|---|---|
| **E2E 3 bên** `tests/test_e2e.py` | **7/7 PASSED** trên stack live (Mongo + Pool + Router, `ROUTER_TEE_PUSH_CIPHERTEXT=1`) |
| Router unit `tests/test_router.py` | **33/33 PASSED** |
| Kết quả TEE của Router | **Khớp chính xác** giá trị giải mã trực tiếp từ Mongo (sum/avg) |

→ Chứng minh luồng: *Client → Router (gom `vien_phi_enc`) → Pool (giải mã AES-GCM trong enclave) → DuckDB (SUM/AVG) → 1 con số*, CSP chỉ thấy ciphertext.

### 1.2. Luồng PII cá nhân (ECC P-384)

Đã implement luồng thật cho `get_patient`/`lookup_patient`: Router lấy `pii_enc` + `dept` từ MongoDB, gọi Pool `/query/pii`, Pool lấy private key theo khoa từ Vault/local-dev-fallback có kiểm soát và giải mã ECC trong Pool/TEE, Router áp RBAC/ABAC mask trước khi trả client.

| Hạng mục | Kết quả |
|---|---|
| Unit/router coverage | `tests/test_router.py` **33/33 PASSED** sau khi thêm route PII, RBAC mask, fetch PII ciphertext |
| E2E PII cases | Đã thêm 3 case: doctor đúng khoa thấy PII, researcher masked, doctor sai khoa 403 |
| Latest local E2E run | `tests/test_e2e.py` **10 skipped** vì Router/Pool stack không live trong lần chạy này |

Ghi chú: để verify live PII path cần reseed dataset để có `pii_enc`: `EHR_FORCE_RECREATE=1 python3 crypto/data/generate_ehr.py`, upload lại keypair vào Vault bằng `crypto/vault/setup_vault.sh`, rồi chạy Router/Pool như các E2E hiện có.

### 1.1. Verify Vault AppRole runtime (2026-05-31)

Mục tiêu verify: chứng minh Pool nạp DEK từ Vault (AppRole) và unwrap qua Transit, không âm thầm rơi về local key khi `T8_ALLOW_LOCAL_KEY_FALLBACK=0`.

| Kịch bản | Kết quả |
|---|---|
| Vault reachable + AppRole hợp lệ + `T8_ALLOW_LOCAL_KEY_FALLBACK=0` | Pool log có `DEK source: vault` và từng key báo `Loaded key from Vault` (`gcm_dek`, `dte_ma_benh`, `ore_key`). Vault KV chỉ lưu wrapped ciphertext, không lưu plaintext DEK. |
| Vault không reachable (mô phỏng Vault down bằng `VAULT_ADDR=http://127.0.0.1:18200`) + `T8_ALLOW_LOCAL_KEY_FALLBACK=0` | Pool startup **fail rõ ràng** với `Vault runtime keys unavailable ...` + `Failed to establish a new connection`; không có log local fallback. |
| E2E pass trên stack thật (MongoDB + Vault) | `tests/test_e2e.py` **7/7 PASSED** khi Router + Pool chạy với `T8_ALLOW_LOCAL_KEY_FALLBACK=0` và Pool log `DEK source: vault`. |

Ghi chú vận hành:
- Trong quá trình bring-up ban đầu, mình từng dùng một MongoDB wire-protocol emulator vì chưa có `mongod`/`docker`. Kết quả cuối cùng ghi ở đây là **run thật** trên MongoDB chạy tại `27017` cùng Vault thật, nên đây là bằng chứng cuối cùng cần dùng để đối chiếu trạng thái dự án.

---

## 2. Hiệu năng (`tests/benchmark.py`, 50 runs/mode)

| Chế độ / truy vấn | avg | p99 | QPS |
|---|---|---|---|
| SOFTWARE — `count` (trên ciphertext) | 3.9 ms | 6.3 ms | 259 |
| TEE — `sum_vien_phi` (toàn bộ 10k) | 70.5 ms | 79 ms | 14.2 |
| TEE — `avg_vien_phi` (toàn bộ 10k) | 71.7 ms | 94 ms | 13.9 |
| **Hybrid Adaptive** — `avg` (lọc E11 trước) | **26.3 ms** | 34.8 ms | 38.0 |

**Diễn giải trung thực:** Software (chỉ thao tác trên ciphertext, không giải mã) rất nhanh. TEE đắt hơn vì **phải giải mã từng bản ghi + DuckDB** — đây là *cái giá của bảo mật*, không phải overhead vô ích. **Hybrid** giảm chi phí TEE ~2.7× bằng cách dùng DTE/ORE lọc bớt bản ghi *trước* khi đẩy vào enclave (70 ms → 26 ms). Đây chính là giá trị của kiến trúc lai.

---

## 3. Chịu tải đồng thời (`tests/benchmark_concurrent.py`)

| Số client | avg | p99 | QPS | **Lỗi** |
|---|---|---|---|---|
| 1 | 15.6 ms | 21 ms | 55 | **0%** |
| 5 | 76 ms | 87 ms | 62 | **0%** |
| 10 | 146 ms | 302 ms | 63 | **0%** |
| 20 | 279 ms | 1049 ms | 63 | **0%** |
| **50** | 623 ms | 6580 ms | 61 | **0%** |

→ Thread pool 8 worker chịu được tới **50 bác sĩ đồng thời, 0% lỗi** (đúng yêu cầu kịch bản Bước 4). Đuôi p99 tăng ở 50 client (~6.6s) là dấu hiệu bão hòa — đúng lúc cơ chế **Adaptive fallback** vào cuộc.

---

## 4. Đánh giá an ninh (chứng minh đánh đổi của searchable encryption)

### 4.1. Rò rỉ THỨ TỰ của ORE (`tests/attack_ore.py`, `tests/attack_bipartite.py`)

| Tấn công | Exact | Trong ±2 năm | MAE (năm) |
|---|---|---|---|
| Baseline (đoán mò theo phân bố) | 0.9% | 4.8% | **32.5** |
| Chỉ biết phân bố tuổi | 38.9% | **100%** | **0.67** |
| Biết 10 cặp known-plaintext | 29.3% | 66.1% | 2.13 |
| Rank-linkage (`attack_bipartite`, N=5000) | 58.5% | 100% | 0.45 |

→ ORE **lộ thứ tự** → kẻ tấn công khôi phục tuổi với sai số **< 1 năm** (so với đoán mò sai 32.5 năm). **Bài học:** truy vấn nhạy cảm cần tổng hợp phải đẩy vào **TEE** (không lộ thứ tự), không chạy trên cột ORE.

### 4.2. Rò rỉ TẦN SUẤT của DTE (`attack_bipartite` — mongo profile)

- `unique_ratio = 0.19` trên mẫu → nhiều bản ghi chung ciphertext (cùng mã bệnh) → **đếm tần suất** + kiến thức nền (phân bố ICD-10) để suy luận.

### 4.3. q-leakage: TEE+RBAC masked vs Software fallback raw (`tests/leakage.py`)

| Nhánh | output_exposure | leakage_score |
|---|---|---|
| TEE + RBAC (researcher, **masked**) | **0.0** | 0.645 |
| Software **fallback** (researcher, **raw**) | **1.0** | 1.0 |

→ Khi ở TEE+RBAC, kết quả nhạy cảm bị **mask hoàn toàn** (output_exposure 0). Khi **fallback** xuống Software, kết quả thô bị **phơi bày** (output_exposure 1.0). Cả hai vẫn còn *pattern leakage* (access-pattern) → minh chứng định lượng: **fallback đánh đổi bảo mật lấy thông lượng**.

---

## 5. Kiểm soát truy cập (RBAC + ABAC) — `scripts/demo_abac.py`

| Tình huống | Kết quả |
|---|---|
| Bác sĩ khoa Tim_mach đếm bệnh nhân | **1700** (chỉ khoa mình) |
| Admin đếm cùng câu | **10000** (toàn viện) |
| Bác sĩ Tim_mach cố lọc khoa Noi | **Vẫn 1700** — ABAC ghi đè, không lách được |
| Nhân viên hành chính xem viện phí | Thấy số, **che `ma_benh`** |
| Researcher xem AVG | **`[MASKED]`** |
| Doctor chạy `sum_vien_phi` | **403 Forbidden** |

→ Cách ly dữ liệu theo khoa (ABAC dept-scoping) + phân quyền cột (RBAC) chạy đúng, có demo.

---

## 6. Tóm tắt một dòng cho hội đồng

> *"Kiến trúc Hybrid Adaptive chạy thật end-to-end (E2E 7/7): truy vấn nhẹ chạy trên ciphertext (Software, 259 QPS), truy vấn nhạy cảm giải mã trong enclave (TEE), tự fallback khi quá tải; chịu 50 client 0% lỗi. Đánh giá an ninh định lượng cho thấy rõ đánh đổi: ORE lộ thứ tự (khôi phục tuổi MAE 0.67 năm), DTE lộ tần suất, và fallback tăng output-exposure — tất cả được kiểm soát bằng RBAC/ABAC + định tuyến về TEE. Giới hạn (SGX simulation, attestation HMAC) nêu rõ ở docs/LIMITATIONS.md."*
