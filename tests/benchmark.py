# T11 - Benchmark 3 chế độ: Software-only / TEE-only / Hybrid Adaptive
import time
import os
import json
import statistics
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.auth import generate_test_jwt

try:
    import httpx
except ImportError:
    raise SystemExit(
        "Missing dependency 'httpx'.\n"
        "Install quickly with: python3 -m pip install --user httpx\n"
        "Or create a virtualenv and install all requirements:\n"
        "  python3 -m venv .venv\n"
        "  source .venv/bin/activate\n"
        "  pip install -r requirements.txt\n"
        "(This repository includes crypto/requirements.txt.)"
    )

ROUTER_URL = "http://localhost:8000"
RUNS = 50  # số lần lặp mỗi query

def run_query(client: httpx.Client, query_type: str, role: str, filters: dict = {}) -> float:
    """Chạy 1 query, trả về latency ms."""
    t0 = time.perf_counter()
    token = os.environ.get("AUTH_JWT")
    if not token:
        secret = os.environ.get("AUTH_JWT_SECRET")
        if not secret:
            raise SystemExit(
                "Missing AUTH_JWT or AUTH_JWT_SECRET. Set AUTH_JWT_SECRET and run scripts/generate_jwt.py, or export AUTH_JWT directly."
            )
        os.environ["AUTH_JWT_SECRET"] = secret
        token = generate_test_jwt(os.environ.get("AUTH_SUBJECT", "benchmark-client"), os.environ.get("AUTH_ROLE", role))
    headers = {"Authorization": f"Bearer {token}"}
    r = client.post(f"{ROUTER_URL}/query", json={
        "query_type": query_type,
        "filters": filters,
        "role": role
    }, headers=headers)
    elapsed = (time.perf_counter() - t0) * 1000
    if r.status_code != 200:
        return -1.0
    return elapsed

def benchmark(label: str, query_type: str, role: str, filters: dict = {}):
    """Chạy RUNS lần, tính avg/p95/p99.

    Cải tiến: thêm warmup (giảm ảnh hưởng thiết lập kết nối), reuse
    httpx.Client, và tính quantile/QPS chính xác hơn.
    """
    print(f"\n[Benchmark] {label} ...")
    WARMUP = min(5, RUNS // 5)
    latencies = []
    with httpx.Client() as client:
        # warmup
        for _ in range(WARMUP):
            try:
                _ = run_query(client, query_type, role, filters)
            except Exception:
                pass

        for _ in range(RUNS):
            ms = run_query(client, query_type, role, filters)
            if ms > 0:
                latencies.append(ms)

    if not latencies:
        raise RuntimeError("No successful runs recorded")

    latencies.sort()
    n = len(latencies)
    avg = statistics.mean(latencies)
    # use ceil-based indexing so p95 truly >= 95% of samples
    p95 = latencies[min(n - 1, math.ceil(n * 0.95) - 1)]
    p99 = latencies[min(n - 1, math.ceil(n * 0.99) - 1)]
    total_ms = sum(latencies)
    qps = (n / (total_ms / 1000.0)) if total_ms > 0 else 0

    result = {
        "label": label,
        "query_type": query_type,
        "runs": len(latencies),
        "avg_ms": round(avg, 3),
        "p95_ms": round(p95, 3),
        "p99_ms": round(p99, 3),
        "qps": round(qps, 1),
    }
    print(f"  avg={avg:.3f}ms  p95={p95:.3f}ms  p99={p99:.3f}ms  QPS={qps:.1f}")
    return result

if __name__ == "__main__":
    print("=" * 60)
    print("BENCHMARK — Enc2Health Query Router")
    print("=" * 60)

    results = []

    # 1. SOFTWARE-only: count (không cần enclave)
    results.append(benchmark(
        "SOFTWARE-only: count",
        query_type="count",
        role="admin"
    ))

    # 2. TEE-only: sum toàn bộ (nặng nhất)
    results.append(benchmark(
        "TEE-only: sum_vien_phi (all records)",
        query_type="sum_vien_phi",
        role="admin"
    ))

    # 3. TEE-only: avg toàn bộ
    results.append(benchmark(
        "TEE-only: avg_vien_phi (all records)",
        query_type="avg_vien_phi",
        role="admin"
    ))

    # 4. Hybrid: avg với filter (ít records hơn)
    results.append(benchmark(
        "Hybrid Adaptive: avg_vien_phi (filtered E11)",
        query_type="avg_vien_phi",
        role="admin",
        filters={"ma_benh": "E11"}
    ))

    # 5. RBAC overhead: researcher (có masking)
    results.append(benchmark(
        "Hybrid + RBAC masking: avg (researcher)",
        query_type="avg_vien_phi",
        role="researcher"
    ))

    # Lưu kết quả ra JSON
    with open("benchmark_results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("Kết quả đã lưu vào benchmark_results.json")
    print("=" * 60)
