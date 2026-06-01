#!/usr/bin/env python3
"""
Demo ABAC (kiểm soát truy cập theo THUỘC TÍNH) — CÓ LỜI DẪN.

Chứng minh chính sách trong kịch bản §3:
  "Bác sĩ điều trị chỉ được xem bệnh nhân thuộc KHOA của mình."

So với RBAC (chỉ theo vai trò), ABAC quyết định dựa trên THUỘC TÍNH của chủ thể
(ở đây là `dept` = khoa). Router TỰ ĐỘNG tiêm bộ lọc khoa và client KHÔNG thể
nới rộng — kể cả khi cố gắng lách.

Chạy (cần MongoDB :27017 đã có data):  python3 scripts/demo_abac.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from router.abac import AbacPolicy, Subject
from router.software_executor import SoftwareExecutor


def line(c="─", n=72):
    print(c * n)


def run(policy: AbacPolicy, ex: SoftwareExecutor, subject: Subject,
        query_type: str, client_filters: dict, label: str):
    """Mô phỏng 1 request: ABAC quyết định → tiêm scope → thực thi → mask."""
    access = policy.evaluate(subject, query_type)
    who = f"{subject.role}" + (f"/{subject.dept}" if subject.dept else "")
    print(f"\n  ▶ {label}")
    print(f"    Chủ thể: {who:22} | query: {query_type} | client gửi filter: {client_filters}")
    if not access.allowed:
        print(f"    ❌ TỪ CHỐI (403): {access.reason}")
        return
    # ABAC tiêm scope BẮT BUỘC — scope thắng filter của client.
    eff = {**client_filters, **access.scope_filters}
    if access.scope_filters:
        print(f"    🔒 ABAC tiêm scope: {access.scope_filters}  → filter hiệu lực: {eff}")
    result = ex.query(query_type, eff)
    raw = {"result": round(result.result), "n_records": result.n_records}
    shown = policy.mask_result(dict(raw), subject.role)
    masked = f"  (che: {', '.join(access.masked_fields)})" if access.masked_fields else ""
    print(f"    ✅ Kết quả: result={shown['result']} | n_records={result.n_records}{masked}")


def main():
    print()
    line("═")
    print("  DEMO ABAC — Kiểm soát truy cập theo THUỘC TÍNH (khoa)")
    print("  Chính sách: bác sĩ chỉ xem bệnh nhân khoa mình; client không lách được")
    line("═")

    ex = SoftwareExecutor()
    if not ex.mongo_available:
        print("\n[!] MongoDB chưa chạy/chưa có data. Chạy: python3 crypto/data/generate_ehr.py")
        return
    policy = AbacPolicy()

    print("\n  [1] BÁC SĨ khoa Tim_mach đếm bệnh nhân — ABAC giới hạn theo khoa")
    line()
    run(policy, ex, Subject("doctor", "Tim_mach"), "count", {}, "Bác sĩ Tim_mach: 'có bao nhiêu bệnh nhân?'")
    run(policy, ex, Subject("admin"), "count", {}, "Admin (toàn viện) hỏi cùng câu — để đối chiếu")

    print("\n  [2] CHỐNG LÁCH — bác sĩ Tim_mach cố xem khoa Noi")
    line()
    run(policy, ex, Subject("doctor", "Tim_mach"), "count", {"khoa_phong": "Noi"},
        "Bác sĩ Tim_mach tự thêm filter khoa_phong=Noi (cố lách)")
    print("    → ABAC GHI ĐÈ về Tim_mach: bác sĩ KHÔNG thể xem khoa khác.")

    print("\n  [3] PHÂN QUYỀN THEO VAI TRÒ + THUỘC TÍNH")
    line()
    run(policy, ex, Subject("admin_staff"), "avg_vien_phi", {},
        "Nhân viên hành chính xem AVG viện phí (được xem chẩn đoán)")
    run(policy, ex, Subject("doctor", "Noi"), "sum_vien_phi", {},
        "Bác sĩ chạy SUM — bị tầng RBAC chặn (ngoài quyền)")

    print()
    line("═")
    print("  Ý NGHĨA: Quyết định truy cập = VAI TRÒ (RBAC) + THUỘC TÍNH khoa (ABAC).")
    print("  Bộ lọc khoa do Router tiêm, client không nới rộng được → cách ly dữ liệu")
    print("  giữa các khoa, đúng yêu cầu HIPAA/§3 của đề tài.")
    line("═")
    print()


if __name__ == "__main__":
    main()
