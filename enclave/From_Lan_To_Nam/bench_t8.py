#!/usr/bin/env python3
"""
Copy of benchmark/bench_t8.py for handoff to Nam
"""

# Full bench_t8.py copied for handoff

import concurrent.futures
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List

import requests

# Configuration
POOL_URL = "http://localhost:9091"
NUM_ITERATIONS = [1, 5, 10, 20]  # Test with varying concurrent requests
TIMEOUT_SEC = 30
OUTPUT_CSV = Path(__file__).resolve().parent.parent / "benchmark" / "t8_results.csv"


def run_concurrent_benchmark(num_requests: int) -> Dict:
    """
    Benchmark concurrent queries.
    
    Returns:
        {
            'num_requests': int,
            'total_time_s': float,
            'avg_latency_ms': float,
            'min_latency_ms': float,
            'max_latency_ms': float,
            'throughput_rps': float,
            'success_count': int,
            'error_count': int,
        }
    """
    query = {
        "query_type": "avg_vien_phi",
        "filters": {},
        "role": "doctor"
    }
    
    def send_query(req_id: int):
        try:
            response = requests.post(
                f"{POOL_URL}/query",
                json=query,
                timeout=TIMEOUT_SEC
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    # Warm-up (single request)
    try:
        send_query(0)
    except:
        pass
    
    # Benchmark
    t_start = time.perf_counter()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_requests) as executor:
        futures = [executor.submit(send_query, i) for i in range(num_requests)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    
    t_end = time.perf_counter()
    total_time = t_end - t_start
    
    # Analyze
    latencies = [r.get('latency_ms', 0) for r in results if 'latency_ms' in r]
    successes = sum(1 for r in results if 'result' in r)
    errors = sum(1 for r in results if 'error' in r)
    
    avg_latency = statistics.mean(latencies) if latencies else 0
    min_latency = min(latencies) if latencies else 0
    max_latency = max(latencies) if latencies else 0
    throughput_rps = num_requests / total_time if total_time > 0 else 0
    
    return {
        'num_requests': num_requests,
        'total_time_s': total_time,
        'avg_latency_ms': avg_latency,
        'min_latency_ms': min_latency,
        'max_latency_ms': max_latency,
        'throughput_rps': throughput_rps,
        'success_count': successes,
        'error_count': errors,
    }


def benchmark_query_types() -> Dict:
    """Benchmark different query types."""
    queries = [
        {"name": "avg_vien_phi", "query_type": "avg_vien_phi"},
        {"name": "sum_vien_phi", "query_type": "sum_vien_phi"},
        {"name": "count", "query_type": "count"},
    ]
    
    results = {}
    
    for q in queries:
        query = {
            "query_type": q['query_type'],
            "filters": {},
            "role": "doctor"
        }
        
        latencies = []
        for _ in range(10):
            try:
                response = requests.post(f"{POOL_URL}/query", json=query, timeout=TIMEOUT_SEC)
                if response.status_code == 200:
                    latencies.append(response.json()['latency_ms'])
            except:
                pass
        
        avg_latency = statistics.mean(latencies) if latencies else 0
        results[q['name']] = {
            'avg_latency_ms': avg_latency,
            'samples': len(latencies)
        }
    
    return results


def main():
    """Run benchmark suite."""
    print()
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 18 + "T8 - ECALL Task Pool Benchmark" + " " * 20 + "║")
    print("╚" + "=" * 68 + "╝")
    print()
    
    # Check service
    print("[Startup] Connecting to pool service...")
    try:
        response = requests.get(f"{POOL_URL}/health", timeout=2)
        if response.status_code != 200:
            raise Exception("Service not ready")
        print(f"✓ Connected to {POOL_URL}")
        print()
    except:
        print(f"✗ Could not connect to {POOL_URL}")
        print("  Start the service first: python3 enclave/ecall_pool.py")
        return 1
    
    # Benchmark 1: Query types
    print("=" * 70)
    print("BENCHMARK 1: Query Types (10 samples each)")
    print("=" * 70)
    print()
    
    type_results = benchmark_query_types()
    for query_type, metrics in type_results.items():
        print(f"{query_type:20} {metrics['avg_latency_ms']:8.2f} ms  ({metrics['samples']} samples)")
    
    print()
    
    # Benchmark 2: Concurrent requests
    print("=" * 70)
    print("BENCHMARK 2: Concurrent Requests")
    print("=" * 70)
    print()
    
    concurrent_results = []
    for num_req in NUM_ITERATIONS:
        print(f"Testing {num_req} concurrent request(s)...")
        result = run_concurrent_benchmark(num_req)
        concurrent_results.append(result)
        
        print(f"  Total time: {result['total_time_s']:.2f}s")
        print(f"  Throughput: {result['throughput_rps']:.2f} req/s")
        print(f"  Latency: {result['avg_latency_ms']:.1f}ms (min={result['min_latency_ms']:.1f}, max={result['max_latency_ms']:.1f})")
        print(f"  Success: {result['success_count']}/{result['num_requests']}")
        print()
    
    # Save results
    print("=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print()
    
    # CSV output
    csv_header = "num_requests,total_time_s,avg_latency_ms,min_latency_ms,max_latency_ms,throughput_rps,success_count,error_count\n"
    csv_lines = [csv_header]
    
    for result in concurrent_results:
        csv_lines.append(
            f"{result['num_requests']},"
            f"{result['total_time_s']:.3f},"
            f"{result['avg_latency_ms']:.2f},"
            f"{result['min_latency_ms']:.2f},"
            f"{result['max_latency_ms']:.2f},"
            f"{result['throughput_rps']:.2f},"
            f"{result['success_count']},"
            f"{result['error_count']}\n"
        )
    
    # Write to CSV
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, 'w') as f:
        f.writelines(csv_lines)
    
    print(f"Results saved to: {OUTPUT_CSV}")
    print()
    print("CSV content:")
    print(csv_header, end='')
    for line in csv_lines[1:]:
        print(line, end='')
    print()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
