# T2 - Cost Model: tính chi phí C_soft vs C_TEE
# - C_TEE: nội suy từ RSS profile thật của Lan (c_tee_metrics.json)
# - C_soft: dẫn xuất từ số liệu micro-benchmark thật của Long (c_soft_metrics.json)

import json
import os
from pathlib import Path

from router.query_router import ExecutionMode

REPO_ROOT = Path(__file__).resolve().parents[1]

# ── Số liệu thực từ Lan (TEE) ────────────────────────────────────
C_SWITCH_MS     = 0.023006   # chi phí ECALL context switch
C_ASym_ECC_MS   = 0.697      # ECC384 (dùng cho auth)
RSS_THRESHOLD   = 0.80       # fallback khi RSS > 80% RAM

# RSS overhead thực đo (n_records → latency_ms)
RSS_PROFILE = [
    {"n": 1000,  "rss_mb": 25.8,  "latency_ms": 1.051},
    {"n": 5000,  "rss_mb": 27.6,  "latency_ms": 4.791},
    {"n": 10000, "rss_mb": 31.0,  "latency_ms": 9.672},
]

# ── C_soft dẫn xuất từ số liệu thật của Long ─────────────────────
# Software mode thao tác trên ciphertext (DTE equality / ORE range) KHÔNG giải mã
# từng bản ghi, nên chi phí chính là: dựng filter (mã hóa điều kiện) + quét index.
_C_SOFT_METRICS_PATH = Path(
    os.environ.get("C_SOFT_METRICS_PATH", REPO_ROOT / "crypto" / "benchmark" / "c_soft_metrics.json")
)

# Giá trị fallback (ms) nếu không đọc được file metrics
_DEFAULT_DTE_ENC_MS      = 0.002   # AES-SIV encrypt (build equality filter)
_DEFAULT_OPE_ENC_MS      = 0.550   # OPE encrypt (build range filter)
_DEFAULT_PER_RECORD_MS   = 0.0003  # quét/đếm 1 bản ghi trên index (ước lượng)


def _load_soft_metrics() -> dict:
    """Đọc avg micro-benchmark thật của Long; fallback hằng số nếu thiếu file."""
    dte_enc = _DEFAULT_DTE_ENC_MS
    ope_enc = _DEFAULT_OPE_ENC_MS
    try:
        data = json.loads(_C_SOFT_METRICS_PATH.read_text())
        by_op = {item["operation"]: item for item in data.get("results", [])}
        if "AES-SIV Encrypt (DTE)" in by_op:
            dte_enc = by_op["AES-SIV Encrypt (DTE)"]["avg_us"] / 1000.0
        if "OPE Encrypt (age)" in by_op:
            ope_enc = by_op["OPE Encrypt (age)"]["avg_us"] / 1000.0
    except Exception:
        pass
    return {"dte_enc_ms": dte_enc, "ope_enc_ms": ope_enc, "per_record_ms": _DEFAULT_PER_RECORD_MS}


_SOFT = _load_soft_metrics()


def estimate_tee_latency(n_records: int) -> float:
    """Nội suy latency TEE từ RSS profile của Lan."""
    if n_records <= 1000:
        return RSS_PROFILE[0]["latency_ms"]
    elif n_records <= 5000:
        ratio = (n_records - 1000) / 4000
        return RSS_PROFILE[0]["latency_ms"] + ratio * (
            RSS_PROFILE[1]["latency_ms"] - RSS_PROFILE[0]["latency_ms"]
        )
    elif n_records <= 10000:
        ratio = (n_records - 5000) / 5000
        return RSS_PROFILE[1]["latency_ms"] + ratio * (
            RSS_PROFILE[2]["latency_ms"] - RSS_PROFILE[1]["latency_ms"]
        )
    else:
        # Extrapolate tuyến tính
        return RSS_PROFILE[2]["latency_ms"] * (n_records / 10000)


def estimate_software_latency(n_records: int, has_range: bool = True) -> float:
    """Ước lượng latency Software mode (DTE/ORE trên ciphertext).

    = chi phí dựng filter (mã hóa điều kiện equality + range) + quét index theo n.
    """
    filter_ms = _SOFT["dte_enc_ms"] + (_SOFT["ope_enc_ms"] if has_range else 0.0)
    return filter_ms + max(0, n_records) * _SOFT["per_record_ms"]


def compute_cost(mode: ExecutionMode, n_records: int) -> dict:
    """Tính chi phí ước tính cho mỗi execution mode."""
    if mode == ExecutionMode.TEE:
        latency = estimate_tee_latency(n_records) + C_SWITCH_MS
        return {
            "mode": "TEE",
            "estimated_latency_ms": round(latency, 3),
            "breakdown": {
                "ecall_switch_ms": C_SWITCH_MS,
                "query_latency_ms": round(latency - C_SWITCH_MS, 3),
            }
        }
    else:
        latency = estimate_software_latency(n_records)
        return {
            "mode": "SOFTWARE",
            "estimated_latency_ms": round(latency, 3),
            "breakdown": {
                "filter_build_ms": round(_SOFT["dte_enc_ms"] + _SOFT["ope_enc_ms"], 4),
                "scan_ms": round(max(0, n_records) * _SOFT["per_record_ms"], 4),
            }
        }


def compare_costs(n_records: int) -> dict:
    """So sánh C_soft vs C_TEE cho cùng n_records và đề xuất mode rẻ hơn.

    Dùng cho observability + hỗ trợ quyết định fallback (KHÔNG ghi đè ràng buộc
    bảo mật: operator nhạy cảm vẫn buộc chạy TEE; đây chỉ là gợi ý chi phí).
    """
    tee = compute_cost(ExecutionMode.TEE, n_records)
    soft = compute_cost(ExecutionMode.SOFTWARE, n_records)
    cheaper = (
        ExecutionMode.SOFTWARE
        if soft["estimated_latency_ms"] <= tee["estimated_latency_ms"]
        else ExecutionMode.TEE
    )
    return {
        "n_records": n_records,
        "tee": tee,
        "software": soft,
        "cheaper_mode": cheaper.value,
        "tee_over_software_ratio": round(
            tee["estimated_latency_ms"] / soft["estimated_latency_ms"], 3
        ) if soft["estimated_latency_ms"] > 0 else None,
    }
