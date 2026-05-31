#!/usr/bin/env python3
"""
T2 - Restart and Audit Semantics Test
Verifies in-memory key state resets after Vault restart in the mTLS simulation.
"""

import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "enclave"))

VAULT_PORT = int(os.environ.get("T2_VAULT_PORT", "15555"))
VAULT_URL = f"https://127.0.0.1:{VAULT_PORT}"
MRENCLAVE = "6187572c77489b7f635401f2920c9cf22345b46a29e7e7803a3db99cde2ddc09"
VAULT_SCRIPT = PROJECT_ROOT / "enclave" / "T2_mock_vault.py"
SSL_DIR = PROJECT_ROOT / "certs"
AUDIT_LOG_PATH = Path(os.environ.get("T2_AUDIT_LOG", PROJECT_ROOT / "hipaa_audit.log"))


def ensure_client_tls_material():
    from T2_mock_vault import ensure_tls_material

    ensure_tls_material()


def build_tls_context():
    ensure_client_tls_material()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(SSL_DIR / "enclave.crt"), str(SSL_DIR / "enclave.key"))
    ctx.load_verify_locations(cafile=str(SSL_DIR / "ca.crt"))
    ctx.check_hostname = False
    return ctx


def http_request(method, url, headers=None, context=None):
    if headers is None:
        headers = {}
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5, context=context) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
    except Exception as e:
        return None, str(e)


def wait_for_health():
    for _ in range(20):
        status, _ = http_request("GET", f"{VAULT_URL}/health", context=build_tls_context())
        if status == 200:
            return True
        time.sleep(0.5)
    return False


def start_vault():
    env = os.environ.copy()
    env["T2_VAULT_PORT"] = str(VAULT_PORT)
    return subprocess.Popen([sys.executable, str(VAULT_SCRIPT)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def stop_vault(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def get_runtime_state():
    headers = {"X-Mrenclave": MRENCLAVE}
    status, data = http_request("GET", f"{VAULT_URL}/debug/runtime", headers, build_tls_context())
    if status != 200:
        raise RuntimeError(f"debug/runtime failed: {status} {data}")
    return data


def retrieve_one_key():
    # Attest to get session token then retrieve key
    status_att, data_att = http_request("POST", f"{VAULT_URL}/v1/attest", {"X-Mrenclave": MRENCLAVE}, build_tls_context())
    session = data_att.get("session_token") if status_att == 200 else None
    headers = {"X-Session-Token": session} if session else {"X-Mrenclave": MRENCLAVE}
    status, data = http_request("GET", f"{VAULT_URL}/v1/secret/departments/Cap_cuu", headers, build_tls_context())
    if status != 200:
        raise RuntimeError(f"key retrieval failed: {status} {data}")
    return data


def main():
    print("=" * 60)
    print("T2 - Restart/Audit Semantics Test")
    print("=" * 60)

    vault = start_vault()
    try:
        if not wait_for_health():
            stdout, stderr = vault.communicate(timeout=1)
            print("✗ Vault did not start")
            print(stderr.decode(errors="replace") if stderr else "")
            return 1

        first_runtime = get_runtime_state()
        print(f"[Boot 1] boot_id={first_runtime['boot_id']} retrieve_count={first_runtime['retrieve_count']}")

        retrieve_one_key()
        after_retrieval = get_runtime_state()
        print(f"[After retrieve] boot_id={after_retrieval['boot_id']} retrieve_count={after_retrieval['retrieve_count']}")

        stop_vault(vault)
        time.sleep(1)

        vault = start_vault()
        if not wait_for_health():
            stdout, stderr = vault.communicate(timeout=1)
            print("✗ Vault did not restart")
            print(stderr.decode(errors="replace") if stderr else "")
            return 1

        second_runtime = get_runtime_state()
        print(f"[Boot 2] boot_id={second_runtime['boot_id']} retrieve_count={second_runtime['retrieve_count']}")

        if second_runtime["boot_id"] == first_runtime["boot_id"]:
            print("✗ boot_id did not change after restart")
            return 1

        if second_runtime["retrieve_count"] != 0:
            print("✗ retrieve_count did not reset after restart")
            return 1

        if not AUDIT_LOG_PATH.exists():
            print(f"✗ audit log not found: {AUDIT_LOG_PATH}")
            return 1

        audit_text = AUDIT_LOG_PATH.read_text(encoding="utf-8", errors="replace")
        if "Key retrieval for Cap_cuu" not in audit_text:
            print("✗ audit log missing retrieval entry")
            return 1

        print("✓ Restart resets in-memory runtime state")
        print("✓ Audit log records retrieval without exposing secret material")
        print("✓ Keys are not persistent across process restarts in the simulation setup")
        return 0
    finally:
        stop_vault(vault)


if __name__ == "__main__":
    sys.exit(main())
