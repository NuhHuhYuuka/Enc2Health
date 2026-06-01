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

def test_get_patient_routes_to_tee():
    r = QueryRouter()
    assert r.route("get_patient").mode == ExecutionMode.TEE
    assert r.route("lookup_patient").mode == ExecutionMode.TEE

def test_group_by_routes_to_software():
    r = QueryRouter()
    assert r.route("group_by").mode == ExecutionMode.SOFTWARE
    assert r.route("group by").mode == ExecutionMode.SOFTWARE

def test_join_and_equality_route_to_software():
    r = QueryRouter()
    assert r.route("join").mode == ExecutionMode.SOFTWARE
    assert r.route("equality").mode == ExecutionMode.SOFTWARE
    assert r.route("=").mode == ExecutionMode.SOFTWARE

def test_keyword_search_routes_to_software():
    r = QueryRouter()
    assert r.route("keyword_search").mode == ExecutionMode.SOFTWARE
    assert r.route("search").mode == ExecutionMode.SOFTWARE

# ── T4: RBAC ─────────────────────────────────────────────────────

def test_admin_can_sum():
    r = RBACMiddleware()
    d = r.check("admin", "sum_vien_phi")
    assert d.allowed is True

def test_doctor_cannot_sum():
    r = RBACMiddleware()
    d = r.check("doctor", "sum_vien_phi")
    assert d.allowed is False

def test_pii_roles_allowed_with_masking():
    r = RBACMiddleware()
    assert r.check("doctor", "get_patient").allowed is True
    assert r.check("admin_staff", "get_patient").allowed is True
    assert r.mask_pii({"ho_ten": "A", "cmnd": "1", "ngay_sinh": "x", "dia_chi": "y"}, "admin_staff")["cmnd"] == "[MASKED]"

def test_keyword_search_roles_allowed():
    r = RBACMiddleware()
    assert r.check("admin", "keyword_search").allowed is True
    assert r.check("doctor", "keyword_search").allowed is True
    assert r.check("admin_staff", "keyword_search").allowed is True

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
    assert ex.fetch_vien_phi_ciphertexts({"ma_benh": "I01"}) == []
    assert ex.mongo_available is False

def test_fetch_patient_pii_requires_mongo():
    from router.software_executor import SoftwareExecutor
    ex = SoftwareExecutor.__new__(SoftwareExecutor)
    ex._mongo_available = False
    with pytest.raises(RuntimeError):
        ex.fetch_patient_pii_ciphertext("pid-1")
    with pytest.raises(RuntimeError):
        ex.fetch_patient_pii_ciphertext_by_cmnd("01234567890")

def test_static_sse_token_and_postings_roundtrip():
    from crypto.crypto.sse import StaticSSECipher, tokenize_text
    sse = StaticSSECipher(b"a" * 64)
    assert sse.token("Đái tháo đường") == sse.token("đái   tháo đường")
    assert sse.token("I01") != sse.token("C01")
    postings = [{"patient_id": "p1", "dept": "Noi"}]
    assert sse.decrypt_postings(sse.encrypt_postings(postings)) == postings
    assert "đái" in tokenize_text("Đái tháo đường I01")


def test_demo_disease_codes_are_canonicalized_to_uppercase():
    from router.software_executor import canonicalize_disease_code
    assert canonicalize_disease_code("i01") == "I01"
    assert canonicalize_disease_code(" p02 ") == "P02"
    assert canonicalize_disease_code("viêm phổi") == "viêm phổi"

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

# ── ABAC: kiểm soát truy cập theo thuộc tính (dept-scoping) ──────

def _abac():
    from router.abac import AbacPolicy, Subject
    return AbacPolicy(), Subject

def test_abac_doctor_scoped_to_own_dept():
    policy, Subject = _abac()
    d = policy.evaluate(Subject(role="doctor", dept="Tim_mach"), "count")
    assert d.allowed is True
    assert d.scope_filters == {"khoa_phong": "Tim_mach"}

def test_abac_admin_not_dept_scoped():
    policy, Subject = _abac()
    d = policy.evaluate(Subject(role="admin", dept="Tim_mach"), "count")
    assert d.allowed is True
    assert d.scope_filters == {}          # admin xem toàn viện, không bị giới hạn khoa

def test_abac_doctor_without_dept_no_scope_by_default():
    policy, Subject = _abac()
    d = policy.evaluate(Subject(role="doctor", dept=None), "count")
    assert d.allowed is True              # tương thích ngược (non-strict)
    assert d.scope_filters == {}

def test_abac_admin_staff_masks_diagnosis_not_billing():
    policy, Subject = _abac()
    d = policy.evaluate(Subject(role="admin_staff"), "avg_vien_phi")
    assert d.allowed is True
    assert "ma_benh" in d.masked_fields   # không xem chẩn đoán
    assert "vien_phi" not in d.masked_fields  # vẫn xem viện phí

def test_abac_doctor_sum_denied_by_rbac_layer():
    policy, Subject = _abac()
    d = policy.evaluate(Subject(role="doctor", dept="Noi"), "sum_vien_phi")
    assert d.allowed is False             # tầng RBAC vẫn chặn doctor chạy SUM

def test_abac_strict_mode_requires_dept(monkeypatch):
    import router.abac as A
    monkeypatch.setattr(A, "ABAC_REQUIRE_DEPT", True)
    d = A.AbacPolicy().evaluate(A.Subject(role="doctor", dept=None), "count")
    assert d.allowed is False

def test_build_filter_includes_khoa_phong_ciphertext():
    from router.software_executor import SoftwareExecutor
    from crypto.crypto.dte import DTECipher
    key = Path(__file__).resolve().parents[1] / "crypto" / "data" / "keys" / "dte_khoa.key"
    ex = SoftwareExecutor.__new__(SoftwareExecutor)   # không mở kết nối Mongo
    ex._dte_ma_benh = None
    ex._ore = None
    ex._dte_khoa = DTECipher.load_key(str(key))
    q = ex._build_filter({"khoa_phong": "Tim_mach"})
    assert "khoa_phong_enc" in q
    # DTE tất định: cùng khoa → cùng ciphertext
    assert q["khoa_phong_enc"] == ex._build_filter({"khoa_phong": "Tim_mach"})["khoa_phong_enc"]
