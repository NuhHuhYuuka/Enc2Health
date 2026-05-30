# T3 - Unit tests cho Query Router, RBAC, Cost Model
import pytest
import sys
from pathlib import Path

# Đảm bảo import được package `router` khi chạy pytest từ bất kỳ cwd nào
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router.query_router import QueryRouter, ExecutionMode
from router.rbac import RBACMiddleware
from router.cost_model import (
    compute_cost, estimate_tee_latency, estimate_software_latency, compare_costs
)

# ── T1: Query Router ─────────────────────────────────────────────

def test_sum_routes_to_tee():
    r = QueryRouter()
    d = r.route("sum_vien_phi")
    assert d.mode == ExecutionMode.TEE

def test_avg_routes_to_tee():
    r = QueryRouter()
    d = r.route("avg_vien_phi")
    assert d.mode == ExecutionMode.TEE

def test_count_routes_to_software():
    r = QueryRouter()
    d = r.route("count")
    assert d.mode == ExecutionMode.SOFTWARE

def test_unknown_routes_to_software():
    r = QueryRouter()
    d = r.route("select_by_id")
    assert d.mode == ExecutionMode.SOFTWARE

def test_count_distinct_routes_to_tee():
    r = QueryRouter()
    assert r.route("count_distinct").mode == ExecutionMode.TEE
    assert r.route("count distinct").mode == ExecutionMode.TEE

def test_group_by_routes_to_software():
    r = QueryRouter()
    assert r.route("group_by").mode == ExecutionMode.SOFTWARE
    assert r.route("group by").mode == ExecutionMode.SOFTWARE

def test_join_and_equality_route_to_software():
    r = QueryRouter()
    assert r.route("join").mode == ExecutionMode.SOFTWARE
    assert r.route("equality").mode == ExecutionMode.SOFTWARE
    assert r.route("=").mode == ExecutionMode.SOFTWARE

# ── T4: RBAC ─────────────────────────────────────────────────────

def test_admin_can_sum():
    r = RBACMiddleware()
    d = r.check("admin", "sum_vien_phi")
    assert d.allowed is True

def test_doctor_cannot_sum():
    r = RBACMiddleware()
    d = r.check("doctor", "sum_vien_phi")
    assert d.allowed is False

def test_researcher_avg_masked():
    r = RBACMiddleware()
    d = r.check("researcher", "avg_vien_phi")
    assert d.allowed is True
    assert "vien_phi" in d.masked_fields
    assert "ma_benh" in d.masked_fields

def test_invalid_role_denied():
    r = RBACMiddleware()
    d = r.check("hacker", "count")
    assert d.allowed is False

# ── T2: Cost Model ───────────────────────────────────────────────

def test_tee_cost_increases_with_records():
    c1 = compute_cost(ExecutionMode.TEE, 1000)
    c2 = compute_cost(ExecutionMode.TEE, 5000)
    assert c2["estimated_latency_ms"] > c1["estimated_latency_ms"]

def test_software_cost_lower_than_tee():
    c_soft = compute_cost(ExecutionMode.SOFTWARE, 1000)
    c_tee  = compute_cost(ExecutionMode.TEE, 1000)
    assert c_soft["estimated_latency_ms"] < c_tee["estimated_latency_ms"]

def test_tee_latency_interpolation():
    # 3000 records phải nằm giữa 1000 và 5000
    l = estimate_tee_latency(3000)
    assert 1.051 < l < 4.791

def test_software_latency_scales_with_records():
    assert estimate_software_latency(10000) > estimate_software_latency(1000)

def test_software_filter_cost_lower_without_range():
    # Không có range (chỉ equality) thì bỏ chi phí OPE → rẻ hơn
    assert estimate_software_latency(1000, has_range=False) < estimate_software_latency(1000, has_range=True)

def test_compare_costs_picks_software_for_small_n():
    cmp = compare_costs(1000)
    assert cmp["cheaper_mode"] == ExecutionMode.SOFTWARE.value
    assert cmp["tee"]["estimated_latency_ms"] > 0
    assert cmp["software"]["estimated_latency_ms"] > 0

def test_compare_costs_has_ratio():
    cmp = compare_costs(5000)
    assert cmp["n_records"] == 5000
    assert cmp["tee_over_software_ratio"] is not None

# ── T1/T9: Router fetch ciphertext cho TEE path ─────────────────

def test_fetch_ciphertexts_empty_when_mongo_unavailable():
    from router.software_executor import SoftwareExecutor
    ex = SoftwareExecutor.__new__(SoftwareExecutor)   # không mở kết nối thật
    ex._mongo_available = False
    assert ex.fetch_vien_phi_ciphertexts({"ma_benh": "E11"}) == []
    assert ex.mongo_available is False

# ── T6: Adaptive state machine (tất định, không cần pool) ────────

def _controller():
    from router.adaptive import AdaptiveController
    return AdaptiveController()   # không start prober

def test_adaptive_stays_tee_under_threshold():
    c = _controller()
    c.set_simulated_pressure(0.50)
    assert c.get_execution_mode(ExecutionMode.TEE) == ExecutionMode.TEE
    assert c.mode.value == "normal"

def test_adaptive_fallback_over_threshold():
    c = _controller()
    c.set_simulated_pressure(0.90)
    assert c.get_execution_mode(ExecutionMode.TEE) == ExecutionMode.SOFTWARE
    assert c.mode.value == "fallback"

def test_adaptive_software_query_unaffected_by_fallback():
    c = _controller()
    c.set_simulated_pressure(0.90)
    # Toán tử vốn là SOFTWARE thì không bị ảnh hưởng
    assert c.get_execution_mode(ExecutionMode.SOFTWARE) == ExecutionMode.SOFTWARE

def test_adaptive_fallback_then_restore_cycle():
    c = _controller()
    c.set_simulated_pressure(0.92)
    assert c.get_execution_mode(ExecutionMode.TEE) == ExecutionMode.SOFTWARE
    c.set_simulated_pressure(0.30)
    assert c.get_execution_mode(ExecutionMode.TEE) == ExecutionMode.TEE
    assert c.mode.value == "normal"
    # 1 lần FALLBACK + 1 lần RESTORE
    assert c.get_status()["switch_count"] == 2
