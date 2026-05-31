#!/usr/bin/env python3
"""
T2 - RA-TLS Client Test (Simulation Mode)
Uses mutual TLS to simulate enclave-to-vault secret retrieval.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "enclave"))

SSL_DIR = PROJECT_ROOT / "certs"
VAULT_PORT = int(os.environ.get("T2_VAULT_PORT", "5555"))
VAULT_URL = os.environ.get("T2_VAULT_URL", f"https://127.0.0.1:{VAULT_PORT}")
MRENCLAVE = "6187572c77489b7f635401f2920c9cf22345b46a29e7e7803a3db99cde2ddc09"
EXPECTED_CLIENT_CN = "Enclave-SimulatedMRENCLAVE"


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
    """Generic HTTP request helper"""
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

def test_vault_health():
    """Test Vault server health"""
    print("[Test 1] Vault Health Check")
    status, data = http_request("GET", f"{VAULT_URL}/health", context=build_tls_context())
    if status == 200:
        print(f"  ✓ Vault responding: {data}")
        return True
    else:
        print(f"  ✗ Vault error: {status}")
        return False

def test_list_departments():
    """List available departments"""
    print("[Test 2] List Departments")
    # Perform attestation to obtain session token
    status_att, data_att = http_request("POST", f"{VAULT_URL}/v1/attest", {"X-Mrenclave": MRENCLAVE}, build_tls_context())
    session = None
    if status_att == 200:
        session = data_att.get("session_token")

    headers = {"X-Session-Token": session} if session else {"X-Mrenclave": MRENCLAVE}
    status, data = http_request("GET", f"{VAULT_URL}/v1/secret/departments", headers, build_tls_context())
    if status == 200:
        print(f"  ✓ Departments: {data['departments']}")
        return data["departments"]
    else:
        print(f"  ✗ Error: {status}")
        return None

def test_retrieve_key(dept, index=None):
    """Retrieve private key for a department (with valid MRENCLAVE)"""
    label = f"Test 3.{index}" if index is not None else "Test 3"
    print(f"[{label}] Retrieve Private Key - {dept}")
    
    # Attest first to receive a session token
    status_att, data_att = http_request("POST", f"{VAULT_URL}/v1/attest", {"X-Mrenclave": MRENCLAVE}, build_tls_context())
    session = data_att.get("session_token") if status_att == 200 else None

    headers = {
        "X-Session-Token": session or "",
        "X-Enclave-Public-Key": "mock_enclave_pub_key_placeholder",
        "X-Sgx-Quote": "mock_sgx_quote_placeholder",
    }
    
    status, data = http_request("GET", f"{VAULT_URL}/v1/secret/departments/{dept}", headers, build_tls_context())
    
    if status == 200:
        privkey = data["data"]["private_key"]
        privkey_len = len(privkey.split("\n"))
        print(f"  ✓ Retrieved {privkey_len} lines of private key")
        print(f"    Department: {data['data']['department']}")
        print(f"    MRENCLAVE: {data['data']['mrenclave'][:16]}...")
        print(f"    Created: {data['data']['created_at']}")
        return True
    else:
        print(f"  ✗ Error {status}: {data}")
        return False

def test_invalid_mrenclave():
    """Test unauthorized access with wrong MRENCLAVE"""
    print("[Test 4] Unauthorized Access (Invalid MRENCLAVE)")
    
    bad_mrenclave = "BADBADBADBADBADBADBADBADBADBADBADBADBADBADBADBADBADBADBADBADBADB"
    # Attempt attest with bad mrenclave
    status_att, data_att = http_request("POST", f"{VAULT_URL}/v1/attest", {"X-Mrenclave": bad_mrenclave}, build_tls_context())
    if status_att == 200:
        print("  ✗ Attestation unexpectedly succeeded with bad mrenclave")
        return False

    # Also verify direct access denied
    headers = {"X-Mrenclave": bad_mrenclave}
    status, data = http_request("GET", f"{VAULT_URL}/v1/secret/departments/Cardio", headers, build_tls_context())
    
    if status == 403:
        print(f"  ✓ Correctly rejected: {data['error']}")
        return True
    else:
        print(f"  ✗ Should have rejected (got {status})")
        return False

def test_nonexistent_department():
    """Test requesting non-existent department"""
    print("[Test 5] Non-existent Department")
    
    # Attest to obtain session token then request non-existent department
    status_att, data_att = http_request("POST", f"{VAULT_URL}/v1/attest", {"X-Mrenclave": MRENCLAVE}, build_tls_context())
    session = data_att.get("session_token") if status_att == 200 else None
    headers = {"X-Session-Token": session} if session else {"X-Mrenclave": MRENCLAVE}
    status, data = http_request("GET", f"{VAULT_URL}/v1/secret/departments/NonExistent", headers, build_tls_context())
    
    if status == 404:
        print(f"  ✓ Correctly returned 404: {data['error']}")
        return True
    else:
        print(f"  ✗ Should have returned 404 (got {status})")
        return False

def main():
    print("=" * 60)
    print("T2 - RA-TLS Client Test Suite")
    print("=" * 60)
    print()
    print(f"Target Vault: {VAULT_URL}")
    print(f"Enclave CN: {EXPECTED_CLIENT_CN}")
    print(f"Enclave MRENCLAVE: {MRENCLAVE[:16]}...")
    print()
    
    # Wait for server to be ready
    print("Waiting for Vault server...")
    for attempt in range(10):
        try:
            status, _ = http_request("GET", f"{VAULT_URL}/health", context=build_tls_context())
            if status == 200:
                break
        except:
            pass

        if attempt < 9:
            time.sleep(1)
        else:
            print("✗ Vault server not responding")
            sys.exit(1)

    print()
    results = []

    # Run tests
    results.append(("Health Check", test_vault_health()))
    time.sleep(0.5)

    departments = test_list_departments()
    results.append(("List Departments", departments is not None))
    time.sleep(0.5)

    # Test key retrieval for each department discovered from the vault
    for index, dept in enumerate(departments or [], start=1):
        results.append((f"Retrieve Key - {dept}", test_retrieve_key(dept, index=index)))
        time.sleep(0.5)

    results.append(("Invalid MRENCLAVE", test_invalid_mrenclave()))
    time.sleep(0.5)

    results.append(("Non-existent Dept", test_nonexistent_department()))
    
    # Summary
    print()
    print("=" * 60)
    print("Test Summary")
    print("=" * 60)
    passed = sum(1 for _, r in results if r)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    print()
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")
    
    print()
    
    if passed == total:
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
