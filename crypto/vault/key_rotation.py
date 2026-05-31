"""
Key Rotation cho Enc2Health.
- DEK (AES-GCM): xoay tự động theo schedule.
- Keypair ECC: xoay thủ công khi có yêu cầu.
"""
import os, base64, json, logging
from datetime import datetime, timezone
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from .vault_client import _client, store_wrapped_dek
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from vault_client import _client, store_wrapped_dek

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("key_rotation")


def rotate_gcm_dek() -> dict:
    """
    Xoay vòng DEK AES-GCM.
    DEK được wrap bằng Vault Transit trước khi ghi vào KV-v2.
    """
    new_key = os.urandom(32)
    store_wrapped_dek(
        key_name="gcm_dek",
        raw_key=new_key,
        algorithm="AES-GCM-256",
        purpose="lab_and_billing",
    )

    log.info("GCM DEK rotated successfully and stored as Transit-wrapped ciphertext.")
    return {"status": "rotated", "wrapped_with": "vault-transit", "key_name": "gcm_dek"}


def rotate_department_keypair(dept: str) -> dict:
    """
    Xoay vòng keypair ECC P-384 cho một Khoa.
    ⚠️ Sau khi rotate, phải re-encrypt tất cả PII của Khoa đó.
    """
    import sys
    sys.path.insert(0, "..")
    from crypto.asym import generate_ecc_keypair

    c = _client()
    new_priv, new_pub = generate_ecc_keypair()

    c.secrets.kv.v2.create_or_update_secret(
        path=f"keypairs/{dept}",
        secret={
            "private_key": new_priv.decode(),
            "public_key": new_pub.decode(),
            "algorithm": "ECC_P384",
            "department": dept,
            "rotated_at": datetime.now(timezone.utc).isoformat()
        },
        mount_point="enc2health"
    )

    log.warning(
        f"Keypair for {dept} rotated! "
        f"⚠️ Re-encryption of existing PII records required."
    )
    return {"status": "rotated", "dept": dept}


def get_key_metadata(key_path: str) -> dict:
    """Lấy metadata của key (version, ngày tạo)."""
    c = _client()
    meta = c.secrets.kv.v2.read_secret_metadata(
        path=key_path,
        mount_point="enc2health"
    )
    versions = meta["data"]["versions"]
    return {
        "current_version": meta["data"]["current_version"],
        "total_versions": len(versions),
        "oldest_version": meta["data"]["oldest_version"],
    }


# Cron-style scheduler (dùng cron job hoặc APScheduler)
def setup_rotation_schedule():
    """
    Setup rotation schedule.
    Trong production: dùng cron hoặc systemd timer.
    """
    cron_entry = "0 2 * * 0  cd ~/enc2health && source .venv/bin/activate && python vault/key_rotation.py --rotate-dek"
    print("Thêm vào crontab (crontab -e):")
    print(f"  {cron_entry}")
    print("\nHoặc dùng systemd timer:")
    print("  sudo systemctl enable enc2health-key-rotation.timer")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--rotate-dek", action="store_true")
    parser.add_argument("--rotate-keypair", metavar="DEPT")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.rotate_dek:
        result = rotate_gcm_dek()
        print(f"DEK rotation: {result}")
    elif args.rotate_keypair:
        result = rotate_department_keypair(args.rotate_keypair)
        print(f"Keypair rotation: {result}")
    elif args.status:
        for key_path in ["dek/gcm_dek", "dek/dte_ma_benh"]:
            meta = get_key_metadata(key_path)
            print(f"  {key_path}: version {meta['current_version']} (total: {meta['total_versions']})")
    else:
        setup_rotation_schedule()
