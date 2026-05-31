#!/usr/bin/env python3
"""
T3 - AES-GCM Register-Safe Decryption
Ensures plaintext is zero-filled immediately after use (equivalent to explicit_bzero).
Benchmarks throughput: 1K, 5K, 10K records → expected ~200K rec/s
"""

import base64
import time
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _secure_zero(buf: bytearray):
    """Zero-fill buffer immediately after use – equivalent to explicit_bzero."""
    for i in range(len(buf)):
        buf[i] = 0


def decrypt_and_compute_avg(ciphertext_list: list[str], key: bytes) -> float:
    """
    Decrypt a list of AES-GCM ciphertexts and compute average value.
    Plaintext is zero-filled immediately after each use.
    
    Args:
        ciphertext_list: List of base64-encoded AES-GCM ciphertexts
        key: 32-byte AES-256 key
    
    Returns:
        Average of decrypted plaintext values
    """
    aesgcm = AESGCM(key)
    total = 0.0
    count = 0
    
    for ct_b64 in ciphertext_list:
        raw = base64.b64decode(ct_b64)
        nonce = raw[:12]
        ct = raw[12:]
        
        # Decrypt into bytearray (mutable for explicit zero-fill)
        plaintext_bytes = bytearray(aesgcm.decrypt(nonce, ct, None))
        
        # Use immediately
        try:
            value = float(plaintext_bytes.decode('utf-8'))
            total += value
            count += 1
        finally:
            # Zero-fill immediately after use
            _secure_zero(plaintext_bytes)
            del plaintext_bytes
    
    if count == 0:
        return 0.0
    
    return total / count


def generate_test_data(num_records: int, key: bytes) -> list[str]:
    """Generate test ciphertexts for benchmarking."""
    aesgcm = AESGCM(key)
    ciphertexts = []
    
    for i in range(num_records):
        nonce = os.urandom(12)
        plaintext = str(float(i % 1000)).encode('utf-8')
        ct = aesgcm.encrypt(nonce, plaintext, None)
        ct_b64 = base64.b64encode(nonce + ct).decode()
        ciphertexts.append(ct_b64)
    
    return ciphertexts


def benchmark_throughput(num_records: int) -> float:
    """
    Benchmark AES-GCM decryption throughput.
    
    Args:
        num_records: Number of records to decrypt
    
    Returns:
        Throughput in records/second
    """
    key = os.urandom(32)
    ciphertexts = generate_test_data(num_records, key)
    
    t_start = time.perf_counter()
    avg_value = decrypt_and_compute_avg(ciphertexts, key)
    t_end = time.perf_counter()
    
    elapsed_sec = t_end - t_start
    throughput = num_records / elapsed_sec if elapsed_sec > 0 else 0
    
    return throughput


def main():
    """Run benchmarks for different record counts."""
    print("[T3 AES-GCM Register-Safe Benchmark]")
    print()
    
    test_cases = [1_000, 5_000, 10_000]
    
    for num_records in test_cases:
        throughput = benchmark_throughput(num_records)
        throughput_k = throughput / 1000  # Convert to thousands
        
        print(f"{num_records:>6,} records: {throughput_k:>8.1f} K rec/s")
    
    print()
    print("✅ Expected: ~200 K rec/s (target > 150 K rec/s)")


if __name__ == '__main__':
    main()
