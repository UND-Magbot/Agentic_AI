"""툴체인저 견적서 엑셀(company_knowledge/quote_xlsx.py) 점검 — 머리·양식(한글/영문)·파일 내용(수식·그림·인쇄).
품목 줄은 견적서 작성(quote_session) 초안에서 만든다 — 대화·수정은 scripts/test_quote_session.py.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts -v ./docs:/docs backend \\
        python /scripts/test_quote_xlsx.py
"""
from __future__ import annotations

import io
import sys
from datetime import date

import openpyxl

from app.company_knowledge import product_prices as pp
from app.company_knowledge import quote_session as qs
from app.company_knowledge import quote_xlsx as qx

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


rows = pp.parse_book()
model = "TCV1"
items = [
    {"name": f"{model} MASTER T.C", "code": None, "qty": 1, "price_model": model, "price_item": "MASTER T.C", "optional": False},
    {"name": "IB (Interface Bracket)", "code": None, "qty": 1, "price_model": model, "price_item": "IB", "optional": True, "included": True},
    {"name": f"{model} T.P", "code": None, "qty": 2, "price_model": model, "price_item": "T.P", "optional": False},
    {"name": "Pogo Pin Male (PPM)", "code": "PPM", "qty": 2, "price_model": model, "price_item": "PPM/PPF", "optional": False},
    {"name": "Pogo Pin Female (PPF)", "code": "PPF", "qty": 2, "price_model": model, "price_item": "PPM/PPF", "optional": False},
    {"name": "Cable 0.3m", "code": None, "qty": 4, "price_model": model, "price_item": "Cable 0.3m", "optional": False},
    {"name": "PneuMatic Male (PMM)", "code": "PMM", "qty": 1, "price_model": model, "price_item": "PMM/PMF", "optional": False},
    {"name": "PneuMatic Female (PMF)", "code": "PMF", "qty": 1, "price_model": model, "price_item": "PMM/PMF", "optional": False},
]
pp.attach_prices(items, rows)
st = qs.draft_state({"applicable": True, "items": items}, model=model, payload_kg=10)
lines = qs.render_lines(st, "ko", None)
check([ln["key"] for ln in lines] == ["MASTER T.C", "T.P", "PPM", "PPF", "Cable 0.3m", "PMM", "PMF", "IB"],
      "품목 순서: 본체 → 액세서리 → 체크한 옵션(IB) 맨 뒤", str([ln["key"] for ln in lines]))
check(lines[0]["desc"] == "TCV1\n- MASTER T.C (Payload 10Kg)" and lines[1]["desc"] == "TCV1\n- Tool side T.C (Payload 10Kg)"
      and lines[2]["desc"] == "PPM\n-Pogo Pin Male" and lines[0]["remark"] == "CONTROLBOX·4m 케이블 기본 포함"
      and lines[2]["remark"] == "포고핀 수, 8pin, 각 핀당 1A 전원 혹은 DIO로 사용",
      "품목 문구 = 최근 견적(가나테크) 표기 + 마스터 비고 'CONTROLBOX·4m 케이블 기본 포함'")
check(qx.total(lines) == 4_800_000, "합계 4.8M(마스터 2.3M + T.P 0.8M + PPM·PPF 1.0M + 케이블 0.2M + PMM·PMF 0.4M + IB 0.1M)", str(qx.total(lines)))
en_lines = qs.render_lines(st, "en", 1380)
check(en_lines[0]["remark"] == "Control box & 4m cable included" and en_lines[0]["unit_price"] == round(2_300_000 / 1380, 2)
      and en_lines[0]["unit_price_krw"] == 2_300_000, "영문(USD): 비고 영어, 원화 ÷ 환율(센트 반올림), 원화 단가도 남김")

form = {"customer": "가나테크", "to": "김 철 수 대표님", "contact_name": "홍길동", "contact_title": "이사",
        "contact_mobile": "010-0000-0000", "contact_email": "name@example.com"}
day = date(2026, 10, 6)
h = qx.quote_header(form, lang="ko", quote_no="S26-0182", day=day, pogo=True)
check(h["delivery"] == "통상 3 ~ 4주" and h["payment"] == "발주시 50%, 납품전 50%" and "고객사 수행 범위" in h["notes"][0],
      "빈 조건은 최근 견적 기본값, 포고핀이 있으면 배선 범위 비고 자동(C17)")
try:
    qx.quote_header({**form, "contact_email": ""}, lang="ko", quote_no="x", day=day, pogo=False)
    check(False, "필수 값 검사")
except qx.QuoteError as e:
    check("담당자 이메일" in str(e), "필수 값(고객사·담당자 이름·휴대폰·이메일)이 비면 거절")
en_form = pp.quote_form("en")
check(en_form["missing"] == [] and en_form["issuer"] is pp.QUOTE_ISSUER_EN,
      "영문 회사 정보 = 회사 프로필(quote_issuer_en)")

wb = openpyxl.load_workbook(io.BytesIO(qx.build_xlsx(h, lines)))
ws = wb.active
vals = {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}
total_cell = next(k for k, v in vals.items() if isinstance(v, str) and v.startswith("=SUM(F17:F"))
check(vals["E2"] == "S26-0182" and vals["A4"].startswith(pp.QUOTE_ISSUER["company"])
      and "담당자 : 홍 길 동 이사" in vals["A4"] and pp.QUOTE_ISSUER["company_en"] in vals.values()
      and vals["E6"] == pp.QUOTE_ISSUER["account_no"] and "가나테크" in vals["A9"],
      "머리: 견적번호, 위쪽 회사명(담당자 '홍 길 동 이사'), 아래쪽 영문 회사명, 계좌, 고객")
check(vals["F17"] == "=D17*E17" and vals["E17"] == 2_300_000 and vals["D17"] == 1 and total_cell == f"G{17 + len(lines)}"
      and vals[total_cell] == f"=SUM(F17:F{16 + len(lines)})" and wb.calculation.fullCalcOnLoad,
      "금액 = 수량×단가 수식, 합계 = SUM 수식(열 때 다시 계산)", str((vals.get("F17"), total_cell)))
check(len(ws._images) == 4 and ws.print_area and int(ws.page_setup.paperSize) == int(ws.PAPERSIZE_A4),
      "제목(양식의 WordArt 그림)·로고·서명·직인 그림 4개, A4 인쇄 영역", str(len(ws._images)))
check(not any(c.font and c.font.name == "Book Antiqua" for row in ws.iter_rows() for c in row),
      "제목을 글꼴(Book Antiqua)로 쓰지 않음 — 서버 PDF 에서 다른 글꼴로 바뀌어 보였다")

he = qx.quote_header({**form, "fx_rate": 1380}, lang="en", quote_no="S26-0183", day=day, pogo=True)
we = openpyxl.load_workbook(io.BytesIO(qx.build_xlsx(he, en_lines))).active
ev = {c.value for row in we.iter_rows() for c in row if isinstance(c.value, str)}
check({"Delivery Schedule", "Payment Terms", "Total Amount", "Purchase Order Confirmation", pp.QUOTE_ISSUER_EN["company_en"],
       pp.QUOTE_ISSUER_EN["account_no"], pp.QUOTE_ISSUER_EN["account_holder"]} <= ev and not any("납기" in v or "부가세" in v for v in ev) and we.title == "Quotation"
      and we["E17"].number_format == '"$"#,##0.00' and he["currency"] == "USD" and he["fx_rate"] == 1380
      and "이사 홍길동" in we["A4"].value,
      "영문 양식: 같은 구성, 문구 영어, 달러 서식, 영문 계좌·예금주, 직함 앞")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
