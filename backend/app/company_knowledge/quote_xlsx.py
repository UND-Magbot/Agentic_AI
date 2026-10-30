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
# 해외 금액 = 해외 기준 견적서의 회계 서식('$' 왼쪽, 숫자 오른쪽). 센트가 없으면 '$ 1,000', 있으면 '$ 1,234.50'
_USD_ACC = '_-[$$-409]* #,##0{d}_ ;_-[$$-409]* \\-#,##0{d}\\ ;_-[$$-409]* "-"_ ;_-@_ '
USD_WHOLE, USD_CENTS = _USD_ACC.format(d=""), _USD_ACC.format(d=".00")
# 통화 — 국내 원화(단가표 그대로), 해외 달러(사용자 2026-10-06). 달러 단가표가 없어 원화 단가 ÷ 입력 환율, 센트 반올림.
CURRENCY = {"ko": "KRW", "en": "USD"}
EA = '0" ea"'
COL_WIDTHS = {"A": 13.58, "B": 16.0, "C": 21.08, "D": 15.08, "E": 27.08, "F": 14.58, "G": 43.0}   # 빈 양식 그대로
_THIN = Side(style="thin", color="FF000000")
BOX = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
GREY = PatternFill("solid", fgColor="FFD9D9D9")
YELLOW = PatternFill("solid", fgColor="FFFFFF00")
ORANGE = PatternFill("solid", fgColor="FFFFC000")       # 소계(Sub total) — 방역 로봇 견적서
NEGO_RED = "FFFF0000"                                     # NEGO 줄 글씨·금액


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
# 품목(Commodity) 기본값 — 최근 TCV1 회사 견적서 표기. 규격(Specifications)은 _DESC·모델 문구.
ATC_COMMODITY = "Magbot ATC"
ATC_ACC_COMMODITY = "Magbot ATC Accessory"
SERVICE_COMMODITY = "Service"
_ACC_KEYS = ("PPM", "PPF", "PMM", "PMF", "Cable 0.3m", "IB")
_DESC = {"PPM": "PPM\n-Pogo Pin Male", "PPF": "PPF\n-Pogo Pin Female", "PMM": "PMM\n-PneuMatic Male",
         "PMF": "PMF\n-PneuMatic Female", "Cable 0.3m": "Cable 0.3m(meter)\n- Pogo Pin Cable", "IB": "IB\n- Interface Bracket"}
# 해외 = 해외 기준 견적서 표기('- Payload: 5 kg', '- 1 Master Tool Changer', '- 2 Tool Plates', 사용자 2026-10-14)
_DESC_EN = {"PPM": "PPM\n- Pogo Pin Male", "PPF": "PPF\n- Pogo Pin Female", "PMM": "PMM\n- Pneumatic Male",
            "PMF": "PMF\n- Pneumatic Female", "Cable 0.3m": "Cable 0.3m\n- Pogo Pin Cable", "IB": "IB\n- Interface Bracket"}
ATC_DESC = {"ko": {"MASTER T.C": "- MASTER T.C", "T.P": "- Tool side T.C"},
            "en": {"MASTER T.C": "- Master Tool Changer", "T.P": "- Tool Plate"}}
# 자동 비고 — 포고핀을 공급하면 배선 범위 안내(v0.8 C17 quote_scope_text_ko)
_SCOPE = {"ko": QUOTE_SCOPE_TEXT,
          "en": "Pogo pin extension wiring and gripper wiring are within the customer's scope. "
                "Please contact us separately if UND is requested to perform them."}
# 해외 기본 문구 = 실제 해외 견적서(docs/examples/quotes — git 제외) 표기(사용자 2026-10-14):
# 공장 인도(Ex-work)·선금 100% 기준. 비고(note)는 언어를 바꿀 때 기본으로 채우고(quote_session), 비워 두면 비어 있다.
DEFAULT_TERMS = {
    "ko": {"comments": "www.magbot.kr\n- Auto Tool Changer 공급 건\n맥봇 로봇 툴체인저", "delivery": "통상 3 ~ 4주",
           "place": "직납 및 배차 비용 별도\n(화물 비용 별도)", "payment": "발주시 50%, 납품전 50%", "note": ""},
    "en": {"comments": "www.magbot.kr\nMagbot Robot Automatic Tool Changer",
           "delivery": "4 ~ 6 weeks after payment", "place": "UND Korea Factory",
           "payment": "100% advance payment before delivery. Shipment will be arranged after payment confirmation.",
           "note": "Ex-work UND Korea Factory"},
}


def intl_mobile(v: str) -> str:
    """해외 견적서 전화번호 — 국가번호 형식(해외 기준 견적서 'Tel : 82-10-1234-5678'). 010-… → 82-10-…, 이미 +/82 로 시작하면 그대로."""
    s = str(v or "").strip()
    if not s or s.startswith(("+", "82")) or not s.startswith("0"):
        return s
    return "82-" + s[1:]


def _key(it: dict[str, Any]) -> str:
    """구성품 줄 → 문구 키(MASTER T.C / T.P / PPM / PPF / PMM / PMF / Cable 0.3m / IB)."""
    if it.get("code") in ("PPM", "PPF", "PMM", "PMF"):
        return it["code"]
    return it.get("price_item") or it.get("name") or ""


def default_commodity(ln: dict[str, Any]) -> str:
    """견적 줄 → 품목 칸 기본값. 툴체인저 본체·액세서리·설치/물류만 정하고, 직접 넣은 항목은 비워 둔다(담당자가 적음)."""
    if ln.get("kind") in ("work", "freight"):
        return SERVICE_COMMODITY
    if ln.get("key") in ("MASTER T.C", "T.P"):
        return ATC_COMMODITY
    if ln.get("key") in _ACC_KEYS:
        return ATC_ACC_COMMODITY
    return ""


def commodity_of(ln: dict[str, Any]) -> str:
    """담당자가 고친 품목(빈 값 포함)이 있으면 그것, 없으면 기본값."""
    return ln["commodity"] if isinstance(ln.get("commodity"), str) else default_commodity(ln)


def _notes(v: Any) -> list[str]:
    """표 아래 주석 — 칸마다 하나(목록). 예전 값(줄바꿈으로 이은 글)은 줄마다 한 칸."""
    raw = v if isinstance(v, list) else str(v or "").splitlines()
    return [str(x).strip() for x in raw if str(x or "").strip()]


def quote_header(form: dict[str, Any], *, lang: str, quote_no: str, day: date, pogo: bool) -> dict[str, Any]:
    """화면 입력(고객·담당자·조건) + 회사 양식 → 견적서 머리. 필수 값이 비면 QuoteError."""
    f = pp.quote_form(lang)
    if f["missing"]:
        names = {"address": "영문 주소", "account_no": "해외 송금 계좌번호", "account_holder": "예금주(영문)"}
        raise QuoteError("영문 견적서에 필요한 회사 정보가 아직 등록되지 않았습니다: "
                         + ", ".join(names.get(m, m) for m in f["missing"]))
    en = lang == "en"
    # 무상 대여(사용자 2026-10-30) — 품목 단가·금액은 build_xlsx 가 '무상'으로. 물류비·설치비를 받으면 그 금액이 Total
    free = form.get("purpose") == "rental" and form.get("rental_fee") == "free"
    if not en and form.get("vat") == "included":
        # 부가세 포함 견적서(사용자 2026-10-28) — 표 위 'VAT 포함', Total 옆 '(부가세 포함)'. 단가는 render_lines 에서 × 1.1
        f = {**f, "terms": {**f["terms"], "vat": "VAT 포함"}, "labels": {**f["labels"], "total": "Total (부가세 포함)"}}
    # 해외 담당자 = 'Mr. Alex Jeon'(호칭 + 영문 이름, 직함 없음 — 해외 기준 견적서). 영문 이름이 비면 이름 칸
    name = (str(form.get("contact_name_en") or "").strip() if en else "") or str(form.get("contact_name") or "").strip()
    need = {"customer": "고객사", "contact_mobile": "담당자 휴대폰", "contact_email": "담당자 이메일"}
    miss = ([] if name else ["담당자 이름"]) + [lab for k, lab in need.items() if not str(form.get(k) or "").strip()]
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
            "contact": {"name": name, "email": str(form.get("contact_email") or "").strip(),
                        "title": str(form.get("contact_honorific" if en else "contact_title") or "").strip(),
                        "mobile": (intl_mobile if en else str.strip)(str(form.get("contact_mobile") or ""))},
            "comments": str(form.get("comments") or "").strip() or d["comments"],
            "delivery": str(form.get("delivery") or "").strip() or d["delivery"],
            "place": str(form.get("place") or "").strip() or d["place"],
            "payment": str(form.get("payment") or "").strip() or d["payment"],
            "notes": notes,
            # Total 아래 보조 문구·표 아래 주석(사용자 2026-10-13, 영업팀 요청 14번)
            "total_note": str(form.get("total_note") or "").strip(),
            "foot_notes": _notes(form.get("foot_notes")),
            # 대여 견적서(사용자 2026-10-22) — 계약 개월 수가 있으면 Total 옆에 월 납입금
            "rental_months": _months(form) if form.get("purpose") == "rental" and not free else None,
            # 무상 대여 — True 면 단가·금액 칸 '무상', Total 칸 '무상 대여 (N개월)'
            **({"rental_free": True, "free_months": _months(form)} if free else {})}


def _months(form: dict[str, Any]) -> int | None:
    try:
        m = int(form.get("rental_months") or 0)
    except (TypeError, ValueError):
        return None
    return m if m > 0 else None


def _times(ln: dict[str, Any]) -> str:
    """대여 견적서 줄의 개월 곱(단가 = 월 납입금, 사용자 2026-10-22) — '=D17*E17*12'. 판매·한 번만 내는 줄은 빈 글."""
    return f"*{int(ln['months'])}" if ln.get("months") else ""


def nlines(*vals: Any) -> int:
    """칸 글의 줄 수 중 가장 많은 것 — 담당자가 줄을 바꾼 칸(Alt+Enter, 사용자 2026-10-16)만큼 행을 높인다."""
    return max([str(v or "").count("\n") + 1 for v in vals] or [1])


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


def who_text(header: dict[str, Any]) -> str:
    """Quotation by 칸 — 회사·주소·담당자. 한글 '홍 길 동 이사'(이름 뒤 직함), 영문 'Mr. Gildong Hong'(호칭 앞) — 각 양식의 실제 견적 표기."""
    iss, ct, en = header["issuer"], header["contact"], header["lang"] == "en"
    name = (f"{ct['title']} {ct['name']}" if en else f"{_spaced(ct['name'])} {ct['title']}").strip()
    if en:
        # 해외 기준 견적서 배치 — 줄마다 앞에 한 칸, 주소는 국가명 앞에서 줄바꿈
        addr = iss["address"].replace(", Republic of Korea", ",\nRepublic of Korea")
        return "\n".join(f" {x}" for x in (iss["company"], *addr.split("\n"), f"Tel : {ct['mobile']}",
                                           f"{name} ({ct['email']})") if x.strip())
    return "\n".join((iss["company"], iss["address"], f"담당자 : {name}", f"Mobile : {ct['mobile']}", f"E-mail : {ct['email']}"))


def customer_text(header: dict[str, Any]) -> str:
    """Customer 칸 — 고객사·To·CC 를 줄마다 가운데 정렬(사용자 2026-10-22: 무조건 가운데)이라 들여쓰기 없이."""
    to = header["to"]
    return f"{header['customer']}\nTo : {to}{',' if to else ''}\nCC : {header['cc']}"


def foot_text(header: dict[str, Any]) -> list[str]:
    """표 아래 주석 칸들 — 회사 고정 문구 다음에 담당자 주석('* ' 붙임, 방역 로봇 견적서처럼)."""
    return [header["terms"]["option_note"],
            *[x if x.startswith(("*", "※")) else f"* {x}" for x in _notes(header.get("foot_notes"))]]


PHOTO_H_PX = 56          # Remark 칸 품목 사진 높이(작게, 사용자 2026-10-12)
PHOTO_MAX_W_PX = 96
REMARK_W_PX = round(COL_WIDTHS["G"] * 7 + 5)


def _photo_at(ws, data: bytes, *, row: int, has_text: bool) -> None:
    """Remark(G) 칸에 품목 사진 — 글이 있으면 오른쪽, 없으면 가운데. 행 높이는 부르는 쪽이 맞춘다."""
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
    from openpyxl.drawing.xdr import XDRPositiveSize2D
    from openpyxl.utils.units import pixels_to_EMU

    img = XLImage(io.BytesIO(data))
    h = PHOTO_H_PX
    w = min(PHOTO_MAX_W_PX, max(1, round(img.width * h / img.height)))
    if w == PHOTO_MAX_W_PX:                            # 가로로 긴 사진 — 폭에 맞추고 높이를 줄인다
        h = max(1, round(img.height * w / img.width))
    img.width, img.height = w, h
    x = REMARK_W_PX - w - 6 if has_text else (REMARK_W_PX - w) // 2
    img.anchor = OneCellAnchor(_from=AnchorMarker(col=6, colOff=pixels_to_EMU(x), row=row - 1, rowOff=pixels_to_EMU(4)),
                               ext=XDRPositiveSize2D(pixels_to_EMU(w), pixels_to_EMU(h)))
    ws.add_image(img)


def build_xlsx(header: dict[str, Any], lines: list[dict[str, Any]], photos: dict[str, bytes] | None = None) -> bytes:
    """견적서 한 장(A4 세로, A~G열) → xlsx 바이트. photos = {사진 키: 바이트} — 줄의 photo 가 있으면 Remark 칸에 작게."""
    en = header["lang"] == "en"
    # 예전에 발행한 판의 머리(header)엔 그때 항목명만 있다 — 나중에 생긴 항목명(품목·규격 등)은 지금 기본값으로 채운다.
    lab = {**pp.QUOTE_LABELS["en" if en else "ko"], **header["labels"]}
    iss, terms, ct = header["issuer"], header["terms"], header["contact"]
    if header.get("currency") == "USD":
        cents = any(round(float(ln.get(k) or 0) * 100) % 100 for ln in lines for k in ("unit_price", "amount"))
        money = USD_CENTS if cents else USD_WHOLE
    else:
        money = WON
    # 무상 대여(사용자 2026-10-30) — 단가·금액 칸은 값 0 그대로 두고 서식으로 '무상'(되읽기·식이 숫자로 남게)
    # 물류비·설치 인건비를 받으면(charged) 그 줄과 Total 은 금액, 아니면 Total 도 '무상 대여 (N개월)'·결제조건 '무상 대여'
    free = header.get("rental_free")
    item_money = ('"Free"' if en else '"무상"') if free else money
    charged = not free or any(ln.get("amount") for ln in lines if not ln.get("row"))
    if not charged:
        lab = {**lab, "total": "Total"}
        terms = {**terms, "vat": ""}
        header = {**header, "payment": "Free of charge (rental)" if en else "무상 대여"}
    wb = Workbook()
    ws = wb.active
    ws.title = "Quotation" if en else "견적서"
    # 해외는 A열 15.0 — 해외 기준 견적서처럼 'Delivery Schedule' 을 한 줄로(서버 PDF 실측: 14.5 까지는 두 줄로 꺾임)
    for col, w in {**COL_WIDTHS, **({"A": 15.0} if en else {})}.items():
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
    _cell(ws, "A4", who_text(header), merge="C7")
    for r, (k, val) in enumerate((("Bank", iss["bank"]), ("Address", iss.get("bank_address") or ""), ("Account No.", iss["account_no"]),
                                  ("Account Holder", iss["account_holder"])), start=4):
        ws.row_dimensions[r].height = 27
        _cell(ws, f"D{r}", k)
        _cell(ws, f"E{r}", val, h="left", merge=f"G{r}", fmt="@")

    # 8~14행: Customer / Comments
    _cell(ws, "A8", lab["customer"], size=11, h="left", border=False, wrap=False)
    ws.row_dimensions[8].height = 22
    # Comments 글자 크기 — 국내는 최근 견적(가나테크) 12pt, 해외는 해외 기준 견적서 14pt
    _cell(ws, "A9", customer_text(header), size=11, merge="C14")
    _cell(ws, "D9", lab["comments"], merge="D14")
    _cell(ws, "E9", header["comments"], size=14 if en else 12, merge="G14")
    for r in range(9, 15):
        ws.row_dimensions[r].height = 15
    ws.row_dimensions[14].height = 40

    # 15~16행: 표 머리
    _cell(ws, "A15", lab["goods"], h="left", border=False, wrap=False)
    if not en:
        _cell(ws, "G15", terms["vat"], border=False)
    _cell(ws, "A16", lab["commodity"], size=9)
    _cell(ws, "B16", lab["spec"], size=9, merge="C16")
    for col, key in (("D", "qty"), ("E", "price"), ("F", "amount"), ("G", "remark")):
        # 대여 견적서 — 단가 칸은 1대당 월 납입금(사용자 2026-10-22)
        text_ = f"{lab[key]}\n({'Monthly' if en else '월 납입금'})" if key == "price" and header.get("rental_months") else lab[key]
        _cell(ws, f"{col}16", text_, size=9)

    # 품목 — 줄 종류(row)별: 없음=품목, gap 묶음 구분, section 소제목, subtotal 소계, option 옵션(in_total 반영이면 소계·합계 포함),
    # spec 사양(항목명 / 내용), text 서술형 한 칸, nego 할인(음수). 합계는 품목·NEGO 칸만 더한다(사용자 2026-10-13).
    r = 17
    billed: list[str] = []
    block_cells: list[str] = []
    for ln in lines:
        kind = ln.get("row")
        if kind == "gap":
            ws.row_dimensions[r].height = 13                  # 방역 로봇 견적서의 회색 구분줄 두께
            _cell(ws, f"A{r}", None, fill=GREY, merge=f"G{r}")
            r += 1
            continue
        if kind == "section":
            ws.row_dimensions[r].height = max(22, 13 * nlines(ln.get("text")) + 8)
            _cell(ws, f"A{r}", ln.get("text") or "", size=9, merge=f"G{r}")
            block_cells = []
            r += 1
            continue
        if kind == "subtotal":
            ws.row_dimensions[r].height = 22
            _cell(ws, f"A{r}", ln.get("text") or "Sub total", size=9, fill=ORANGE, merge=f"E{r}")
            _cell(ws, f"F{r}", f"=SUM({','.join(block_cells)})" if block_cells else 0, size=9,
                  fmt=money if ln.get("amount") else item_money, fill=ORANGE)
            _cell(ws, f"G{r}", None, fill=ORANGE)
            block_cells = []
            r += 1
            continue
        if kind == "spec":
            # 방역 로봇 견적서처럼 항목명은 넓게(품목+규격 칸 A:B), 내용은 가운데(C:F), 비고(G)는 비움
            n_lines = max(nlines(ln.get("value"), ln.get("label")), -(-len(str(ln.get("label") or "")) // 22))
            ws.row_dimensions[r].height = max(20, 13 * n_lines + 6)
            _cell(ws, f"A{r}", ln.get("label") or "", size=9, merge=f"B{r}")
            _cell(ws, f"C{r}", ln.get("value") or "", size=9, merge=f"F{r}")
            _cell(ws, f"G{r}", None, size=9)
            r += 1
            continue
        if kind == "text":
            ws.row_dimensions[r].height = max(22, 14 * (str(ln.get("text") or "").count("\n") + 1) + 8)
            _cell(ws, f"A{r}", ln.get("text") or "", size=9, h="left", merge=f"G{r}")
            r += 1
            continue
        if kind == "nego":
            ws.row_dimensions[r].height = 22
            _cell(ws, f"A{r}", ln["desc"], size=9, merge=f"E{r}")
            _cell(ws, f"F{r}", ln["amount"], size=9, fmt=money)
            _cell(ws, f"G{r}", ln.get("remark") or "", size=9)
            for ref in (f"A{r}", f"F{r}"):                     # NEGO 글씨·금액은 빨간색(사용자 2026-10-13)
                ws[ref].font = Font(name=FONT, size=9, bold=True, color=NEGO_RED)
            billed.append(f"F{r}")
            r += 1
            continue
        pic = (photos or {}).get(str(ln.get("photo") or ""))
        ws.row_dimensions[r].height = max(31, 13 * nlines(ln["desc"], ln.get("commodity"), ln.get("remark")) + 5, 48 if pic else 0)
        _cell(ws, f"A{r}", ln.get("commodity") or "", size=9)
        _cell(ws, f"B{r}", ln["desc"], size=9, merge=f"C{r}")
        # 해외 수량 서식 = 해외 기준 견적서('#,##0_ "sets"' — 천 단위 쉼표, 숫자와 단위 사이 한 칸)
        _cell(ws, f"D{r}", ln["qty"], size=9, fmt=f'#,##0_ "{ln.get("unit") or "ea"}"' if en
              else f'0" {ln["unit"]}"' if ln.get("unit") else EA)
        line_money = money if ln.get("kind") in ("work", "freight") else item_money      # 무상 대여여도 물류비·인건비는 금액
        _cell(ws, f"E{r}", ln["unit_price"], size=9, fmt=line_money)
        if kind == "option" and "in_total" not in ln:      # 예전 옵션 — 공급가액 칸은 비우고 합계에서 뺀다(LG CNS 견적서)
            _cell(ws, f"F{r}", None, size=9)
        elif kind == "option" and not ln["in_total"]:      # 미반영 옵션 — 공급가액은 보이고 Sub total·Total 에는 안 넣음(사용자 2026-10-19)
            _cell(ws, f"F{r}", f"=D{r}*E{r}{_times(ln)}", size=9, fmt=line_money)
        else:
            _cell(ws, f"F{r}", f"=D{r}*E{r}{_times(ln)}", size=9, fmt=line_money)
            billed.append(f"F{r}")
            block_cells.append(f"F{r}")
        _cell(ws, f"G{r}", ln["remark"], size=9, h="left" if pic else "center")
        if pic:
            if ln["remark"]:                               # 사진 자리만큼 글 오른쪽을 비운다
                ws[f"G{r}"].alignment = Alignment(horizontal="left", vertical="center", wrap_text=True, indent=0)
            _photo_at(ws, pic, row=r, has_text=bool(ln["remark"]))
        r += 1
    # Total 아래 문구는 담당자가 적은 것만('* Option 미포함'도 자동으로 넣지 않음 — 사용자 2026-10-13)
    tot_lab = "\n".join(x for x in (lab["total"], header.get("total_note") or "") if x)
    ws.row_dimensions[r].height = 24 if tot_lab == lab["total"] else 16 * (tot_lab.count("\n") + 1) + 6
    plain = billed == [f"F{i}" for i in range(17, 17 + len(billed))]      # 묶음 없는 견적 — 예전처럼 범위 SUM
    total = (f"=SUM(F17:F{16 + len(billed)})" if plain else f"=SUM({','.join(billed)})") if billed else 0
    months = header.get("rental_months")
    if months:   # 대여 — 방역 로봇 3년 렌탈 견적서처럼 금액 칸에 총액, 비고 칸에 월 납입금(총액 ÷ 개월, 천 원 올림)
        _cell(ws, f"A{r}", tot_lab, fill=YELLOW, merge=f"E{r}")
        _cell(ws, f"F{r}", total, size=9 if en else 10, fmt=money, h="right", fill=YELLOW)
        _cell(ws, f"G{r}", f"=ROUNDUP(F{r}/{months},{0 if en else -3})", size=9 if en else 10, fill=YELLOW,
              fmt='"Monthly "$#,##0' if en else '"월 "#,##0"원"')
        ws[f"G{r}"].font = Font(name=FONT, size=9 if en else 10, bold=True, color="FF0000")
    elif not charged:   # 무상 대여(받을 금액 없음) — Total 칸 값은 0, 보이는 글은 '무상 대여 (N개월)'
        fm = header.get("free_months")
        shown = (f"Free rental ({fm} months)" if fm else "Free rental") if en else (f"무상 대여 ({fm}개월)" if fm else "무상 대여")
        _cell(ws, f"A{r}", tot_lab, fill=YELLOW, merge=f"F{r}")
        _cell(ws, f"G{r}", total, size=9 if en else 10, fmt=f'"{shown}"', h="right", fill=YELLOW)
        ws[f"G{r}"].font = Font(name=FONT, size=9 if en else 10, bold=True, color="FF0000")
    else:
        _cell(ws, f"A{r}", tot_lab, fill=YELLOW, merge=f"F{r}")
        _cell(ws, f"G{r}", total, size=9 if en else 10, fmt=money, h="right", fill=YELLOW)
    r += 1

    # 조건 · 비고 · 발주 확인
    rows = ((lab["delivery"], header["delivery"], lab["place"], header["place"]),
            (lab["validity"], terms["validity"], lab["payment"], header["payment"]))
    for a_lab, a_val, f_lab, f_val in rows:
        ws.row_dimensions[r].height = max(34.4, 14 * nlines(a_val, f_val) + 6)
        _cell(ws, f"A{r}", a_lab, fill=GREY)
        _cell(ws, f"B{r}", a_val, h="left", merge=f"E{r}")
        _cell(ws, f"F{r}", f_lab, fill=GREY)
        _cell(ws, f"G{r}", f_val, h="left")
        r += 1
    ws.row_dimensions[r].height = max(34.4, 17 * (sum(nlines(x) for x in header["notes"]) + 1))
    _cell(ws, f"A{r}", lab["note"], fill=GREY)
    _cell(ws, f"B{r}", "\n".join(header["notes"]), h="left", merge=f"G{r}", size=10 if en else 9)
    r += 1
    ws.row_dimensions[r].height = 100.4
    _cell(ws, f"A{r}", lab["order"], fill=GREY)
    if en:
        order = (f"{terms['order_note']}\n\n{terms['order_accept']}\n\n\n"
                 + " " * 148 + "Purchaser Name :  _______________________ (Signature)")   # 해외 기준 견적서 공백 수
    else:
        order = ("※ 발주 시 서명 날인하여  담당자 이메일을 통해 발신해주시기 바랍니다.\n\n당사는 이 견적서 상의 가격 및 조건들을 수용하고 "
                 "이 견적서를 귀사에 대한 공식 발주서로 대신하고자 합니다.\n\n\n" + " " * 120 + "발주자명 :  _______________________ (서명)")
    _cell(ws, f"B{r}", order, h="left", v="top", merge=f"G{r}")
    r += 1
    # 표 아래 주석 — 회사 고정 문구 다음에 담당자가 추가한 주석을 칸마다 한 줄씩(방역 로봇 견적서처럼, 사용자 2026-10-13)
    for note in foot_text(header):
        ws.row_dimensions[r].height = max(20, 14 * (note.count("\n") + 1) + 6)
        _cell(ws, f"A{r}", note, size=9, merge=f"G{r}")
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
    """견적 합계 — 품목·Total 반영 옵션·NEGO 줄만(소계·미반영 옵션·사양·문구 줄 제외). 예전 기록(row 없음)은 모두 품목."""
    return round(sum(ln["amount"] for ln in lines if ln.get("row") in (None, "nego")
                     or (ln.get("row") == "option" and ln.get("in_total"))), 2)


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
