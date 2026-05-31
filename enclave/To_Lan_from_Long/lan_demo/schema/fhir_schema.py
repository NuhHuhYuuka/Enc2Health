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
