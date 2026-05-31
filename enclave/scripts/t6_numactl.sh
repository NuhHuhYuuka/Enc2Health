#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DUCKDB_BIN="${ROOT_DIR}/duckdb/duckdb" # adjust if using different binary
RESULTS_DIR="${ROOT_DIR}/benchmark"
mkdir -p "$RESULTS_DIR"

RECORDS_DB="/tmp/test.db"

echo "operation,run,elapsed_sec" > "$RESULTS_DIR/t6_numactl_results.csv"

echo "Running baseline (no numactl) 10 iterations..."
for i in $(seq 1 10); do
    START=$(date +%s.%N)
    time gramine-direct "$DUCKDB_BIN" -c "SELECT AVG(length(payload)) FROM patient_records;" >/dev/null 2>&1 || true
    END=$(date +%s.%N)
    ELAPSED=$(awk "BEGIN {print $END - $START}")
    echo "baseline,$i,$ELAPSED" >> "$RESULTS_DIR/t6_numactl_results.csv"
done

echo "Running numactl --cpunodebind=0 --membind=0 10 iterations..."
for i in $(seq 1 10); do
    START=$(date +%s.%N)
    time numactl --cpunodebind=0 --membind=0 gramine-direct "$DUCKDB_BIN" -c "SELECT AVG(length(payload)) FROM patient_records;" >/dev/null 2>&1 || true
    END=$(date +%s.%N)
    ELAPSED=$(awk "BEGIN {print $END - $START}")
    echo "numactl,$i,$ELAPSED" >> "$RESULTS_DIR/t6_numactl_results.csv"
done

echo "Results written to $RESULTS_DIR/t6_numactl_results.csv"
