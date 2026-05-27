# T9 - Kết nối ECALL Task Pool của Lan
# Service của Lan chạy tại http://127.0.0.1:9091

import httpx
import time
from typing import Optional

ECALL_POOL_URL = "http://127.0.0.1:9091"
TIMEOUT_S = 30

class EcallClient:
    """
    Client kết nối tới ECALL Task Pool của Lan.
    Gửi query aggregation → nhận kết quả từ enclave.
    """

    def __init__(self, base_url: str = ECALL_POOL_URL):
        self.base_url = base_url

    def health_check(self) -> bool:
        """Kiểm tra service của Lan còn sống không."""
        try:
            r = httpx.get(f"{self.base_url}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def query(
        self,
        query_type: str,
        filters: dict = {},
        role: str = "doctor"
    ) -> Optional[dict]:
        """
        Gửi query vào ECALL pool của Lan.
        Returns: dict kết quả hoặc None nếu lỗi
        """
        payload = {
            "query_type": query_type,
            "filters": filters,
            "role": role
        }
        try:
            t0 = time.perf_counter()
            r = httpx.post(
                f"{self.base_url}/query",
                json=payload,
                timeout=TIMEOUT_S
            )
            elapsed = (time.perf_counter() - t0) * 1000

            if r.status_code == 200:
                result = r.json()
                result["router_latency_ms"] = round(elapsed, 3)
                return result
            else:
                print(f"[EcallClient] Error {r.status_code}: {r.text}")
                return None

        except httpx.ConnectError:
            print(f"[EcallClient] Không kết nối được tới Lan's service ({self.base_url})")
            return None
        except Exception as e:
            print(f"[EcallClient] Lỗi: {e}")
            return None
