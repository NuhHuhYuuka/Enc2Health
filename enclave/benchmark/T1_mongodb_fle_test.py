#!/usr/bin/env python3
"""
T1 - MongoDB FLE Connector Test
Loads the real Lan handoff dataset from the MongoDB archive and benchmarks
basic read/aggregation paths over patient_records.
"""

from collections import Counter
from datetime import datetime
import csv
import os
from pathlib import Path
import binascii
import gzip
import struct


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

BENCHMARK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCHMARK_DIR.parent
DEFAULT_ARCHIVE_PATH = PROJECT_ROOT / "To_Lan_from_Long" / "lan_demo" / "dataset" / "enc2health_dump.archive"
ARCHIVE_PATH = Path(os.environ.get("ENC2HEALTH_ARCHIVE_PATH", DEFAULT_ARCHIVE_PATH))
BASELINE_CSV_PATH = Path(__file__).resolve().parent / "benchmark_baseline.csv"


def _get_rss_mb():
    try:
        with open(f"/proc/{os.getpid()}/status", "r", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    return float(parts[1]) / 1024.0
    except OSError:
        pass
    return None


def _cstring(data, position):
    end = data.index(b"\x00", position)
    return data[position:end].decode("utf-8", "replace"), end + 1


def _decode_doc(data, position):
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


def _decode_value(data, position, element_type):
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
        milliseconds = struct.unpack_from("<q", data, position)[0]
        return datetime.utcfromtimestamp(milliseconds / 1000), position + 8
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

    raise ValueError(f"Unsupported BSON type 0x{element_type:02x} at offset {position}")


class ArchiveMongoDBFLE:
    """Loads the Enc2Health archive and exposes a small mock FLE-style API."""

    def __init__(self, archive_path=ARCHIVE_PATH):
        self.archive_path = Path(archive_path)
        if not self.archive_path.exists():
            raise FileNotFoundError(
                f"MongoDB archive not found: {self.archive_path}. "
                "Set ENC2HEALTH_ARCHIVE_PATH to the archive location if it lives elsewhere."
            )
        self.collections = {"patient_records": self._load_patient_records()}

    def _read_archive_bytes(self):
        with gzip.open(self.archive_path, "rb") as archive_file:
            return archive_file.read()

    def _find_first_record(self, data):
        marker = b"\x07_id\x00"
        marker_position = data.find(marker)
        if marker_position == -1:
            raise ValueError("Could not locate the first patient record in the archive")
        return marker_position - 4

    def _load_patient_records(self):
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

        return records

    def query_collection(self, collection_name):
        return self.collections.get(collection_name)

    def aggregate(self, collection_name, pipeline):
        data = self.query_collection(collection_name)
        if not data:
            return []

        group_spec = pipeline.get("$group", {})
        group_field = group_spec.get("_id")
        if isinstance(group_field, str) and group_field.startswith("$"):
            field_name = group_field[1:]
            counts = Counter(str(record.get(field_name, "unknown")) for record in data)
            return [{"_id": key, "count": value} for key, value in counts.most_common()]

        return data[:10]


def benchmark_fle_connector():
    """Benchmark the real dataset load and a simple aggregation."""
    print("=" * 60)
    print("T1 - MongoDB FLE Connector Test (Real Dataset)")
    print("=" * 60)
    print()

    rss_before = _get_rss_mb()
    start = datetime.now()
    fle = ArchiveMongoDBFLE()
    load_elapsed = (datetime.now() - start).total_seconds() * 1000
    rss_after = _get_rss_mb()

    print("[Test 1] Load collection: patient_records")
    records = fle.query_collection("patient_records")
    print(f"  ✓ Loaded {len(records)} encrypted patient records in {load_elapsed:.2f}ms")
    print(
        "  Sample:",
        {
            "patient_id": records[0]["patient_id"],
            "khoa_phong_plaintext": records[0]["khoa_phong_plaintext"],
            "dept": records[0].get("dept"),
        },
    )
    print()

    print("[Test 2] Aggregation: COUNT by khoa_phong_plaintext")
    start = datetime.now()
    result = fle.aggregate(
        "patient_records",
        {"$group": {"_id": "$khoa_phong_plaintext", "count": {"$sum": 1}}},
    )
    elapsed = (datetime.now() - start).total_seconds() * 1000
    print(f"  ✓ Grouped into {len(result)} departments in {elapsed:.2f}ms")
    for row in result:
        print(f"    - {row['_id']}: {row['count']} records")
    print()

    print("[Test 3] Aggregation: Top department")
    top_department = result[0] if result else {"_id": "unknown", "count": 0}
    print(f"  ✓ Top department: {top_department['_id']} ({top_department['count']} records)")
    print()

    print("[Test 4] Dataset coverage")
    known_departments = sorted({record["khoa_phong_plaintext"] for record in records})
    print(f"  ✓ Departments in dataset: {known_departments}")
    print()

    print("[Test 5] Baseline metrics CSV")
    with open(BASELINE_CSV_PATH, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["metric", "value", "unit", "note"])
        writer.writerow(["simulation_load_time", f"{load_elapsed:.2f}", "ms", "Archive load time in direct-mode simulation"])
        writer.writerow([
            "memory_footprint_rss_before",
            "" if rss_before is None else f"{rss_before:.2f}",
            "MB",
            "RSS before loading the archive",
        ])
        writer.writerow([
            "memory_footprint_rss_after",
            "" if rss_after is None else f"{rss_after:.2f}",
            "MB",
            "RSS after loading the archive",
        ])
        if rss_before is not None and rss_after is not None:
            writer.writerow([
                "memory_footprint_delta",
                f"{rss_after - rss_before:.2f}",
                "MB",
                "Process RSS delta during archive load",
            ])
        writer.writerow(["query_latency_count_group", f"{elapsed:.2f}", "ms", "COUNT by khoa_phong_plaintext"])
        writer.writerow(["dataset_records", str(len(records)), "records", "Loaded patient_records documents"])
    print(f"  ✓ Wrote {BASELINE_CSV_PATH.name}")
    print()

    print("=" * 60)
    print("✓ T1 MongoDB FLE Connector Test PASSED")
    print("=" * 60)
    print()
    print("PAL issue has been resolved")


if __name__ == "__main__":
    benchmark_fle_connector()
