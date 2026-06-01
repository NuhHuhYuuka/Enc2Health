"""Software-mode execution against MongoDB ciphertext.

This module covers the project script's software path: equality and range
operators run outside the enclave on deterministic / order-preserving
encrypted columns, while count can use the same query pipeline.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
import random

from pymongo import MongoClient
from bson import ObjectId

from crypto.crypto.dte import DTECipher
from crypto.crypto.gcm import AESGCMCipher
from crypto.crypto.ore import ORECipher
from crypto.crypto.sse import StaticSSECipher, normalize_keyword, tokenize_text


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DEFAULT_DB_NAME = os.environ.get("MONGO_DB", "enc2health")
DEFAULT_COLLECTION = os.environ.get("MONGO_COLLECTION", "patient_records")
DEFAULT_SSE_COLLECTION = os.environ.get("SSE_COLLECTION", "sse_index")
KEY_DIR = Path(os.environ.get("ENC2HEALTH_KEY_DIR", REPO_ROOT / "crypto" / "data" / "keys"))
# Expect clients to provide demo disease codes (e.g., "I01").
ICD10_ALIASES = {}
STRICT_MODE = os.environ.get("SOFTWARE_STRICT_MODE", "0") == "1"
DEMO_DISEASE_CODE_RE = re.compile(r"^[A-Za-z]\d{2}$")


def canonicalize_disease_code(value: Any) -> str:
    """Keep demo disease-code letters uppercase: p01 -> P01."""
    text = str(value).strip()
    return text.upper() if DEMO_DISEASE_CODE_RE.fullmatch(text) else text


@dataclass
class SoftwareQueryResult:
    result: float
    n_records: int


class SoftwareExecutor:
    def __init__(self, mongo_uri: str = DEFAULT_MONGO_URI):
        self.mongo_uri = mongo_uri
        timeout_ms = int(os.environ.get("SOFTWARE_MONGO_TIMEOUT_MS", "300"))
        self.client = MongoClient(
            self.mongo_uri,
            serverSelectionTimeoutMS=timeout_ms,
            connectTimeoutMS=timeout_ms,
            socketTimeoutMS=timeout_ms,
        )
        self.collection = self.client[DEFAULT_DB_NAME][DEFAULT_COLLECTION]
        self.sse_collection = self.client[DEFAULT_DB_NAME][DEFAULT_SSE_COLLECTION]
        self._dte_ma_benh = self._load_dte_cipher("dte_ma_benh.key")
        self._dte_khoa = self._load_dte_cipher("dte_khoa.key")
        self._dte_cmnd = self._load_dte_cipher("dte_cmnd.key")
        self._gcm = self._load_gcm_cipher("gcm_dek.key")
        self._ore = self._load_ore_cipher("ore.key")
        self._sse = self._load_sse_cipher("sse.key")
        self._fallback_records = self._build_fallback_records(int(os.environ.get("EHR_RECORD_COUNT", "10000")))
        self._mongo_available = self._detect_mongo_available()
        if STRICT_MODE and not self._mongo_available:
            raise RuntimeError("SOFTWARE_STRICT_MODE=1 but MongoDB is unavailable")

    def _load_dte_cipher(self, filename: str) -> DTECipher | None:
        path = KEY_DIR / filename
        if not path.exists():
            return None
        return DTECipher.load_key(str(path))

    def _load_ore_cipher(self, filename: str) -> ORECipher | None:
        path = KEY_DIR / filename
        if not path.exists():
            return None
        return ORECipher.load_key(str(path))

    def _load_gcm_cipher(self, filename: str) -> AESGCMCipher | None:
        path = KEY_DIR / filename
        if not path.exists():
            return None
        return AESGCMCipher.load_key(str(path))

    def _load_sse_cipher(self, filename: str) -> StaticSSECipher | None:
        path = KEY_DIR / filename
        if not path.exists():
            return None
        return StaticSSECipher.load_key(str(path))

    def _build_filter(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        query: Dict[str, Any] = {}

        if "ma_benh" in filters and self._dte_ma_benh is not None:
            ma_benh = canonicalize_disease_code(filters["ma_benh"])
            query["ma_benh_enc"] = self._dte_ma_benh.encrypt(
                ma_benh,
                b"field:ma_benh",
            )

        # ABAC dept-scoping: lọc theo khoa trên cột DTE (CSP chỉ thấy ciphertext).
        if "khoa_phong" in filters and self._dte_khoa is not None:
            query["khoa_phong_enc"] = self._dte_khoa.encrypt(
                str(filters["khoa_phong"]),
                b"field:khoa_phong",
            )

        if "tuoi_min_enc" in filters and self._ore is not None:
            query["tuoi_enc"] = {"$gte": self._ore.encrypt_age(int(filters["tuoi_min_enc"]))}

        if "tuoi_max_enc" in filters and self._ore is not None:
            query.setdefault("tuoi_enc", {})["$lte"] = self._ore.encrypt_age(int(filters["tuoi_max_enc"]))

        return query

    def _build_fallback_records(self, n_records: int) -> list[dict[str, Any]]:
        diseases_by_dept = {
            "Noi": ["I01", "I02"],
            "Ngoai": ["S01", "S02"],
            "Cap_cuu": ["E01", "E02"],
            "Tim_mach": ["C01", "C02"],
            "Than_kinh": ["N01", "N02"],
            "Nhi": ["P01", "P02"],
        }
        departments = list(diseases_by_dept)
        records: list[dict[str, Any]] = []
        for index in range(n_records):
            dept = departments[index % len(departments)]
            records.append(
                {
                    "ma_benh": diseases_by_dept[dept][index % 2],
                    "tuoi": 1 + (index % 15) if dept == "Nhi" else 16 + (index % 80),
                    "khoa": dept,
                    "vien_phi": float(900 + (index % 9100)),
                }
            )
        return records

    def _fallback_count(self, filters: Dict[str, Any]) -> int:
        records = self._fallback_records
        if "ma_benh" in filters:
            ma_benh = canonicalize_disease_code(filters["ma_benh"])
            records = [r for r in records if r["ma_benh"] == ma_benh]
        if "khoa_phong" in filters:
            records = [r for r in records if r["khoa"] == str(filters["khoa_phong"])]
        if "tuoi_min_enc" in filters:
            min_age = int(filters["tuoi_min_enc"])
            records = [r for r in records if r["tuoi"] >= min_age]
        if "tuoi_max_enc" in filters:
            max_age = int(filters["tuoi_max_enc"])
            records = [r for r in records if r["tuoi"] <= max_age]
        return len(records)

    def _fallback_keyword_search(self, keyword: str, filters: Dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_keyword(keyword)
        rows = []
        for index, record in enumerate(self._fallback_records):
            haystack = " ".join([record["ma_benh"], record["khoa"]]).casefold()
            if normalized and normalized in haystack:
                rows.append({"patient_id": f"fallback-{index}", "dept": record["khoa"]})
        if "khoa_phong" in filters:
            rows = [row for row in rows if row["dept"] == str(filters["khoa_phong"])]
        return {
            "keyword": normalized,
            "token": None,
            "n_records": len(rows),
            "volume_leakage": len(rows),
            "postings": rows,
            "note": "SSE fallback mock path",
        }

    def _fallback_aggregate(self, query_type: str, filters: Dict[str, Any]) -> SoftwareQueryResult:
        records = self._fallback_records
        if "ma_benh" in filters:
            ma_benh = canonicalize_disease_code(filters["ma_benh"])
            records = [r for r in records if r["ma_benh"] == ma_benh]
        if "khoa_phong" in filters:
            records = [r for r in records if r["khoa"] == str(filters["khoa_phong"])]
        if "tuoi_min_enc" in filters:
            min_age = int(filters["tuoi_min_enc"])
            records = [r for r in records if r["tuoi"] >= min_age]
        if "tuoi_max_enc" in filters:
            max_age = int(filters["tuoi_max_enc"])
            records = [r for r in records if r["tuoi"] <= max_age]

        if query_type == "count":
            n_records = len(records)
            return SoftwareQueryResult(result=float(n_records), n_records=n_records)

        values = [float(record["vien_phi"]) for record in records]
        n_records = len(values)
        if not values:
            aggregate = 0.0
        elif query_type == "sum_vien_phi":
            aggregate = sum(values)
        elif query_type == "avg_vien_phi":
            aggregate = sum(values) / n_records
        else:
            raise ValueError(f"Unknown query type: {query_type}")
        return SoftwareQueryResult(result=float(aggregate), n_records=n_records)

    def _decrypt_vien_phi(self, ciphertext: str) -> float:
        if self._gcm is None:
            raise RuntimeError("gcm_dek key not available")
        plaintext = self._gcm.decrypt_json(ciphertext)
        if isinstance(plaintext, (int, float)):
            return float(plaintext)
        return float(plaintext)

    def _query_mongo_records(self, filters: Dict[str, Any]) -> list[dict[str, Any]]:
        query = self._build_filter(filters)
        return list(self.collection.find(query, {"vien_phi_enc": 1, "_id": 0}))

    @property
    def mongo_available(self) -> bool:
        return self._mongo_available

    def fetch_vien_phi_ciphertexts(self, filters: Dict[str, Any]) -> list[str]:
        """Lấy danh sách ciphertext `vien_phi_enc` khớp filter (KHÔNG giải mã).

        Router dùng hàm này ở TEE mode để gom bản mã rồi đẩy vào Enclave — Cloud
        chỉ thấy ciphertext, việc giải mã + tính toán xảy ra bên trong enclave.
        Trả về [] nếu MongoDB không khả dụng (router sẽ quay về luồng pool cũ).
        """
        if not self._mongo_available:
            if STRICT_MODE:
                raise RuntimeError("SOFTWARE_STRICT_MODE=1 forbids ciphertext fallback without MongoDB")
            return []
        try:
            rows = self._query_mongo_records(filters)
            return [row["vien_phi_enc"] for row in rows if row.get("vien_phi_enc")]
        except Exception:
            if STRICT_MODE:
                raise
            return []

    def fetch_patient_pii_ciphertext(self, patient_id: str) -> dict[str, Any]:
        """Lấy `pii_enc` và khoa của một bệnh nhân từ MongoDB, không giải mã."""
        if not self._mongo_available:
            raise RuntimeError("MongoDB unavailable; cannot fetch patient PII ciphertext")

        candidates: list[dict[str, Any]] = [{"patient_id": patient_id}]
        if ObjectId.is_valid(patient_id):
            candidates.append({"_id": ObjectId(patient_id)})

        projection = {
            "pii_enc": 1,
            "dept": 1,
            "khoa_phong_plaintext": 1,
            "patient_id": 1,
            "ma_benh_enc": 1,
            "chan_doan_enc": 1,
        }
        record = None
        for query in candidates:
            record = self.collection.find_one(query, projection)
            if record is not None:
                break

        if record is None:
            raise LookupError(f"Patient not found: {patient_id}")
        if not record.get("pii_enc"):
            raise RuntimeError(
                "Patient record has no pii_enc; reseed with "
                "EHR_FORCE_RECREATE=1 python3 crypto/data/generate_ehr.py"
            )

        dept = record.get("dept") or record.get("khoa_phong_plaintext")
        if not dept:
            raise RuntimeError(f"Patient record missing department: {patient_id}")

        ma_benh = ""
        chan_doan = ""
        if record.get("ma_benh_enc") and self._dte_ma_benh:
            ma_benh = self._dte_ma_benh.decrypt(record["ma_benh_enc"], b"field:ma_benh")
        if record.get("chan_doan_enc") and self._gcm:
            chan_doan = self._gcm.decrypt_json(record["chan_doan_enc"])

        return {
            "patient_id": record.get("patient_id", patient_id),
            "pii_enc": record["pii_enc"],
            "dept": dept,
            "ma_benh": ma_benh,
            "chan_doan": chan_doan,
        }

    def fetch_patient_pii_ciphertext_by_cmnd(self, cmnd: str) -> dict[str, Any]:
        """Lấy `pii_enc` và khoa của một bệnh nhân từ MongoDB bằng cmnd (CCCD)."""
        if not self._mongo_available:
            raise RuntimeError("MongoDB unavailable; cannot fetch patient PII by CCCD")
        if self._dte_cmnd is None:
            raise RuntimeError("dte_cmnd key not available")

        cmnd_dte_ciphertext = self._dte_cmnd.encrypt(cmnd, b"field:cmnd")

        projection = {
            "pii_enc": 1,
            "dept": 1,
            "khoa_phong_plaintext": 1,
            "patient_id": 1,
            "ma_benh_enc": 1,
            "chan_doan_enc": 1,
        }
        record = self.collection.find_one({"cmnd_dte": cmnd_dte_ciphertext}, projection)

        if record is None:
            raise LookupError(f"Không tìm thấy bệnh nhân có CCCD: {cmnd}")
        if not record.get("pii_enc"):
            raise RuntimeError(
                "Patient record has no pii_enc; reseed with "
                "EHR_FORCE_RECREATE=1 python3 crypto/data/generate_ehr.py"
            )

        dept = record.get("dept") or record.get("khoa_phong_plaintext")
        if not dept:
            raise RuntimeError(f"Patient record missing department for CCCD: {cmnd}")

        ma_benh = ""
        chan_doan = ""
        if record.get("ma_benh_enc") and self._dte_ma_benh:
            ma_benh = self._dte_ma_benh.decrypt(record["ma_benh_enc"], b"field:ma_benh")
        if record.get("chan_doan_enc") and self._gcm:
            chan_doan = self._gcm.decrypt_json(record["chan_doan_enc"])

        return {
            "patient_id": record.get("patient_id"),
            "pii_enc": record["pii_enc"],
            "dept": dept,
            "ma_benh": ma_benh,
            "chan_doan": chan_doan,
        }

    def keyword_search(self, keyword: str, filters: Dict[str, Any] | None = None, limit: int = 20) -> dict[str, Any]:
        """Search static SSE index by keyword token.

        Server-side Mongo sees only the deterministic HMAC token and result
        volume. Router decrypts posting lists with the SSE key, matching the
        project's Software-mode SSE baseline.
        """
        filters = filters or {}
        display_keyword = canonicalize_disease_code(keyword)
        normalized = normalize_keyword(display_keyword)
        if not normalized:
            raise ValueError("keyword is required")
        if not self._mongo_available:
            if STRICT_MODE:
                raise RuntimeError("SOFTWARE_STRICT_MODE=1 forbids SSE fallback path")
            return self._fallback_keyword_search(normalized, filters)
        if self._sse is None:
            raise RuntimeError("sse key not available")

        exact_token = self._sse.token(normalized)
        row = self.sse_collection.find_one({"token": exact_token}, {"_id": 0})
        query_tokens = [exact_token]
        rows = [row] if row else []

        if not rows:
            keyword_tokens = tokenize_text(normalized)
            if len(keyword_tokens) > 1:
                query_tokens = [self._sse.token(token) for token in keyword_tokens]
                rows = list(self.sse_collection.find({"token": {"$in": query_tokens}}, {"_id": 0}))

        if not rows:
            return {
                "keyword": display_keyword if display_keyword != normalized else normalized,
                "token": exact_token,
                "tokens": query_tokens,
                "n_records": 0,
                "volume_leakage": 0,
                "postings": [],
                "note": "Static SSE encrypted index",
            }

        posting_sets: list[dict[str, dict[str, Any]]] = []
        volume_leakage = 0
        for matched_row in rows:
            volume_leakage += int(matched_row.get("n_records", 0))
            postings_for_token = self._sse.decrypt_postings(matched_row["postings_enc"])
            posting_sets.append({str(posting["patient_id"]): posting for posting in postings_for_token})

        common_ids = set(posting_sets[0])
        for posting_set in posting_sets[1:]:
            common_ids &= set(posting_set)
        postings = [posting_sets[0][patient_id] for patient_id in sorted(common_ids)]

        if "khoa_phong" in filters:
            dept = str(filters["khoa_phong"])
            postings = [posting for posting in postings if posting.get("dept") == dept]
        filtered_records = len(postings)
        postings = postings[: max(0, int(limit))]
        return {
            "keyword": display_keyword if display_keyword != normalized else normalized,
            "token": exact_token if len(query_tokens) == 1 else None,
            "tokens": query_tokens,
            "n_records": len(common_ids),
            "filtered_records": filtered_records,
            "volume_leakage": volume_leakage,
            "postings": postings,
            "note": "Static SSE encrypted index",
        }

    def query(self, query_type: str, filters: Dict[str, Any]) -> SoftwareQueryResult:
        if not self._mongo_available:
            if STRICT_MODE:
                raise RuntimeError("SOFTWARE_STRICT_MODE=1 forbids mock fallback path")
            return self._fallback_aggregate(query_type, filters)

        try:
            if query_type == "count":
                return self.count(filters)

            rows = self._query_mongo_records(filters)
            values = [self._decrypt_vien_phi(row["vien_phi_enc"]) for row in rows if row.get("vien_phi_enc")]
            n_records = len(values)
            if not values:
                aggregate = 0.0
            elif query_type == "sum_vien_phi":
                aggregate = sum(values)
            elif query_type == "avg_vien_phi":
                aggregate = sum(values) / n_records
            else:
                raise ValueError(f"Unknown query type: {query_type}")
            return SoftwareQueryResult(result=float(aggregate), n_records=n_records)
        except Exception:
            if STRICT_MODE:
                raise
            return self._fallback_aggregate(query_type, filters)

    def _detect_mongo_available(self) -> bool:
        try:
            self.client.admin.command("ping")
            return True
        except Exception:
            return False

    def count(self, filters: Dict[str, Any]) -> SoftwareQueryResult:
        if not self._mongo_available:
            if STRICT_MODE:
                raise RuntimeError("SOFTWARE_STRICT_MODE=1 forbids mock fallback path")
            n_records = self._fallback_count(filters)
        else:
            query = self._build_filter(filters)
            try:
                n_records = self.collection.count_documents(query)
            except Exception:
                if STRICT_MODE:
                    raise
                n_records = self._fallback_count(filters)
        return SoftwareQueryResult(result=float(n_records), n_records=n_records)

    def close(self) -> None:
        self.client.close()
