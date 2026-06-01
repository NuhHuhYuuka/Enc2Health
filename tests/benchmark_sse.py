"""Static SSE keyword-search benchmark for Enc²Health.

Requires live Router + MongoDB seeded with `sse_index`.
"""

from __future__ import annotations

import json
import os
import sys
import statistics
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.auth import generate_test_jwt


ROUTER_URL = os.environ.get("ROUTER_URL", "http://127.0.0.1:8000")
KEYWORDS = [kw.strip() for kw in os.environ.get("SSE_BENCH_KEYWORDS", "I01,tăng huyết áp,glucose,viện phí").split(",") if kw.strip()]
RUNS = int(os.environ.get("SSE_BENCH_RUNS", "30"))


def _headers() -> dict[str, str]:
    os.environ.setdefault("AUTH_JWT_SECRET", "dev-secret-32-bytes-long-1234567890")
    token = generate_test_jwt("sse-benchmark", "admin")
    return {"Authorization": f"Bearer {token}"}


def main() -> None:
    headers = _headers()
    results = []
    for keyword in KEYWORDS:
        latencies = []
        counts = []
        for _ in range(RUNS):
            t0 = time.perf_counter()
            r = requests.post(
                f"{ROUTER_URL}/search",
                json={"keyword": keyword, "limit": 10, "role": "admin"},
                headers=headers,
                timeout=30,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            r.raise_for_status()
            latencies.append(elapsed_ms)
            counts.append(int(r.json()["result"]["count"]))
        results.append(
            {
                "keyword": keyword,
                "runs": RUNS,
                "avg_ms": round(statistics.mean(latencies), 3),
                "p95_ms": round(statistics.quantiles(latencies, n=20)[18], 3) if len(latencies) >= 20 else round(max(latencies), 3),
                "p99_ms": round(statistics.quantiles(latencies, n=100)[98], 3) if len(latencies) >= 100 else round(max(latencies), 3),
                "qps": round(1000 / statistics.mean(latencies), 2) if latencies else 0,
                "count": counts[-1] if counts else 0,
            }
        )

    with open("sse_benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
