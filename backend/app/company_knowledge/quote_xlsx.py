"""툴체인저 견적서 엑셀 — 최근 유엔디로보틱스 양식(docs/examples/quotes/(주)유엔디로보틱스_견적서_가나테크_magbot툴체인저_VT260930-2.pdf)을 따른다.

- 칸 너비·글꼴·숫자 서식은 회사 빈 양식(docs/examples/quotes/(주)유엔디_견적서.xlsx)에서, 배치·문구는 최근 견적(가나테크)에서 가져왔다.
  빈 양식의 'Quotation' 글씨는 WordArt 도형이라 openpyxl 로 옮길 수 없어 글자로 쓴다. 로고·서명·직인은 빈 양식 그림(data/quote).
- 한글·영문(해외) 양식은 같은 구성, 영문은 문구만 영어(사용자 2026-10-06). 위쪽 회사명, 아래쪽 서명란 'UND Robotics Co., Ltd'.
- 품목·머리는 견적서 작성(quote_session)에서 대화·직접 수정으로 채운 것. 단가는 회사 단가표 고객사가 — 국내는 원화,
  해외(영문)는 달러: 달러 단가표가 없어 원화 단가 ÷ 입력 환율(원/달러), 센트 단위 반올림.
- 최종 견적에 미정·0원을 넣지 않는다(v0.8 C13) — 발행 전 검사는 quote_session.problems.
- 금액 칸은 수식(수량×단가, 합계 SUM)이라 엑셀에서 수량을 고치면 다시 계산된다. 열 때 다시 계산하도록 표시한다.
"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from . import product_prices as pp
from .atc_selection import QUOTE_SCOPE_TEXT

ASSETS = Path(__file__).with_name("data") / "quote"
FONT = "맑은 고딕"
WON = '"₩"#,##0'
USD = '"$"#,##0.00'
# 통화 — 국내 원화(단가표 그대로), 해외 달러(사용자 2026-10-06). 달러 단가표가 없어 원화 단가 ÷ 입력 환율, 센트 반올림.
CURRENCY = {"ko": "KRW", "en": "USD"}
EA = '0" ea"'
COL_WIDTHS = {"A": 13.58, "B": 16.0, "C": 21.08, "D": 15.08, "E": 27.08, "F": 14.58, "G": 43.0}   # 빈 양식 그대로
_THIN = Side(style="thin", color="FF000000")
BOX = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
GREY = PatternFill("solid", fgColor="FFD9D9D9")
YELLOW = PatternFill("solid", fgColor="FFFFFF00")


class QuoteError(ValueError):
    """견적서를 만들 수 없는 이유(사람이 읽는 문장)."""


# ── 품목 문구 ─────────────────────────────────────────────────────────────────
# 최근 견적(가나테크) 표기: 'TCV1\n- MASTER T.C (Payload 10Kg)', 'PPM\n-Pogo Pin Male' + 비고 '포고핀 수, 8pin, …'
_REMARK = {
    "ko": {"PPM": "포고핀 수, 8pin, 각 핀당 1A 전원 혹은 DIO로 사용", "PPF": "포고핀 암, 8pin, 각 핀당 1A 전원 혹은 DIO로 사용",
           "PMM": "공압커플러 수, 2 hole", "PMF": "공압커플러 암, 2 hole", "Cable 0.3m": "포고핀 모듈(PPM·PPF) 1개당 1EA",
           "IB": "로봇 플랜지–툴체인저 마스터 연결 브라켓", "MASTER T.C": "CONTROLBOX·4m 케이블 기본 포함"},
    "en": {"PPM": "Pogo pin male, 8 pins, 1A per pin for power or DIO",
           "PPF": "Pogo pin female, 8 pins, 1A per pin for power or DIO",
           "PMM": "Pneumatic coupler male, 2 holes", "PMF": "Pneumatic coupler female, 2 holes",
           "Cable 0.3m": "1 EA per pogo pin module (PPM/PPF)", "IB": "Bracket between robot flange and tool changer master",
           "MASTER T.C": "Control box & 4m cable included"},
}
_DESC = {"PPM": "PPM\n-Pogo Pin Male", "PPF": "PPF\n-Pogo Pin Female", "PMM": "PMM\n-PneuMatic Male",
         "PMF": "PMF\n-PneuMatic Female", "Cable 0.3m": "Cable 0.3m(meter)\n- Pogo Pin Cable", "IB": "IB\n- Interface Bracket"}
# 자동 비고 — 포고핀을 공급하면 배선 범위 안내(v0.8 C17 quote_scope_text_ko)
_SCOPE = {"ko": QUOTE_SCOPE_TEXT,
          "en": "Pogo pin extension wiring and gripper wiring are within the customer's scope. "
                "Please contact us separately if UND is requested to perform them."}
DEFAULT_TERMS = {
    "ko": {"comments": "www.magbot.kr\n- Auto Tool Changer 공급 건\n맥봇 로봇 툴체인저", "delivery": "통상 3 ~ 4주",
           "place": "직납 및 배차 비용 별도\n(화물 비용 별도)", "payment": "발주시 50%, 납품전 50%"},
    "en": {"comments": "www.magbot.kr\n- Auto Tool Changer Supply\nMagbot Robot Tool Changer",
           "delivery": "Usually 3 ~ 4 weeks", "place": "Direct delivery & dispatch costs excluded\n(Freight costs excluded)",
           "payment": "50% upon order, 50% before delivery"},
}


def _key(it: dict[str, Any]) -> str:
    """구성품 줄 → 문구 키(MASTER T.C / T.P / PPM / PPF / PMM / PMF / Cable 0.3m / IB)."""
    if it.get("code") in ("PPM", "PPF", "PMM", "PMF"):
        return it["code"]
    return it.get("price_item") or it.get("name") or ""


def quote_header(form: dict[str, Any], *, lang: str, quote_no: str, day: date, pogo: bool) -> dict[str, Any]:
    """화면 입력(고객·담당자·조건) + 회사 양식 → 견적서 머리. 필수 값이 비면 QuoteError."""
    f = pp.quote_form(lang)
    if f["missing"]:
        names = {"address": "영문 주소", "account_no": "해외 송금 계좌번호", "account_holder": "예금주(영문)"}
        raise QuoteError("영문 견적서에 필요한 회사 정보가 아직 등록되지 않았습니다: "
                         + ", ".join(names.get(m, m) for m in f["missing"]))
    need = {"customer": "고객사", "contact_name": "담당자 이름", "contact_mobile": "담당자 휴대폰", "contact_email": "담당자 이메일"}
    miss = [lab for k, lab in need.items() if not str(form.get(k) or "").strip()]
    if miss:
        raise QuoteError("견적서에 꼭 필요한 값이 비어 있습니다: " + ", ".join(miss))
    d = DEFAULT_TERMS[lang]
    notes = [x for x in (str(form.get("note") or "").strip(), _SCOPE[lang] if pogo else "") if x]
    # 입금 계좌 — 견적에서 바꾼 값, 비어 있으면 회사 기본 계좌(사용자 2026-10-08)
    issuer = {**f["issuer"], **{k: str(form[k]).strip() for k in ("bank", "account_no", "account_holder") if str(form.get(k) or "").strip()}}
    return {"lang": lang, "quote_no": quote_no, "date": day.isoformat(), "issuer": issuer, "terms": f["terms"],
            "currency": CURRENCY[lang], "fx_rate": form.get("fx_rate") if lang == "en" else None,
            "labels": f["labels"], "customer": str(form["customer"]).strip(), "to": str(form.get("to") or "").strip(),
            "cc": str(form.get("cc") or "").strip(),
            "contact": {k: str(form.get(f"contact_{k}") or "").strip() for k in ("name", "title", "mobile", "email")},
            "comments": str(form.get("comments") or "").strip() or d["comments"],
            "delivery": str(form.get("delivery") or "").strip() or d["delivery"],
            "place": str(form.get("place") or "").strip() or d["place"],
            "payment": str(form.get("payment") or "").strip() or d["payment"],
            "notes": notes}


# ── 엑셀 ─────────────────────────────────────────────────────────────────────

def _cell(ws, ref: str, value: Any = None, *, size: float = 10, bold: bool = True, h: str | None = "center",
          v: str = "center", wrap: bool = True, fmt: str | None = None, fill: PatternFill | None = None,
          italic: bool = False, merge: str | None = None, border: bool = True) -> None:
    if merge:
        ws.merge_cells(f"{ref}:{merge}")
        if border:                                       # 병합 영역 테두리는 칸마다 줘야 인쇄에 보인다
            for row in ws[f"{ref}:{merge}"]:
                for c in row:
                    c.border = BOX
                    if fill:
                        c.fill = fill
    c = ws[ref]
    c.value = value
    c.font = Font(name=FONT, size=size, bold=bold, italic=italic)
    c.alignment = Alignment(horizontal=h, vertical=v, wrap_text=wrap)
    if border:
        c.border = BOX
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill


def _image(ws, name: str, anchor: str, height_px: int) -> None:
    img = XLImage(str(ASSETS / name))
    ratio = height_px / img.height
    img.width, img.height = int(img.width * ratio), height_px
    ws.add_image(img, anchor)


def _image_at(ws, name: str, *, col: int, row: int, col_off_px: int, row_off_px: int, width_px: int | None = None,
              height_px: int) -> None:
    """셀 왼쪽 위에서 화소만큼 띄워 그림을 놓는다(양식의 그림 위치를 그대로 옮길 때). width 를 안 주면 비율 유지."""
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
    from openpyxl.drawing.xdr import XDRPositiveSize2D
    from openpyxl.utils.units import pixels_to_EMU

    img = XLImage(str(ASSETS / name))
    w = width_px or round(img.width * height_px / img.height)
    img.width, img.height = w, height_px
    img.anchor = OneCellAnchor(_from=AnchorMarker(col=col, colOff=pixels_to_EMU(col_off_px), row=row,
                                                  rowOff=pixels_to_EMU(row_off_px)),
                               ext=XDRPositiveSize2D(pixels_to_EMU(w), pixels_to_EMU(height_px)))
    ws.add_image(img)


def _spaced(name: str) -> str:
    """'홍길동' → '홍 길 동' (최근 견적의 담당자 표기). 한글 이름일 때만."""
    return " ".join(name) if name and all("가" <= ch <= "힣" for ch in name) and len(name) <= 4 else name


def build_xlsx(header: dict[str, Any], lines: list[dict[str, Any]]) -> bytes:
    """견적서 한 장(A4 세로, A~G열) → xlsx 바이트."""
    en = header["lang"] == "en"
    lab, iss, terms, ct = header["labels"], header["issuer"], header["terms"], header["contact"]
    money = USD if header.get("currency") == "USD" else WON
    wb = Workbook()
    ws = wb.active
    ws.title = "Quotation" if en else "견적서"
    for col, w in COL_WIDTHS.items():
        ws.column_dimensions[col].width = w
    ws.sheet_view.showGridLines = False

    # 1~2행: 제목·로고 / 견적일·견적번호
    ws.row_dimensions[1].height = 62.5
    # 제목 — 양식의 WordArt('Quotation', 흰 글자·검은 테두리·회색 그림자)를 회사 견적서 PDF 에서 잘라 낸 그림으로,
    # 양식과 같은 자리(D열 +28px ~ F열 끝, 1행 위에서 11px)에. 글꼴로 쓰면 서버 PDF 에서 다른 글꼴로 바뀌어 보였다.
    _image_at(ws, "title.png", col=3, row=0, col_off_px=28, row_off_px=11, width_px=384, height_px=41)
    # 로고 — 양식처럼 제목 바로 오른쪽(G열)에, 회사 견적서 PDF 의 로고 칸 그대로(제목 대비 너비 142/174.6, 가로:세로 5.48).
    # logo_quote.png = logo.png 의 위아래 빈 여백을 양식처럼 잘라 낸 것(원본 그림은 글자 위 31%·아래 22% 가 비어 작게 보였다)
    _image_at(ws, "logo_quote.png", col=6, row=0, col_off_px=4, row_off_px=12, width_px=312, height_px=57)
    ws.row_dimensions[2].height = 18.75
    _cell(ws, "A2", lab["date"])
    _cell(ws, "B2", date.fromisoformat(header["date"]), fmt='yyyy"-"mm"-"dd', merge="C2")
    _cell(ws, "D2", lab["no"])
    _cell(ws, "E2", header["quote_no"], merge="G2", fmt="@")

    # 3~7행: Quotation by(회사·담당자) / 은행
    _cell(ws, "A3", f" {lab['by']}", size=11, h="left", border=False, wrap=False)
    ws.row_dimensions[3].height = 22
    # 한글: '홍 길 동 이사'(이름 뒤 직함), 영문: 'Mr. Gildong Hong'(직함·호칭 앞) — 각 양식의 실제 견적 표기
    name = (f"{ct['title']} {ct['name']}" if en else f"{_spaced(ct['name'])} {ct['title']}").strip()
    if en:
        who = "\n".join(x for x in (iss["company"], iss["address"], f"Tel : {ct['mobile']}",
                                    f"{name} ({ct['email']})") if x)
    else:
        who = "\n".join((iss["company"], iss["address"], f"담당자 : {name}", f"Mobile : {ct['mobile']}", f"E-mail : {ct['email']}"))
    _cell(ws, "A4", who, merge="C7")
    for r, (k, val) in enumerate((("Bank", iss["bank"]), ("Address", iss.get("bank_address") or ""), ("Account No.", iss["account_no"]),
                                  ("Account Holder", iss["account_holder"])), start=4):
        ws.row_dimensions[r].height = 27
        _cell(ws, f"D{r}", k)
        _cell(ws, f"E{r}", val, h="left", merge=f"G{r}", fmt="@")

    # 8~14행: Customer / Comments
    _cell(ws, "A8", lab["customer"], size=11, h="left", border=False, wrap=False)
    ws.row_dimensions[8].height = 22
    cust = f"        {header['customer']}\n  To : {header['to']}{',' if header['to'] else ''}\n  CC : {header['cc']}"
    _cell(ws, "A9", cust, size=11, h="left", merge="C14")
    _cell(ws, "D9", lab["comments"], merge="D14")
    _cell(ws, "E9", header["comments"], size=12, merge="G14")
    for r in range(9, 15):
        ws.row_dimensions[r].height = 15
    ws.row_dimensions[14].height = 40

    # 15~16행: 표 머리
    _cell(ws, "A15", lab["goods"], h="left", border=False, wrap=False)
    if not en:
        _cell(ws, "G15", terms["vat"], border=False)
    _cell(ws, "A16", lab["item"], size=9, merge="C16")
    for col, key in (("D", "qty"), ("E", "price"), ("F", "amount"), ("G", "remark")):
        _cell(ws, f"{col}16", lab[key], size=9)

    # 품목
    r = 17
    for ln in lines:
        ws.row_dimensions[r].height = 31
        _cell(ws, f"A{r}", ln["desc"], size=9, merge=f"C{r}")
        _cell(ws, f"D{r}", ln["qty"], size=9, fmt=f'0" {ln["unit"]}"' if ln.get("unit") else EA)
        _cell(ws, f"E{r}", ln["unit_price"], size=9, fmt=money)
        _cell(ws, f"F{r}", f"=D{r}*E{r}", size=9, fmt=money)
        _cell(ws, f"G{r}", ln["remark"], size=9)
        r += 1
    last = r - 1
    ws.row_dimensions[r].height = 24
    _cell(ws, f"A{r}", lab["total"], fill=YELLOW, merge=f"F{r}")
    _cell(ws, f"G{r}", f"=SUM(F17:F{last})", fmt=money, h="right", fill=YELLOW)
    r += 1

    # 조건 · 비고 · 발주 확인
    rows = ((lab["delivery"], header["delivery"], lab["place"], header["place"]),
            (lab["validity"], terms["validity"], lab["payment"], header["payment"]))
    for a_lab, a_val, f_lab, f_val in rows:
        ws.row_dimensions[r].height = 34.4
        _cell(ws, f"A{r}", a_lab, fill=GREY)
        _cell(ws, f"B{r}", a_val, h="left", merge=f"E{r}")
        _cell(ws, f"F{r}", f_lab, fill=GREY)
        _cell(ws, f"G{r}", f_val, h="left")
        r += 1
    ws.row_dimensions[r].height = max(34.4, 17 * (len(header["notes"]) + 1))
    _cell(ws, f"A{r}", lab["note"], fill=GREY)
    _cell(ws, f"B{r}", "\n".join(header["notes"]), h="left", merge=f"G{r}", size=9)
    r += 1
    ws.row_dimensions[r].height = 100.4
    _cell(ws, f"A{r}", lab["order"], fill=GREY)
    if en:
        order = (f"{terms['order_note']}\n\n{terms['order_accept']}\n\n\n"
                 + " " * 120 + "Purchaser Name :  _______________________ (Signature)")
    else:
        order = ("※ 발주 시 서명 날인하여  담당자 이메일을 통해 발신해주시기 바랍니다.\n\n당사는 이 견적서 상의 가격 및 조건들을 수용하고 "
                 "이 견적서를 귀사에 대한 공식 발주서로 대신하고자 합니다.\n\n\n" + " " * 120 + "발주자명 :  _______________________ (서명)")
    _cell(ws, f"B{r}", order, h="left", v="top", merge=f"G{r}")
    r += 1
    ws.row_dimensions[r].height = 20
    _cell(ws, f"A{r}", terms["option_note"], size=9, merge=f"G{r}")
    r += 1

    # 서명란 — 아래쪽 회사 표기는 UND Robotics Co., Ltd(사용자 2026-10-06)
    _cell(ws, f"E{r}", f"  {iss['signed_by']}", size=9, h="left", merge=f"G{r}", border=False)
    for c in ws[f"E{r}:G{r}"][0]:
        c.border = Border(bottom=_THIN)
    r += 1
    _cell(ws, f"E{r}", iss["company_en"], size=14, italic=True, h="left", v="top", merge=f"G{r}", border=False)
    ws[f"E{r}"].alignment = Alignment(horizontal="left", vertical="top", indent=5)   # 회사 견적서처럼 서명 위 가운데쯤에서 시작
    ws.row_dimensions[r].height = 24
    # 서명 — 회사 견적서 PDF 실측(약 100×37pt, 양식처럼 가로로 늘린 칸), 직인은 서명 끝에 겹쳐 F열 시작에
    _image_at(ws, "signature.png", col=4, row=r, col_off_px=5, row_off_px=0, width_px=226, height_px=83)
    _image(ws, "seal.png", f"F{r + 1}", 62)
    for k in range(r + 1, r + 5):
        ws.row_dimensions[k].height = 16
    r += 4
    for c in ws[f"E{r}:G{r}"][0]:
        c.border = Border(bottom=_THIN)
    r += 2
    _cell(ws, f"A{r}", "THANK YOU FOR YOUR BUSINESS!", size=10, merge=f"G{r}", border=False)

    # 인쇄: A4 세로 한 장 폭, 가운데
    ws.print_area = f"A1:G{r}"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.59
    ws.page_margins.top, ws.page_margins.bottom = 0.75, 0.6
    wb.calculation.fullCalcOnLoad = True
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def total(lines: list[dict[str, Any]]) -> float:
    return round(sum(ln["amount"] for ln in lines), 2)


# ── PDF(미리보기·PDF 내려받기) ────────────────────────────────────────────────
# 같은 엑셀을 LibreOffice(헤드리스 Calc)로 변환한다 — 화면 미리보기와 PDF 가 엑셀과 어긋나지 않게(사용자 2026-10-06).
# 동시에 여러 변환이 돌아도 서로의 설정을 잠그지 않도록 호출마다 임시 프로필을 쓴다.
PDF_TIMEOUT_S = 90


async def to_pdf(xlsx: bytes) -> bytes:
    """엑셀 바이트 → PDF 바이트. 변환기가 없거나 실패하면 QuoteError."""
    import asyncio
    import shutil
    import tempfile

    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise QuoteError("PDF 변환기(LibreOffice)가 서버에 없습니다. 엑셀로 받아 주세요.")
    with tempfile.TemporaryDirectory(prefix="quote_pdf_") as tmp:
        src = Path(tmp) / "quote.xlsx"
        src.write_bytes(xlsx)
        proc = await asyncio.create_subprocess_exec(
            exe, f"-env:UserInstallation=file://{tmp}/profile", "--headless", "--norestore",
            "--convert-to", "pdf", "--outdir", tmp, str(src),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), PDF_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            raise QuoteError("PDF 변환이 너무 오래 걸립니다. 잠시 뒤 다시 시도하거나 엑셀로 받아 주세요.") from None
        out = Path(tmp) / "quote.pdf"
        if proc.returncode != 0 or not out.exists():
            raise QuoteError(f"PDF 로 바꾸지 못했습니다: {err.decode('utf-8', 'replace')[:200]}")
        return out.read_bytes()
