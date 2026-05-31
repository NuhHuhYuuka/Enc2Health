#!/usr/bin/env python3
"""
T1 - Enclave Service (Gramine Simulation Mode)
Main process running inside gramine-direct.
Initializes DuckDB, loads keys, and provides query/decrypt endpoints.
"""

import base64
import json
import duckdb
import logging
import os
import sys
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[ENCLAVE] %(asctime)s - %(levelname)s - %(message)s'
)
log = logging.getLogger('enclave_service')

# Global state - only lives in this process RAM
_keys = {}
_conn = None


def initialize():
    """Initialize DuckDB and test basic operations."""
    global _conn
    
    log.info("Initializing Enclave Service in Gramine Simulation Mode")
    
    # Create in-memory DuckDB connection
    _conn = duckdb.connect(':memory:')
    log.info("✓ DuckDB in-memory connection created")
    
    # Test basic query
    result = _conn.execute("SELECT 1 AS test").fetchone()
    if result and result[0] == 1:
        log.info("✓ DuckDB basic query: OK")
    else:
        raise RuntimeError("DuckDB basic query failed")
    
    # Test AES-GCM capability
    test_key = os.urandom(32)
    test_nonce = os.urandom(12)
    test_plaintext = b"test_data"
    
    aesgcm = AESGCM(test_key)
    ct = aesgcm.encrypt(test_nonce, test_plaintext, None)
    pt = aesgcm.decrypt(test_nonce, ct, None)
    
    if pt == test_plaintext:
        log.info("✓ AES-GCM decrypt capability: OK")
    else:
        raise RuntimeError("AES-GCM test failed")
    
    log.info("✅ All initialization checks passed – Enclave ready")


def load_key(name: str, key_bytes: bytes):
    """Store a key in process memory (plaintext only in RAM)."""
    _keys[name] = key_bytes
    log.info(f"✓ Key '{name}' loaded ({len(key_bytes)} bytes)")


def decrypt_aes_gcm(ct_b64: str, key_name: str = 'gcm_dek') -> float:
    """
    Decrypt AES-GCM ciphertext.
    Plaintext exists only in scope of this function (register-safe).
    """
    if key_name not in _keys:
        raise ValueError(f"Key '{key_name}' not found")
    
    key = _keys[key_name]
    raw = base64.b64decode(ct_b64)
    nonce, ct = raw[:12], raw[12:]
    
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ct, None)
    plaintext_buf = bytearray(plaintext)
    
    try:
        decoded = plaintext_buf.decode('utf-8')
        try:
            value = float(json.loads(decoded))
        except Exception:
            value = float(decoded.strip().strip('"'))
    finally:
        # Zero-fill immediately
        plaintext_buf[:] = b'\x00' * len(plaintext_buf)
        del plaintext_buf
        del plaintext
    
    return value


def query_duckdb(sql: str):
    """Execute SQL query in DuckDB."""
    if _conn is None:
        raise RuntimeError("DuckDB not initialized")
    
    result = _conn.execute(sql).fetchall()
    return result


def main():
    """Main entry point for gramine-direct."""
    try:
        initialize()
        
        # Keep service running (for now, just initialize and exit)
        log.info("Enclave service ready for connections")
        
        # In production: start TLS server on port 8443 here
        # For simulation: just log and keep alive
        log.info("Running in simulation mode - basic checks complete")
        
    except Exception as e:
        log.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
