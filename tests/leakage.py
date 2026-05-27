# T13 - Đo q-leakage khi fallback Software vs TEE mode
import httpx
import json
import collections
import math

ROUTER_URL = "http://localhost:8000"

# Simulate nhiều queries với các filter khác nhau
QUERY_PATTERNS = [
    {"ma_benh": "DTE001"},
    {"ma_benh": "DTE001"},
    {"ma_benh": "DTE001"},  # DTE001 xuất hiện nhiều → frequency leak
    {"ma_benh": "DTE002"},
    {"ma_benh": "DTE002"},
    {"ma_benh": "DTE003"},
    {"ma_benh": "DTE004"},
    {},  # no filter
    {},
]

def run_queries_and_collect(role: str, query_type: str) -> list:
    """Gửi nhiều queries, ghi lại access pattern."""
    access_log = []
    for filters in QUERY_PATTERNS:
        r = httpx.post(f"{ROUTER_URL}/query", json={
            "query_type": query_type,
            "filters": filters,
            "role": role
        })
        if r.status_code == 200:
            data = r.json()
            access_log.append({
                "mode": data["mode"],
                "filters": filters,
                "n_records": data["result"].get("n_records", 0),
                "result_masked": data["result"].get("result") == "[MASKED]",
            })
    return access_log

def compute_frequency_leakage(access_log: list) -> dict:
    """
    Tính frequency leakage:
    - Đếm tần suất mỗi filter pattern
    - Entropy thấp → leakage cao
    """
    counter = collections.Counter()
    for entry in access_log:
        key = str(sorted(entry["filters"].items()))
        counter[key] += 1

    total = sum(counter.values())
    # Shannon entropy
    entropy = 0.0
    for count in counter.values():
        p = count / total
        entropy -= p * math.log2(p)

    max_entropy = math.log2(len(counter)) if len(counter) > 1 else 1
    leakage_score = 1.0 - (entropy / max_entropy) if max_entropy > 0 else 0.0

    return {
        "pattern_counts": dict(counter),
        "entropy": round(entropy, 4),
        "max_entropy": round(max_entropy, 4),
        "leakage_score": round(leakage_score, 4),
        "interpretation": (
            "HIGH leakage" if leakage_score > 0.5
            else "MEDIUM leakage" if leakage_score > 0.2
            else "LOW leakage"
        )
    }

if __name__ == "__main__":
    print("=" * 60)
    print("Q-LEAKAGE ANALYSIS — Enc2Health")
    print("=" * 60)

    results = {}

    # 1. TEE mode (admin - sum) → leakage thấp vì kết quả trong enclave
    print("\n[1] TEE mode — admin sum_vien_phi")
    log_tee = run_queries_and_collect("admin", "sum_vien_phi")
    leak_tee = compute_frequency_leakage(log_tee)
    results["TEE_mode_admin"] = leak_tee
    print(f"  Leakage score: {leak_tee['leakage_score']} ({leak_tee['interpretation']})")
    print(f"  Entropy: {leak_tee['entropy']} / {leak_tee['max_entropy']}")

    # 2. Software mode (admin - count) → frequency leak cao hơn
    print("\n[2] SOFTWARE mode — admin count")
    log_soft = run_queries_and_collect("admin", "count")
    leak_soft = compute_frequency_leakage(log_soft)
    results["SOFTWARE_mode_admin"] = leak_soft
    print(f"  Leakage score: {leak_soft['leakage_score']} ({leak_soft['interpretation']})")
    print(f"  Entropy: {leak_soft['entropy']} / {leak_soft['max_entropy']}")

    # 3. Researcher (masked) → leakage từ access pattern dù result bị mask
    print("\n[3] Researcher (RBAC masked) — avg_vien_phi")
    log_res = run_queries_and_collect("researcher", "avg_vien_phi")
    leak_res = compute_frequency_leakage(log_res)
    results["TEE_mode_researcher_masked"] = leak_res
    print(f"  Leakage score: {leak_res['leakage_score']} ({leak_res['interpretation']})")
    print(f"  Entropy: {leak_res['entropy']} / {leak_res['max_entropy']}")

    # So sánh
    print("\n" + "=" * 60)
    print("SO SÁNH LEAKAGE SCORE:")
    print(f"  TEE mode (admin):        {results['TEE_mode_admin']['leakage_score']}")
    print(f"  SOFTWARE mode (admin):   {results['SOFTWARE_mode_admin']['leakage_score']}")
    print(f"  TEE + RBAC (researcher): {results['TEE_mode_researcher_masked']['leakage_score']}")
    print("=" * 60)
    print("\nKết luận: TEE mode bảo vệ nội dung kết quả,")
    print("nhưng access pattern (filter frequency) vẫn bị leak ở cả 2 mode.")

    with open("leakage_results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("\nĐã lưu vào leakage_results.json")
