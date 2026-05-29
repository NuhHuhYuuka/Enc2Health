# T9 - Kết nối ECALL Task Pool của Lan
# Service của Lan chạy tại http://127.0.0.1:9091

import httpx
import os
import ssl
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
            headers = {}
            token = os.environ.get("INTERNAL_AUTH_TOKEN")
            if token:
                headers["X-Internal-Auth"] = token

            # Use mTLS client cert when provided
            client_cert = os.environ.get("ROUTER_CLIENT_CERT")
            client_key = os.environ.get("ROUTER_CLIENT_KEY")
            ca_bundle = os.environ.get("T8_SSL_CA")

            verify = True
            cert = None
            if ca_bundle:
                verify = ca_bundle
            if client_cert and client_key:
                cert = (client_cert, client_key)

            with httpx.Client(verify=verify, cert=cert, timeout=5) as c:
                # Prefer attest endpoint to verify enclave identity
                try:
                    r = c.get(f"{self.base_url}/attest", headers=headers)
                    if r.status_code == 200:
                        data = r.json()
                        expected = os.environ.get("T8_EXPECTED_MRENCLAVE")
                        if expected and data.get("mrenclave") != expected:
                            print("[EcallClient] Attestation MRENCLAVE mismatch")
                            return False
                        return True
                except Exception:
                    pass

                r = c.get(f"{self.base_url}/health", headers=headers)
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
            headers = {}
            token = os.environ.get("INTERNAL_AUTH_TOKEN")
            if token:
                headers["X-Internal-Auth"] = token

            client_cert = os.environ.get("ROUTER_CLIENT_CERT")
            client_key = os.environ.get("ROUTER_CLIENT_KEY")
            ca_bundle = os.environ.get("T8_SSL_CA")

            verify = True
            cert = None
            if ca_bundle:
                verify = ca_bundle
            if client_cert and client_key:
                cert = (client_cert, client_key)

            with httpx.Client(verify=verify, cert=cert, timeout=TIMEOUT_S) as c:
                r = c.post(
                    f"{self.base_url}/query",
                    json=payload,
                    timeout=TIMEOUT_S,
                    headers=headers
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
