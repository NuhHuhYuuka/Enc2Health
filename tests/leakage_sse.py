"""Leakage profile for the static SSE keyword index.

Static SSE intentionally leaks:
- search pattern: same keyword -> same HMAC token
- volume: `n_records` per token
- access pattern: decrypted posting ids at Router side
"""

from __future__ import annotations

import json
import math
import os

from pymongo import MongoClient


MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DB = os.environ.get("MONGO_DB", "enc2health")
SSE_COLLECTION = os.environ.get("SSE_COLLECTION", "sse_index")


def entropy(values: list[int]) -> float:
    total = sum(values)
    if total <= 0:
        return 0.0
    score = 0.0
    for value in values:
        if value <= 0:
            continue
        p = value / total
        score -= p * math.log2(p)
    return score


def main() -> None:
    col = MongoClient(MONGO_URI)[MONGO_DB][SSE_COLLECTION]
    rows = list(col.find({}, {"_id": 0, "token": 1, "n_records": 1}).sort("n_records", -1))
    volumes = [int(row.get("n_records", 0)) for row in rows]
    top = rows[:10]
    result = {
        "available": bool(rows),
        "n_keywords": len(rows),
        "total_posting_refs": sum(volumes),
        "volume_entropy": round(entropy(volumes), 4),
        "max_volume": max(volumes) if volumes else 0,
        "top_token_volumes": [[row["token"], int(row.get("n_records", 0))] for row in top],
        "leakage": {
            "search_pattern": "deterministic HMAC token repeats for repeated keyword",
            "volume": "n_records is stored per keyword token",
            "access_pattern": "matching posting ids are available after Router decrypts postings",
        },
    }
    with open("sse_leakage_results.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
