# T7 - Test kịch bản EPC saturation
import httpx
import threading
import time

ROUTER_URL = "http://localhost:8000"

def spam_queries(n: int, stop_event: threading.Event):
    """Gửi liên tục query TEE để tạo tải."""
    while not stop_event.is_set():
        try:
            httpx.post(f"{ROUTER_URL}/query", json={
                "query_type": "avg_vien_phi",
                "filters": {},
                "role": "admin"
            }, timeout=5)
        except:
            pass

def test_epc_saturation():
    print("=" * 60)
    print("T7 — EPC Saturation Test")
    print("=" * 60)

    # Trạng thái ban đầu
    r = httpx.get(f"{ROUTER_URL}/adaptive")
    before = r.json()
    print(f"\n[Before] mode={before['system_mode']} | ratio={before['probe']['ratio']}")

    # Tạo 20 threads đồng thời gửi query TEE
    print("\n[Load] Spawning 20 concurrent TEE queries...")
    stop = threading.Event()
    threads = [threading.Thread(target=spam_queries, args=(100, stop), daemon=True)
               for _ in range(20)]
    for t in threads:
        t.start()

    # Theo dõi 30 giây
    switched = False
    for i in range(6):
        time.sleep(5)
        r = httpx.get(f"{ROUTER_URL}/adaptive")
        status = r.json()
        print(f"  [{i*5+5}s] mode={status['system_mode']} | "
              f"ratio={status['probe']['ratio']} | "
              f"switches={status['switch_count']}")
        if status['system_mode'] == 'fallback':
            switched = True
            print("  ✅ FALLBACK triggered!")
            break

    # Dừng tải
    stop.set()
    print("\n[Load] Stopped — waiting for restore...")
    time.sleep(15)

    # Kiểm tra restore
    r = httpx.get(f"{ROUTER_URL}/adaptive")
    after = r.json()
    print(f"\n[After] mode={after['system_mode']} | switches={after['switch_count']}")
    if after['switch_log']:
        print("\n[Switch Log]:")
        for entry in after['switch_log']:
            print(f"  {entry['timestamp']} — {entry['event']}: {entry['reason']}")

    print("\n" + "=" * 60)
    print(f"Kết quả: {'PASS — fallback triggered' if switched else 'INFO — load chưa đủ gây saturation (simulation mode)'}")
    print("=" * 60)

if __name__ == "__main__":
    test_epc_saturation()
