# T6 - Adaptive Switching: fallback & restore dựa trên EPC saturation
import time
import threading
from enum import Enum
from typing import Optional
from router.probing import EPCProber
from router.query_router import ExecutionMode

EPC_THRESHOLD = 0.80          # >=80% áp lực EPC/RSS → fallback (đúng kịch bản)
EPC_RESTORE_THRESHOLD = 0.60  # chỉ phục hồi khi <=60% → hysteresis chống dao động (flapping)

class SystemMode(Enum):
    NORMAL   = "normal"
    FALLBACK = "fallback"

class AdaptiveController:
    def __init__(self):
        self.prober = EPCProber()
        self.mode = SystemMode.NORMAL
        self._lock = threading.Lock()
        self.switch_log = []
        # Núm mô phỏng áp lực EPC cho demo/kiểm thử tất định.
        # None = dùng tín hiệu THẬT từ prober (/proc RSS, EPC smaps).
        self._sim_pressure: Optional[float] = None

    def start(self):
        self.prober.start()
        print("[Adaptive] Controller started")

    def stop(self):
        self.prober.stop()
        print("[Adaptive] Controller stopped")

    def set_simulated_pressure(self, ratio: Optional[float]) -> None:
        """Ép áp lực EPC để demo/kiểm thử (0.0–1.0). None = trở lại tín hiệu thật."""
        with self._lock:
            self._sim_pressure = ratio

    def _signal(self) -> tuple[bool, Optional[float], str]:
        """Trả (saturated_bool, pressure_ratio|None, nguồn). Ưu tiên núm mô phỏng."""
        if self._sim_pressure is not None:
            return self._sim_pressure >= EPC_THRESHOLD, self._sim_pressure, "simulated"
        status = self.prober.get_status()
        resource = status.get("resource") or {}
        epc_available = resource.get("epc_available")
        pressure = resource.get("epc_percent") if epc_available else resource.get("rss_percent")
        return self.prober.is_saturated(), pressure, "probe"

    def _apply_hysteresis(self, saturated_bool: bool, pressure: Optional[float], source: str) -> None:
        """Chuyển trạng thái có vùng chết (dead band) để tránh flapping.

        - NORMAL → FALLBACK khi pressure >= 80% (hoặc prober báo saturated nếu thiếu số).
        - FALLBACK → NORMAL khi pressure <= 60% (hoặc prober hết saturated).
        - Vùng 60–80%: GIỮ NGUYÊN trạng thái hiện tại.
        """
        if self.mode == SystemMode.NORMAL:
            trigger = pressure >= EPC_THRESHOLD if pressure is not None else saturated_bool
            if trigger:
                self._switch_to_fallback(pressure, source)
        else:  # FALLBACK
            relieved = pressure <= EPC_RESTORE_THRESHOLD if pressure is not None else (not saturated_bool)
            if relieved:
                self._restore_to_normal(pressure, source)

    def get_execution_mode(self, requested_mode: ExecutionMode) -> ExecutionMode:
        with self._lock:
            self._apply_hysteresis(*self._signal())
            # Dưới fallback: hạ các toán tử TEE (AVG/SUM) xuống Software để giữ thông lượng.
            if self.mode == SystemMode.FALLBACK and requested_mode == ExecutionMode.TEE:
                return ExecutionMode.SOFTWARE
            return requested_mode

    def _switch_to_fallback(self, pressure: Optional[float], source: str):
        self.mode = SystemMode.FALLBACK
        entry = {
            "event": "FALLBACK",
            "timestamp": time.strftime("%H:%M:%S"),
            "reason": f"EPC saturation detected (pressure={_fmt(pressure)}, source={source})",
            "pressure": pressure,
            "probe_status": self.prober.get_status(),
        }
        self.switch_log.append(entry)
        print(f"[Adaptive] FALLBACK activated — {entry['timestamp']} — {entry['reason']}")

    def _restore_to_normal(self, pressure: Optional[float], source: str):
        self.mode = SystemMode.NORMAL
        entry = {
            "event": "RESTORE",
            "timestamp": time.strftime("%H:%M:%S"),
            "reason": f"EPC pressure relieved (pressure={_fmt(pressure)}, source={source})",
            "pressure": pressure,
            "probe_status": self.prober.get_status(),
        }
        self.switch_log.append(entry)
        print(f"[Adaptive] RESTORED to TEE mode — {entry['timestamp']} — {entry['reason']}")

    def get_status(self) -> dict:
        with self._lock:
            saturated, pressure, source = self._signal()
            self._apply_hysteresis(saturated, pressure, source)
            mode = self.mode.value
        return {
            "system_mode": mode,
            "pressure_ratio": pressure,
            "epc_threshold": EPC_THRESHOLD,
            "restore_threshold": EPC_RESTORE_THRESHOLD,
            "saturated": saturated,
            "pressure_source": source,
            "probe": self.prober.get_status(),
            "switch_count": len(self.switch_log),
            "switch_log": self.switch_log[-5:],
        }


def _fmt(pressure: Optional[float]) -> str:
    return f"{pressure*100:.0f}%" if isinstance(pressure, (int, float)) else "n/a"
