#!/usr/bin/env python3
"""T10 Prometheus exporter for enclave metrics."""

from __future__ import annotations

import random
import signal
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server
except ModuleNotFoundError:
    Counter = Gauge = Histogram = start_http_server = None

HAVE_PROMETHEUS_CLIENT = start_http_server is not None

if HAVE_PROMETHEUS_CLIENT:
    asym_latency = Histogram(
        "enclave_asym_latency_ms",
        "Asymmetric operation latency in milliseconds",
        ["algorithm"],
    )
    epc_pressure = Gauge(
        "enclave_epc_pressure_ratio",
        "RSS/totalRAM proxy",
    )
    key_retrieve = Counter(
        "enclave_private_key_retrieve_total",
        "Private key retrieval events",
        ["dept"],
    )
    aes_throughput = Gauge(
        "enclave_aes_gcm_throughput_mbs",
        "AES-GCM throughput in MB/s",
    )
else:
    asym_latency = epc_pressure = key_retrieve = aes_throughput = None

stop_event = Event()
fallback_metrics = {
    "asym_latency": {"rsa-4096": [], "ecc-p384": []},
    "epc_pressure": 0.0,
    "key_retrieve": {"Cap_cuu": 0, "Noi": 0},
    "aes_throughput": 0.0,
}


def observe_asym_latency(algorithm: str, value: float) -> None:
    if HAVE_PROMETHEUS_CLIENT:
        asym_latency.labels(algorithm=algorithm).observe(value)
    else:
        fallback_metrics["asym_latency"].setdefault(algorithm, []).append(value)


def set_epc_pressure(value: float) -> None:
    if HAVE_PROMETHEUS_CLIENT:
        epc_pressure.set(value)
    else:
        fallback_metrics["epc_pressure"] = value


def increment_key_retrieve(dept: str) -> None:
    if HAVE_PROMETHEUS_CLIENT:
        key_retrieve.labels(dept=dept).inc(1)
    else:
        fallback_metrics["key_retrieve"][dept] = fallback_metrics["key_retrieve"].get(dept, 0) + 1


def set_aes_throughput(value: float) -> None:
    if HAVE_PROMETHEUS_CLIENT:
        aes_throughput.set(value)
    else:
        fallback_metrics["aes_throughput"] = value


def render_fallback_metrics() -> bytes:
    lines = [
        "# HELP enclave_asym_latency_ms Asymmetric operation latency in milliseconds",
        "# TYPE enclave_asym_latency_ms summary",
    ]
    for algorithm, values in fallback_metrics["asym_latency"].items():
        count = len(values)
        total = sum(values)
        latest = values[-1] if values else 0.0
        lines.append(f'enclave_asym_latency_ms{{algorithm="{algorithm}"}} {latest:.6f}')
        lines.append(f'enclave_asym_latency_ms_count{{algorithm="{algorithm}"}} {count}')
        lines.append(f'enclave_asym_latency_ms_sum{{algorithm="{algorithm}"}} {total:.6f}')

    lines.extend([
        "# HELP enclave_epc_pressure_ratio RSS/totalRAM proxy",
        "# TYPE enclave_epc_pressure_ratio gauge",
        f'enclave_epc_pressure_ratio {fallback_metrics["epc_pressure"]:.6f}',
        "# HELP enclave_private_key_retrieve_total Private key retrieval events",
        "# TYPE enclave_private_key_retrieve_total counter",
    ])
    for dept, count in fallback_metrics["key_retrieve"].items():
        lines.append(f'enclave_private_key_retrieve_total{{dept="{dept}"}} {count}')

    lines.extend([
        "# HELP enclave_aes_gcm_throughput_mbs AES-GCM throughput in MB/s",
        "# TYPE enclave_aes_gcm_throughput_mbs gauge",
        f'enclave_aes_gcm_throughput_mbs {fallback_metrics["aes_throughput"]:.6f}',
        "",
    ])
    return "\n".join(lines).encode("utf-8")


class FallbackMetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in {"/", "/metrics"}:
            self.send_response(404)
            self.end_headers()
            return

        body = render_fallback_metrics()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args) -> None:
        pass


def start_fallback_http_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), FallbackMetricsHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server


def seed_demo_metrics() -> None:
    """Emit a small, safe baseline so curl /metrics shows the enclave series."""
    observe_asym_latency("rsa-4096", 92.0)
    observe_asym_latency("ecc-p384", 58.0)
    set_epc_pressure(0.34)
    increment_key_retrieve("Cap_cuu")
    set_aes_throughput(128.0)


def update_demo_metrics() -> None:
    """Refresh the demo values periodically while the exporter runs standalone."""
    while not stop_event.wait(5):
        observe_asym_latency("rsa-4096", 85.0 + random.random() * 10.0)
        observe_asym_latency("ecc-p384", 50.0 + random.random() * 8.0)
        set_epc_pressure(0.25 + random.random() * 0.35)
        increment_key_retrieve("Cap_cuu")
        increment_key_retrieve("Noi")
        set_aes_throughput(110.0 + random.random() * 35.0)


def handle_shutdown(signum, frame) -> None:
    del signum, frame
    stop_event.set()


def main() -> None:
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    port = int(os.environ.get("T10_EXPORTER_PORT", "8002"))
    seed_demo_metrics()
    server = None
    if HAVE_PROMETHEUS_CLIENT:
        start_http_server(port)
    else:
        server = start_fallback_http_server(port)
        print("[T10] prometheus_client not installed; using stdlib metrics exporter")
    print(f"[T10] Prometheus exporter started on port {port}")

    Thread(target=update_demo_metrics, daemon=True).start()

    while not stop_event.wait(1):
        pass

    if server is not None:
        server.shutdown()


if __name__ == "__main__":
    main()
