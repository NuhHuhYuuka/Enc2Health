# T1 - Query Router: phân loại SQL operator → Software hoặc TEE mode

from enum import Enum
from dataclasses import dataclass

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
    Phân loại SQL operator (theo kịch bản Enc²Health):
    - SOFTWARE mode (DTE/ORE xử lý trực tiếp trên ciphertext, không giải mã):
        =, JOIN, GROUP BY, COUNT, range lọc theo vùng/tuổi
    - TEE mode (cần giải mã trong enclave để tính toán):
        SUM, AVG, COUNT DISTINCT
    """

    # Toán tử cần enclave (giải mã rồi mới tính được)
    TEE_QUERY_TYPES = {
        "sum_vien_phi", "avg_vien_phi",
        "avg_glucose", "avg_creatinine",
        "sum", "avg", "count_distinct", "count distinct",
        "get_patient", "lookup_patient",
        "stddev", "median",
    }
    # Toán tử chạy được trên ciphertext bằng DTE/ORE
    SOFTWARE_QUERY_TYPES = {
        "count", "equality", "=", "eq",
        "join", "group_by", "group by", "range", "filter",
        "keyword_search", "search",
    }

    def _normalize(self, query_type: str) -> str:
        return query_type.lower().strip().replace("-", "_")

    def route(self, query_type: str, filters: dict = None) -> RouteDecision:
        q = self._normalize(query_type)

        if q in self.TEE_QUERY_TYPES:
            reason = (
                "PII lookup cần private key và giải mã trong TEE enclave"
                if q in ("get_patient", "lookup_patient")
                else
                "COUNT DISTINCT cần enclave (deduplicate trên plaintext)"
                if q in ("count_distinct", "count distinct")
                else "Aggregation operator cần giải mã trong TEE enclave"
            )
            return RouteDecision(mode=ExecutionMode.TEE, reason=reason, query_type=query_type)

        if q == "count":
            return RouteDecision(
                mode=ExecutionMode.SOFTWARE,
                reason="COUNT đơn giản chạy trên ciphertext, Software Mode đủ",
                query_type=query_type,
            )

        if q in self.SOFTWARE_QUERY_TYPES:
            if q in ("keyword_search", "search"):
                return RouteDecision(
                    mode=ExecutionMode.SOFTWARE,
                    reason="SSE keyword search chạy trên encrypted inverted index",
                    query_type=query_type,
                )
            return RouteDecision(
                mode=ExecutionMode.SOFTWARE,
                reason="Equality/range/join/group-by chạy trên DTE/ORE, Software Mode",
                query_type=query_type,
            )

        # Mặc định an toàn: toán tử lạ → Software (không đẩy dữ liệu vào enclave vô cớ)
        return RouteDecision(
            mode=ExecutionMode.SOFTWARE,
            reason="Toán tử không xác định, mặc định Software Mode",
            query_type=query_type,
        )
