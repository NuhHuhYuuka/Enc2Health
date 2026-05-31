"""Software-mode execution against MongoDB ciphertext.

This module covers the project script's software path: equality and range
operators run outside the enclave on deterministic / order-preserving
encrypted columns, while count can use the same query pipeline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
import random

from pymongo import MongoClient
from bson import ObjectId

from crypto.crypto.dte import DTECipher
from crypto.crypto.gcm import AESGCMCipher
from crypto.crypto.ore import ORECipher


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DEFAULT_DB_NAME = os.environ.get("MONGO_DB", "enc2health")
DEFAULT_COLLECTION = os.environ.get("MONGO_COLLECTION", "patient_records")
KEY_DIR = Path(os.environ.get("ENC2HEALTH_KEY_DIR", REPO_ROOT / "crypto" / "data" / "keys"))
# Expect clients to provide real ICD-10 codes (e.g., "E11").
ICD10_ALIASES = {}
STRICT_MODE = os.environ.get("SOFTWARE_STRICT_MODE", "0") == "1"


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
        self._dte_ma_benh = self._load_dte_cipher("dte_ma_benh.key")
        self._dte_khoa = self._load_dte_cipher("dte_khoa.key")
        self._gcm = self._load_gcm_cipher("gcm_dek.key")
        self._ore = self._load_ore_cipher("ore.key")
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

    def _build_filter(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        query: Dict[str, Any] = {}

        if "ma_benh" in filters and self._dte_ma_benh is not None:
            ma_benh = str(filters["ma_benh"])
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
        diseases = ["E11", "I10", "J18", "K29", "M54", "N18"]
        departments = ["Noi", "Ngoai", "Cap_cuu", "Tim_mach", "Than_kinh", "Nhi"]
        records: list[dict[str, Any]] = []
        for index in range(n_records):
            records.append(
                {
                    "ma_benh": diseases[index % len(diseases)],
                    "tuoi": 18 + (index % 78),
                    "khoa": departments[index % len(departments)],
                    "vien_phi": float(900 + (index % 9100)),
                }
            )
        return records

    def _fallback_count(self, filters: Dict[str, Any]) -> int:
        records = self._fallback_records
        if "ma_benh" in filters:
            ma_benh = str(filters["ma_benh"])
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

    def _fallback_aggregate(self, query_type: str, filters: Dict[str, Any]) -> SoftwareQueryResult:
        records = self._fallback_records
        if "ma_benh" in filters:
            ma_benh = str(filters["ma_benh"])
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

        projection = {"pii_enc": 1, "dept": 1, "khoa_phong_plaintext": 1, "patient_id": 1}
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

        return {
            "patient_id": record.get("patient_id", patient_id),
            "pii_enc": record["pii_enc"],
            "dept": dept,
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
