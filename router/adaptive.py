# T6 - Adaptive Switching: fallback & restore dựa trên EPC saturation
import time
import threading
from enum import Enum
from typing import Optional
from router.probing import EPCProber
from router.query_router import ExecutionMode

class SystemMode(Enum):
    NORMAL   = "normal"
    FALLBACK = "fallback"

class AdaptiveController:
    def __init__(self):
        self.prober = EPCProber()
        self.mode = SystemMode.NORMAL
        self._lock = threading.Lock()
        self.switch_log = []

    def start(self):
        self.prober.start()
        print("[Adaptive] Controller started")

    def stop(self):
        self.prober.stop()
        print("[Adaptive] Controller stopped")

    def get_execution_mode(self, requested_mode: ExecutionMode) -> ExecutionMode:
        with self._lock:
            saturated = self.prober.is_saturated()
            if saturated and self.mode == SystemMode.NORMAL:
                self._switch_to_fallback()
            elif not saturated and self.mode == SystemMode.FALLBACK:
                self._restore_to_normal()
            if self.mode == SystemMode.FALLBACK and requested_mode == ExecutionMode.TEE:
                return ExecutionMode.SOFTWARE
            return requested_mode

    def _switch_to_fallback(self):
        self.mode = SystemMode.FALLBACK
        entry = {
            "event": "FALLBACK",
            "timestamp": time.strftime("%H:%M:%S"),
            "reason": "EPC saturation detected",
            "probe_status": self.prober.get_status()
        }
        self.switch_log.append(entry)
        print(f"[Adaptive] FALLBACK activated — {entry['timestamp']}")

    def _restore_to_normal(self):
        self.mode = SystemMode.NORMAL
        entry = {
            "event": "RESTORE",
            "timestamp": time.strftime("%H:%M:%S"),
            "reason": "EPC pressure relieved",
            "probe_status": self.prober.get_status()
        }
        self.switch_log.append(entry)
        print(f"[Adaptive] RESTORED to TEE mode — {entry['timestamp']}")

    def get_status(self) -> dict:
        return {
            "system_mode": self.mode.value,
            "probe": self.prober.get_status(),
            "switch_count": len(self.switch_log),
            "switch_log": self.switch_log[-5:]
        }
