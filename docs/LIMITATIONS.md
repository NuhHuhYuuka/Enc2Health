# Enc²Health — Mô hình đe dọa, Thuộc tính bảo mật, và Giới hạn

> Tài liệu này nêu **thật vs mô phỏng**, **bảo vệ được gì / chưa bảo vệ được gì**, và các **giả định**.
> Mục tiêu: trung thực khoa học — biết rõ ranh giới của hệ thống để trình bày/bảo vệ đúng mức.
> Cập nhật: 2026-05-31 · Môn NT219.Q2.ANTT.

---

## 1. Mô hình đe dọa (Threat Model)

**Kẻ tấn công (adversary):** Nhà cung cấp Cloud / quản trị viên đám mây **honest-but-curious** —
tuân thủ giao thức (không phá hoại dữ liệu) nhưng **tò mò**, có quyền:
- Đọc toàn bộ **đĩa** (MongoDB data files, log).
- Đọc **RAM** và trạng thái **OS-level** của các tiến trình *ngoài* enclave.
- Quan sát **traffic mạng** giữa các service (nếu không mã hóa kênh).
- Quan sát **access pattern** (truy vấn nào chạm bản ghi nào).

**Mục tiêu bảo vệ:** PHI (thông tin định danh + lâm sàng + tài chính y tế) — tuân HIPAA/GDPR.

**NGOÀI phạm vi (out of scope):**
- Kẻ tấn công chủ động phá hoại / sửa dữ liệu (integrity attack có chủ đích).
- Tấn công kênh phụ phần cứng (cache timing, Spectre/Meltdown, power) — có *giảm thiểu* cơ bản (`spectre_mitig.py`, `aes_register_safe.py`) nhưng **không** là trọng tâm đánh giá.
- Client/bác sĩ bị xâm nhập (giả định endpoint client là tin cậy).
- Rò rỉ qua **kết quả tổng hợp hợp pháp** (vd: researcher xem AVG nhiều lần để suy luận) — chỉ giảm thiểu bằng RBAC/masking, không chống differential-privacy.

---

## 2. Thuộc tính bảo mật theo lớp dữ liệu

| Lớp dữ liệu | Cơ chế | Bảo vệ trước CSP | Đánh đổi (rò rỉ) |
|---|---|---|---|
| PII (họ tên, CMND, địa chỉ) | ECC P-384 ECIES (bất đối xứng) | ✅ CSP chỉ thấy ciphertext; chỉ enclave có private key giải mã | Không rò (semantic security) |
| Lâm sàng (mã bệnh, khoa) | **DTE** AES-SIV (tất định) | ⚠️ Cho phép tìm `=` trên ciphertext | **Rò tần suất / equality** (xem §4) |
| Tuổi, ngày | **ORE/OPE** Boldyreva | ⚠️ Cho phép so sánh `<,>` trên ciphertext | **Rò thứ tự** (xem §4) |
| Viện phí, kết quả XN | AES-GCM-256 | ✅ Chỉ giải mã trong enclave để tính SUM/AVG | Không rò khi ở yên; rò kết quả tổng hợp |

---

## 3. THẬT vs MÔ PHỎNG — ranh giới quan trọng nhất

> Đây là phần phải nói rõ khi bảo vệ. Hệ thống **chạy end-to-end thật** ở tầng phần mềm,
> nhưng tầng *phần cứng tin cậy* hiện là **mô phỏng**.

| Thành phần | Trạng thái | Ghi chú trung thực |
|---|---|---|
| Mã hóa DTE/ORE/GCM/ECC | ✅ **THẬT** | `cryptography` + `pyope`, có self-test |
| Lưu & tìm kiếm trên ciphertext (MongoDB) | ✅ **THẬT** | DTE equality + ORE range chạy trực tiếp trên bản mã |
| Router: routing, RBAC/ABAC, cost model, adaptive | ✅ **THẬT** | 33/33 unit test |
| Enclave nhận ciphertext → giải mã AES-GCM → DuckDB SUM/AVG | ✅ **THẬT** (logic) | Chạy trong tiến trình Python, kết quả khớp Mongo (E2E 7/7) |
| Enclave nhận `pii_enc` → lấy private key khoa → giải mã ECC PII | ✅ **THẬT** (logic) | Router luôn route `get_patient`/`lookup_patient` qua Pool/TEE và áp RBAC/ABAC mask; cần SGX hardware thật để plaintext RAM được phần cứng bảo vệ. |
| **SGX Enclave (cô lập RAM bằng phần cứng)** | 🟡 **MÔ PHỎNG** | Chạy `gramine-direct` (`remote_attestation = "none"`). **Trong simulation, plaintext VẪN nằm ở RAM thường** — guarantee "admin không đọc được RAM" là *kiến trúc*, **chưa được phần cứng cưỡng chế**. Cần SGX hardware thật để guarantee có hiệu lực. |
| **Attestation (RA-TLS)** | 🟡 **SIGNED SIMULATION** | `/attest` trả document ký **HMAC + freshness (timestamp)**, KHÔNG phải SGX Quote/DCAP thật. Chống replay cơ bản, nhưng **không chứng minh được danh tính enclave bằng phần cứng**. |
| Vault (Envelope Encryption / unwrap DEK) | ✅ **THẬT** | DEK được wrap bằng Vault Transit lúc setup, KV-v2 chỉ lưu ciphertext wrapped blob + metadata; runtime dùng AppRole (`VAULT_ROLE_ID` + `VAULT_SECRET_ID` → token) để unwrap DEK qua Transit. File local chỉ là **fallback dev có chủ ý** khi bật `T8_ALLOW_LOCAL_KEY_FALLBACK=1`. |
| mTLS giữa các service | 🟡 **TÙY CHỌN** | Có cert + chạy pass ở smoke; **chưa bật mặc định** mọi mode. Không set env → chạy HTTP trần. |
| Client-side encryption (app bác sĩ) | 🟡 **MÔ PHỎNG** | Kịch bản: client mã hóa trước khi gửi. Thực tế: `generate_ehr.py` mã hóa phía server-script. |

**Tóm tắt 1 câu cho hội đồng:** *"Toàn bộ pipeline mã hóa–truy vấn–giải mã–tổng hợp chạy thật và đúng (E2E verified). Lớp cô lập phần cứng (SGX) và attestation hiện ở chế độ mô phỏng Gramine; nâng lên SGX/DCAP thật là future work, không thay đổi kiến trúc."*

---

## 4. Rò rỉ đã biết (đo được)

Đây là **đánh đổi có chủ đích** của searchable encryption, không phải lỗi:

1. **DTE — rò equality & tần suất.** Cùng mã bệnh → cùng ciphertext. Kẻ tấn công đếm tần suất + dùng kiến thức nền (phân bố ICD-10) để suy luận.
   - Đo: `tests/attack_bipartite.py` (frequency/rank-linkage), `tests/leakage.py` (entropy).
2. **ORE — rò thứ tự.** Bản mã `tuoi` giữ nguyên thứ tự → suy ra ai già hơn ai, và (đủ dữ liệu) xấp xỉ giá trị (tấn công Naveed et al. CCS'15).
   - Đo: `tests/attack_ore.py` (N=2000). Kẻ tấn công **chỉ biết phân bố tuổi** khôi phục được tuổi với **MAE 0.67 năm, 100% trong ±2 năm** — so với baseline đoán mò MAE 32.5 năm. → ORE gần như không che được tuổi khi attacker có auxiliary distribution. (Kịch bản known-plaintext 10 anchor: MAE 2.13 năm — thực tế hơn nhưng thô hơn vì không giả định phân bố.)
3. **Access-pattern leakage khi FALLBACK.** Khi TEE→Software, truy vấn chạy trên cột DTE/ORE → CSP thấy *pattern* nào được chạm. `leakage.py` đo output-exposure tăng rõ ở nhánh fallback so với TEE+RBAC masked.
4. **Rò kết quả tổng hợp hợp pháp.** Vai trò được phép (admin/doctor) nhận con số thật; nhiều truy vấn có thể bị lạm dụng suy luận. Giảm thiểu: RBAC/ABAC + masking, KHÔNG có differential privacy.

---

## 5. Giả định (Assumptions)

- **Phân phối khóa an toàn:** Bộ key dùng chung (`crypto/data/keys/`) được giả định phân phối qua kênh tin cậy; production phải dùng Vault + envelope encryption thật.
- **Enclave & code bên trong là tin cậy** (TCB): giả định manifest ký đúng, code enclave không độc hại.
- **JWT secret (`AUTH_JWT_SECRET`) được giữ bí mật** và chia sẻ an toàn giữa Router ↔ Pool.
- **Client endpoint tin cậy** (máy bác sĩ không bị xâm nhập).
- **MongoDB/Vault/Prometheus chạy trong cùng vùng tin cậy vận hành** với honest-but-curious admin (admin tò mò nhưng không phá hoại).

---

## 6. Lộ trình đóng khoảng trống (Future work)

| Khoảng trống | Việc cần làm | Mức |
|---|---|---|
| SGX mô phỏng | Chạy trên phần cứng SGX2 thật + `gramine-sgx`, bật EPC thật | Cao (cần hardware) |
| Attestation HMAC | RA-TLS + DCAP quote thật, verify MRENCLAVE | Cao |
| Vault Transit hardening | Chính sách rotation, audit, rewrap và monitoring cho DEK/MK trên Vault Transit | Trung bình |
| mTLS chưa mặc định | Bật TLS mặc định Router↔Pool↔Vault | Thấp |
| Chưa đánh ORE | `tests/attack_ore.py`: tái dựng rank/xấp xỉ tuổi từ ORE ciphertext | Thấp |
| q-leakage chưa định lượng chặt | Đo "bit rò thêm" khi fallback (định lượng so sánh) | Trung bình |
| Client-side enc mô phỏng | App client mã hóa trước khi gửi (đúng kịch bản); hiện `generate_ehr.py` đã sinh `pii_enc` ECC theo khoa nhưng chưa có HIS/EMR client thật | Trung bình |
| HL7 FHIR | Parse FHIR Document thật thay vì chỉ schema | Trung bình |

---

## 7. Vì sao các giới hạn này CHẤP NHẬN ĐƯỢC cho đồ án

- **Trọng tâm đồ án** là kiến trúc **Hybrid Adaptive** (định tuyến truy vấn mã hóa + tự thích nghi TEE↔Software) — phần này **chạy thật, verify được** (E2E 7/7, adaptive fallback có hysteresis, benchmark overhead <2×).
- SGX/DCAP thật là vấn đề **hạ tầng phần cứng**, không thay đổi *thiết kế* hệ thống; Gramine simulation cho phép phát triển + chứng minh logic mà không cần CPU SGX.
- Việc **nêu rõ ranh giới thật/mô phỏng** (tài liệu này) chính là điểm cộng về **tính trung thực khoa học** — thường được hội đồng đánh giá cao hơn là che giấu.
