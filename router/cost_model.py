# T2 - Cost Model: tính chi phí C_soft vs C_TEE
# Số liệu thực từ Lan (c_tee_metrics.json)

from router.query_router import ExecutionMode

# ── Số liệu thực từ Lan ──────────────────────────────────────────
C_SWITCH_MS     = 0.023006   # chi phí ECALL context switch
C_ASym_ECC_MS   = 0.697      # ECC384 (dùng cho auth)
RSS_THRESHOLD   = 0.80       # fallback khi RSS > 80% RAM

# RSS overhead thực đo (n_records → latency_ms)
RSS_PROFILE = [
    {"n": 1000,  "rss_mb": 25.8,  "latency_ms": 1.051},
    {"n": 5000,  "rss_mb": 27.6,  "latency_ms": 4.791},
    {"n": 10000, "rss_mb": 31.0,  "latency_ms": 9.672},
]

# ── C_soft (placeholder, chờ số liệu từ Long) ───────────────────
C_SOFT_BASE_MS  = 0.5        # ước tính DTE/ORE lookup

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
        latency = C_SOFT_BASE_MS * (n_records / 1000)
        return {
            "mode": "SOFTWARE",
            "estimated_latency_ms": round(latency, 3),
            "breakdown": {
                "dte_ore_ms": round(latency, 3),
            }
        }
