# T11 - Benchmark 3 chế độ: Software-only / TEE-only / Hybrid Adaptive
import httpx
import time
import json
import statistics

ROUTER_URL = "http://localhost:8000"
RUNS = 50  # số lần lặp mỗi query

def run_query(query_type: str, role: str, filters: dict = {}) -> float:
    """Chạy 1 query, trả về latency ms."""
    t0 = time.perf_counter()
    r = httpx.post(f"{ROUTER_URL}/query", json={
        "query_type": query_type,
        "filters": filters,
        "role": role
    })
    elapsed = (time.perf_counter() - t0) * 1000
    if r.status_code != 200:
        return -1.0
    return elapsed

def benchmark(label: str, query_type: str, role: str, filters: dict = {}):
    """Chạy RUNS lần, tính avg/p95/p99."""
    print(f"\n[Benchmark] {label} ...")
    latencies = []
    for _ in range(RUNS):
        ms = run_query(query_type, role, filters)
        if ms > 0:
            latencies.append(ms)

    latencies.sort()
    avg = statistics.mean(latencies)
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]
    qps = 1000 / avg

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
        "Hybrid Adaptive: avg_vien_phi (filtered DTE001)",
        query_type="avg_vien_phi",
        role="admin",
        filters={"ma_benh": "DTE001"}
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
