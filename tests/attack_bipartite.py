#!/usr/bin/env python3
"""
Simple bipartite-linkage attack simulator (stub).

This script attempts to illustrate linkage risk on an ORE-encrypted `tuoi_enc`
column. If a real MongoDB is available it will sample ciphertext values from
`enc2health.patient_records`. If not, it will run a small simulated experiment
and print a short summary and JSON results file.

This is intentionally lightweight — replace with a more rigorous attacker model
for full evaluations.
"""
import json
import random
import sys

try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None


def analyze_ciphertexts(ciphertexts):
    total = len(ciphertexts)
    counts = {}
    for c in ciphertexts:
        counts[c] = counts.get(c, 0) + 1
    unique = sum(1 for v in counts.values() if v == 1)
    most_common = sorted(counts.items(), key=lambda x: -x[1])[:5]
    uniqueness_ratio = unique / total if total else 0.0
    return {
        "total_samples": total,
        "unique_count": unique,
        "uniqueness_ratio": uniqueness_ratio,
        "most_common": most_common,
    }


def run_against_mongo(uri="mongodb://127.0.0.1:27017", limit=500):
    client = MongoClient(uri, serverSelectionTimeoutMS=2000)
    db = client.get_database("enc2health")
    coll = db.get_collection("patient_records")
    cursor = coll.find({}, {"tuoi_enc": 1}, limit=limit)
    cts = []
    for d in cursor:
        if "tuoi_enc" in d and d["tuoi_enc"] is not None:
            # stringify to make hashable
            cts.append(str(d["tuoi_enc"]))
    return analyze_ciphertexts(cts)


def run_simulation(n=500):
    # Simulate an ORE encoding where values preserve order but are not unique.
    # For the stub we map actual ages to a deterministic pseudo-ciphertext.
    ages = [random.randint(18, 90) for _ in range(n)]
    cts = [f"ORE#{a + (random.randrange(0,3))}" for a in ages]
    return analyze_ciphertexts(cts)


def main():
    out = {"source": None}
    if MongoClient is not None:
        try:
            print("Attempting to connect to MongoDB at mongodb://127.0.0.1:27017 ...")
            res = run_against_mongo(limit=1000)
            out.update(res)
            out["source"] = "mongo_sample"
        except Exception as e:
            print("MongoDB unavailable, running simulated experiment:", e)
            res = run_simulation(500)
            out.update(res)
            out["source"] = "simulated"
    else:
        print("pymongo not installed; running simulated experiment.")
        res = run_simulation(500)
        out.update(res)
        out["source"] = "simulated"

    print("\nBipartite linkage attack (stub) summary:")
    print(json.dumps(out, indent=2))
    with open("attack_bipartite_result.json", "w") as f:
        json.dump(out, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
