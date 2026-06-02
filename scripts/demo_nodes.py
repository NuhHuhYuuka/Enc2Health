#!/usr/bin/env python3
"""
DEMO NODE-INTERACTION TRACER — soi tương tác giữa các node trên CLI.

Hit thẳng vào stack ĐANG CHẠY THẬT (Router + MongoDB + Enclave/Pool + Vault) và
in từng bước: node nào gọi node nào, dữ liệu thật băng qua mỗi chặng (ciphertext
từ Mongo → con số từ enclave), kèm trạng thái sống/chết của từng node.

Dùng cho buổi bảo vệ: chứng minh các node tương tác THẬT, không phải animation.

Chạy (cần Router :8000, Pool :9091, Mongo :27017 đang chạy; cùng AUTH_JWT_SECRET):
    AUTH_JWT_SECRET=dev-secret-32-bytes-long-1234567890 python3 scripts/demo_nodes.py
    # tuỳ chọn: python3 scripts/demo_nodes.py <query_type> <ma_benh> <tuoi_min> <role>
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

os.environ.setdefault("AUTH_JWT_SECRET", "dev-secret-32-bytes-long-1234567890")
import common.auth as ca
ca.JWT_SECRET = os.environ["AUTH_JWT_SECRET"]
from common.auth import generate_test_jwt

ROUTER = os.environ.get("ROUTER_URL", "http://127.0.0.1:8000")

# ── màu ANSI ────────────────────────────────────────────────
R="\033[0m"; B="\033[1m"; DIM="\033[2m"
CY="\033[36m"; GR="\033[32m"; YE="\033[33m"; MA="\033[35m"; RD="\033[31m"; BL="\033[34m"
def c(s,col): return f"{col}{s}{R}"


def hr(ch="─", n=66): print(c(ch*n, DIM))


def node_status():
    print(c("┌─ TRẠNG THÁI CÁC NODE ", B) + c("─"*44+"┐", DIM))
    try:
        d = httpx.get(f"{ROUTER}/nodes", timeout=4).json()
        for n in d["nodes"]:
            ok = n["status"] == "ok"
            dot = c("●", GR) if ok else c("●", RD)
            port = f":{n['port']}" if n.get("port") else ""
            owner = f"({n['owner']})" if n.get("owner") and n["owner"] != "—" else ""
            line = f"{dot}  {n['label']:<34} {port:<7} {owner}"
            print("│ " + line)
    except Exception as e:
        msg = f"Không kết nối được Router /nodes: {type(e).__name__}: {e}"
        print("│ " + c(msg, RD))
    print(c("└"+"─"*65+"┘", DIM))


def _vis(s):
    import re
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def hop(idx, src, arrow, dst, what):
    print(f" {c(f'[{idx}]',B)} {src} {c(arrow,YE)} {dst}")
    if what:
        print(f"      {c('↳ '+what, DIM)}")


def main():
    qtype = sys.argv[1] if len(sys.argv) > 1 else "avg_vien_phi"
    ma_benh = sys.argv[2] if len(sys.argv) > 2 else "I01"
    tuoi_min = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    role = sys.argv[4] if len(sys.argv) > 4 else "admin"
    dept = "Tim_mach" if role == "doctor" else None

    filters = {}
    if ma_benh: filters["ma_benh"] = ma_benh
    if tuoi_min: filters["tuoi_min_enc"] = tuoi_min

    print()
    hr("═")
    print(c("  ENC²HEALTH — SOI TƯƠNG TÁC GIỮA CÁC NODE (live)", B))
    hr("═")
    node_status()

    print()
    print(c("  TRUY VẤN: ", B) + f"{qtype}  ·  mã bệnh={ma_benh or '(mọi)'}  ·  tuổi ≥ {tuoi_min}  ·  vai trò={role}"
          + (f" (khoa {dept})" if dept else ""))
    hr()

    # Độc lập soi MongoDB: CSP thấy gì? (ciphertext thật)
    csp_cipher = None
    try:
        from router.software_executor import SoftwareExecutor
        ex = SoftwareExecutor()
        enc = ex._build_filter(filters)
        try:
            cts = ex.fetch_ciphertexts(qtype, filters)   # tên mới của Long
        except Exception:
            cts = []
        csp_cipher = (enc, cts)
    except Exception:
        pass

    # NODE FLOW
    NODE = {"cli": c("👤 Client", CY), "rt": c("🧠 Router", BL), "mg": c("🗄️  MongoDB", GR),
            "en": c("🔒 Enclave/TEE", MA), "va": c("🔑 Vault", YE)}

    tok = generate_test_jwt(f"trace-{role}", role, claims={"dept": dept} if dept else None)
    t0 = time.perf_counter()
    r = httpx.post(f"{ROUTER}/query", json={"query_type": qtype, "filters": filters, "role": role},
                   headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    elapsed = (time.perf_counter()-t0)*1000

    if r.status_code != 200:
        print(c(f"  Router trả {r.status_code}: {r.text}", RD)); return
    d = r.json()
    mode = d["mode"]; tee = mode == "tee"; res = d.get("result", {}); val = res.get("result")
    pushed = d.get("ciphertext_pushed")

    hop(1, NODE["cli"], "──JWT + truy vấn──▶", NODE["rt"], "Bác sĩ gửi yêu cầu; Router xác thực JWT + áp RBAC/ABAC")
    hop(2, NODE["rt"], "──filter MÃ HÓA──▶", NODE["mg"], "Router mã hóa điều kiện (DTE cho mã bệnh, ORE cho tuổi)")
    if csp_cipher:
        enc, cts = csp_cipher
        print(f"      {c('CSP chỉ thấy (ciphertext):', RD)}")
        if "ma_benh_enc" in enc:
            print(f"        ma_benh_enc = {c(str(enc['ma_benh_enc'])[:40]+'…', DIM)}")
        if "tuoi_enc" in enc:
            print(f"        tuoi_enc    = {c(str(enc['tuoi_enc']), DIM)}")
        print(f"      MongoDB trả {c(str(len(cts)),B)} bản mã vien_phi_enc, ví dụ: {c((cts[0][:36]+'…') if cts else '—', DIM)}")

    if tee:
        hop(3, NODE["rt"], "──N bản mã vien_phi──▶", NODE["en"], f"Router đẩy {pushed if pushed is not None else 'các'} ciphertext vào enclave (CSP vẫn chỉ thấy chuỗi vô nghĩa)")
        hop(4, NODE["en"], "──xin DEK──▶", NODE["va"], "Enclave lấy khóa AES-GCM (DEK) từ Vault sau attestation")
        hop(5, NODE["en"], "giải mã + tính", "", "Giải mã AES-GCM TRONG enclave → DuckDB tính SUM/AVG (plaintext không rời enclave)")
        hop(6, NODE["en"], "──1 con số──▶", NODE["rt"]+" "+c("──▶",YE)+" "+NODE["cli"], "Chỉ KẾT QUẢ tổng hợp đi ra")
    else:
        hop(3, NODE["mg"], "──đếm/lọc trên ciphertext──▶", NODE["rt"], "SOFTWARE mode: xử lý thẳng trên bản mã, KHÔNG giải mã (Enclave/Vault không tham gia)")
        hop(4, NODE["rt"], "──kết quả──▶", NODE["cli"], "Trả về client")

    hr()
    badge = c(" TEE · Enclave ", MA+"\033[7m") if tee else c(" SOFTWARE ", GR+"\033[7m")
    shown = "🔒 [MASKED]" if val == "[MASKED]" else (f"{val:,.0f}" if isinstance(val, (int, float)) else val)
    print(f"  Chế độ thực thi : {badge}")
    print(f"  KẾT QUẢ bác sĩ nhận : {c(str(shown), B+GR)}" + (" VND" if "vien_phi" in qtype else ""))
    print(f"  Số bệnh nhân (CSP chỉ thấy ciphertext) : {c(str(res.get('n_records','—')), B)}")
    print(f"  Độ trễ E2E qua các node : {c(f'{elapsed:.1f} ms', B)}")
    print(f"  {c('→ Suốt quá trình, nhà cung cấp Cloud KHÔNG hề thấy con số trên — chỉ thấy ciphertext.', GR)}")
    hr("═")
    print()


if __name__ == "__main__":
    main()
