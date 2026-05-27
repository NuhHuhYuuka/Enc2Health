# T14 - Vẽ biểu đồ trade-off cho báo cáo
import json
import matplotlib.pyplot as plt
import numpy as np

# ── Load data ────────────────────────────────────────────────────
with open("benchmark_results.json") as f:
    bench = json.load(f)

with open("leakage_results.json") as f:
    leakage = json.load(f)

# ── Figure 1: Latency comparison (avg / p95 / p99) ───────────────
labels = [b["label"].replace(" (all records)", "").replace("Hybrid Adaptive: ", "Hybrid: ") for b in bench]
avg_ms = [b["avg_ms"] for b in bench]
p95_ms = [b["p95_ms"] for b in bench]
p99_ms = [b["p99_ms"] for b in bench]

x = np.arange(len(labels))
width = 0.25

fig, axes = plt.subplots(1, 3, figsize=(18, 6))
fig.suptitle("Enc2Health — Benchmark & Leakage Analysis", fontsize=14, fontweight="bold")

# Chart 1: Latency bar chart
ax1 = axes[0]
ax1.bar(x - width, avg_ms, width, label="avg", color="#4C9BE8")
ax1.bar(x,         p95_ms, width, label="p95", color="#F5A623")
ax1.bar(x + width, p99_ms, width, label="p99", color="#E84C4C")
ax1.set_title("Latency by Mode (ms)")
ax1.set_ylabel("Latency (ms)")
ax1.set_xticks(x)
ax1.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
ax1.legend()
ax1.grid(axis="y", alpha=0.3)

# Chart 2: QPS comparison
ax2 = axes[1]
qps = [b["qps"] for b in bench]
colors = ["#4C9BE8", "#E84C4C", "#E84C4C", "#9B59B6", "#9B59B6"]
bars = ax2.bar(labels, qps, color=colors)
ax2.set_title("Throughput QPS by Mode")
ax2.set_ylabel("Queries per Second")
ax2.set_xticks(range(len(labels)))
ax2.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
ax2.grid(axis="y", alpha=0.3)
for bar, val in zip(bars, qps):
    ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
             f"{val:.0f}", ha="center", va="bottom", fontsize=8)

# Chart 3: Leakage score
ax3 = axes[2]
leak_labels = ["TEE\n(admin)", "SOFTWARE\n(admin)", "TEE+RBAC\n(researcher)"]
leak_scores = [
    leakage["TEE_mode_admin"]["leakage_score"],
    leakage["SOFTWARE_mode_admin"]["leakage_score"],
    leakage["TEE_mode_researcher_masked"]["leakage_score"],
]
entropy_vals = [
    leakage["TEE_mode_admin"]["entropy"],
    leakage["SOFTWARE_mode_admin"]["entropy"],
    leakage["TEE_mode_researcher_masked"]["entropy"],
]

x3 = np.arange(len(leak_labels))
ax3.bar(x3 - 0.2, leak_scores, 0.4, label="Leakage Score", color="#E84C4C", alpha=0.8)
ax3_twin = ax3.twinx()
ax3_twin.bar(x3 + 0.2, entropy_vals, 0.4, label="Entropy", color="#2ECC71", alpha=0.8)
ax3.set_title("Q-Leakage Score & Entropy")
ax3.set_ylabel("Leakage Score (lower=better)", color="#E84C4C")
ax3_twin.set_ylabel("Entropy (higher=better)", color="#2ECC71")
ax3.set_xticks(x3)
ax3.set_xticklabels(leak_labels)
ax3.set_ylim(0, 0.3)
ax3_twin.set_ylim(0, 3)
ax3.legend(loc="upper left")
ax3_twin.legend(loc="upper right")
ax3.grid(axis="y", alpha=0.3)

plt.tight_layout()
plt.savefig("enc2health_benchmark.png", dpi=150, bbox_inches="tight")
print("Đã lưu biểu đồ: enc2health_benchmark.png")
plt.show()
