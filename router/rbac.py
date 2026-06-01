# T4 - RBAC/ABAC Middleware
# Roles: doctor, admin, researcher
# Column-level: researcher không thấy vien_phi plaintext

from dataclasses import dataclass
from typing import Optional

# ── Role definitions ─────────────────────────────────────────────
ROLE_PERMISSIONS = {
    "admin": {
        "allowed_query_types": {"sum_vien_phi", "avg_vien_phi", "count", "get_patient", "lookup_patient", "keyword_search"},
        "can_see_vien_phi": True,
        "can_see_ma_benh": True,
        "pii_access": "full",
    },
    "doctor": {
        "allowed_query_types": {"avg_vien_phi", "count", "get_patient", "lookup_patient", "keyword_search"},
        "can_see_vien_phi": True,
        "can_see_ma_benh": True,
        "pii_access": "full",
    },
    # Nhân viên hành chính: chỉ xử lý viện phí, KHÔNG xem chẩn đoán y khoa (kịch bản §3).
    "admin_staff": {
        "allowed_query_types": {"sum_vien_phi", "avg_vien_phi", "count", "get_patient", "lookup_patient", "keyword_search"},
        "can_see_vien_phi": True,
        "can_see_ma_benh": False,    # ẩn mã bệnh / chẩn đoán
        "pii_access": "partial",
    },
    "researcher": {
        "allowed_query_types": {"avg_vien_phi", "count", "get_patient", "lookup_patient", "keyword_search"},
        "can_see_vien_phi": False,   # chỉ thấy kết quả aggregate, không thấy raw
        "can_see_ma_benh": False,    # ẩn mã bệnh
        "pii_access": "masked",
    },
}

@dataclass
class AccessDecision:
    allowed: bool
    reason: str
    masked_fields: list

class RBACMiddleware:
    """
    Kiểm tra quyền truy cập trước khi route query.
    - Role-level: query_type có được phép không
    - Column-level: field nào bị ẩn với role này
    """

    def check(self, role: str, query_type: str) -> AccessDecision:
        if role not in ROLE_PERMISSIONS:
            return AccessDecision(
                allowed=False,
                reason=f"Role không hợp lệ: {role}",
                masked_fields=[]
            )

        perms = ROLE_PERMISSIONS[role]

        if query_type not in perms["allowed_query_types"]:
            return AccessDecision(
                allowed=False,
                reason=f"Role '{role}' không được phép chạy '{query_type}'",
                masked_fields=[]
            )

        # Column-level masking
        masked = []
        if not perms["can_see_vien_phi"]:
            masked.append("vien_phi")
        if not perms["can_see_ma_benh"]:
            masked.append("ma_benh")

        return AccessDecision(
            allowed=True,
            reason="OK",
            masked_fields=masked
        )

    def mask_result(self, result: dict, role: str) -> dict:
        """Ẩn field nhạy cảm trong kết quả trả về."""
        perms = ROLE_PERMISSIONS.get(role, {})
        masked = result.copy()
        if not perms.get("can_see_vien_phi", True):
            if "result" in masked:
                masked["result"] = "[MASKED]"
        return masked

    def mask_pii(self, pii: dict, role: str) -> dict:
        """Mask PII plaintext after enclave decryption, according to role."""
        access = ROLE_PERMISSIONS.get(role, {}).get("pii_access", "masked")
        if access == "full":
            return dict(pii)
        if access == "partial":
            return {
                "ho_ten": pii.get("ho_ten", "[MASKED]"),
                "cmnd": "[MASKED]",
                "ngay_sinh": pii.get("ngay_sinh", "[MASKED]"),
                "dia_chi": "[MASKED]",
            }
        return {key: "[MASKED]" for key in ("ho_ten", "cmnd", "ngay_sinh", "dia_chi")}
