#!/usr/bin/env python3
"""
Copy of enclave/test_ecall_pool.py for handoff to Nam
"""

import pathlib

with open(str(pathlib.Path(__file__).resolve().parent.parent / 'enclave' / 'test_ecall_pool.py'), 'r') as src:
    content = src.read()

with open(str(pathlib.Path(__file__).resolve().parent / 'test_ecall_pool.py'), 'w') as dst:
    dst.write(content)
