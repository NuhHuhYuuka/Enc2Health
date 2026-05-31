"""T13b — Tấn công rò rỉ THỨ TỰ của ORE/OPE (cột `tuoi_enc`).

Bối cảnh: ORE (Order-Revealing/Order-Preserving Encryption) giữ nguyên thứ tự để
hỗ trợ truy vấn range trên ciphertext. Đánh đổi: bản mã **lộ thứ tự**. Một kẻ tấn
công honest-but-curious chỉ cần:
  - Sắp xếp các ciphertext `tuoi_enc` (làm được vì ORE bảo toàn thứ tự).
  - Dùng KIẾN THỨC NỀN về phân bố tuổi (hoặc vài cặp tuổi↔ciphertext đã biết) để
    ánh xạ rank → tuổi ước lượng.

Demo 2 kịch bản tấn công + đo độ phục hồi (giống `attack_bipartite.py`):
  A. Chỉ biết PHÂN BỐ (uniform 1–95 như generate_ehr) → quantile mapping.
  B. Biết vài cặp known-plaintext anchor → nội suy đơn điệu.

Kết luận kỳ vọng: ORE KHÔNG che được thứ tự → tuổi bị khôi phục với sai số nhỏ,
đặc biệt khi có anchor. Đây là rò rỉ CÓ CHỦ ĐÍCH của searchable encryption, cần
nêu rõ trong đánh giá an ninh (xem docs/LIMITATIONS.md §4).

Chạy:  python3 tests/attack_ore.py   (offline, KHÔNG cần MongoDB)
"""
from __future__ import annotations

import json
import random
import sys
from bisect import bisect_left
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crypto.crypto.ore import ORECipher

# Cùng phân bố với generate_ehr.py: random.randint(1, 95)
AGE_MIN, AGE_MAX = 1, 95
N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
SEED = 42


def _metrics(true_ages: list[int], est_ages: list[int]) -> dict:
    n = len(true_ages)
    exact = sum(1 for t, e in zip(true_ages, est_ages) if e == t)
    within2 = sum(1 for t, e in zip(true_ages, est_ages) if abs(e - t) <= 2)
    mae = sum(abs(e - t) for t, e in zip(true_ages, est_ages)) / n
    return {
        "exact_recovery_rate": round(exact / n, 4),
        "within_2_years_rate": round(within2 / n, 4),
        "mae_years": round(mae, 4),
    }


def attack_distribution_only(ciphertexts: list[int], true_ages: list[int]) -> dict:
    """A — Kẻ tấn công chỉ biết phân bố tuổi (uniform [AGE_MIN, AGE_MAX]).

    Sắp xếp ciphertext, ánh xạ rank → quantile của phân bố đã biết.
    """
    order = sorted(range(len(ciphertexts)), key=lambda i: ciphertexts[i])
    est = [0] * len(ciphertexts)
    n = len(ciphertexts)
    for rank, idx in enumerate(order):
        # Quantile (rank+0.5)/n của Uniform{AGE_MIN..AGE_MAX}
        q = (rank + 0.5) / n
        est[idx] = round(AGE_MIN + (AGE_MAX - AGE_MIN) * q)
    return _metrics(true_ages, est)


def attack_known_plaintext(ciphertexts: list[int], true_ages: list[int], n_anchors: int = 10) -> dict:
    """B — Kẻ tấn công biết `n_anchors` cặp (tuổi, ciphertext) thật.

    Nội suy đơn điệu: với mỗi ciphertext, tìm 2 anchor bao quanh → nội suy tuyến tính.
    """
    rng = random.Random(SEED + 1)
    anchor_idx = rng.sample(range(len(ciphertexts)), min(n_anchors, len(ciphertexts)))
    anchors = sorted((ciphertexts[i], true_ages[i]) for i in anchor_idx)
    a_ct = [c for c, _ in anchors]
    a_age = [a for _, a in anchors]

    est = []
    for c in ciphertexts:
        j = bisect_left(a_ct, c)
        if j <= 0:
            est.append(a_age[0])
        elif j >= len(a_ct):
            est.append(a_age[-1])
        else:
            c0, c1 = a_ct[j - 1], a_ct[j]
            g0, g1 = a_age[j - 1], a_age[j]
            frac = 0 if c1 == c0 else (c - c0) / (c1 - c0)
            est.append(round(g0 + (g1 - g0) * frac))
    return _metrics(true_ages, est)


def baseline_random(true_ages: list[int]) -> dict:
    """Baseline: đoán ngẫu nhiên theo phân bố (không dùng ciphertext)."""
    rng = random.Random(SEED + 2)
    est = [rng.randint(AGE_MIN, AGE_MAX) for _ in true_ages]
    return _metrics(true_ages, est)


def main():
    print("=" * 64)
    print("TẤN CÔNG RÒ RỈ THỨ TỰ — ORE/OPE (tuoi_enc)")
    print(f"N = {N} bản ghi | phân bố tuổi Uniform[{AGE_MIN},{AGE_MAX}]")
    print("=" * 64)

    rng = random.Random(SEED)
    true_ages = [rng.randint(AGE_MIN, AGE_MAX) for _ in range(N)]

    ore = ORECipher()
    ciphertexts = [ore.encrypt_age(a) for a in true_ages]

    # Sanity: ORE thật sự bảo toàn thứ tự
    assert all(
        (ciphertexts[i] < ciphertexts[j]) == (true_ages[i] < true_ages[j])
        for i in range(0, min(N, 50)) for j in range(i + 1, min(N, 50))
        if true_ages[i] != true_ages[j]
    ), "ORE phải bảo toàn thứ tự"

    results = {
        "n_records": N,
        "baseline_random_guess": baseline_random(true_ages),
        "attack_A_distribution_only": attack_distribution_only(ciphertexts, true_ages),
        "attack_B_known_plaintext_10_anchors": attack_known_plaintext(ciphertexts, true_ages, 10),
    }

    def show(name, m):
        print(f"\n[{name}]")
        print(f"  exact recovery : {m['exact_recovery_rate']*100:5.1f}%")
        print(f"  within ±2 years: {m['within_2_years_rate']*100:5.1f}%")
        print(f"  MAE            : {m['mae_years']:.2f} năm")

    show("Baseline (đoán mò theo phân bố)", results["baseline_random_guess"])
    show("Tấn công A — chỉ biết phân bố", results["attack_A_distribution_only"])
    show("Tấn công B — biết 10 cặp known-plaintext", results["attack_B_known_plaintext_10_anchors"])

    print("\n" + "=" * 64)
    print("KẾT LUẬN: ORE lộ thứ tự → chỉ cần phân bố (A) hoặc vài anchor (B) là")
    print("khôi phục tuổi tốt hơn HẲN đoán mò. Đây là cái giá của range-query trên")
    print("ciphertext. Truy vấn nhạy cảm cần tổng hợp → đẩy vào TEE (không lộ thứ tự).")
    print("Lưu ý: A mạnh hơn B vì A giả định BIẾT phân bố + dữ liệu dày (rank↔quantile")
    print("gần như tất định); B chỉ có vài cặp rò rỉ, không biết phân bố → thô hơn.")
    print("Cả hai đều vượt xa baseline → bản chất ORE đã lộ thứ tự.")
    print("=" * 64)

    out = Path(__file__).resolve().parent.parent / "attack_ore_results.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nĐã lưu: {out}")


if __name__ == "__main__":
    main()
