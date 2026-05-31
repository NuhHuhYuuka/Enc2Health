#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFEST_DIR="$ROOT_DIR/T4_gramine"
RESULT_DIR="$ROOT_DIR/T4_results"
CSV_FILE="$ROOT_DIR/benchmark_t4.csv"
PROCESSED_MANIFEST="$MANIFEST_DIR/openssl_processed.manifest"

mkdir -p "$RESULT_DIR"
: > "$CSV_FILE"

gramine-manifest "$MANIFEST_DIR/openssl.manifest" "$PROCESSED_MANIFEST" >/dev/null

echo "metric,mode,ops_per_sec,avg_latency_ms,raw_summary" > "$CSV_FILE"

run_speed() {
  local label="$1"
  shift
  local gramine_log="$RESULT_DIR/${label}_gramine.log"
  local host_log="$RESULT_DIR/${label}_host.log"

  if (cd "$MANIFEST_DIR" && gramine-direct openssl_processed speed -elapsed "$@") >"$gramine_log" 2>&1; then
    echo "gramine-direct|$gramine_log"
    return 0
  fi

  echo "[T4] gramine-direct failed for $label; falling back to host openssl" >&2
  openssl speed -elapsed "$@" >"$host_log" 2>&1
  echo "host-openssl|$host_log"
}

append_rows() {
  local mode="$1"
  local log_file="$2"

  python3 - "$mode" "$log_file" >> "$CSV_FILE" <<'PY'
import re
import sys
from pathlib import Path

mode = sys.argv[1]
log_path = Path(sys.argv[2])
text = log_path.read_text(errors='replace')

for raw_line in text.splitlines():
    line = raw_line.strip()
    if not line:
        continue

    aes_direct = re.search(r"Doing AES-256-GCM for (\d+)s on (\d+) size blocks: ([0-9]+) AES-256-GCM's in", line)
    if aes_direct and aes_direct.group(2) == '16384':
      seconds = float(aes_direct.group(1))
      count = float(aes_direct.group(3))
      throughput_kbps = (count * 16384.0) / seconds / 1000.0
      latency_ms = 16384.0 / throughput_kbps
      print(f"aes-256-gcm,{mode},{throughput_kbps:.2f},{latency_ms:.6f},\"{line}\"")
      continue

    rsa_private_direct = re.search(r"Doing (\d+) bits private rsa's for (\d+)s: ([0-9]+) 4096 bits private RSA's in", line)
    if rsa_private_direct:
      seconds = float(rsa_private_direct.group(2))
      count = float(rsa_private_direct.group(3))
      ops_per_sec = count / seconds
      print(f"rsa4096-sign,{mode},{ops_per_sec:.2f},{1000.0 / ops_per_sec:.6f},\"{line}\"")
      continue

    rsa_public_direct = re.search(r"Doing (\d+) bits public rsa's for (\d+)s: ([0-9]+) 4096 bits public RSA's in", line)
    if rsa_public_direct:
      seconds = float(rsa_public_direct.group(2))
      count = float(rsa_public_direct.group(3))
      ops_per_sec = count / seconds
      print(f"rsa4096-verify,{mode},{ops_per_sec:.2f},{1000.0 / ops_per_sec:.6f},\"{line}\"")
      continue

    ecdsa_sign_direct = re.search(r"Doing (\d+) bits sign ecdsa's for (\d+)s: ([0-9]+) 384 bits ECDSA signs in", line)
    if ecdsa_sign_direct:
      seconds = float(ecdsa_sign_direct.group(2))
      count = float(ecdsa_sign_direct.group(3))
      ops_per_sec = count / seconds
      print(f"ecdsap384-sign,{mode},{ops_per_sec:.2f},{1000.0 / ops_per_sec:.6f},\"{line}\"")
      continue

    ecdsa_verify_direct = re.search(r"Doing (\d+) bits verify ecdsa's for (\d+)s: ([0-9]+) 384 bits ECDSA verify in", line)
    if ecdsa_verify_direct:
      seconds = float(ecdsa_verify_direct.group(2))
      count = float(ecdsa_verify_direct.group(3))
      ops_per_sec = count / seconds
      print(f"ecdsap384-verify,{mode},{ops_per_sec:.2f},{1000.0 / ops_per_sec:.6f},\"{line}\"")
      continue

    if line.startswith("AES-256-GCM"):
      values = re.findall(r"([0-9]+(?:\.[0-9]+)?)k", line)
      if not values:
        continue
      throughput_kbps = float(values[-1])
      latency_ms = 16384.0 / throughput_kbps
      print(f"aes-256-gcm,{mode},{throughput_kbps:.2f},{latency_ms:.6f},\"{line}\"")
    elif line.startswith("rsa ") and "sign/s" in line and "verify/s" in line:
      match = re.search(r"rsa\s+4096 bits\s+([0-9.]+)s\s+([0-9.]+)s\s+([0-9.]+)\s+([0-9.]+)", line)
      if not match:
        continue
      sign_s = float(match.group(3))
      verify_s = float(match.group(4))
      print(f"rsa4096-sign,{mode},{sign_s:.2f},{1000.0 / sign_s:.6f},\"{line}\"")
      print(f"rsa4096-verify,{mode},{verify_s:.2f},{1000.0 / verify_s:.6f},\"{line}\"")
    elif "ecdsa (nistp384)" in line and "sign/s" in line and "verify/s" in line:
      match = re.search(r"384 bits ecdsa \(nistp384\)\s+([0-9.]+)s\s+([0-9.]+)s\s+([0-9.]+)\s+([0-9.]+)", line)
      if not match:
        continue
      sign_s = float(match.group(3))
      verify_s = float(match.group(4))
      print(f"ecdsap384-sign,{mode},{sign_s:.2f},{1000.0 / sign_s:.6f},\"{line}\"")
      print(f"ecdsap384-verify,{mode},{verify_s:.2f},{1000.0 / verify_s:.6f},\"{line}\"")
PY
}

echo "[T4] OpenSSL version"
openssl version -a | tee "$RESULT_DIR/openssl_version.txt"

echo
if grep -q '\baes\b' /proc/cpuinfo; then
  echo "[T4] AES-NI detected in /proc/cpuinfo"
else
  echo "[T4] Warning: AES-NI flag not found in /proc/cpuinfo"
fi

echo
IFS='|' read -r MODE AES_LOG <<< "$(run_speed aes256gcm -evp aes-256-gcm)"
echo "[T4] AES-GCM log: $AES_LOG"
IFS='|' read -r MODE RSA_LOG <<< "$(run_speed rsa4096 rsa4096)"
echo "[T4] RSA-4096 log: $RSA_LOG"
IFS='|' read -r MODE ECC_LOG <<< "$(run_speed ecdsap384 ecdsap384)"
echo "[T4] ECC P-384 log: $ECC_LOG"

append_rows "$MODE" "$AES_LOG"
append_rows "$MODE" "$RSA_LOG"
append_rows "$MODE" "$ECC_LOG"

echo
cat "$CSV_FILE"
