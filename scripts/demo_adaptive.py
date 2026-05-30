#!/usr/bin/env python3
"""
Demo cơ chế TỰ THÍCH NGHI (Adaptive Fallback) — CÓ LỜI DẪN.

Mô phỏng kịch bản trong đề tài (mục 4):
  Bình thường  → mọi phân tích chạy trong Enclave (TEE) để bảo mật tuyệt đối.
  Đợt dịch     → quá nhiều yêu cầu → EPC enclave bão hòa (>80%).
  Probing      → phát hiện áp lực tăng.
  Fallback     → Router TỰ ĐỘNG hạ các truy vấn AVG/SUM xuống Software Mode
                 (DTE/ORE) để giữ thông lượng, tránh treo hệ thống bệnh viện.
  Phục hồi     → áp lực giảm → tự quay lại TEE.

Demo này KHÔNG cần MongoDB hay Pool — chỉ chứng minh logic điều khiển của Router
(Nam) bằng núm mô phỏng áp lực EPC. Chạy:  python3 scripts/demo_adaptive.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from router.adaptive import AdaptiveController, EPC_THRESHOLD
from router.query_router import ExecutionMode


def line(c="─", n=72):
    print(c * n)


def step(ctrl: AdaptiveController, pressure: float, note: str):
    """Đặt áp lực EPC, hỏi router 'AVG nên chạy ở đâu?', in kết quả."""
    ctrl.set_simulated_pressure(pressure)
    mode = ctrl.get_execution_mode(ExecutionMode.TEE)  # truy vấn AVG (vốn là TEE)
    sys_mode = ctrl.mode.value.upper()
    bar_len = int(pressure * 30)
    bar = "█" * bar_len + "░" * (30 - bar_len)
    flag = "⚠️ FALLBACK" if mode == ExecutionMode.SOFTWARE else "🔒 TEE"
    print(f"  EPC [{bar}] {pressure*100:4.0f}%  | AVG chạy ở: {mode.value.upper():8} {flag}")
    print(f"        hệ thống: {sys_mode:8} | {note}")


def main():
    print()
    line("═")
    print("  DEMO Tự thích nghi (Adaptive Fallback) — Router Enc²Health")
    print(f"  Ngưỡng fallback: EPC > {EPC_THRESHOLD*100:.0f}%   (truy vấn minh họa: AVG viện phí)")
    line("═")

    ctrl = AdaptiveController()   # KHÔNG start prober — chỉ test logic điều khiển

    print("\n  [1] BÌNH THƯỜNG — ít tải, enclave thoải mái")
    line()
    step(ctrl, 0.25, "phân tích nhạy cảm chạy trong enclave (bảo mật tối đa)")
    step(ctrl, 0.55, "tải tăng nhẹ nhưng vẫn dưới ngưỡng → giữ TEE")

    print("\n  [2] ĐỢT DỊCH — yêu cầu phân tích tăng vọt, EPC bão hòa")
    line()
    step(ctrl, 0.72, "áp lực tăng, probing theo dõi sát")
    step(ctrl, 0.86, "VƯỢT 80% → router TỰ ĐỘNG fallback AVG xuống Software")
    step(ctrl, 0.95, "cao điểm — vẫn chạy Software để không treo bệnh viện")

    print("\n  [3] PHỤC HỒI — đợt dịch qua đi, áp lực giảm")
    line()
    step(ctrl, 0.65, "65% nằm trong vùng chết 60–80% → GIỮ fallback (chống dao động)")
    step(ctrl, 0.35, "xuống dưới 60% → router TỰ ĐỘNG khôi phục về TEE")

    print()
    line("━")
    print("  NHẬT KÝ CHUYỂN ĐỔI (switch log) — bằng chứng tự thích nghi:")
    line("━")
    status = ctrl.get_status()
    if not status["switch_log"]:
        print("  (không có chuyển đổi nào)")
    for e in status["switch_log"]:
        print(f"  {e['timestamp']}  {e['event']:8}  {e['reason']}")
    print(f"\n  Tổng số lần chuyển đổi: {status['switch_count']}")

    print()
    line("═")
    print("  Ý NGHĨA: Router cân bằng động giữa BẢO MẬT (TEE) và THÔNG LƯỢNG (Software).")
    print("  Khi enclave quá tải, hệ thống không sập — nó hạ cấp có kiểm soát rồi tự hồi.")
    print("  Đây chính là chữ 'Adaptive' trong kiến trúc Hybrid Adaptive của đề tài.")
    line("═")
    print()


if __name__ == "__main__":
    main()
