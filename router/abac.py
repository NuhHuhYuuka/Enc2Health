"""ABAC — Attribute-Based Access Control cho Enc²Health.

Mở rộng RBAC (router/rbac.py) bằng lớp THUỘC TÍNH theo đúng kịch bản §3:
chính sách không chỉ dựa trên VAI TRÒ mà còn trên THUỘC TÍNH của chủ thể —
cụ thể là KHOA (department):

    "Bác sĩ điều trị chỉ được xem bệnh nhân thuộc khoa của mình."

Cơ chế: ngoài quyết định allow/deny + mask của RBAC, ABAC còn trả về
`scope_filters` — bộ lọc BẮT BUỘC mà Router tiêm vào truy vấn (client không thể
nới rộng). Ví dụ bác sĩ khoa Tim_mach → mọi truy vấn tự kèm khoa_phong=Tim_mach.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from router.rbac import RBACMiddleware

# Vai trò bị giới hạn theo khoa (thuộc tính dept của chủ thể).
DEPT_SCOPED_ROLES = {"doctor"}

# Strict mode: nếu bật, vai trò bị-giới-hạn-theo-khoa mà THIẾU thuộc tính dept
# sẽ bị từ chối. Mặc định tắt để tương thích ngược (token cũ không có dept).
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
    """Policy engine: kết hợp RBAC (role) + rule thuộc tính (dept-scoping)."""

    def __init__(self):
        self._rbac = RBACMiddleware()

    def evaluate(self, subject: Subject, query_type: str) -> AbacDecision:
        # 1) Tầng RBAC: vai trò có được chạy query_type không + mask cột.
        access = self._rbac.check(subject.role, query_type)
        if not access.allowed:
            return AbacDecision(allowed=False, reason=access.reason)

        # 2) Tầng thuộc tính: giới hạn theo khoa.
        scope: Dict[str, str] = {}
        if subject.role in DEPT_SCOPED_ROLES and subject.dept:
            scope["khoa_phong"] = subject.dept
        elif subject.role in DEPT_SCOPED_ROLES and ABAC_REQUIRE_DEPT:
            return AbacDecision(
                allowed=False,
                reason=f"Vai trò '{subject.role}' yêu cầu thuộc tính 'dept' (ABAC strict mode)",
            )

        return AbacDecision(
            allowed=True,
            reason="OK",
            masked_fields=list(access.masked_fields),
            scope_filters=scope,
        )

    def mask_result(self, result: dict, role: str) -> dict:
        return self._rbac.mask_result(result, role)

    def mask_pii(self, pii: dict, role: str) -> dict:
        return self._rbac.mask_pii(pii, role)
