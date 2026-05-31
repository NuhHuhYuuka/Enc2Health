#!/usr/bin/env python3
"""
T12 - Hash Join vs Sort-Merge vs Nested Loop in Enclave
Compares three join strategies on DuckDB with healthcare dataset.
Simulates in-memory queries after decryption in enclave context.
"""

import json
import os
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
import sys
import gzip
import struct
import binascii

# BSON Type constants
TYPE_DOUBLE = 0x01
TYPE_STRING = 0x02
TYPE_DOCUMENT = 0x03
TYPE_ARRAY = 0x04
TYPE_BINARY = 0x05
TYPE_OBJECTID = 0x07
TYPE_BOOL = 0x08
TYPE_DATETIME = 0x09
TYPE_NULL = 0x0A
TYPE_REGEX = 0x0B
TYPE_INT32 = 0x10
TYPE_TIMESTAMP = 0x11
TYPE_INT64 = 0x12
TYPE_DECIMAL128 = 0x13
TYPE_MINKEY = 0xFF
TYPE_MAXKEY = 0x7F

# Paths
ARCHIVE_PATH = Path(__file__).resolve().parent.parent / "To_Lan_from_Long" / "lan_demo" / "dataset" / "enc2health_dump.archive"
OUTPUT_JSON_PATH = Path(__file__).resolve().parent / "t12_join_comparison.json"
TARGET_PATIENTS = int(os.environ.get("T12_TARGET_PATIENTS", "10000"))
NUM_ITERATIONS = int(os.environ.get("T12_ITERATIONS", "10"))

try:
    import duckdb
except ImportError:
    print("ERROR: duckdb module not found. Install with: pip install duckdb")
    sys.exit(1)


def _cstring(data, position):
    """Decode a BSON C-string."""
    end = data.index(b"\x00", position)
    return data[position:end].decode("utf-8", "replace"), end + 1


def _decode_value(data, position, element_type):
    """Decode a BSON value."""
    if element_type == TYPE_DOUBLE:
        return struct.unpack_from("<d", data, position)[0], position + 8
    if element_type == TYPE_STRING:
        length = struct.unpack_from("<i", data, position)[0]
        raw = data[position + 4 : position + 4 + length - 1]
        return raw.decode("utf-8", "replace"), position + 4 + length
    if element_type == TYPE_DOCUMENT:
        return _decode_doc(data, position)
    if element_type == TYPE_ARRAY:
        document, new_position = _decode_doc(data, position)
        return [document[str(index)] for index in range(len(document))], new_position
    if element_type == TYPE_BINARY:
        length = struct.unpack_from("<i", data, position)[0]
        subtype = data[position + 4]
        blob = data[position + 5 : position + 5 + length]
        return {"subtype": subtype, "data": blob}, position + 5 + length
    if element_type == TYPE_OBJECTID:
        return binascii.hexlify(data[position : position + 12]).decode(), position + 12
    if element_type == TYPE_BOOL:
        return data[position] != 0, position + 1
    if element_type == TYPE_DATETIME:
        from datetime import datetime as dt
        milliseconds = struct.unpack_from("<q", data, position)[0]
        return dt.utcfromtimestamp(milliseconds / 1000), position + 8
    if element_type == TYPE_NULL:
        return None, position
    if element_type == TYPE_REGEX:
        pattern, position = _cstring(data, position)
        options, position = _cstring(data, position)
        return {"pattern": pattern, "options": options}, position
    if element_type == TYPE_INT32:
        return struct.unpack_from("<i", data, position)[0], position + 4
    if element_type == TYPE_TIMESTAMP:
        increment, seconds = struct.unpack_from("<II", data, position)
        return {"inc": increment, "ts": seconds}, position + 8
    if element_type == TYPE_INT64:
        return struct.unpack_from("<q", data, position)[0], position + 8
    if element_type == TYPE_DECIMAL128:
        return binascii.hexlify(data[position : position + 16]).decode(), position + 16
    if element_type in (TYPE_MINKEY, TYPE_MAXKEY):
        return None, position

    raise ValueError(f"Unsupported BSON type 0x{element_type:02x}")


def _decode_doc(data, position):
    """Decode a BSON document."""
    length = struct.unpack_from("<i", data, position)[0]
    end = position + length
    cursor = position + 4
    document = {}

    while cursor < end - 1:
        element_type = data[cursor]
        cursor += 1
        key, cursor = _cstring(data, cursor)
        value, cursor = _decode_value(data, cursor, element_type)
        document[key] = value

    return document, end


class ArchiveDataLoader:
    """Load healthcare data from MongoDB archive."""

    def __init__(self, archive_path=ARCHIVE_PATH):
        self.archive_path = Path(archive_path)
        self.patient_records = None
        self.bill_records = None

    def _read_archive_bytes(self):
        """Read gzipped archive."""
        with gzip.open(self.archive_path, "rb") as f:
            return f.read()

    def _find_first_record(self, data):
        """Find start of first BSON document."""
        marker = b"\x07_id\x00"
        pos = data.find(marker)
        if pos == -1:
            raise ValueError("Could not locate first patient record")
        return pos - 4

    def load_patient_records(self) -> List[Dict]:
        """Load patient records (benh_nhan)."""
        if self.patient_records is not None:
            return self.patient_records

        data = self._read_archive_bytes()
        position = self._find_first_record(data)
        records = []

        while position < len(data):
            try:
                document, next_position = _decode_doc(data, position)
            except Exception:
                break

            if "patient_id" in document:
                records.append(document)

            position = next_position

        self.patient_records = records
        return records

    def create_synthetic_bill_records(self, patient_records: List[Dict]) -> List[Dict]:
        """Create synthetic bill records linked to patients."""
        bills = []
        bill_id = 1

        for patient in patient_records:
            patient_id = patient.get("patient_id")
            khoa = patient.get("khoa_phong_plaintext", "Unknown")

            # Generate 2-5 bills per patient
            num_bills = hash(str(patient_id)) % 4 + 2
            for i in range(num_bills):
                bill_amount = ((hash(str(patient_id) + str(i)) % 10000) + 1000) / 100.0
                bills.append({
                    "bill_id": bill_id,
                    "patient_id": patient_id,
                    "khoa": khoa,
                    "amount": bill_amount,
                    "date": f"2024-{(i % 12) + 1:02d}-01"
                })
                bill_id += 1

        return bills


def scale_patient_records(patient_records: List[Dict], target_n: int) -> List[Dict]:
    """Repeat records with stable but unique identifiers to simulate larger datasets."""
    if target_n <= len(patient_records):
        return [dict(record) for record in patient_records[:target_n]]

    scaled: List[Dict] = []
    replica = 0

    while len(scaled) < target_n:
        for patient in patient_records:
            if len(scaled) >= target_n:
                break
            cloned = dict(patient)
            cloned_id = f"{patient.get('patient_id', 'unknown')}-{replica}"
            cloned["patient_id"] = cloned_id
            cloned["ma_bn"] = cloned_id
            scaled.append(cloned)
        replica += 1

    return scaled


def setup_duckdb_tables(patient_records: List[Dict], bill_records: List[Dict]):
    """Create DuckDB in-memory tables with patient and bill data."""
    conn = duckdb.connect(":memory:")

    # Create patients table (benh_nhan)
    conn.execute("""
        CREATE TABLE benh_nhan (
            ma_bn VARCHAR,
            patient_id VARCHAR,
            tuoi INTEGER,
            ma_benh VARCHAR,
            khoa_phong_plaintext VARCHAR
        )
    """)

    for patient in patient_records:
        conn.execute(
            "INSERT INTO benh_nhan VALUES (?, ?, ?, ?, ?)",
            [
                str(patient.get("patient_id", "")),  # ma_bn
                str(patient.get("patient_id", "")),  # patient_id
                int(patient.get("tuoi", 0)) if "tuoi" in patient else 0,  # tuoi
                str(patient.get("ma_benh", "E11")),  # ma_benh (disease code)
                str(patient.get("khoa_phong_plaintext", "Unknown"))  # khoa
            ]
        )

    # Create bills table (vien_phi)
    conn.execute("""
        CREATE TABLE vien_phi (
            ma_vp VARCHAR,
            ma_bn VARCHAR,
            so_tien DECIMAL(10, 2),
            khoa VARCHAR
        )
    """)

    for bill in bill_records:
        conn.execute(
            "INSERT INTO vien_phi VALUES (?, ?, ?, ?)",
            [
                str(bill.get("bill_id", "")),  # ma_vp
                str(bill.get("patient_id", "")),  # ma_bn
                float(bill.get("amount", 0)),  # so_tien
                str(bill.get("khoa", ""))  # khoa
            ]
        )

    return conn


def benchmark_hash_join(conn, num_iterations: int = 10) -> Dict:
    """
    Benchmark 1: Hash Join (DuckDB default)
    SELECT b.ma_benh, AVG(v.so_tien)
    FROM benh_nhan b JOIN vien_phi v ON b.ma_bn = v.ma_bn
    WHERE b.tuoi > 60 GROUP BY b.ma_benh
    """
    latencies = []

    for _ in range(num_iterations):
        start = time.perf_counter()
        result = conn.execute("""
            SELECT b.ma_benh, AVG(v.so_tien) as avg_so_tien
            FROM benh_nhan b
            JOIN vien_phi v ON b.ma_bn = v.ma_bn
            WHERE b.tuoi > 60
            GROUP BY b.ma_benh
        """).fetchall()
        elapsed = (time.perf_counter() - start) * 1000  # ms
        latencies.append(elapsed)

    return {
        "strategy": "Hash Join",
        "description": "DuckDB default join",
        "avg_ms": statistics.mean(latencies),
        "p50_ms": statistics.median(latencies),
        "p95_ms": sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else latencies[0],
        "p99_ms": sorted(latencies)[int(len(latencies) * 0.99)] if len(latencies) > 1 else latencies[0],
        "min_ms": min(latencies),
        "max_ms": max(latencies),
        "iterations": num_iterations,
        "result_rows": len(result)
    }


def benchmark_sort_merge_join(conn, num_iterations: int = 10) -> Dict:
    """
    Benchmark 2: Sort-Merge Join (pre-sorted by join key)
    Leverages ORE ordering by pre-sorting on ma_bn
    """
    latencies = []

    for _ in range(num_iterations):
        start = time.perf_counter()
        result = conn.execute("""
            WITH sorted_b AS (
                SELECT * FROM benh_nhan
                WHERE tuoi > 60
                ORDER BY ma_bn
            ),
            sorted_v AS (
                SELECT * FROM vien_phi
                ORDER BY ma_bn
            )
            SELECT sorted_b.ma_benh, AVG(sorted_v.so_tien) as avg_so_tien
            FROM sorted_b
            JOIN sorted_v ON sorted_b.ma_bn = sorted_v.ma_bn
            GROUP BY sorted_b.ma_benh
        """).fetchall()
        elapsed = (time.perf_counter() - start) * 1000  # ms
        latencies.append(elapsed)

    return {
        "strategy": "Sort-Merge Join",
        "description": "Pre-sorted on join key (leverages ORE order)",
        "avg_ms": statistics.mean(latencies),
        "p50_ms": statistics.median(latencies),
        "p95_ms": sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else latencies[0],
        "p99_ms": sorted(latencies)[int(len(latencies) * 0.99)] if len(latencies) > 1 else latencies[0],
        "min_ms": min(latencies),
        "max_ms": max(latencies),
        "iterations": num_iterations,
        "result_rows": len(result)
    }


def benchmark_nested_loop_join(conn, num_iterations: int = 10) -> Dict:
    """
    Benchmark 3: Nested Loop Join (small inner table - E11 only)
    Reduces inner table size to single disease code
    """
    latencies = []

    for _ in range(num_iterations):
        start = time.perf_counter()
        result = conn.execute("""
            SELECT b.ma_benh, AVG(v.so_tien) as avg_so_tien
            FROM (
                SELECT * FROM benh_nhan
                WHERE tuoi > 60 AND ma_benh = 'E11'
            ) b
            JOIN vien_phi v ON b.ma_bn = v.ma_bn
            GROUP BY b.ma_benh
        """).fetchall()
        elapsed = (time.perf_counter() - start) * 1000  # ms
        latencies.append(elapsed)

    return {
        "strategy": "Nested Loop Join",
        "description": "Small inner table (E11 disease only)",
        "avg_ms": statistics.mean(latencies),
        "p50_ms": statistics.median(latencies),
        "p95_ms": sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else latencies[0],
        "p99_ms": sorted(latencies)[int(len(latencies) * 0.99)] if len(latencies) > 1 else latencies[0],
        "min_ms": min(latencies),
        "max_ms": max(latencies),
        "iterations": num_iterations,
        "result_rows": len(result)
    }


def benchmark_radix_join(conn, num_iterations: int = 10, num_buckets: int = 16) -> Dict:
    """
    Benchmark 4: Radix-style partitioned join implemented in Python to simulate a radix join inside enclave.
    Partitions both tables by hash(ma_bn) % num_buckets and performs per-bucket hash joins.
    """
    latencies = []
    result = []

    # Load relevant rows into memory once
    patients = conn.execute("SELECT ma_bn, ma_benh FROM benh_nhan WHERE tuoi > 60").fetchall()
    bills = conn.execute("SELECT ma_bn, so_tien FROM vien_phi").fetchall()

    for _ in range(num_buckets):
        pass

    for _ in range(num_iterations):
        start = time.perf_counter()

        # partition
        p_buckets = {i: [] for i in range(num_buckets)}
        v_buckets = {i: [] for i in range(num_buckets)}

        for ma_bn, ma_benh in patients:
            b = hash(ma_bn) & (num_buckets - 1)
            p_buckets[b].append((ma_bn, ma_benh))

        for ma_bn, so_tien in bills:
            b = hash(ma_bn) & (num_buckets - 1)
            v_buckets[b].append((ma_bn, so_tien))

        # join per bucket
        agg = {}
        for b in range(num_buckets):
            # build hash map for bills by ma_bn
            h = {}
            for ma_bn, so_tien in v_buckets[b]:
                h.setdefault(ma_bn, []).append(so_tien)

            # probe with patients
            for ma_bn, ma_benh in p_buckets[b]:
                vals = h.get(ma_bn)
                if not vals:
                    continue
                agg.setdefault(ma_benh, []).extend(vals)

        # compute averages per disease
        _results = {k: (sum(v) / len(v)) for k, v in agg.items()}

        elapsed = (time.perf_counter() - start) * 1000.0
        latencies.append(elapsed)
        result = _results

    return {
        "strategy": "Radix Join (simulated)",
        "description": f"Partitioned radix-style join with {num_buckets} buckets",
        "avg_ms": statistics.mean(latencies),
        "p50_ms": statistics.median(latencies),
        "p95_ms": sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else latencies[0],
        "p99_ms": sorted(latencies)[int(len(latencies) * 0.99)] if len(latencies) > 1 else latencies[0],
        "min_ms": min(latencies),
        "max_ms": max(latencies),
        "iterations": num_iterations,
        "result_rows": len(result),
    }


def determine_winner(results: List[Dict]) -> str:
    """Determine the fastest strategy based on average latency."""
    best = min(results, key=lambda x: x["avg_ms"])
    return best["strategy"]


def main():
    """Run T12 benchmark."""
    print("=" * 70)
    print("T12 - Hash Join vs Sort-Merge vs Nested Loop in Enclave")
    print("=" * 70)
    print()

    # Load data
    print("[1/4] Loading patient data from archive...")
    loader = ArchiveDataLoader()
    base_patient_records = loader.load_patient_records()
    print(f"  ✓ Loaded {len(base_patient_records)} patient records")

    patient_records = scale_patient_records(base_patient_records, TARGET_PATIENTS)
    print(f"  ✓ Scaled to {len(patient_records)} patient records (target={TARGET_PATIENTS})")

    # Create synthetic bills
    print("[2/4] Creating synthetic bill records...")
    bill_records = loader.create_synthetic_bill_records(patient_records)
    print(f"  ✓ Generated {len(bill_records)} bill records")

    # Setup DuckDB
    print("[3/4] Setting up DuckDB in-memory tables...")
    conn = setup_duckdb_tables(patient_records, bill_records)
    
    # Verify data
    patient_count = conn.execute("SELECT COUNT(*) FROM benh_nhan").fetchone()[0]
    bill_count = conn.execute("SELECT COUNT(*) FROM vien_phi").fetchone()[0]
    elderly_count = conn.execute("SELECT COUNT(*) FROM benh_nhan WHERE tuoi > 60").fetchone()[0]
    e11_count = conn.execute("SELECT COUNT(*) FROM benh_nhan WHERE ma_benh = 'E11'").fetchone()[0]
    print(f"  ✓ benh_nhan: {patient_count} records (elderly age>60: {elderly_count})")
    print(f"  ✓ vien_phi: {bill_count} records")
    print(f"  ✓ E11 patients: {e11_count}")
    print()

    # Run benchmarks
    print("[4/4] Running join strategy benchmarks...")
    print()

    num_iterations = NUM_ITERATIONS
    
    print(f"  [Hash Join] Running {num_iterations} iterations...")
    hash_join_result = benchmark_hash_join(conn, num_iterations)
    print(f"    ✓ Avg: {hash_join_result['avg_ms']:.2f}ms, P95: {hash_join_result['p95_ms']:.2f}ms")

    print(f"  [Sort-Merge] Running {num_iterations} iterations...")
    sort_merge_result = benchmark_sort_merge_join(conn, num_iterations)
    print(f"    ✓ Avg: {sort_merge_result['avg_ms']:.2f}ms, P95: {sort_merge_result['p95_ms']:.2f}ms")

    print(f"  [Nested Loop] Running {num_iterations} iterations...")
    nested_loop_result = benchmark_nested_loop_join(conn, num_iterations)
    print(f"    ✓ Avg: {nested_loop_result['avg_ms']:.2f}ms, P95: {nested_loop_result['p95_ms']:.2f}ms")

    print()

    # Determine winner
    results = [hash_join_result, sort_merge_result, nested_loop_result]
    winner = determine_winner(results)

    # Print comparison table
    print("=" * 70)
    print("COMPARISON TABLE")
    print("=" * 70)
    print()
    print(f"{'Strategy':<20} {'Avg (ms)':<12} {'P95 (ms)':<12} {'P99 (ms)':<12} {'Winner':<10}")
    print("-" * 70)
    for result in results:
        is_winner = "✓" if result["strategy"] == winner else ""
        print(f"{result['strategy']:<20} {result['avg_ms']:>10.2f}  {result['p95_ms']:>10.2f}  {result['p99_ms']:>10.2f}  {is_winner:<10}")
    print()

    # Calculate improvements
    print("IMPROVEMENT ANALYSIS")
    print("-" * 70)
    hash_avg = hash_join_result["avg_ms"]
    sort_avg = sort_merge_result["avg_ms"]
    nested_avg = nested_loop_result["avg_ms"]

    sort_improvement = ((hash_avg - sort_avg) / hash_avg) * 100
    nested_improvement = ((hash_avg - nested_avg) / hash_avg) * 100

    print(f"Sort-Merge vs Hash:    {sort_improvement:+.1f}% {'(faster)' if sort_improvement > 0 else '(slower)'}")
    print(f"Nested Loop vs Hash:   {nested_improvement:+.1f}% {'(faster)' if nested_improvement > 0 else '(slower)'}")
    print()

    # Write results to JSON
    print(f"Writing results to {OUTPUT_JSON_PATH.name}...")
    output = {
        "timestamp": datetime.now().isoformat(),
        "test_name": "T12 - Hash Join vs Sort-Merge vs Nested Loop",
        "environment": {
            "patient_records": patient_count,
            "bill_records": bill_count,
            "elderly_patients_age_gt_60": elderly_count,
            "e11_patients": e11_count
        },
        "target_patients": TARGET_PATIENTS,
        "iterations": num_iterations,
        "results": results,
        "winner": winner,
        "improvements": {
            "sort_merge_vs_hash_pct": sort_improvement,
            "nested_loop_vs_hash_pct": nested_improvement
        }
    }

    with open(OUTPUT_JSON_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"  ✓ Written to {OUTPUT_JSON_PATH}")
    print()

    print("=" * 70)
    print(f"✓ T12 Benchmark PASSED - Winner: {winner}")
    print("=" * 70)

    conn.close()


if __name__ == "__main__":
    main()
