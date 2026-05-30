"""Real resource monitor for the ECALL pool process.

This module reads real process RSS from /proc and tries to estimate EPC usage
from enclave-related memory regions when they are visible in /proc/<pid>/smaps.
It also writes snapshots to a JSON file for external consumers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

EPC_HINTS = ("sgx", "enclave", "gramine", "/dev/sgx")
DEFAULT_METRICS_PATH = Path(os.environ.get("EPC_METRICS_PATH", "/tmp/enc2health_epc_metrics.json"))
DEFAULT_PROCESS_NAME = os.environ.get("ECALL_POOL_PROCESS_NAME", "ecall_pool.py")


@dataclass
class ResourceSample:
    pid: Optional[int]
    rss_bytes: int
    rss_mb: float
    rss_percent: float
    epc_bytes: int
    epc_mb: float
    epc_percent: float
    epc_available: bool
    source: str
    timestamp: float

    @property
    def saturated(self) -> bool:
        return (self.epc_available and self.epc_percent >= 0.80) or self.rss_percent >= 0.80



def _read_text(path: Path) -> str:
    return path.read_text(errors="ignore")



def _read_total_memory_bytes() -> int:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        phys_pages = os.sysconf("SC_PHYS_PAGES")
        if isinstance(page_size, int) and isinstance(phys_pages, int) and page_size > 0 and phys_pages > 0:
            return int(page_size) * int(phys_pages)
    except Exception:
        pass

    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in _read_text(meminfo).splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                # kB -> bytes
                return int(parts[1]) * 1024
    return 0



def _read_pid_from_env() -> Optional[int]:
    raw_pid = os.environ.get("ECALL_POOL_PID")
    if not raw_pid:
        return None
    try:
        pid = int(raw_pid)
        return pid if pid > 0 else None
    except ValueError:
        return None



def discover_ecall_pool_pid(process_name: str = DEFAULT_PROCESS_NAME) -> Optional[int]:
    pid = _read_pid_from_env()
    if pid:
        return pid

    try:
        result = subprocess.run(
            ["pgrep", "-f", process_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = [int(item) for item in result.stdout.split() if item.strip().isdigit()]
            if pids:
                return pids[-1]
    except Exception:
        pass
    return None



def read_rss_bytes(pid: int) -> int:
    status_path = Path(f"/proc/{pid}/status")
    if not status_path.exists():
        return 0
    for line in _read_text(status_path).splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            return int(parts[1]) * 1024
    return 0



def _parse_kb_value(line: str) -> int:
    parts = line.split()
    if len(parts) < 2:
        return 0
    try:
        return int(parts[1]) * 1024
    except ValueError:
        return 0



def read_epc_bytes(pid: int) -> tuple[int, bool, str]:
    """Try to estimate EPC bytes from enclave-related mappings in smaps.

    On a real SGX/Gramine host, enclave memory may show up in smaps with a
    distinctive mapping label or path. If none is found, the function returns
    zero and marks EPC as unavailable.
    """
    smaps_path = Path(f"/proc/{pid}/smaps")
    if not smaps_path.exists():
        return 0, False, "smaps_unavailable"

    total = 0
    matched = False
    current_match = False
    try:
        for line in _read_text(smaps_path).splitlines():
            if re.match(r"^[0-9a-fA-F]+-[0-9a-fA-F]+\s", line):
                current_match = any(hint in line.lower() for hint in EPC_HINTS)
            elif current_match and line.startswith("Rss:"):
                total += _parse_kb_value(line)
                matched = True
    except Exception:
        return 0, False, "smaps_parse_error"

    if matched:
        return total, True, "smaps_enclave_match"
    return 0, False, "no_enclave_mapping"



def capture_process_sample(pid: Optional[int] = None) -> ResourceSample:
    total_mem = _read_total_memory_bytes()
    timestamp = time.time()
    if pid is None:
        pid = discover_ecall_pool_pid()

    if pid is None:
        return ResourceSample(
            pid=None,
            rss_bytes=0,
            rss_mb=0.0,
            rss_percent=0.0,
            epc_bytes=0,
            epc_mb=0.0,
            epc_percent=0.0,
            epc_available=False,
            source="process_not_found",
            timestamp=timestamp,
        )

    rss_bytes = read_rss_bytes(pid)
    epc_bytes, epc_available, epc_source = read_epc_bytes(pid)
    rss_percent = (rss_bytes / total_mem) if total_mem else 0.0
    epc_percent = (epc_bytes / total_mem) if total_mem else 0.0

    return ResourceSample(
        pid=pid,
        rss_bytes=rss_bytes,
        rss_mb=round(rss_bytes / (1024 * 1024), 3),
        rss_percent=round(rss_percent, 6),
        epc_bytes=epc_bytes,
        epc_mb=round(epc_bytes / (1024 * 1024), 3),
        epc_percent=round(epc_percent, 6),
        epc_available=epc_available,
        source=epc_source,
        timestamp=timestamp,
    )



def write_metrics_snapshot(sample: ResourceSample, path: Path = DEFAULT_METRICS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(sample)
    payload["timestamp_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sample.timestamp))
    payload["saturated"] = sample.saturated
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
