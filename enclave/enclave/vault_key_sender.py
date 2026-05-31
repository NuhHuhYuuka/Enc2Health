#!/usr/bin/env python3
"""
T2 - Vault Key Sender (Simulation Mode)
Fetches the private key from the mock vault and delivers it to the enclave receiver.
Located in: enclave/vault_key_sender.py
"""

import base64
import json
import socket
import ssl
import urllib.request
from pathlib import Path

from T2_mock_vault import EXPECTED_MRENCLAVE, EXPECTED_VAULT_CN, SSL_DIR, ensure_tls_material


VAULT_URL = "https://127.0.0.1:5555"


def build_tls_context():
    ensure_tls_material()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(SSL_DIR / "vault.crt"), str(SSL_DIR / "vault.key"))
    context.load_verify_locations(cafile=str(SSL_DIR / "ca.crt"))
    context.check_hostname = False
    return context


def fetch_private_key(dept):
    request = urllib.request.Request(
        f"{VAULT_URL}/v1/secret/departments/{dept}",
        headers={"X-Mrenclave": EXPECTED_MRENCLAVE},
        method="GET",
    )
    with urllib.request.urlopen(request, context=build_tls_context(), timeout=5) as response:
        payload = json.loads(response.read().decode())
        return payload["data"]["private_key"]


def send_to_enclave(dept, enclave_host="127.0.0.1", enclave_port=8443):
    ensure_tls_material()
    private_key = fetch_private_key(dept)
    payload = f"{dept}:{base64.b64encode(private_key.encode()).decode()}".encode()

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(SSL_DIR / "vault.crt"), str(SSL_DIR / "vault.key"))
    context.load_verify_locations(cafile=str(SSL_DIR / "ca.crt"))
    context.check_hostname = False

    with socket.create_connection((enclave_host, enclave_port), timeout=5) as raw_socket:
        with context.wrap_socket(raw_socket, server_hostname="Enclave-SimulatedMRENCLAVE") as tls_socket:
            tls_socket.sendall(payload)

    print(f"[Vault] Key delivered to Enclave for Khoa {dept}")


if __name__ == "__main__":
    send_to_enclave("Cap_cuu")
