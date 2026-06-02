# 🎓 Cẩm Nang Chuẩn Bị Bảo Vệ Đồ Án & Phân Tích Sâu Enc²Health

Tài liệu này chứa bộ 30 câu hỏi và câu trả lời thực tế, bao quát toàn bộ khía cạnh kiến trúc lai (Hybrid Adaptive), mật mã học chọn lọc, cơ chế tự thích nghi và an ninh hệ thống **Enc²Health**. Bộ câu hỏi được thiết kế nhằm giúp sinh viên tự tin trả lời các câu hỏi phản biện từ Hội đồng Giảng viên.

---

## 📌 PHẦN 1: TỔNG QUAN & KIẾN TRÚC LAI TỰ THÍCH NGHI (HYBRID ADAPTIVE)

### Q1: Tại sao không mã hóa toàn bộ dữ liệu bằng một thuật toán mạnh nhất (như AES-GCM) mà phải chia ra nhiều thuật toán (DTE, ORE, ECC ECIES, AES-GCM)?
> [!IMPORTANT]
> Đây là câu hỏi kinh điển về mặt thiết kế hệ thống cơ sở dữ liệu mã hóa.
* **Trả lời**: Nếu mã hóa toàn bộ cơ sở dữ liệu bằng AES-GCM, bản mã sinh ra sẽ hoàn toàn ngẫu nhiên. Khi đó, máy chủ đám mây (Cloud) hoàn toàn không thể thực hiện so khớp bằng (`=`), tìm kiếm khoảng (`>`, `<`, `between`) hay tìm kiếm triệu chứng lâm sàng. Để chạy bất kỳ câu lệnh nào, ta buộc phải tải toàn bộ cơ sở dữ liệu về phía Client/Enclave để giải mã và xử lý, gây nghẽn băng thông nghiêm trọng và tốn tài nguyên tính toán.
* Do đó, ta phải áp dụng mô hình **Mã hóa chọn lọc (Field-Level Encryption)**:
  * **DTE (AES-SIV)** cho tìm kiếm bằng chính xác (`ma_benh`, `khoa_phong`).
  * **ORE (Boldyreva)** cho lọc khoảng (`tuoi`).
  * **AES-GCM-256** cho dữ liệu cần tính toán số học (`vien_phi`) và chỉ giải mã trong vùng an toàn TEE.
  * **ECC ECIES P-384** cho dữ liệu định danh cá nhân (PII) cần bảo mật bất đối xứng từ nguồn.
  * **Static SSE** cho tìm kiếm từ khóa chẩn đoán/triệu chứng lâm sàng văn bản dài.

---

### Q2: Tại sao gọi kiến trúc này là "lai tự thích nghi" (Hybrid Adaptive)? Cơ chế hoạt động của nó như thế nào?
* **Trả lời**: 
  * **Lai (Hybrid)**: Hệ thống kết hợp cả xử lý truy vấn trên bản mã ở máy chủ Cloud thông thường (Software Mode) và xử lý tính toán giải mã an toàn trong phân vùng phần cứng CPU bảo mật (TEE Mode).
  * **Tự thích nghi (Adaptive)**: Router liên tục giám sát tải của Enclave (áp lực bộ nhớ EPC). Khi phát hiện Enclave bị bão hòa hoặc quá tải ($\ge 80\%$), hệ thống tự động định tuyến toán tử từ TEE Mode xuống Software Fallback Mode để duy trì tính sẵn sàng. Khi áp lực giảm xuống mức an toàn ($\le 60\%$), hệ thống tự động khôi phục về TEE Mode để bảo vệ an toàn tối đa cho dữ liệu.

---

### Q3: Vùng nhớ EPC là gì? Tại sao phải giám sát áp lực bộ nhớ EPC?
* **Trả lời**: 
  * **EPC (Enclave Page Cache)** là phân vùng bộ nhớ vật lý được CPU dành riêng và mã hóa phần cứng để chạy các ứng dụng an toàn trong Intel SGX Enclave. Trên các CPU thông thường, dung lượng EPC bị giới hạn cứng (thường là 128MB hoặc 256MB).
  * **Lý do giám sát**: Nếu lượng dữ liệu cần xử lý vượt quá dung lượng EPC, hệ điều hành host sẽ phải thực hiện cơ chế hoán trang (swapping) vùng nhớ mã hóa ra ngoài RAM thường (gọi là *EPC paging overhead*). Thao tác này cực kỳ tốn chi phí và làm giảm hiệu năng hệ thống lên tới hàng trăm lần. Giám sát áp lực EPC giúp hệ thống chủ động chuyển sang Software mode trước khi thảm họa hoán trang xảy ra.

---

### Q4: Cơ chế Hysteresis (vùng trễ) trong bộ điều khiển tự thích nghi dùng để làm gì? Nếu không có nó thì sao?
* **Trả lời**:
  * Cơ chế thiết lập hai ngưỡng chuyển đổi khác nhau: ngưỡng hạ cấp (fallback) là $\ge 80\%$ và ngưỡng khôi phục (restore) là $\le 60\%$.
  * **Nếu không có vùng trễ** (chỉ dùng duy nhất một ngưỡng 80%): Khi áp lực bộ nhớ dao động liên tục quanh mức 80% (ví dụ: 79% $\leftrightarrow$ 81%), hệ thống sẽ liên tục chuyển đổi qua lại giữa TEE và Software (hiện tượng **flapping** hay dao động). Việc chuyển đổi liên tục này gây tốn hao tài nguyên hệ thống và làm mất ổn định thời gian phản hồi của dịch vụ. Vùng trễ 20% đóng vai trò làm vùng đệm giúp ổn định trạng thái hệ thống.

---

### Q5: EPC Prober trong đồ án phát hiện sự bão hòa bộ nhớ Enclave bằng cách nào?
* **Trả lời**: 
  * EPC Prober khởi chạy một luồng nền định kỳ mỗi 5 giây gửi một truy vấn siêu nhỏ (`COUNT` đơn giản) vào Enclave Pool để đo thời gian phản hồi (latency).
  * Trong 3 lần chạy đầu tiên, hệ thống ghi nhận latency và khóa lại làm **Baseline Latency** (đường cơ sở khi Enclave rảnh rỗi).
  * Nếu latency của phép thử tăng gấp đôi so với Baseline (latency ratio $\ge 2.0$), prober báo trạng thái bão hòa (Saturated).
  * Đồng thời, hệ thống kết hợp đọc RSS thật của tiến trình Pool qua `/proc/<pid>/status` và `/proc/<pid>/smaps` (thông qua module `resource_monitor.py`) để xác định chính xác phần trăm bộ nhớ đang sử dụng.

---

### Q6: Tại sao toán tử `COUNT` lại thuộc Software Mode, còn `COUNT DISTINCT` lại thuộc TEE Mode?
* **Trả lời**:
  * **`COUNT`**: Ta chỉ cần đếm số lượng bản ghi khớp điều kiện lọc. Vì các bộ lọc (`ma_benh`, `tuoi`) đều được Router mã hóa tương ứng bằng DTE/ORE và gửi trực tiếp lên MongoDB, MongoDB chỉ việc dùng index để đếm số lượng bản ghi trả về mà hoàn toàn không cần giải mã dữ liệu.
  * **`COUNT DISTINCT`**: Phép toán này yêu cầu đếm các giá trị phân biệt (loại bỏ trùng lặp). Nếu thực hiện trên bản mã ngẫu nhiên (AES-GCM), hai bản ghi có cùng số tiền viện phí sẽ cho ra hai bản mã hoàn toàn khác nhau, khiến kết quả đếm bị sai. Do đó, bắt buộc phải gom các bản mã chuyển vào Enclave để giải mã về dạng số plaintext rồi mới dùng cấu trúc dữ liệu loại bỏ trùng lặp.

---

### Q7: Tại sao toán tử `SUM` và `AVG` mặc định phải chạy trong TEE Mode?
* **Trả lời**: Phép toán gộp `SUM` (tổng) và `AVG` (trung bình) yêu cầu thực hiện các phép tính số học cộng và chia trực tiếp trên giá trị số. Bản mã y tế thông thường không hỗ trợ tính toán số học trực tiếp. Do đó, ta buộc phải giải mã dữ liệu về dạng plaintext mới tính toán được. Để bảo vệ dữ liệu khỏi kẻ tấn công đọc trộm RAM, việc giải mã này bắt buộc phải diễn ra bên trong phân vùng nhớ mã hóa phần cứng của TEE.

---

### Q8: Cost Model tính toán chi phí $C_{soft}$ và $C_{TEE}$ như thế nào? Ý nghĩa của nó trong hệ thống?
* **Trả lời**:
  * **Chi phí Software ($C_{soft}$)**: $C_{soft} = T_{build\_filter} + N \times T_{scan\_record}$. Trong đó $T_{build\_filter}$ là thời gian mã hóa DTE/ORE trên host lấy từ số liệu micro-benchmark thật (`c_soft_metrics.json`), và $T_{scan\_record}$ là thời gian quét chỉ mục trên MongoDB.
  * **Chi phí TEE ($C_{TEE}$)**: $C_{TEE} = T_{ecall\_switch} + T_{TEE\_query}(N)$. Trong đó $T_{ecall\_switch}$ là chi phí chuyển ngữ cảnh (context switch) vào enclave và $T_{TEE\_query}(N)$ là thời gian giải mã AES-GCM + tính toán của DuckDB in-memory nội suy từ RSS profile thực tế của Lan.
  * **Ý nghĩa**: Trong hệ thống hiện tại, Cost Model đóng vai trò hiển thị độ trễ overhead giữa hai chế độ phục vụ mục đích giám sát và tối ưu hóa hệ thống, đồng thời cung cấp căn cứ để nhà quản trị đưa ra quyết định định tuyến.

---

## 🔐 PHẦN 2: LỚP MẬT MÃ HỌC CHỌN LỌC (CRYPTOGRAPHIC LAYER)

### Q9: Tại sao bạn chọn thuật toán AES-SIV cho DTE (Deterministic Encryption) mà không dùng AES-ECB hay AES-CBC với IV cố định?
> [!IMPORTANT]
> Đây là câu hỏi mật mã học chuyên sâu mà các giảng viên rất thích hỏi để đánh giá mức độ hiểu biết thuật toán của sinh viên.
* **Trả lời**: 
  * Cả ba thuật toán đều là tất định (deterministic) - giúp cùng một giá trị gốc cho ra cùng một bản mã để tìm kiếm bằng. Tuy nhiên:
  * **AES-ECB** cực kỳ rò rỉ thông tin vì nó mã hóa từng khối 128-bit độc lập, kẻ tấn công có thể nhìn thấy các khối trùng nhau trong một chuỗi dài.
  * **AES-CBC với IV cố định** bị rò rỉ tiền tố (prefix leakage) nếu hai chuỗi dữ liệu có đoạn đầu giống nhau, đồng thời dễ bị tấn công CPA (Chosen Plaintext Attack).
  * **AES-SIV (Synthetic IV - RFC 5297)** là chuẩn mã hóa tất định an toàn nhất hiện nay. Nó sử dụng cơ chế băm toàn bộ nội dung bản tin (kèm theo Associated Data) để sinh ra một vector khởi tạo IV nhân tạo. Nó đạt cấp độ bảo mật **MRAE (Misuse-Resistant Authenticated Encryption)**. Dù khóa bị lạm dụng hay IV bị trùng, nó cũng không bao giờ làm rò rỉ bất kỳ thông tin nào ngoài mối quan hệ bằng nhau giữa hai bản tin gốc.

---

### Q10: Associated Data (Dữ liệu liên kết) trong thuật toán AES-SIV được bạn cấu hình thế nào và nhằm mục đích gì?
* **Trả lời**: 
  * Trong mã nguồn (file `software_executor.py` và `ecall_pool.py`), Associated Data được truyền vào dưới dạng nhãn định danh cột, ví dụ: `b"field:ma_benh"` cho trường mã bệnh, hoặc `b"field:khoa_phong"` cho trường khoa phòng.
  * **Mục đích**: Chống lại tấn công hoán đổi cột dữ liệu (Column-Mix Attack). Nếu kẻ tấn công sao chép một chuỗi bản mã DTE của cột `ma_benh` rồi dán đè vào cột `khoa_phong` trên cơ sở dữ liệu, khi Router giải mã cột `khoa_phong` sử dụng Associated Data là `b"field:khoa_phong"`, thuật toán AES-SIV sẽ phát hiện ra sự sai lệch và báo lỗi giải mã ngay lập tức, ngăn chặn việc làm giả dữ liệu.

---

### Q11: Bản chất của OPE/ORE (Order-Preserving/Revealing Encryption) là gì? Điểm yếu bảo mật lớn nhất của nó là gì?
* **Trả lời**:
  * **Bản chất**: Boldyreva OPE ánh xạ tập số tự nhiên sang một tập số lớn hơn nhưng bảo toàn thứ tự toán học ($x < y \Rightarrow OPE(x) < OPE(y)$). Nhờ đó, MongoDB có thể xây dựng các chỉ mục cây B-Tree trực tiếp trên bản mã để thực hiện các phép quét khoảng ($>, <, \ge, \le$) cực kỳ nhanh chóng.
  * **Điểm yếu lớn nhất**: Rò rỉ phân bố thứ tự (Order/Distribution Leakage). Vì bản mã giữ nguyên thứ tự của dữ liệu gốc, kẻ tấn công chỉ cần quan sát bản mã là biết được thứ tự tương đối của tất cả bệnh nhân. Nếu có thêm một cơ sở dữ liệu plaintext tham chiếu (ví dụ: phân bố tuổi của dân cư địa phương), kẻ tấn công có thể dùng tấn công ánh xạ thứ hạng (Rank-Linkage Attack) để khôi phục chính xác tuổi của bệnh nhân mà không cần biết khóa giải mã.

---

### Q12: Hệ thống của bạn làm gì để đánh giá và giảm thiểu rủi ro rò rỉ của ORE?
* **Trả lời**:
  * **Đánh giá**: Đồ án có công cụ đánh giá an ninh chuyên biệt tại [tests/attack_ore.py](file:///home/beserk/crypto/repo/Enc2Health/tests/attack_ore.py) để mô phỏng khả năng tấn công khôi phục tuổi thực tế của kẻ tấn công (MAE chỉ khoảng 0.67 tuổi đối với kịch bản có dữ liệu neo).
  * **Giảm thiểu**: Hệ thống giảm thiểu rò rỉ bằng cách hạn chế tối đa sử dụng ORE: chỉ dùng ORE cho trường số tuổi (`tuoi_enc`). Đồng thời, các truy vấn thống kê nhạy cảm đều được giữ cứng trong Enclave, ORE chỉ dùng lọc thô ở Software mode. Ở môi trường sản xuất, có thể nâng cấp lên cấu trúc ORAM (Oblivious RAM) hoặc áp dụng kỹ thuật Bucketization (chia cụm khoảng) để che giấu phân bố tần suất.

---

### Q13: Tại sao lại chọn mã hóa bất đối xứng ECC ECIES P-384 cho thông tin định danh cá nhân (PII) thay vì RSA-4096?
* **Trả lời**:
  * **Độ dài khóa và bản mã**: Khóa ECC P-384 chỉ dài 384-bit nhưng cung cấp mức độ bảo mật tương đương khóa RSA 7680-bit. Điều này giúp kích thước bản mã lưu trữ trên MongoDB nhỏ gọn hơn rất nhiều.
  * **Tốc độ tính toán trong TEE**: Phép toán giải mã RSA cực kỳ tốn năng lực xử lý của CPU. ECC sử dụng các phép toán trên đường cong elliptic có tốc độ giải mã nhanh hơn hàng chục lần so với RSA trên cùng một mức độ bảo mật. Điều này đặc biệt quan trọng trong môi trường SGX Enclave vốn bị giới hạn về hiệu năng.
  * **Bảo mật bất đối xứng từ nguồn**: Client có thể tự sinh dữ liệu và mã hóa PII bằng khóa công khai (Public Key) của từng khoa mà không cần giữ khóa giải mã (đảm bảo nguyên tắc đặc quyền tối thiểu).

---

### Q14: Tại sao trong hàm giải mã PII (`_execute_pii_query` trong `ecall_pool.py`), bạn lại ghi đè vùng nhớ khóa bằng byte rỗng (`\x00`) sau khi giải mã xong?
* **Trả lời**: 
  * Đây là kỹ thuật **Zero-fill** dùng để bảo vệ an toàn cho khóa mật mã trong RAM.
  * Trong các ngôn ngữ có bộ dọn rác tự động (Garbage Collector) như Python, khi một biến chứa khóa hết phạm vi sử dụng, vùng nhớ chứa khóa đó vẫn nằm lại trên RAM cho đến khi bộ dọn rác thực sự quét qua và giải phóng. Kẻ tấn công có quyền root trên host có thể thực hiện kỹ thuật Dump RAM để trích xuất khóa.
  * Việc chủ động ghi đè mảng byte rỗng (`b"\x00" * len(private_key)`) trực tiếp lên vùng nhớ vật lý của biến khóa đảm bảo khóa biến mất khỏi RAM ngay lập tức sau khi hoàn thành tác vụ giải mã.

---

### Q15: KMS Envelope Encryption (Mã hóa phong bì) hoạt động như thế nào với HashiCorp Vault trong dự án của bạn?
* **Trả lời**:
  * Khóa mật mã trực tiếp dùng để mã hóa và giải mã dữ liệu y tế (như `gcm_dek`) được gọi là **DEK** (Data Encryption Key).
  * Khóa DEK này không được lưu dưới dạng plaintext trên đĩa. Nó được mã hóa (bọc - wrap) bởi khóa Master Key (**MK**) nằm an toàn bên trong HashiCorp Vault.
  * Khi Enclave Pool khởi chạy, nó sử dụng cơ chế AppRole để xác thực với Vault, lấy token tạm thời và gửi yêu cầu unwrap khóa DEK. Vault sẽ giải mã và trả khóa DEK plaintext về vùng RAM được bảo vệ của Enclave.
  * **Lợi ích**: Giúp quản lý khóa tập trung, hỗ trợ xoay vòng khóa (key rotation) dễ dàng mà không cần phải mã hóa lại toàn bộ cơ sở dữ liệu y tế.

---

### Q16: Cơ chế tìm kiếm từ khóa lâm sàng SSE (Searchable Symmetric Encryption) hoạt động ra sao? Rò rỉ thông tin của nó gồm những gì?
* **Trả lời**:
  * **Cơ chế**: Khi gieo dữ liệu, các từ khóa trong hồ sơ bệnh án được trích xuất. Với mỗi từ khóa $W$, hệ thống tính toán $Token = HMAC(K_{sse}, W)$. Sau đó, lưu $Token$ ánh xạ tới danh sách các patient ID đã mã hóa AES-GCM trong collection `sse_index`. Khi tìm kiếm từ khóa $W$, client tính toán $Token$ tương ứng gửi lên, MongoDB chỉ việc tìm kiếm bằng chính xác trên trường `token` để trả về postings mà không biết từ khóa gốc là gì.
  * **Rò rỉ của SSE**:
    1. *Search Pattern*: Kẻ tấn công biết được khi nào người dùng tìm kiếm lại cùng một từ khóa (vì HMAC token trùng nhau).
    2. *Access Pattern*: Kẻ tấn công biết được các bệnh nhân nào có chung triệu chứng (vì posting list trả về các ID giống nhau).
    3. *Volume Leakage*: Kẻ tấn công biết được số lượng bệnh nhân mắc triệu chứng đó (trường `n_records` lộ trên chỉ mục).

---

## 👥 PHẦN 3: PHÂN QUYỀN & BẢO MẬT (ABAC, RBAC & THREAT MODEL)

### Q17: Điểm khác biệt lớn nhất giữa mô hình phân quyền RBAC và ABAC trong dự án của bạn là gì?
* **Trả lời**:
  * **RBAC (Role-Based Access Control)**: Phân quyền dựa trên **Vai trò** của chủ thể. Ví dụ: `doctor` được phép thống kê trung bình viện phí (`avg_vien_phi`) nhưng cấm thống kê tổng viện phí (`sum_vien_phi`) (Router trả về lỗi 403). `researcher` bị ẩn hoàn toàn cột viện phí và mã bệnh.
  * **ABAC (Attribute-Based Access Control)**: Phân quyền dựa trên **Thuộc tính** của chủ thể và đối tượng. Cụ thể ở đây là thuộc tính khoa phòng (`dept`). Bác sĩ khoa Tim mạch (thuộc tính `dept="Tim_mach"` trong JWT) chỉ được xem bệnh nhân thuộc khoa Tim mạch. ABAC hoạt động bằng cách Router tự động tiêm thêm bộ lọc `{khoa_phong: "Tim_mach"}` vào câu lệnh truy vấn MongoDB, ngăn chặn bác sĩ xem trái phép dữ liệu của khoa khác.

---

### Q18: Tại sao bạn lại tiêm bộ lọc ABAC ở phía Router mà không tin cậy bộ lọc gửi lên từ Client?
* **Trả lời**: Nguyên tắc bảo mật cơ bản là **Không bao giờ tin cậy phía Client** (Zero Trust Client). Nếu cho phép Client tự gửi bộ lọc khoa phòng, một bác sĩ độc hại hoặc một ứng dụng client bị hack có thể sửa đổi filter từ `Tim_mach` thành `Noi` hoặc xóa bỏ filter để truy cập toàn bộ dữ liệu bệnh viện. Việc tiêm filter ở phía Router dựa trên thông tin đã được xác thực trong chữ ký JWT đảm bảo tính toàn vẹn của chính sách ABAC.

---

### Q19: Tại sao JWT được sử dụng để truyền tải thông tin phân quyền? Bạn bảo vệ tính toàn vẹn của nó bằng cách nào?
* **Trả lời**: JWT (JSON Web Token) cho phép đóng gói thông tin vai trò (`role`) và khoa phòng (`dept`) của người dùng vào một chuỗi ký tự. Để bảo vệ tính toàn vẹn, JWT được ký bằng thuật toán đối xứng **HS256** sử dụng khóa bí mật `AUTH_JWT_SECRET` chỉ Router và Enclave biết. Nếu kẻ tấn công tự ý thay đổi vai trò từ `researcher` lên `admin` trong payload JWT, chữ ký signature sẽ không khớp và Router sẽ lập tức từ chối yêu cầu (trả về lỗi 401).

---

### Q20: Tại sao vai trò `admin_staff` (nhân viên hành chính) trong đồ án lại được xem PII plaintext nhưng bị ẩn thông tin bệnh án, còn `doctor` (bác sĩ) lại được xem cả hai?
* **Trả lời**: Đây là nguyên tắc **Phân chia nhiệm vụ (Separation of Duties)** và **Quyền truy cập tối thiểu**:
  * Nhân viên hành chính chỉ làm nhiệm vụ thanh toán hoặc tiếp đón, họ cần xác thực thông tin cá nhân (Họ tên, CCCD, địa chỉ) để làm thủ tục nhưng không cần và không được phép biết bệnh án chi tiết hay phác đồ điều trị của bệnh nhân.
  * Bác sĩ điều trị cần biết cả thông tin cá nhân lẫn bệnh án chi tiết để đưa ra quyết định y khoa chính xác.

---

### Q21: Mô hình đe dọa (Threat Model) của hệ thống này giả định những gì? Kẻ tấn công có thể làm gì?
* **Trả lời**: Hệ thống giả định mô hình **Honest-but-curious Cloud Provider / Administrator**:
  * Nhà cung cấp đám mây vận hành đúng hệ thống (không tự ý xóa dữ liệu hoặc thay đổi logic xử lý).
  * Tuy nhiên, họ tò mò và có quyền quản trị tối cao trên hệ điều hành của máy chủ ảo (Hypervisor/OS level). Họ có thể dump bộ nhớ RAM của database MongoDB để đọc trộm dữ liệu, hoặc đọc trực tiếp file dữ liệu trên đĩa cứng.
  * Hệ thống của ta bảo vệ an toàn cho dữ liệu PHI bằng cách mã hóa dữ liệu từ phía Client trước khi đẩy lên đám mây, và chỉ giải mã tính toán trong vùng nhớ được bảo vệ phần cứng của CPU (Enclave).

---

### Q22: Ở chế độ Software Fallback (kịch bản 2B), Router nạp khóa `gcm_dek.key` vào RAM thường của host để tính toán. Điều này có vi phạm mô hình đe dọa không? Bạn giải thích thế nào với giảng viên?
> [!WARNING]
> Đây là câu hỏi "bẫy" cực kỳ nguy hiểm nhằm kiểm tra xem sinh viên có trung thực khoa học và hiểu rõ điểm yếu của hệ thống mình hay không.
* **Trả lời**: 
  * **Thừa nhận**: Có, việc này tạo ra một khe hở bảo mật tạm thời (security trade-off). Khi rơi vào Fallback mode, khóa giải mã và dữ liệu plaintext sẽ xuất hiện trên RAM thường của host, tức là không còn được bảo vệ trước một Cloud admin có khả năng đọc trộm RAM.
  * **Giải thích**: Đây là sự đánh đổi có chủ ý giữa **Tính an toàn (Security)** và **Tính sẵn sàng (Availability)**. Khi Enclave bị quá tải, nếu ta khăng khăng giữ an toàn tuyệt đối bằng cách chặn truy vấn, hệ thống bệnh viện sẽ bị tê liệt (từ chối dịch vụ). Fallback được kích hoạt để cứu vãn tính sẵn sàng của dịch vụ y tế.
  * **Giải pháp khắc phục nâng cao**: Để khắc phục triệt để rò rỉ này trong tương lai, ta có thể thay thế việc giải mã trên host bằng thuật toán **Mã hóa đồng cấu (Homomorphic Encryption)**. Khi đó máy chủ host có thể tính toán trực tiếp trên bản mã mà hoàn toàn không cần giải mã dữ liệu hay giữ khóa giải mã.

---

## ⚙️ PHẦN 4: TRIỂN KHAI & HIỆU NĂNG (IMPLEMENTATION & EVALUATION)

### Q23: Sự khác biệt giữa chế độ Gramine Simulation (mô phỏng) và Hardware Mode (thực tế) là gì?
* **Trả lời**:
  * **Gramine Simulation**: Enclave được chạy dưới dạng một tiến trình bình thường trên hệ điều hành host. Vùng nhớ EPC chỉ là giả lập trên RAM thường và không được mã hóa phần cứng. Chế độ này dùng để phát triển, kiểm thử logic và đo đạc hiệu năng mô phỏng mà không cần phần cứng Intel SGX thật.
  * **Hardware Mode**: Yêu cầu CPU Intel hỗ trợ công nghệ SGX2. Gramine sẽ nạp ứng dụng vào vùng nhớ bảo mật được mã hóa bằng phần cứng bởi bộ điều khiển bộ nhớ của CPU. RAM của enclave hoàn toàn không thể đọc được bởi hệ điều hành host hay quản trị viên ảo hóa.

---

### Q24: Remote Attestation (Chứng thực từ xa) là gì? Trong đồ án bạn đã hiện thực nó ra sao?
* **Trả lời**:
  * **Khái niệm**: Remote Attestation là cơ chế giúp một thực thể bên ngoài (ví dụ: Router) xác minh xem Enclave Pool có thực sự chạy đúng cấu hình mã nguồn an toàn bên trong CPU SGX thật hay không, ngăn chặn kẻ tấn công giả mạo dịch vụ Enclave.
  * **Hiện thực trong đồ án**: Do chạy mô phỏng, hệ thống triển khai cơ chế **Signed Simulation Attestation** tại endpoint `/attest` của Pool. Pool sẽ ký HMAC-SHA256 lên một tài liệu chứa thông số MRENCLAVE (mã băm của enclave code), boot ID, timestamp và gửi về cho Router. Router sử dụng khóa bí mật dùng chung để kiểm tra chữ ký và tính tươi mới (freshness) của timestamp nhằm chống lại tấn công phát lại (Replay Attack).

---

### Q25: Tại sao bạn lại chọn cơ sở dữ liệu in-memory DuckDB chạy bên trong Enclave thay vì SQLite hay tự viết code tính toán bằng Python?
* **Trả lời**:
  * **SQLite**: Là cơ sở dữ liệu hướng dòng (Row-oriented). Khi chạy các truy vấn phân tích thống kê lớn như `AVG`/`SUM`, SQLite phải quét qua toàn bộ các cột khác trong hàng, gây chậm hiệu năng.
  * **Tự viết bằng Python**: Code Python thuần xử lý mảng rất chậm và tiêu tốn nhiều bộ nhớ RAM bên trong Enclave (làm tăng nguy cơ quá tải EPC).
  * **DuckDB**: Là cơ sở dữ liệu hướng cột (Column-oriented) được tối ưu hóa cực mạnh cho phân tích dữ liệu lớn (OLAP). DuckDB xử lý vector hóa (vectorized execution), cho phép giải mã và tính toán gộp hàng trăm nghìn bản ghi chỉ trong vài mili-giây với lượng RAM tiêu thụ cực kỳ thấp, giảm thiểu áp lực lên bộ nhớ EPC của Enclave.

---

### Q26: Bạn nói hệ thống hỗ trợ mTLS (Mutual TLS). Nó bảo vệ luồng dữ liệu nào và tại sao cần thiết?
* **Trả lời**:
  * mTLS thiết lập xác thực hai chiều giữa **Query Router** và **ECALL Task Pool**. Cả hai bên đều phải xuất trình chứng chỉ số hợp lệ được ký bởi một CA nội bộ chung.
  * **Lý do cần thiết**: Ngăn chặn tấn công giả mạo (Impersonation) và nghe lén (Eavesdropping) trên đường truyền nội bộ. Ngay cả khi kẻ tấn công nằm trong mạng nội bộ của đám mây, họ cũng không thể gửi yêu cầu giải mã PII giả mạo tới Enclave, hoặc bắt trộm luồng dữ liệu bản giải mã trả về Router.

---

### Q27: Kết quả đánh giá hiệu năng (Evaluation) của bạn cho thấy overhead của TEE so với Software là bao nhiêu? Độ trễ đó có chấp nhận được không?
* **Trả lời**:
  * Theo kết quả benchmark thực tế đo được (T11): thời gian chạy trung bình của Software (phép `COUNT`) là **5.7ms**, còn TEE (phép `SUM`/`AVG` viện phí) là khoảng **10.6ms - 11.0ms**.
  * Độ trễ của TEE cao gấp khoảng **2 lần** so với Software.
  * **Đánh giá**: Sự chênh lệch này là hoàn toàn chấp nhận được và cực kỳ tối ưu trong lĩnh vực y tế. Việc đánh đổi thêm khoảng **5ms** độ trễ để đổi lấy sự bảo mật tuyệt đối cho thông tin bệnh án nhạy cảm của bệnh nhân là một sự đánh đổi vô cùng xứng đáng.

---

### Q28: Khi chạy với số lượng Client đồng thời lớn (Concurrency), hệ thống của bạn gặp nút thắt cổ chai (bottleneck) ở đâu và giải quyết thế nào?
* **Trả lời**:
  * **Nút thắt cổ chai**: Nằm ở Thread Pool của Enclave Pool khi phải thực hiện giải mã hàng loạt bản mã AES-GCM/ECC đồng thời. Giải mã là tác vụ cực kỳ ngốn CPU.
  * **Giải pháp**:
    1. Enclave Pool sử dụng thư viện mã hóa được tăng tốc phần cứng thông qua tập lệnh **AES-NI** của CPU.
    2. Cấu hình ThreadPoolExecutor với **8 workers** chuyên trách xử lý song song các tác vụ ecall, giúp hệ thống chịu tải tốt lên tới 50 concurrent clients mà tỷ lệ lỗi (error rate) duy trì ở mức **0%**.

---

### Q29: Tại sao cơ sở dữ liệu MongoDB trên ciphertext của bạn vẫn giữ được tốc độ truy vấn nhanh mà không bị quét toàn bộ bảng (collscan)?
* **Trả lời**: Khi gieo dữ liệu bằng [generate_ehr.py](file:///home/beserk/crypto/repo/Enc2Health/crypto/data/generate_ehr.py), hệ thống đã chủ động tạo các chỉ mục Single Field Index trên các trường mã hóa tất định và bảo toàn thứ tự: `ma_benh_enc`, `khoa_phong_enc`, `cmnd_dte`, và `tuoi_enc`. Do bản mã DTE là tất định và ORE bảo toàn thứ tự, MongoDB có thể tận dụng cấu trúc chỉ mục cây B-Tree có sẵn để tìm kiếm và quét khoảng với độ phức tạp $O(\log N)$ cực nhanh thay vì phải duyệt tuần tự từng document.

---

### Q30: Hạn chế lớn nhất hiện tại của hệ thống của bạn là gì? Định hướng phát triển tiếp theo nếu có thêm thời gian là gì?
* **Trả lời**:
  * **Hạn chế**:
    1. Hệ thống vẫn đang chạy giả lập Gramine Simulation chứ chưa chạy trên phần cứng Intel SGX thật.
    2. Software Fallback vẫn giải mã bằng khóa trên RAM thường của host, tạo ra rò rỉ bộ nhớ.
    3. SSE và ORE vẫn rò rỉ phân bố thứ tự/tần suất đối với các cuộc tấn công nâng cao.
  * **Định hướng phát triển**:
    1. Triển khai hệ thống lên đám mây hỗ trợ Intel SGX thật (như Azure DCsv3) và tích hợp chứng thực RA-TLS.
    2. Áp dụng kỹ thuật mã hóa đồng cấu Paillier cho cơ chế Fallback để tính toán trực tiếp trên bản mã mà không cần giữ khóa giải mã trên host.
    3. Tích hợp ORAM (Oblivious RAM) và chèn nhiễu dummy records để giảm thiểu access pattern leakage cho SSE và ORE.
