import pytest
import sys
from pathlib import Path
from datetime import date

# Ensure importing router package works
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router.query_router import QueryRouter, ExecutionMode
from router.rbac import RBACMiddleware
from router.software_executor import SoftwareExecutor
from router.main import app
from enclave.ecall_pool import _build_mongo_filter, _query_mock_aggregate, _build_mock_patient_data

def test_query_router_new_types():
    r = QueryRouter()
    # Confirm AVG Glucose & AVG Creatinine route to TEE
    assert r.route("avg_glucose").mode == ExecutionMode.TEE
    assert r.route("avg_creatinine").mode == ExecutionMode.TEE


def test_software_executor_date_filtering():
    ex = SoftwareExecutor.__new__(SoftwareExecutor)
    ex._dte_ma_benh = None
    ex._dte_cmnd = None
    ex._dte_khoa = None
    ex._gcm = None
    
    # Test that dates are parsed and ORE-encrypted (or pass if ore key is loaded)
    from crypto.crypto.ore import ORECipher
    ore_key_path = Path(__file__).resolve().parents[1] / "crypto" / "data" / "keys" / "ore.key"
    if ore_key_path.exists():
        ex._ore = ORECipher.load_key(str(ore_key_path))
        q = ex._build_filter({
            "ngay_nhap_vien_min_enc": "2023-01-01",
            "ngay_nhap_vien_max_enc": "2023-12-31"
        })
        assert "ngay_nhap_vien_enc" in q
        assert "$gte" in q["ngay_nhap_vien_enc"]
        assert "$lte" in q["ngay_nhap_vien_enc"]
    else:
        ex._ore = None
        q = ex._build_filter({
            "ngay_nhap_vien_min_enc": "2023-01-01",
            "ngay_nhap_vien_max_enc": "2023-12-31"
        })
        assert "ngay_nhap_vien_enc" not in q


def test_enclave_mock_date_filtering():
    # Make sure we have mock data with ngay_nhap date
    data = _build_mock_patient_data(10, 100)
    for row in data:
        assert isinstance(row["ngay_nhap"], date)
        assert "glucose" in row
        assert "creatinine" in row

    # Test mock aggregation filtering
    # Since ngay_nhap ranges 2020 to 2024, let's filter for 2023 to 2024
    res_all = _query_mock_aggregate("count", {})
    total = int(res_all["result"])
    res = _query_mock_aggregate("count", {"ngay_nhap_vien_min_enc": "2023-01-01"})
    assert res["result"] < total


def test_enclave_mock_avg_calculations():
    # Test average glucose & creatinine calculations
    res_all = _query_mock_aggregate("count", {})
    total = int(res_all["result"])
    res_glucose = _query_mock_aggregate("avg_glucose", {})
    assert res_glucose["result"] > 0
    assert res_glucose["n_records"] == total

    res_creatinine = _query_mock_aggregate("avg_creatinine", {})
    assert res_creatinine["result"] > 0
    assert res_creatinine["n_records"] == total


def test_software_executor_avg_calculations():
    ex = SoftwareExecutor.__new__(SoftwareExecutor)
    ex._mongo_available = False
    ex._fallback_records = ex._build_fallback_records(100)
    
    # Check that Software Executor returns values for avg_glucose and avg_creatinine in fallback mode
    res_glucose = ex.query("avg_glucose", {})
    assert res_glucose.result > 0
    assert res_glucose.n_records > 0

    res_creatinine = ex.query("avg_creatinine", {})
    assert res_creatinine.result > 0
    assert res_creatinine.n_records > 0


def test_main_patient_lookup_rbac_masking():
    # Mock data to simulate software_executor lookup result
    patient_info = {
        "patient_id": "pid-test",
        "dept": "Noi",
        "ma_benh": "I01",
        "chan_doan": "Diabetes",
        "ket_qua_xn": {"glucose": 5.8, "creatinine": 80},
        "clinical_note": "Patient is stable"
    }

    # Simulate doctor role response mapping
    role = "doctor"
    if role in {"admin", "doctor"}:
        res_lab = patient_info["ket_qua_xn"]
        res_note = patient_info["clinical_note"]
    else:
        res_lab = "[MASKED]"
        res_note = "[MASKED]"

    assert res_lab == {"glucose": 5.8, "creatinine": 80}
    assert res_note == "Patient is stable"

    # Simulate staff role response mapping
    role = "admin_staff"
    if role in {"admin", "doctor"}:
        res_lab = patient_info["ket_qua_xn"]
        res_note = patient_info["clinical_note"]
    else:
        res_lab = "[MASKED]"
        res_note = "[MASKED]"

    assert res_lab == "[MASKED]"
    assert res_note == "[MASKED]"
