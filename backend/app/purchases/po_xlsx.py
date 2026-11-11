# -*- coding: utf-8 -*-
"""발주서(Purchasing order) 엑셀 — 회사 발주서 양식을 그대로 따른다(사용자 2026-11-02). 국내 _build_ko · 해외 _build_en.

해외(사용자 2026-11-11): LHTC 해외 발주서(유엔디로보틱스_LHTC_Purchasing_Lu20261008.xlsx)를 기준으로, TOTAL 색·Commodity/Description
두 칸은 국내 발주서와 같게.

기준: docs/examples/quotes/(쥬)유엔디로보틱스_발주서_크레켄_Lu260808 (1).xlsx (칸 너비·행 높이·병합·글꼴·테두리)
      + 같은 양식의 최근 PDF(나우티앤에스 Lu261013 — 오른쪽 위 magbot 로고).
- 'Purchasing order' 제목은 WordArt 도형이라 openpyxl 로 옮길 수 없어 PDF 에서 잘라 낸 그림(data/po_title.png)으로 넣는다.
- 서명·직인·로고는 견적서와 같은 회사 그림(company_knowledge/data/quote) — 양식의 그림과 같은 파일이다.
- 금액 칸은 수식(수량×단가, 합계 SUM)이라 엑셀에서 수량을 고치면 다시 계산된다. 금액은 VAT 별도(양식 그대로).
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils.units import pixels_to_EMU

QUOTE_ASSETS = Path(__file__).resolve().parent.parent / "company_knowledge" / "data" / "quote"
TITLE_IMG = Path(__file__).with_name("data") / "po_title.png"
FONT = "맑은 고딕"
WON = '"₩"#,##0_);[Red]\\("₩"#,##0\\)'                     # 양식 금액 서식 그대로
COL_WIDTHS = {"A": 12.5, "B": 3.63, "C": 37.0, "D": 13.5, "E": 10.13, "F": 11.13, "G": 11.75}
_THIN = Side(style="thin", color="FF000000")
GREY = PatternFill("solid", fgColor="FFF2F2F2")            # TOTAL 줄(테마 흰색 -5%)
RED = "FFFF0000"
MAX_ITEMS = 30
# 원본 PDF 는 엑셀(맑은 고딕 기본 글꼴 — 칸이 넓어 쪽 맞춤 약 84%)로 인쇄한 것, 서버 LibreOffice(나눔고딕)는 약 97% 로 인쇄한다.
# 가로 글자 폭은 두 글꼴 차이로 원본과 거의 같게 나오지만 행 높이는 그만큼 길어져(렌더 비교 2026-11-02: 세로 약 9% 김)
# 행 높이에 이 배율을 곱해 종이 위 세로 배치를 원본과 맞춘다.
ROW_SCALE = 0.91
SIGNED_BY = "  Signed by President / Mr.Lee Cheol-Su"
DEFAULT_FAX = "82-54-462-5468"


class POError(ValueError):
    """발주서를 만들 수 없는 이유(사람이 읽는 문장)."""


def supplier_text(h: dict[str, Any]) -> str:
    """Supplier 칸 — 공급사·담당자(호칭 포함)·Tel·이메일. 빈 줄은 뺀다(에이딘로보틱스 예시: '강종구 팀장', 'Tel : 010-…')."""
    s = h.get("supplier") or {}
    lines = [str(s.get("name") or "").strip(), str(s.get("contact") or "").strip()]
    if str(s.get("tel") or "").strip():
        lines.append(f"Tel : {s['tel'].strip()}")
    if str(s.get("email") or "").strip():
        lines.append(f"e-mail : {s['email'].strip()}")
    return "\n".join(x for x in lines if x)


def issuer_lines(h: dict[str, Any]) -> list[str]:
    """Issued the order by 칸 — 첫 줄 '㈜ 유엔디로보틱스 권성하 이사'(이름 붙여 씀 — 에이딘로보틱스 예시), 다음 주소·Tel·Fax·ATTN."""
    i = h.get("issuer") or {}
    first = " ".join(x for x in ("㈜ 유엔디로보틱스", str(i.get("name") or "").strip(), str(i.get("title") or "").strip()) if x)
    rest = [str(i.get("address") or "").strip(),
            f"Tel : {i['tel'].strip()}" if str(i.get("tel") or "").strip() else "",
            f"Fax : {i['fax'].strip()}" if str(i.get("fax") or "").strip() else "",
            f"ATTN : {i['attn'].strip()}" if str(i.get("attn") or "").strip() else ""]
    return [first, *[x for x in rest if x]]


def nlines(*vals: Any) -> int:
    return max([len(str(v or "").split("\n")) for v in vals] or [1])


def line_amount(it: dict[str, Any], currency: str = "KRW") -> float:
    """줄 금액 — 원화는 원 단위, 달러는 센트 단위 반올림."""
    v = float(it.get("qty") or 0) * float(it.get("price") or 0)
    return float(round(v, 2) if currency == "USD" else round(v))


def total(items: list[dict[str, Any]], currency: str = "KRW") -> float:
    return float(round(sum(line_amount(it, currency) for it in items), 2))


def check(header: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    """발행 전 검사 — 빠진 값 목록(비면 발행 가능)."""
    miss = []
    if not str((header.get("supplier") or {}).get("name") or "").strip():
        miss.append("공급사")
    if not str((header.get("issuer") or {}).get("name") or "").strip():
        miss.append("발주 담당자 이름")
    return miss + check_items(items)


def item_name(it: dict[str, Any]) -> str:
    """품목 한 줄 이름 — Commodity(모델명), 없으면 Description 첫 줄(예전 발주서는 한 칸에 같이 적었다)."""
    return str(it.get("commodity") or "").strip() or str(it.get("description") or "").strip().split("\n")[0]


def check_items(items: list[dict[str, Any]]) -> list[str]:
    """품목 검사 — 고친 발주서 파일로 발행할 때도 쓴다(머리 칸은 파일에 이미 있다)."""
    miss = []
    if not items:
        miss.append("품목")
    for n, it in enumerate(items, 1):
        if not item_name(it):
            miss.append(f"{n}번 품목 내용")
        if not it.get("qty") or float(it["qty"]) <= 0:
            miss.append(f"{n}번 품목 수량")
        if it.get("price") is None or float(it["price"]) < 0:
            miss.append(f"{n}번 품목 단가")
    if len(items) > MAX_ITEMS:
        miss.append(f"품목은 {MAX_ITEMS}개까지")
    return miss


def _cell(ws, ref: str, value: Any = None, *, size: float = 10, bold: bool = True, h: str | None = "center",
          v: str = "center", wrap: bool = False, fmt: str | None = None, color: str | None = None, italic: bool = False,
          merge: str | None = None, border: bool = True, fill: PatternFill | None = None) -> None:
    if merge:
        ws.merge_cells(f"{ref}:{merge}")
    cells = [c for row in ws[f"{ref}:{merge or ref}"] for c in row]
    if border:                                     # 병합 영역은 칸마다 바깥 테두리를 줘야 인쇄에 보인다
        r0, r1 = cells[0].row, cells[-1].row
        c0, c1 = cells[0].column, cells[-1].column
        for c in cells:
            c.border = Border(left=_THIN if c.column == c0 else None, right=_THIN if c.column == c1 else None,
                              top=_THIN if c.row == r0 else None, bottom=_THIN if c.row == r1 else None)
    if fill:
        for c in cells:
            c.fill = fill
    c = ws[ref]
    c.value = value
    c.font = Font(name=FONT, size=size, bold=bold, italic=italic, color=color)
    c.alignment = Alignment(horizontal=h, vertical=v, wrap_text=wrap)
    if fmt:
        c.number_format = fmt


def _height(ws, row: int, pt: float) -> None:
    ws.row_dimensions[row].height = round(pt * ROW_SCALE, 2)


def _picture(ws, path: Path, *, col: int, row: int, dx: int, dy: int, h: int, w: int | None = None) -> None:
    """셀 왼쪽 위에서 화소만큼 띄워 그림을 놓는다(양식의 그림 위치 그대로). w 를 안 주면 비율 유지."""
    img = XLImage(str(path))
    w = w or round(img.width * h / img.height)
    img.width, img.height = w, h
    img.anchor = OneCellAnchor(_from=AnchorMarker(col=col, colOff=pixels_to_EMU(dx), row=row, rowOff=pixels_to_EMU(dy)),
                               ext=XDRPositiveSize2D(pixels_to_EMU(w), pixels_to_EMU(h)))
    ws.add_image(img)


def build_xlsx(header: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    """발주서 엑셀 — header.lang 'en' 이면 해외(Purchasing order, 달러), 아니면 국내 양식."""
    miss = check(header, items)
    if miss:
        raise POError("발주서에 꼭 필요한 값이 비어 있습니다: " + ", ".join(miss))
    return _build_en(header, items) if header.get("lang") == "en" else _build_ko(header, items)


def _build_ko(header: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    """국내 발주서 머리·품목 → 엑셀 바이트. header = {no, date(YYYY-MM-DD), supplier{name,contact,email}, bank, address,
    account_no, account_holder, issuer{name,title,address,tel,fax,attn}, comments}. items = [{commodity, description, moq, qty, price}]."""
    import datetime as dt

    wb = Workbook()
    ws = wb.active
    ws.title = "발주서"
    ws.sheet_view.showGridLines = False
    for k, w in COL_WIDTHS.items():
        ws.column_dimensions[k].width = w
    for r, hgt in {1: 49.5, 2: 18.75, 3: 18.75, 4: 18.75, 5: 13.5, 6: 16.5, 7: 15, 8: 15, 9: 15, 10: 17, 11: 24.75,
                   12: 15, 13: 15, 14: 15, 15: 15, 16: 15, 17: 27, 18: 27, 19: 23.25, 20: 16.5}.items():
        _height(ws, r, hgt)

    # 1행 — 제목(WordArt 그림)·로고
    _picture(ws, TITLE_IMG, col=1, row=0, dx=22, dy=6, w=410, h=49)
    _picture(ws, QUOTE_ASSETS / "logo_quote.png", col=5, row=0, dx=8, dy=17, h=27)

    # 2행 — 발주일·발주번호
    _cell(ws, "A2", "Order Date")
    _cell(ws, "B2", dt.date.fromisoformat(header["date"]), merge="C2", fmt='yyyy"-"mm"-"dd;@')
    _cell(ws, "D2", "Order No.")
    _cell(ws, "E2", header.get("no") or "", merge="G2")

    # 3~10행 — 공급사 · 공급사 계좌
    _cell(ws, "A3", "Supplier and Shipper by", h="left", border=False)
    _cell(ws, "A4", supplier_text(header), size=14, wrap=True, merge="C10")
    for top, bot, label, key, h in ((4, 5, "Bank", "bank", "left"), (6, 7, "Address", "address", "center"),
                                    (8, 9, "Account No.", "account_no", "left")):
        _cell(ws, f"D{top}", label, merge=f"D{bot}")
        _cell(ws, f"E{top}", str(header.get(key) or "").strip() or (" -" if key == "address" else ""), h=h, wrap=True,
              merge=f"G{bot}")
    _cell(ws, "D10", "Account Holder")
    _cell(ws, "E10", str(header.get("account_holder") or "").strip(), h="left", merge="G10")

    # 11~17행 — 발주처(우리) · Comments. 첫 줄(회사·담당자)만 크게 — 양식 PDF 표기
    _cell(ws, "A11", "Issued the order by", size=11, h="left", border=False)
    lines = issuer_lines(header)
    rich = CellRichText([TextBlock(InlineFont(rFont=FONT, sz=13, b=True), lines[0])]
                        + [TextBlock(InlineFont(rFont=FONT, sz=10, b=True), "\n" + x) for x in lines[1:]])
    _cell(ws, "A12", None, size=11, wrap=True, merge="C17")
    ws["A12"].value = rich
    _cell(ws, "D12", "Comments", merge="D17")
    _cell(ws, "E12", str(header.get("comments") or "").strip(), wrap=True, merge="G17")

    # 19행~ — 품목 표
    _cell(ws, "A19", "Goods Description & Details", size=11, h="left", border=False)
    _cell(ws, "G19", "VAT 별도", border=False)
    # Commodity(모델명) · Description(설명) 칸을 나눈다(사용자 2026-11-11, 에이딘로보틱스 발주서 예시)
    _cell(ws, "A20", "Commodity", size=9)
    _cell(ws, "B20", "Description", size=9, merge="C20")
    for ref, lab in (("D20", "MOQ"), ("E20", "Q'ty (pcs)"), ("F20", "Price"), ("G20", "Amount")):
        _cell(ws, ref, lab, size=9)
    r = 21
    for it in items:
        com, desc = str(it.get("commodity") or "").strip(), str(it.get("description") or "").strip()
        n = nlines(com, desc)
        _height(ws, r, 16.5 if n == 1 else n * 15.0 + 6)
        _cell(ws, f"A{r}", com, size=9, wrap="\n" in com)
        if "\n" not in com:                           # 한 줄 모델명은 칸 폭에 맞춰 줄인다(서버 글꼴에서 'AFT2000-D80-C' 가 넘쳐 잘림)
            ws[f"A{r}"].alignment = Alignment(horizontal="center", vertical="center", shrink_to_fit=True)
        _cell(ws, f"B{r}", desc, size=9, wrap=True, merge=f"C{r}")
        moq = it.get("moq")
        _cell(ws, f"D{r}", (int(moq) if float(moq).is_integer() else float(moq)) if moq not in (None, "") else None, size=9)
        q = float(it["qty"])
        _cell(ws, f"E{r}", int(q) if q.is_integer() else q, size=9)
        _cell(ws, f"F{r}", float(it["price"]), size=9, fmt=WON)
        _cell(ws, f"G{r}", f"=PRODUCT(E{r}:F{r})", size=9, fmt=WON)
        r += 1
    first, last = 21, r - 1
    _height(ws, r, 15.65)
    _cell(ws, f"A{r}", "TOTAL", size=9, color=RED, merge=f"F{r}", fill=GREY)
    _cell(ws, f"G{r}", f"=SUM(G{first}:G{last})", size=9, color=RED, fmt=WON, fill=GREY)

    # 서명란 — Signed by(아래 줄) · UND Robotics · 서명·직인 · 서명 줄 · 인사말
    s = r + 1
    for k in range(9):
        _height(ws, s + k, 33 if k == 7 else 17)
    _cell(ws, f"E{s}", SIGNED_BY, size=9, h="left", border=False)
    for col in "EFG":
        ws[f"{col}{s}"].border = Border(bottom=_THIN)
    _cell(ws, f"E{s + 1}", "UND Robotics", size=14, italic=True, v="top", merge=f"F{s + 6}", border=False)
    # 서명·직인 — 양식 그림 위치 그대로(UND Robotics 다음 줄에서 시작 — 양식 xdr row 24). 서명은 바탕이 투명한 그림(양식은 jpg 를 밝게 보정)
    _picture(ws, QUOTE_ASSETS / "signature.png", col=4, row=s + 1, dx=0, dy=6, w=250, h=83)
    _picture(ws, QUOTE_ASSETS / "seal.png", col=6, row=s + 1, dx=9, dy=21, w=63, h=55)
    for col in "EFG":                                 # 서명 줄 — 양식 30행 위 테두리
        ws[f"{col}{s + 7}"].border = Border(top=_THIN)
    _cell(ws, f"A{s + 8}", "THANK YOU FOR YOUR BUSINESS!", merge=f"G{s + 8}", border=False)

    ws.print_area = f"A1:G{s + 8}"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left, ws.page_margins.right, ws.page_margins.top, ws.page_margins.bottom = 0.65, 0.5, 0.75, 0.75
    wb.calculation.fullCalcOnLoad = True
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── 해외 발주서(사용자 2026-11-11) — docs/examples/quotes/유엔디로보틱스_LHTC_Purchasing_Lu20261008.xlsx 를 기준으로 ─────────────
# 칸 너비·행 구성·글꼴(공급사·계좌 값 14.5 굵은 기울임, 품목 14)·제목 그림·서명+직인 그림(data/po_en_*.png = 그 양식 속 그림)은 양식대로.
# 국내 발주서와 맞춘 것(사용자 지시): TOTAL 줄 회색 바탕·빨간 글씨, Commodity·Description 두 칸.
# 그 밖: 양식의 'Quotation Date / No.'(견적서에서 옮긴 흔적)는 'Order'로, 빈 품목 줄·회색 구분 줄은 넣지 않는다,
#        양식의 선 도형(Signed by 위·서명 아래)은 openpyxl 로 못 옮겨 칸 테두리로 같은 자리에 긋는다, 가로 한 쪽 맞춤.
EN_COL_WIDTHS = {"A": 22.18, "B": 22.0, "C": 24.88, "D": 18.0, "E": 23.29, "F": 18.88, "G": 4.18}
USD = '"US$"#,##0.00_);\\("US$"#,##0.00\\)'
EN_DATA = Path(__file__).with_name("data")
EN_COMPANY = "UND Robotics Co., Ltd."
EN_VALUE_CHARS = 28                                   # E:F 칸 한 줄에 들어가는 14.5pt 글자 수(행 높이 계산용, 렌더로 맞춤)


def supplier_text_en(h: dict[str, Any]) -> str:
    """Supplier 칸(해외) — 'Zhengzhou Linghang Robot Co.,Ltd.\\nTo : Keating (mail)\\nTel : (+86) …' 양식 표기."""
    s = h.get("supplier") or {}
    contact, email = str(s.get("contact") or "").strip(), str(s.get("email") or "").strip()
    to = f"{contact} ({email})" if contact and email else contact or email
    lines = [str(s.get("name") or "").strip(), f"To : {to}" if to else "",
             f"Tel : {s['tel'].strip()}" if str(s.get("tel") or "").strip() else ""]
    return "\n".join(x for x in lines if x)


def issuer_lines_en(h: dict[str, Any]) -> list[str]:
    """Issued the order by(해외) — 회사·주소(국가명 앞 줄바꿈)·Tel·'Mr. Alex Jeon (alex@unde.co.kr)', 줄마다 앞 한 칸(양식 그대로)."""
    i = h.get("issuer") or {}
    addr = str(i.get("address") or "").strip().replace(", Republic of Korea", ",\nRepublic of Korea")
    who = str(i.get("attn") or "").strip() or " ".join(x for x in (str(i.get("title") or "").strip(), str(i.get("name") or "").strip()) if x)
    lines = [EN_COMPANY, *addr.split("\n"), f"Tel : {i['tel'].strip()}" if str(i.get("tel") or "").strip() else "", who]
    return [f" {x.strip()}" for x in lines if x.strip()]


def _wrapped(text: str, per_line: int) -> int:
    """칸 폭에 맞춰 접힌 줄 수(대략) — 행 높이 계산용."""
    return sum(max(1, -(-len(x) // per_line)) for x in (text or "").split("\n")) or 1


def _build_en(header: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    import datetime as dt

    wb = Workbook()
    ws = wb.active
    ws.title = "Purchasing order"
    ws.sheet_view.showGridLines = False
    for k, w in EN_COL_WIDTHS.items():
        ws.column_dimensions[k].width = w
    for r, hgt in {1: 49.5, 2: 22.5, 3: 22.5, 9: 24, 10: 115.25, 11: 38.25, 12: 22.5}.items():
        ws.row_dimensions[r].height = hgt
    # 양식 그림 자리(두 칸 고정: B칸+99px ~ D칸+75px, 1행 안 58px) 그대로
    _picture(ws, EN_DATA / "po_en_title.png", col=1, row=0, dx=99, dy=0, w=314, h=58)

    _cell(ws, "A2", "Order Date :", h="left", v="top")
    _cell(ws, "B2", dt.date.fromisoformat(header["date"]), v="top", merge="C2", fmt="yyyy\\-mm\\-dd;@")
    _cell(ws, "D2", "Order No. :", v="top")
    _cell(ws, "E2", header.get("no") or "", h="left", v="top", merge="F2")
    _cell(ws, "A3", "Supplier and Shipper by", h="left", v="top", merge="G3", border=False)

    # 4~8행 — 공급사(왼쪽 큰 칸) · 계좌(오른쪽 다섯 줄, 값은 14.5 굵은 기울임). 값이 길면 그 줄만 높인다(양식도 내용 따라 높이가 다름)
    sup = supplier_text_en(header)
    _cell(ws, "A4", sup, size=14.5, italic=True, h="left", v="top", wrap=True, merge="C8")
    rows_h = []
    for r, label, key in ((4, "Bank", "bank"), (5, "Address", "address"), (6, "Swift Code", "swift"),
                          (7, "Account No.", "account_no"), (8, "Account Holder", "account_holder")):
        val = str(header.get(key) or "").strip()
        _cell(ws, f"D{r}", label, v="top")
        _cell(ws, f"E{r}", val, size=14.5, italic=True, h="left", wrap=True, merge=f"F{r}")
        rows_h.append(max(22.5, _wrapped(val, EN_VALUE_CHARS) * 19.0 + 6))
    need = nlines(sup) * 19.0 + 8                     # 왼쪽 공급사 글이 다 보이게 — 모자라면 마지막 줄을 늘린다
    if sum(rows_h) < need:
        rows_h[-1] += need - sum(rows_h)
    for k, hgt in enumerate(rows_h):
        ws.row_dimensions[4 + k].height = hgt

    _cell(ws, "A9", "Issued the order by", size=11, h="left", v="top", merge="G9", border=False)
    _cell(ws, "A10", "\n".join(issuer_lines_en(header)), wrap=True, merge="C10")
    _cell(ws, "D10", "Comments", v="top")
    _cell(ws, "E10", str(header.get("comments") or "").strip(), h="left", v="top", wrap=True, merge="F10")
    _cell(ws, "A11", "Goods Description & Details", h="left", merge="G11", border=False)

    # 품목 표 — Commodity · Description 두 칸(국내와 같게), Quantity · Unit Price · Amount · Remark
    for ref, lab in (("A12", "Commodity"), ("B12", "Description"), ("C12", "Quantity"), ("D12", "Unit Price"),
                     ("E12", "Amount"), ("F12", "Remark")):
        _cell(ws, ref, lab, size=14.5, italic=True)
    r = 13
    for it in items:
        com, desc = str(it.get("commodity") or "").strip(), str(it.get("description") or "").strip()
        remark = str(it.get("remark") or "").strip()
        n = max(_wrapped(com, 18), _wrapped(desc, 18), _wrapped(remark, 15))
        ws.row_dimensions[r].height = max(20.0, n * 19.0 + 4)
        _cell(ws, f"A{r}", com, size=14, wrap=True)
        _cell(ws, f"B{r}", desc, size=14, wrap=True)
        q = float(it["qty"])
        _cell(ws, f"C{r}", int(q) if q.is_integer() else q, size=14)
        _cell(ws, f"D{r}", float(it["price"]), size=14, fmt=USD)
        _cell(ws, f"E{r}", f"=C{r}*D{r}", size=14, fmt=USD)
        _cell(ws, f"F{r}", remark, size=14, wrap=True)
        r += 1
    # TOTAL — 국내 발주서처럼 회색 바탕·빨간 글씨(사용자 2026-11-11)
    ws.row_dimensions[r].height = 38.4
    _cell(ws, f"A{r}", "TOTAL", size=14.5, color=RED, merge=f"D{r}", fill=GREY)
    _cell(ws, f"E{r}", f"=SUM(E13:E{r - 1})", size=14.5, color=RED, fmt=USD, fill=GREY)
    _cell(ws, f"F{r}", None, size=14.5, fill=GREY)

    # 서명란 — 양식: 오른쪽 맞춤 'Signed by President / Mr.Lee Cheol-Su'(위·아래 선) · 'UND Robotics Co., Ltd' · 서명+직인 그림 · 아래 선
    s = r + 1
    for k, hgt in enumerate((20, 22, 18, 74, 10)):
        ws.row_dimensions[s + k].height = hgt
    _cell(ws, f"A{s + 1}", SIGNED_BY.strip(), size=11, italic=True, h="right", merge=f"F{s + 1}", border=False)
    _cell(ws, f"A{s + 2}", EN_COMPANY.rstrip("."), h="right", merge=f"F{s + 2}", border=False)
    for col in "EF":
        ws[f"{col}{s}"].border = Border(bottom=_THIN)          # Signed by 위 선
        ws[f"{col}{s + 3}"].border = Border(bottom=_THIN)      # 서명 아래 선
    # 그림 자리의 row 는 0부터 센다 — 엑셀 {s + 3}행(서명 줄) = row s + 2 (예전에 한 줄 밀려 인쇄 영역 밖으로 잘렸음)
    _picture(ws, EN_DATA / "po_en_sign.png", col=4, row=s + 2, dx=79, dy=4, w=217, h=87)

    ws.print_area = f"A1:G{s + 4}"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.7
    ws.page_margins.top = ws.page_margins.bottom = 0.75
    wb.calculation.fullCalcOnLoad = True
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def file_name(supplier: str, no: str, lang: str = "ko") -> str:
    """국내 '유엔디로보틱스_발주서_{공급사}_{발주번호}'(에이딘로보틱스 예시), 해외 '유엔디로보틱스_{공급사}_Purchasing_{발주번호}'(각 양식 파일명 관례).
    파일명에 못 쓰는 글자는 뺀다."""
    sup = re.sub(r'[\\/:*?"<>|\r\n]+', " ", supplier or "").strip()[:60] or "공급사"
    return f"유엔디로보틱스_{sup}_Purchasing_{no}" if lang == "en" else f"유엔디로보틱스_발주서_{sup}_{no}"
