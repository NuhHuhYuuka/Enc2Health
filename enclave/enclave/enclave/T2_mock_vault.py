#!/usr/bin/env python3
"""
T2 - Mock RA-TLS Vault Server (Simulation Mode)
Simulates the key-distribution flow with mTLS and logical enclave identity checks.
Located in: enclave/T2_mock_vault.py
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import errno
import json
import os
from pathlib import Path
from datetime import datetime
import subprocess
import ssl
import sys
import tempfile
import uuid

# Mock department keypairs storage
DEPARTMENT_KEYS = {}
EXPECTED_MRENCLAVE = "6187572c77489b7f635401f2920c9cf22345b46a29e7e7803a3db99cde2ddc09"
EXPECTED_CLIENT_CN = "Enclave-SimulatedMRENCLAVE"
EXPECTED_VAULT_CN = "Vault-KeyDistributor"
BUNDLE_DIR = Path(__file__).resolve().parent.parent / "To_Lan_from_Long" / "lan_demo"
SSL_DIR = Path(__file__).resolve().parent.parent / "certs"
AUDIT_LOG_PATH = Path(os.environ.get("T2_AUDIT_LOG", Path(__file__).resolve().parent.parent / "hipaa_audit.log"))
CA_KEY = SSL_DIR / "ca.key"
CA_CRT = SSL_DIR / "ca.crt"
SERVER_KEY = SSL_DIR / "server.key"
SERVER_CRT = SSL_DIR / "server.crt"
CLIENT_KEY = SSL_DIR / "enclave.key"
CLIENT_CRT = SSL_DIR / "enclave.crt"
VAULT_KEY = SSL_DIR / "vault.key"
VAULT_CRT = SSL_DIR / "vault.crt"
BOOT_ID = uuid.uuid4().hex
RETRIEVE_COUNT = 0
RETRIEVE_COUNT_BY_DEPT = {}
SESSION_TOKENS = {}
SESSION_TTL_S = 300


def _run_openssl(args):
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def ensure_tls_material():
    """Create local CA/server/client certs for mutual TLS simulation."""
    SSL_DIR.mkdir(parents=True, exist_ok=True)

    if not CA_KEY.exists() or not CA_CRT.exists():
        _run_openssl(["genrsa", "-out", str(CA_KEY), "2048"])
        _run_openssl([
            "req", "-x509", "-new", "-nodes", "-key", str(CA_KEY), "-sha256",
            "-days", "1024", "-out", str(CA_CRT), "-subj", "/CN=T2 Mock Vault CA",
        ])

    if not SERVER_KEY.exists() or not SERVER_CRT.exists():
        server_csr = SSL_DIR / "server.csr"
        _run_openssl(["genrsa", "-out", str(SERVER_KEY), "2048"])
        _run_openssl(["req", "-new", "-key", str(SERVER_KEY), "-out", str(server_csr), "-subj", "/CN=localhost"])
        _run_openssl([
            "x509", "-req", "-days", "360", "-in", str(server_csr),
            "-CA", str(CA_CRT), "-CAkey", str(CA_KEY), "-CAcreateserial",
            "-out", str(SERVER_CRT),
        ])
        server_csr.unlink(missing_ok=True)

    if not CLIENT_KEY.exists() or not CLIENT_CRT.exists():
        client_csr = SSL_DIR / "enclave.csr"
        _run_openssl(["genrsa", "-out", str(CLIENT_KEY), "2048"])
        _run_openssl([
            "req", "-new", "-key", str(CLIENT_KEY), "-out", str(client_csr),
            "-subj", f"/CN={EXPECTED_CLIENT_CN}",
        ])
        _run_openssl([
            "x509", "-req", "-days", "360", "-in", str(client_csr),
            "-CA", str(CA_CRT), "-CAkey", str(CA_KEY), "-CAcreateserial",
            "-out", str(CLIENT_CRT),
        ])
        client_csr.unlink(missing_ok=True)

    if not VAULT_KEY.exists() or not VAULT_CRT.exists():
        vault_csr = SSL_DIR / "vault.csr"
        _run_openssl(["genrsa", "-out", str(VAULT_KEY), "2048"])
        _run_openssl([
            "req", "-new", "-key", str(VAULT_KEY), "-out", str(vault_csr),
            "-subj", f"/CN={EXPECTED_VAULT_CN}",
        ])
        _run_openssl([
            "x509", "-req", "-days", "360", "-in", str(vault_csr),
            "-CA", str(CA_CRT), "-CAkey", str(CA_KEY), "-CAcreateserial",
            "-out", str(VAULT_CRT),
        ])
        vault_csr.unlink(missing_ok=True)


def write_audit_log(message):
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as audit_file:
        audit_file.write(f"{datetime.now().isoformat()} | {message}\n")


def get_peer_common_name(handler):
    peer_cert = handler.connection.getpeercert()
    if not peer_cert:
        return None

    for attributes in peer_cert.get("subject", ()):
        for key, value in attributes:
            if key == "commonName":
                return value
    return None


def require_client_cert(handler):
    client_cn = get_peer_common_name(handler)
    if client_cn not in {EXPECTED_CLIENT_CN, EXPECTED_VAULT_CN}:
        handler.send_response(403)
        handler.send_header("Content-type", "application/json")
        handler.end_headers()
        handler.wfile.write(json.dumps({"error": "Invalid client certificate"}).encode())
        return False
    return True


def require_enclave_claim(handler):
    # Allow either a valid session token (post-attestation) or a direct X-Mrenclave header
    session = handler.headers.get("X-Session-Token", "")
    if session:
        info = SESSION_TOKENS.get(session)
        if not info:
            handler.send_response(401)
            handler.send_header("Content-type", "application/json")
            handler.end_headers()
            handler.wfile.write(json.dumps({"error": "Invalid or expired session token"}).encode())
            return False
        # Session valid and bound to mrenclave
        return True

    mrenclave = handler.headers.get("X-Mrenclave", "")
    if mrenclave != EXPECTED_MRENCLAVE:
        handler.send_response(403)
        handler.send_header("Content-type", "application/json")
        handler.end_headers()
        handler.wfile.write(json.dumps({"error": "Invalid MRENCLAVE"}).encode())
        return False
    return True

class VaultHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler for Vault API"""
    
    def do_GET(self):
        """Handle GET requests"""
        global RETRIEVE_COUNT, RETRIEVE_COUNT_BY_DEPT
        path = self.path

        if not require_client_cert(self):
            return
        
        if path == "/health":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "service": "T2 Mock RA-TLS Vault",
                "boot_id": BOOT_ID,
                "in_memory_only": True,
                "transport": "mutual-tls",
                "attestation": "session-token-supported",
            }).encode())

        elif path == "/debug/runtime":
            if not require_enclave_claim(self):
                return

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "boot_id": BOOT_ID,
                "retrieve_count": RETRIEVE_COUNT,
                "retrieve_count_by_dept": RETRIEVE_COUNT_BY_DEPT,
                "in_memory_only": True,
            }).encode())
            
        elif path == "/v1/secret/departments":
            if not require_enclave_claim(self):
                return
            
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "departments": list(DEPARTMENT_KEYS.keys()),
                "count": len(DEPARTMENT_KEYS),
            }).encode())
            
        elif path.startswith("/v1/secret/departments/"):
            dept = path.split("/")[-1]
            
            if not require_enclave_claim(self):
                return
            
            if dept not in DEPARTMENT_KEYS:
                self.send_response(404)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Department {dept} not found"}).encode())
                return
            
            RETRIEVE_COUNT += 1
            RETRIEVE_COUNT_BY_DEPT[dept] = RETRIEVE_COUNT_BY_DEPT.get(dept, 0) + 1

            # Log retrieval (for HIPAA audit)
            client_cn = get_peer_common_name(self) or "unknown"
            audit_line = f"[AUDIT] Key retrieval for {dept} from enclave {client_cn}"
            print(audit_line, file=sys.stderr)
            write_audit_log(audit_line)
            
            # Only return private_key if client has a valid session token (post-attestation)
            session = self.headers.get("X-Session-Token", "")
            payload = {
                "data": {
                    "public_key": DEPARTMENT_KEYS[dept]["public_key"],
                    "department": dept,
                    "created_at": DEPARTMENT_KEYS[dept]["created_at"],
                    "mrenclave": DEPARTMENT_KEYS[dept]["mrenclave"],
                    "encrypted": False,
                }
            }
            if session and SESSION_TOKENS.get(session):
                payload["data"]["private_key"] = DEPARTMENT_KEYS[dept].get("private_key")

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress default logging"""
        pass

    def do_POST(self):
        """Handle attest endpoint to simulate RA-TLS and issue session tokens"""
        path = self.path
        if path == "/v1/attest":
            length = int(self.headers.get('Content-Length', '0'))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                req = json.loads(body.decode() or '{}')
            except Exception:
                req = {}

            # In a real RA-TLS flow the enclave would present an attestation quote
            # Here we simulate by requiring X-Mrenclave header and returning a short-lived session token
            if not require_client_cert(self):
                return
            mrenclave = self.headers.get("X-Mrenclave", "")
            if mrenclave != EXPECTED_MRENCLAVE:
                self.send_response(403)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Attestation failed: invalid MRENCLAVE"}).encode())
                return

            # Issue session token
            token = uuid.uuid4().hex
            SESSION_TOKENS[token] = {
                "mrenclave": mrenclave,
                "issued_at": datetime.now().isoformat(),
                "expires_at": (datetime.now()).isoformat(),
            }

            # Note: TTL simulation, tokens cleared on restart (BOOT_ID change)
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"session_token": token, "ttl_s": SESSION_TTL_S}).encode())
            return
        else:
            self.send_response(404)
            self.end_headers()

def generate_mock_keys():
    """Generate RSA-2048 mock keypairs for departments"""
    depts = ["Cardio", "ICU", "Surgery", "Ortho"]
    
    for dept in depts:
        try:
            # Generate RSA keypair
            with tempfile.NamedTemporaryFile(mode='w+', suffix='.key', delete=False) as f:
                key_file = f.name
            
            subprocess.run(
                ["openssl", "genrsa", "-out", key_file, "2048"],
                capture_output=True,
                timeout=10,
                check=True
            )
            
            with open(key_file, "r") as f:
                private_key = f.read()
            
            # Get public key
            pub_result = subprocess.run(
                ["openssl", "rsa", "-in", key_file, "-pubout"],
                capture_output=True,
                timeout=10,
                check=True
            )
            
            public_key = pub_result.stdout.decode()
            
            DEPARTMENT_KEYS[dept] = {
                "private_key": private_key,
                "public_key": public_key,
                "created_at": datetime.now().isoformat(),
                "mrenclave": EXPECTED_MRENCLAVE,
            }
            
            os.remove(key_file)
        except Exception as e:
            print(f"Error generating key for {dept}: {e}", file=sys.stderr)


def load_keys_from_bundle():
    """Load department keypairs from the Lan handoff bundle when available."""
    private_dir = BUNDLE_DIR / "private"
    public_dir = BUNDLE_DIR / "public"

    if not private_dir.is_dir() or not public_dir.is_dir():
        return False

    loaded = 0
    for private_path in sorted(private_dir.glob("*_private.pem")):
        dept = private_path.name.removesuffix("_private.pem")
        public_path = public_dir / f"{dept}_public.pem"
        if not public_path.is_file():
            continue

        DEPARTMENT_KEYS[dept] = {
            "private_key": private_path.read_text(),
            "public_key": public_path.read_text(),
            "created_at": datetime.now().isoformat(),
            "mrenclave": EXPECTED_MRENCLAVE,
            "source": "To_Lan_from_Long/lan_demo",
        }
        loaded += 1

    return loaded > 0

def main():
    port = int(os.environ.get("T2_VAULT_PORT", "5555"))
    print("=" * 60)
    print("T2 - Mock RA-TLS Vault Server")
    print("=" * 60)
    print()
    print(f"Expected MRENCLAVE: {EXPECTED_MRENCLAVE}")
    print(f"Allowed client CNs: {EXPECTED_CLIENT_CN}, {EXPECTED_VAULT_CN}")
    print(f"Loading keypairs from bundle: {BUNDLE_DIR}")
    print(f"TLS material dir: {SSL_DIR}")
    print(f"Audit log: {AUDIT_LOG_PATH}")
    print()

    ensure_tls_material()

    if not load_keys_from_bundle():
        print("Bundle not found, generating fallback mock keypairs...", file=sys.stderr)
        generate_mock_keys()
    
    print("✓ Departments configured:")
    for dept in DEPARTMENT_KEYS.keys():
        print(f"  - {dept}")
    print()
    
    print("Endpoints:")
    print("  GET  /health")
    print("  GET  /debug/runtime")
    print("  GET  /v1/secret/departments")
    print("  GET  /v1/secret/departments/<dept>")
    print()
    print(f"Starting server on https://127.0.0.1:{port}")
    print("=" * 60)
    print()
    
    try:
        server = HTTPServer(("127.0.0.1", port), VaultHTTPHandler)
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        tls_context.load_cert_chain(certfile=str(SERVER_CRT), keyfile=str(SERVER_KEY))
        tls_context.load_verify_locations(cafile=str(CA_CRT))
        tls_context.verify_mode = ssl.CERT_REQUIRED
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
        server.serve_forever()
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(
                f"Port {port} is already in use. Stop the existing process or start this "
                f"server on another port with: T2_VAULT_PORT=15555 python3 T2_mock_vault.py",
                file=sys.stderr,
            )
            sys.exit(1)
        raise
    except KeyboardInterrupt:
        print("\n✓ Server stopped")

if __name__ == "__main__":
    main()
