# Kịch Bản Demo Enc2Health Cho Môn Mật Mã Học

Mục tiêu demo: cho thầy thấy hệ thống không chỉ "mã hóa rồi lưu DB", mà có thiết kế xử lý truy vấn trên dữ liệu mã hóa, phân quyền truy cập, đánh giá rò rỉ thông tin và đo trade-off hiệu năng.

## 1. Chuẩn Bị Trước Khi Demo

Chạy toàn bộ stack:

```bash
make up
```

Kiểm tra service:

```bash
docker compose ps
curl http://localhost:8000/health
```

Tạo token mẫu:

```bash
export AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890

AUTH_ROLE=admin python3 scripts/generate_jwt.py
AUTH_ROLE=doctor python3 scripts/generate_jwt.py
AUTH_ROLE=researcher python3 scripts/generate_jwt.py
```

Khi demo curl, gán token đang dùng:

```bash
export TOKEN="<paste JWT ở đây>"
```

## 2. Mở Đầu Kiến Trúc

Lời thoại gợi ý:

> Enc2Health là hệ thống truy vấn dữ liệu y tế mã hóa. Dữ liệu nhạy cảm được lưu ở dạng ciphertext trong MongoDB. Query Router phân loại truy vấn: toán tử phù hợp với xử lý trên ciphertext thì chạy software mode, còn phép tính nhạy cảm như SUM/AVG thì chuyển sang TEE/enclave simulation. Ngoài ra hệ thống có RBAC/ABAC, masking, adaptive fallback và đánh giá leakage.

Điểm cần chỉ trên sơ đồ:

- Client gửi query kèm JWT.
- Router kiểm tra quyền RBAC/ABAC.
- Cost Model và Query Router quyết định mode.
- MongoDB lưu ciphertext.
- ECALL Task Pool mô phỏng enclave để xử lý truy vấn nhạy cảm.
- Adaptive Controller theo dõi áp lực tài nguyên và fallback khi cần.

## 3. Demo Dữ Liệu Trong Database Là Ciphertext

Mục tiêu: chứng minh dữ liệu y tế không lưu plaintext trực tiếp.

Lệnh gợi ý:

```bash
docker compose exec mongodb mongosh enc2health --quiet --eval \
'db.patient_records.findOne({}, {_id:0, patient_id:1, vien_phi_enc:1, pii_enc:1, ma_benh:1, khoa_phong:1})'
```

Lời thoại:

> Ở database, các trường nhạy cảm như viện phí và PII không xuất hiện dưới dạng plaintext. Cloud database chỉ nhìn thấy ciphertext và một số metadata phục vụ lọc/query. Đây là phần bảo vệ data-at-rest.

Điểm cần nhấn mạnh:

- Nếu attacker đọc thẳng DB, họ không lấy được viện phí/PII plaintext.
- Nhưng mã hóa vẫn có leakage tùy scheme, nên đồ án có phần leakage analysis ở cuối.

## 4. Demo Query Software Mode

Mục tiêu: cho thấy một số toán tử có thể xử lý bằng software mode trên dữ liệu mã hóa/metadata.

Ví dụ `count`:

```bash
curl -s -X POST http://localhost:8000/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query_type":"count","filters":{}}' | python3 -m json.tool
```

Lời thoại:

> Với query dạng COUNT hoặc equality filter, hệ thống không nhất thiết phải đưa vào enclave. Router chọn software mode vì toán tử này phù hợp hơn về chi phí và không cần giải mã toàn bộ giá trị nhạy cảm.

Chỉ vào response:

- `mode`
- `requested_mode`
- `reason`
- `cost_estimate`
- `total_latency_ms`

## 5. Demo Query TEE Mode

Mục tiêu: cho thấy các phép tính trên giá trị nhạy cảm được đưa vào enclave simulation.

Ví dụ `sum_vien_phi`:

```bash
curl -s -X POST http://localhost:8000/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query_type":"sum_vien_phi","filters":{}}' | python3 -m json.tool
```

Ví dụ `avg_vien_phi` có filter:

```bash
curl -s -X POST http://localhost:8000/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query_type":"avg_vien_phi","filters":{"ma_benh":"I01"}}' | python3 -m json.tool
```

Lời thoại:

> SUM và AVG trên ciphertext thường không xử lý trực tiếp được nếu không dùng homomorphic encryption. Đồ án chọn hướng hybrid: giá trị mã hóa được đẩy sang enclave simulation để giải mã và tính toán trong vùng tin cậy, còn cloud database vẫn không thấy plaintext.

Điểm cần nhấn mạnh:

- Đây là trade-off giữa bảo mật và hiệu năng.
- TEE mode chậm hơn software mode nhưng phù hợp cho toán tử nhạy cảm.
- Response thể hiện `mode` và latency thực tế.

## 6. Demo RBAC/ABAC Và Masking

Mục tiêu: cùng một hệ thống nhưng quyền khác nhau cho kết quả khác nhau.

Tạo token researcher:

```bash
export TOKEN=$(AUTH_JWT_SECRET=$AUTH_JWT_SECRET AUTH_ROLE=researcher python3 scripts/generate_jwt.py)
```

Chạy query:

```bash
curl -s -X POST http://localhost:8000/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query_type":"avg_vien_phi","filters":{"ma_benh":"I01"}}' | python3 -m json.tool
```

Tạo token admin và chạy lại:

```bash
export TOKEN=$(AUTH_JWT_SECRET=$AUTH_JWT_SECRET AUTH_ROLE=admin python3 scripts/generate_jwt.py)

curl -s -X POST http://localhost:8000/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query_type":"avg_vien_phi","filters":{"ma_benh":"I01"}}' | python3 -m json.tool
```

Lời thoại:

> RBAC/ABAC không chỉ quyết định được gọi endpoint hay không, mà còn quyết định field nào bị mask. Researcher có thể xem thống kê phục vụ nghiên cứu, nhưng dữ liệu nhạy cảm hoặc định danh có thể bị ẩn.

Chỉ vào response:

- `masked_fields`
- `abac_scope`
- `result`

## 7. Demo Adaptive Fallback

Mục tiêu: thể hiện phần hybrid adaptive khi áp lực tài nguyên cao.

Xem trạng thái hiện tại:

```bash
curl -s http://localhost:8000/adaptive | python3 -m json.tool
```

Giả lập EPC pressure cao:

```bash
curl -s -X POST "http://localhost:8000/adaptive/simulate?pressure=0.9" | python3 -m json.tool
```

Chạy lại query TEE:

```bash
curl -s -X POST http://localhost:8000/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query_type":"avg_vien_phi","filters":{"ma_benh":"I01"}}' | python3 -m json.tool
```

Khôi phục trạng thái bình thường:

```bash
curl -s -X POST "http://localhost:8000/adaptive/simulate" | python3 -m json.tool
```

Lời thoại:

> Khi áp lực EPC/tài nguyên cao, hệ thống có thể fallback để giữ availability. Đây là lý do gọi kiến trúc là hybrid adaptive: không cố định một mode cho mọi query, mà chọn theo loại toán tử, chi phí và trạng thái runtime.

## 8. Demo SSE Keyword Search Và Leakage

Mục tiêu: cho thấy tìm kiếm keyword trên dữ liệu mã hóa và các leakage liên quan.

Ví dụ:

```bash
curl -s -X POST http://localhost:8000/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"keyword":"tim","filters":{},"limit":5}' | python3 -m json.tool
```

Lời thoại:

> Với Searchable Symmetric Encryption, server có thể hỗ trợ tìm keyword mà không cần lưu keyword plaintext. Tuy nhiên SSE vẫn có leakage: search pattern, volume pattern và access pattern. Đồ án không che giấu điểm yếu này mà đo và báo cáo rõ.

Chỉ vào response:

- `token`
- `count`
- `leakage.search_pattern`
- `leakage.volume`
- `leakage.access_pattern`

## 9. Demo Leakage Và Attack Evaluation

Mục tiêu: phần này rất quan trọng với môn mật mã học.

Chạy hoặc mở kết quả đã sinh:

```bash
python3 tests/leakage.py
python3 tests/attack_bipartite.py
python3 tests/leakage_sse.py
```

Nếu không muốn chạy live, mở các file kết quả:

```bash
cat leakage_results.json | python3 -m json.tool
cat attack_results.json | python3 -m json.tool
cat attack_sela_results.json | python3 -m json.tool
cat sse_leakage_results.json | python3 -m json.tool
```

Lời thoại:

> Trong mật mã ứng dụng, mã hóa không có nghĩa là không rò rỉ gì. DTE có thể rò rỉ equality pattern, ORE rò rỉ thứ tự/rank, SSE rò rỉ search pattern, volume và access pattern. Phần benchmark này dùng để định lượng leakage thay vì chỉ mô tả lý thuyết.

Điểm cần nhấn mạnh:

- DTE: cùng plaintext có thể cho pattern giống nhau nếu dùng encryption phục vụ equality.
- ORE: giữ được thứ tự nên có nguy cơ rank-linkage.
- SSE: lặp keyword sẽ lộ search pattern, số lượng kết quả lộ volume.
- Đây là đánh đổi có chủ đích để hỗ trợ truy vấn.

## 10. Demo Benchmark Hiệu Năng

Mục tiêu: chứng minh trade-off bảo mật và hiệu năng.

Chạy benchmark nếu còn thời gian:

```bash
python3 tests/benchmark.py
python3 tests/benchmark_concurrent.py
```

Hoặc mở biểu đồ/kết quả:

```bash
cat benchmark_results.json | python3 -m json.tool
cat concurrent_results.json | python3 -m json.tool
```

Lời thoại:

> Software mode nhanh hơn vì không cần qua enclave. TEE mode có overhead nhưng bảo vệ tốt hơn cho phép toán nhạy cảm. Kết quả benchmark cho thấy hệ thống đo được avg, p95, p99 và QPS để đánh giá trade-off.

## 11. Thứ Tự Demo 10 Phút

| Thời lượng | Nội dung |
|---|---|
| 1 phút | Giới thiệu kiến trúc |
| 1 phút | Show ciphertext trong MongoDB |
| 1 phút | Query `count` software mode |
| 1.5 phút | Query `sum_vien_phi` hoặc `avg_vien_phi` TEE mode |
| 1.5 phút | RBAC/ABAC và masking theo role |
| 1 phút | Adaptive fallback |
| 1.5 phút | SSE/leakage/attack evaluation |
| 1 phút | Benchmark hiệu năng |

## 12. Thông Điệp Chốt

Lời chốt gợi ý:

> Điểm chính của Enc2Health là thiết kế hybrid cho truy vấn dữ liệu y tế mã hóa. Hệ thống phân loại toán tử theo khả năng xử lý mật mã: query đơn giản chạy software mode, phép tính nhạy cảm chạy trong enclave simulation. Bên cạnh đó, hệ thống có RBAC/ABAC, masking, adaptive fallback, benchmark hiệu năng và đánh giá leakage. Vì vậy đồ án thể hiện cả mặt xây dựng hệ thống và mặt phân tích mật mã ứng dụng.

## 13. Câu Hỏi Thầy Có Thể Hỏi

**Vì sao không dùng homomorphic encryption cho SUM/AVG?**

Trả lời ngắn:

> Homomorphic encryption hỗ trợ tính toán trực tiếp trên ciphertext nhưng chi phí cao và phức tạp hơn nhiều. Trong phạm vi đồ án, nhóm chọn hướng hybrid: dùng encryption phù hợp cho lưu trữ/truy vấn đơn giản, còn phép toán nhạy cảm đưa vào TEE simulation để cân bằng hiệu năng và bảo mật.

**TEE simulation có phải SGX thật không?**

Trả lời ngắn:

> Đây là mô phỏng kiến trúc enclave/ECALL task pool, không phải production SGX thật. Mục tiêu là thể hiện luồng xử lý, boundary tin cậy và trade-off khi đưa phép tính nhạy cảm ra khỏi cloud database thường.

**Mã hóa rồi có còn rò rỉ không?**

Trả lời ngắn:

> Có. Tùy scheme mà vẫn có leakage. DTE có equality leakage, ORE có order leakage, SSE có search/volume/access leakage. Đồ án có đo leakage để thể hiện rõ đánh đổi này.

**Router có phải điểm tin cậy không?**

Trả lời ngắn:

> Có, Router là thành phần thực thi policy, xác thực JWT, routing và masking. Trong mô hình threat của đồ án, database/cloud storage là bên không tin cậy hơn; Router và enclave simulation thuộc phần kiểm soát của hệ thống.

**Nếu attacker có DB dump thì sao?**

Trả lời ngắn:

> Attacker thấy ciphertext và metadata, không thấy plaintext trực tiếp. Tuy nhiên họ vẫn có thể khai thác leakage pattern tùy scheme, nên hệ thống có phần đánh giá attack/leakage để định lượng rủi ro.
