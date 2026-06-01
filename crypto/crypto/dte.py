"""
DTE (Deterministic Encryption) dùng AES-SIV.
Hỗ trợ equality search trên MongoDB ciphertext.
"""
import os
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESSIV


class DTECipher:
    """AES-SIV deterministic encryption cho equality-searchable fields."""

    KEY_SIZE = 64  # AES-SIV dùng double-length key: 64 bytes cho AES-256-SIV

    def __init__(self, key: bytes = None):
        if key is None:
            key = os.urandom(self.KEY_SIZE)
        if len(key) != self.KEY_SIZE:
            raise ValueError(f"AES-SIV key phải là {self.KEY_SIZE} bytes")
        self._key = key
        self._cipher = AESSIV(key)

    @property
    def key(self) -> bytes:
        return self._key

    def encrypt(self, plaintext: str, associated_data: bytes = None) -> str:
        """
        Mã hóa tất định. Cùng plaintext + cùng AD → cùng ciphertext.
        Trả về base64 string để lưu MongoDB.
        """
        data = plaintext.encode("utf-8")
        ad = [associated_data] if associated_data else []
        ct = self._cipher.encrypt(data, ad)
        return base64.b64encode(ct).decode()

    def decrypt(self, ciphertext_b64: str, associated_data: bytes = None) -> str:
        """Giải mã AES-SIV."""
        ct = base64.b64decode(ciphertext_b64)
        ad = [associated_data] if associated_data else []
        return self._cipher.decrypt(ct, ad).decode("utf-8")

    def save_key(self, path: str):
        """Lưu key ra file (base64). Trong production: lưu vào Vault."""
        with open(path, "w") as f:
            f.write(base64.b64encode(self._key).decode())

    @classmethod
    def load_key(cls, path: str) -> "DTECipher":
        with open(path) as f:
            key = base64.b64decode(f.read().strip())
        return cls(key)


# Singleton keys cho từng field type (trong production: lấy từ Vault)
_dte_keys: dict[str, DTECipher] = {}

def get_dte_cipher(field_name: str) -> DTECipher:
    """
    Lấy DTE cipher cho field cụ thể.
    Mỗi field dùng 1 key riêng để tránh cross-field correlation.
    """
    if field_name not in _dte_keys:
        _dte_keys[field_name] = DTECipher()
    return _dte_keys[field_name]


def encrypt_ma_benh(ma_benh: str) -> str:
    """Mã hóa mã bệnh demo (ví dụ: 'I01')."""
    return get_dte_cipher("ma_benh").encrypt(ma_benh, b"field:ma_benh")

def decrypt_ma_benh(ct: str) -> str:
    return get_dte_cipher("ma_benh").decrypt(ct, b"field:ma_benh")

def encrypt_khoa_phong(khoa: str) -> str:
    """Mã hóa tên khoa/phòng."""
    return get_dte_cipher("khoa_phong").encrypt(khoa, b"field:khoa_phong")

def decrypt_khoa_phong(ct: str) -> str:
    return get_dte_cipher("khoa_phong").decrypt(ct, b"field:khoa_phong")


if __name__ == "__main__":
    print("=== Test AES-SIV DTE ===")
    cipher = DTECipher()

    # Test tính tất định
    ct1 = cipher.encrypt("I01")
    ct2 = cipher.encrypt("I01")
    assert ct1 == ct2, "DTE phải cho cùng ciphertext!"
    print(f"  Deterministic: OK | 'I01' → {ct1[:20]}...")

    # Test decrypt
    assert cipher.decrypt(ct1) == "I01"
    print(f"  Decrypt: OK")

    # Test equality search simulation
    search_ct = cipher.encrypt("I01")
    # MongoDB query: { "ma_benh": search_ct } → trả về đúng hồ sơ
    assert search_ct == ct1
    print(f"  Equality search simulation: OK")

    # Test AD isolation: I01 với AD khác nhau → kết quả khác
    ct_khoa = cipher.encrypt("I01", b"field:khoa_phong")
    assert ct_khoa != ct1
    print(f"  Associated data isolation: OK")

    print("\n=== Test field-specific ciphers ===")
    enc_benh = encrypt_ma_benh("I01")
    enc_khoa = encrypt_khoa_phong("Noi")
    print(f"  ma_benh('I01'): {enc_benh[:20]}...")
    print(f"  khoa_phong('Noi'): {enc_khoa[:20]}...")
    assert decrypt_ma_benh(enc_benh) == "I01"
    assert decrypt_khoa_phong(enc_khoa) == "Noi"
    print("  All OK!")
