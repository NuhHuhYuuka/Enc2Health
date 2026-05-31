#!/usr/bin/env python3
"""
T1 - Enclave Service (Gramine Simulation Mode)
Main process running inside gramine-direct.
Initializes DuckDB, loads keys, and provides query/decrypt endpoints.
"""

import base64
import json
import logging
import os
import sys
import sqlite3
from pathlib import Path
from typing import Iterable, Sequence
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    import duckdb
except Exception:
    duckdb = None

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
    
    # Create in-memory SQL connection. Prefer DuckDB, fall back to sqlite3 when unavailable.
    if duckdb is not None and hasattr(duckdb, "connect"):
        _conn = duckdb.connect(':memory:')
        log.info("✓ DuckDB in-memory connection created")
    else:
        _conn = sqlite3.connect(':memory:', check_same_thread=False)
        log.info("✓ SQLite in-memory connection created (DuckDB unavailable)")
    
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


def encrypt_aes_gcm(plaintext, key_name: str = 'gcm_dek') -> str:
    """Encrypt a plaintext value and return a base64 nonce+ciphertext payload."""
    if key_name not in _keys:
        raise ValueError(f"Key '{key_name}' not found")

    key = _keys[key_name]
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    payload = str(plaintext).encode('utf-8')
    ciphertext = aesgcm.encrypt(nonce, payload, None)
    return base64.b64encode(nonce + ciphertext).decode('ascii')


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
    plaintext = bytearray(aesgcm.decrypt(nonce, ct, None))
    
    try:
        decoded = bytes(plaintext).decode('utf-8')
        try:
            value = float(json.loads(decoded))
        except Exception:
            value = float(decoded)
    finally:
        plaintext[:] = b'\x00' * len(plaintext)
    
    return value


def register_patient_rows(rows: Iterable[dict]) -> None:
    """Load patient rows into DuckDB for query execution."""
    if _conn is None:
        raise RuntimeError("DuckDB not initialized")

    _conn.execute("DROP TABLE IF EXISTS patient_records")
    _conn.execute(
        """
        CREATE TABLE patient_records (
            record_id INTEGER,
            patient_id VARCHAR,
            ma_benh VARCHAR,
            tuoi INTEGER,
            khoa VARCHAR,
            vien_phi_enc VARCHAR,
            payload VARCHAR
        )
        """
    )

    inserts = []
    for row in rows:
        vien_phi_enc = row.get("vien_phi_enc")
        if vien_phi_enc is None and "vien_phi" in row:
            vien_phi_enc = encrypt_aes_gcm(row["vien_phi"])

        inserts.append(
            (
                int(row.get("record_id", 0)),
                str(row.get("patient_id", "")),
                str(row.get("ma_benh", "")),
                int(row.get("tuoi", 0)),
                str(row.get("khoa", "")),
                str(vien_phi_enc or ""),
                str(row.get("payload", "")),
            )
        )

    if inserts:
        _conn.executemany(
            "INSERT INTO patient_records VALUES (?, ?, ?, ?, ?, ?, ?)",
            inserts,
        )

    log.info(f"✓ DuckDB patient_records loaded ({len(inserts)} rows)")


def query_patient_aggregate(query_type: str, filters: dict) -> dict:
    """Execute a filtered aggregate against the DuckDB-backed enclave table."""
    if _conn is None:
        raise RuntimeError("DuckDB not initialized")

    clauses = []
    params = []

    if "ma_benh" in filters:
        clauses.append("ma_benh = ?")
        params.append(str(filters["ma_benh"]))

    if "tuoi_min_enc" in filters:
        clauses.append("tuoi >= ?")
        params.append(int(filters["tuoi_min_enc"]))

    if "khoa" in filters:
        clauses.append("khoa = ?")
        params.append(str(filters["khoa"]))

    where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    if query_type == "count":
        row = _conn.execute(
            f"SELECT COUNT(*) FROM patient_records{where_clause}",
            params,
        ).fetchone()
        n_records = int(row[0] if row else 0)
        return {
            "result": float(n_records),
            "n_records": n_records,
            "query_type": query_type,
        }

    rows = _conn.execute(
        f"SELECT vien_phi_enc FROM patient_records{where_clause}",
        params,
    ).fetchall()

    billing_values = [decrypt_aes_gcm(row[0]) for row in rows if row and row[0]]
    n_records = len(billing_values)

    if not billing_values:
        result = 0.0
    elif query_type == "avg_vien_phi":
        result = sum(billing_values) / n_records
    elif query_type == "sum_vien_phi":
        result = sum(billing_values)
    else:
        raise ValueError(f"Unknown query type: {query_type}")

    return {
        "result": result,
        "n_records": n_records,
        "query_type": query_type,
    }


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
