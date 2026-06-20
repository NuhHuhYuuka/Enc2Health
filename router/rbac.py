"""DEPRECATED — RBAC đã được GỘP vào ABAC (router/abac.py).

Hệ giờ dùng MỘT engine kiểm soát truy cập duy nhất: `AbacPolicy`. File này chỉ
còn là lớp tương thích ngược cho test/demo cũ gọi `RBACMiddleware().check(...)`.
Code mới hãy dùng trực tiếp `router.abac.AbacPolicy`.
"""
from __future__ import annotations

from dataclasses import dataclass

from router.abac import AbacPolicy, ROLE_PERMISSIONS  # noqa: F401


@dataclass
class AccessDecision:
    allowed: bool
    reason: str
    masked_fields: list


class RBACMiddleware:
    """Shim: ủy quyền toàn bộ cho AbacPolicy (tầng vai trò)."""

    def __init__(self):
        self._policy = AbacPolicy()

    def check(self, role: str, query_type: str) -> AccessDecision:
        d = self._policy._role_check(role, query_type)
        return AccessDecision(allowed=d.allowed, reason=d.reason, masked_fields=d.masked_fields)

    def mask_result(self, result: dict, role: str) -> dict:
        return self._policy.mask_result(result, role)

    def mask_pii(self, pii: dict, role: str) -> dict:
        return self._policy.mask_pii(pii, role)
