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
from crypto.sse  import StaticSSECipher, tokenize_text
from pymongo import MongoClient

# ── Cấu hình ──────────────────────────────────────────────────────────────
MONGO_URI    = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME      = os.getenv("MONGO_DB", "enc2health")
COLLECTION   = os.getenv("MONGO_COLLECTION", "patient_records")
SSE_COLLECTION = os.getenv("SSE_COLLECTION", "sse_index")
RECORD_COUNT = int(os.getenv("EHR_RECORD_COUNT", "10000"))
BATCH_SIZE   = int(os.getenv("EHR_BATCH_SIZE", "500"))
FORCE_RECREATE = os.getenv("EHR_FORCE_RECREATE", "0") == "1"
DATASET_SEED = int(os.getenv("EHR_RANDOM_SEED", "42"))
ALLOW_KEY_CREATE = os.getenv("EHR_ALLOW_KEY_CREATE", "0") == "1"

random.seed(DATASET_SEED)
Faker.seed(DATASET_SEED)
fake = Faker("vi_VN")
fake.seed_instance(DATASET_SEED)

DEPARTMENTS = ["Noi", "Ngoai", "Cap_cuu", "Tim_mach", "Than_kinh", "Nhi"]
DEPARTMENT_DISEASES = {
    "Nhi": {
        "P01": "Viêm phổi ở trẻ em",
        "P02": "Sốt xuất huyết Dengue trẻ em",
    },
    "Tim_mach": {
        "C01": "Tăng huyết áp vô căn",
        "C02": "Bệnh tim thiếu máu cục bộ mạn tính",
    },
    "Noi": {
        "I01": "Đái tháo đường Type 2",
        "I02": "Bệnh thận mạn giai đoạn cuối",
    },
    "Than_kinh": {
        "N01": "Giai đoạn trầm cảm nặng",
        "N02": "Đau thần kinh tọa / Thoát vị đĩa đệm",
    },
    "Ngoai": {
        "S01": "Viêm ruột thừa cấp",
        "S02": "Sỏi túi mật",
    },
    "Cap_cuu": {
        "E01": "Đa chấn thương do tai nạn",
        "E02": "Ngộ độc thực phẩm cấp tính",
    },
}
DISEASE_CODES = {
    code: name
    for diseases in DEPARTMENT_DISEASES.values()
    for code, name in diseases.items()
}
STREET_NAMES = [
    "Nguyễn Trãi", "Lê Lợi", "Trần Hưng Đạo", "Hai Bà Trưng",
    "Điện Biên Phủ", "Nguyễn Huệ", "Phan Đình Phùng", "Lý Thường Kiệt",
]
WARD_NAMES = [
    "Phường Bến Nghé", "Phường Tân Định", "Phường Linh Trung",
    "Phường Hòa Cường", "Xã Bình Minh", "Xã Tân Phú",
]
DISTRICT_NAMES = [
    "Quận 1", "Quận 3", "Quận Bình Thạnh", "Quận Hải Châu",
    "Huyện Nhà Bè", "Thị xã Sơn Tây",
]
CITY_NAMES = [
    "TP. Hồ Chí Minh", "TP. Hà Nội", "TP. Đà Nẵng",
    "TP. Cần Thơ", "TP. Hải Phòng",
]
CLINICAL_NOTE_TEMPLATES = [
    "Bệnh nhân được theo dõi {diagnosis}, cần kiểm tra định kỳ tại khoa {dept}.",
    "Hồ sơ ghi nhận tiền sử {diagnosis}, chỉ định xét nghiệm glucose và creatinine.",
    "Tái khám sau điều trị {diagnosis}, đánh giá đáp ứng thuốc và viện phí.",
    "Theo dõi nguy cơ biến chứng liên quan {diagnosis} tại khoa {dept}.",
]

CLINICAL_SUMMARIES = {
    "P01": "Bệnh nhi ho nhiều, sốt cao, khò khè, phổi có rít ẩm, X-quang phổi có tổn thương thâm nhiễm.",
    "P02": "Bệnh nhi sốt cao liên tục ngày thứ 4, xuất hiện xuất huyết dưới da, tiểu cầu giảm mạnh.",
    "C01": "Bệnh nhân thường xuyên đau đầu, chóng mặt, đo huyết áp ghi nhận trị số 160/90 mmHg kéo dài.",
    "C02": "Bệnh nhân đau tức ngực trái khi gắng sức, điện tâm đồ có dấu hiệu thiếu máu cơ tim vùng hoành.",
    "I01": "Bệnh nhân có triệu chứng ăn nhiều, uống nhiều, tiểu nhiều, sụt cân, xét nghiệm glucose máu đói 8.5 mmol/L.",
    "I02": "Bệnh nhân mệt mỏi, phù hai chi dưới, thiếu máu nhẹ, ure và creatinine máu tăng cao (creatinine 350 umol/L).",
    "N01": "Bệnh nhân mất ngủ kéo dài, buồn bã vô cớ, suy giảm năng lượng, có suy nghĩ tiêu cực.",
    "N02": "Bệnh nhân đau dọc đường đi của dây thần kinh hông to một bên, tăng lên khi ho hoặc cúi người.",
    "S01": "Bệnh nhân đau âm ỉ hố chậu phải tăng dần, có phản ứng thành bụng, sốt nhẹ 38 độ C.",
    "S02": "Bệnh nhân đau quặn hạ sườn phải lan lên vai phải sau bữa ăn nhiều dầu mỡ, siêu âm có sỏi bùn túi mật.",
    "E01": "Bệnh nhân nhập viện trong tình trạng sốc chấn thương, đa vết thương phần mềm, nghi gãy xương đùi.",
    "E02": "Bệnh nhân nôn mửa, đau bụng quặn thắt, đi ngoài nhiều lần sau khi ăn hải sản không rõ nguồn gốc.",
}

TREATMENT_PROTOCOLS = {
    "P01": "Kháng sinh Amoxicillin phối hợp, hạ sốt bằng Paracetamol, khí dung giãn phế quản, bổ sung nước điện giải.",
    "P02": "Bù dịch tĩnh mạch theo phác đồ chống sốc Dengue, theo dõi sát mạch, huyết áp và số lượng tiểu cầu hàng giờ.",
    "C01": "Chỉ định dùng thuốc hạ áp Amlodipine 5mg uống sáng, hạn chế ăn mặn, tập thể dục nhẹ nhàng.",
    "C02": "Dùng Aspirin 81mg chống ngưng tập tiểu cầu, Atorvastatin 20mg kiểm soát mỡ máu, giảm gắng sức.",
    "I01": "Điều trị bằng Metformin 850mg uống sau ăn, tư vấn chế độ ăn giảm tinh bột, theo dõi đường huyết đói hàng tuần.",
    "I02": "Chế độ ăn giảm đạm, kiểm soát huyết áp bằng ARB, bổ sung Erythropoietin kích hồng cầu, chuẩn bị lọc máu chu kỳ.",
    "N01": "Sử dụng thuốc chống trầm cảm dòng SSRI (Sertraline 50mg), kết hợp trị liệu tâm lý nhận thức hành vi.",
    "N02": "Giảm đau bằng NSAID (Meloxicam 15mg), uống bổ sung vitamin nhóm B, hạn chế mang vác vật nặng.",
    "S01": "Chỉ định mổ nội soi cắt ruột thừa cấp cứu, kháng sinh dự phòng cefuroxime + metronidazole.",
    "S02": "Chỉ định phẫu thuật nội soi cắt túi mật chủ động, tư vấn chế độ ăn ít chất béo sau mổ.",
    "E01": "Cố định xương gãy, truyền dịch chống sốc, chụp CT toàn thân xác định tổn thương nội tạng, hội chẩn mổ khẩn.",
    "E02": "Truyền dịch bù nước điện giải Ringer Lactate, uống than hoạt tính hấp phụ độc tố, dùng kháng sinh đường ruột nếu nhiễm khuẩn.",
}

def random_date(start_year=2020, end_year=2024) -> date:
    start = date(start_year, 1, 1)
    end   = date(end_year, 12, 31)
    return start + timedelta(days=random.randint(0, (end - start).days))


def birth_date_from_age(admission_date: date, age: int) -> date:
    """Sinh ngày sinh xấp xỉ từ tuổi và ngày nhập viện."""
    days = age * 365 + random.randint(0, 364)
    return admission_date - timedelta(days=days)


def generate_vietnamese_address() -> str:
    """Tạo địa chỉ demo có cấu trúc ổn định, không phụ thuộc format thô của Faker."""
    house_number = random.randint(1, 999)
    street = random.choice(STREET_NAMES)
    ward = random.choice(WARD_NAMES)
    district = random.choice(DISTRICT_NAMES)
    city = random.choice(CITY_NAMES)
    return f"Số {house_number} đường {street}, {ward}, {district}, {city}"


def _load_required_cipher(cipher_cls, key_path: Path):
    if key_path.exists():
        return cipher_cls.load_key(str(key_path))
    if not ALLOW_KEY_CREATE:
        raise FileNotFoundError(
            f"Missing required key: {key_path}. "
            "Pull the shared crypto/data/keys files first, or set EHR_ALLOW_KEY_CREATE=1 only to bootstrap new dev keys."
        )
    cipher = cipher_cls()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    cipher.save_key(str(key_path))
    return cipher


def _load_dept_keypairs(key_dir: Path) -> dict:
    """Load the shared department keypairs used by the whole team.

    By default this script refuses to generate missing keys, so reseeding cannot
    silently drift away from the committed team keyset. Set EHR_ALLOW_KEY_CREATE=1
    only when intentionally bootstrapping a new dev keyset.
    """
    from cryptography.hazmat.primitives import serialization

    def keypair_matches(private_pem: bytes, public_pem: bytes) -> bool:
        try:
            private_key = serialization.load_pem_private_key(private_pem, password=None)
            public_key = serialization.load_pem_public_key(public_pem)
            return private_key.public_key().public_numbers() == public_key.public_numbers()
        except Exception:
            return False

    result = {}
    for dept in DEPARTMENTS:
        pub_path = key_dir / f"{dept}_public.pem"
        priv_path = key_dir / f"{dept}_private.pem"
        if pub_path.exists() and priv_path.exists() and keypair_matches(priv_path.read_bytes(), pub_path.read_bytes()):
            result[dept] = {"public_pem": pub_path.read_text()}
        else:
            if not ALLOW_KEY_CREATE:
                raise FileNotFoundError(
                    f"Missing or mismatched department keypair for {dept}: {pub_path}, {priv_path}. "
                    "Pull the shared crypto/data/keys files first, or set EHR_ALLOW_KEY_CREATE=1 only to bootstrap new dev keys."
                )
            from crypto.asym import generate_ecc_keypair
            priv, pub = generate_ecc_keypair()
            key_dir.mkdir(parents=True, exist_ok=True)
            pub_path.write_text(pub.decode())
            priv_path.write_text(priv.decode())
            result[dept] = {"public_pem": pub.decode()}
    return result

def main():
    key_dir = Path(__file__).resolve().parent / "keys"

    print("[1/5] Nạp keypair Khoa (ECC P-384) từ bộ key dùng chung...")
    key_dir.mkdir(parents=True, exist_ok=True)
    dept_keypairs = _load_dept_keypairs(key_dir)
    print(f"  {len(dept_keypairs)} khoa: dùng key chung tại {key_dir}")

    print("[2/5] Nạp các cipher key đã có...")
    # DTE: mỗi field 1 key riêng
    dte_ma_benh   = _load_required_cipher(DTECipher, key_dir / "dte_ma_benh.key")
    dte_khoa      = _load_required_cipher(DTECipher, key_dir / "dte_khoa.key")
    dte_cmnd      = _load_required_cipher(DTECipher, key_dir / "dte_cmnd.key")
    # ORE: 1 key cho tất cả integer/date fields
    ore = _load_required_cipher(ORECipher, key_dir / "ore.key")
    # AES-GCM: DEK cho lab results & billing
    gcm = _load_required_cipher(AESGCMCipher, key_dir / "gcm_dek.key")
    sse = _load_required_cipher(StaticSSECipher, key_dir / "sse.key")

    # NOTE: Do NOT write raw cipher keys or DEKs to disk. Store them into Vault or use envelope encryption.
    print("  Loaded existing keys. Missing keys will fail unless EHR_ALLOW_KEY_CREATE=1.")

    print("[3/5] Kết nối MongoDB...")
    client = MongoClient(MONGO_URI)
    db     = client[DB_NAME]
    col    = db[COLLECTION]
    sse_col = db[SSE_COLLECTION]

    if col.estimated_document_count() > 0 and not FORCE_RECREATE:
        print(f"  Collection already has {col.estimated_document_count():,} documents; skipping reseed")
        client.close()
        return

    col.drop()  # Reset nếu chạy lại
    sse_col.drop()

    print(f"[4/5] Sinh và insert {RECORD_COUNT:,} hồ sơ...")
    print(f"  Dataset seed: {DATASET_SEED}")
    batch_size = BATCH_SIZE
    batch = []
    sse_index: dict[str, list[dict[str, str]]] = {}

    for i in range(RECORD_COUNT):
        dept    = random.choice(DEPARTMENTS)
        pub_pem = dept_keypairs[dept]["public_pem"].encode()
        dept_diseases = DEPARTMENT_DISEASES[dept]
        ma_benh = random.choice(list(dept_diseases.keys()))
        tuoi = random.randint(1, 15) if dept == "Nhi" else random.randint(16, 95)
        ngay_nhap = random_date()
        ngay_sinh = birth_date_from_age(ngay_nhap, tuoi)
        vien_phi  = round(random.uniform(500_000, 50_000_000), 0)  # VND
        chan_doan = dept_diseases[ma_benh]
        clinical_note = random.choice(CLINICAL_NOTE_TEMPLATES).format(
            diagnosis=chan_doan,
            dept=dept.replace("_", " "),
        )
        tom_tat_benh_an = CLINICAL_SUMMARIES[ma_benh]
        phac_do_dieu_tri = TREATMENT_PROTOCOLS[ma_benh]
        ket_qua_xn = {
            "glucose": round(random.uniform(3.5, 15.0), 1),   # mmol/L
            "hba1c":   round(random.uniform(4.0, 12.0), 1),   # %
            "creatinine": round(random.uniform(50, 300), 0),  # umol/L
        }

        ho_ten = fake.name()
        cmnd = fake.numerify("0##########")
        dia_chi = generate_vietnamese_address()
        pii_payload = {
            "ho_ten": ho_ten,
            "cmnd": cmnd,
            "ngay_sinh": ngay_sinh.isoformat(),
            "dia_chi": dia_chi,
            "tom_tat_benh_an": tom_tat_benh_an,
            "phac_do_dieu_tri": phac_do_dieu_tri,
        }

        patient_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"enc2health:{DATASET_SEED}:{i}"))

        keyword_source = " ".join(
            [
                ma_benh,
                chan_doan,
                clinical_note,
                dept.replace("_", " "),
                tom_tat_benh_an,
                phac_do_dieu_tri,
            ]
        )
        for keyword in tokenize_text(keyword_source):
            token = sse.token(keyword)
            sse_index.setdefault(token, []).append({"patient_id": patient_id, "dept": dept})

        doc = {
            # Plaintext fields
            "patient_id": patient_id,
            "record_index": i,
            "dataset_seed": DATASET_SEED,
            "khoa_phong_plaintext": dept,  # Chỉ để debug, xóa trong production

            # PII – Asymmetric ECC
            "pii_enc": ecc_encrypt(json.dumps(pii_payload, ensure_ascii=False), pub_pem),
            "ho_ten_enc":  ecc_encrypt(ho_ten, pub_pem),
            "cmnd_enc":    ecc_encrypt(cmnd, pub_pem),
            "dia_chi_enc": ecc_encrypt(dia_chi, pub_pem),
            "dept":        dept,  # Để biết dùng keypair nào khi decrypt

            # Clinical – DTE AES-SIV
            "ma_benh_enc":    dte_ma_benh.encrypt(ma_benh, b"field:ma_benh"),
            "khoa_phong_enc": dte_khoa.encrypt(dept, b"field:khoa_phong"),
            "cmnd_dte":       dte_cmnd.encrypt(cmnd, b"field:cmnd"),

            # Range – OPE
            "tuoi_enc":          ore.encrypt_age(tuoi),
            "ngay_sinh_enc":      ore.encrypt_date(ngay_sinh),
            "ngay_nhap_vien_enc": ore.encrypt_date(ngay_nhap),

            # Lab & Billing – AES-GCM
            "chan_doan_enc":     gcm.encrypt(chan_doan),
            "ket_qua_xn_enc": gcm.encrypt(ket_qua_xn),
            "vien_phi_enc":   gcm.encrypt(str(vien_phi)),
            "clinical_note_enc": gcm.encrypt(clinical_note),

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

    print("[4b/5] Tạo SSE encrypted inverted index...")
    sse_docs = []
    for token, postings in sse_index.items():
        sse_docs.append(
            {
                "token": token,
                "postings_enc": sse.encrypt_postings(postings),
                "n_records": len(postings),  # volume leakage intentionally visible for evaluation
                "dataset_seed": DATASET_SEED,
            }
        )
        if len(sse_docs) >= batch_size:
            sse_col.insert_many(sse_docs)
            sse_docs = []
    if sse_docs:
        sse_col.insert_many(sse_docs)
    print(f"  SSE keywords: {sse_col.count_documents({}):,}")

    print("[5/5] Tạo MongoDB indexes...")
    # Index trên DTE fields (equality search)
    col.create_index("ma_benh_enc")
    col.create_index("khoa_phong_enc")
    col.create_index("cmnd_dte")
    # Index trên ORE fields (range query)
    col.create_index("tuoi_enc")
    col.create_index("ngay_sinh_enc")
    col.create_index("ngay_nhap_vien_enc")
    col.create_index("dept")
    sse_col.create_index("token", unique=True)
    print("  Indexes created: ma_benh_enc, khoa_phong_enc, tuoi_enc, ngay_nhap_vien_enc")
    print("  Verified indexes:")
    for index_info in col.list_indexes():
        print(f"    - {index_info.get('name')}")

    print("\n✅ Dataset generation complete!")
    print(f"   DB: {DB_NAME} | Collection: {COLLECTION}")
    print(f"   Records: {RECORD_COUNT:,}")

    # Demo query để verify
    print("\n[Demo] Equality search: ma_benh = 'I01'")
    i01_ct = dte_ma_benh.encrypt("I01", b"field:ma_benh")
    count_i01 = col.count_documents({"ma_benh_enc": i01_ct})
    print(f"  Tìm thấy {count_i01} bệnh nhân Đái tháo đường Type 2 (I01)")

    print("\n[Demo] Range query: tuoi > 60")
    threshold = ore.encrypt_age(60)
    count_elderly = col.count_documents({"tuoi_enc": {"$gt": threshold}})
    print(f"  Tìm thấy {count_elderly} bệnh nhân > 60 tuổi")

    client.close()

if __name__ == "__main__":
    main()
