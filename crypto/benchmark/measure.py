"""
Benchmark tổng hợp cho Long T10.
Đo: ECC encrypt/decrypt, RSA encrypt/decrypt, AES-SIV, OPE, AES-GCM, KMS API latency.
Output: JSON + bảng in terminal.
"""
import sys, os, time, statistics, json, base64
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crypto.asym import generate_ecc_keypair, ecc_encrypt, ecc_decrypt
from crypto.asym import generate_rsa_keypair, rsa_encrypt, rsa_decrypt
from crypto.dte  import DTECipher
from crypto.ore  import ORECipher
from crypto.gcm  import AESGCMCipher

ITERATIONS = 1000

def bench(name: str, fn, n: int = ITERATIONS) -> dict:
    """Chạy fn() n lần, tính latency statistics."""
    # Warm-up
    for _ in range(10):
        fn()

    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1_000_000)  # microseconds

    times.sort()
    return {
        "operation": name,
        "iterations": n,
        "avg_us":  round(statistics.mean(times), 2),
        "med_us":  round(statistics.median(times), 2),
        "p95_us":  round(times[int(n * 0.95)], 2),
        "p99_us":  round(times[int(n * 0.99)], 2),
        "max_us":  round(times[-1], 2),
        "throughput_per_sec": round(1_000_000 / statistics.mean(times), 0),
    }


def run_benchmarks():
    results = []
    SAMPLE_PII  = "Nguyen Thi Bich Phuong - CCCD: 034099012345 - Dia chi: 123 Le Loi, Q1, HCMC"
    SAMPLE_LAB  = {"glucose": 7.5, "hba1c": 8.2, "creatinine": 95.0}
    SAMPLE_BILL = 12_500_000.0  # VND

    print("Generating keys...")
    ecc_priv, ecc_pub = generate_ecc_keypair()
    rsa_priv, rsa_pub = generate_rsa_keypair()
    ecc_ct = ecc_encrypt(SAMPLE_PII, ecc_pub)
    rsa_ct = rsa_encrypt(SAMPLE_PII[:200], rsa_pub)  # RSA-4096 max ~501 bytes

    dte    = DTECipher()
    ore    = ORECipher()
    gcm    = AESGCMCipher()
    gcm_ct = gcm.encrypt(SAMPLE_LAB)
    dte_ct = dte.encrypt("E11")
    ore_ct = ore.encrypt_age(65)

    print(f"Running {ITERATIONS} iterations per operation...\n")

    # Asymmetric operations
    results.append(bench("ECC P-384 Encrypt (ECIES)",
        lambda: ecc_encrypt(SAMPLE_PII, ecc_pub), n=200))
    results.append(bench("ECC P-384 Decrypt (ECIES)",
        lambda: ecc_decrypt(ecc_ct, ecc_priv), n=200))
    results.append(bench("RSA-4096 Encrypt (OAEP)",
        lambda: rsa_encrypt(SAMPLE_PII[:200], rsa_pub), n=50))
    results.append(bench("RSA-4096 Decrypt (OAEP)",
        lambda: rsa_decrypt(rsa_ct, rsa_priv), n=50))

    # Symmetric operations
    results.append(bench("AES-SIV Encrypt (DTE)",
        lambda: dte.encrypt("E11"), n=ITERATIONS))
    results.append(bench("AES-SIV Decrypt (DTE)",
        lambda: dte.decrypt(dte_ct), n=ITERATIONS))
    results.append(bench("OPE Encrypt (age)",
        lambda: ore.encrypt_age(65), n=ITERATIONS))
    results.append(bench("OPE Decrypt (age)",
        lambda: ore.decrypt_age(ore_ct), n=ITERATIONS))
    results.append(bench("AES-GCM-256 Encrypt (lab)",
        lambda: gcm.encrypt(SAMPLE_LAB), n=ITERATIONS))
    results.append(bench("AES-GCM-256 Decrypt (lab)",
        lambda: gcm.decrypt(gcm_ct), n=ITERATIONS))

    # KMS API latency (nếu server đang chạy)
    try:
        import httpx
        results.append(bench("KMS API GET /dek/gcm_dek",
            lambda: httpx.get("http://localhost:8001/dek/gcm_dek", timeout=1.0),
            n=100))
        results.append(bench("KMS API GET /pubkey/Noi",
            lambda: httpx.get("http://localhost:8001/pubkey/Noi", timeout=1.0),
            n=100))
    except Exception:
        print("  [KMS API] Server không chạy – bỏ qua KMS API benchmark")

    return results


def print_table(results: list[dict]):
    header = f"{'Operation':<40} {'Avg(µs)':>10} {'P95(µs)':>10} {'P99(µs)':>10} {'Throughput/s':>14}"
    print("=" * len(header))
    print(header)
    print("=" * len(header))
    for r in results:
        print(f"{r['operation']:<40} {r['avg_us']:>10.1f} {r['p95_us']:>10.1f} "
              f"{r['p99_us']:>10.1f} {r['throughput_per_sec']:>14.0f}")
    print("=" * len(header))


def main():
    print("=" * 60)
    print("  Enc2Health – Crypto & KMS Benchmark (Long T10)")
    print("=" * 60)

    results = run_benchmarks()
    print_table(results)

    # Lưu kết quả để Nam dùng trong Cost Model
    output_path = "benchmark/c_soft_metrics.json"
    with open(output_path, "w") as f:
        json.dump({
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "machine": "Ubuntu 24.04 / RTX 3060 host",
            "results": results
        }, f, indent=2)

    print(f"\n✅ Kết quả lưu tại: {output_path}")
    print("   Gửi file này cho Nam để hiệu chỉnh Cost Model (C_soft).")


if __name__ == "__main__":
    main()
