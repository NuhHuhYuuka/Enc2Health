#!/usr/bin/env python3
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.auth import generate_test_jwt

def main():
    # ES256: ký bằng private key trong crypto/data/keys/ (sinh bằng generate_jwt_keys.py).
    sub = os.environ.get("AUTH_SUBJECT", "router-service")
    role = os.environ.get("AUTH_ROLE", "service")
    try:
        token = generate_test_jwt(sub, role)
    except RuntimeError as exc:
        print(f"{exc}", file=sys.stderr)
        sys.exit(2)
    print(token)

if __name__ == '__main__':
    main()
