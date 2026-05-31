#!/usr/bin/env python3
"""
T11 - Benchmark TEE-only 10k EHR records

Implements three queries (Q1/Q2/Q3) over a synthetic 10k EHR dataset.

Outputs `benchmark/t11_results.json` with throughput and latency stats.
"""

from __future__ import annotations

import gc
import json
import os
import random
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


OUTPUT_JSON = Path(__file__).resolve().parent / "t11_results.json"
DATASET_SIZE = int(os.environ.get("T11_DATASET_SIZE", "10000"))
PAYLOAD_BYTES = int(os.environ.get("T11_PAYLOAD_BYTES", "4096"))
DEFAULT_ITERATIONS = int(os.environ.get("T11_ITERATIONS", "100"))


def deterministic_token(key: bytes, value: str) -> str:
    h = sha256()
    h.update(key)
    h.update(value.encode("utf-8"))
    return h.hexdigest()


def make_dataset(n: int = DATASET_SIZE, seed: int = 42, payload_bytes: int = PAYLOAD_BYTES) -> Tuple[List[dict], dict]:
    random.seed(seed)
    # keys
    aes_key = AESGCM.generate_key(bit_length=128)
    det_key = sha256(b"deterministic-key" + aes_key).digest()

    ma_benh_choices = ["E11", "I10", "J45", "K21", "N18"]
    khoa_choices = ["Cap_cuu", "Ngoai", "Nhi", "Noi", "Than_kinh", "Tim_mach"]

    base_date = datetime.utcnow() - timedelta(days=365)
    payload = "x" * max(0, payload_bytes)

    records: List[dict] = []
    for i in range(n):
        ma_benh = random.choice(ma_benh_choices)
        khoa = random.choice(khoa_choices)
        tuoi = random.randint(1, 95)
        vien_phi = round(random.uniform(200.0, 10000.0), 2)
        days = random.randint(0, 364)
        ngay = (base_date + timedelta(days=days)).date()

        # AES-GCM encrypt vien_phi (store nonce + ct)
        aesgcm = AESGCM(aes_key)
        nonce = random.randbytes(12)
        pt = str(vien_phi).encode("utf-8")
        ct = aesgcm.encrypt(nonce, pt, None)

        # deterministic token for equality fields (ma_benh, khoa)
        ma_benh_tok = deterministic_token(det_key, ma_benh)
        khoa_tok = deterministic_token(det_key, khoa)

        # ORE-like encoding: store numeric values to allow range checks
        tuoi_ore = tuoi
        ngay_epoch = int(datetime(ngay.year, ngay.month, ngay.day).timestamp())

        records.append(
            {
                "id": i,
                "ma_benh_enc": ma_benh_tok,
                "khoa_enc": khoa_tok,
                "tuoi_ore": tuoi_ore,
                "ngay_epoch_ore": ngay_epoch,
                "vien_phi_ct": ct.hex(),
                "vien_phi_nonce": nonce.hex(),
                "payload": payload,
            }
        )

    meta = {"aes_key": aes_key.hex(), "det_key": det_key.hex(), "n": n}
    return records, meta


def decrypt_vien_phi(record: dict, aes_key_hex: str) -> float:
    aes_key = bytes.fromhex(aes_key_hex)
    aesgcm = AESGCM(aes_key)
    nonce = bytes.fromhex(record["vien_phi_nonce"])
    ct = bytes.fromhex(record["vien_phi_ct"])
    pt = aesgcm.decrypt(nonce, ct, None)
    return float(pt.decode("utf-8"))


def stats_from_samples(samples_ms: List[float]) -> Dict[str, float]:
    if not samples_ms:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0}
    ordered = sorted(samples_ms)
    return {
        "p50": ordered[int(0.50 * (len(ordered) - 1))],
        "p95": ordered[int(0.95 * (len(ordered) - 1))],
        "p99": ordered[int(0.99 * (len(ordered) - 1))],
        "avg": statistics.mean(ordered),
    }


def run_query_Q1(records: List[dict], meta: dict, iterations: int = 100) -> Dict[str, Any]:
    """Q1: AVG vien_phi where ma_benh==E11 and tuoi>60.
    Simulates DTE equality on `ma_benh_enc`, ORE range on `tuoi_ore`, AES-GCM decrypt cost for matched rows.
    """
    aes_key_hex = meta["aes_key"]
    # precompute token for 'E11'
    det_key = bytes.fromhex(meta["det_key"])
    target_tok = deterministic_token(det_key, "E11")

    latencies: List[float] = []
    matched_counts: List[int] = []
    total_records_processed = 0

    for _ in range(iterations):
        t0 = time.perf_counter()
        matched = []
        for r in records:
            if r["ma_benh_enc"] != target_tok:
                continue
            if r["tuoi_ore"] <= 60:
                continue
            matched.append(r)

        # decrypt matched vien_phi and compute avg
        vals = [decrypt_vien_phi(r, aes_key_hex) for r in matched]
        _avg = sum(vals) / len(vals) if vals else 0.0
        t1 = time.perf_counter()
        latency_ms = (t1 - t0) * 1000.0
        latencies.append(latency_ms)
        matched_counts.append(len(matched))
        total_records_processed += len(matched)

    total_time_s = sum(latencies) / 1000.0
    throughput = total_records_processed / total_time_s if total_time_s > 0 else 0.0

    return {
        "throughput_rec_per_s": round(throughput, 2),
        "latency_ms": stats_from_samples(latencies),
        "n_records_matched_avg": round(statistics.mean(matched_counts) if matched_counts else 0, 2),
    }


def run_query_Q2(records: List[dict], meta: dict, iterations: int = 100) -> Dict[str, Any]:
    """Q2: COUNT by khoa (DTE equality on khoa_phong_enc) — software mode (no decrypt)."""
    det_key = bytes.fromhex(meta["det_key"])
    khoa_choices = ["Cap_cuu", "Ngoai", "Nhi", "Noi", "Than_kinh", "Tim_mach"]

    latencies: List[float] = []
    matched_counts: List[int] = []
    total_records_processed = 0

    for _ in range(iterations):
        target = random.choice(khoa_choices)
        target_tok = deterministic_token(det_key, target)
        t0 = time.perf_counter()
        cnt = 0
        for r in records:
            if r["khoa_enc"] == target_tok:
                cnt += 1
        t1 = time.perf_counter()
        latency_ms = (t1 - t0) * 1000.0
        latencies.append(latency_ms)
        matched_counts.append(cnt)
        total_records_processed += cnt

    total_time_s = sum(latencies) / 1000.0
    throughput = total_records_processed / total_time_s if total_time_s > 0 else 0.0

    return {
        "throughput_rec_per_s": round(throughput, 2),
        "latency_ms": stats_from_samples(latencies),
        "n_records_matched_avg": round(statistics.mean(matched_counts) if matched_counts else 0, 2),
    }


def run_query_Q3(records: List[dict], meta: dict, iterations: int = 100) -> Dict[str, Any]:
    """Q3: SUM vien_phi for records in a month range (ORE on ngay_epoch_ore + AES-GCM decrypt)."""
    aes_key_hex = meta["aes_key"]
    # pick random month windows
    latencies: List[float] = []
    matched_counts: List[int] = []
    total_records_processed = 0

    # compute min/max epoch in dataset
    epochs = [r["ngay_epoch_ore"] for r in records]
    lo, hi = min(epochs), max(epochs)

    for _ in range(iterations):
        # choose random start epoch, window ~30 days
        start = random.randint(lo, hi - 1)
        end = start + 30 * 24 * 3600
        t0 = time.perf_counter()
        matched = [r for r in records if start <= r["ngay_epoch_ore"] <= end]
        s = 0.0
        for r in matched:
            s += decrypt_vien_phi(r, aes_key_hex)
        t1 = time.perf_counter()
        latency_ms = (t1 - t0) * 1000.0
        latencies.append(latency_ms)
        matched_counts.append(len(matched))
        total_records_processed += len(matched)

    total_time_s = sum(latencies) / 1000.0
    throughput = total_records_processed / total_time_s if total_time_s > 0 else 0.0

    return {
        "throughput_rec_per_s": round(throughput, 2),
        "latency_ms": stats_from_samples(latencies),
        "n_records_matched_avg": round(statistics.mean(matched_counts) if matched_counts else 0, 2),
    }


def benchmark_t11(iterations: int = DEFAULT_ITERATIONS, dataset_size: int = DATASET_SIZE, payload_bytes: int = PAYLOAD_BYTES) -> Dict[str, Any]:
    print(f"[T11] Building synthetic dataset ({dataset_size:,} records | payload={payload_bytes} bytes)")
    records, meta = make_dataset(dataset_size, seed=12345, payload_bytes=payload_bytes)
    gc.collect()

    print("[T11] Running Q1 (AVG vien_phi where ma_benh=E11 and tuoi>60)")
    q1 = run_query_Q1(records, meta, iterations=iterations)

    print("[T11] Running Q2 (COUNT by khoa — software mode)")
    q2 = run_query_Q2(records, meta, iterations=iterations)

    print("[T11] Running Q3 (SUM vien_phi by month window)")
    q3 = run_query_Q3(records, meta, iterations=iterations)

    result = {
        "mode": "simulation (deterministic-token DTE + AES-GCM decrypt)",
        "n_dataset": len(records),
        "dataset_size_requested": dataset_size,
        "payload_bytes": payload_bytes,
        "Q1_avg_vien_phi": q1,
        "Q2_count_khoa": q2,
        "Q3_sum_vien_phi_by_month": q3,
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ t11_results.json saved to {OUTPUT_JSON}")
    return result


if __name__ == "__main__":
    res = benchmark_t11()
    print(json.dumps(res, indent=2, ensure_ascii=False))
