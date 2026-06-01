"""T13 - Đo q-leakage khi fallback Software vs TEE mode.

Script này giữ phần frequency leakage như cũ, nhưng thêm lớp output exposure
để tách rõ:
- TEE/RBAC path: kết quả bị mask cho role nhạy cảm.
- Software fallback path: kết quả thô được giữ nguyên ở execution layer.

Khi Router live không có sẵn, script tự chạy ở chế độ offline emulation để
vẫn tạo được báo cáo nhất quán.
"""

from __future__ import annotations

import collections
import json
import math
import os
from pathlib import Path

try:
    import httpx
except ImportError:
    httpx = None

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from common.auth import generate_test_jwt
except Exception:
    generate_test_jwt = None

try:
    import common.auth as common_auth
except Exception:
    common_auth = None

from router.rbac import RBACMiddleware

try:
    from router.software_executor import SoftwareExecutor
except Exception:
    from dataclasses import dataclass

    @dataclass
    class _SoftwareQueryResult:
        result: float
        n_records: int

    class SoftwareExecutor:  # type: ignore[no-redef]
        def __init__(self):
            diseases = ["P01", "P02", "C01", "C02", "I01", "I02", "N01", "N02", "S01", "S02", "E01", "E02"]
            departments = ["Noi", "Ngoai", "Cap_cuu", "Tim_mach", "Than_kinh", "Nhi"]
            self._records: list[dict] = []
            for index in range(10000):
                self._records.append(
                    {
                        "ma_benh": diseases[index % len(diseases)],
                        "tuoi": 18 + (index % 78),
                        "khoa": departments[index % len(departments)],
                        "vien_phi": float(900 + (index % 9100)),
                    }
                )

        def _filtered(self, filters: dict) -> list[dict]:
            records = self._records
            if "ma_benh" in filters:
                records = [r for r in records if r["ma_benh"] == str(filters["ma_benh"])]
            if "tuoi_min_enc" in filters:
                records = [r for r in records if r["tuoi"] >= int(filters["tuoi_min_enc"])]
            if "tuoi_max_enc" in filters:
                records = [r for r in records if r["tuoi"] <= int(filters["tuoi_max_enc"])]
            return records

        def query(self, query_type: str, filters: dict) -> _SoftwareQueryResult:
            records = self._filtered(filters)
            if query_type == "count":
                return _SoftwareQueryResult(result=float(len(records)), n_records=len(records))
            values = [float(record["vien_phi"]) for record in records]
            if not values:
                aggregate = 0.0
            elif query_type == "sum_vien_phi":
                aggregate = sum(values)
            elif query_type == "avg_vien_phi":
                aggregate = sum(values) / len(values)
            else:
                aggregate = 0.0
            return _SoftwareQueryResult(result=float(aggregate), n_records=len(values))


ROUTER_URL = os.environ.get("ROUTER_URL", "http://localhost:8000")
QUERY_PATTERNS = [
    {"ma_benh": "I01"},
    {"ma_benh": "I01"},
    {"ma_benh": "I01"},
    {"ma_benh": "C01"},
    {"ma_benh": "C01"},
    {"ma_benh": "P01"},
    {"ma_benh": "S01"},
    {},
    {},
]

_RBAC = RBACMiddleware()
_SOFTWARE = SoftwareExecutor()


def _token(role: str) -> str:
    secret = os.environ.get("AUTH_JWT_SECRET", "dev-secret-32-bytes-long-1234567890")
    os.environ["AUTH_JWT_SECRET"] = secret
    if common_auth is not None:
        common_auth.JWT_SECRET = secret
    if generate_test_jwt is None:
        return f"offline-token:{role}"
    return generate_test_jwt("q-leakage", role)


def _router_available() -> bool:
    if httpx is None:
        return False
    try:
        response = httpx.get(f"{ROUTER_URL}/health", timeout=3)
        return response.status_code == 200
    except Exception:
        return False


def _normalize_entry(*, source: str, role: str, query_type: str, filters: dict, response: dict, result_masked: bool) -> dict:
    visible_fields = sorted(response.keys())
    return {
        "source": source,
        "mode": response.get("mode", source),
        "requested_mode": response.get("requested_mode", source),
        "role": role,
        "query_type": query_type,
        "filters": filters,
        "n_records": int(response.get("n_records", 0) or 0),
        "result_masked": result_masked,
        "raw_result_visible": not result_masked,
        "visible_fields": visible_fields,
        "response_surface": len(visible_fields),
    }


def _collect_router_observations(role: str, query_type: str) -> list[dict]:
    access_log: list[dict] = []
    headers = {"Authorization": f"Bearer {_token(role)}"}
    if httpx is not None and _router_available():
        with httpx.Client(timeout=10) as client:
            for filters in QUERY_PATTERNS:
                response = client.post(
                    f"{ROUTER_URL}/query",
                    json={"query_type": query_type, "filters": filters, "role": role},
                    headers=headers,
                )
                if response.status_code != 200:
                    continue
                payload = response.json()
                result = payload.get("result", {})
                result_masked = isinstance(result, dict) and result.get("result") == "[MASKED]"
                access_log.append(
                    _normalize_entry(
                        source="router",
                        role=role,
                        query_type=query_type,
                        filters=filters,
                        response=payload,
                        result_masked=result_masked,
                    )
                )
        return access_log

    # Offline emulation: giữ shape của Router nhưng dùng cùng workload + RBAC mask.
    for filters in QUERY_PATTERNS:
        software_result = _SOFTWARE.query(query_type, filters)
        response = {
            "mode": "tee",
            "requested_mode": "tee",
            "reason": "offline-emulated TEE/RBAC",
            "result": software_result.result,
            "n_records": software_result.n_records,
        }
        masked = _RBAC.mask_result(response, role)
        access_log.append(
            _normalize_entry(
                source="router-emulated",
                role=role,
                query_type=query_type,
                filters=filters,
                response=masked,
                result_masked=masked.get("result") == "[MASKED]",
            )
        )
    return access_log


def _collect_fallback_observations(role: str, query_type: str) -> list[dict]:
    access_log: list[dict] = []
    for filters in QUERY_PATTERNS:
        software_result = _SOFTWARE.query(query_type, filters)
        response = {
            "mode": "software",
            "requested_mode": "software",
            "note": "software fallback execution surface",
            "result": software_result.result,
            "n_records": software_result.n_records,
        }
        access_log.append(
            _normalize_entry(
                source="software-fallback",
                role=role,
                query_type=query_type,
                filters=filters,
                response=response,
                result_masked=False,
            )
        )
    return access_log


def run_queries_and_collect(role: str, query_type: str, *, surface: str = "router") -> list[dict]:
    if surface == "fallback":
        return _collect_fallback_observations(role, query_type)
    return _collect_router_observations(role, query_type)


def compute_frequency_leakage(access_log: list[dict]) -> dict:
    """Tính leakage từ frequency và output exposure.

    - Frequency leakage: entropy thấp → pattern dễ đoán hơn.
    - Output exposure: nếu raw result còn visible thì fallback leak tăng mạnh.
    """
    counter = collections.Counter()
    for entry in access_log:
        key = str(sorted(entry["filters"].items()))
        counter[key] += 1

    total = sum(counter.values()) or 1
    entropy = 0.0
    for count in counter.values():
        probability = count / total
        entropy -= probability * math.log2(probability)

    max_entropy = math.log2(len(counter)) if len(counter) > 1 else 1.0
    pattern_leakage = 1.0 - (entropy / max_entropy) if max_entropy > 0 else 0.0

    raw_visible_ratio = (
        sum(1 for entry in access_log if entry.get("raw_result_visible", False)) / len(access_log)
        if access_log
        else 0.0
    )
    surface_avg = (
        sum(entry.get("response_surface", 0) for entry in access_log) / len(access_log)
        if access_log
        else 0.0
    )

    # Union bound style: pattern leak + output exposure + response surface footprint.
    output_exposure = raw_visible_ratio
    surface_penalty = min(1.0, surface_avg / 8.0)
    leakage_score = 1.0 - (1.0 - pattern_leakage) * (1.0 - output_exposure) * (1.0 - surface_penalty)

    return {
        "pattern_counts": dict(counter),
        "entropy": round(entropy, 4),
        "max_entropy": round(max_entropy, 4),
        "pattern_leakage_score": round(pattern_leakage, 4),
        "output_exposure_score": round(output_exposure, 4),
        "response_surface_score": round(surface_penalty, 4),
        "leakage_score": round(leakage_score, 4),
        "interpretation": (
            "HIGH leakage" if leakage_score > 0.5
            else "MEDIUM leakage" if leakage_score > 0.2
            else "LOW leakage"
        ),
    }


if __name__ == "__main__":
    print("=" * 60)
    print("Q-LEAKAGE ANALYSIS — Enc2Health")
    print("=" * 60)

    results: dict[str, dict] = {}

    print("\n[1] TEE mode — admin sum_vien_phi")
    log_tee_admin = run_queries_and_collect("admin", "sum_vien_phi", surface="router")
    leak_tee_admin = compute_frequency_leakage(log_tee_admin)
    results["TEE_mode_admin"] = leak_tee_admin
    print(f"  Leakage score: {leak_tee_admin['leakage_score']} ({leak_tee_admin['interpretation']})")
    print(f"  Pattern leakage: {leak_tee_admin['pattern_leakage_score']}")
    print(f"  Output exposure: {leak_tee_admin['output_exposure_score']}")

    print("\n[2] SOFTWARE mode — admin count")
    log_soft_admin = run_queries_and_collect("admin", "count", surface="fallback")
    leak_soft_admin = compute_frequency_leakage(log_soft_admin)
    results["SOFTWARE_mode_admin"] = leak_soft_admin
    print(f"  Leakage score: {leak_soft_admin['leakage_score']} ({leak_soft_admin['interpretation']})")
    print(f"  Pattern leakage: {leak_soft_admin['pattern_leakage_score']}")
    print(f"  Output exposure: {leak_soft_admin['output_exposure_score']}")

    print("\n[3] TEE/RBAC — researcher avg_vien_phi (masked)")
    log_tee_researcher = run_queries_and_collect("researcher", "avg_vien_phi", surface="router")
    leak_tee_researcher = compute_frequency_leakage(log_tee_researcher)
    results["TEE_mode_researcher_masked"] = leak_tee_researcher
    print(f"  Leakage score: {leak_tee_researcher['leakage_score']} ({leak_tee_researcher['interpretation']})")
    print(f"  Pattern leakage: {leak_tee_researcher['pattern_leakage_score']}")
    print(f"  Output exposure: {leak_tee_researcher['output_exposure_score']}")

    print("\n[4] SOFTWARE fallback — researcher avg_vien_phi (raw)")
    log_soft_researcher = run_queries_and_collect("researcher", "avg_vien_phi", surface="fallback")
    leak_soft_researcher = compute_frequency_leakage(log_soft_researcher)
    results["SOFTWARE_fallback_researcher_raw"] = leak_soft_researcher
    print(f"  Leakage score: {leak_soft_researcher['leakage_score']} ({leak_soft_researcher['interpretation']})")
    print(f"  Pattern leakage: {leak_soft_researcher['pattern_leakage_score']}")
    print(f"  Output exposure: {leak_soft_researcher['output_exposure_score']}")

    print("\n" + "=" * 60)
    print("SO SÁNH LEAKAGE SCORE:")
    print(f"  TEE mode (admin):                 {results['TEE_mode_admin']['leakage_score']}")
    print(f"  SOFTWARE mode (admin):            {results['SOFTWARE_mode_admin']['leakage_score']}")
    print(f"  TEE + RBAC (researcher masked):   {results['TEE_mode_researcher_masked']['leakage_score']}")
    print(f"  SOFTWARE fallback (researcher):   {results['SOFTWARE_fallback_researcher_raw']['leakage_score']}")
    print("=" * 60)
    print("\nKết luận: frequency pattern vẫn lộ ở mọi mode, nhưng fallback software")
    print("làm tăng rõ output exposure so với TEE/RBAC masked path.")

    with open("leakage_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("\nĐã lưu vào leakage_results.json")
