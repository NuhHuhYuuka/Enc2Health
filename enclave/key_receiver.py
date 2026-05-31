#!/usr/bin/env python3
"""
T2 - Enclave Key Receiver (Simulation Mode)
Listens for the provisioned private key and stores it only in process memory.
Located in: enclave/key_receiver.py
"""

import base64
import json
import socket
import ssl
import threading
from datetime import datetime
from pathlib import Path

from T2_mock_vault import AUDIT_LOG_PATH, EXPECTED_VAULT_CN, SSL_DIR, ensure_tls_material


PRIVATE_KEYS = {}
PRIVATE_KEY_LOCK = threading.Lock()


def write_audit_log(message):
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as audit_file:
        audit_file.write(f"{datetime.now().isoformat()} | {message}\n")


def get_peer_common_name(connection):
    peer_cert = connection.getpeercert()
    if not peer_cert:
        return None

    for attributes in peer_cert.get("subject", ()):
        for key, value in attributes:
            if key == "commonName":
                return value
    return None


def main(host="127.0.0.1", port=8443):
    ensure_tls_material()

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(SSL_DIR / "enclave.crt"), keyfile=str(SSL_DIR / "enclave.key"))
    context.load_verify_locations(cafile=str(SSL_DIR / "ca.crt"))
    context.verify_mode = ssl.CERT_REQUIRED

    print("=" * 60)
    print("T2 - Enclave Key Receiver")
    print("=" * 60)
    print(f"Listening on {host}:{port}")
    print(f"Expected sender CN: {EXPECTED_VAULT_CN}")
    print(f"Audit log: {AUDIT_LOG_PATH}")
    print()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((host, port))
        server_socket.listen(1)

        with context.wrap_socket(server_socket, server_side=True) as tls_server:
            connection, address = tls_server.accept()
            with connection:
                sender_cn = get_peer_common_name(connection)
                if sender_cn != EXPECTED_VAULT_CN:
                    connection.sendall(b"ERR:INVALID_SENDER")
                    return 1

                payload = b""
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    payload += chunk

                dept, key_b64 = payload.decode().split(":", 1)
                private_key = base64.b64decode(key_b64)

                with PRIVATE_KEY_LOCK:
                    PRIVATE_KEYS[dept] = private_key

                write_audit_log(f"[AUDIT] PRIVATE_KEY_LOADED | khoa={dept} | sender={sender_cn}")
                print(f"[Enclave] Key loaded for {dept} from {sender_cn} at {address[0]}:{address[1]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
