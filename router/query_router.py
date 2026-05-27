# T1 - Query Router: phân loại SQL operator → Software hoặc TEE mode

from enum import Enum
from dataclasses import dataclass
from typing import Optional

class ExecutionMode(Enum):
    SOFTWARE = "software"
    TEE = "tee"

@dataclass
class RouteDecision:
    mode: ExecutionMode
    reason: str
    query_type: str

class QueryRouter:
    """
    Phân loại SQL operator:
    - SOFTWARE mode: =, JOIN, GROUP BY  (DTE/ORE xử lý được)
    - TEE mode: SUM, AVG, COUNT DISTINCT (cần enclave)
    """

    TEE_OPERATORS = {"sum", "avg", "count distinct"}
    SOFTWARE_OPERATORS = {"=", "join", "group by", "count"}

    def route(self, query_type: str, filters: dict = {}) -> RouteDecision:
        q = query_type.lower().strip()

        if q in ("sum_vien_phi", "avg_vien_phi"):
            return RouteDecision(
                mode=ExecutionMode.TEE,
                reason="Aggregation operator cần TEE enclave",
                query_type=query_type
            )
        elif q == "count":
            return RouteDecision(
                mode=ExecutionMode.SOFTWARE,
                reason="COUNT đơn giản, Software Mode đủ",
                query_type=query_type
            )
        else:
            return RouteDecision(
                mode=ExecutionMode.SOFTWARE,
                reason="Equality/range operator, Software Mode",
                query_type=query_type
            )
