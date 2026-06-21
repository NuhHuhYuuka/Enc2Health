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
DEMO_PATIENTS = [
    {
        "ho_ten": "An Vũ",
        "cmnd": "03321819600",
        "dept": "Nhi",
        "ma_benh": "P01",
        "tom_tat_benh_an": (
            "Trẻ sốt nhẹ kèm ho khan kéo dài, thở rút lõm lồng ngực nhẹ, "
            "nghe phổi có ran ẩm vùng đáy phổi."
        ),
    },
    {"ho_ten": "Quang Đức Nguyễn", "cmnd": "07816184959", "dept": "Noi", "ma_benh": "I01"},
    {"ho_ten": "Hải Nguyễn", "cmnd": "05641395376", "dept": "Tim_mach"},
    {"ho_ten": "Nam Lê", "cmnd": "06542351161", "dept": "Cap_cuu"},
    {"ho_ten": "Tùng Nguyễn", "cmnd": "01845146270", "dept": "Than_kinh"},
    {"ho_ten": "Lâm Phạm", "cmnd": "08908386379", "dept": "Ngoai"},
]
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
    "P01": [
        "Bệnh nhi ho nhiều, sốt cao, khò khè, phổi có rít ẩm, X-quang phổi có tổn thương thâm nhiễm.",
        "Trẻ sốt nhẹ kèm ho khan kéo dài, thở rút lõm lồng ngực nhẹ, nghe phổi có ran ẩm vùng đáy phổi.",
        "Bệnh nhi nhập viện do ho có đờm xanh, sốt cao 39 độ, thở nhanh nông, X-quang phổi có đám mờ rải rác."
    ],
    "P02": [
        "Bệnh nhi sốt cao liên tục ngày thứ 4, xuất hiện xuất huyết dưới da, tiểu cầu giảm mạnh.",
        "Trẻ sốt cao ngày thứ 3 kèm đau đầu, đau hốc mắt, chảy máu cam nhẹ, xét nghiệm NS1Ag dương tính.",
        "Bệnh nhi sốt ngày thứ 5, mệt lả, nôn nhiều, đau bụng vùng gan, hematocrit tăng cao kèm tiểu cầu < 50 G/L."
    ],
    "C01": [
        "Bệnh nhân thường xuyên đau đầu, chóng mặt, đo huyết áp ghi nhận trị số 160/90 mmHg kéo dài.",
        "Đo huyết áp khám sức khỏe định kỳ ghi nhận 150/95 mmHg, có tiền sử gia đình bị tai biến mạch máu não.",
        "Bệnh nhân nhập viện vì cơn tăng huyết áp kịch phát 180/100 mmHg kèm hồi hộp trống ngực dữ dội."
    ],
    "C02": [
        "Bệnh nhân đau tức ngực trái khi gắng sức, điện tâm đồ có dấu hiệu thiếu máu cơ tim vùng hoành.",
        "Bệnh nhân đau thắt ngực ổn định lan lên vai trái, giảm khi nghỉ ngơi, có tiền sử xơ vữa động mạch.",
        "Đau nặng ngực trái âm ỉ kèm khó thở nhẹ khi đi bộ đường dài, chụp động mạch vành phát hiện hẹp nhẹ."
    ],
    "I01": [
        "Bệnh nhân có triệu chứng ăn nhiều, uống nhiều, tiểu nhiều, sụt cân, xét nghiệm glucose máu đói 8.5 mmol/L.",
        "Bệnh nhân tê bì hai bàn chân, kiểm tra đường huyết ngẫu nhiên đạt 11.2 mmol/L, HbA1c ghi nhận 7.8%.",
        "Phát hiện tăng đường huyết khi khám định kỳ (glucose đói 7.6 mmol/L), thể trạng béo phì trung tâm."
    ],
    "I02": [
        "Bệnh nhân mệt mỏi, phù hai chi dưới, thiếu máu nhẹ, ure và creatinine máu tăng cao (creatinine 350 umol/L).",
        "Bệnh nhân tiểu ít, phù toàn thân kèm khó thở khi nằm, creatinine 580 umol/L, Kali máu tăng.",
        "Phát hiện suy giảm chức năng thận mạn tiến triển từ đái tháo đường, creatinine 420 umol/L kèm protein niệu thể nặng."
    ],
    "N01": [
        "Bệnh nhân mất ngủ kéo dài, buồn bã vô cớ, suy giảm năng lượng, có suy nghĩ tiêu cực.",
        "Bệnh nhân chán ăn, sụt cân, không còn hứng thú với sở thích cũ, thường xuyên cảm thấy tội lỗi và vô dụng.",
        "Bệnh nhân mệt mỏi cùng cực, giảm tập trung chú ý, cô lập bản thân và có hành vi làm đau cơ thể."
    ],
    "N02": [
        "Bệnh nhân đau dọc đường đi của dây thần kinh hông to một bên, tăng lên khi ho hoặc cúi người.",
        "Đau vùng thắt lưng lan xuống mông và mặt sau đùi phải, kèm cảm giác tê bì ngón chân cái.",
        "Đau nhức cột sống thắt lưng cấp tính sau khi bê vật nặng, kết quả MRI ghi nhận thoát vị đĩa đệm L4-L5 chèn ép rễ."
    ],
    "S01": [
        "Bệnh nhân đau âm ỉ hố chậu phải tăng dần, có phản ứng thành bụng, sốt nhẹ 38 độ C.",
        "Đau đầu vị thượng vị sau lan xuống hố chậu phải, buồn nôn, ấn đau điểm ruột thừa, bạch cầu tăng cao.",
        "Đau hố chậu phải dữ dội kèm sốt 38.5 độ, siêu âm thấy hình ảnh ruột thừa sưng to đường kính 8mm."
    ],
    "S02": [
        "Bệnh nhân đau quặn hạ sườn phải lan lên vai phải sau bữa ăn nhiều dầu mỡ, siêu âm có sỏi bùn túi mật.",
        "Bệnh nhân đau tức hạ sườn phải âm ỉ kéo dài, siêu âm ghi nhận sỏi túi mật kích thước lớn 15mm.",
        "Đau quặn mật tái phát nhiều lần kèm đầy bụng, ăn uống khó tiêu, thành túi mật không dày."
    ],
    "E01": [
        "Bệnh nhân nhập viện trong tình trạng sốc chấn thương, đa vết thương phần mềm, nghi gãy xương đùi.",
        "Nhập viện cấp cứu sau tai nạn giao thông: tri giác lơ mơ, chấn thương sọ não kín kèm tràn dịch màng phổi nhẹ.",
        "Bệnh nhân gãy hở xương cẳng chân trái, chấn thương bụng kín nghi vỡ lách độ 2 do ngã cao."
    ],
    "E02": [
        "Bệnh nhân nôn mửa, đau bụng quặn thắt, đi ngoài nhiều lần sau khi ăn hải sản không rõ nguồn gốc.",
        "Bệnh nhân sốt nhẹ, đi ngoài phân lỏng tóe nước liên tục ngày thứ 2, có dấu hiệu mất nước nhẹ (môi khô, khát nước).",
        "Đau bụng quặn thắt quanh rốn, sốt 38.2 độ, nôn nhiều lần kèm đi ngoài phân có chất nhầy sau ăn tiệc cưới."
    ],
}

TREATMENT_PROTOCOLS = {
    "P01": [
        "Kháng sinh Amoxicillin phối hợp, hạ sốt bằng Paracetamol, khí dung giãn phế quản, bổ sung nước điện giải.",
        "Dùng kháng sinh Ceftriaxone tiêm tĩnh mạch, hỗ trợ thở oxy mask, truyền dịch điện giải và vỗ rung lồng ngực.",
        "Chỉ định kháng sinh nhóm Macrolide (Azithromycin), thuốc ho thảo dược, hạ sốt khi sốt trên 38.5 độ C, nằm đầu cao."
    ],
    "P02": [
        "Bù dịch tĩnh mạch theo phác đồ chống sốc Dengue, theo dõi sát mạch, huyết áp và số lượng tiểu cầu hàng giờ.",
        "Hạ sốt bằng Paracetamol (tránh Ibuprofen), uống nhiều nước oresol bù điện giải, theo dõi các dấu hiệu cảnh báo.",
        "Truyền dịch Ringer Lactate khống chế tốc độ, theo dõi sát lượng nước tiểu và công thức máu mỗi 6-12 giờ."
    ],
    "C01": [
        "Chỉ định dùng thuốc hạ áp Amlodipine 5mg uống sáng, hạn chế ăn mặn, tập thể dục nhẹ nhàng.",
        "Phối hợp thuốc Coversyl 5mg uống sáng kèm lợi tiểu nhẹ Indapamide, tư vấn giảm cân và hạn chế muối.",
        "Sử dụng Losartan 50mg uống tối, đo huyết áp tại nhà 2 lần/ngày, tái khám định kỳ sau 2 tuần."
    ],
    "C02": [
        "Dùng Aspirin 81mg chống ngưng tập tiểu cầu, Atorvastatin 20mg kiểm soát mỡ máu, giảm gắng sức.",
        "Chỉ định thuốc chẹn beta (Metoprolol 25mg), Nitroglycerin xịt dưới lưỡi khi có cơn đau ngực, kiểm soát HA.",
        "Sử dụng Clopidogrel 75mg thay thế, kết hợp Vastarel MR uống ngày 2 lần, tư vấn chế độ ăn ít lipid."
    ],
    "I01": [
        "Điều trị bằng Metformin 850mg uống sau ăn, tư vấn chế độ ăn giảm tinh bột, theo dõi đường huyết đói hàng tuần.",
        "Sử dụng phối hợp Metformin 500mg và Gliclazide 30mg uống trước ăn sáng, tư vấn luyện tập thể lực 30p/ngày.",
        "Chỉ định dùng Glucophage XR 1000mg tối, hạn chế chất ngọt, kiểm tra HbA1c và chức năng thận định kỳ mỗi 3 tháng."
    ],
    "I02": [
        "Chế độ ăn giảm đạm, kiểm soát huyết áp bằng ARB, bổ sung Erythropoietin kích hồng cầu, chuẩn bị lọc máu chu kỳ.",
        "Chỉ định hạn chế nước và muối uống, dùng thuốc lợi tiểu Quai (Furosemide 40mg), chuẩn bị làm cầu nối động tĩnh mạch (FAV).",
        "Lọc màng bụng tại nhà hàng ngày hoặc chạy thận nhân tạo 3 lần/tuần, dùng thêm canxi cacbonat uống giảm phosphat máu."
    ],
    "N01": [
        "Sử dụng thuốc chống trầm cảm dòng SSRI (Sertraline 50mg), kết hợp trị liệu tâm lý nhận thức hành vi.",
        "Chỉ định dùng Escitalopram 10mg uống sáng, tư vấn người nhà giám sát chặt chẽ, kết hợp thiền và vận động nhẹ.",
        "Sử dụng Fluoxetine 20mg uống sau ăn sáng, hội chẩn chuyên khoa tâm thần định kỳ hàng tuần, trị liệu gia đình."
    ],
    "N02": [
        "Giảm đau bằng NSAID (Meloxicam 15mg), uống bổ sung vitamin nhóm B, hạn chế mang vác vật nặng.",
        "Sử dụng Paracetamol phối hợp Tramadol khi đau nhiều, nằm giường cứng nghỉ ngơi, tập vật lý trị liệu kéo giãn cột sống.",
        "Dùng Celecoxib 200mg/ngày, kết hợp thuốc giãn cơ Mydocalm 150mg, chỉ định đeo đai thắt lưng khi đi lại."
    ],
    "S01": [
        "Chỉ định mổ nội soi cắt ruột thừa cấp cứu, kháng sinh dự phòng cefuroxime + metronidazole.",
        "Chuẩn bị mổ nội soi cắt ruột thừa khẩn cấp, truyền dịch bồi hoàn, nhịn ăn uống tuyệt đối.",
        "Phẫu thuật nội soi cắt ruột thừa trong ngày, theo dõi sát dấu hiệu sinh tồn và tình trạng đau bụng sau mổ."
    ],
    "S02": [
        "Chỉ định phẫu thuật nội soi cắt túi mật chủ động, tư vấn chế độ ăn ít chất béo sau mổ.",
        "Tạm thời dùng thuốc giảm co thắt cơ trơn (Spasfon), theo dõi biến chứng viêm túi mật cấp để chỉ định mổ chương trình.",
        "Phẫu thuật nội soi cắt túi mật trì hoãn, hạn chế ăn thức ăn nhiều cholesterol và dầu mỡ động vật."
    ],
    "E01": [
        "Cổ định xương gãy, truyền dịch chống sốc, chụp CT toàn thân xác định tổn thương nội tạng, hội chẩn mổ khẩn.",
        "Truyền dịch điện giải và dung dịch keo chống sốc, đặt ống nội khí quản hỗ trợ hô hấp, chuyển phòng mổ xử trí chấn thương.",
        "Giảm đau toàn thân cực mạnh, truyền máu cùng nhóm, mổ cấp cứu kết hợp xương và nội soi thăm dò ổ bụng."
    ],
    "E02": [
        "Truyền dịch bù nước điện giải Ringer Lactate, uống than hoạt tính hấp phụ độc tố, dùng kháng sinh đường ruột nếu nhiễm khuẩn.",
        "Bù dịch bằng Oresol đường uống liều lượng lớn, dùng thuốc bảo vệ niêm mạc ruột Smecta, theo dõi sát điện giải đồ.",
        "Truyền dịch tĩnh mạch bù nước khẩn cấp, sử dụng men vi sinh Probiotics, dùng kháng sinh Ciprofloxacin uống nếu sốt cao."
    ],
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
        demo_patient = DEMO_PATIENTS[i] if i < len(DEMO_PATIENTS) else None
        dept    = demo_patient["dept"] if demo_patient else random.choice(DEPARTMENTS)
        pub_pem = dept_keypairs[dept]["public_pem"].encode()
        dept_diseases = DEPARTMENT_DISEASES[dept]
        ma_benh = demo_patient.get("ma_benh") if demo_patient and demo_patient.get("ma_benh") else random.choice(list(dept_diseases.keys()))
        tuoi = random.randint(1, 15) if dept == "Nhi" else random.randint(16, 95)
        ngay_nhap = random_date()
        ngay_sinh = birth_date_from_age(ngay_nhap, tuoi)
        vien_phi  = round(random.uniform(500_000, 50_000_000), 0)  # VND
        chan_doan = dept_diseases[ma_benh]
        clinical_note = random.choice(CLINICAL_NOTE_TEMPLATES).format(
            diagnosis=chan_doan,
            dept=dept.replace("_", " "),
        )
        tom_tat_benh_an = (
            demo_patient.get("tom_tat_benh_an")
            if demo_patient and demo_patient.get("tom_tat_benh_an")
            else random.choice(CLINICAL_SUMMARIES[ma_benh])
        )
        phac_do_dieu_tri = random.choice(TREATMENT_PROTOCOLS[ma_benh])
        ket_qua_xn = {
            "glucose": round(random.uniform(3.5, 15.0), 1),   # mmol/L
            "hba1c":   round(random.uniform(4.0, 12.0), 1),   # %
            "creatinine": round(random.uniform(50, 300), 0),  # umol/L
        }

        ho_ten = demo_patient["ho_ten"] if demo_patient else fake.name()
        cmnd = demo_patient["cmnd"] if demo_patient else fake.numerify("0##########")
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
