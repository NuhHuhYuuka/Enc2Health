#!/usr/bin/env python3
"""SelAttack-epsilon on DTE `ma_benh_enc` leakage.

This experiment models a partial-revealed leakage-abuse attack against
deterministic encryption (AES-SIV/DTE) used for ICD-10 disease codes.

Attacker input:
- leakage vector L = ciphertext equality groups and their counts
- auxiliary plaintext distribution rho over ICD-10 disease codes
- epsilon = fraction of ciphertext->plaintext mappings already revealed

The attacker recovers the remaining groups by maximum likelihood under a
Poisson approximation to a multinomial histogram, with dynamic programming to
enforce one-to-one plaintext assignments.

Run:
    python3 tests/attack_sela.py

Useful env:
    ATTACK_SELA_EPSILON=0.3
    ATTACK_SELA_REVEAL_STRATEGY=top       # top | random
    ATTACK_SELA_MONGO_URI=mongodb://127.0.0.1:27017
    ATTACK_SELA_SYNTHETIC_N=10000
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from crypto.crypto.dte import DTECipher


FIELD_AD = b"field:ma_benh"
KEY_PATH = REPO_ROOT / "crypto" / "data" / "keys" / "dte_ma_benh.key"

# Project ICD-10 set from crypto/data/generate_ehr.py plus J45 from the prompt.
ICD10_LABELS = {
    "P01": "Viem phoi o tre em",
    "P02": "Sot xuat huyet Dengue tre em",
    "C01": "Tang huyet ap vo can",
    "C02": "Benh tim thieu mau cuc bo man tinh",
    "I01": "Dai thao duong Type 2",
    "I02": "Benh than man giai doan cuoi",
    "N01": "Giai doan tram cam nang",
    "N02": "Dau than kinh toa Thoat vi dia dem",
    "S01": "Viem ruot thua cap",
    "S02": "Soi tui mat",
    "E01": "Da chan thuong do tai nan",
    "E02": "Ngo doc thuc pham cap tinh",
}

# Auxiliary distribution rho. These values are intentionally external to the
# generated dataset and encode an attacker's imperfect population prior.
AUX_DISTRIBUTION = {
    "I01": 0.10, "I02": 0.08,
    "C01": 0.10, "C02": 0.08,
    "P01": 0.10, "P02": 0.08,
    "N01": 0.08, "N02": 0.08,
    "S01": 0.08, "S02": 0.07,
    "E01": 0.08, "E02": 0.07,
}

# Same disease set as the current EHR generator. Synthetic fallback samples
# uniformly because generate_ehr.py uses random.choice over ICD10_CODES.
GENERATOR_CODES = ["P01", "P02", "C01", "C02", "I01", "I02", "N01", "N02", "S01", "S02", "E01", "E02"]


@dataclass(frozen=True)
class LeakageGroup:
    ciphertext: str
    count: int
    true_plaintext: str | None = None


def normalize_distribution(dist: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, v) for v in dist.values())
    if total <= 0:
        raise ValueError("auxiliary distribution must have positive mass")
    return {k: max(0.0, v) / total for k, v in dist.items()}


def load_dte_cipher() -> DTECipher | None:
    if not KEY_PATH.exists():
        return None
    return DTECipher.load_key(str(KEY_PATH))


def ciphertext_for_code(code: str, cipher: DTECipher | None) -> str:
    if cipher is None:
        # Deterministic stand-in for offline fallback when the shared key is not
        # present. This keeps the attack algorithm runnable without Mongo/keys.
        return f"mock-dte:{code}"
    return cipher.encrypt(code, FIELD_AD)


def true_ciphertext_map(codes: list[str], cipher: DTECipher | None) -> dict[str, str]:
    return {ciphertext_for_code(code, cipher): code for code in codes}


def load_leakage_from_mongo(
    *,
    mongo_uri: str,
    db_name: str,
    collection_name: str,
    candidate_codes: list[str],
    cipher: DTECipher | None,
) -> tuple[list[LeakageGroup], str]:
    if MongoClient is None:
        raise RuntimeError("pymongo is not installed")

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
    coll = client[db_name][collection_name]
    pipeline = [
        {"$group": {"_id": "$ma_benh_enc", "count": {"$sum": 1}}},
        {"$match": {"_id": {"$ne": None}}},
        {"$sort": {"count": -1}},
    ]
    rows = list(coll.aggregate(pipeline))
    if not rows:
        raise RuntimeError("Mongo collection has no ma_benh_enc leakage groups")

    ct_to_plain = true_ciphertext_map(candidate_codes, cipher)
    groups = [
        LeakageGroup(
            ciphertext=str(row["_id"]),
            count=int(row["count"]),
            true_plaintext=ct_to_plain.get(str(row["_id"])),
        )
        for row in rows
    ]
    client.close()
    return groups, "mongo"


def load_synthetic_leakage(
    *,
    n_records: int,
    seed: int,
    candidate_codes: list[str],
    cipher: DTECipher | None,
) -> tuple[list[LeakageGroup], str]:
    rng = random.Random(seed)
    plaintexts = [rng.choice(GENERATOR_CODES) for _ in range(n_records)]
    counts = Counter(plaintexts)
    groups = [
        LeakageGroup(
            ciphertext=ciphertext_for_code(code, cipher),
            count=count,
            true_plaintext=code,
        )
        for code, count in counts.items()
    ]

    # Include only codes that are actually present in the sampled dataset, but
    # leave candidate_codes wider to preserve the attacker's uncertainty.
    valid = set(candidate_codes)
    groups = [g for g in groups if g.true_plaintext in valid]
    groups.sort(key=lambda g: g.count, reverse=True)
    return groups, "synthetic_mock"


def choose_revealed_indices(
    groups: list[LeakageGroup],
    epsilon: float,
    *,
    strategy: str,
    seed: int,
) -> set[int]:
    epsilon = min(1.0, max(0.0, epsilon))
    n_revealed = int(round(len(groups) * epsilon))
    n_revealed = min(n_revealed, len(groups))
    if n_revealed <= 0:
        return set()
    if strategy == "random":
        rng = random.Random(seed)
        return set(rng.sample(range(len(groups)), n_revealed))
    if strategy != "top":
        raise ValueError("ATTACK_SELA_REVEAL_STRATEGY must be top or random")
    return set(range(n_revealed))


def poisson_log_likelihood(count: int, code: str, total_records: int, rho: dict[str, float]) -> float:
    # Small floor avoids -inf for low-probability codes while still penalizing
    # assignments outside the attacker's prior.
    p = max(rho.get(code, 0.0), 1e-12)
    lam = max(total_records * p, 1e-12)
    return count * math.log(lam) - lam - math.lgamma(count + 1)


def mle_dp_assign(
    groups: list[LeakageGroup],
    candidate_codes: list[str],
    rho: dict[str, float],
    revealed_indices: set[int],
) -> tuple[dict[str, str], float]:
    """Return ciphertext->plaintext assignments and total log-likelihood."""
    total_records = sum(g.count for g in groups)
    assignments: dict[str, str] = {}
    revealed_plaintexts: set[str] = set()

    for idx in sorted(revealed_indices):
        true_plaintext = groups[idx].true_plaintext
        if true_plaintext is None:
            continue
        assignments[groups[idx].ciphertext] = true_plaintext
        revealed_plaintexts.add(true_plaintext)

    unknown_groups = [g for idx, g in enumerate(groups) if idx not in revealed_indices]
    free_codes = [c for c in candidate_codes if c not in revealed_plaintexts]
    if len(free_codes) < len(unknown_groups):
        raise ValueError("not enough candidate plaintexts for one-to-one assignment")

    # DP state: (i, mask) after assigning i unknown groups to selected free_codes.
    # Store best log-likelihood and predecessor for reconstruction.
    dp: dict[tuple[int, int], float] = {(0, 0): 0.0}
    parent: dict[tuple[int, int], tuple[int, int, int]] = {}

    for i, group in enumerate(unknown_groups):
        next_dp: dict[tuple[int, int], float] = {}
        for (old_i, mask), score in dp.items():
            if old_i != i:
                continue
            for code_idx, code in enumerate(free_codes):
                bit = 1 << code_idx
                if mask & bit:
                    continue
                new_mask = mask | bit
                new_state = (i + 1, new_mask)
                candidate_score = score + poisson_log_likelihood(
                    group.count,
                    code,
                    total_records,
                    rho,
                )
                if candidate_score > next_dp.get(new_state, -math.inf):
                    next_dp[new_state] = candidate_score
                    parent[new_state] = (i, mask, code_idx)
        dp = next_dp

    if not dp:
        return assignments, -math.inf

    best_state, best_score = max(dp.items(), key=lambda item: item[1])
    chosen_code_indices: list[int] = []
    state = best_state
    for i in range(len(unknown_groups), 0, -1):
        prev_i, prev_mask, code_idx = parent[state]
        chosen_code_indices.append(code_idx)
        state = (prev_i, prev_mask)
    chosen_code_indices.reverse()

    for group, code_idx in zip(unknown_groups, chosen_code_indices):
        assignments[group.ciphertext] = free_codes[code_idx]

    return assignments, best_score


def random_unique_baseline(
    groups: list[LeakageGroup],
    candidate_codes: list[str],
    revealed_indices: set[int],
    *,
    seed: int,
) -> dict[str, str]:
    rng = random.Random(seed)
    revealed_plaintexts = {
        groups[idx].true_plaintext
        for idx in revealed_indices
        if groups[idx].true_plaintext is not None
    }
    unknown_indices = [idx for idx in range(len(groups)) if idx not in revealed_indices]
    free_codes = [c for c in candidate_codes if c not in revealed_plaintexts]

    shuffled = free_codes[:]
    rng.shuffle(shuffled)
    assignment = {}
    for idx in revealed_indices:
        if groups[idx].true_plaintext is not None:
            assignment[groups[idx].ciphertext] = groups[idx].true_plaintext
    for idx, code in zip(unknown_indices, shuffled):
        assignment[groups[idx].ciphertext] = code
    return assignment


def compute_metrics(groups: list[LeakageGroup], assignment: dict[str, str]) -> dict[str, Any]:
    known_groups = [g for g in groups if g.true_plaintext is not None]
    total_groups = len(known_groups)
    total_rows = sum(g.count for g in known_groups)
    if total_groups == 0 or total_rows == 0:
        return {
            "value_recovery_rate": 0.0,
            "row_recovery_rate": 0.0,
            "correct_values": 0,
            "total_values": total_groups,
            "correct_rows": 0,
            "total_rows": total_rows,
        }

    correct_values = 0
    correct_rows = 0
    for group in known_groups:
        guessed = assignment.get(group.ciphertext)
        if guessed == group.true_plaintext:
            correct_values += 1
            correct_rows += group.count

    return {
        "value_recovery_rate": round(correct_values / total_groups, 4),
        "row_recovery_rate": round(correct_rows / total_rows, 4),
        "correct_values": correct_values,
        "total_values": total_groups,
        "correct_rows": correct_rows,
        "total_rows": total_rows,
    }


def assignment_preview(groups: list[LeakageGroup], assignment: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for group in groups:
        guessed = assignment.get(group.ciphertext)
        rows.append(
            {
                "ciphertext_prefix": group.ciphertext[:18] + "...",
                "count": group.count,
                "guess": guessed,
                "truth": group.true_plaintext,
                "correct": guessed == group.true_plaintext,
            }
        )
    return rows


def run_attack() -> dict[str, Any]:
    epsilon = float(os.environ.get("ATTACK_SELA_EPSILON", "0.3"))
    reveal_strategy = os.environ.get("ATTACK_SELA_REVEAL_STRATEGY", "top").strip().lower()
    seed = int(os.environ.get("ATTACK_SELA_SEED", "42"))
    synthetic_n = int(os.environ.get("ATTACK_SELA_SYNTHETIC_N", "10000"))
    mongo_uri = os.environ.get("ATTACK_SELA_MONGO_URI", "mongodb://127.0.0.1:27017")
    db_name = os.environ.get("MONGO_DB", "enc2health")
    collection_name = os.environ.get("MONGO_COLLECTION", "patient_records")

    rho = normalize_distribution(AUX_DISTRIBUTION)
    candidate_codes = list(rho.keys())
    cipher = load_dte_cipher()

    try:
        groups, source = load_leakage_from_mongo(
            mongo_uri=mongo_uri,
            db_name=db_name,
            collection_name=collection_name,
            candidate_codes=candidate_codes,
            cipher=cipher,
        )
    except Exception as exc:
        groups, source = load_synthetic_leakage(
            n_records=synthetic_n,
            seed=seed,
            candidate_codes=candidate_codes,
            cipher=cipher,
        )
        source = f"{source} (mongo unavailable: {exc})"

    groups = sorted(groups, key=lambda g: g.count, reverse=True)
    revealed = choose_revealed_indices(groups, epsilon, strategy=reveal_strategy, seed=seed + 7)

    sela_assignment, log_likelihood = mle_dp_assign(groups, candidate_codes, rho, revealed)
    baseline_assignment = random_unique_baseline(
        groups,
        candidate_codes,
        revealed,
        seed=seed + 99,
    )
    full_revealed_assignment = {
        group.ciphertext: group.true_plaintext
        for group in groups
        if group.true_plaintext is not None
    }

    results = {
        "attack": "SelAttack-epsilon DTE ma_benh_enc",
        "source": source,
        "epsilon": epsilon,
        "reveal_strategy": reveal_strategy,
        "n_groups": len(groups),
        "n_records": sum(g.count for g in groups),
        "candidate_plaintexts": candidate_codes,
        "aux_distribution_rho": rho,
        "revealed": [
            {
                "rank": idx + 1,
                "count": groups[idx].count,
                "plaintext": groups[idx].true_plaintext,
                "ciphertext_prefix": groups[idx].ciphertext[:18] + "...",
            }
            for idx in sorted(revealed)
        ],
        "baseline_random_unique": compute_metrics(groups, baseline_assignment),
        "sela_mle_dp": {
            **compute_metrics(groups, sela_assignment),
            "log_likelihood": round(log_likelihood, 4),
        },
        "bipartite_full_revealed_oracle": compute_metrics(groups, full_revealed_assignment),
        "assignment_preview": assignment_preview(groups, sela_assignment),
        "notes": [
            "Evaluator uses the local DTE key only to label ground truth; the attack itself uses leakage counts, rho, and epsilon revealed mappings.",
            "Poisson likelihood uses lambda = total_records * rho[plaintext].",
            "DP enforces one ciphertext group maps to at most one ICD-10 plaintext.",
        ],
    }
    return results


def print_summary(results: dict[str, Any]) -> None:
    def pct(value: float) -> str:
        return f"{100 * value:5.1f}%"

    print("=" * 72)
    print("SelAttack-epsilon on DTE ma_benh_enc")
    print("=" * 72)
    print(f"source           : {results['source']}")
    print(f"epsilon          : {results['epsilon']} ({results['reveal_strategy']})")
    print(f"groups / records : {results['n_groups']} / {results['n_records']}")
    print()
    for name in ["baseline_random_unique", "sela_mle_dp", "bipartite_full_revealed_oracle"]:
        metrics = results[name]
        print(f"[{name}]")
        print(f"  value recovery : {pct(metrics['value_recovery_rate'])}")
        print(f"  row recovery   : {pct(metrics['row_recovery_rate'])}")
        print(f"  correct values : {metrics['correct_values']}/{metrics['total_values']}")
        print()
    print("Recovered mapping preview:")
    for row in results["assignment_preview"]:
        mark = "OK" if row["correct"] else "MISS"
        print(
            f"  {mark:4} count={row['count']:5d} "
            f"guess={row['guess'] or '-':>3} truth={row['truth'] or '-':>3} "
            f"ct={row['ciphertext_prefix']}"
        )
    print("=" * 72)


def main() -> int:
    results = run_attack()
    out = REPO_ROOT / "attack_sela_results.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print_summary(results)
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
