"""
Sinh keypair bất đối xứng cho từng Khoa vào BỘ KEY DÙNG CHUNG (crypto/data/keys/).
Idempotent: chỉ tạo khóa CÒN THIẾU, KHÔNG ghi đè khóa đã có → giữ key chung ổn định
(không gây churn pull/push). Không đụng MongoDB/Vault.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from crypto.asym import DEPARTMENTS, generate_ecc_keypair

KEY_DIR = Path(__file__).resolve().parent / "keys"


def main():
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    created = kept = 0
    for dept in DEPARTMENTS:
        pub = KEY_DIR / f"{dept}_public.pem"
        priv = KEY_DIR / f"{dept}_private.pem"
        if pub.exists() and priv.exists():
            kept += 1
            continue
        priv_pem, pub_pem = generate_ecc_keypair()
        pub.write_text(pub_pem.decode())
        priv.write_text(priv_pem.decode())
        created += 1
    print(f"Keypairs in {KEY_DIR}: {created} tạo mới, {kept} giữ nguyên (đã có)")


if __name__ == '__main__':
    main()
