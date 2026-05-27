# T3 - Unit tests cho Query Router, RBAC, Cost Model
import pytest
import sys
sys.path.insert(0, "/home/yuwkaa/enc2health")

from router.query_router import QueryRouter, ExecutionMode
from router.rbac import RBACMiddleware
from router.cost_model import compute_cost, estimate_tee_latency

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
