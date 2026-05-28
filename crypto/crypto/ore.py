"""
Order-Preserving Encryption (OPE) cho range queries.
Dùng Boldyreva OPE (pyope).
Hỗ trợ: tuoi (integer), ngay_nhap_vien (epoch integer).
"""
import os
from datetime import date
import base64
from pyope.ope import OPE, ValueRange


class ORECipher:
    """Wrapper OPE cho integer và date range queries."""

    # Domain cho tuổi: 0-150
    AGE_IN_RANGE  = ValueRange(0, 150)
    AGE_OUT_RANGE = ValueRange(0, 2**31 - 1)

    # Domain cho ngày (epoch days từ 1900-01-01): 0 đến ~50000
    DATE_IN_RANGE  = ValueRange(0, 50000)
    DATE_OUT_RANGE = ValueRange(0, 2**31 - 1)

    def __init__(self, key: bytes = None):
        if key is None:
            key = os.urandom(16)  # OPE dùng 16-byte key
        self._key = key
        self._age_cipher  = OPE(key, self.AGE_IN_RANGE,  self.AGE_OUT_RANGE)
        self._date_cipher = OPE(key, self.DATE_IN_RANGE, self.DATE_OUT_RANGE)

    @property
    def key(self) -> bytes:
        return self._key

    def encrypt_age(self, age: int) -> int:
        """Mã hóa tuổi → integer lưu MongoDB (vẫn so sánh được)."""
        return self._age_cipher.encrypt(age)

    def decrypt_age(self, encrypted_age: int) -> int:
        return self._age_cipher.decrypt(encrypted_age)

    def _date_to_int(self, d: date) -> int:
        """Chuyển date → số ngày từ 1900-01-01."""
        origin = date(1900, 1, 1)
        return (d - origin).days

    def _int_to_date(self, n: int) -> date:
        from datetime import timedelta
        origin = date(1900, 1, 1)
        return origin + timedelta(days=n)

    def encrypt_date(self, d: date) -> int:
        """Mã hóa date → integer (có thể range query)."""
        return self._date_cipher.encrypt(self._date_to_int(d))

    def decrypt_date(self, encrypted_date: int) -> date:
        return self._int_to_date(self._date_cipher.decrypt(encrypted_date))

    def save_key(self, path: str):
        with open(path, "w") as f:
            f.write(base64.b64encode(self._key).decode())

    @classmethod
    def load_key(cls, path: str) -> "ORECipher":
        with open(path) as f:
            key = base64.b64decode(f.read().strip())
        return cls(key)


# Global instance (trong production: key từ Vault)
_ore_cipher: ORECipher = None

def get_ore_cipher() -> ORECipher:
    global _ore_cipher
    if _ore_cipher is None:
        _ore_cipher = ORECipher()
    return _ore_cipher


if __name__ == "__main__":
    print("=== Test OPE (ORE) ===")
    cipher = ORECipher()

    # Test tuổi
    ages = [25, 45, 60, 61, 75, 90]
    encrypted = [cipher.encrypt_age(a) for a in ages]
    print(f"  Ages:      {ages}")
    print(f"  Encrypted: {encrypted}")

    # Verify thứ tự được bảo toàn
    for i in range(len(ages)-1):
        assert ages[i] < ages[i+1]
        assert encrypted[i] < encrypted[i+1], "Thứ tự phải được bảo toàn!"
    print("  Thứ tự bảo toàn: OK")

    # Test range query: tuoi > 60
    threshold_enc = cipher.encrypt_age(60)
    matches = [ages[i] for i, e in enumerate(encrypted) if e > threshold_enc]
    assert matches == [61, 75, 90]
    print(f"  Range query (tuoi > 60): {matches} ✓")

    # Test decrypt
    for i, age in enumerate(ages):
        assert cipher.decrypt_age(encrypted[i]) == age
    print("  Decrypt: OK")

    # Test date
    print("\n=== Test date encryption ===")
    dates = [date(1950, 1, 1), date(1960, 6, 15), date(1980, 12, 31)]
    enc_dates = [cipher.encrypt_date(d) for d in dates]
    print(f"  Dates: {dates}")
    print(f"  Enc:   {enc_dates}")
    for i in range(len(dates)-1):
        assert enc_dates[i] < enc_dates[i+1]
    print("  Date order preserved: OK")
