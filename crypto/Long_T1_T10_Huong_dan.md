# Hướng dẫn thực hiện T1–T10 – Nguyễn Hoàng Long
**Kịch bản:** Enc²Health | **OS:** Ubuntu 24.04 LTS | **Vai trò:** Tầng Mã hóa & KMS

---

## Mục lục
1. [Cài đặt môi trường tổng thể](#0-cài-đặt-môi-trường-tổng-thể)
2. [T1 – Thiết kế schema HL7 FHIR & phân loại cột](#t1--thiết-kế-schema-hl7-fhir--phân-loại-cột-nhạy-cảm)
3. [T2 – Mã hóa bất đối xứng phía Client (RSA-4096 / ECC P-384)](#t2--mã-hóa-bất-đối-xứng-phía-client-rsa-4096--ecc-p-384)
4. [T3 – DTE (AES-SIV) cho trường lâm sàng](#t3--dte-aes-siv-cho-trường-lâm-sàng)
5. [T4 – ORE cho truy vấn khoảng (tuổi, ngày)](#t4--ore-cho-truy-vấn-khoảng-tuổi-ngày)
6. [T5 – Tạo 10.000 hồ sơ EHR & MongoDB FLE](#t5--tạo-10000-hồ-sơ-ehr--mongodb-fle)
7. [T6 – HashiCorp Vault – Keypairs per-Khoa](#t6--hashicorp-vault--keypairs-per-khoa)
8. [T7 – Key Rotation tự động](#t7--key-rotation-tự-động)
9. [T8 – Vault Audit Log cho Private Key retrieval](#t8--vault-audit-log-cho-private-key-retrieval)
10. [T9 – KMS API Wrapper cho Nam](#t9--kms-api-wrapper-cho-nam)
11. [T10 – Đo lường KMS latency & tốc độ mã hóa](#t10--đo-lường-kms-latency--tốc-độ-mã-hóa)

---

## 0. Cài đặt môi trường tổng thể

> Làm **một lần duy nhất** trước khi bắt đầu bất kỳ task nào.

### 0.1 Cập nhật hệ thống

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl wget gnupg software-properties-common \
    build-essential python3-pip python3-venv python3-dev libssl-dev
```

### 0.2 Tạo Python virtual environment

```bash
mkdir -p ~/enc2health && cd ~/enc2health
python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install \
    cryptography \
    pymongo \
    "pymongo[encryption]" \
    faker \
    pyope \
    hvac \
    fastapi \
    uvicorn \
    prometheus-client \
    httpx \
    python-dotenv \
    pydantic
```

> **Lưu ý:** Mỗi lần mở terminal mới, chạy `source ~/enc2health/.venv/bin/activate` trước.

### 0.3 Cài đặt MongoDB 8.0

```bash
# Import GPG key
curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc \
  | sudo gpg --dearmor -o /usr/share/keyrings/mongodb-server-8.0.gpg

# Thêm repo Ubuntu 24.04 (Noble)
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] \
  https://repo.mongodb.org/apt/ubuntu noble/mongodb-org/8.0 multiverse" \
  | sudo tee /etc/apt/sources.list.d/mongodb-org-8.0.list

sudo apt update
sudo apt install -y mongodb-org

# Khởi động & bật auto-start
sudo systemctl enable --now mongod
sudo systemctl status mongod
```

Kiểm tra:
```bash
mongosh --eval "db.runCommand({ ping: 1 })"
# Expected: { ok: 1 }
```

### 0.4 Cài đặt HashiCorp Vault

```bash
wget -O- https://apt.releases.hashicorp.com/gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg

echo "deb [arch=$(dpkg --print-architecture) \
  signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] \
  https://apt.releases.hashicorp.com $(grep -oP '(?<=UBUNTU_CODENAME=).*' /etc/os-release || lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/hashicorp.list

sudo apt update && sudo apt install -y vault
vault version
# Expected: Vault v1.21.x (hoặc mới hơn)
```

### 0.5 Cấu trúc thư mục project

```
~/enc2health/
├── .venv/
├── .env                    # Chứa VAULT_TOKEN, MONGO_URI (không commit git)
├── schema/
│   └── fhir_schema.py      # T1
├── crypto/
│   ├── asym.py             # T2
│   ├── dte.py              # T3
│   └── ore.py              # T4
├── data/
│   └── generate_ehr.py     # T5
├── vault/
│   ├── setup_vault.sh      # T6
│   └── vault_client.py     # T6, T8
├── kms_api/
│   └── main.py             # T9
└── benchmark/
    └── measure.py          # T10
```

---

## T1 – Thiết kế schema HL7 FHIR & phân loại cột nhạy cảm

**Deadline:** Cuối tuần 1 – giao tài liệu schema cho cả nhóm.

### Mục tiêu
Định nghĩa rõ từng trường trong MongoDB document và kiểu mã hóa tương ứng.

### Bảng phân loại trường

| Trường | Loại dữ liệu | Kiểu mã hóa | Mục đích |
|--------|-------------|-------------|---------|
| `patient_id` | UUID | Plaintext (index) | Khóa chính |
| `ho_ten` | String | **RSA/ECC (Asym)** | PII – tên bệnh nhân |
| `cmnd` | String | **RSA/ECC (Asym)** | PII – số CMND/CCCD |
| `dia_chi` | String | **RSA/ECC (Asym)** | PII – địa chỉ |
| `ngay_sinh` | Date | **ORE** | Range query (tuổi) |
| `tuoi` | Integer | **ORE** | Range query `WHERE tuoi > 60` |
| `ma_benh` | String (ICD-10) | **DTE (AES-SIV)** | Equality: `WHERE ma_benh = 'E11'` |
| `khoa_phong` | String | **DTE (AES-SIV)** | Equality: GROUP BY khoa |
| `chan_doan` | String | **AES-GCM** | Decrypt trong Enclave |
| `ket_qua_xn` | Object | **AES-GCM** | Xét nghiệm – Enclave only |
| `vien_phi` | Decimal | **AES-GCM** | Billing – AVG trong Enclave |
| `ngay_nhap_vien` | Date | **ORE** | Range query |

### File schema

```bash
mkdir -p ~/enc2health/schema
nano ~/enc2health/schema/fhir_schema.py
```

```python
# ~/enc2health/schema/fhir_schema.py
"""
Schema EHR theo HL7 FHIR R4 (simplified) cho Enc2Health.
Tham chiếu: https://www.hl7.org/fhir/patient.html
"""
from enum import Enum

class EncryptionType(str, Enum):
    ASYMMETRIC = "RSA4096_ECC_P384"   # PII – client-side, private key in Enclave
    DTE_AES_SIV = "DTE_AES_SIV"       # Clinical – equality search on ciphertext
    ORE = "OPE_BOLDYREVA"              # Range – order-preserving encryption
    AES_GCM = "AES_GCM_256"           # Lab/Billing – decrypt inside Enclave
    PLAINTEXT = "PLAINTEXT"            # Non-sensitive (IDs, timestamps)

# Định nghĩa từng trường
FIELD_SCHEMA = {
    "patient_id":       {"type": "UUID",    "enc": EncryptionType.PLAINTEXT},
    "ho_ten":           {"type": "String",  "enc": EncryptionType.ASYMMETRIC},
    "cmnd":             {"type": "String",  "enc": EncryptionType.ASYMMETRIC},
    "dia_chi":          {"type": "String",  "enc": EncryptionType.ASYMMETRIC},
    "tuoi":             {"type": "Integer", "enc": EncryptionType.ORE},
    "ngay_sinh":        {"type": "Date",    "enc": EncryptionType.ORE},
    "ngay_nhap_vien":   {"type": "Date",    "enc": EncryptionType.ORE},
    "ma_benh":          {"type": "String",  "enc": EncryptionType.DTE_AES_SIV},
    "khoa_phong":       {"type": "String",  "enc": EncryptionType.DTE_AES_SIV},
    "chan_doan":         {"type": "String",  "enc": EncryptionType.AES_GCM},
    "ket_qua_xn":       {"type": "Object",  "enc": EncryptionType.AES_GCM},
    "vien_phi":         {"type": "Decimal", "enc": EncryptionType.AES_GCM},
}

# Nhóm theo kiểu mã hóa (dùng để sinh code tự động)
BY_ENC_TYPE = {}
for field, meta in FIELD_SCHEMA.items():
    enc = meta["enc"]
    BY_ENC_TYPE.setdefault(enc, []).append(field)

if __name__ == "__main__":
    for enc_type, fields in BY_ENC_TYPE.items():
        print(f"{enc_type.value}: {fields}")
```

Chạy kiểm tra:
```bash
cd ~/enc2health
source .venv/bin/activate
python schema/fhir_schema.py
```

---

## T2 – Mã hóa bất đối xứng phía Client (RSA-4096 / ECC P-384)

**Deadline:** Cuối tuần 2.  
**Phụ thuộc:** Lan cần kết quả này để T2 (lưu private key vào Enclave).

### Mục tiêu
- Sinh cặp khóa RSA-4096 hoặc ECC P-384 **cho từng Khoa/Phòng**.
- Public key: mã hóa PII tại client trước khi gửi lên MongoDB.
- Private key: lưu vào Vault, Enclave lấy qua RA-TLS.

### Lý do dùng cả 2 thuật toán
- **RSA-4096:** Dễ tích hợp với thư viện cũ, encrypt trực tiếp payload nhỏ (tên, CMND).
- **ECC P-384 (ECIES):** Nhỏ hơn, nhanh hơn; phù hợp cho mobile HIS app.

Trong capstone này, dùng **ECC P-384 + ECIES** (ECDH + AES-GCM) vì hiệu năng tốt hơn. RSA-4096 implement thêm để so sánh benchmark.

```bash
mkdir -p ~/enc2health/crypto
nano ~/enc2health/crypto/asym.py
```

```python
# ~/enc2health/crypto/asym.py
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
```

Chạy kiểm tra:
```bash
python crypto/asym.py
```

---

## T3 – DTE (AES-SIV) cho trường lâm sàng

### Mục tiêu
Mã hóa tất định (Deterministic Encryption) cho `ma_benh` và `khoa_phong` để MongoDB có thể equality search trực tiếp trên bản mã.

**Tại sao AES-SIV?** Không dùng nonce ngẫu nhiên → cùng plaintext → cùng ciphertext → hỗ trợ `WHERE ma_benh = encrypt('E11')`.

```bash
nano ~/enc2health/crypto/dte.py
```

```python
# ~/enc2health/crypto/dte.py
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
    """Mã hóa mã bệnh ICD-10 (ví dụ: 'E11')."""
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
    ct1 = cipher.encrypt("E11")
    ct2 = cipher.encrypt("E11")
    assert ct1 == ct2, "DTE phải cho cùng ciphertext!"
    print(f"  Deterministic: OK | 'E11' → {ct1[:20]}...")

    # Test decrypt
    assert cipher.decrypt(ct1) == "E11"
    print(f"  Decrypt: OK")

    # Test equality search simulation
    search_ct = cipher.encrypt("E11")
    # MongoDB query: { "ma_benh": search_ct } → trả về đúng hồ sơ
    assert search_ct == ct1
    print(f"  Equality search simulation: OK")

    # Test AD isolation: E11 với AD khác nhau → kết quả khác
    ct_khoa = cipher.encrypt("E11", b"field:khoa_phong")
    assert ct_khoa != ct1
    print(f"  Associated data isolation: OK")

    print("\n=== Test field-specific ciphers ===")
    enc_benh = encrypt_ma_benh("E11")
    enc_khoa = encrypt_khoa_phong("Noi")
    print(f"  ma_benh('E11'): {enc_benh[:20]}...")
    print(f"  khoa_phong('Noi'): {enc_khoa[:20]}...")
    assert decrypt_ma_benh(enc_benh) == "E11"
    assert decrypt_khoa_phong(enc_khoa) == "Noi"
    print("  All OK!")
```

Chạy:
```bash
python crypto/dte.py
```

---

## T4 – ORE cho truy vấn khoảng (tuổi, ngày)

### Mục tiêu
Mã hóa `tuoi` và `ngay_nhap_vien` sao cho MongoDB vẫn thực hiện được `WHERE tuoi > 60` trên bản mã.

**Phương pháp dùng:** Boldyreva OPE (Order-Preserving Encryption) qua thư viện `pyope`. Đây là OPE, không phải ORE đầy đủ nhưng đủ cho capstone và tương thích MongoDB range index.

> **Lưu ý học thuật:** OPE bị leakage về thứ tự (biết được thứ tự của bản mã). Trong báo cáo, ghi rõ điều này và đề xuất ORE thực sự (Lewi-Wu ORE) như hướng cải tiến.

```bash
nano ~/enc2health/crypto/ore.py
```

```python
# ~/enc2health/crypto/ore.py
"""
Order-Preserving Encryption (OPE) cho range queries.
Dùng Boldyreva OPE (pyope).
Hỗ trợ: tuoi (integer), ngay_nhap_vien (epoch integer).
"""
import os
import struct
import base64
from datetime import date, datetime
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
```

Chạy:
```bash
python crypto/ore.py
```

---

## T5 – Tạo 10.000 hồ sơ EHR & MongoDB FLE

**Deadline:** Cuối tuần 2 – giao cho Lan verify.

### 5.1 Chuẩn bị AES-GCM helper

```bash
nano ~/enc2health/crypto/gcm.py
```

```python
# ~/enc2health/crypto/gcm.py
import os, base64, json
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class AESGCMCipher:
    def __init__(self, key: bytes = None):
        self._key = key or os.urandom(32)  # 256-bit
        self._aes = AESGCM(self._key)

    @property
    def key(self) -> bytes:
        return self._key

    def encrypt(self, plaintext) -> str:
        if not isinstance(plaintext, bytes):
            plaintext = json.dumps(plaintext).encode()
        nonce = os.urandom(12)
        ct = self._aes.encrypt(nonce, plaintext, None)
        return base64.b64encode(nonce + ct).decode()

    def decrypt(self, ciphertext_b64: str):
        raw = base64.b64decode(ciphertext_b64)
        nonce, ct = raw[:12], raw[12:]
        return self._aes.decrypt(nonce, ct, None)

    def decrypt_json(self, ciphertext_b64: str):
        return json.loads(self.decrypt(ciphertext_b64))

    def decrypt_float(self, ciphertext_b64: str) -> float:
        return float(self.decrypt(ciphertext_b64))
```

### 5.2 Script sinh 10.000 hồ sơ

```bash
nano ~/enc2health/data/generate_ehr.py
```

```python
# ~/enc2health/data/generate_ehr.py
"""
Sinh 10.000 hồ sơ EHR giả lập và insert vào MongoDB với mã hóa.
Runtime: ~3-5 phút trên máy thường.
"""
import sys, os, random, json
from datetime import date, timedelta
import uuid
from faker import Faker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from crypto.asym import ecc_encrypt, generate_all_department_keypairs
from crypto.dte  import DTECipher
from crypto.ore  import ORECipher
from crypto.gcm  import AESGCMCipher
from pymongo import MongoClient

fake = Faker("vi_VN")
random.seed(42)

# ── Cấu hình ──────────────────────────────────────────────────────────────
MONGO_URI    = "mongodb://localhost:27017"
DB_NAME      = "enc2health"
COLLECTION   = "patient_records"
RECORD_COUNT = 10_000

# ICD-10 mã bệnh phổ biến
ICD10_CODES = {
    "E11": "Đái tháo đường type 2",
    "I10": "Tăng huyết áp nguyên phát",
    "J18": "Viêm phổi không rõ tác nhân",
    "K29": "Viêm dạ dày",
    "M54": "Đau lưng",
    "N18": "Bệnh thận mạn",
    "I25": "Bệnh tim thiếu máu cục bộ mạn",
    "F32": "Trầm cảm",
}

DEPARTMENTS = ["Noi", "Ngoai", "Cap_cuu", "Tim_mach", "Than_kinh", "Nhi"]

def random_date(start_year=2020, end_year=2024) -> date:
    start = date(start_year, 1, 1)
    end   = date(end_year, 12, 31)
    return start + timedelta(days=random.randint(0, (end - start).days))

def main():
    print("[1/5] Sinh keypairs cho tất cả Khoa (ECC P-384)...")
    dept_keypairs = generate_all_department_keypairs("ECC")
    # Lưu public keys ra file để dùng lại (private keys sẽ lưu Vault ở T6)
    os.makedirs("data/keys", exist_ok=True)
    for dept, kp in dept_keypairs.items():
        with open(f"data/keys/{dept}_public.pem", "w") as f:
            f.write(kp["public_pem"])
        # ⚠️ Private key: chỉ lưu tạm, T6 sẽ chuyển vào Vault
        with open(f"data/keys/{dept}_private.pem", "w") as f:
            f.write(kp["private_pem"])
    print("  Keypairs saved to data/keys/")

    print("[2/5] Khởi tạo các cipher...")
    # DTE: mỗi field 1 key riêng
    dte_ma_benh   = DTECipher()
    dte_khoa      = DTECipher()
    # ORE: 1 key cho tất cả integer/date fields
    ore = ORECipher()
    # AES-GCM: DEK cho lab results & billing
    gcm = AESGCMCipher()

    # Lưu keys tạm (T6 sẽ wrap vào Vault Envelope Encryption)
    dte_ma_benh.save_key("data/keys/dte_ma_benh.key")
    dte_khoa.save_key("data/keys/dte_khoa.key")
    ore.save_key("data/keys/ore.key")
    with open("data/keys/gcm_dek.key", "w") as f:
        import base64
        f.write(base64.b64encode(gcm.key).decode())
    print("  Cipher keys saved (tạm thời – T6 sẽ migrate vào Vault)")

    print("[3/5] Kết nối MongoDB...")
    client = MongoClient(MONGO_URI)
    db     = client[DB_NAME]
    col    = db[COLLECTION]
    col.drop()  # Reset nếu chạy lại

    print(f"[4/5] Sinh và insert {RECORD_COUNT:,} hồ sơ...")
    batch_size = 500
    batch = []

    for i in range(RECORD_COUNT):
        dept    = random.choice(DEPARTMENTS)
        pub_pem = dept_keypairs[dept]["public_pem"].encode()
        ma_benh = random.choice(list(ICD10_CODES.keys()))
        tuoi    = random.randint(1, 95)
        ngay_nhap = random_date()
        vien_phi  = round(random.uniform(500_000, 50_000_000), 0)  # VND
        ket_qua_xn = {
            "glucose": round(random.uniform(3.5, 15.0), 1),   # mmol/L
            "hba1c":   round(random.uniform(4.0, 12.0), 1),   # %
            "creatinine": round(random.uniform(50, 300), 0),  # umol/L
        }

        doc = {
            # Plaintext fields
            "patient_id": str(uuid.uuid4()),
            "khoa_phong_plaintext": dept,  # Chỉ để debug, xóa trong production

            # PII – Asymmetric ECC
            "ho_ten_enc":  ecc_encrypt(fake.name(), pub_pem),
            "cmnd_enc":    ecc_encrypt(fake.numerify("0##########"), pub_pem),
            "dia_chi_enc": ecc_encrypt(fake.address(), pub_pem),
            "dept":        dept,  # Để biết dùng keypair nào khi decrypt

            # Clinical – DTE AES-SIV
            "ma_benh_enc":    dte_ma_benh.encrypt(ma_benh, b"field:ma_benh"),
            "khoa_phong_enc": dte_khoa.encrypt(dept, b"field:khoa_phong"),

            # Range – OPE
            "tuoi_enc":          ore.encrypt_age(tuoi),
            "ngay_nhap_vien_enc": ore.encrypt_date(ngay_nhap),

            # Lab & Billing – AES-GCM
            "ket_qua_xn_enc": gcm.encrypt(ket_qua_xn),
            "vien_phi_enc":   gcm.encrypt(str(vien_phi)),

            # Metadata (plaintext ok)
            "created_at": ngay_nhap.isoformat(),
        }
        batch.append(doc)

        if len(batch) >= batch_size:
            col.insert_many(batch)
            batch = []
            print(f"  Inserted {min(i+1, RECORD_COUNT):,}/{RECORD_COUNT:,}", end="\r")

    if batch:
        col.insert_many(batch)

    print(f"\n  Done! Total: {col.count_documents({}):,} documents")

    print("[5/5] Tạo MongoDB indexes...")
    # Index trên DTE fields (equality search)
    col.create_index("ma_benh_enc")
    col.create_index("khoa_phong_enc")
    # Index trên ORE fields (range query)
    col.create_index("tuoi_enc")
    col.create_index("ngay_nhap_vien_enc")
    col.create_index("dept")
    print("  Indexes created: ma_benh_enc, khoa_phong_enc, tuoi_enc, ngay_nhap_vien_enc")

    print("\n✅ Dataset generation complete!")
    print(f"   DB: {DB_NAME} | Collection: {COLLECTION}")
    print(f"   Records: {RECORD_COUNT:,}")

    # Demo query để verify
    print("\n[Demo] Equality search: ma_benh = 'E11'")
    e11_ct = dte_ma_benh.encrypt("E11", b"field:ma_benh")
    count_e11 = col.count_documents({"ma_benh_enc": e11_ct})
    print(f"  Tìm thấy {count_e11} bệnh nhân Tiểu đường (E11)")

    print("\n[Demo] Range query: tuoi > 60")
    threshold = ore.encrypt_age(60)
    count_elderly = col.count_documents({"tuoi_enc": {"$gt": threshold}})
    print(f"  Tìm thấy {count_elderly} bệnh nhân > 60 tuổi")

    client.close()

if __name__ == "__main__":
    main()
```

Chạy:
```bash
cd ~/enc2health
python data/generate_ehr.py
```

---

## T6 – HashiCorp Vault – Keypairs per-Khoa

### 6.1 Khởi động Vault (Dev mode cho development)

```bash
# Terminal 1: chạy Vault dev server
vault server -dev -dev-root-token-id="enc2health-root-token" &

# Terminal 2: cấu hình env
export VAULT_ADDR='http://127.0.0.1:8200'
export VAULT_TOKEN='enc2health-root-token'
echo 'export VAULT_ADDR="http://127.0.0.1:8200"' >> ~/.bashrc
echo 'export VAULT_TOKEN="enc2health-root-token"' >> ~/.bashrc

vault status
```

> **Production:** Dùng `vault server -config=/etc/vault.d/vault.hcl` với Raft storage + TLS.  
> Dev mode dùng in-memory storage, reset khi restart – đủ cho capstone.

### 6.2 Script setup Vault

```bash
mkdir -p ~/enc2health/vault
nano ~/enc2health/vault/setup_vault.sh
```

```bash
#!/bin/bash
# ~/enc2health/vault/setup_vault.sh
# Setup HashiCorp Vault cho Enc2Health

set -e
export VAULT_ADDR='http://127.0.0.1:8200'
export VAULT_TOKEN='enc2health-root-token'

echo "=== [1/4] Enable KV-v2 secrets engine ==="
vault secrets enable -path=enc2health kv-v2 2>/dev/null || echo "  Already enabled"

echo "=== [2/4] Upload keypairs cho từng Khoa ==="
DEPARTMENTS=("Noi" "Ngoai" "Cap_cuu" "Tim_mach" "Than_kinh" "Nhi")
for dept in "${DEPARTMENTS[@]}"; do
    PRIV_FILE="data/keys/${dept}_private.pem"
    PUB_FILE="data/keys/${dept}_public.pem"
    if [ -f "$PRIV_FILE" ] && [ -f "$PUB_FILE" ]; then
        vault kv put "enc2health/keypairs/${dept}" \
            private_key=@"$PRIV_FILE" \
            public_key=@"$PUB_FILE" \
            algorithm="ECC_P384" \
            department="$dept"
        echo "  ✓ Uploaded keypair for: $dept"
    else
        echo "  ⚠ Missing key files for: $dept (chạy generate_ehr.py trước)"
    fi
done

echo "=== [3/4] Upload DEK (AES-GCM) và DTE keys ==="
vault kv put enc2health/dek/gcm_dek \
    key=@"data/keys/gcm_dek.key" \
    algorithm="AES-GCM-256" \
    purpose="lab_and_billing"

vault kv put enc2health/dek/dte_ma_benh \
    key=@"data/keys/dte_ma_benh.key" \
    algorithm="AES-SIV-256" \
    purpose="icd10_equality_search"

vault kv put enc2health/dek/dte_khoa \
    key=@"data/keys/dte_khoa.key" \
    algorithm="AES-SIV-256" \
    purpose="department_equality_search"

vault kv put enc2health/dek/ore_key \
    key=@"data/keys/ore.key" \
    algorithm="OPE-Boldyreva" \
    purpose="age_date_range_query"

echo "=== [4/4] Tạo Vault policy cho Enclave (Lan) ==="
cat <<'POLICY' | vault policy write enclave-policy -
path "enc2health/keypairs/*" {
  capabilities = ["read"]
}
path "enc2health/dek/*" {
  capabilities = ["read"]
}
POLICY

cat <<'POLICY' | vault policy write kms-api-policy -
path "enc2health/dek/*" {
  capabilities = ["read"]
}
path "enc2health/keypairs/*/public_key" {
  capabilities = ["read"]
}
POLICY

echo ""
echo "✅ Vault setup complete!"
echo "   List secrets: vault kv list enc2health/keypairs/"
echo "   Get keypair:  vault kv get enc2health/keypairs/Noi"
```

```bash
chmod +x vault/setup_vault.sh
cd ~/enc2health
bash vault/setup_vault.sh
```

Verify:
```bash
vault kv list enc2health/keypairs/
vault kv get enc2health/keypairs/Noi
vault kv get enc2health/dek/gcm_dek
```

### 6.3 Vault Python Client

```bash
nano ~/enc2health/vault/vault_client.py
```

```python
# ~/enc2health/vault/vault_client.py
"""
Vault client cho Enc2Health.
Cung cấp: get_department_public_key, get_dek, get_private_key (Enclave only)
"""
import os
import hvac
import base64
from functools import lru_cache

VAULT_ADDR  = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
VAULT_TOKEN = os.getenv("VAULT_TOKEN", "enc2health-root-token")

def _client() -> hvac.Client:
    c = hvac.Client(url=VAULT_ADDR, token=VAULT_TOKEN)
    assert c.is_authenticated(), "Vault authentication failed!"
    return c


@lru_cache(maxsize=32)
def get_department_public_key(dept: str) -> bytes:
    """Lấy public key của Khoa. Cache vì không thay đổi thường xuyên."""
    c = _client()
    secret = c.secrets.kv.v2.read_secret_version(
        path=f"keypairs/{dept}",
        mount_point="enc2health"
    )
    return secret["data"]["data"]["public_key"].encode()


def get_department_private_key(dept: str) -> bytes:
    """
    Lấy private key. CHỈ gọi trong Enclave context.
    Trong production: Enclave phải đã xác thực qua RA-TLS trước khi gọi.
    """
    c = _client()
    secret = c.secrets.kv.v2.read_secret_version(
        path=f"keypairs/{dept}",
        mount_point="enc2health"
    )
    return secret["data"]["data"]["private_key"].encode()


def get_dek(key_name: str) -> bytes:
    """Lấy DEK từ Vault. key_name: 'gcm_dek', 'dte_ma_benh', 'ore_key', ..."""
    c = _client()
    secret = c.secrets.kv.v2.read_secret_version(
        path=f"dek/{key_name}",
        mount_point="enc2health"
    )
    return base64.b64decode(secret["data"]["data"]["key"])


def list_departments() -> list[str]:
    c = _client()
    result = c.secrets.kv.v2.list_secrets(
        path="keypairs",
        mount_point="enc2health"
    )
    return [k.rstrip("/") for k in result["data"]["keys"]]


if __name__ == "__main__":
    print("=== Vault Client Test ===")
    depts = list_departments()
    print(f"  Departments in Vault: {depts}")

    pub = get_department_public_key("Noi")
    print(f"  Public key (Noi): {pub[:50]}...")

    gcm_key = get_dek("gcm_dek")
    print(f"  GCM DEK length: {len(gcm_key)} bytes ✓")
    print("All OK!")
```

```bash
python vault/vault_client.py
```

---

## T7 – Key Rotation tự động

### Mục tiêu
Tự động xoay vòng DEK (AES-GCM) định kỳ mà không downtime. Keypair ECC xoay theo yêu cầu thủ công.

```bash
nano ~/enc2health/vault/key_rotation.py
```

```python
# ~/enc2health/vault/key_rotation.py
"""
Key Rotation cho Enc2Health.
- DEK (AES-GCM): xoay tự động theo schedule.
- Keypair ECC: xoay thủ công khi có yêu cầu.
"""
import os, base64, json, logging
from datetime import datetime, timezone
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from vault_client import _client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("key_rotation")


def rotate_gcm_dek() -> dict:
    """
    Xoay vòng DEK AES-GCM.
    Vault KV-v2 tự động versioning – không cần xóa version cũ ngay.
    """
    c = _client()
    new_key = os.urandom(32)
    new_key_b64 = base64.b64encode(new_key).decode()

    c.secrets.kv.v2.create_or_update_secret(
        path="dek/gcm_dek",
        secret={
            "key": new_key_b64,
            "algorithm": "AES-GCM-256",
            "purpose": "lab_and_billing",
            "rotated_at": datetime.now(timezone.utc).isoformat()
        },
        mount_point="enc2health"
    )

    log.info("GCM DEK rotated successfully. New version created in Vault.")
    return {"status": "rotated", "key_preview": new_key_b64[:8] + "..."}


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
```

Test:
```bash
cd ~/enc2health
python vault/key_rotation.py --status
python vault/key_rotation.py --rotate-dek
python vault/key_rotation.py --status
```

Setup crontab (xoay DEK hàng tuần, 2:00 AM Chủ nhật):
```bash
(crontab -l 2>/dev/null; echo "0 2 * * 0 cd ~/enc2health && source .venv/bin/activate && python vault/key_rotation.py --rotate-dek >> /var/log/enc2health_rotation.log 2>&1") | crontab -
crontab -l
```

---

## T8 – Vault Audit Log cho Private Key retrieval

### Mục tiêu
Ghi lại mỗi lần Enclave (hoặc bất kỳ service nào) lấy Private Key từ Vault – hỗ trợ HIPAA audit trail của Nam.

### 8.1 Enable Vault Audit Device

```bash
# Enable file audit log
sudo mkdir -p /var/log/vault
sudo chown $USER /var/log/vault

vault audit enable file file_path=/var/log/vault/audit.log

# Verify
vault audit list
```

### 8.2 Audit log parser

```bash
nano ~/enc2health/vault/audit_logger.py
```

```python
# ~/enc2health/vault/audit_logger.py
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
```

---

## T9 – KMS API Wrapper cho Nam

**Deadline:** Cuối tuần 4 – Nam cần để kết nối pipeline.

### Mục tiêu
FastAPI service trả về DEK và Public Key cho Router của Nam. Latency target: `<5ms` cho DEK, `<10ms` cho asymmetric key lookup.

```bash
mkdir -p ~/enc2health/kms_api
nano ~/enc2health/kms_api/main.py
```

```python
# ~/enc2health/kms_api/main.py
"""
KMS API Wrapper – endpoint cho Nam's Query Router.
Chạy: uvicorn kms_api.main:app --port 8001 --reload
"""
import time
import logging
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from prometheus_client import Histogram, Counter, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vault.vault_client import (
    get_dek, get_department_public_key, list_departments
)

app = FastAPI(title="Enc2Health KMS API", version="1.0.0")
log = logging.getLogger("kms_api")

# ── Prometheus metrics ──────────────────────────────────────────────────────
REQUEST_LATENCY = Histogram(
    "kms_request_latency_seconds",
    "KMS API request latency",
    ["endpoint", "status"]
)
REQUEST_COUNT = Counter(
    "kms_request_total",
    "Total KMS requests",
    ["endpoint", "dept"]
)

# ── Models ──────────────────────────────────────────────────────────────────
class DEKResponse(BaseModel):
    key_name: str
    key_b64: str
    algorithm: str
    latency_ms: float

class PublicKeyResponse(BaseModel):
    dept: str
    public_key_pem: str
    algorithm: str
    latency_ms: float


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "enc2health-kms"}


@app.get("/dek/{key_name}", response_model=DEKResponse)
def get_data_encryption_key(
    key_name: str,
    x_caller: str = Header(default="unknown")
):
    """
    Lấy DEK cho Router.
    key_name: 'gcm_dek' | 'dte_ma_benh' | 'dte_khoa' | 'ore_key'
    """
    valid_keys = {"gcm_dek", "dte_ma_benh", "dte_khoa", "ore_key"}
    if key_name not in valid_keys:
        raise HTTPException(status_code=404, detail=f"Unknown key: {key_name}")

    t0 = time.perf_counter()
    try:
        import base64
        raw_key = get_dek(key_name)
        latency = (time.perf_counter() - t0) * 1000  # ms

        REQUEST_LATENCY.labels(endpoint="get_dek", status="ok").observe(latency / 1000)
        REQUEST_COUNT.labels(endpoint="get_dek", dept="N/A").inc()

        algo_map = {
            "gcm_dek": "AES-GCM-256",
            "dte_ma_benh": "AES-SIV-256",
            "dte_khoa": "AES-SIV-256",
            "ore_key": "OPE-Boldyreva",
        }

        return DEKResponse(
            key_name=key_name,
            key_b64=base64.b64encode(raw_key).decode(),
            algorithm=algo_map[key_name],
            latency_ms=round(latency, 3)
        )
    except Exception as e:
        REQUEST_LATENCY.labels(endpoint="get_dek", status="error").observe(
            (time.perf_counter() - t0) / 1000
        )
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/pubkey/{dept}", response_model=PublicKeyResponse)
def get_public_key(dept: str, x_caller: str = Header(default="unknown")):
    """
    Lấy Public Key của Khoa cho Router để verify encryption.
    Đây là public key – không nhạy cảm, có thể cache ở client.
    """
    t0 = time.perf_counter()
    try:
        pub_pem = get_department_public_key(dept)
        latency = (time.perf_counter() - t0) * 1000

        REQUEST_LATENCY.labels(endpoint="get_pubkey", status="ok").observe(latency / 1000)
        REQUEST_COUNT.labels(endpoint="get_pubkey", dept=dept).inc()

        return PublicKeyResponse(
            dept=dept,
            public_key_pem=pub_pem.decode(),
            algorithm="ECC-P384",
            latency_ms=round(latency, 3)
        )
    except Exception as e:
        REQUEST_LATENCY.labels(endpoint="get_pubkey", status="error").observe(
            (time.perf_counter() - t0) / 1000
        )
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/departments")
def list_all_departments():
    """Danh sách Khoa có keypair trong Vault."""
    return {"departments": list_departments()}


@app.get("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

Chạy KMS API:
```bash
cd ~/enc2health
uvicorn kms_api.main:app --host 0.0.0.0 --port 8001 --reload
```

Test (mở terminal khác):
```bash
# Health check
curl http://localhost:8001/health

# Lấy DEK
curl http://localhost:8001/dek/gcm_dek | python3 -m json.tool

# Lấy Public Key của Khoa Nội
curl http://localhost:8001/pubkey/Noi | python3 -m json.tool

# List departments
curl http://localhost:8001/departments

# Metrics
curl http://localhost:8001/metrics
```

Kiểm tra latency SLA:
```bash
# Chạy 100 requests, kiểm tra latency trung bình < 5ms
python3 -c "
import httpx, time, statistics

latencies = []
for _ in range(100):
    t0 = time.perf_counter()
    r = httpx.get('http://localhost:8001/dek/gcm_dek')
    latencies.append((time.perf_counter() - t0)*1000)

print(f'avg: {statistics.mean(latencies):.2f}ms')
print(f'p95: {sorted(latencies)[94]:.2f}ms')
print(f'max: {max(latencies):.2f}ms')
print('SLA <5ms:', statistics.mean(latencies) < 5)
"
```

---

## T10 – Đo lường KMS latency & tốc độ mã hóa

### Mục tiêu
Đo `C_soft` để Nam dùng trong Cost Model. Output: số liệu latency và throughput cho từng thao tác crypto.

```bash
mkdir -p ~/enc2health/benchmark
nano ~/enc2health/benchmark/measure.py
```

```python
# ~/enc2health/benchmark/measure.py
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
```

Chạy benchmark:
```bash
cd ~/enc2health
# Đảm bảo KMS API đang chạy (terminal khác):
# uvicorn kms_api.main:app --port 8001

python benchmark/measure.py
cat benchmark/c_soft_metrics.json
```

---

## Tóm tắt các file output cần giao cho nhóm

| File | Giao cho | Deadline | Mô tả |
|------|----------|----------|-------|
| `schema/fhir_schema.py` | Cả nhóm | Tuần 1 | Schema & phân loại trường |
| `data/keys/{dept}_public.pem` | Lan, Nam | Tuần 2 | Public keys per-Khoa |
| `data/keys/{dept}_private.pem` | Lan (qua Vault) | Tuần 2 | Private keys → Vault |
| `benchmark/c_soft_metrics.json` | Nam | Tuần 8 | C_soft cho Cost Model |
| KMS API `localhost:8001` | Nam | Tuần 4 | DEK + Public Key endpoint |

## Checklist hoàn thành

- [ ] T1: `python schema/fhir_schema.py` chạy không lỗi
- [ ] T2: `python crypto/asym.py` – ECC + RSA test pass
- [ ] T3: `python crypto/dte.py` – deterministic OK
- [ ] T4: `python crypto/ore.py` – range query simulation OK
- [ ] T5: `python data/generate_ehr.py` – 10.000 records trong MongoDB
- [ ] T6: `bash vault/setup_vault.sh` – all keypairs uploaded
- [ ] T7: crontab -l hiển thị rotation job
- [ ] T8: vault audit enable chạy thành công
- [ ] T9: `curl localhost:8001/dek/gcm_dek` trả về latency < 5ms
- [ ] T10: `python benchmark/measure.py` tạo `c_soft_metrics.json`
