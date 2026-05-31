#!/usr/bin/env bash
set -euo pipefail

# Build DuckDB with glibc malloc (disable jemalloc)
# Usage: ./scripts/build_duckdb_noglibc.sh [clone-only]
# If clone-only is passed, the script will only clone and configure, not run the build.

REPO_DIR="$PWD/duckdb"
BUILD_DIR="$PWD/duckdb_build_noglibc"

if [ ! -d "$REPO_DIR" ]; then
    echo "Cloning DuckDB..."
    git clone https://github.com/duckdb/duckdb.git "$REPO_DIR"
else
    echo "duckdb repo already exists at $REPO_DIR"
fi

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

echo "Configuring build with DISABLE_JEMALLOC=ON..."
cmake -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_UNITTESTS=OFF \
      -DENABLE_SANITIZER=OFF \
      -DDUCKDB_USE_OPENSSL=ON \
      -DDISABLE_BUILTIN_MBEDTLS=ON \
      -DDISABLE_JEMALLOC=ON \
      -S "$REPO_DIR" -B "$BUILD_DIR"

if [ "${1:-}" = "clone-only" ]; then
    echo "Configured. To build, run: cmake --build $BUILD_DIR -j$(nproc)"
    exit 0
fi

echo "Building DuckDB (this may take several minutes)..."
cmake --build "$BUILD_DIR" -j$(nproc)

echo "Build finished. Binary located in $BUILD_DIR/bin or $BUILD_DIR/duckdb"
