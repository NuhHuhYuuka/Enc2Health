# T10 - Test E2E hoàn chỉnh
# Client → Router → RBAC → ECALL pool → kết quả

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from pymongo import MongoClient

from common.auth import generate_test_jwt
from crypto.crypto.dte import DTECipher
from crypto.crypto.gcm import AESGCMCipher
from crypto.crypto.ore import ORECipher


ROUTER_URL = os.environ.get("ROUTER_URL", "http://127.0.0.1:8000")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DB = os.environ.get("MONGO_DB", "enc2health")
MONGO_COLLECTION = os.environ.get("MONGO_COLLECTION", "patient_records")
KEY_DIR = Path(__file__).resolve().parents[1] / "crypto" / "data" / "keys"


def _token(role: str, dept: str | None = None) -> str:
    secret = os.environ.get("AUTH_JWT_SECRET", "dev-secret-32-bytes-long-1234567890")
    os.environ["AUTH_JWT_SECRET"] = secret
    claims = {"dept": dept} if dept else None
    return generate_test_jwt("e2e-test", role, claims=claims)


def _headers(role: str, dept: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(role, dept=dept)}"}


def _mongo_collection():
    return MongoClient(MONGO_URI)[MONGO_DB][MONGO_COLLECTION]


def _dte_cipher() -> DTECipher:
    return DTECipher.load_key(str(KEY_DIR / "dte_ma_benh.key"))


def _ore_cipher() -> ORECipher:
    return ORECipher.load_key(str(KEY_DIR / "ore.key"))


def _gcm_cipher() -> AESGCMCipher:
    return AESGCMCipher.load_key(str(KEY_DIR / "gcm_dek.key"))


def _mongo_filter(filters: dict) -> dict:
    query: dict = {}
    dte = _dte_cipher()
    ore = _ore_cipher()

    if "ma_benh" in filters:
        query["ma_benh_enc"] = dte.encrypt(str(filters["ma_benh"]), b"field:ma_benh")

    if "tuoi_min_enc" in filters:
        query["tuoi_enc"] = {"$gte": ore.encrypt_age(int(filters["tuoi_min_enc"]))}

    if "tuoi_max_enc" in filters:
        query.setdefault("tuoi_enc", {})["$lte"] = ore.encrypt_age(int(filters["tuoi_max_enc"]))

    return query


def _expected_values(filters: dict) -> list[float]:
    col = _mongo_collection()
    gcm = _gcm_cipher()
    rows = col.find(_mongo_filter(filters), {"vien_phi_enc": 1, "_id": 0})
    values: list[float] = []
    for row in rows:
        ciphertext = row.get("vien_phi_enc")
        if ciphertext:
            values.append(float(gcm.decrypt_json(ciphertext)))
    return values


def _expected_count(filters: dict) -> int:
    col = _mongo_collection()
    return int(col.count_documents(_mongo_filter(filters)))


def _patient_with_pii(dept: str | None = None) -> dict:
    query = {"pii_enc": {"$exists": True}}
    if dept:
        query["dept"] = dept
    row = _mongo_collection().find_one(query, {"patient_id": 1, "dept": 1, "pii_enc": 1})
    if not row:
        pytest.skip("Mongo dataset has no pii_enc records; reseed with EHR_FORCE_RECREATE=1")
    return row


@pytest.fixture(scope="module", autouse=True)
def require_live_stack():
    try:
        r = httpx.get(f"{ROUTER_URL}/health", timeout=5)
        assert r.status_code == 200
        pool = httpx.get(f"{ROUTER_URL}/health", timeout=5).json().get("ecall_pool")
        assert pool is True
    except Exception as exc:
        pytest.skip(f"Live router/pool not available: {exc}")


def test_health_router():
    r = httpx.get(f"{ROUTER_URL}/health", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["ecall_pool"] is True


def test_admin_sum_full_flow():
    """admin gọi sum → TEE → nhận kết quả thật"""
    filters = {}
    expected_values = _expected_values(filters)
    expected_sum = sum(expected_values)

    r = httpx.post(
        f"{ROUTER_URL}/query",
        json={"query_type": "sum_vien_phi", "filters": filters, "role": "admin"},
        headers=_headers("admin"),
        timeout=30,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "tee"
    assert data["result"]["n_records"] == len(expected_values)
    assert data["result"]["result"] == pytest.approx(expected_sum, rel=1e-9)


def test_doctor_avg_full_flow():
    """doctor gọi avg → TEE → nhận kết quả"""
    filters = {}
    expected_values = _expected_values(filters)
    expected_avg = sum(expected_values) / len(expected_values)

    r = httpx.post(
        f"{ROUTER_URL}/query",
        json={"query_type": "avg_vien_phi", "filters": filters, "role": "doctor"},
        headers=_headers("doctor"),
        timeout=30,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "tee"
    assert data["result"]["n_records"] == len(expected_values)
    assert data["result"]["result"] == pytest.approx(expected_avg, rel=1e-9)


def test_researcher_avg_masked_e2e():
    """researcher gọi avg → TEE → kết quả bị mask"""
    r = httpx.post(
        f"{ROUTER_URL}/query",
        json={"query_type": "avg_vien_phi", "filters": {}, "role": "researcher"},
        headers=_headers("researcher"),
        timeout=30,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["result"]["result"] == "[MASKED]"
    assert "vien_phi" in data["masked_fields"]


def test_doctor_sum_blocked_e2e():
    """doctor gọi sum → bị 403"""
    r = httpx.post(
        f"{ROUTER_URL}/query",
        json={"query_type": "sum_vien_phi", "filters": {}, "role": "doctor"},
        headers=_headers("doctor"),
        timeout=30,
    )
    assert r.status_code == 403


def test_count_software_mode_e2e():
    """count → SOFTWARE mode"""
    filters = {"ma_benh": "E11"}
    expected_count = _expected_count(filters)

    r = httpx.post(
        f"{ROUTER_URL}/query",
        json={"query_type": "count", "filters": filters, "role": "admin"},
        headers=_headers("admin"),
        timeout=30,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "software"
    assert data["result"]["n_records"] == expected_count
    assert data["result"]["result"] == pytest.approx(float(expected_count), rel=1e-9)


def test_filter_by_ma_benh():
    """filter theo ma_benh → ít records hơn"""
    all_values = _expected_values({})
    filtered_values = _expected_values({"ma_benh": "E11"})

    r_all = httpx.post(
        f"{ROUTER_URL}/query",
        json={"query_type": "avg_vien_phi", "filters": {}, "role": "admin"},
        headers=_headers("admin"),
        timeout=30,
    )
    r_filtered = httpx.post(
        f"{ROUTER_URL}/query",
        json={"query_type": "avg_vien_phi", "filters": {"ma_benh": "E11"}, "role": "admin"},
        headers=_headers("admin"),
        timeout=30,
    )
    assert r_all.status_code == 200
    assert r_filtered.status_code == 200
    assert r_all.json()["result"]["n_records"] == len(all_values)
    assert r_filtered.json()["result"]["n_records"] == len(filtered_values)
    assert len(filtered_values) < len(all_values)


def test_pii_decrypt_in_enclave_doctor():
    """Bác sĩ đúng khoa xem được PII giải mã trong Pool/TEE."""
    patient = _patient_with_pii("Tim_mach")
    r = httpx.post(
        f"{ROUTER_URL}/query",
        json={"query_type": "get_patient", "patient_id": patient["patient_id"], "role": "doctor"},
        headers=_headers("doctor", dept="Tim_mach"),
        timeout=30,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "tee"
    assert data["result"]["dept"] == "Tim_mach"
    assert data["result"]["pii"]["ho_ten"] != "[MASKED]"
    assert data["result"]["pii"]["cmnd"] != "[MASKED]"


def test_pii_masked_for_researcher():
    """Researcher được phép lookup nhưng PII bị mask hoàn toàn."""
    patient = _patient_with_pii()
    r = httpx.post(
        f"{ROUTER_URL}/query",
        json={"query_type": "get_patient", "patient_id": patient["patient_id"], "role": "researcher"},
        headers=_headers("researcher"),
        timeout=30,
    )
    assert r.status_code == 200
    pii = r.json()["result"]["pii"]
    assert pii["ho_ten"] == "[MASKED]"
    assert pii["cmnd"] == "[MASKED]"


def test_pii_abac_wrong_dept_for_doctor():
    """Bác sĩ Tim_mach không xem được bệnh nhân khoa khác."""
    patient = _patient_with_pii("Noi")
    r = httpx.post(
        f"{ROUTER_URL}/query",
        json={"query_type": "get_patient", "patient_id": patient["patient_id"], "role": "doctor"},
        headers=_headers("doctor", dept="Tim_mach"),
        timeout=30,
    )
    assert r.status_code == 403
