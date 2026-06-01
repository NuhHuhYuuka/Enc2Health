#!/usr/bin/env python3
"""
T8 - ECALL Task Pool (HTTP Service via FastAPI)
Gramine-compatible concurrent query executor for medical data.
Port: 9091 (NOT 9090 – Prometheus uses 9090)

Replaces Intel SGX SDK EDL (incompatible with Gramine) with HTTP/FastAPI Python.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
import traceback
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Add project root early so package imports resolve when running this file directly.
# This file lives at repo_root/enclave/ecall_pool.py, so parents[1] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, HTTPException, Request
# Import shared auth helpers from project `common/auth.py` without requiring a package import
import importlib.util
common_auth_path = REPO_ROOT / "common" / "auth.py"
if common_auth_path.exists():
    spec = importlib.util.spec_from_file_location("common.auth", str(common_auth_path))
    _common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_common)
    validate_jwt_bearer = getattr(_common, "validate_jwt_bearer")
else:
    def validate_jwt_bearer(x):
        raise RuntimeError("common/auth.py not found; JWT validation unavailable")
from pydantic import BaseModel, ConfigDict, Field
import uvicorn
import ssl
import base64 as _b64

try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None

from crypto.crypto.dte import DTECipher
from crypto.crypto.ore import ORECipher
from crypto.crypto.asym import ecc_decrypt

try:
    from crypto.vault.vault_client import get_dek, get_department_private_key
except Exception:
    get_dek = None
    get_department_private_key = None

# Add enclave directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

import enclave_service

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

POOL_WORKERS = 8  # Thread pool size for 50 doctors
PORT = int(os.environ.get("T8_POOL_PORT", "9091"))  # Prometheus uses 9090
HOST = os.environ.get("T8_POOL_HOST", "127.0.0.1")
POOL_RECORDS = int(os.environ.get("T8_POOL_RECORDS", "10000"))
POOL_PAYLOAD_BYTES = int(os.environ.get("T8_POOL_PAYLOAD_BYTES", "256"))
USE_INDEXES = os.environ.get("T8_POOL_USE_INDEXES", "1") != "0"
DATA_MODE = os.environ.get("T8_POOL_DATA_MODE", "auto").strip().lower()  # auto | mongo | mock
STRICT_MODE = os.environ.get("T8_STRICT_MODE", "0") == "1"
ALLOW_LOCAL_KEY_FALLBACK = os.environ.get("T8_ALLOW_LOCAL_KEY_FALLBACK", "0") == "1"
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "enc2health")
MONGO_COLLECTION = os.environ.get("MONGO_COLLECTION", "patient_records")

ALLOW_LEGACY_DTE_CODES = os.environ.get("ALLOW_LEGACY_DTE_CODES", "0") == "1"

ATTESTATION_SECRET = os.environ.get("T8_ATTESTATION_SECRET", "enc2health-dev-attestation-secret")
ATTESTATION_KEY_ID = os.environ.get("T8_ATTESTATION_KEY_ID", "dev-kid-1")
ATTESTATION_MAX_AGE_S = int(os.environ.get("T8_ATTESTATION_MAX_AGE_S", "120"))
_BOOT_ID = secrets.token_hex(16)

ICD10_ALIASES = {
    "DTE001": "E11",
    "DTE002": "I10",
    "DTE003": "J18",
    "DTE004": "K29",
    "DTE005": "M54",
    "DTE006": "N18",
} if ALLOW_LEGACY_DTE_CODES else {}

# Global state (in-memory, process-only)
_keys: Dict[str, bytes] = {}
_keys_lock = None  # Placeholder for threading.Lock if needed
_executor = ThreadPoolExecutor(max_workers=POOL_WORKERS)
_mongo_client = None
_mongo_collection = None
_dte_cipher: DTECipher | None = None
_dte_khoa_cipher: DTECipher | None = None
_ore_cipher: ORECipher | None = None
_keys_loaded = False
_dek_source = "unknown"


def _load_runtime_keys() -> None:
    """Load runtime keys from Vault first; local files are only an explicit dev fallback."""
    global _dek_source
    runtime_keys = {
        "gcm_dek": "gcm_dek",
        "dte_ma_benh": "dte_ma_benh",
        "dte_khoa": "dte_khoa",
        "ore_key": "ore_key",
    }
    local_key_files = {
        "gcm_dek": REPO_ROOT / "crypto" / "data" / "keys" / "gcm_dek.key",
        "dte_ma_benh": REPO_ROOT / "crypto" / "data" / "keys" / "dte_ma_benh.key",
        "dte_khoa": REPO_ROOT / "crypto" / "data" / "keys" / "dte_khoa.key",
        "ore_key": REPO_ROOT / "crypto" / "data" / "keys" / "ore.key",
    }

    missing_keys: list[str] = []
    source_label: str | None = None
    for key_name, vault_key_name in runtime_keys.items():
        key_bytes = None
        if get_dek is not None:
            try:
                key_bytes = get_dek(vault_key_name)
                source_label = source_label or "vault"
                print(f"[T8 Pool] Loaded key from Vault: {key_name}")
            except Exception as exc:
                print(f"[T8 Pool] Vault load failed for {key_name}: {exc}")

        if key_bytes is None and ALLOW_LOCAL_KEY_FALLBACK:
            local_path = local_key_files[key_name]
            if local_path.exists():
                try:
                    key_bytes = base64.b64decode(local_path.read_text().strip())
                    source_label = source_label or "local_fallback"
                    print(f"[T8 Pool] Loaded key from local dev file (explicit fallback): {key_name}")
                except Exception as exc:
                    print(f"[T8 Pool] Local key load failed for {key_name}: {exc}")
        elif key_bytes is None:
            missing_keys.append(key_name)

        if key_bytes is not None:
            load_key(key_name, key_bytes)

    _dek_source = source_label or "unknown"
    if missing_keys:
        raise RuntimeError(
            "Vault runtime keys unavailable for: "
            + ", ".join(missing_keys)
            + ". Provide VAULT_TOKEN or VAULT_ROLE_ID/VAULT_SECRET_ID; set T8_ALLOW_LOCAL_KEY_FALLBACK=1 only for local dev."
        )

    print(f"[T8 Pool] DEK source: {_dek_source}")

    # mark keys_loaded if we have the primary GCM DEK in RAM
    global _keys_loaded
    _keys_loaded = "gcm_dek" in _keys


def _refresh_crypto_helpers() -> None:
    global _dte_cipher, _dte_khoa_cipher, _ore_cipher
    if "dte_ma_benh" in _keys:
        _dte_cipher = DTECipher(_keys["dte_ma_benh"])
    if "dte_khoa" in _keys:
        _dte_khoa_cipher = DTECipher(_keys["dte_khoa"])
    if "ore_key" in _keys:
        _ore_cipher = ORECipher(_keys["ore_key"])


def _init_mongo_collection() -> bool:
    global _mongo_client, _mongo_collection
    if MongoClient is None:
        return False
    try:
        timeout_ms = int(os.environ.get("SOFTWARE_MONGO_TIMEOUT_MS", "300"))
        _mongo_client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=timeout_ms,
            connectTimeoutMS=timeout_ms,
            socketTimeoutMS=timeout_ms,
        )
        _mongo_client.admin.command("ping")
        _mongo_collection = _mongo_client[MONGO_DB][MONGO_COLLECTION]
        return True
    except Exception as exc:
        print(f"[T8 Pool] Mongo init failed: {exc}")
        _mongo_client = None
        _mongo_collection = None
        return False


def _build_mongo_filter(filters: Dict[str, object]) -> Dict[str, object]:
    query: Dict[str, object] = {}

    if "ma_benh" in filters and _dte_cipher is not None:
        ma_benh = _normalize_ma_benh(str(filters["ma_benh"]))
        query["ma_benh_enc"] = _dte_cipher.encrypt(ma_benh, b"field:ma_benh")

    if "tuoi_min_enc" in filters and _ore_cipher is not None:
        query["tuoi_enc"] = {"$gte": _ore_cipher.encrypt_age(int(filters["tuoi_min_enc"]))}

    if "tuoi_max_enc" in filters and _ore_cipher is not None:
        query.setdefault("tuoi_enc", {})["$lte"] = _ore_cipher.encrypt_age(int(filters["tuoi_max_enc"]))

    dept = filters.get("khoa_phong") or filters.get("khoa")
    if dept is not None and _dte_khoa_cipher is not None:
        query["khoa_phong_enc"] = _dte_khoa_cipher.encrypt(str(dept), b"field:khoa_phong")

    return query


def _query_mongo_aggregate(req) -> Dict:
    if _mongo_collection is None or _dte_cipher is None or _ore_cipher is None:
        raise RuntimeError("Mongo ciphertext path is not initialized")

    mongo_filter = _build_mongo_filter(req.filters)
    projection = {"vien_phi_enc": 1, "_id": 0}

    if req.query_type == "count":
        n_records = _mongo_collection.count_documents(mongo_filter)
        return {
            "result": float(n_records),
            "n_records": int(n_records),
            "latency_ms": 0.0,
            "query_type": req.query_type,
        }

    docs = _mongo_collection.find(mongo_filter, projection)
    values: List[float] = []
    for doc in docs:
        ct = doc.get("vien_phi_enc")
        if ct:
            values.append(enclave_service.decrypt_aes_gcm(ct))

    n_records = len(values)
    if not values:
        result = 0.0
    elif req.query_type == "avg_vien_phi":
        result = sum(values) / n_records
    elif req.query_type == "sum_vien_phi":
        result = sum(values)
    else:
        raise ValueError(f"Unknown query type: {req.query_type}")

    return {
        "result": result,
        "n_records": n_records,
        "latency_ms": 0.0,
        "query_type": req.query_type,
    }

def _build_mock_patient_data(n_records: int, payload_bytes: int) -> List[dict]:
    """Build a configurable in-memory EHR table for simulation benchmarks."""
    diseases = ["E11", "I10", "J18", "K29", "M54", "N18"]
    departments = ["Noi", "Ngoai", "Cap_cuu", "Tim_mach", "Than_kinh", "Nhi"]
    payload = "x" * max(0, payload_bytes)
    records: List[dict] = []

    for index in range(n_records):
        age = 18 + (index % 78)
        records.append(
            {
                "record_id": index,
                "patient_id": f"pid-{index}",
                "ma_benh": diseases[index % len(diseases)],
                "tuoi": age,
                "vien_phi": float(900 + (index % 9100)),
                "khoa": departments[index % len(departments)],
                "payload": payload,
            }
        )

    return records


MOCK_PATIENT_DATA = _build_mock_patient_data(POOL_RECORDS, POOL_PAYLOAD_BYTES)
INDEX_BY_MA_BENH = defaultdict(list)
INDEX_BY_TUOI = defaultdict(list)
INDEX_BY_KHOA = defaultdict(list)

for record in MOCK_PATIENT_DATA:
    INDEX_BY_MA_BENH[record["ma_benh"]].append(record)
    INDEX_BY_TUOI[record["tuoi"]].append(record)
    INDEX_BY_KHOA[record["khoa"]].append(record)

MAX_AGE = max(INDEX_BY_TUOI.keys(), default=0)


def _records_for_min_age(min_age: int) -> List[dict]:
    if not USE_INDEXES:
        return [record for record in MOCK_PATIENT_DATA if record.get("tuoi", 0) >= min_age]

    candidates: List[dict] = []
    for age in range(max(0, min_age), MAX_AGE + 1):
        candidates.extend(INDEX_BY_TUOI.get(age, []))
    return candidates


DEMO_DISEASE_CODE_RE = re.compile(r"^[A-Za-z]\d{2}$")

def canonicalize_disease_code(value: str) -> str:
    """Keep demo disease-code letters uppercase: p01 -> P01."""
    text = str(value).strip()
    return text.upper() if DEMO_DISEASE_CODE_RE.fullmatch(text) else text


def _normalize_ma_benh(value: str) -> str:
    val = canonicalize_disease_code(value)
    return ICD10_ALIASES.get(val, val)


def _query_mock_aggregate(query_type: str, filters: Dict[str, object]) -> Dict[str, object]:
    """Run aggregate queries against the in-process simulation dataset."""
    records = MOCK_PATIENT_DATA

    if "ma_benh" in filters:
        ma_benh = _normalize_ma_benh(str(filters["ma_benh"]))
        records = [record for record in records if record["ma_benh"] == ma_benh]

    dept = filters.get("khoa_phong", filters.get("khoa"))
    if dept is not None:
        records = [record for record in records if record["khoa"] == str(dept)]

    if "tuoi_min_enc" in filters:
        min_age = int(filters["tuoi_min_enc"])
        records = [record for record in records if int(record["tuoi"]) >= min_age]

    if "tuoi_max_enc" in filters:
        max_age = int(filters["tuoi_max_enc"])
        records = [record for record in records if int(record["tuoi"]) <= max_age]

    n_records = len(records)
    if query_type == "count":
        result = float(n_records)
    elif query_type == "avg_vien_phi":
        result = sum(float(record["vien_phi"]) for record in records) / n_records if records else 0.0
    elif query_type == "sum_vien_phi":
        result = sum(float(record["vien_phi"]) for record in records)
    else:
        raise ValueError(f"Unknown query type: {query_type}")

    return {
        "result": result,
        "n_records": n_records,
        "latency_ms": 0.0,
        "query_type": query_type,
    }


def _make_attestation_payload() -> dict:
    payload = {
        "mrenclave": os.environ.get("T8_EXPECTED_MRENCLAVE", "simulated-mrenclave"),
        "timestamp": int(time.time()),
        "boot_id": _BOOT_ID,
        "host": HOST,
        "port": PORT,
    }
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(ATTESTATION_SECRET.encode(), payload_json, hashlib.sha256).hexdigest()
    quote = _b64.b64encode(payload_json).decode()
    return {
        "mrenclave": payload["mrenclave"],
        "quote": quote,
        "signature": signature,
        "signature_alg": "HMAC-SHA256",
        "kid": ATTESTATION_KEY_ID,
        "timestamp": payload["timestamp"],
        "max_age_s": ATTESTATION_MAX_AGE_S,
        "mode": "simulated-signed-attestation",
    }

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    """Medical query request model."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query_type": "avg_vien_phi",
                "filters": {"ma_benh": "E11"},
                "role": "doctor"
            }
        }
    )

    query_type: str  # "avg_vien_phi" | "sum_vien_phi" | "count"
    filters: Dict = Field(default_factory=dict)  # {"ma_benh": "E11", "tuoi_min_enc": 18, ...}
    role: str  # "doctor" | "admin" | "researcher"
    ciphertexts: Optional[List[str]] = Field(default=None)


class QueryResponse(BaseModel):
    """Query response model."""
    result: float
    n_records: int
    latency_ms: float
    query_type: str


class PIIQueryRequest(BaseModel):
    pii_enc: str
    dept: str


class PIIQueryResponse(BaseModel):
    pii: Dict[str, object]
    dept: str
    latency_ms: float


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    keys_loaded: List[str]
    workers: int
    uptime_s: float
    port: int


# ─────────────────────────────────────────────────────────────────────────────
# Core Query Logic
# ─────────────────────────────────────────────────────────────────────────────

_start_time = time.time()


def load_key(name: str, key_bytes: bytes):
    """Load a key into the pool (simulated)."""
    _keys[name] = key_bytes
    enclave_service.load_key(name, key_bytes)
    _refresh_crypto_helpers()
    global _keys_loaded
    if name == "gcm_dek":
        _keys_loaded = True
    print(f"[T8 Pool] Key loaded: {name} ({len(key_bytes)} bytes)")


def decrypt_value_if_needed(value, key_name: str = "default") -> float:
    """
    Decrypt a value if it's base64-encoded ciphertext.
    In practice, would check if value is DTE-encrypted format.
    """
    if isinstance(value, str) and value.startswith("DTE-"):
        # Placeholder: actual decryption would happen here
        # For now, return mock value
        return float(hash(value) % 10000) / 10  # Mock decrypted value
    elif isinstance(value, (int, float)):
        return float(value)
    else:
        return 0.0


def _execute_medical_query(req: QueryRequest) -> Dict:
    """
    Execute a medical aggregation query.
    Runs in thread pool to allow concurrent access.
    """
    allowed_roles = {"doctor", "admin", "admin_staff"}
    if req.role not in allowed_roles:
        raise ValueError(f"Invalid role: {req.role}")

    normalized_filters = dict(req.filters)
    if "ma_benh" in normalized_filters:
        normalized_filters["ma_benh"] = _normalize_ma_benh(str(normalized_filters["ma_benh"]))

    t_start = time.perf_counter()
    # If Router pushed ciphertexts directly for TEE-mode execution, use them.
    if getattr(req, "ciphertexts", None):
        # Decrypt stream using enclave_service and run aggregation inside an in-memory DuckDB
        try:
            try:
                import duckdb
            except Exception:
                duckdb = None

            values: List[float] = []
            for ct in req.ciphertexts:
                try:
                    v = enclave_service.decrypt_aes_gcm(ct)
                    values.append(float(v))
                except Exception as exc:
                    print(f"[T8 Pool] decrypt error for a ciphertext: {exc}")

            n_records = len(values)
            if duckdb is not None:
                conn = duckdb.connect(database=":memory:")
                conn.execute("CREATE TABLE IF NOT EXISTS patient_values(vien_phi DOUBLE)")
                if values:
                    conn.executemany("INSERT INTO patient_values VALUES(?)", [(v,) for v in values])
                if req.query_type == "count":
                    row = conn.execute("SELECT COUNT(*) FROM patient_values").fetchone()
                    result = float(row[0] if row and row[0] is not None else 0.0)
                elif req.query_type == "avg_vien_phi":
                    row = conn.execute("SELECT AVG(vien_phi) FROM patient_values").fetchone()
                    result = float(row[0] if row and row[0] is not None else 0.0)
                elif req.query_type == "sum_vien_phi":
                    row = conn.execute("SELECT SUM(vien_phi) FROM patient_values").fetchone()
                    result = float(row[0] if row and row[0] is not None else 0.0)
                else:
                    raise ValueError(f"Unknown query type: {req.query_type}")
                conn.close()
            else:
                # Fallback to python aggregation if DuckDB not available
                if not values:
                    result = 0.0
                elif req.query_type == "count":
                    result = float(len(values))
                elif req.query_type == "avg_vien_phi":
                    result = sum(values) / n_records
                elif req.query_type == "sum_vien_phi":
                    result = sum(values)
                else:
                    raise ValueError(f"Unknown query type: {req.query_type}")

            result_data = {
                "result": result,
                "n_records": n_records,
                "latency_ms": 0.0,
                "query_type": req.query_type,
            }
        except Exception:
            traceback.print_exc()
            raise
    else:
        if _mongo_collection is not None and _dte_cipher is not None and _ore_cipher is not None:
            result_data = _query_mongo_aggregate(QueryRequest(
                query_type=req.query_type,
                filters=normalized_filters,
                role=req.role,
            ))
        else:
            if STRICT_MODE:
                raise RuntimeError("Strict mode forbids fallback aggregate without Mongo/ciphertexts")
            result_data = _query_mock_aggregate(req.query_type, normalized_filters)
    latency_ms = (time.perf_counter() - t_start) * 1000

    return {
        "result": result_data["result"],
        "n_records": result_data["n_records"],
        "latency_ms": latency_ms,
        "query_type": req.query_type,
    }


def _load_department_private_key(dept: str) -> bytes:
    private_key = None
    if get_department_private_key is not None:
        try:
            private_key = get_department_private_key(dept)
            print(f"[T8 Pool] Loaded private key from Vault for dept={dept}")
        except Exception as exc:
            print(f"[T8 Pool] Vault private key load failed for {dept}: {exc}")

    if private_key is None and ALLOW_LOCAL_KEY_FALLBACK:
        local_path = REPO_ROOT / "crypto" / "data" / "keys" / f"{dept}_private.pem"
        if local_path.exists():
            private_key = local_path.read_bytes()
            print(f"[T8 Pool] Loaded private key from local dev file (explicit fallback): {dept}")

    if private_key is None:
        raise RuntimeError(f"Private key unavailable for department: {dept}")
    return private_key


def _execute_pii_query(req: PIIQueryRequest) -> Dict[str, object]:
    t_start = time.perf_counter()
    private_key = _load_department_private_key(req.dept)
    try:
        plaintext = ecc_decrypt(req.pii_enc, private_key)
        pii_data = json.loads(plaintext)
    finally:
        private_key = b"\x00" * len(private_key)
        del private_key

    return {
        "pii": pii_data,
        "dept": req.dept,
        "latency_ms": (time.perf_counter() - t_start) * 1000,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Application
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage ECALL pool startup and shutdown."""
    enclave_service.initialize()
    _load_runtime_keys()

    # Data source policy:
    # - mongo: require Mongo ciphertext path, fail startup if unavailable.
    # - mock: use in-memory mock data only.
    # - auto: prefer Mongo, fall back to mock.
    mongo_ready = False
    if DATA_MODE not in {"auto", "mongo", "mock"}:
        raise RuntimeError(f"Invalid T8_POOL_DATA_MODE={DATA_MODE}. Use auto|mongo|mock")

    if DATA_MODE in {"auto", "mongo"}:
        mongo_ready = _init_mongo_collection()
        if DATA_MODE == "mongo" and not mongo_ready:
            raise RuntimeError("T8_POOL_DATA_MODE=mongo but Mongo ciphertext path is unavailable")

    if not mongo_ready:
        if STRICT_MODE:
            raise RuntimeError("T8_STRICT_MODE=1 requires Mongo ciphertext path; mock fallback is disabled")
    if STRICT_MODE and not _keys_loaded:
        raise RuntimeError("T8_STRICT_MODE=1 requires gcm_dek loaded at startup")
    print("[T8 Pool] ECALL Task Pool started")
    print(f"  - DEK source: {_dek_source}")
    print(f"  - Workers: {POOL_WORKERS}")
    print(f"  - Port: {PORT}")
    print(f"  - Records: {'mongo' if mongo_ready else 0} | payload_bytes={POOL_PAYLOAD_BYTES} | indexed={USE_INDEXES}")
    if _mongo_collection is not None:
        print("  - Mode: Mongo ciphertext")
    else:
        print("  - Mode: DuckDB-backed simulation (gramine-direct)")
    print(f"  - Data mode policy: {DATA_MODE}")
    print(f"  - Strict mode: {STRICT_MODE}")
    try:
        yield
    finally:
        print("[T8 Pool] ECALL Task Pool shutting down...")
        _executor.shutdown(wait=True)
        print("[T8 Pool] Shutdown complete")


app = FastAPI(
    title="Enclave ECALL Task Pool",
    description="T8 - Concurrent medical query executor",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/attest", summary="Return signed attestation document")
async def attest_endpoint():
    """Return a signed simulation attestation document for Router verification."""
    return _make_attestation_payload()


@app.post("/query", response_model=QueryResponse, summary="Execute medical query")
async def query_endpoint(req: QueryRequest, request: Request):
    """
    Execute a medical aggregation query.
    
    - **query_type**: avg_vien_phi (average), sum_vien_phi (total), count (record count)
    - **filters**: Optional filters (ma_benh, tuoi_min_enc, etc.)
    - **role**: Access control (doctor, admin, researcher)
    
    Returns:
    - **result**: Query result (float)
    - **n_records**: Number of records matched
    - **latency_ms**: Query execution time in milliseconds
    """
    # Require internal auth if configured
    # Require Authorization: Bearer <JWT> (mTLS should also be enforced at TLS layer)
    auth_hdr = request.headers.get("authorization")
    if not auth_hdr:
        raise HTTPException(status_code=401, detail="missing Authorization header")
    validate_jwt_bearer(auth_hdr)

    try:
        # Submit to thread pool
        future = _executor.submit(_execute_medical_query, req)
        result = future.result(timeout=30)
        return QueryResponse(**result)
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Query timeout (>30s)")
    except Exception as e:
        print(f"[T8 Pool] Error: {repr(e)}", file=sys.stderr)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal error")


@app.post("/query/pii", response_model=PIIQueryResponse, summary="Decrypt patient PII in enclave")
async def query_pii_endpoint(req: PIIQueryRequest, request: Request):
    """Decrypt ECC-encrypted PII with the per-department private key inside the pool."""
    auth_hdr = request.headers.get("authorization")
    if not auth_hdr:
        raise HTTPException(status_code=401, detail="missing Authorization header")
    validate_jwt_bearer(auth_hdr)

    try:
        future = _executor.submit(_execute_pii_query, req)
        result = future.result(timeout=30)
        return PIIQueryResponse(**result)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="PII query timeout (>30s)")
    except Exception as e:
        print(f"[T8 Pool] PII error: {repr(e)}", file=sys.stderr)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal error")


@app.get("/health", response_model=HealthResponse, summary="Health check")
async def health_endpoint():
    """
    Health check endpoint.
    
    Returns:
    - **status**: Service status (ok/degraded)
    - **keys_loaded**: List of loaded keys
    - **workers**: Thread pool size
    - **uptime_s**: Service uptime in seconds
    - **port**: Service port
    """
    uptime = time.time() - _start_time
    # Service only considered fully healthy when the GCM DEK is loaded into RAM
    status = "ok" if _keys_loaded else "degraded"
    return HealthResponse(
        status=status,
        keys_loaded=list(_keys.keys()),
        workers=POOL_WORKERS,
        uptime_s=uptime,
        port=PORT
    )


@app.get("/stats", summary="Pool statistics")
async def stats_endpoint():
    """Get pool statistics (for monitoring)."""
    return {
        "workers": POOL_WORKERS,
        "active_threads": _executor._work_queue.qsize() if hasattr(_executor, '_work_queue') else 'unknown',
        "mock_records": len(MOCK_PATIENT_DATA),
        "payload_bytes": POOL_PAYLOAD_BYTES,
        "indexed": USE_INDEXES,
        "keys_loaded": len(_keys),
        "dek_source": _dek_source,
        "strict_mode": STRICT_MODE,
        "legacy_dte_aliases_enabled": ALLOW_LEGACY_DTE_CODES,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """Start the ECALL Task Pool service."""
    print("=" * 70)
    print("T8 - ECALL Task Pool (FastAPI)")
    print("=" * 70)
    print()
    print(f"Service: http://{HOST}:{PORT}")
    print(f"Workers: {POOL_WORKERS}")
    print()
    print("Endpoints:")
    print(f"  POST   /query        - Execute medical query")
    print(f"  GET    /health       - Health check")
    print(f"  GET    /stats        - Pool statistics")
    print(f"  GET    /docs         - API documentation (Swagger)")
    print()
    print("=" * 70)
    print()

    ssl_certfile = os.environ.get("T8_SSL_CERT")
    ssl_keyfile = os.environ.get("T8_SSL_KEY")
    ssl_ca_certs = os.environ.get("T8_SSL_CA")
    ssl_kwargs = {}
    if ssl_certfile and ssl_keyfile:
        ssl_kwargs["ssl_certfile"] = ssl_certfile
        ssl_kwargs["ssl_keyfile"] = ssl_keyfile
        if ssl_ca_certs:
            ssl_kwargs["ssl_ca_certs"] = ssl_ca_certs
            ssl_kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
        print(f"[T8 Pool] TLS enabled with cert={ssl_certfile} key={ssl_keyfile} ca={ssl_ca_certs or 'n/a'}")
    
    # Run with uvicorn
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=True
        , **ssl_kwargs
    )


if __name__ == "__main__":
    main()
