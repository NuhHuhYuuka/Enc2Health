# Hướng Dẫn Kiến Trúc & Vận Hành Hệ Thống Enc²Health
## (Tài liệu chi tiết từ cơ bản đến nâng cao dành cho nghiên cứu và báo cáo)

Chào mừng bạn đến với tài liệu phân tích sâu hệ thống **Enc²Health**. Tài liệu này được thiết kế để giải thích hệ thống từ những khái niệm cốt lõi nhất (dành cho người mới tiếp cận) đến các cơ chế vận hành phức tạp của mã nguồn (dành cho kỹ sư và nhà phát triển).

---

# PHẦN 1: BÀI TOÁN & TƯ DUY THIẾT KẾ (DÀNH CHO NGƯỜI MỚI)

## 1. Bài toán: Bảo vệ dữ liệu y tế trên đám mây (Cloud)
Khi bệnh viện số hóa hồ sơ bệnh án điện tử (EHR) và đưa lên đám mây, họ đối mặt với một rủi ro lớn: **Rò rỉ thông tin y tế nhạy cảm (PHI)**. 
* Theo luật pháp quốc tế (như HIPAA), thông tin bệnh án là tối mật.
* Tuy nhiên, nhà cung cấp dịch vụ đám mây (Cloud Service Provider - CSP) hoặc các quản trị viên hệ thống đám mây có thể tò mò (mô hình **Honest-but-curious**). Họ có thể đọc trộm cơ sở dữ liệu trên đĩa cứng, hoặc thậm chí đọc trộm dữ liệu đang nằm trên RAM.

## 2. Giải pháp thông thường và hạn chế
Nếu ta mã hóa dữ liệu theo cách thông thường (như mã hóa đĩa AES):
* **Lúc lưu trữ**: Dữ liệu an toàn.
* **Lúc tính toán**: Để chạy được các câu lệnh như *"Tìm bệnh nhân có mã bệnh I01"* hoặc *"Tính viện phí trung bình của khoa cấp cứu"*, cơ sở dữ liệu bắt buộc phải giải mã dữ liệu ra plaintext. Lúc này dữ liệu sẽ lộ trên RAM của Cloud.
* **Hạn chế**: Nếu mã hóa toàn bộ mà không giải mã, cơ sở dữ liệu sẽ không thể tìm kiếm, sắp xếp hay tính toán được gì.

## 3. Cách Enc²Health giải quyết bài toán: Kiến trúc lai thích nghi
**Enc²Health** sử dụng một kiến trúc kết hợp (Hybrid):
1. **Mã hóa chọn lọc (Field-Level Encryption)**: Mỗi loại thông tin (tên, tuổi, viện phí, mã bệnh) được mã hóa bằng một thuật toán chuyên biệt trước khi đẩy lên Cloud. Dữ liệu trên Cloud luôn là các chuỗi ký tự vô nghĩa (ciphertext).
2. **Xử lý trên bản mã (Software Mode)**: Chạy các câu lệnh tìm kiếm thô trực tiếp trên ciphertext mà không cần giải mã.
3. **Môi trường tính toán an toàn (TEE SGX Enclave)**: Đối với các phép toán nhạy cảm (như tính tổng, tính trung bình viện phí), dữ liệu mã hóa được chuyển vào một "vùng an toàn" được bảo vệ bằng phần cứng của CPU (Enclave). Dữ liệu chỉ được giải mã và tính toán bên trong vùng này, Cloud hoàn toàn không thể can thiệp hay đọc trộm.
4. **Tự động thích ứng (Self-adaptive)**: Vì vùng an toàn của phần cứng (enclave) có giới hạn dung lượng và hiệu năng thấp hơn CPU ngoài, hệ thống sẽ tự động đo lường tải. Khi enclave bị quá tải, hệ thống tự động hạ cấp chuyển dịch vụ tính toán về dạng Software thông thường để đảm bảo không bị nghẽn (Fallback Mode).

---

# PHẦN 2: LỚP MẬT MÃ CHỌN LỌC (CRYPTOGRAPHIC SYSTEM)

Để hiểu được luồng đi của dữ liệu, ta cần hiểu rõ 5 dạng mã hóa được Enc²Health áp dụng lên cơ sở dữ liệu MongoDB:

```
[Hồ sơ bệnh nhân gốc]
 ├── Họ tên, CCCD, Địa chỉ, Bệnh án ───> Mã hóa bất đối xứng (ECC ECIES) ──> pii_enc
 ├── Mã bệnh, Khoa phòng, CCCD (DTE) ──> Mã hóa tất định (AES-SIV) ────────> ma_benh_enc, khoa_phong_enc, cmnd_dte
 ├── Tuổi, Ngày nhập viện ─────────────> Mã hóa bảo toàn thứ tự (OPE) ──────> tuoi_enc
 ├── Viện phí ────────────────────────> Mã hóa đối xứng ngẫu nhiên (GCM) ──> vien_phi_enc
 └── Triệu chứng lâm sàng ─────────────> Chỉ mục tìm kiếm ngược SSE ────────> sse_index (HMAC + GCM)
```

### 2.1. Mã hóa bất đối xứng ECC ECIES P-384 (Dành cho PII nhạy cảm)
* **Ý tưởng**: Giống như hòm thư. Ai cũng có thể nhét thư vào qua khe hở (dùng Public Key của khoa để mã hóa), nhưng chỉ người giữ chìa khóa hòm thư (Private Key nằm trong Enclave) mới mở ra đọc được.
* **Cách dùng**: Mã hóa trường `pii_enc` chứa thông tin định danh cá nhân (Họ tên, CCCD, Ngày sinh, Địa chỉ, Bóm tắt bệnh án, Phác đồ điều trị). 
* **Tệp tin**: `crypto/crypto/asym.py`.

### 2.2. Mã hóa tất định DTE - AES-SIV-256 (Dành cho tìm kiếm bằng)
* **Ý tưởng**: Nếu từ gốc là "I01" thì bản mã lúc nào cũng là "xyz123". Do bản mã giống nhau nên ta có thể tìm kiếm chính xác (`=`) trực tiếp trên bản mã.
* **Cách dùng**: Mã hóa trường mã bệnh (`ma_benh_enc`), khoa phòng (`khoa_phong_enc`), số CCCD tra cứu nhanh (`cmnd_dte`).
* **Tệp tin**: `crypto/crypto/dte.py`.

### 2.3. Mã hóa bảo toàn thứ tự OPE/ORE (Dành cho lọc khoảng)
* **Ý tưởng**: Nếu tuổi 30 nhỏ hơn tuổi 40, thì bản mã của tuổi 30 cũng nhỏ hơn bản mã của tuổi 40 (`OPE(30) < OPE(40)`).
* **Cách dùng**: Mã hóa số tuổi (`tuoi_enc`) và ngày nhập viện. MongoDB có thể chạy các câu lệnh tìm kiếm khoảng tuổi dạng `{"tuoi_enc": {"$gte": OPE(60)}}` trực tiếp mà không cần giải mã.
* **Tệp tin**: `crypto/crypto/ore.py`.

### 2.4. Mã hóa đối xứng ngẫu nhiên AES-GCM-256 (Dành cho tính toán số liệu)
* **Ý tưởng**: Đây là mã hóa tiêu chuẩn bảo mật cực cao. Cùng một số tiền viện phí `10.000.000` VND của 2 bệnh nhân khác nhau sẽ cho ra 2 bản mã hoàn toàn khác nhau nhờ vector khởi tạo (IV) ngẫu nhiên. Kẻ tấn công nhìn vào không thể biết ai đóng nhiều tiền hơn ai.
* **Cách dùng**: Mã hóa trường viện phí (`vien_phi_enc`) và kết quả xét nghiệm y khoa.
* **Tệp tin**: `crypto/crypto/gcm.py`.

### 2.5. Chỉ mục tìm kiếm mã hóa SSE (Dành cho tìm kiếm từ khóa lâm sàng)
* **Ý tưởng**: Tạo một bảng mục lục riêng (`sse_index`). Khi bệnh nhân nhập viện, các từ khóa triệu chứng (ví dụ: "ho", "sốt") được băm thành một token HMAC: `token = HMAC(K_token, "ho")`. Bản ghi chỉ mục sẽ lưu token này ánh xạ tới danh sách Patient ID đã được mã hóa bằng AES-GCM.
* **Cách dùng**: Cho phép tìm kiếm bệnh nhân theo triệu chứng hoặc chẩn đoán bằng từ khóa lâm sàng mà không để lộ từ khóa cho MongoDB.
* **Tệp tin**: `crypto/crypto/sse.py`.

---

# PHẦN 3: CHI TIẾT TỪNG NÚT VÀ CƠ CHẾ VẬN HÀNH

Hệ thống hoạt động dựa trên sự phối hợp của 5 nút (nodes) chính:

```
                  ┌────────────────────────┐
                  │   1. Client (JWT)      │
                  └───────────┬────────────┘
                              │ POST /query
                              ▼
                  ┌────────────────────────┐
                  │ 2. Query Router (:8000)│◄─── mTLS ───┐
                  └───────────┬────────────┘             │
            TEE Path  │       │ Software Fallback        │
                      ▼       ▼                          ▼
        ┌──────────────────┐  ┌──────────────┐  ┌─────────────────┐
        │ 4. TEE Enclave   │  │ 3. MongoDB   │  │ 5. Vault (:8200)│
        │    Pool (:9091)  │  │    (:27017)  │  └─────────────────┘
        └──────────────────┘  └──────────────┘
```

### 1. Client Node (Trình duyệt & Giao diện người dùng)
* **Tệp cấu trúc**: [router/ui.html](file:///home/beserk/crypto/repo/Enc2Health/router/ui.html)
* **Cách hoạt động**: Gửi yêu cầu HTTP POST kèm token JWT trong header `Authorization: Bearer <JWT>`. Giao diện có các tab:
  - *Tổng quan*: Xem số lượng, biểu đồ tuổi, viện phí trung bình.
  - *Phân tích lâm sàng*: Nhập mã bệnh, lọc tuổi để chạy thống kê.
  - *Tìm bệnh nhân*: Nhập từ khóa lâm sàng (như "sốt") để tìm danh sách bệnh nhân.
  - *Tra cứu CCCD*: Nhập số CCCD bệnh nhân để giải mã chi tiết hồ sơ.

### 2. Query Router Node (Cổng FastAPI - Cổng `8000`)
* **Tệp cấu trúc**: [router/main.py](file:///home/beserk/crypto/repo/Enc2Health/router/main.py)
* **Nhiệm vụ**: Điều phối toàn bộ vòng đời của một truy vấn.
* **Các thành phần con**:
  - **Phân quyền ABAC/RBAC (`abac.py` & `rbac.py`)**: Kiểm tra JWT. Bác sĩ khoa Tim mạch sẽ bị ABAC giới hạn cứng: tiêm filter `{khoa_phong: "Tim_mach"}` vào tất cả các truy vấn. Nhân viên hành chính (`admin_staff`) được cấu hình cho phép xem đầy đủ PII cũ (CCCD, địa chỉ) nhưng ẩn đi bệnh án và phác đồ điều trị (`tom_tat_benh_an`, `phac_do_dieu_tri`).
  - **Bộ định tuyến (`query_router.py`)**: Đọc tên query (ví dụ: `avg_vien_phi`). Quyết định xem toán tử này cần chạy TEE hay Software.
  - **Mô hình chi phí (`cost_model.py`)**: Ước lượng chi phí thời gian chạy Software $C_{soft}$ (dựa trên số liệu đo lường tốc độ mã hóa thực tế lưu trong `c_soft_metrics.json`) so với thời gian chạy enclave $C_{tee}$ (dựa trên bộ nhớ đệm RSS).
  - **Tự thích nghi (`adaptive.py`)**: Lắng nghe áp lực phần cứng. Khi tài nguyên EPC của enclave bão hòa ($\ge 80\%$), router tự động chuyển dịch vụ TEE sang Software Fallback và tự phục hồi khi áp lực giảm xuống $\le 60\%$.

### 3. Cơ sở dữ liệu MongoDB (Cổng `27017`)
* **Nhiệm vụ**: Lưu trữ dữ liệu mã hóa.
* **Cấu trúc collection `patient_records`**:
  - Cột định danh plaintext: `patient_id` (UUIDv5 ngẫu nhiên), `dept` (Khoa để biết dùng key khoa nào giải mã).
  - Cột mã hóa đối xứng tất định (DTE): `ma_benh_enc`, `khoa_phong_enc`, `cmnd_dte`.
  - Cột mã hóa bảo toàn thứ tự (OPE): `tuoi_enc`.
  - Cột mã hóa đối xứng ngẫu nhiên (AES-GCM): `vien_phi_enc`.
  - Cột mã hóa bất đối xứng (ECC ECIES): `pii_enc`.

### 4. TEE Enclave Pool Node (FastAPI chạy trong Gramine - Cổng `9091`)
* **Tệp cấu trúc**: [enclave/ecall_pool.py](file:///home/beserk/crypto/repo/Enc2Health/enclave/ecall_pool.py) & [enclave/enclave_service.py](file:///home/beserk/crypto/repo/Enc2Health/enclave/enclave_service.py)
* **Nhiệm vụ**: Giải mã dữ liệu và tính toán kết quả trong RAM an toàn.
* **Cách hoạt động**:
  - Chạy cơ sở dữ liệu in-memory **DuckDB**.
  - Nhận danh sách các bản mã `vien_phi_enc` hoặc `pii_enc` từ Router.
  - Tải khóa giải mã y tế tương ứng từ Vault thông qua phương thức bảo mật.
  - Tiến hành giải mã dữ liệu về plaintext bên trong enclave.
  - Đẩy dữ liệu plaintext vào bảng tạm thời của DuckDB và thực thi các phép toán gộp (`SUM`, `AVG`, `COUNT DISTINCT`).
  - Ghi đè bộ nhớ RAM chứa plaintext bằng byte rỗng sau khi tính toán xong để ngăn rò rỉ dữ liệu.

### 5. HashiCorp Vault Node (KMS - Cổng `8200`)
* **Nhiệm vụ**: Quản lý khóa an toàn.
* **Cơ chế**: Cung cấp cơ chế xác thực **AppRole** cho Enclave Pool. Khi Enclave khởi động, nó gửi `role_id` và `secret_id` để lấy token tạm thời từ Vault, từ đó dùng token để lấy các private key của khoa hoặc giải bọc (unwrap) khóa giải mã viện phí `gcm_dek`.

---

# PHẦN 4: LUỒNG HOẠT ĐỘNG CHI TIẾT CỦA CÁC TRUY VẤN (STEP-BY-STEP FLOWS)

Dưới đây là mô tả chi tiết từng bước hoạt động của hệ thống khi chạy 3 kịch bản truy vấn thực tế.

## Luồng 1: Tra cứu hồ sơ bệnh nhân bằng CCCD (Lookup Patient)
*Bối cảnh: Nhân viên hành chính nhập số CCCD `05641395376` trên màn hình tra cứu.*

```
[Trình duyệt] ── (1) POST /query {query_type: "lookup_patient", cmnd: "05641395376"} ──> [Query Router]
                                                                                             │
                                                                                 (2) Auth JWT -> role=admin_staff
                                                                                 (3) ABAC: Không giới hạn khoa phòng
                                                                                 (4) encrypt_cmnd("05641395376") -> cmnd_dte
                                                                                             │
   [Query Router] <── (6) Trả về bản ghi chứa pii_enc, dept="Tim_mach" ── [MongoDB] <── (5) findOne({"cmnd_dte": cmnd_dte})
         │
         ├── (7) Kiểm tra ABAC: admin_staff xem được mọi khoa phòng (Pass)
         │
         └── (8) POST /query/pii {pii_enc: "...", dept: "Tim_mach"} ──> [TEE Enclave]
                                                                             │
                                                                   (9) Auth AppRole -> Đọc Vault
                                                                   (10) Tải private key khoa Tim_mach từ Vault
                                                                   (11) Giải mã pii_enc bằng ECC ECIES P-384
                                                                             │
   [Query Router] <── (12) Trả về plaintext PII y tế ────────────────────────┘
         │
         ├── (13) Giải mã ma_benh_enc (DTE) và chan_doan_enc (AES-GCM) cục bộ
         ├── (14) Áp dụng RBAC:
         │        - Giữ nguyên: ho_ten, cmnd, dia_chi, ngay_sinh, ma_benh, chan_doan
         │        - Mask thành "[MASKED]": tom_tat_benh_an, phac_do_dieu_tri
         │
[Trình duyệt] <── (15) HTTP 200: Trả về thông tin đã được phân quyền và che giấu ẩn y tế
```

---

## Luồng 2: Phân tích viện phí trung bình (AVG Billing)
*Bối cảnh: Bác sĩ Nội thực hiện thống kê chi phí trung bình của nhóm bệnh nhân tiểu đường (ICD-10: `I01`).*

### Kịch bản 2A: Hệ thống chạy bình thường (Normal Mode - TEE)
*Khi áp lực bộ nhớ của Enclave dưới 80%.*

```
[Trình duyệt] ── (1) POST /query {query_type: "avg_vien_phi", filters: {ma_benh: "I01"}} ──> [Query Router]
                                                                                                   │
                                                                                      (2) Auth JWT -> role=doctor, dept=Noi
                                                                                      (3) ABAC: Tiêm bắt buộc khoa_phong="Noi"
                                                                                      (4) encrypt_dte("I01") -> ma_benh_enc
                                                                                      (5) encrypt_dte("Noi") -> khoa_phong_enc
                                                                                      (6) Định tuyến: avg_vien_phi -> TEE mode
                                                                                      (7) Check EPC: < 80% -> OK (TEE)
                                                                                                   │
   [Query Router] <── (9) Trả về 200 bản mã vien_phi_enc ── [MongoDB] <── (8) find({ma_benh_enc, khoa_phong_enc})
         │
         └── (10) POST /query {query_type: "avg_vien_phi", ciphertexts: [200 bản mã]} ──> [TEE Enclave]
                                                                                               │
                                                                                     (11) Giải mã 200 bản mã bằng gcm_dek
                                                                                     (12) Đẩy 200 floats vào DuckDB
                                                                                     (13) SQL: SELECT AVG(vien_phi) FROM temp
                                                                                               │
   [Query Router] <── (14) Trả về kết quả AVG và số record ────────────────────────────────────┘
         │
         ├── (15) Áp dụng RBAC: Bác sĩ được quyền xem viện phí trung bình (Không mask)
         │
[Trình duyệt] <── (16) HTTP 200: Hiển thị kết quả viện phí trung bình và độ trễ
```

### Kịch bản 2B: Hệ thống quá tải bộ nhớ (Fallback Mode - Software)
*Khi áp lực bộ nhớ của Enclave vượt quá 80%.*

```
[Trình duyệt] ── (1) POST /query {query_type: "avg_vien_phi", filters: {ma_benh: "I01"}} ──> [Query Router]
                                                                                                   │
                                                                                      (2) Auth JWT -> role=doctor, dept=Noi
                                                                                      (3) ABAC: Tiêm bắt buộc khoa_phong="Noi"
                                                                                      (4) encrypt_dte("I01") -> ma_benh_enc
                                                                                      (5) encrypt_dte("Noi") -> khoa_phong_enc
                                                                                      (6) Định tuyến: avg_vien_phi -> TEE mode
                                                                                      (7) Check EPC: >= 80% (Quá tải!)
                                                                                      (8) Kích hoạt: SOFTWARE FALLBACK
                                                                                                   │
   [Query Router] <── (10) Trả về 200 bản mã vien_phi_enc ── [MongoDB] <── (9) find({ma_benh_enc, khoa_phong_enc})
         │
         ├── (11) Giải mã 200 bản mã vien_phi_enc trực tiếp trên host bằng khóa gcm_dek.key nạp trong RAM Python
         ├── (12) Tính toán trung bình cộng trực tiếp bằng thuật toán Python (Bỏ qua gọi Enclave)
         │
[Trình duyệt] <── (13) HTTP 200: Trả kết quả kèm nhãn cảnh báo "Software Fallback Mode"
```

---

## Luồng 3: Tìm kiếm bệnh nhân theo triệu chứng lâm sàng (SSE Search)
*Bối cảnh: Trình duyệt gửi từ khóa tìm kiếm triệu chứng "ho khan".*

```
[Trình duyệt] ── (1) POST /search {keyword: "ho khan"} ──> [Query Router]
                                                                │
                                                    (2) Auth JWT -> role=doctor, dept=Tim_mach
                                                    (3) ABAC: Tiêm filter khoa_phong="Tim_mach"
                                                    (4) Chuẩn hóa từ khóa -> token = HMAC(K_token, "ho khan")
                                                                │
   [Query Router] <── (6) Trả về postings_enc mã hóa ── [MongoDB] <── (5) findOne({"token": token})
         │
         ├── (7) Giải mã postings_enc bằng khóa SSE -> Trả về danh sách bệnh nhân plaintext
         ├── (8) Lọc ABAC: Loại bỏ các bệnh nhân không thuộc khoa "Tim_mach"
         ├── (9) Áp dụng RBAC: doctor có quyền xem danh sách postings bệnh nhân
         │
[Trình duyệt] <── (10) Trả về danh sách Patient ID trùng khoa Tim_mach có triệu chứng "ho khan"
```

---

# PHẦN 5: ĐÁNH GIÁ AN NINH & ĐỊNH HƯỚNG SẢN XUẤT (PRODUCTION-READY)

Khi trình bày đồ án hoặc đưa hệ thống vào vận hành thực tế, có một số điểm yếu bảo mật cần nắm vững và đưa ra phương án khắc phục:

1. **Điểm yếu giải mã Fallback ngoài Enclave**:
   - *Vấn đề*: Chế độ Software Fallback nạp khóa `gcm_dek.key` trực tiếp vào RAM của tiến trình Python ngoài Enclave. Nếu Cloud admin độc hại đọc trộm RAM của host lúc này, họ sẽ lấy được khóa.
   - *Giải pháp*: Thay thế phép tính giải mã-rồi-cộng bằng thuật toán **mã hóa đồng cấu Paillier (Homomorphic Encryption)**. Router có thể nhân trực tiếp các bản mã Paillier để ra bản mã của phép tính tổng mà hoàn toàn không cần giải mã dữ liệu hay giữ khóa giải mã.
2. **Gramine Simulation**:
   - *Vấn đề*: Hệ thống hiện chạy giả lập nên vùng enclave không thực sự được mã hóa phần cứng.
   - *Giải pháp*: Triển khai trên các nền tảng đám mây hỗ trợ Intel SGX vật lý thật (như Azure DC-series) và sử dụng chứng thực **RA-TLS (Remote Attestation TLS)** để thiết lập kết nối an toàn với Enclave.
3. **Rò rỉ SSE & ORE**:
   - *Vấn đề*: Các bản mã ORE giữ nguyên tính chất thứ tự và SSE giữ nguyên tính chất lặp lại của từ khóa truy vấn, có thể bị tấn công bằng phân tích tần suất (Frequency/Order Analysis).
   - *Giải pháp*: Áp dụng cơ chế **ORAM (Oblivious RAM)** hoặc chèn thêm dữ liệu nhiễu giả lập (*Padding/Dummy records*) vào chỉ mục tìm kiếm để che giấu kích thước thực của kết quả trả về.
