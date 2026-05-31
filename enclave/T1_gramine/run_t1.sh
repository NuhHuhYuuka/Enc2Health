#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFEST_SRC="$SCRIPT_DIR/duckdb_real.manifest"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

gramine-manifest -D libdir=/usr/lib/x86_64-linux-gnu "$MANIFEST_SRC" > "$TEMP_DIR/duckdb_real.manifest"
cp "$SCRIPT_DIR/duckdb_real" "$TEMP_DIR/duckdb_real"

cd "$TEMP_DIR"
LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu/gramine/direct:${LD_LIBRARY_PATH:-} \
  gramine-direct duckdb_real "$@"