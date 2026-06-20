"""ABAC — engine kiểm soát truy cập DUY NHẤT của Enc²Health.

Trước đây tách RBAC (router/rbac.py) + ABAC. Nay **gộp về một** AbacPolicy:
quyết định dựa trên cả VAI TRÒ (role) lẫn THUỘC TÍNH (dept) của chủ thể —
theo đúng mô hình Attribute-Based Access Control.

Một quyết định gồm 3 phần:
  1. allow/deny  — vai trò có được chạy `query_type` không.
  2. masked_fields + mask_result/mask_pii — che cột nhạy cảm theo vai trò.
  3. scope_filters — bộ lọc BẮT BUỘC tiêm vào query (vd bác sĩ khoa Tim_mach →
     khoa_phong=Tim_mach), client KHÔNG thể nới rộng.

(router/rbac.py giờ chỉ là shim tương thích ngược, trỏ về engine này.)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ── Thuộc tính quyền theo VAI TRÒ (gộp từ RBAC cũ) ────────────────────────────
ROLE_PERMISSIONS = {
    "admin": {
        "allowed_query_types": {"sum_vien_phi", "avg_vien_phi", "avg_glucose", "avg_creatinine",
                                "count", "get_patient", "lookup_patient", "keyword_search"},
        "can_see_vien_phi": True,
        "can_see_ma_benh": True,
        "pii_access": "full",
    },
    "doctor": {
        "allowed_query_types": {"avg_vien_phi", "avg_glucose", "avg_creatinine",
                                "count", "get_patient", "lookup_patient", "keyword_search"},
        "can_see_vien_phi": True,
        "can_see_ma_benh": True,
        "pii_access": "full",
    },
    # Nhân viên hành chính: xem viện phí, che mã bệnh / hạn chế PII.
    "admin_staff": {
        "allowed_query_types": {"sum_vien_phi", "avg_vien_phi", "avg_glucose", "avg_creatinine",
                                "count", "get_patient", "lookup_patient", "keyword_search"},
        "can_see_vien_phi": True,
        "can_see_ma_benh": True,
        "pii_access": "full",
    },
}

# Vai trò bị giới hạn theo khoa (thuộc tính dept của chủ thể).
DEPT_SCOPED_ROLES = {"doctor"}

# Strict mode: vai trò dept-scoped mà THIẾU dept → từ chối. Mặc định tắt (token cũ).
ABAC_REQUIRE_DEPT = os.environ.get("ABAC_REQUIRE_DEPT", "0") == "1"


@dataclass
class Subject:
    """Chủ thể truy cập, dựng từ JWT claims."""
    role: str
    dept: Optional[str] = None


@dataclass
class AbacDecision:
    allowed: bool
    reason: str
    masked_fields: List[str] = field(default_factory=list)
    scope_filters: Dict[str, str] = field(default_factory=dict)  # filter bắt buộc tiêm vào query


class AbacPolicy:
    """Policy engine duy nhất: role-check + column-mask + dept-scoping."""

    # ── Tầng vai trò (role) ──────────────────────────────────────────────────
    def _role_check(self, role: str, query_type: str) -> AbacDecision:
        if role not in ROLE_PERMISSIONS:
            return AbacDecision(allowed=False, reason=f"Role không hợp lệ: {role}")
        perms = ROLE_PERMISSIONS[role]
        if query_type not in perms["allowed_query_types"]:
            return AbacDecision(allowed=False,
                                reason=f"Role '{role}' không được phép chạy '{query_type}'")
        masked: List[str] = []
        if not perms["can_see_vien_phi"]:
            masked.append("vien_phi")
        if not perms["can_see_ma_benh"]:
            masked.append("ma_benh")
        return AbacDecision(allowed=True, reason="OK", masked_fields=masked)

    # ── Quyết định đầy đủ: role + thuộc tính khoa ───────────────────────────
    def evaluate(self, subject: Subject, query_type: str) -> AbacDecision:
        decision = self._role_check(subject.role, query_type)
        if not decision.allowed:
            return decision

        scope: Dict[str, str] = {}
        if subject.role in DEPT_SCOPED_ROLES and subject.dept:
            scope["khoa_phong"] = subject.dept
        elif subject.role in DEPT_SCOPED_ROLES and ABAC_REQUIRE_DEPT:
            return AbacDecision(
                allowed=False,
                reason=f"Vai trò '{subject.role}' yêu cầu thuộc tính 'dept' (ABAC strict mode)",
            )

        decision.scope_filters = scope
        return decision

    # ── Che dữ liệu theo vai trò ────────────────────────────────────────────
    def mask_result(self, result: dict, role: str) -> dict:
        perms = ROLE_PERMISSIONS.get(role, {})
        masked = result.copy()
        if not perms.get("can_see_vien_phi", True) and "result" in masked:
            masked["result"] = "[MASKED]"
        return masked

    def mask_pii(self, pii: dict, role: str) -> dict:
        res: Dict[str, object] = {}
        access = ROLE_PERMISSIONS.get(role, {}).get("pii_access", "masked")
        if access == "full":
            res.update({
                "ho_ten": pii.get("ho_ten"),
                "cmnd": pii.get("cmnd"),
                "ngay_sinh": pii.get("ngay_sinh"),
                "dia_chi": pii.get("dia_chi"),
            })
        elif access == "partial":
            res.update({
                "ho_ten": pii.get("ho_ten", "[MASKED]"),
                "cmnd": "[MASKED]",
                "ngay_sinh": pii.get("ngay_sinh", "[MASKED]"),
                "dia_chi": "[MASKED]",
            })
        else:
            res.update({k: "[MASKED]" for k in ("ho_ten", "cmnd", "ngay_sinh", "dia_chi")})

        # Tóm tắt bệnh án & phác đồ điều trị chỉ cho admin/doctor.
        if role in {"admin", "doctor"}:
            res["tom_tat_benh_an"] = pii.get("tom_tat_benh_an")
            res["phac_do_dieu_tri"] = pii.get("phac_do_dieu_tri")
        else:
            res["tom_tat_benh_an"] = "[MASKED]"
            res["phac_do_dieu_tri"] = "[MASKED]"
        return res
