#!/usr/bin/env bash
set -euo pipefail

# Script: run_T5_swap_allocator.sh
# Purpose: Build DuckDB with DISABLE_JEMALLOC=ON and summarise T5 malloc comparison results
# Usage: bash scripts/run_T5_swap_allocator.sh

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

echo "1) Building DuckDB with jemalloc disabled (DISABLE_JEMALLOC=ON)"
if [ -x scripts/build_duckdb_noglibc.sh ]; then
  bash scripts/build_duckdb_noglibc.sh
else
  echo "build_duckdb_noglibc.sh not found or not executable"
fi

echo
echo "2) Summarising existing T5 results (T5_results/malloc_comparison.csv)"
if [ -f T5_results/malloc_comparison.csv ]; then
  cat T5_results/malloc_comparison.csv
else
  echo "T5_results/malloc_comparison.csv not found"
fi

echo
echo "Note: To re-run full T5 microbenchmark after rebuilding DuckDB, run the specific benchmark runner (not included)." 
