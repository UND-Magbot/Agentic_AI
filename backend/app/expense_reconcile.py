# -*- coding: utf-8 -*-
"""개인카드 영수증 대조·검증 — expense xlsx(내역 시트 + 영수증 첨부 시트) → 날짜별 대조.

배경:
  expense 파일은 한 통합 워크북에 세 시트를 담는다 — 작성방법 / expense_내역 /
  개인카드영수증첨부. 본 모듈은 **개인카드 지출**에 한해:
    1) expense_내역 시트의 개인카드 표(계정과목·사용일자·지출사유·합계금액·공급자)를 파싱.
    2) 영수증 시트의 헤더(사진 위 날짜·제목·금액) + 내장 이미지를 파싱·매핑.
    3) 각 영수증은 (헤더 금액) + (이미지 OCR 금액) 2소스를 **종합**해 판단하고,
       날짜별로 하루치를 모두 더한다.
    4) 개인카드 내역 날짜별 합계 ↔ 영수증 날짜별 합계를 대조해 일치/불일치를 판정.

원본은 절대 수정하지 않는다(임시 복사본만 읽고, 결과는 별도 검증 워크북으로 생성).
모든 수치는 실제 파일에서 읽은 값 — 가공된 점수/등급 없이 사실만 보고한다.

흐름(챗 fast-path):
  첨부(expense xlsx 1개) → MinIO fetch → 파싱 → 이미지 OCR → 대조 → 검증 워크북
  MinIO 저장 + Attachment row → 다운로드 메타 + 사람이 읽을 요약 반환.
"""
from __future__ import annotations

import io
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select

from . import expense_vision
from .database import SessionLocal
from .models import Attachment, User, UserRole
from .storage import get_object_stream, make_object_key, put_object

logger = logging.getLogger("expense_reconcile")

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_EPS = 0.5

# 진행 단계 ID — frontend ProgressCard 와 동일 식별자.
PROGRESS_STEPS: tuple[tuple[str, str], ...] = (
    ("fetch", "첨부 파일 준비"),
    ("parse", "개인카드 내역·영수증 인식"),
    ("ocr", "영수증 이미지 OCR 판독"),
    ("compare", "날짜별 대조·검증"),
    ("upload", "검증 결과 저장"),
)

# 개인카드 표 서브헤더 라벨.
_HDR_DATE = ("사용일자",)
_HDR_AMOUNT = ("합계금액",)
_HDR_PURPOSE = ("지출사유",)
_HDR_CATEGORY = ("계정과목",)
_HDR_VENDOR = ("공급자 상호", "공급자상호", "공급자")


# ── 값 정규화 ─────────────────────────────────────────────────────────────────
def _to_date(v: Any) -> str | None:
    """셀 값 → 'YYYY-MM-DD'(없으면 None)."""
    if isinstance(v, (datetime, _date)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, str):
        m = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", v)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _num(v: Any) -> float | None:
    """셀 값 → 금액 float(없으면 None). 콤마/원/공백 제거."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v))
    try:
        return float(s) if s else None
    except ValueError:
        return None


# ── 데이터 구조 ──────────────────────────────────────────────────────────────
@dataclass
class PcEntry:
    """expense_내역 개인카드 표의 지출 1줄."""

    date: str
    amount: float
    category: str
    purpose: str
    vendor: str


@dataclass
class Receipt:
    """영수증 첨부 시트의 영수증 1건 — 헤더(사진 위 텍스트) + 내장 이미지."""

    date: str
    title: str
    header_amount: float      # 사진 위에 사용자가 적은 금액(선언값)
    hdr_row: int
    hdr_col: int
    image_bytes: bytes | None = None
    ocr_amount: float | None = None   # 이미지 OCR 추출 금액(교차검증)
    ocr_date: str | None = None
    ocr_vendor: str | None = None

    @property
    def ocr_status(self) -> str:
        """헤더 금액 ↔ OCR 금액 대조 상태."""
        if self.image_bytes is None:
            return "이미지없음"
        if self.ocr_amount is None:
            return "판독실패"
        if abs(self.ocr_amount - self.header_amount) < _EPS:
            return "일치"
        return "차이"


@dataclass
class DateRow:
    """날짜별 대조 결과 1줄."""

    date: str
    naeyeok_total: float
    receipt_total: float
    naeyeok_count: int
    receipt_count: int

    @property
    def diff(self) -> float:
        return self.naeyeok_total - self.receipt_total

    @property
    def verdict(self) -> str:
        has_n = self.naeyeok_count > 0
        has_r = self.receipt_count > 0
        if has_n and has_r:
            return "일치" if abs(self.diff) < _EPS else "금액상이"
        if has_n and not has_r:
            return "영수증없음"
        return "내역누락"

    @property
    def ok(self) -> bool:
        return self.verdict == "일치"


@dataclass
class ReconcileReport:
    """대조 전체 결과."""

    author: str
    year: int
    month: int
    pc_entries: list[PcEntry]
    receipts: list[Receipt]
    rows: list[DateRow] = field(default_factory=list)

    @property
    def matched(self) -> list[DateRow]:
        return [r for r in self.rows if r.verdict == "일치"]

    @property
    def amount_diff(self) -> list[DateRow]:
        return [r for r in self.rows if r.verdict == "금액상이"]

    @property
    def receipt_missing(self) -> list[DateRow]:
        return [r for r in self.rows if r.verdict == "영수증없음"]

    @property
    def ledger_missing(self) -> list[DateRow]:
        return [r for r in self.rows if r.verdict == "내역누락"]

    @property
    def all_ok(self) -> bool:
        return all(r.ok for r in self.rows)


# ── expense_내역 개인카드 표 파싱 ─────────────────────────────────────────────
def parse_personal_card(ws) -> list[PcEntry]:
    """expense_내역 시트에서 개인카드 지출 표를 파싱.

    '개인카드' 라벨 셀의 열을 기준으로, 그 우측 영역의 서브헤더
    (사용일자·합계금액·지출사유·계정과목·공급자)를 찾아 데이터 행을 읽는다.
    'Total' 행 또는 데이터 종료 시 중단.
    """
    pc_col = _find_label_col(ws, "개인카드")
    if pc_col is None:
        raise ValueError(
            "expense_내역 시트에서 '개인카드' 지출 표를 찾지 못했습니다. "
            "개인카드 지출 표가 포함된 expense 파일인지 확인해 주세요."
        )

    hdr_row, cols = _find_subheaders(ws, pc_col)
    if hdr_row is None:
        raise ValueError(
            "개인카드 표의 머리글(사용일자·합계금액)을 찾지 못했습니다."
        )

    c_date = cols["date"]
    c_amt = cols["amount"]
    c_purpose = cols.get("purpose")
    c_cat = cols.get("category")
    c_vendor = cols.get("vendor")

    entries: list[PcEntry] = []
    for r in range(hdr_row + 1, min(ws.max_row, hdr_row + 200) + 1):
        # Total/합계 행 감지 → 종료.
        band = [str(ws.cell(r, cc).value or "") for cc in range(pc_col, c_amt + 1)]
        if any("total" in v.lower() or v.strip() == "합계" for v in band):
            break
        d = _to_date(ws.cell(r, c_date).value)
        a = _num(ws.cell(r, c_amt).value)
        if d is None or a is None:
            continue
        entries.append(
            PcEntry(
                date=d,
                amount=a,
                category=str(ws.cell(r, c_cat).value or "") if c_cat else "",
                purpose=str(ws.cell(r, c_purpose).value or "") if c_purpose else "",
                vendor=str(ws.cell(r, c_vendor).value or "") if c_vendor else "",
            )
        )
    return entries


def _find_label_col(ws, label: str) -> int | None:
    """라벨 문자열을 포함하는 첫 셀의 열 번호."""
    for row in ws.iter_rows():
        for c in row:
            if c.value and label in str(c.value):
                return c.column
    return None


def _find_subheaders(ws, min_col: int) -> tuple[int | None, dict[str, int]]:
    """min_col 이상 영역에서 개인카드 표 서브헤더 행과 각 열 위치를 찾는다."""
    for row in ws.iter_rows():
        found: dict[str, int] = {}
        for c in row:
            if c.column < min_col or not c.value:
                continue
            v = str(c.value).strip()
            if v in _HDR_DATE:
                found["date"] = c.column
            elif v in _HDR_AMOUNT:
                found["amount"] = c.column
            elif v in _HDR_PURPOSE:
                found["purpose"] = c.column
            elif v in _HDR_CATEGORY:
                found["category"] = c.column
            elif v in _HDR_VENDOR:
                found["vendor"] = c.column
        if "date" in found and "amount" in found:
            return row[0].row, found
    return None, {}


# ── 영수증 시트 파싱 + 이미지 매핑 ────────────────────────────────────────────
def parse_receipts(ws) -> list[Receipt]:
    """영수증 첨부 시트에서 영수증 헤더(날짜·제목·금액)와 내장 이미지를 파싱·매핑.

    레이아웃: 헤더 행마다 여러 영수증이 가로로 배치되고, 각 영수증은
    (날짜 셀, +1=제목, +2=금액) 3칸을 쓰며 이미지가 그 아래에 앵커된다.
    날짜(datetime) 셀을 앵커로 삼아 영수증을 잡고, 이미지는 (같은 그룹 열) &
    (자기 위쪽 헤더 행 중 가장 가까운 것)으로 매핑한다.
    """
    receipts: list[Receipt] = []
    for row in ws.iter_rows():
        for c in row:
            if not isinstance(c.value, (datetime, _date)):
                continue
            d = _to_date(c.value)
            if d is None:
                continue
            amt = _num(ws.cell(c.row, c.column + 2).value)
            if amt is None:
                continue
            title = ws.cell(c.row, c.column + 1).value
            receipts.append(
                Receipt(
                    date=d,
                    title=str(title or "").strip(),
                    header_amount=amt,
                    hdr_row=c.row,
                    hdr_col=c.column,
                )
            )

    if not receipts:
        return receipts

    hdr_cols = sorted({r.hdr_col for r in receipts})

    def group_col(col: int) -> int:
        """이미지 열 → 소속 그룹(가장 가까운 헤더 열, 약간의 우측 여유 허용)."""
        cands = [hc for hc in hdr_cols if hc <= col + 1]
        return max(cands) if cands else hdr_cols[0]

    for img in getattr(ws, "_images", []):
        try:
            irow = img.anchor._from.row + 1
            icol = img.anchor._from.col + 1
            data = img._data()
        except Exception as e:  # 앵커/데이터 접근 실패 시 이 이미지 skip.
            logger.warning("[expense_recon] 이미지 접근 실패: %s", e)
            continue
        gcol = group_col(icol)
        cand = [r for r in receipts if r.hdr_col == gcol and r.hdr_row <= irow]
        if not cand:
            cand = [r for r in receipts if r.hdr_col == gcol]
        if not cand:
            continue
        target = max(cand, key=lambda r: r.hdr_row)
        if target.image_bytes is None:  # 한 영수증엔 이미지 1장(첫 매핑 우선).
            target.image_bytes = data
    return receipts


# ── 대조 ─────────────────────────────────────────────────────────────────────
def build_report(
    author: str,
    pc_entries: list[PcEntry],
    receipts: list[Receipt],
) -> ReconcileReport:
    """개인카드 내역 ↔ 영수증을 날짜별로 대조한 리포트 생성."""
    n_by_date: dict[str, list[PcEntry]] = defaultdict(list)
    for e in pc_entries:
        n_by_date[e.date].append(e)
    r_by_date: dict[str, list[Receipt]] = defaultdict(list)
    for r in receipts:
        r_by_date[r.date].append(r)

    year, month = _infer_year_month(pc_entries, receipts)

    rows: list[DateRow] = []
    for d in sorted(set(n_by_date) | set(r_by_date)):
        ns = n_by_date.get(d, [])
        rs = r_by_date.get(d, [])
        rows.append(
            DateRow(
                date=d,
                naeyeok_total=sum(e.amount for e in ns),
                receipt_total=sum(r.header_amount for r in rs),
                naeyeok_count=len(ns),
                receipt_count=len(rs),
            )
        )

    return ReconcileReport(
        author=author,
        year=year,
        month=month,
        pc_entries=pc_entries,
        receipts=receipts,
        rows=rows,
    )


def _infer_year_month(
    pc_entries: list[PcEntry], receipts: list[Receipt]
) -> tuple[int, int]:
    """데이터 날짜들의 최빈 (연, 월) — 연도 보정/파일명용."""
    from collections import Counter

    ym = Counter()
    for e in pc_entries:
        ym[e.date[:7]] += 1
    for r in receipts:
        ym[r.date[:7]] += 1
    if not ym:
        return 0, 0
    top = ym.most_common(1)[0][0]  # 'YYYY-MM'
    return int(top[:4]), int(top[5:7])


# ── 검증 워크북 생성 ──────────────────────────────────────────────────────────
_HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")
_HEAD_FONT = Font(color="FFFFFF", bold=True, size=11)
_OK_FILL = PatternFill("solid", fgColor="E4F3E7")
_BAD_FILL = PatternFill("solid", fgColor="FBE4E4")
_WARN_FILL = PatternFill("solid", fgColor="FDF3DF")
_TITLE_FONT = Font(bold=True, size=13)
_SECTION_FONT = Font(bold=True, size=11, color="1F3A5F")
_THIN = Side(style="thin", color="C9D2DF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_WON = "#,##0"


def build_verification_xlsx(report: ReconcileReport) -> bytes:
    """대조 결과를 담은 검증 워크북(단일 시트) 생성 → bytes."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "개인카드 영수증 대조검증"
    ws.sheet_view.showGridLines = False

    r = 1
    ws.cell(r, 1, f"개인카드 영수증 대조·검증 — {report.author} {report.year}년 {report.month:02d}월")
    ws.cell(r, 1).font = _TITLE_FONT
    r += 1
    n = len(report.rows)
    ws.cell(
        r, 1,
        f"총 {n}개 일자 · 일치 {len(report.matched)} · 금액상이 {len(report.amount_diff)} · "
        f"영수증없음 {len(report.receipt_missing)} · 내역누락 {len(report.ledger_missing)}",
    ).font = Font(size=10, color="55627A")
    r += 2

    # ── 섹션 1: 날짜별 대조 ──
    r = _section(ws, r, "1. 날짜별 대조 (개인카드 하루치 합계)")
    hdr = ["날짜", "내역 합계", "영수증 합계", "차이(내역-영수증)", "내역 건수", "영수증 건수", "판정"]
    _write_header(ws, r, hdr)
    r += 1
    for row in report.rows:
        fill = _OK_FILL if row.verdict == "일치" else (
            _WARN_FILL if row.verdict in ("영수증없음", "내역누락") else _BAD_FILL
        )
        vals = [
            row.date, row.naeyeok_total, row.receipt_total, row.diff,
            row.naeyeok_count, row.receipt_count, row.verdict,
        ]
        for ci, v in enumerate(vals, start=1):
            cell = ws.cell(r, ci, v)
            cell.border = _BORDER
            cell.fill = fill
            if ci in (2, 3, 4):
                cell.number_format = _WON
        r += 1
    # 합계 행.
    tot_n = sum(x.naeyeok_total for x in report.rows)
    tot_r = sum(x.receipt_total for x in report.rows)
    _write_total(ws, r, ["합계", tot_n, tot_r, tot_n - tot_r, "", "", ""])
    r += 3

    # ── 섹션 2: 개인카드 내역 상세 ──
    r = _section(ws, r, "2. 개인카드 지출 내역 (expense_내역)")
    _write_header(ws, r, ["날짜", "계정과목", "지출사유", "금액", "공급자"])
    r += 1
    for e in sorted(report.pc_entries, key=lambda x: x.date):
        vals = [e.date, e.category, e.purpose, e.amount, e.vendor]
        for ci, v in enumerate(vals, start=1):
            cell = ws.cell(r, ci, v)
            cell.border = _BORDER
            if ci == 4:
                cell.number_format = _WON
        r += 1
    r += 2

    # ── 섹션 3: 영수증 상세(헤더 금액 ↔ OCR 종합) ──
    r = _section(ws, r, "3. 영수증 상세 (사진 위 금액 ↔ OCR 판독 종합)")
    _write_header(
        ws, r,
        ["날짜", "제목", "헤더 금액", "OCR 금액", "OCR 판정", "OCR 가맹점"],
    )
    r += 1
    for rc in sorted(report.receipts, key=lambda x: (x.date, x.hdr_col)):
        status = rc.ocr_status
        fill = None
        if status == "차이":
            fill = _BAD_FILL
        elif status in ("판독실패", "이미지없음"):
            fill = _WARN_FILL
        vals = [
            rc.date, rc.title, rc.header_amount,
            rc.ocr_amount if rc.ocr_amount is not None else "-",
            status, rc.ocr_vendor or "-",
        ]
        for ci, v in enumerate(vals, start=1):
            cell = ws.cell(r, ci, v)
            cell.border = _BORDER
            if fill:
                cell.fill = fill
            if ci in (3, 4) and isinstance(v, (int, float)):
                cell.number_format = _WON
        r += 1

    # 열 너비.
    widths = [13, 16, 30, 18, 12, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.column_dimensions["G"].width = 12

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _section(ws, r: int, title: str) -> int:
    ws.cell(r, 1, title).font = _SECTION_FONT
    return r + 1


def _write_header(ws, r: int, headers: list[str]) -> None:
    for ci, h in enumerate(headers, start=1):
        cell = ws.cell(r, ci, h)
        cell.fill = _HEAD_FILL
        cell.font = _HEAD_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = _BORDER


def _write_total(ws, r: int, vals: list[Any]) -> None:
    for ci, v in enumerate(vals, start=1):
        cell = ws.cell(r, ci, v)
        cell.font = Font(bold=True)
        cell.border = _BORDER
        cell.fill = PatternFill("solid", fgColor="EEF2F8")
        if ci in (2, 3, 4) and isinstance(v, (int, float)):
            cell.number_format = _WON


# ── 요약 작성 (가공 점수 없이 사실만) ─────────────────────────────────────────
def _fmt(v: float) -> str:
    return f"{v:,.0f}"


def build_summary(report: ReconcileReport) -> str:
    """사람이 읽을 검증 요약 — 날짜별 일치/불일치 사실과 차이 금액만."""
    n = len(report.rows)
    ok = report.all_ok
    lines: list[str] = []
    lines.append(
        f"{'✓' if ok else '⚠'} {report.author} {report.year}년 {report.month:02d}월 "
        f"개인카드 영수증 대조·검증 — "
        f"{'모든 일자 금액 일치' if ok else '불일치 항목 있음(점검 필요)'}"
    )
    lines.append("")
    lines.append(
        f"• 대상 {n}개 일자 — 일치 {len(report.matched)} · 금액상이 "
        f"{len(report.amount_diff)} · 영수증없음 {len(report.receipt_missing)} · "
        f"내역누락 {len(report.ledger_missing)}"
    )
    lines.append(
        f"• 총액 — 개인카드 내역 {_fmt(sum(r.naeyeok_total for r in report.rows))} vs "
        f"영수증 {_fmt(sum(r.receipt_total for r in report.rows))}"
    )

    problems = report.amount_diff + report.receipt_missing + report.ledger_missing
    if problems:
        lines.append("")
        lines.append("불일치 상세:")
        for row in sorted(problems, key=lambda x: x.date):
            if row.verdict == "금액상이":
                lines.append(
                    f"  ✗ {row.date} 금액상이 — 내역 {_fmt(row.naeyeok_total)} vs "
                    f"영수증 {_fmt(row.receipt_total)} (차이 {_fmt(row.diff)})"
                )
            elif row.verdict == "영수증없음":
                lines.append(
                    f"  ✗ {row.date} 영수증 없음 — 내역 {_fmt(row.naeyeok_total)} "
                    f"({row.naeyeok_count}건)에 대응하는 영수증이 없습니다."
                )
            else:  # 내역누락
                lines.append(
                    f"  ✗ {row.date} 내역 누락 — 영수증 {_fmt(row.receipt_total)} "
                    f"({row.receipt_count}건)이 개인카드 내역에 없습니다."
                )

    # OCR 교차검증 요약(보조 신호 — 완벽하지 않음을 명시).
    ocr_done = [r for r in report.receipts if r.image_bytes is not None]
    if ocr_done:
        agree = sum(1 for r in ocr_done if r.ocr_status == "일치")
        differ = [r for r in ocr_done if r.ocr_status == "차이"]
        failed = sum(1 for r in ocr_done if r.ocr_status == "판독실패")
        lines.append("")
        lines.append(
            f"• 영수증 이미지 OCR 교차검증(보조) — {len(ocr_done)}건 중 "
            f"헤더금액과 일치 {agree} · 차이 {len(differ)} · 판독실패 {failed}"
        )
        for r in differ:
            lines.append(
                f"  · {r.date} '{r.title}' 헤더 {_fmt(r.header_amount)} ↔ "
                f"OCR {_fmt(r.ocr_amount or 0)} — 사람 확인 권장"
            )
        lines.append("  ※ OCR 은 매출전표 화질·양식에 따라 오독이 있어 참고용입니다.")

    lines.append("")
    lines.append(
        "원본 파일은 수정하지 않았습니다. 첨부한 검증 워크북에서 날짜별 대조표와 "
        "내역·영수증 상세를 확인하세요. 최종 확정은 검토 후 진행하세요."
    )
    return "\n".join(lines)


# ── 결과 dataclass + 서비스 entry ─────────────────────────────────────────────
@dataclass
class ExpenseReconcileResult:
    """reconcile_expense_receipts 호출 결과(챗 fast-path 가 요약/다운로드에 사용)."""

    ok: bool
    author: str
    year: int
    month: int
    filename: str
    download_url: str
    size_bytes: int
    attachment_id: int
    summary: str


async def _fetch_expense_xlsx(
    db, attachment_ids: list[int]
) -> tuple[bytes, str, int | None]:
    """첨부 ID 목록에서 첫 xlsx 바이트 + 원본 파일명 + owner 를 가져온다."""
    if not attachment_ids:
        raise ValueError("첨부 파일이 없습니다.")
    res = await db.execute(select(Attachment).where(Attachment.id.in_(attachment_ids)))
    by_id = {a.id: a for a in res.scalars().all()}
    for att_id in attachment_ids:
        att = by_id.get(att_id)
        if att is None:
            continue
        name = (att.original_filename or "").lower()
        if not (name.endswith(".xlsx") or "spreadsheet" in (att.mime or "")):
            continue
        try:
            resp = get_object_stream(att.object_key)
            try:
                chunks = [c for c in resp.stream(amt=256 * 1024)]
                data = b"".join(chunks)
            finally:
                resp.close()
                resp.release_conn()
        except Exception as e:
            raise RuntimeError(f"첨부 다운로드 실패 (id={att_id}): {e}") from e
        return data, att.original_filename or "expense.xlsx", att.user_id
    raise ValueError(
        "엑셀(.xlsx) 첨부를 찾지 못했습니다. 개인카드 내역과 영수증이 포함된 "
        "expense 엑셀 파일을 첨부해 주세요."
    )


def _has_pc_table(ws) -> bool:
    """개인카드 표(개인카드 라벨 + 사용일자·합계금액 서브헤더)가 실재하는 시트인지."""
    col = _find_label_col(ws, "개인카드")
    if col is None:
        return False
    hdr_row, _ = _find_subheaders(ws, col)
    return hdr_row is not None


def _locate_sheets(wb) -> tuple[Any, Any]:
    """워크북에서 (내역 시트, 영수증 시트)를 판별.

    영수증 시트 = 내장 이미지가 가장 많은 시트(또는 '영수증' 이름).
    내역 시트 = '내역' 이름 우선, 아니면 개인카드 표가 실재하는 시트
    (작성방법 시트가 '개인카드' 단어만 언급하는 경우를 배제).
    """
    sheets = list(wb.worksheets)

    # 영수증 시트 — 이미지 최다(≥1). 없으면 '영수증' 이름.
    receipt = None
    best_imgs = 0
    for ws in sheets:
        n = len(getattr(ws, "_images", []))
        if n > best_imgs:
            best_imgs, receipt = n, ws
    if receipt is None:
        for ws in sheets:
            if "영수증" in (ws.title or ""):
                receipt = ws
                break

    # 내역 시트 — '내역' 이름 우선(영수증 시트 제외), 아니면 개인카드 표 실재 시트.
    naeyeok = None
    for ws in sheets:
        if ws is receipt:
            continue
        if "내역" in (ws.title or "") and _has_pc_table(ws):
            naeyeok = ws
            break
    if naeyeok is None:
        for ws in sheets:
            if ws is receipt:
                continue
            if _has_pc_table(ws):
                naeyeok = ws
                break

    if naeyeok is None:
        raise ValueError("개인카드 지출 내역 시트를 찾지 못했습니다.")
    if receipt is None:
        raise ValueError("영수증 첨부 시트(내장 이미지)를 찾지 못했습니다.")
    return naeyeok, receipt


def _extract_author(ws) -> str:
    """내역 시트에서 작성자명 추출('작성자' 라벨 우측 셀). 없으면 빈 문자열."""
    col = _find_label_col(ws, "작성자")
    if col is None:
        return ""
    for row in ws.iter_rows():
        for c in row:
            if c.value and "작성자" in str(c.value):
                nxt = ws.cell(c.row, c.column + 1).value
                if nxt:
                    return str(nxt).strip()
    return ""


async def _resolve_owner(db, fallback: int | None) -> int:
    if fallback is not None:
        return fallback
    res = await db.execute(
        select(User.id)
        .where(User.role.in_([UserRole.superadmin, UserRole.domain_admin]))
        .order_by(User.id.asc())
        .limit(1)
    )
    admin_id = res.scalar_one_or_none()
    if admin_id is not None:
        return admin_id
    res = await db.execute(select(User.id).order_by(User.id.asc()).limit(1))
    any_id = res.scalar_one_or_none()
    if any_id is None:
        raise RuntimeError("저장 가능한 사용자가 없습니다 — users 테이블이 비어 있습니다.")
    return any_id


def _safe_name(stem: str) -> str:
    return re.sub(r"[\\/:*?\"<>|\r\n\t]", "_", stem).strip() or "개인카드영수증대조"


def _xlsx_bucket() -> str:
    from .config import settings
    return settings.minio_bucket


async def reconcile_expense_receipts(
    *,
    attachment_ids: list[int],
    on_ocr_progress: Any = None,
) -> ExpenseReconcileResult:
    """expense xlsx 첨부 → 개인카드 내역 ↔ 영수증 대조 → 검증 워크북 + 요약.

    Args:
        attachment_ids: 챗 첨부 ID(첫 xlsx 를 대상으로).
        on_ocr_progress: OCR 진행 콜백(done, total) — 선택.
    """
    async with SessionLocal() as db:
        data, orig_name, owner = await _fetch_expense_xlsx(db, attachment_ids)

    # 원본 바이트는 읽기 전용 — 절대 저장/수정하지 않는다.
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws_naeyeok, ws_receipt = _locate_sheets(wb)

    author = _extract_author(ws_naeyeok)
    pc_entries = parse_personal_card(ws_naeyeok)
    receipts = parse_receipts(ws_receipt)
    if not pc_entries and not receipts:
        raise ValueError(
            "개인카드 내역과 영수증을 모두 인식하지 못했습니다. 파일 양식을 확인해 주세요."
        )

    year, month = _infer_year_month(pc_entries, receipts)

    # ── 영수증 이미지 OCR(헤더 금액 교차검증) ──
    imgs = [r.image_bytes for r in receipts if r.image_bytes is not None]
    idx_map = [i for i, r in enumerate(receipts) if r.image_bytes is not None]
    if imgs:
        parsed = await expense_vision.ocr_image_batch(
            imgs, expense_year=year or None, on_progress=on_ocr_progress
        )
        for slot, p in zip(idx_map, parsed):
            rc = receipts[slot]
            rc.ocr_amount = p.get("amount")
            rc.ocr_date = p.get("date")
            rc.ocr_vendor = p.get("vendor")

    report = build_report(author or "(작성자)", pc_entries, receipts)
    summary = build_summary(report)
    verify_bytes = build_verification_xlsx(report)

    # 파일명 — 원본 stem 기반 + 대조검증 표기.
    from pathlib import Path

    stem = Path(orig_name).stem
    filename = f"{_safe_name(stem + '_영수증대조검증')}.xlsx"

    async with SessionLocal() as db:
        owner_id = await _resolve_owner(db, owner)
        key = make_object_key(owner_id, filename)
        put_object(
            key=key, data=io.BytesIO(verify_bytes),
            length=len(verify_bytes), mime=_XLSX_MIME,
        )
        att = Attachment(
            user_id=owner_id, bucket=_xlsx_bucket(), object_key=key,
            original_filename=filename, mime=_XLSX_MIME, size_bytes=len(verify_bytes),
        )
        db.add(att)
        await db.commit()
        await db.refresh(att)

        return ExpenseReconcileResult(
            ok=report.all_ok,
            author=report.author,
            year=year,
            month=month,
            filename=filename,
            download_url=f"/api/attachments/{att.id}/download",
            size_bytes=att.size_bytes,
            attachment_id=att.id,
            summary=summary,
        )


__all__ = [
    "ExpenseReconcileResult",
    "ReconcileReport",
    "reconcile_expense_receipts",
    "parse_personal_card",
    "parse_receipts",
    "build_report",
    "build_verification_xlsx",
    "build_summary",
    "PROGRESS_STEPS",
]
