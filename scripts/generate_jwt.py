#!/usr/bin/env python3
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.auth import generate_test_jwt

def main():
    secret = os.environ.get("AUTH_JWT_SECRET")
    if not secret:
        print("Set AUTH_JWT_SECRET in environment to generate a test JWT", file=sys.stderr)
        sys.exit(2)
    sub = os.environ.get("AUTH_SUBJECT", "router-service")
    role = os.environ.get("AUTH_ROLE", "service")
    token = generate_test_jwt(sub, role)
    print(token)

if __name__ == '__main__':
    main()
