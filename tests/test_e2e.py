# T10 - Test E2E hoàn chỉnh
# Client → Router → RBAC → ECALL pool → kết quả
import httpx
import pytest

ROUTER_URL = "http://localhost:8000"

def test_health_router():
    r = httpx.get(f"{ROUTER_URL}/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["ecall_pool"] is True  # Lan's service phải sống

def test_admin_sum_full_flow():
    """admin gọi sum → TEE → nhận kết quả thật"""
    r = httpx.post(f"{ROUTER_URL}/query", json={
        "query_type": "sum_vien_phi",
        "filters": {},
        "role": "admin"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "tee"
    assert data["result"]["result"] > 0
    assert data["result"]["n_records"] == 5

def test_doctor_avg_full_flow():
    """doctor gọi avg → TEE → nhận kết quả"""
    r = httpx.post(f"{ROUTER_URL}/query", json={
        "query_type": "avg_vien_phi",
        "filters": {},
        "role": "doctor"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "tee"
    assert data["result"]["result"] > 0

def test_researcher_avg_masked_e2e():
    """researcher gọi avg → TEE → kết quả bị mask"""
    r = httpx.post(f"{ROUTER_URL}/query", json={
        "query_type": "avg_vien_phi",
        "filters": {},
        "role": "researcher"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["result"]["result"] == "[MASKED]"
    assert "vien_phi" in data["masked_fields"]

def test_doctor_sum_blocked_e2e():
    """doctor gọi sum → bị 403"""
    r = httpx.post(f"{ROUTER_URL}/query", json={
        "query_type": "sum_vien_phi",
        "filters": {},
        "role": "doctor"
    })
    assert r.status_code == 403

def test_count_software_mode_e2e():
    """count → SOFTWARE mode"""
    r = httpx.post(f"{ROUTER_URL}/query", json={
        "query_type": "count",
        "filters": {},
        "role": "admin"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "software"

def test_filter_by_ma_benh():
    """filter theo ma_benh → ít records hơn"""
    r_all = httpx.post(f"{ROUTER_URL}/query", json={
        "query_type": "avg_vien_phi",
        "filters": {},
        "role": "admin"
    })
    r_filtered = httpx.post(f"{ROUTER_URL}/query", json={
        "query_type": "avg_vien_phi",
        "filters": {"ma_benh": "DTE001"},
        "role": "admin"
    })
    assert r_all.status_code == 200
    assert r_filtered.status_code == 200
    all_records = r_all.json()["result"]["n_records"]
    filtered_records = r_filtered.json()["result"]["n_records"]
    assert filtered_records < all_records
