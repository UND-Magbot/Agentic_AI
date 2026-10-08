"""Expense 보고 양식 xlsx 생성기.

설계:
- `app/templates/expense_template.xlsx` 를 매 요청마다 복제(in-memory)하여 시트 구조/수식/
  스타일을 그대로 보존한다. 원본 동작 보존 원칙(req.md §2 원칙 1).
- 시트 'expense_내역' 에 데이터 행만 채우고, D53/K53 의 합계 수식은 손대지 않는다.
- 시트 'expense_개인카드영수증 첨부' 의 16개 슬롯(4×4 그리드)에 영수증 이미지를 순서대로
  임베드. 슬롯 픽셀 크기에 맞춰 비율 유지 리사이즈.
- OCR/분류는 1차 범위 외 — 호출자가 이미 구조화된 라인을 넘겨주는 책임.

호출자(tools.dispatch)는 영수증 이미지 바이트만 넘기고, 라벨링/캡션은 템플릿이 갖고 있는
정적 텍스트를 재사용한다.
"""
from __future__ import annotations

import datetime as _dt
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from openpyxl import load_workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.utils import column_index_from_string, get_column_letter
from PIL import Image as PILImage

# ── 템플릿 경로 ────────────────────────────────────────────────────────────────
_TEMPLATE_PATH = Path(__file__).parent / "templates" / "expense_template.xlsx"

# ── 시트2: expense_내역 매핑 ──────────────────────────────────────────────────
_SHEET_DATA = "expense_내역"
_AUTHOR_CELL = "L1"
_DATA_FIRST_ROW = 5
_DATA_LAST_ROW = 52  # 53 행은 합계 — 건드리지 않음

# 법인카드 (좌측): A=계정과목, B=사용일자, C=지출사유, D=합계금액, E=공급자상호, F=사용자
_CORP_COLS = {
    "category": "A",
    "date": "B",
    "purpose": "C",
    "amount": "D",
    "vendor": "E",
    "user": "F",
}
# 개인카드 (우측): H=계정과목, I=사용일자, J=지출사유, K=합계금액, L=공급자상호
_PERSONAL_COLS = {
    "category": "H",
    "date": "I",
    "purpose": "J",
    "amount": "K",
    "vendor": "L",
}

# ── 시트3: 영수증 첨부 슬롯 매핑 ──────────────────────────────────────────────
_SHEET_RECEIPTS = "expense_개인카드영수증 첨부"
# 슬롯 좌상단 좌표 = (row, col_letter). 좌→우, 상→하 reading order.
_RECEIPT_SLOT_ANCHORS: list[tuple[int, str]] = [
    (r, c) for r in (6, 28, 50, 72) for c in ("C", "G", "K", "O")
]
# 슬롯 위쪽 캡션 행: (이미지 시작행 → 캡션 행).
# row 6 슬롯들의 캡션 = row 4, row 28 슬롯들의 캡션 = row 26, …
_CAPTION_ROW_FOR_SLOT_ROW = {6: 4, 28: 26, 50: 48, 72: 70}
# 슬롯 인덱스(0..15) → (date_cell, purpose_cell, amount_cell). 슬롯 시작열로부터 +0/+1/+2.
def _make_caption_cells() -> list[tuple[str, str, str]]:
    cells: list[tuple[str, str, str]] = []
    for row, col_letter in _RECEIPT_SLOT_ANCHORS:
        caption_row = _CAPTION_ROW_FOR_SLOT_ROW[row]
        c1 = column_index_from_string(col_letter)
        cells.append((
            f"{get_column_letter(c1)}{caption_row}",
            f"{get_column_letter(c1 + 1)}{caption_row}",
            f"{get_column_letter(c1 + 2)}{caption_row}",
        ))
    return cells
_RECEIPT_CAPTION_CELLS: list[tuple[str, str, str]] = _make_caption_cells()
# 슬롯 크기: 3열 × 19행 (예: C6:E24).
_SLOT_COLS = 3
_SLOT_ROWS = 19

# EMU(English Metric Unit) 변환 상수. Excel drawing 좌표계는 EMU 단위.
_EMU_PER_PIXEL = 9525           # @96 DPI
_EMU_PER_INCH = 914400
_DPI = 96

# 카테고리(계정과목) 화이트리스트 — 작성방법 시트 N4:N11 기준.
ExpenseCategory = Literal[
    "접대비",
    "복리후생비",
    "여비교통비",
    "소모품비",
    "차량유지비",
    "지급수수료",
    "도서인쇄비",
    "해당없음",
]
ExpenseSource = Literal["법인카드", "개인카드"]


@dataclass
class ExpenseLine:
    """expense 한 줄. source 에 따라 법인/개인 영역에 기입된다."""

    source: ExpenseSource
    category: str  # ExpenseCategory; 호환을 위해 str 로 받는다(잘못된 값은 그대로 들어감)
    date: str       # YYYY-MM-DD
    purpose: str
    amount: float
    vendor: str
    user: str = ""  # 법인카드일 때만 의미가 있음


@dataclass
class ReceiptImage:
    """영수증 이미지. order_index 로 슬롯 자리를 강제할 수 있고, 비우면 입력 순서를 사용."""

    filename: str
    data: bytes
    order_index: int | None = None


@dataclass
class ExpenseReport:
    author: str
    year: int
    month: int  # 1~12
    lines: list[ExpenseLine] = field(default_factory=list)
    receipts: list[ReceiptImage] = field(default_factory=list)


# ── 템플릿 잔재(예시 데이터/이미지) ──────────────────────────────────────────
# expense_내역 row 5 에 박혀있는 개인카드 예시 데이터(H5~L5).
_TEMPLATE_DATA_EXAMPLE_CELLS = ("H5", "I5", "J5", "K5", "L5")

# 영수증 시트 row 4 의 "EX)" 예시 (B4~I4). K4/L4/M4 와 O4/P4/Q4 는 슬롯3/4의
# 실제 헤더(날짜/지출사유/합계금액)이므로 보존.
_TEMPLATE_RECEIPT_EXAMPLE_CELLS = ("B4", "C4", "D4", "E4", "G4", "H4", "I4")

# 슬롯1(C6:E24) 좌상단에 남아있는 "#VALUE!" 문자열 잔재.
_TEMPLATE_RECEIPT_STRAY_CELL = "C6"


# ── 픽셀 환산 (slot 비율 계산용) ────────────────────────────────────────────
# Excel column width(문자 폭) → 픽셀. MS Office 표준식: px = round(width * 7 + 5).
def _col_width_to_px(width: float | None) -> int:
    if width is None or width <= 0:
        width = 8.43  # openpyxl 기본
    return int(round(width * 7 + 5))


# Excel row height(pt) → 픽셀. 1pt = 96/72 px @ 96 DPI.
def _row_height_to_px(height: float | None, default_pt: float = 15.0) -> int:
    if height is None or height <= 0:
        height = default_pt
    return int(round(height * _DPI / 72))


def _slot_pixel_size(ws, start_row: int, start_col: str) -> tuple[int, int]:
    """슬롯 시각 크기(width_px, height_px). 이미지 비율 계산용으로만 사용 — 실제
    Excel 렌더 크기는 TwoCellAnchor 가 결정한다(자동 슬롯 맞춤).
    """
    default_row_pt = float(ws.sheet_format.defaultRowHeight or 15.0)
    start_col_idx = column_index_from_string(start_col)
    width_px = sum(
        _col_width_to_px(ws.column_dimensions[get_column_letter(start_col_idx + i)].width)
        for i in range(_SLOT_COLS)
    )
    height_px = sum(
        _row_height_to_px(ws.row_dimensions[start_row + i].height, default_row_pt)
        for i in range(_SLOT_ROWS)
    )
    return width_px, height_px


def _normalize_image(image_bytes: bytes, max_w: int, max_h: int) -> bytes:
    """EXIF 회전 보정 + RGB 변환 + 최대 크기 리사이즈(비율 유지).

    슬롯에 어차피 TwoCellAnchor 로 stretch 되므로 본 함수의 목적은:
    (1) EXIF orientation 정상화 (스마트폰 사진),
    (2) 큰 원본을 적당히 축소하여 xlsx 용량 폭증 방지 (max_w/max_h 의 2배 정도까지).
    """
    img = PILImage.open(io.BytesIO(image_bytes))
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    # 슬롯의 2배 정도로 캡 — 디스플레이 픽셀보다 약간 큰 정도면 고해상도 화면에서도 선명.
    cap_w, cap_h = max_w * 2, max_h * 2
    w, h = img.size
    scale = min(cap_w / w, cap_h / h, 1.0)
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), PILImage.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _make_two_cell_anchor(
    ws,
    *,
    start_row: int,        # 1-indexed
    start_col_idx: int,    # 1-indexed
    end_row: int,          # 1-indexed inclusive (슬롯 마지막 행)
    end_col_idx: int,      # 1-indexed inclusive (슬롯 마지막 열)
) -> TwoCellAnchor:
    """슬롯 전체를 채우는 stretch-fit anchor. 종횡비는 슬롯 비율에 맞춰 늘어남.

    from 은 슬롯 좌상단(+1px 인셋), to 는 슬롯 마지막 셀의 우하단(-1px 인셋).
    1px 인셋은 두 가지 이유로 필요:
    (1) 슬롯 테두리(border) 위에 이미지가 정확히 올라가면 Excel 렌더링에서 테두리가
        가려져 시각적으로 슬롯 경계가 사라진다.
    (2) 부동소수 EMU 환산 오차로 인접 셀(F/J/N/R 등 gap 열)을 ~1px 침범하는 사례를
        실제로 관측(expense_error_v4) — gap 열 침범 시 이미지가 슬롯을 벗어난 듯 보임.

    Excel 의 a:stretch + fillRect 와 결합되면 이미지가 anchor 영역에 정확히 stretch
    되어 슬롯 전체가 영수증으로 채워진다. 종횡비 유지 옵션은 사용자 요청(2026-05-14:
    "그 영역 사이즈에 딱 맞춤")으로 제거 — 영수증은 슬롯에 약간 늘어나도 무방.
    """
    default_row_pt = float(ws.sheet_format.defaultRowHeight or 15.0)

    last_col_letter = get_column_letter(end_col_idx)
    last_col_w_px = _col_width_to_px(ws.column_dimensions[last_col_letter].width)
    last_row_h_px = _row_height_to_px(
        ws.row_dimensions[end_row].height, default_row_pt
    )

    inset = _EMU_PER_PIXEL  # 1px

    _from = AnchorMarker(
        col=start_col_idx - 1,
        colOff=inset,
        row=start_row - 1,
        rowOff=inset,
    )
    # 우하단 = 마지막 셀의 (full width/height - 1px). 절대 슬롯 밖으로 안 나감.
    to = AnchorMarker(
        col=end_col_idx - 1,
        colOff=max(0, last_col_w_px * _EMU_PER_PIXEL - inset),
        row=end_row - 1,
        rowOff=max(0, last_row_h_px * _EMU_PER_PIXEL - inset),
    )
    # editAs="oneCell": 슬롯 셀 크기가 바뀌면 이미지도 함께 변형(슬롯에 결속).
    return TwoCellAnchor(editAs="oneCell", _from=_from, to=to)


# ── 템플릿 잔재 정리 ──────────────────────────────────────────────────────────
def _clear_template_examples(ws_data, ws_receipts) -> None:
    """템플릿에 박혀있는 예시 데이터/이미지를 출력 전에 비운다.

    합계 수식(D53/K53), 헤더(행 1~4의 진짜 라벨), 참고사항 사이드바(N~O 열)는
    그대로 유지. 사용자가 라인을 0건만 넘기는 경우에도 예시가 새어 나오지 않도록
    cell 값을 None 으로 비운다. 스타일/병합/수식은 손대지 않는다.

    추가 — `_RECEIPT_CAPTION_CELLS` 전체(16슬롯×3셀)를 일괄 비운다. 이렇게 하면
    `_fill_receipt_captions` 가 사용된 슬롯만 다시 채우고, 사용되지 않은 슬롯의
    템플릿 라벨('날짜'/'지출사유'/'합계금액') 은 자연스럽게 사라진다 — 라벨 잔재
    누수 가능성을 원천 차단(expense_error_v4 재발 방지).
    """
    for coord in _TEMPLATE_DATA_EXAMPLE_CELLS:
        ws_data[coord] = None
    for coord in _TEMPLATE_RECEIPT_EXAMPLE_CELLS:
        ws_receipts[coord] = None
    ws_receipts[_TEMPLATE_RECEIPT_STRAY_CELL] = None
    # 모든 캡션 셀 일괄 비움 — 채울 라인은 _fill_receipt_captions 가 다시 기입.
    for date_cell, purpose_cell, amount_cell in _RECEIPT_CAPTION_CELLS:
        ws_receipts[date_cell] = None
        ws_receipts[purpose_cell] = None
        ws_receipts[amount_cell] = None
    # 슬롯2 에 preload 된 EX) 영수증 이미지를 제거. openpyxl 의 _images 는 list 라
    # 통째로 비워도 안전 — 사용자가 추가한 이미지는 이 함수 호출 *뒤* 에 들어간다.
    ws_receipts._images = []


# ── 시트 채움 ────────────────────────────────────────────────────────────────
def _fill_lines(ws, lines: list[ExpenseLine]) -> None:
    """법인/개인 라인을 각각 따로 모아 위에서부터 채운다.

    날짜 정렬은 호출자 책임 — 사용자 입력 순서를 그대로 존중한다.
    행이 _DATA_LAST_ROW 를 넘어가면 초과분은 버리고 ValueError 를 던진다.
    """
    corp = [l for l in lines if l.source == "법인카드"]
    personal = [l for l in lines if l.source == "개인카드"]
    capacity = _DATA_LAST_ROW - _DATA_FIRST_ROW + 1
    if len(corp) > capacity or len(personal) > capacity:
        raise ValueError(
            f"라인 수가 한도({capacity})를 초과: 법인={len(corp)} 개인={len(personal)}"
        )

    for idx, line in enumerate(corp):
        row = _DATA_FIRST_ROW + idx
        ws[f"{_CORP_COLS['category']}{row}"] = line.category
        ws[f"{_CORP_COLS['date']}{row}"] = _parse_data_date(line.date)
        ws[f"{_CORP_COLS['purpose']}{row}"] = line.purpose
        ws[f"{_CORP_COLS['amount']}{row}"] = line.amount
        ws[f"{_CORP_COLS['vendor']}{row}"] = line.vendor
        ws[f"{_CORP_COLS['user']}{row}"] = line.user

    for idx, line in enumerate(personal):
        row = _DATA_FIRST_ROW + idx
        ws[f"{_PERSONAL_COLS['category']}{row}"] = line.category
        ws[f"{_PERSONAL_COLS['date']}{row}"] = _parse_data_date(line.date)
        ws[f"{_PERSONAL_COLS['purpose']}{row}"] = line.purpose
        ws[f"{_PERSONAL_COLS['amount']}{row}"] = line.amount
        ws[f"{_PERSONAL_COLS['vendor']}{row}"] = line.vendor


# 캡션 날짜 표시 포맷용 — YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD 흡수 후 MM/DD 출력.
# 캡션 셀(C4/G4/K4/O4 등)의 폭이 5.625 (≈ 44px) 라 풀 ISO 날짜는 시각적으로 잘린다.
# 월/년은 파일명·작성자 셀(L1)에서 이미 명시되므로 캡션은 MM/DD 만으로 충분.
_CAPTION_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
# 'YYYY-MM-DD' 또는 'YYYY-M-D' 등 흡수. 캡션과 동일 패턴이지만 datetime.date 로 변환.
_ISO_DATE_RE = _CAPTION_DATE_RE


def _format_caption_date(date_str: str) -> str:
    """캡션 표시용 짧은 날짜. 'YYYY-MM-DD' → 'MM/DD'. 파싱 실패 시 원문 그대로."""
    s = (date_str or "").strip()
    m = _CAPTION_DATE_RE.match(s)
    if not m:
        return s
    _, mo, d = m.groups()
    return f"{mo.zfill(2)}/{d.zfill(2)}"


def _parse_data_date(date_str: str) -> _dt.date | str:
    """내역 시트 사용일자 셀에 들어갈 값.

    템플릿의 B5/I5 셀 number_format 은 'm/d aaa' (월/일 + 한국어 요일) 인데, 이 포맷은
    문자열에는 적용되지 않는다. 따라서 'YYYY-MM-DD' 문자열을 그대로 넣으면 Excel 이
    포맷팅 없이 raw text 로 표시 → 좁은 컬럼(I=7.12char ≈ 55px)에서 '023-02-1' 같이
    중간이 잘려 보인다 (expense_error_v3).

    해결: 문자열을 `datetime.date` 로 변환해 셀에 저장. 그러면 Excel 이 m/d aaa 를
    적용해 '2/10 화' 처럼 표시한다. 파싱 실패(=원문 보존이 합리적인 케이스: 자유 텍스트
    날짜) 시에는 원문 문자열을 그대로 반환 — 사용자가 의도적으로 비표준 표기를 쓰는
    가능성을 보존한다.
    """
    s = (date_str or "").strip()
    m = _ISO_DATE_RE.match(s)
    if not m:
        return s
    y, mo, d = (int(x) for x in m.groups())
    try:
        return _dt.date(y, mo, d)
    except ValueError:
        # 2/30 같은 잘못된 날짜는 그대로 문자열로 — Excel 사용자가 즉시 인지 가능.
        return s


def _fill_receipt_captions(ws_receipts, personal_lines: list[ExpenseLine]) -> None:
    """개인카드 라인을 순서대로 영수증 슬롯 캡션(날짜/지출사유/합계금액)에 기입.

    슬롯 0..15 중:
    - 사용된 슬롯(라인 존재) → 날짜/지출사유/합계금액 셀에 실제 값 기입
    - 사용 안 된 슬롯 → 템플릿에 남은 라벨 텍스트("날짜"/"지출사유"/"합계금액")를 비움.
      미완성 캡션이 결과물에 노출되는 것을 막기 위함.

    라인 수가 16(슬롯 한도)을 초과하면 ValueError. 영수증 라인 수와 무관 — 영수증을
    첨부하지 않아도 라인이 있으면 캡션은 채워진다(사용자 의도: '개인카드 지출 순서대로').
    """
    if len(personal_lines) > len(_RECEIPT_CAPTION_CELLS):
        raise ValueError(
            f"개인카드 라인 수({len(personal_lines)})가 영수증 슬롯 한도"
            f"({len(_RECEIPT_CAPTION_CELLS)})를 초과"
        )

    for idx, (date_cell, purpose_cell, amount_cell) in enumerate(
        _RECEIPT_CAPTION_CELLS
    ):
        if idx < len(personal_lines):
            line = personal_lines[idx]
            ws_receipts[date_cell] = _format_caption_date(line.date)
            ws_receipts[purpose_cell] = line.purpose
            ws_receipts[amount_cell] = line.amount
        else:
            # 사용되지 않은 슬롯의 라벨 잔재 정리.
            ws_receipts[date_cell] = None
            ws_receipts[purpose_cell] = None
            ws_receipts[amount_cell] = None


def _embed_receipts(ws, receipts: list[ReceiptImage]) -> None:
    """영수증 이미지를 16개 슬롯에 순서대로 임베드.

    슬롯이 부족하면 ValueError. order_index 가 명시된 이미지는 그 위치로,
    나머지는 빈 슬롯을 위에서부터 채운다.
    """
    if not receipts:
        return
    if len(receipts) > len(_RECEIPT_SLOT_ANCHORS):
        raise ValueError(
            f"영수증 수가 슬롯 한도({len(_RECEIPT_SLOT_ANCHORS)})를 초과: {len(receipts)}"
        )

    # 슬롯 배정: 명시된 order_index 우선, 그 다음 미지정분이 빈 자리 채움.
    assigned: dict[int, ReceiptImage] = {}
    leftovers: list[ReceiptImage] = []
    for r in receipts:
        if r.order_index is not None and 0 <= r.order_index < len(_RECEIPT_SLOT_ANCHORS):
            assigned.setdefault(r.order_index, r)
        else:
            leftovers.append(r)
    next_slot = 0
    for r in leftovers:
        while next_slot in assigned and next_slot < len(_RECEIPT_SLOT_ANCHORS):
            next_slot += 1
        if next_slot >= len(_RECEIPT_SLOT_ANCHORS):
            raise ValueError("슬롯 배정 실패: 빈 자리 없음")
        assigned[next_slot] = r
        next_slot += 1

    for slot_idx, receipt in assigned.items():
        row, col = _RECEIPT_SLOT_ANCHORS[slot_idx]
        col_idx = column_index_from_string(col)
        slot_w, slot_h = _slot_pixel_size(ws, row, col)
        normalized = _normalize_image(receipt.data, slot_w, slot_h)

        xl_img = XLImage(io.BytesIO(normalized))
        # 슬롯 전체를 stretch-fit 으로 채움. 이미지 픽셀 크기와 무관하게 슬롯에 맞춰 늘어남.
        xl_img.anchor = _make_two_cell_anchor(
            ws,
            start_row=row,
            start_col_idx=col_idx,
            end_row=row + _SLOT_ROWS - 1,
            end_col_idx=col_idx + _SLOT_COLS - 1,
        )
        ws.add_image(xl_img)


# ── 공개 API ────────────────────────────────────────────────────────────────
def build_expense_xlsx(report: ExpenseReport) -> bytes:
    """ExpenseReport 를 xlsx 바이트로 변환해 반환.

    호출자는 결과를 MinIO 에 저장하거나 메일에 첨부한다. 본 함수는 I/O 없음.
    """
    if not _TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"템플릿이 없습니다: {_TEMPLATE_PATH}")

    wb = load_workbook(_TEMPLATE_PATH)
    try:
        ws_data = wb[_SHEET_DATA]
        ws_receipts = wb[_SHEET_RECEIPTS]
    except KeyError as e:
        raise ValueError(f"템플릿 시트 누락: {e}") from e

    _clear_template_examples(ws_data, ws_receipts)
    ws_data[_AUTHOR_CELL] = report.author
    _fill_lines(ws_data, report.lines)
    # 영수증 시트 캡션 — 개인카드 라인 순서대로 채움. 사용 안 된 슬롯은 라벨 잔재 정리.
    personal_lines = [l for l in report.lines if l.source == "개인카드"]
    _fill_receipt_captions(ws_receipts, personal_lines)
    _embed_receipts(ws_receipts, report.receipts)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def build_filename(report: ExpenseReport) -> str:
    """파일명 규칙: (주)유엔디_expense{MM}월_{작성자}.xlsx"""
    mm = f"{report.month:02d}"
    safe_author = report.author.strip() or "미상"
    return f"(주)유엔디_expense{mm}월_{safe_author}.xlsx"


__all__ = [
    "ExpenseCategory",
    "ExpenseSource",
    "ExpenseLine",
    "ReceiptImage",
    "ExpenseReport",
    "build_expense_xlsx",
    "build_filename",
]
