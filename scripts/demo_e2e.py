#!/usr/bin/env python3
"""
Demo end-to-end Enc²Health — CÓ LỜI DẪN.

Chạy đúng truy vấn kinh điển của đề tài:
    "Tính AVG viện phí của bệnh nhân Tiểu đường (E11) TRÊN 60 tuổi"

Mục tiêu: cho thấy TRỰC QUAN ai nhìn thấy gì.
- Cloud provider (CSP) CHỈ thấy ciphertext (chuỗi vô nghĩa).
- Plaintext CHỈ xuất hiện ở bước "trong enclave".
- Bác sĩ nhận về 1 con số; researcher bị che (RBAC).

Chạy (trong env có pymongo/cryptography/pyope, MongoDB :27017 đã có data):
    python3 scripts/demo_e2e.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from router.query_router import QueryRouter, ExecutionMode
from router.rbac import RBACMiddleware
from router.cost_model import compare_costs
from router.software_executor import SoftwareExecutor
from crypto.crypto.gcm import AESGCMCipher

KEY_DIR = REPO / "crypto" / "data" / "keys"


def line(c="─", n=70):
    print(c * n)


def section(title):
    print()
    line("━")
    print(f"  {title}")
    line("━")


def main():
    print()
    line("═")
    print("  DEMO Enc²Health — Phân tích dữ liệu y tế MÃ HÓA trên Cloud")
    print("  Truy vấn: AVG viện phí — bệnh nhân Tiểu đường (E11) TRÊN 60 tuổi")
    line("═")

    # Điều kiện của câu hỏi (dạng "người"):
    human_filters = {"ma_benh": "E11", "tuoi_min_enc": 61}  # >60 tuổi = >=61
    query_type = "avg_vien_phi"

    ex = SoftwareExecutor()
    if not ex.mongo_available:
        print("\n[!] MongoDB chưa chạy hoặc chưa có data. Chạy: python3 crypto/data/generate_ehr.py")
        return

    # ── Bước 1: Router phân loại toán tử ────────────────────────────
    section("BƯỚC 1 — Router (Nam) phân loại: chạy ở đâu?")
    decision = QueryRouter().route(query_type, human_filters)
    print(f"  Toán tử '{query_type}'  →  chế độ: {decision.mode.value.upper()}")
    print(f"  Lý do: {decision.reason}")
    print("  (AVG cần giải mã viện phí → bắt buộc vào TEE/Enclave để bảo mật)")

    # ── Bước 2: Dựng filter MÃ HÓA gửi xuống MongoDB ────────────────
    section("BƯỚC 2 — Mã hóa điều kiện lọc (đây là TẤT CẢ những gì CSP thấy)")
    mongo_query = ex._build_filter(human_filters)
    print("  Điều kiện người đọc:  ma_benh = 'E11'  AND  tuoi > 60")
    print("  Điều kiện gửi xuống MongoDB (ciphertext):")
    print(f"    ma_benh_enc =  {str(mongo_query.get('ma_benh_enc'))[:48]}...   ← DTE (tìm '=')")
    print(f"    tuoi_enc    =  {mongo_query.get('tuoi_enc')}   ← ORE (so sánh '>')")
    print("  → CSP KHÔNG biết đây là 'E11' hay 'trên 60 tuổi'. Chỉ là chuỗi/số vô nghĩa.")

    # ── Bước 3: MongoDB trả về các BẢN MÃ khớp ──────────────────────
    section("BƯỚC 3 — MongoDB lọc trên ciphertext, trả về bản mã viện phí")
    n = ex.count(human_filters).n_records
    ciphertexts = ex.fetch_vien_phi_ciphertexts(human_filters)
    print(f"  Số hồ sơ khớp: {n} bệnh nhân")
    print(f"  Router nhận {len(ciphertexts)} bản mã 'vien_phi_enc'. Ví dụ 3 cái đầu:")
    for ct in ciphertexts[:3]:
        print(f"    {ct[:56]}...")
    print("  → Đây vẫn là ciphertext. CSP/Router CHƯA biết viện phí thật là bao nhiêu.")

    # ── Bước 4: TRONG ENCLAVE — giải mã + tính toán ─────────────────
    section("BƯỚC 4 — TRONG ENCLAVE (SGX): giải mã trong CPU rồi tính AVG")
    print("  (Bước này trong hệ thống thật chạy bên trong Intel SGX — admin KHÔNG nhìn vào được.")
    print("   Khóa AES-GCM lấy từ Vault sau RA-TLS. Ở demo: lấy khóa từ file để minh họa.)")
    gcm = AESGCMCipher.load_key(str(KEY_DIR / "gcm_dek.key"))
    values = [float(gcm.decrypt_json(ct)) for ct in ciphertexts]
    avg = sum(values) / len(values) if values else 0.0
    print(f"  Plaintext viện phí (CHỈ tồn tại ở đây, ví dụ 3 giá trị): "
          f"{[round(v) for v in values[:3]]} ...")
    print(f"  Kết quả AVG = {avg:,.0f} VND")
    print("  → Plaintext KHÔNG BAO GIỜ rời khỏi enclave. Chỉ con số tổng hợp đi ra.")

    # ── Bước 5: RBAC — ai nhận được gì ──────────────────────────────
    section("BƯỚC 5 — RBAC: cùng 1 kết quả, mỗi vai trò thấy khác nhau")
    rbac = RBACMiddleware()
    raw = {"result": round(avg), "n_records": n}
    for role in ("admin", "doctor", "admin_staff"):
        access = rbac.check(role, query_type)
        if not access.allowed:
            shown = f"❌ BỊ CHẶN 403 ({access.reason})"
        else:
            masked = rbac.mask_result(dict(raw), role)
            shown = f"result = {masked['result']}"
            if access.masked_fields:
                shown += f"   (che: {', '.join(access.masked_fields)})"
        print(f"  {role:<11}: {shown}")

    # ── Bước 6: Cost model — vì sao chọn TEE ────────────────────────
    section("BƯỚC 6 — Cost model: so sánh chi phí 2 chế độ (số liệu thật)")
    cost = compare_costs(n)
    print(f"  Ước tính cho {n} bản ghi:")
    print(f"    Software (DTE/ORE) ≈ {cost['software']['estimated_latency_ms']} ms")
    print(f"    TEE (enclave)      ≈ {cost['tee']['estimated_latency_ms']} ms")
    print(f"    → rẻ hơn: {cost['cheaper_mode'].upper()} "
          f"(TEE đắt hơn ~{cost['tee_over_software_ratio']}x — cái giá của bảo mật)")
    print("  Khi enclave quá tải (đợt dịch), Adaptive Controller tự chuyển việc nhẹ về Software.")

    # ── Tổng kết ────────────────────────────────────────────────────
    section("TỔNG KẾT — Bức tranh bảo mật")
    print(f"  • CSP (admin cloud) nhìn thấy:  ciphertext (chuỗi vô nghĩa)")
    print(f"  • Bác sĩ nhận về:               1 con số = {avg:,.0f} VND")
    print(f"  • Plaintext:                    chỉ tồn tại trong enclave, rồi biến mất")
    print(f"  • NV Hành chính nhận về:        kết quả trung bình (nhưng bị giới hạn chẩn đoán)")
    print()
    print("  Đó chính là câu trả lời của đề tài: bệnh viện phân tích được dữ liệu trên")
    print("  Cloud mà KHÔNG lộ thông tin bệnh nhân cho nhà cung cấp dịch vụ.")
    line("═")
    print()


if __name__ == "__main__":
    main()
