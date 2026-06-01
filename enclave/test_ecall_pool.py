#!/usr/bin/env python3
"""
T8 - ECALL Task Pool Test Client
Tests concurrent medical queries against the ECALL pool service.
"""

import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

import requests

# Configuration
POOL_PORT = int(os.environ.get("T8_POOL_PORT", "9091"))
POOL_URL = os.environ.get("T8_POOL_URL", f"http://127.0.0.1:{POOL_PORT}")
NUM_CONCURRENT_REQUESTS = 10
TIMEOUT_SEC = 30

# Test cases
TEST_QUERIES = [
    {
        "name": "avg_vien_phi_all",
        "query": {
            "query_type": "avg_vien_phi",
            "filters": {},
            "role": "doctor"
        }
    },
    {
        "name": "sum_vien_phi_E11",
        "query": {
            "query_type": "sum_vien_phi",
            "filters": {"ma_benh": "E11"},
            "role": "doctor"
        }
    },
    {
        "name": "count_adults",
        "query": {
            "query_type": "count",
            "filters": {"tuoi_min_enc": 30},
            "role": "admin_staff"
        }
    },
]


def send_query(query_dict: Dict, request_id: int) -> Dict:
    """Send a single query to the pool."""
    try:
        response = requests.post(
            f"{POOL_URL}/query",
            json=query_dict,
            timeout=TIMEOUT_SEC
        )
        response.raise_for_status()
        result = response.json()
        result["request_id"] = request_id
        result["status"] = "ok"
        return result
    
    except requests.exceptions.Timeout:
        return {
            "request_id": request_id,
            "status": "timeout",
            "error": "Query exceeded timeout"
        }
    
    except requests.exceptions.RequestException as e:
        return {
            "request_id": request_id,
            "status": "error",
            "error": str(e)
        }
    
    except Exception as e:
        return {
            "request_id": request_id,
            "status": "error",
            "error": f"Unexpected: {e}"
        }


def health_check() -> bool:
    """Check if the pool service is healthy."""
    try:
        response = requests.get(f"{POOL_URL}/health", timeout=5)
        response.raise_for_status()
        data = response.json()
        
        print("[Test] Health Check:")
        print(f"  Status: {data['status']}")
        print(f"  Workers: {data['workers']}")
        print(f"  Keys loaded: {data['keys_loaded']}")
        print(f"  Uptime: {data['uptime_s']:.1f}s")
        print()
        
        return data['status'] == 'ok'
    
    except Exception as e:
        print(f"[Error] Health check failed: {e}")
        return False


def test_sequential() -> bool:
    """Test sequential queries."""
    print("=" * 70)
    print("TEST 1: Sequential Queries")
    print("=" * 70)
    print()
    
    results = []
    for test_case in TEST_QUERIES:
        print(f"[Test] Running: {test_case['name']}")
        result = send_query(test_case['query'], 0)
        
        if result['status'] == 'ok':
            print(f"  ✓ Result: {result['result']:.2f} ({result['n_records']} records, {result['latency_ms']:.1f}ms)")
            results.append(result)
        else:
            print(f"  ✗ Failed: {result.get('error', 'unknown error')}")
        
        print()
    
    return len(results) == len(TEST_QUERIES)


def test_concurrent(num_requests: int = NUM_CONCURRENT_REQUESTS) -> bool:
    """Test concurrent queries."""
    print("=" * 70)
    print(f"TEST 2: Concurrent Queries ({num_requests} requests)")
    print("=" * 70)
    print()
    
    query = TEST_QUERIES[0]['query']  # Use avg_vien_phi query
    
    t_start = time.perf_counter()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_requests) as executor:
        futures = [
            executor.submit(send_query, query, i)
            for i in range(num_requests)
        ]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    
    t_end = time.perf_counter()
    total_time = t_end - t_start
    
    # Analyze results
    successful = sum(1 for r in results if r['status'] == 'ok')
    failed = sum(1 for r in results if r['status'] != 'ok')
    avg_latency = sum(r.get('latency_ms', 0) for r in results if r['status'] == 'ok') / max(successful, 1)
    
    print(f"[Test] Total time: {total_time:.2f}s")
    print(f"[Test] Successful: {successful}/{num_requests}")
    print(f"[Test] Failed: {failed}/{num_requests}")
    print(f"[Test] Avg latency: {avg_latency:.1f}ms")
    print()
    
    if successful == num_requests:
        print("✅ All concurrent requests succeeded")
        return True
    else:
        print(f"⚠ {failed} request(s) failed")
        return False


def test_role_access() -> bool:
    """Test role-based access control."""
    print("=" * 70)
    print("TEST 3: Role-Based Access Control")
    print("=" * 70)
    print()
    
    roles = ["doctor", "admin", "admin_staff"]
    results = []
    
    for role in roles:
        query = {
            "query_type": "count",
            "filters": {},
            "role": role
        }
        
        print(f"[Test] Role: {role}")
        result = send_query(query, 0)
        
        if result['status'] == 'ok':
            print(f"  ✓ Allowed ({result['n_records']} records)")
            results.append(result)
        else:
            print(f"  ✗ Denied: {result.get('error', 'unknown error')}")
        
        print()
    
    return len(results) == len(roles)


def test_invalid_role() -> bool:
    """Test invalid role rejection."""
    print("=" * 70)
    print("TEST 4: Invalid Role Rejection")
    print("=" * 70)
    print()
    
    query = {
        "query_type": "count",
        "filters": {},
        "role": "invalid_role"
    }
    
    print(f"[Test] Attempting role: 'invalid_role'")
    result = send_query(query, 0)
    
    if result['status'] != 'ok':
        print(f"  ✓ Correctly rejected: {result.get('error', 'unknown error')}")
        print()
        return True
    else:
        print(f"  ✗ Should have been rejected")
        print()
        return False


def main():
    """Run all tests."""
    print()
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 15 + "T8 - ECALL Task Pool Test Suite" + " " * 23 + "║")
    print("╚" + "=" * 68 + "╝")
    print()
    
    # Check if service is running
    print("[Startup] Connecting to pool service...")
    try:
        requests.get(f"{POOL_URL}/health", timeout=2)
        print(f"✓ Connected to {POOL_URL}")
        print()
    except:
        print(f"✗ Could not connect to {POOL_URL}")
        print("  Please start the service first:")
        print("    python3 enclave/ecall_pool.py")
        print()
        return 1
    
    # Run tests
    tests = [
        ("Health Check", health_check),
        ("Sequential Queries", test_sequential),
        ("Concurrent Queries", lambda: test_concurrent(NUM_CONCURRENT_REQUESTS)),
        ("Role-Based Access", test_role_access),
        ("Invalid Role Rejection", test_invalid_role),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            passed = test_func()
            results.append((test_name, passed))
        except Exception as e:
            print(f"[Error] {test_name} failed: {e}")
            results.append((test_name, False))
        
        print()
    
    # Summary
    print("=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print()
    
    passed = sum(1 for _, p in results if p)
    total = len(results)
    
    for test_name, passed_test in results:
        status = "✓ PASS" if passed_test else "✗ FAIL"
        print(f"{status:8} {test_name}")
    
    print()
    print(f"Result: {passed}/{total} tests passed")
    print()
    
    if passed == total:
        print("🎉 All tests passed!")
        return 0
    else:
        print(f"⚠ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
