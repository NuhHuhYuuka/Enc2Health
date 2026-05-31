#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
T7_SRC="$SCRIPT_DIR/T7_spectre_mitigation_benchmark.c"
BASE_BIN="$ROOT_DIR/T7_nomic"
MITIG_BIN="$ROOT_DIR/T7_mitig"
ITERATIONS="${T7_ITERATIONS:-20000000}"

echo "[T7] Building baseline spectre benchmark"
gcc -O2 -maes -msse4.1 -o "$BASE_BIN" "$T7_SRC"

echo "[T7] Building mitigated spectre benchmark"
gcc -O2 -maes -msse4.1 -DSPECTRE_MITIGATION=1 -o "$MITIG_BIN" "$T7_SRC"

echo "[T7] Baseline (no mitigation)"
BASE_OUT="$($BASE_BIN baseline "$ITERATIONS")"
echo "$BASE_OUT"

echo "[T7] Mitigated (lfence + workload)"
MITIG_OUT="$($MITIG_BIN mitigated "$ITERATIONS")"
echo "$MITIG_OUT"

BASE_RPS="$(printf '%s\n' "$BASE_OUT" | sed -n 's/.*iter_per_sec=\([0-9.][0-9.]*\).*/\1/p')"
MITIG_RPS="$(printf '%s\n' "$MITIG_OUT" | sed -n 's/.*iter_per_sec=\([0-9.][0-9.]*\).*/\1/p')"

if [[ -n "$BASE_RPS" && -n "$MITIG_RPS" ]]; then
	OVERHEAD_PCT="$(awk -v base="$BASE_RPS" -v mitig="$MITIG_RPS" 'BEGIN { printf "%.2f", ((base - mitig) / base) * 100.0 }')"
	printf '[T7] overhead_pct=%s baseline_iter_per_sec=%s mitigated_iter_per_sec=%s\n' "$OVERHEAD_PCT" "$BASE_RPS" "$MITIG_RPS"
else
	echo "[T7] Unable to parse iteration throughput for overhead calculation" >&2
fi
