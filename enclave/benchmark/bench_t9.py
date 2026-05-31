#!/usr/bin/env python3
"""
T9 - C_TEE Benchmark
C_TEE performance testing and comparison.
"""

from __future__ import annotations

import gc
import json
import os
import secrets
import socket
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa, padding
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

OUTPUT_JSON = Path(__file__).resolve().parent / "c_tee_metrics.json"


def _now_ns() -> int:
    return time.perf_counter_ns()


def _avg_ms(samples_ns: list[int]) -> float:
    return statistics.mean(samples_ns) / 1_000_000.0 if samples_ns else 0.0


def _p95_ms(samples_ns: list[int]) -> float:
    if not samples_ns:
        return 0.0
    ordered = sorted(samples_ns)
    index = max(0, int(round(0.95 * (len(ordered) - 1))))
    return ordered[index] / 1_000_000.0


def _rss_mb() -> float:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    return float(parts[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def _measure_socketpair_roundtrip(iterations: int = 5000) -> dict:
    left, right = socket.socketpair()
    left.settimeout(1.0)
    right.settimeout(1.0)

    try:
        warmup = b"x"
        for _ in range(100):
            left.sendall(warmup)
            right.recv(1)
            right.sendall(warmup)
            left.recv(1)

        samples_ns: list[int] = []
        payload = b"x"
        for _ in range(iterations):
            start = _now_ns()
            left.sendall(payload)
            right.recv(1)
            right.sendall(payload)
            left.recv(1)
            samples_ns.append(_now_ns() - start)

        return {
            "avg_ms": round(_avg_ms(samples_ns), 6),
            "p95_ms": round(_p95_ms(samples_ns), 6),
        }
    finally:
        left.close()
        right.close()


def _measure_rsa4096_decrypt(iterations: int = 25) -> dict:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    public_key = private_key.public_key()
    plaintext = secrets.token_bytes(190)
    ciphertext = public_key.encrypt(
        plaintext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    samples_ns: list[int] = []
    for _ in range(iterations):
        start = _now_ns()
        decrypted = private_key.decrypt(
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        samples_ns.append(_now_ns() - start)
        if decrypted != plaintext:
            raise RuntimeError("RSA-4096 decrypt verification failed")

    return {
        "avg_ms": round(_avg_ms(samples_ns), 3),
        "p95_ms": round(_p95_ms(samples_ns), 3),
    }


def _measure_ecc384_ecdh(iterations: int = 50) -> dict:
    private_key = ec.generate_private_key(ec.SECP384R1())
    peer_key = ec.generate_private_key(ec.SECP384R1())
    peer_public = peer_key.public_key()

    samples_ns: list[int] = []
    for _ in range(iterations):
        start = _now_ns()
        shared = private_key.exchange(ec.ECDH(), peer_public)
        samples_ns.append(_now_ns() - start)
        if not shared:
            raise RuntimeError("ECC P-384 ECDH produced empty shared secret")

    return {
        "avg_ms": round(_avg_ms(samples_ns), 3),
        "p95_ms": round(_p95_ms(samples_ns), 3),
    }


def _build_dataset(n_records: int) -> tuple[list[dict], float]:
    """Build a synthetic dataset and return it plus measured build latency in ms."""
    start = _now_ns()
    payload = "x" * 16384
    records = [
        {
            "record_id": i,
            "dept": f"dept-{i % 6}",
            "payload": payload,
            "nonce": secrets.token_hex(12),
        }
        for i in range(n_records)
    ]
    latency_ms = (_now_ns() - start) / 1_000_000.0
    return records, round(latency_ms, 3)


def _measure_rss_overhead() -> list[dict]:
    results: list[dict] = []
    kept_datasets: list[list[dict]] = []

    for n_records in (1000, 5000, 10000):
        gc.collect()
        start_rss = _rss_mb()
        dataset, latency_ms = _build_dataset(n_records)
        kept_datasets.append(dataset)
        current_rss = _rss_mb()
        results.append(
            {
                "n": n_records,
                "rss_mb": round(max(start_rss, current_rss), 1),
                "latency_ms": latency_ms,
            }
        )

    # Keep the dataset alive long enough for RSS measurement, then release.
    kept_datasets.clear()
    gc.collect()
    return results


def benchmark_c_tee() -> dict:
    """Benchmark the C_TEE components and emit the JSON payload for Nam."""
    print("[T9] C_TEE Benchmark")
    print("[1] C_switch (socketpair round-trip)...")
    c_switch = _measure_socketpair_roundtrip()
    print(f"  C_switch: {c_switch['avg_ms']:.6f}ms avg | P95: {c_switch['p95_ms']:.6f}ms")

    print("\n[2] C_asym RSA-4096 decrypt...")
    c_asym_rsa = _measure_rsa4096_decrypt()
    print(f"  C_asym RSA-4096: {c_asym_rsa['avg_ms']:.3f}ms avg | P95: {c_asym_rsa['p95_ms']:.3f}ms")

    print("\n[3] C_asym ECC P-384 ECDH...")
    c_asym_ecc = _measure_ecc384_ecdh()
    print(f"  C_asym ECC P-384: {c_asym_ecc['avg_ms']:.3f}ms avg | P95: {c_asym_ecc['p95_ms']:.3f}ms")

    print("\n[4] RSS overhead (dataset growth)...")
    rss_data = _measure_rss_overhead()
    for row in rss_data:
        print(f"  n={row['n']:>5} rss_mb={row['rss_mb']:.1f} latency_ms={row['latency_ms']:.3f}")

    result = {
        "mode": "gramine-direct (simulation)",
        "note": "Hardware SGX: C_switch cao hơn ~3-5x; EPC paging thay bằng RSS overhead",
        "C_switch_ms": c_switch["avg_ms"],
        "C_asym_rsa4096_ms": c_asym_rsa["avg_ms"],
        "C_asym_ecc384_ms": c_asym_ecc["avg_ms"],
        "RSS_overhead": rss_data,
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(f"\n✅ c_tee_metrics.json saved to {OUTPUT_JSON}")
    return result

if __name__ == '__main__':
    result = benchmark_c_tee()
    print(f"Result: {result}")
