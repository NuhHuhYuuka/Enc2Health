"""
Mã hóa bất đối xứng cho PII bệnh nhân.
- ECC P-384 (ECIES): ECDH key exchange + AES-256-GCM
- RSA-4096: OAEP + SHA-256 (cho so sánh benchmark)
"""
import os
import json
import base64
from cryptography.hazmat.primitives.asymmetric import ec, rsa, padding
from cryptography.hazmat.primitives.asymmetric.ec import (
    EllipticCurvePublicKey, EllipticCurvePrivateKey, ECDH
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ─────────────────────────────────────────
# ECC P-384 ECIES
# ─────────────────────────────────────────

def generate_ecc_keypair() -> tuple[bytes, bytes]:
    """Sinh cặp khóa ECC P-384. Trả về (private_pem, public_pem)."""
    private_key = ec.generate_private_key(ec.SECP384R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


def _ecc_derive_key(private_key: EllipticCurvePrivateKey,
                     peer_public_key: EllipticCurvePublicKey,
                     salt: bytes) -> bytes:
    """ECDH → HKDF → 32-byte AES key."""
    shared_secret = private_key.exchange(ECDH(), peer_public_key)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"enc2health-ecies-v1"
    ).derive(shared_secret)


def ecc_encrypt(plaintext: str, recipient_public_pem: bytes) -> str:
    """
    ECIES encrypt: sinh ephemeral key → ECDH → HKDF → AES-256-GCM.
    Trả về JSON base64 string (ephemeral_pub + nonce + ciphertext).
    """
    recipient_pub = serialization.load_pem_public_key(recipient_public_pem)

    # Ephemeral key pair
    ephemeral_priv = ec.generate_private_key(ec.SECP384R1())
    ephemeral_pub = ephemeral_priv.public_key()
    ephemeral_pub_bytes = ephemeral_pub.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo
    )

    salt = os.urandom(16)
    aes_key = _ecc_derive_key(ephemeral_priv, recipient_pub, salt)

    nonce = os.urandom(12)
    aesgcm = AESGCM(aes_key)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)

    payload = {
        "alg": "ECIES-P384-AES256GCM",
        "epk": base64.b64encode(ephemeral_pub_bytes).decode(),
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ct": base64.b64encode(ct).decode()
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def ecc_decrypt(encrypted_b64: str, recipient_private_pem: bytes) -> str:
    """Giải mã ECIES. Chỉ chạy trong Enclave."""
    payload = json.loads(base64.b64decode(encrypted_b64))
    assert payload["alg"] == "ECIES-P384-AES256GCM"

    recipient_priv = serialization.load_pem_private_key(recipient_private_pem, password=None)
    epk = serialization.load_pem_public_key(base64.b64decode(payload["epk"]))
    salt = base64.b64decode(payload["salt"])
    nonce = base64.b64decode(payload["nonce"])
    ct = base64.b64decode(payload["ct"])

    aes_key = _ecc_derive_key(recipient_priv, epk, salt)
    aesgcm = AESGCM(aes_key)
    return aesgcm.decrypt(nonce, ct, None).decode()


# ─────────────────────────────────────────
# RSA-4096 (cho benchmark so sánh)
# ─────────────────────────────────────────

def generate_rsa_keypair() -> tuple[bytes, bytes]:
    """Sinh cặp khóa RSA-4096. Trả về (private_pem, public_pem)."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


def rsa_encrypt(plaintext: str, public_pem: bytes) -> str:
    """RSA-4096 OAEP encrypt. Trả về base64 ciphertext."""
    public_key = serialization.load_pem_public_key(public_pem)
    ct = public_key.encrypt(
        plaintext.encode(),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    return base64.b64encode(ct).decode()


def rsa_decrypt(encrypted_b64: str, private_pem: bytes) -> str:
    """RSA-4096 OAEP decrypt. Chỉ chạy trong Enclave."""
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    ct = base64.b64decode(encrypted_b64)
    return private_key.decrypt(
        ct,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    ).decode()


# ─────────────────────────────────────────
# Helper: sinh keypair cho từng Khoa
# ─────────────────────────────────────────

DEPARTMENTS = ["Noi", "Ngoai", "Cap_cuu", "Tim_mach", "Than_kinh", "Nhi"]

def generate_all_department_keypairs(algo: str = "ECC") -> dict:
    """
    Sinh keypair cho tất cả Khoa.
    Returns: {dept: {"private_pem": ..., "public_pem": ...}}
    """
    result = {}
    for dept in DEPARTMENTS:
        if algo == "ECC":
            priv, pub = generate_ecc_keypair()
        else:
            priv, pub = generate_rsa_keypair()
        result[dept] = {
            "private_pem": priv.decode(),
            "public_pem": pub.decode()
        }
        print(f"  [OK] Sinh keypair {algo} cho Khoa: {dept}")
    return result


if __name__ == "__main__":
    print("=== Test ECC P-384 ECIES ===")
    priv_pem, pub_pem = generate_ecc_keypair()
    plaintext = "Nguyen Van A - 034099001234"
    ct = ecc_encrypt(plaintext, pub_pem)
    pt = ecc_decrypt(ct, priv_pem)
    assert pt == plaintext
    print(f"  ECC encrypt/decrypt OK | ciphertext length: {len(ct)} bytes")

    print("\n=== Test RSA-4096 ===")
    priv_pem, pub_pem = generate_rsa_keypair()
    ct = rsa_encrypt(plaintext, pub_pem)
    pt = rsa_decrypt(ct, priv_pem)
    assert pt == plaintext
    print(f"  RSA encrypt/decrypt OK | ciphertext length: {len(ct)} bytes")

    print("\n=== Sinh keypairs cho tất cả Khoa ===")
    keypairs = generate_all_department_keypairs("ECC")
    print(f"  Tổng số Khoa: {len(keypairs)}")
