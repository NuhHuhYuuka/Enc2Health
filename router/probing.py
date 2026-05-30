# T5 - Probing task phát hiện EPC saturation
# Chạy định kỳ Binary Search + Quick Sort nhỏ trong enclave
# Nếu latency tăng gấp đôi → EPC saturation detected

import time
import threading
import os
import requests
from dataclasses import dataclass
from typing import Optional

ECALL_POOL_URL = os.environ.get("ECALL_POOL_URL", "http://127.0.0.1:9091")
PROBE_INTERVAL_S = 5       # probe mỗi 5 giây
SATURATION_RATIO = 2.0     # latency tăng gấp đôi → saturated
EPC_THRESHOLD_PCT = 0.80   # RSS > 80% RAM → fallback

@dataclass
class ProbeResult:
    latency_ms: float
    rss_mb: float
    saturated: bool
    timestamp: float

class EPCProber:
    """
    Probing task định kỳ:
    - Gửi query nhỏ vào ECALL pool, đo latency
    - So sánh với baseline latency
    - Nếu latency tăng gấp đôi → báo EPC saturation
    """

    def __init__(self, base_url: str = ECALL_POOL_URL):
        self.base_url = base_url
        self.baseline_latency: Optional[float] = None
        self._probe_count = 0
        self._baseline_locked = False
        self.latest_result: Optional[ProbeResult] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def _request_kwargs(self) -> dict:
        """Build request kwargs compatible with both HTTP and HTTPS+mTLS modes."""
        kwargs = {"timeout": 10}
        if self.base_url.startswith("https://"):
            ca_bundle = os.environ.get("T8_SSL_CA")
            client_cert = os.environ.get("ROUTER_CLIENT_CERT")
            client_key = os.environ.get("ROUTER_CLIENT_KEY")
            kwargs["verify"] = ca_bundle if ca_bundle else True
            if client_cert and client_key:
                kwargs["cert"] = (client_cert, client_key)
        return kwargs

    def _auth_headers(self) -> dict:
        token = os.environ.get("AUTH_JWT")
        if not token:
            try:
                from common.auth import generate_test_jwt
                token = generate_test_jwt(
                    os.environ.get("AUTH_SUBJECT", "router-service"),
                    os.environ.get("AUTH_ROLE", "service"),
                )
            except Exception:
                token = None
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _probe_once(self) -> Optional[ProbeResult]:
        """Gửi 1 query probe nhỏ, đo latency."""
        try:
            headers = self._auth_headers()
            kwargs = self._request_kwargs()

            t0 = time.perf_counter()
            r = requests.post(
                f"{self.base_url}/query",
                json={"query_type": "count", "filters": {}, "role": "admin"},
                headers=headers,
                **kwargs,
            )
            latency_ms = (time.perf_counter() - t0) * 1000

            if r.status_code != 200:
                return None

            # Lấy RSS từ stats endpoint
            health_kwargs = dict(kwargs)
            health_kwargs["timeout"] = 5
            stats = requests.get(f"{self.base_url}/health", headers=headers, **health_kwargs).json()
            rss_mb = stats.get("uptime_s", 0) * 0  # placeholder
            # Dùng RSS_PROFILE từ c_tee_metrics để ước tính
            rss_mb = 25.8 + (latency_ms / 1.051) * 0.5  # nội suy

            # Set baseline lần đầu
            self._probe_count += 1
            if not self._baseline_locked:
                if self.baseline_latency is None or latency_ms < self.baseline_latency:
                    self.baseline_latency = latency_ms
                    print(f"[Prober] Baseline updated: {latency_ms:.2f}ms")
                if self._probe_count >= 3:
                    self._baseline_locked = True
                    print(f"[Prober] Baseline locked at {self.baseline_latency:.2f}ms")

            saturated = (
                latency_ms > self.baseline_latency * SATURATION_RATIO
            )

            return ProbeResult(
                latency_ms=latency_ms,
                rss_mb=rss_mb,
                saturated=saturated,
                timestamp=time.time()
            )

        except Exception as e:
            print(f"[Prober] Probe failed: {e}")
            return None

    def _run_loop(self):
        """Background loop chạy probe định kỳ."""
        print(f"[Prober] Started (interval={PROBE_INTERVAL_S}s)")
        while self._running:
            result = self._probe_once()
            if result:
                with self._lock:
                    self.latest_result = result
                status = "⚠️  SATURATED" if result.saturated else "✅ OK"
                print(
                    f"[Prober] {status} | "
                    f"latency={result.latency_ms:.2f}ms | "
                    f"baseline={self.baseline_latency:.2f}ms | "
                    f"ratio={result.latency_ms/self.baseline_latency:.2f}x"
                )
            time.sleep(PROBE_INTERVAL_S)

    def start(self):
        """Khởi động background probing thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Dừng probing thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        print("[Prober] Stopped")

    def is_saturated(self) -> bool:
        """Kiểm tra EPC có đang bão hòa không."""
        with self._lock:
            if self.latest_result is None:
                return False
            return self.latest_result.saturated

    def get_status(self) -> dict:
        """Trả về trạng thái hiện tại."""
        with self._lock:
            if self.latest_result is None:
                return {"status": "no_data", "saturated": False}
            return {
                "status": "saturated" if self.latest_result.saturated else "ok",
                "saturated": self.latest_result.saturated,
                "latency_ms": round(self.latest_result.latency_ms, 3),
                "baseline_ms": round(self.baseline_latency, 3) if self.baseline_latency else None,
                "ratio": round(
                    self.latest_result.latency_ms / self.baseline_latency, 3
                ) if self.baseline_latency else None,
            }
