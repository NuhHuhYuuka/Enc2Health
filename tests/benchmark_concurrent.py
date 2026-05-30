# T12 - Benchmark concurrent clients 1→5→10→20→50
import httpx
import os
import threading
import time
import math
import json
import statistics
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.auth import generate_test_jwt

ROUTER_URL = "http://localhost:8000"
QUERIES_PER_CLIENT = 20

def client_worker(client_id: int, results: list, query_type: str, role: str):
    latencies = []
    errors = 0
    # reuse a client for this thread to avoid per-request connection/setup overhead
    with httpx.Client(timeout=30) as client:
        for _ in range(QUERIES_PER_CLIENT):
            try:
                t0 = time.perf_counter()
                token = os.environ.get("AUTH_JWT")
                if not token:
                    secret = os.environ.get("AUTH_JWT_SECRET")
                    if not secret:
                        raise RuntimeError("Missing AUTH_JWT or AUTH_JWT_SECRET")
                    os.environ["AUTH_JWT_SECRET"] = secret
                    token = generate_test_jwt(os.environ.get("AUTH_SUBJECT", "benchmark-client"), os.environ.get("AUTH_ROLE", role))
                headers = {"Authorization": f"Bearer {token}"}

                r = client.post(f"{ROUTER_URL}/query", json={
                    "query_type": query_type,
                    "filters": {},
                    "role": role
                }, headers=headers)
                elapsed = (time.perf_counter() - t0) * 1000
                if r.status_code == 200:
                    latencies.append(elapsed)
                else:
                    errors += 1
            except Exception:
                errors += 1
    results.append({"client_id": client_id, "latencies": latencies, "errors": errors})

def run_concurrent(n_clients: int, query_type: str, role: str) -> dict:
    results = []
    threads = [
        threading.Thread(
            target=client_worker,
            args=(i, results, query_type, role)
        )
        for i in range(n_clients)
    ]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total_s = time.perf_counter() - t0

    all_latencies = []
    total_errors = 0
    for r in results:
        all_latencies.extend(r["latencies"])
        total_errors += r["errors"]

    all_latencies.sort()
    n = len(all_latencies)
    attempts = n + total_errors
    avg_ms = round(statistics.mean(all_latencies), 2) if n > 0 else 0
    # use ceil-based quantile indexing
    p95_ms = round(all_latencies[min(n - 1, math.ceil(n * 0.95) - 1)], 2) if n > 0 else 0
    p99_ms = round(all_latencies[min(n - 1, math.ceil(n * 0.99) - 1)], 2) if n > 0 else 0
    throughput_qps = round(attempts / total_s, 1) if total_s > 0 else 0

    return {
        "n_clients": n_clients,
        "total_queries": n,
        "attempts": attempts,
        "errors": total_errors,
        "avg_ms": avg_ms,
        "p95_ms": p95_ms,
        "p99_ms": p99_ms,
        "throughput_qps": throughput_qps,
        "error_rate_pct": round(total_errors / attempts * 100, 1) if attempts > 0 else 0,
    }

if __name__ == "__main__":
    print("=" * 65)
    print("T12 — Concurrent Clients Benchmark")
    print("=" * 65)

    results = []
    for n in [1, 5, 10, 20, 50]:
        print(f"\n[{n:2d} clients] running...", end=" ", flush=True)
        r = run_concurrent(n, "avg_vien_phi", "admin")
        results.append(r)
        print(f"avg={r['avg_ms']}ms | p99={r['p99_ms']}ms | "
              f"QPS={r['throughput_qps']} | err={r['error_rate_pct']}%")

    print("\n" + "=" * 65)
    print(f"{'Clients':>8} {'avg_ms':>8} {'p99_ms':>8} {'QPS':>8} {'err%':>6}")
    print("-" * 65)
    for r in results:
        print(f"{r['n_clients']:>8} {r['avg_ms']:>8} {r['p99_ms']:>8} "
              f"{r['throughput_qps']:>8} {r['error_rate_pct']:>6}")

    with open("concurrent_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nĐã lưu vào concurrent_results.json")
    print("=" * 65)
