#!/usr/bin/env python3
"""
Run T3 in register mode, capture the average value, and run a DuckDB aggregate
This script doesn't rebuild or run DuckDB inside an enclave; it uses the local duckdb Python package
and the T3 binary outputs to create a small table to run AVG/SUM.

Usage: python3 benchmark/t3_duckdb_integration.py
"""
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
T3_RUN = ROOT / "T3" / "T3_aes_gcm_benchmark"

# If binary not present, try building via T3_run.sh
if not T3_RUN.exists():
    sh = ROOT / "T3" / "T3_run.sh"
    if sh.exists():
        subprocess.run(["bash", str(sh)], check=True)
    else:
        print("T3 binary not found and T3_run.sh missing", file=sys.stderr)
        sys.exit(1)

# Run register mode and capture output
proc = subprocess.run([str(T3_RUN), "--mode", "register", "--records", "10000"], capture_output=True, text=True)
print(proc.stdout)
if proc.returncode != 0:
    print(proc.stderr, file=sys.stderr)
    sys.exit(proc.returncode)

# Parse avg from output line "[T3] mode=register records=10000 avg=..."
avg = None
for line in proc.stdout.splitlines():
    if "mode=register" in line and "avg=" in line:
        parts = line.split()
        for p in parts:
            if p.startswith("avg="):
                try:
                    avg = float(p.split("=")[1])
                except Exception:
                    pass

if avg is None:
    print("Could not parse avg from T3 output", file=sys.stderr)
    sys.exit(1)

# Create a small CSV and run DuckDB query
import duckdb

con = duckdb.connect(database=':memory:')
con.execute("CREATE TABLE t3_results(r DOUBLE)")
# Insert single value 10000 times to mimic records
con.execute("INSERT INTO t3_results SELECT ? FROM range(10000)", [avg])
res = con.execute("SELECT AVG(r) as avg_r, SUM(r) as sum_r FROM t3_results").fetchall()
print("DuckDB aggregates on decrypted register-mode values:")
print(res)

