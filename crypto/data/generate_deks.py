"""
Sinh DEK đối xứng (DTE / ORE / GCM) vào BỘ KEY DÙNG CHUNG (crypto/data/keys/).
Idempotent: chỉ tạo key CÒN THIẾU, KHÔNG ghi đè → giữ key chung ổn định (hết churn).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from crypto.dte import DTECipher
from crypto.ore import ORECipher
from crypto.gcm import AESGCMCipher
from crypto.sse import StaticSSECipher

KEY_DIR = Path(__file__).resolve().parent / "keys"


def _ensure(cipher_cls, filename: str) -> bool:
    path = KEY_DIR / filename
    if path.exists():
        return False
    cipher_cls().save_key(str(path))
    return True


def main():
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    specs = [
        (DTECipher, "dte_ma_benh.key"),
        (DTECipher, "dte_khoa.key"),
        (DTECipher, "dte_cmnd.key"),
        (ORECipher, "ore.key"),
        (AESGCMCipher, "gcm_dek.key"),
        (StaticSSECipher, "sse.key"),
    ]
    created = sum(_ensure(cls, name) for cls, name in specs)
    print(f"DEKs in {KEY_DIR}: {created} tạo mới, {len(specs) - created} giữ nguyên (đã có)")


if __name__ == '__main__':
    main()
