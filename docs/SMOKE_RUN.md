# Hướng dẫn kiểm tra local (mTLS) & tổng hợp thay đổi

Tài liệu này hướng dẫn cách chạy kiểm tra smoke cục bộ (Docker hoặc không Docker) và cách xác thực handshake mTLS giữa Query Router và ECALL Task Pool. Phần cuối liệt kê các thay đổi tôi đã thực hiện so với nội dung trong `PROJECT.md` sau khi pull code.

Yêu cầu trước khi chạy
- Python 3.11+ (để tạo virtualenv và chạy dịch vụ)
- openssl (để sinh chứng chỉ dev)
- Docker + docker-compose (nếu muốn dùng flow containerized)

Lệnh nhanh
- Containerized (yêu cầu Docker): `make smoke`
- Local (không Docker, bật mTLS): `REGEN_CERTS=1 KEEP_ALIVE=1 make smoke-local`
  - `REGEN_CERTS=1`: ép sinh lại chứng chỉ server/client/CA với SAN
  - `KEEP_ALIVE=1`: giữ các tiến trình chạy để bạn có thể kiểm tra thủ công

Các file liên quan
- `scripts/smoke_test.sh` — helper chạy compose và kiểm tra nhanh
- `scripts/smoke_test_local.sh` — chạy local không Docker, tự sinh certs (SAN), tạo `.venv`, seed (nếu có mongod), start ECALL pool (TLS) và Router
- `Makefile` — có các target `smoke` và `smoke-local`

Quy trình kiểm tra mTLS cục bộ (bước ngắn)
1. Dọn tiến trình cũ (nếu có):
```bash
pkill -f ecall_pool.py || true
pkill -f 'uvicorn router.main' || true
```
2. Regen certs và khởi stack local (giữ processes):
```bash
REGEN_CERTS=1 KEEP_ALIVE=1 make smoke-local
```
3. Kiểm tra handshake và health endpoint của ECALL pool:
```bash
curl -v --cacert certs/ca.crt --cert certs/client.crt --key certs/client.key https://127.0.0.1:9091/health
```
   Kết quả mong đợi: TLS handshake hoàn tất và trả HTTP 200. Nếu gặp lỗi `subject name`/`CN mismatch` thì kiểm tra `certs/server.crt` có chứa SAN `IP:127.0.0.1` và `DNS:ecall-pool` hay không.
4. Gửi truy vấn TEE (Router → ECALL pool):
```bash
# Sinh JWT (admin) bằng venv
./.venv/bin/python -c "from common.auth import generate_test_jwt; print(generate_test_jwt('manual', role='admin'))"

# Gọi Router (ví dụ tính tổng viện phí)
curl -v -X POST http://127.0.0.1:8000/query \
  -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" \
  -d '{"query_type":"sum_vien_phi","filters":{}}'
```

5. Kiểm tra snapshot metrics realtime (RSS/EPC, trạng thái saturated):
```bash
curl http://127.0.0.1:8000/metrics
```
Nếu endpoint trả JSON có `probe.resource.rss_mb` và `probe.metrics_path`, nghĩa là đường đo resource monitor đã chạy.

Nếu Router forward thành công sang ECALL pool và pool trả kết quả, flow mTLS + TEE path được xác thực.

Những điều script `scripts/smoke_test_local.sh` đã làm tự động
- Tạo `./certs` (CA + server + client) nếu chưa có, hoặc regen khi `REGEN_CERTS=1`.
- Sinh server cert kèm file cấu hình ext (`server_ext.cnf`) chứa SAN: `localhost`, `ecall-pool`, `127.0.0.1`.
- Tạo virtualenv `./.venv` và cài `crypto/requirements.txt` vào đó.
- Chọn chế độ dữ liệu cho ECALL pool bằng `T8_POOL_DATA_MODE`:
  - `mongo`: bắt buộc đọc dữ liệu thật từ MongoDB, fail-fast nếu không kết nối được.
  - `mock`: chỉ dùng cho local cô lập, script sẽ in cảnh báo compliance.
  - `auto`: ưu tiên Mongo nếu có, không có thì dùng mock.
- Nếu có `mongod` cục bộ thì seed dữ liệu; nếu không, ECALL pool chạy ở chế độ mock-data (DuckDB simulation).
- Khởi ECALL pool với biến môi trường TLS (`T8_SSL_CERT`, `T8_SSL_KEY`, `T8_SSL_CA`) để uvicorn chạy https.
- Khởi Router (uvicorn) và thiết lập env `ROUTER_CLIENT_CERT`, `ROUTER_CLIENT_KEY`, `T8_SSL_CA` để Router gọi ECALL pool qua mTLS.
- Router expose thêm `GET /metrics` để trả snapshot thời gian thực gồm RSS/EPC, trạng thái saturated và đường dẫn metrics file (`/tmp/enc2health_epc_metrics.json` mặc định).

Ghi chú về lỗi thường gặp
- Lỗi `certificate subject name '...' does not match target host name '127.0.0.1'`: do server cert thiếu SAN IP. Regenerate cert (REGEN_CERTS=1) hoặc tạo SAN đúng.
- Router báo `EcallClient` không kết nối: kiểm tra biến môi trường `ROUTER_CLIENT_CERT`, `ROUTER_CLIENT_KEY`, `T8_SSL_CA` và đảm bảo ECALL pool đang lắng nghe `https://127.0.0.1:9091`.

Kết quả xác minh mới nhất
- Đã chạy thành công: `REGEN_CERTS=1 KEEP_ALIVE=0 make smoke-local`.
- mTLS trực tiếp tới ECALL pool đã pass:
```bash
curl -v --cacert certs/ca.crt --cert certs/client.crt --key certs/client.key https://127.0.0.1:9091/health
```
Trả `HTTP/1.1 200 OK`, chứng chỉ hợp lệ, SAN match `127.0.0.1`.
- Luồng Router → ECALL (TEE path) đã pass trong smoke-local:
  - Router `/query` trả `200`
  - Response có `"mode":"tee"` và kết quả `sum_vien_phi`.

Root cause đã gặp và cách xử lý
- Triệu chứng cũ: Router trả `503`, log `EcallClient` báo `Server disconnected without sending a response`.
- Kết quả điều tra: `curl` và `requests` kết nối mTLS thành công, nhưng `httpx` trong môi trường local này bị `RemoteProtocolError`.
- Bản vá: trong `router/ecall_client.py`, giữ `httpx` là đường chính và thêm fallback sang `requests` cho cả `health_check()` và `query()` khi `httpx` lỗi.

Danh sách thay đổi tôi đã **sửa / thêm** so với `PROJECT.md`
(tóm tắt các file chính tôi đã chỉnh sửa hoặc thêm vào repo để hỗ trợ chạy local, mTLS và E2E smoke):

- Makefile: thêm target `smoke` và `smoke-local` để tiện khởi stack containerized hoặc cục bộ.
- docker-compose.yml: thêm service `certgen`, cấu hình `ecall-task-pool` và `query-router` để mount certs và bật mTLS trong compose (ECALL_POOL_URL=https://ecall-task-pool:9091).
- Dockerfile: cài openssl, bash để chạy certgen trong container.
- .dockerignore: giảm context build.
- scripts/generate_mtls_certs.sh: helper tạo CA/server/client (dùng trong compose flow).
- scripts/smoke_test.sh: helper `make smoke` (compose flow) — sinh certs, up stack, chờ health, sample query.
- scripts/smoke_test_local.sh: helper non-Docker chạy local, tự sinh certs (SAN), tạo venv, seed (nếu có mongod), start ECALL pool với TLS và Router, test sample query. (Đã mở rộng để support `REGEN_CERTS` và `KEEP_ALIVE`.)
- enclave/ecall_pool.py: hỗ trợ đọc env `T8_SSL_CERT`, `T8_SSL_KEY`, `T8_SSL_CA` và truyền vào `uvicorn.run(...)` để bật TLS cho service.
- router/ecall_client.py: hỗ trợ mTLS client (`verify=ca_bundle`, `cert=(client_cert, client_key)`) và thêm fallback `requests` khi `httpx` lỗi `RemoteProtocolError` trong local run.
- crypto/data/generate_ehr.py: làm idempotent/parametric theo env vars (MONGO_URI, counts, force recreate) để seeder phù hợp cho local runs.
- crypto/requirements.txt: được dùng để tạo virtualenv cho local run.
- tests/test_e2e.py: sửa/test lại để dùng JWT generaton và tương tác với Router + real ciphertext (nếu seed có sẵn).
- docs/SMOKE_RUN.md: thêm file hướng dẫn (bản tiếng Việt đang được cập nhật) — tóm tắt cách chạy và troubleshooting.

Tóm tắt ý nghĩa các thay đổi
- Mục tiêu chính: biến flow từ "mock-to-mock" thành một flow có thể chạy end-to-end trong môi trường dev (có hoặc không có Docker), và bảo đảm kênh nội bộ Router ↔ ECALL pool được bảo mật bằng mTLS.
- Tôi đã thêm cả script containerized (`scripts/smoke_test.sh`) và local (`scripts/smoke_test_local.sh`) để bạn lựa chọn môi trường thuận tiện.

Các bước tiếp theo khuyến nghị (ngắn):
1. Chạy `REGEN_CERTS=1 KEEP_ALIVE=1 make smoke-local` trên máy dev để regen certs và khởi các service cục bộ.
2. Chạy `curl -v --cacert certs/ca.crt --cert certs/client.crt --key certs/client.key https://127.0.0.1:9091/health` để xác nhận mTLS.
3. Gửi truy vấn `sum_vien_phi` bằng JWT role `admin` để kiểm tra Router forward sang TEE.

## Báo cáo chạy thực tế (đã xác minh)

Môi trường chạy
- Máy local Linux
- Chạy local non-Docker với mTLS

Chuỗi lệnh đã chạy

```bash
REGEN_CERTS=1 KEEP_ALIVE=1 make smoke-local
```

Kết quả chính từ log
- Sinh cert CA/server/client thành công.
- ECALL pool bật TLS thành công:
  - `Uvicorn running on https://127.0.0.1:9091`
- Router bật thành công:
  - `Uvicorn running on http://127.0.0.1:8000`
- Sample query trong smoke script thành công:
  - Router trả `HTTP 200`
  - JSON có `"mode":"tee"`, `"requested_mode":"tee"`
  - `result.result = 50805000.0`, `n_records = 10000`

Lệnh kiểm tra sau khi stack đã lên

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health
```

Kết quả: `200`

```bash
TOKEN=$(AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 ./.venv/bin/python - <<'PY'
from common.auth import generate_test_jwt
print(generate_test_jwt('manual', role='admin'))
PY
)
```

```bash
curl -v -X POST http://127.0.0.1:8000/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query_type":"sum_vien_phi","filters":{}}'
```

Kết quả: `HTTP/1.1 200 OK`, response chứa:
- `"mode":"tee"`
- `"reason":"Aggregation operator cần TEE enclave"`
- `"result":{"result":50805000.0,"n_records":10000,...}`

Kiểm tra mTLS trực tiếp tới ECALL pool

```bash
curl -v --cacert certs/ca.crt --cert certs/client.crt --key certs/client.key https://127.0.0.1:9091/health
```

Kết quả: `HTTP/1.1 200 OK`, TLS verify thành công, SAN match `127.0.0.1`.

Kết luận hiện tại
- Luồng local smoke mTLS hoạt động đúng.
- Router -> ECALL pool đi theo TEE path thành công.
- Bài test truy vấn tổng (`sum_vien_phi`) cho kết quả hợp lệ.
