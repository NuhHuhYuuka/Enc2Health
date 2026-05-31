#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_FILE="$SCRIPT_DIR/T3_aes_gcm_benchmark.c"
AGGREGATE_FILE="$SCRIPT_DIR/T3_ecall_aggregate.c"
BINARY_FILE="$SCRIPT_DIR/T3_aes_gcm_benchmark"

cc="${CC:-gcc}"

"$cc" -O2 -Wall -Wextra -maes -msse2 "$SOURCE_FILE" "$AGGREGATE_FILE" -o "$BINARY_FILE" \
  $(pkg-config --cflags --libs openssl 2>/dev/null || echo "-lcrypto")

echo "[T3] Running heap mode"
"$BINARY_FILE" --mode heap --records 10000

echo
echo "[T3] Running stack mode"
"$BINARY_FILE" --mode stack --records 10000

echo
echo "[T3] Running register mode"
"$BINARY_FILE" --mode register --records 10000
