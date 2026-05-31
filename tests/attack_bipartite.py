#!/usr/bin/env python3
"""
Bipartite linkage attack evaluation for ORE age ciphertexts.

This script provides a non-trivial, reproducible experiment:
1) Synthetic experiment with known plaintext ages and simulated order-preserving
   ciphertexts; attacker performs rank/quantile linkage from ciphertext order.
2) Optional real-Mongo leakage profile (when Mongo is available): reports
   uniqueness/rank leakage of `tuoi_enc` without requiring plaintext ages.

Outputs:
- attack_results.json
- attack_chart.png (if matplotlib is available)
"""

from __future__ import annotations

import json
import math
import os
import random
from bisect import bisect_right
from collections import Counter
from pathlib import Path
from typing import Iterable

try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None


def sample_age_distribution(n: int, seed: int = 42) -> list[int]:
    """Generate a realistic-ish age distribution for attack simulation."""
    rng = random.Random(seed)
    ages: list[int] = []
    for _ in range(n):
        p = rng.random()
        if p < 0.12:
            age = rng.randint(18, 30)
        elif p < 0.42:
            age = rng.randint(31, 45)
        elif p < 0.78:
            age = rng.randint(46, 65)
        else:
            age = rng.randint(66, 90)
        ages.append(age)
    return ages


def ore_encrypt_sim(age: int, secret_offset: int = 913_271) -> int:
    """Deterministic order-preserving stand-in for ORE ciphertext values."""
    # Monotonic mapping: preserves order exactly.
    return age * 10_000 + secret_offset


def empirical_cdf(values: Iterable[int]) -> tuple[list[int], list[float]]:
    counts = Counter(values)
    keys = sorted(counts.keys())
    total = float(sum(counts.values()) or 1)
    probs: list[float] = []
    c = 0.0
    for k in keys:
        c += counts[k] / total
        probs.append(c)
    return keys, probs


def quantile_to_age(q: float, keys: list[int], cdf: list[float]) -> int:
    idx = bisect_right(cdf, q)
    if idx <= 0:
        return keys[0]
    if idx >= len(keys):
        return keys[-1]
    return keys[idx]


def attack_rank_linkage(ciphertexts: list[int], aux_plain_ages: list[int]) -> list[int]:
    """Infer ages from ciphertext rank + auxiliary plaintext distribution."""
    keys, cdf = empirical_cdf(aux_plain_ages)

    order = sorted(range(len(ciphertexts)), key=lambda i: ciphertexts[i])
    inferred = [0] * len(ciphertexts)
    n = len(ciphertexts)

    for rank, original_idx in enumerate(order, start=1):
        q = rank / (n + 1.0)
        inferred[original_idx] = quantile_to_age(q, keys, cdf)

    return inferred


def compute_recovery_metrics(true_ages: list[int], inferred_ages: list[int]) -> dict:
    n = len(true_ages)
    if n == 0:
        return {
            "n": 0,
            "exact_recovery_rate": 0.0,
            "within_2_years_rate": 0.0,
            "mae": 0.0,
        }

    exact = sum(1 for t, i in zip(true_ages, inferred_ages) if t == i)
    near2 = sum(1 for t, i in zip(true_ages, inferred_ages) if abs(t - i) <= 2)
    mae = sum(abs(t - i) for t, i in zip(true_ages, inferred_ages)) / n

    return {
        "n": n,
        "exact_recovery_rate": round(exact / n, 4),
        "within_2_years_rate": round(near2 / n, 4),
        "mae": round(mae, 4),
    }


def run_synthetic_attack(n: int = 5000, seed: int = 42) -> dict:
    true_ages = sample_age_distribution(n, seed=seed)

    # Attacker sees only ciphertexts produced by an order-preserving scheme.
    cts = [ore_encrypt_sim(a) for a in true_ages]

    # Auxiliary data: external demographic age distribution (different seed).
    aux = sample_age_distribution(n * 2, seed=seed + 99)

    inferred = attack_rank_linkage(cts, aux)
    metrics = compute_recovery_metrics(true_ages, inferred)
    metrics["experiment"] = "synthetic_rank_linkage"
    metrics["ore_order_leakage"] = True
    return metrics


def run_mongo_leakage_profile(uri: str = "mongodb://127.0.0.1:27017") -> dict:
    if MongoClient is None:
        return {"available": False, "reason": "pymongo missing"}

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        coll = client["enc2health"]["patient_records"]
        docs = list(coll.find({}, {"tuoi_enc": 1}, limit=10_000))
        cts = [str(d.get("tuoi_enc")) for d in docs if d.get("tuoi_enc") is not None]
        n = len(cts)
        if n == 0:
            return {"available": True, "n": 0, "note": "no tuoi_enc records"}

        counts = Counter(cts)
        unique = sum(1 for v in counts.values() if v == 1)
        top = counts.most_common(10)

        return {
            "available": True,
            "n": n,
            "unique_ratio": round(unique / n, 4),
            "top_ciphertext_frequencies": top,
            "note": "real plaintext age labels unavailable; reporting leakage profile only",
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def maybe_render_chart(results: dict, png_path: Path) -> bool:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return False

    syn = results.get("synthetic_attack", {})
    exact = syn.get("exact_recovery_rate", 0.0)
    near2 = syn.get("within_2_years_rate", 0.0)
    mae = syn.get("mae", 0.0)

    plt.figure(figsize=(8, 4))
    plt.subplot(1, 2, 1)
    plt.bar(["exact", "within±2y"], [exact, near2])
    plt.ylim(0, 1)
    plt.title("Recovery rates")

    plt.subplot(1, 2, 2)
    plt.bar(["MAE"], [mae])
    plt.title("Mean absolute error")

    plt.tight_layout()
    plt.savefig(png_path)
    plt.close()
    return True


def main() -> int:
    n = int(os.environ.get("ATTACK_SYNTHETIC_N", "5000"))
    seed = int(os.environ.get("ATTACK_SYNTHETIC_SEED", "42"))

    synthetic = run_synthetic_attack(n=n, seed=seed)
    mongo_profile = run_mongo_leakage_profile()

    results = {
        "synthetic_attack": synthetic,
        "mongo_leakage_profile": mongo_profile,
        "conclusion": "Order leakage enables rank-based linkage; strict TEE path avoids exposing plaintext ages.",
    }

    out_json = Path("attack_results.json")
    out_png = Path("attack_chart.png")
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    chart_ok = maybe_render_chart(results, out_png)

    print("Bipartite attack evaluation complete")
    print(json.dumps(results, indent=2))
    print(f"Wrote: {out_json}")
    if chart_ok:
        print(f"Wrote: {out_png}")
    else:
        print("Skipped chart generation (matplotlib unavailable)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
