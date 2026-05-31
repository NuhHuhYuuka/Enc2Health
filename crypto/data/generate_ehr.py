"""
Sinh 10.000 hồ sơ EHR giả lập và insert vào MongoDB với mã hóa.
Runtime: ~3-5 phút trên máy thường.
"""
import sys, os, random, json
from datetime import date, timedelta
import uuid
from faker import Faker
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from crypto.asym import ecc_encrypt
from crypto.dte  import DTECipher
from crypto.ore  import ORECipher
from crypto.gcm  import AESGCMCipher
from pymongo import MongoClient

fake = Faker("vi_VN")
random.seed(42)

# ── Cấu hình ──────────────────────────────────────────────────────────────
MONGO_URI    = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME      = os.getenv("MONGO_DB", "enc2health")
COLLECTION   = os.getenv("MONGO_COLLECTION", "patient_records")
RECORD_COUNT = int(os.getenv("EHR_RECORD_COUNT", "10000"))
BATCH_SIZE   = int(os.getenv("EHR_BATCH_SIZE", "500"))
FORCE_RECREATE = os.getenv("EHR_FORCE_RECREATE", "0") == "1"

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


def birth_date_from_age(admission_date: date, age: int) -> date:
    """Sinh ngày sinh xấp xỉ từ tuổi và ngày nhập viện."""
    days = age * 365 + random.randint(0, 364)
    return admission_date - timedelta(days=days)


def _load_or_create_cipher(cipher_cls, key_path: Path):
    if key_path.exists():
        return cipher_cls.load_key(str(key_path))
    cipher = cipher_cls()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    cipher.save_key(str(key_path))
    return cipher


def _load_or_create_dept_keypairs(key_dir: Path) -> dict:
    """Keypair Khoa DÙNG CHUNG cả nhóm: nạp từ file nếu đã có, chỉ sinh mới khi thiếu.

    Nhờ vậy public/private PEM ổn định — không random lại mỗi lần chạy → hết
    xung đột pull/push. (Trước đây luôn gọi generate_all_department_keypairs nên
    mỗi người chạy ra key khác nhau.)
    """
    from crypto.asym import generate_ecc_keypair
    result = {}
    for dept in DEPARTMENTS:
        pub_path = key_dir / f"{dept}_public.pem"
        priv_path = key_dir / f"{dept}_private.pem"
        if pub_path.exists():
            result[dept] = {"public_pem": pub_path.read_text()}
        else:
            priv, pub = generate_ecc_keypair()
            key_dir.mkdir(parents=True, exist_ok=True)
            pub_path.write_text(pub.decode())
            priv_path.write_text(priv.decode())
            result[dept] = {"public_pem": pub.decode()}
    return result

def main():
    key_dir = Path(__file__).resolve().parent / "keys"

    print("[1/5] Đồng bộ keypair Khoa (ECC P-384) — DÙNG CHUNG, nạp nếu đã có...")
    key_dir.mkdir(parents=True, exist_ok=True)
    dept_keypairs = _load_or_create_dept_keypairs(key_dir)
    print(f"  {len(dept_keypairs)} khoa: dùng key chung tại {key_dir} (không random lại nếu đã có)")

    print("[2/5] Khởi tạo các cipher...")
    # DTE: mỗi field 1 key riêng
    dte_ma_benh   = _load_or_create_cipher(DTECipher, key_dir / "dte_ma_benh.key")
    dte_khoa      = _load_or_create_cipher(DTECipher, key_dir / "dte_khoa.key")
    # ORE: 1 key cho tất cả integer/date fields
    ore = _load_or_create_cipher(ORECipher, key_dir / "ore.key")
    # AES-GCM: DEK cho lab results & billing
    gcm = _load_or_create_cipher(AESGCMCipher, key_dir / "gcm_dek.key")

    # NOTE: Do NOT write raw cipher keys or DEKs to disk. Store them into Vault or use envelope encryption.
    print("  Cipher keys generated in-memory. DO NOT write raw keys to disk — push to Vault instead.")

    print("[3/5] Kết nối MongoDB...")
    client = MongoClient(MONGO_URI)
    db     = client[DB_NAME]
    col    = db[COLLECTION]

    if col.estimated_document_count() > 0 and not FORCE_RECREATE:
        print(f"  Collection already has {col.estimated_document_count():,} documents; skipping reseed")
        client.close()
        return

    col.drop()  # Reset nếu chạy lại

    print(f"[4/5] Sinh và insert {RECORD_COUNT:,} hồ sơ...")
    batch_size = BATCH_SIZE
    batch = []

    for i in range(RECORD_COUNT):
        dept    = random.choice(DEPARTMENTS)
        pub_pem = dept_keypairs[dept]["public_pem"].encode()
        ma_benh = random.choice(list(ICD10_CODES.keys()))
        tuoi    = random.randint(1, 95)
        ngay_nhap = random_date()
        ngay_sinh = birth_date_from_age(ngay_nhap, tuoi)
        vien_phi  = round(random.uniform(500_000, 50_000_000), 0)  # VND
        chan_doan = random.choice(list(ICD10_CODES.values()))
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
            "ngay_sinh_enc":      ore.encrypt_date(ngay_sinh),
            "ngay_nhap_vien_enc": ore.encrypt_date(ngay_nhap),

            # Lab & Billing – AES-GCM
            "chan_doan_enc":     gcm.encrypt(chan_doan),
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
    col.create_index("ngay_sinh_enc")
    col.create_index("ngay_nhap_vien_enc")
    col.create_index("dept")
    print("  Indexes created: ma_benh_enc, khoa_phong_enc, tuoi_enc, ngay_nhap_vien_enc")
    print("  Verified indexes:")
    for index_info in col.list_indexes():
        print(f"    - {index_info.get('name')}")

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
