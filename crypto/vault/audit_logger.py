"""
Parser cho Vault audit log.
Tách các sự kiện lấy Private Key → ghi vào HIPAA audit trail.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from pymongo import MongoClient

VAULT_AUDIT_LOG = "/var/log/vault/audit.log"
MONGO_URI = "mongodb://localhost:27017"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("audit")


def parse_vault_audit_event(line: str) -> dict | None:
    """Parse 1 dòng JSON từ Vault audit log."""
    try:
        event = json.loads(line)
        if event.get("type") != "request":
            return None
        path = event.get("request", {}).get("path", "")
        # Chỉ quan tâm: lấy private key
        if "keypairs/" in path and "private" not in path:
            return None
        if "keypairs/" not in path:
            return None
        return {
            "timestamp": event.get("time"),
            "path": path,
            "operation": event.get("request", {}).get("operation"),
            "remote_address": event.get("request", {}).get("remote_address"),
            "auth_display_name": event.get("auth", {}).get("display_name", "unknown"),
        }
    except (json.JSONDecodeError, KeyError):
        return None


def tail_audit_log_to_mongo():
    """
    Đọc Vault audit log và lưu sự kiện private key access vào MongoDB.
    Chạy như daemon hoặc gọi định kỳ.
    """
    client = MongoClient(MONGO_URI)
    audit_col = client["enc2health"]["hipaa_audit_log"]

    audit_path = Path(VAULT_AUDIT_LOG)
    if not audit_path.exists():
        log.warning(f"Vault audit log không tồn tại: {VAULT_AUDIT_LOG}")
        log.info("Enable với: vault audit enable file file_path=/var/log/vault/audit.log")
        return

    count = 0
    with open(audit_path) as f:
        for line in f:
            event = parse_vault_audit_event(line.strip())
            if event:
                audit_col.update_one(
                    {"timestamp": event["timestamp"], "path": event["path"]},
                    {"$set": event},
                    upsert=True
                )
                count += 1

    log.info(f"Synced {count} private key access events to MongoDB audit log")
    client.close()


def log_manual_event(action: str, dept: str, user: str, details: str = ""):
    """Ghi manual audit event (không qua Vault)."""
    client = MongoClient(MONGO_URI)
    client["enc2health"]["hipaa_audit_log"].insert_one({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "department": dept,
        "user": user,
        "details": details,
        "source": "manual"
    })
    client.close()


if __name__ == "__main__":
    tail_audit_log_to_mongo()
    # Xem audit log
    client = MongoClient(MONGO_URI)
    recent = list(client["enc2health"]["hipaa_audit_log"].find().limit(5))
    for r in recent:
        r.pop("_id", None)
        print(json.dumps(r, indent=2))
    client.close()
