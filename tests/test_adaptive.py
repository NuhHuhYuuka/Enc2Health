# T7 - Test kịch bản EPC saturation
import os
import time

import httpx

import common.auth as auth
from common.auth import generate_test_jwt

ROUTER_URL = "http://localhost:8000"


def _headers() -> dict[str, str]:
    secret = os.environ.get("AUTH_JWT_SECRET", "dev-secret-32-bytes-long-1234567890")
    os.environ["AUTH_JWT_SECRET"] = secret
    auth.JWT_SECRET = secret
    token = generate_test_jwt("adaptive-test", "admin")
    return {"Authorization": f"Bearer {token}"}


def _simulate_pressure(pressure: float | None):
    return httpx.post(
        f"{ROUTER_URL}/adaptive/simulate",
        params={"pressure": pressure} if pressure is not None else {},
        timeout=5,
    )


def _wait_for_mode(expected: str, timeout_s: float = 5.0) -> dict:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        r = httpx.get(f"{ROUTER_URL}/adaptive", timeout=5)
        last = r.json()
        if last["system_mode"] == expected:
            return last
        time.sleep(0.2)
    raise AssertionError(f"Adaptive mode did not reach {expected!r}; last={last}")


def _trigger_tee_query() -> httpx.Response:
    return httpx.post(
        f"{ROUTER_URL}/query",
        json={"query_type": "avg_vien_phi", "filters": {}, "role": "admin"},
        headers=_headers(),
        timeout=30,
    )

def test_epc_saturation():
    print("=" * 60)
    print("T7 — EPC Saturation Test")
    print("=" * 60)

    # Trạng thái ban đầu
    r = httpx.get(f"{ROUTER_URL}/adaptive")
    before = r.json()
    print(f"\n[Before] mode={before['system_mode']} | ratio={before['probe']['ratio']}")

    # Mô phỏng áp lực EPC để kiểm tra fallback/restore một cách tất định.
    # Unit test đã kiểm tra state machine; ở đây chỉ xác nhận endpoint live hoạt động.
    print("\n[Load] Simulating EPC pressure = 0.90...")
    _simulate_pressure(0.90)
    query = _trigger_tee_query()
    assert query.status_code == 200
    status = _wait_for_mode("fallback")
    print(f"  ✅ FALLBACK triggered! mode={status['system_mode']} | ratio={status['pressure_ratio']} | switches={status['switch_count']}")

    print("\n[Load] Releasing EPC pressure = 0.30...")
    _simulate_pressure(0.30)
    query = _trigger_tee_query()
    assert query.status_code == 200
    after = _wait_for_mode("normal")
    print(f"\n[After] mode={after['system_mode']} | switches={after['switch_count']}")
    if after['switch_log']:
        print("\n[Switch Log]:")
        for entry in after['switch_log']:
            print(f"  {entry['timestamp']} — {entry['event']}: {entry['reason']}")

    print("\n" + "=" * 60)
    print("Kết quả: PASS — fallback triggered and restored via simulation")
    print("=" * 60)

if __name__ == "__main__":
    test_epc_saturation()
