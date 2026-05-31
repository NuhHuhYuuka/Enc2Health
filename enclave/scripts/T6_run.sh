#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DUCKDB_BIN="${DUCKDB_BIN:-$ROOT_DIR/build/duckdb}"
DB_PATH="${T6_DB_PATH:-/tmp/test.db}"
QUERY="${T6_QUERY:-SELECT AVG(length(payload)) FROM patient_records;}"
BOOTSTRAP_ROWS="${T6_BOOTSTRAP_ROWS:-100000}"

if ! command -v numactl >/dev/null 2>&1; then
	echo "[T6] numactl is not installed" >&2
	exit 1
fi

if [[ ! -x "$DUCKDB_BIN" ]]; then
	echo "[T6] duckdb binary not found or not executable: $DUCKDB_BIN" >&2
	exit 1
fi

if [[ ! -f "$DB_PATH" ]]; then
	echo "[T6] database file not found: $DB_PATH"
	echo "[T6] creating synthetic benchmark database with $BOOTSTRAP_ROWS patient_records rows"
	mkdir -p "$(dirname "$DB_PATH")"
	"$DUCKDB_BIN" "$DB_PATH" -c "
		CREATE TABLE patient_records AS
		SELECT
			i AS patient_id,
			repeat('enc2health_payload_', 64) || CAST(i AS VARCHAR) AS payload
		FROM range($BOOTSTRAP_ROWS) AS records(i);
	"
fi

run_measure() {
	local label="$1"
	shift
	local output
	output="$(/usr/bin/time -v "$@" "$DB_PATH" -c "$QUERY" 2>&1)"
	echo "[T6] $label"
	printf '%s\n' "$output" | grep -E 'Elapsed|Maximum resident set size'
}

echo "[T6] NUMA topology"
numactl --hardware

run_measure "Baseline (no NUMA binding)" "$DUCKDB_BIN"
run_measure "numactl --cpunodebind=0 --membind=0" numactl --cpunodebind=0 --membind=0 "$DUCKDB_BIN"
