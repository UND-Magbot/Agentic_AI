# -*- coding: utf-8 -*-
"""자금계획 자동생성 — 자동이체/자동출금 반복 지출을 7·8월 영업일 블록으로 전개.

요구사항(사용자 확정):
  - 대상: 납부방법 ∈ {자동이체, 자동출금} 인 매월 반복 지출만 자동 배치.
  - 금액: 지난 6개월 중 가장 최신(연-월) 출현값을 반영(1원칙). 변동이어도 공란이 아니라
    최신값 기입 — 어차피 미확정(분홍셀)으로 표시되어 사용자 확정 대상임.
  - 블록: 대상 월의 모든 영업일마다 15줄 고정 entry 영역 + '계' 소계행.
  - 날짜 보정: 표준 결제일이 주말·공휴일이면 직전 영업일로 당김.

원본(자금계획_원화/외화)은 절대 수정하지 않는다 — 복사본에만 기존 마지막 6월 '계'행
이후로 새 블록을 외과적으로 기입한다(기존 행은 유지, 부분 7/1 블록만 재생성).
"""
from __future__ import annotations

import datetime
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import plan_reader as PR

# ── 납부방법 자동 분류 ────────────────────────────────────────────────────────
AUTO_PAY_METHODS = ("자동이체", "자동출금")   # 집행일 보정: 비영업일→다음 영업일(밀기)
# 반복 학습 대상 납부방법 — 자동이체/자동출금 + 송금. 송금으로 나가는 월반복 지출(급여·식대·
# 임차료·안전관리수수료·재료비 등)도 학습한다(사용자 확정 2026-07-02). 배치 방향은 납부방법별로
# 다르다: 자동이체/자동출금=다음 영업일, 송금(일반 집행)=직전 영업일(주말 전 미리) — _expense_day 참조.
RECUR_PAY_METHODS = AUTO_PAY_METHODS + ("송금",)
ENTRY_ROWS_PER_DAY = 15          # 영업일당 entry 줄 수(사용자 확정)
MIN_MONTHS_RECUR = 3             # 1~6월 중 N개월 이상 출현해야 반복으로 인정

# 2026년 대한민국 공휴일(7·8월 정밀, 그 외는 보강용). 8/15(토)→대체 8/17(월).
HOLIDAYS_2026 = {
    datetime.date(2026, 1, 1),
    datetime.date(2026, 2, 16), datetime.date(2026, 2, 17), datetime.date(2026, 2, 18),
    datetime.date(2026, 3, 1), datetime.date(2026, 3, 2),
    datetime.date(2026, 5, 5), datetime.date(2026, 5, 24), datetime.date(2026, 5, 25),
    datetime.date(2026, 6, 6),
    datetime.date(2026, 8, 15), datetime.date(2026, 8, 17),
    datetime.date(2026, 9, 24), datetime.date(2026, 9, 25), datetime.date(2026, 9, 26),
    datetime.date(2026, 10, 3), datetime.date(2026, 10, 5), datetime.date(2026, 10, 9),
    datetime.date(2026, 12, 25),
}


# ── 열 인덱스(1-based) — plan_reader 와 동일 레이아웃 ──────────────────────────
C_SEQ = 1        # A 순번
C_DATE = 2       # B 일자/계
C_INC_DETAIL = 3   # C 내역(수입)
C_INC_VENDOR = 4   # D 거래처(수입)
C_INC_ACCOUNT = 5  # E 발생계좌(수입)
C_INC_AMOUNT = 6   # F 금액(수입)
C_INC_NOTE = 7     # G 비고(수입) / '계'행 수입소계
C_EXP_ITEM = 8     # H 항목(지출)
C_EXP_DESC = 9     # I 적요(지출)
C_EXP_PAY = 10     # J 납부방법
C_EXP_ACCOUNT = 11 # K 발생계좌(지출)
C_EXP_VENDOR = 12  # L 거래처(지출)
C_EXP_ACCTG = 13   # M 회계처리
C_EXP_AMOUNT = 14  # N 금액(지출)
C_EXP_NOTE = 15    # O 비고(지출)
C_BAL = 16         # P 총잔액
C_AVAIL = 17       # Q 사용가능액
C_PROJ_BAL = 19    # S 과제계좌 잔액
C_SERP = 21        # U serp 총잔액
C_VERIFY = 22      # V 검증


# ── 데이터 구조 ───────────────────────────────────────────────────────────────
@dataclass
class RecurTemplate:
    """매월 반복되는 자동이체/자동출금 지출 1종의 템플릿."""

    item: str            # H 항목
    desc_tmpl: str       # I 적요(월 토큰 포함 — 대상월로 치환)
    pay: str             # J 납부방법
    account: str         # K 발생계좌
    vendor: str          # L 거래처
    acctg: str           # M 회계처리
    anchor_day: int      # 표준 결제일(중앙값)
    fixed_amount: float | None  # 고정금액(변동이면 None)
    months: list[int] = field(default_factory=list)  # 출현 월(진단용)
    source_month: int = 6  # 적요 월 토큰 기준 월(최근 출현월)


@dataclass
class PlanLineOut:
    """생성 블록에 기입할 지출 한 줄."""

    item: str
    desc: str
    pay: str
    account: str
    vendor: str
    acctg: str
    amount: float | None  # None=공란(변동)


@dataclass
class IncomeTemplate:
    """매월 반복되는 수입 1종의 템플릿(전자어음 만기·외상대 회수 등)."""

    detail_tmpl: str     # C 내역(월 토큰 포함)
    vendor: str          # D 거래처
    account: str         # E 발생계좌
    note: str            # G 비고
    anchor_day: int
    fixed_amount: float | None
    months: list[int] = field(default_factory=list)
    source_month: int = 6


@dataclass
class IncomeLineOut:
    """생성 블록에 기입할 수입 한 줄."""

    detail: str
    vendor: str
    account: str
    amount: float | None
    note: str = ""


@dataclass
class DayBlock:
    """한 영업일의 생성 블록."""

    date: datetime.date
    expenses: list[PlanLineOut] = field(default_factory=list)
    gen_income: list[IncomeLineOut] = field(default_factory=list)  # 반복 수입(생성)
    carry_income: list[tuple] = field(default_factory=list)  # 기존 7/1 등 보존 수입 (C,D,E,F,G)
    closing_income: list[IncomeLineOut] = field(default_factory=list)   # 마감 입금예정(확정)
    closing_expense: list[PlanLineOut] = field(default_factory=list)    # 마감 결제예정(확정)


# ── 영업일 / 날짜 보정 ────────────────────────────────────────────────────────
def is_business_day(d: datetime.date, holidays: set[datetime.date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def prev_business_day(d: datetime.date, holidays: set[datetime.date]) -> datetime.date:
    """d 가 영업일이 아니면 직전 영업일로 당긴다(영업일이면 그대로). 지출(집행) 기준."""
    cur = d
    while not is_business_day(cur, holidays):
        cur -= datetime.timedelta(days=1)
    return cur


def next_business_day(d: datetime.date, holidays: set[datetime.date]) -> datetime.date:
    """d 가 영업일이 아니면 다음 영업일로 민다(영업일이면 그대로). 수입(수금) 기준.

    말일이 비영업일이면 다음 영업일 = 익월 첫 영업일(월 경계를 넘는다).
    """
    cur = d
    while not is_business_day(cur, holidays):
        cur += datetime.timedelta(days=1)
    return cur


def adjust_payment_day(
    d: datetime.date, side: str, holidays: set[datetime.date] = HOLIDAYS_2026,
) -> datetime.date:
    """비영업일 보정(방향성). side='expense'(집행)→직전 영업일, 'income'(수금)→다음 영업일.

    사용자 확정 기준: 말일이 비영업일일 때 ①집행=해당 월 마지막 영업일(당김),
    ②수금=익월 첫 영업일(밀기). 영업일이면 그대로.
    """
    if side == "income":
        return next_business_day(d, holidays)
    return prev_business_day(d, holidays)


def business_days(year: int, month: int, holidays: set[datetime.date]) -> list[datetime.date]:
    """해당 월의 모든 영업일(오름차순)."""
    if month == 12:
        nxt = datetime.date(year + 1, 1, 1)
    else:
        nxt = datetime.date(year, month + 1, 1)
    last = (nxt - datetime.timedelta(days=1)).day
    out = []
    for day in range(1, last + 1):
        d = datetime.date(year, month, day)
        if is_business_day(d, holidays):
            out.append(d)
    return out


def weekdays(year: int, month: int) -> list[datetime.date]:
    """해당 월의 모든 평일(월~금, 공휴일 포함). 주말만 제외.

    블록은 평일마다 만들되 공휴일 블록은 데이터 없이 빈 채로 둔다(정답지 관행과 동일).
    거래 배치는 여전히 영업일(공휴일 제외)만 대상으로 하므로 공휴일 블록은 비어 있게 된다.
    """
    return business_days(year, month, set())


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)).day


# ── 적요 월 토큰 치환 ─────────────────────────────────────────────────────────
_MONTH_TOKEN = re.compile(r"(?<!\d)(\d{1,2})월")


def shift_desc_month(desc: str, delta: int) -> str:
    """적요 내 'N월' 토큰을 delta 만큼 시프트(1~12 순환). '5.20%' 등은 미변경.

    원본이 zero-padded('07월')면 결과도 zero-pad('08월')로 유지.
    """
    def repl(m: re.Match) -> str:
        raw = m.group(1)
        n = int(raw)
        if not 1 <= n <= 12:
            return m.group(0)
        shifted = (n - 1 + delta) % 12 + 1
        padded = len(raw) >= 2 and raw[0] == "0"
        return f"{shifted:02d}월" if padded else f"{shifted}월"
    return _MONTH_TOKEN.sub(repl, desc)


# ── 반복 템플릿 추출 ──────────────────────────────────────────────────────────
# 방향규칙 비대상 수입 — 내부이체(계좌간 이동)·과제금·환급·환전. 반복 수금 드래프트에서 제외.
# 근거: docs/design/자금계획_거래처방향_분석표.md [E] — 고정 회수일이 없어(실행일·정산일·자계좌이동)
# 매월 반복 배치 대상이 아니다. **이자수익은 제외하지 않는다**(매월 반복 자동입금 — 사용자 확정:
# 금액이 틀려도 자동이체성이면 넣는다). 실제 외상대/전자어음/이자 수금만 템플릿화한다.
_INCOME_EXCLUDE = re.compile(
    r"자계좌|→|과제|시장확대|대전환|자부담|지역혁신|환급|환전"
)


def _is_nonrecurring_income(detail: str, vendor: str = "") -> bool:
    """수입 내역이 방향규칙 비대상(내부이체·과제금·환급·환전)이면 True(이자수익은 대상 포함)."""
    return bool(_INCOME_EXCLUDE.search(f"{detail} {vendor}"))


def _income_group_core(detail: str) -> str:
    """수입 반복 그룹 키. 이자수익류는 내역에 박힌 정산기간 날짜·이율이 매월 달라
    `_desc_core` 로는 안 묶이므로, 이자 계열만 날짜·이율·구분자를 제거한 canonical 키로 묶는다
    (은행별 월 이자 1스트림 → 반복 템플릿). 그 외는 기존 `_desc_core`.
    """
    if "이자" in detail:
        s = re.sub(r"\d", "", detail)          # 정산기간 날짜·이율 숫자 제거
        s = re.sub(r"[~/.\-·\s]", "", s)        # 구분자 제거 → '이자수익'/'RCMS이자수익'/'결산이자'
        return _norm(s)[:18]
    return _desc_core(detail)


def _clean_interest_detail(detail: str) -> str:
    """이자 드래프트용 내역 — 스테일 정산기간(예 '25.6.15~25.12.13')을 제거. 이율(%)은 유지.

    반복 이자는 매월 정산기간이 달라 최근값 그대로 두면 과거 날짜가 노출된다 → 기간만 지운다.
    """
    s = re.sub(
        r"\d{2,4}[.\-]\s?\d{1,2}[.\-]\s?\d{1,2}\.?\s*[~\-]\s*\d{2,4}[.\-]\s?\d{1,2}[.\-]\s?\d{1,2}",
        "", detail,
    )
    s = re.sub(r"/\s*/", "/", s)
    return re.sub(r"\s{2,}", " ", s).strip(" /") or detail


# 앵커일 결정: 실제 출현일은 이미 영업일 보정된 값이라, 월초/월말 집중형은 중앙값이 명목일을
# 놓친다(예: 회비=매월 첫 영업일인데 1/2/3/4일이 섞여 중앙값 2 → 하루 밀림). 집중 구간을 보정.
_MONTH_START_MAX = 5    # 모든 출현일이 ≤5 → '월초(첫 영업일)형' → 최소일(명목 1일 근사)
_MONTH_END_MIN = 25     # 모든 출현일이 ≥25 → '월말(마지막 영업일)형' → 최대일


def _anchor_from_days(days: list[int]) -> int:
    """반복 결제/수금의 대표 결제일(명목). 월초 집중=최소일, 월말 집중=최대일, 그 외 중앙값."""
    ds = sorted(days)
    if not ds:
        return 1
    if ds[-1] <= _MONTH_START_MAX:
        return ds[0]
    if ds[0] >= _MONTH_END_MIN:
        return ds[-1]
    return ds[len(ds) // 2]


def _norm(s) -> str:
    s = str(s or "")
    for tok in ("주식회사", "(주)", "㈜", "(유)", "유한회사", " "):
        s = s.replace(tok, "")
    return s.lower().strip()


# 문자/SMS 알림 수수료 — 은행·월마다 표기가 달라(문자발신/문자알림서비스/SMS통지수수료 등)
# 같은 거래처의 월반복이 desc_core 로 안 묶인다 → 하나의 canonical 로 합쳐 반복 인식.
_SMS_FEE = re.compile(r"문자|sms|에스엠에스", re.I)
_SMS_CTX = re.compile(r"수수료|알림|발신|통지|서비스|통보")


def _desc_core(desc: str) -> str:
    """적요에서 '식별 핵심'만 추출 — 월 토큰 이전까지를 정규화.

    같은 거래처·항목이라도 대출/카드 식별자(예: '…대출9656' vs '9601')가 다르면
    별개 템플릿으로 구분하기 위한 그룹 키. 'N월' 토큰 이후(월별로 바뀌는 부분)는 버린다.
    문자/SMS 알림 수수료는 표기가 매월 달라도 canonical 로 통합(거래처는 키에 별도 포함되어 은행별 구분).
    """
    s = str(desc or "")
    if _SMS_FEE.search(s) and _SMS_CTX.search(s):
        return "문자알림수수료"
    head = re.split(r"\d{1,2}월", s)[0]
    return _norm(head)[:18]


def lookback_period(base: datetime.date, n: int = 6) -> set[tuple[int, int]]:
    """base 월을 포함해 직전 n개월의 (연, 월) 집합. 자동이체 학습 구간(연 경계 처리)."""
    out: set[tuple[int, int]] = set()
    y, m = base.year, base.month
    for _ in range(n):
        out.add((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


def extract_templates(
    plan_path: Path,
    sheet: str = PR.SHEET_NAME,
    *,
    months: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
    pay_methods: tuple[str, ...] = RECUR_PAY_METHODS,
    min_months: int = MIN_MONTHS_RECUR,
    period: set[tuple[int, int]] | None = None,
) -> list[RecurTemplate]:
    """자금계획 시트에서 반복 지출 템플릿을 추출한다(자동이체/자동출금 + 송금).

    months 구간의 지출 라인 중 납부방법이 pay_methods 인 것만 모아 (거래처, 항목, 적요핵심)별로
    묶고, min_months 개월 이상 출현하면 템플릿화한다. 송금은 일회성이 많지만 min_months 임계가
    전시회·통관·구매 같은 단발성을 걸러낸다(급여·식대·임차료·안전관리수수료·재료비 등 월반복만 통과).
    금액은 최신값 기입(1원칙). 적요·계좌·회계처리는 가장 최근(연-월) 출현 라인의 값을 채택한다.
    """
    import openpyxl

    wb = openpyxl.load_workbook(plan_path, data_only=True, read_only=True)
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    markers, year = _resolve_marker_years(rows)

    # (vendor_norm, item_norm) -> list of occurrence dicts
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for k in range(len(markers)):
        mm = markers[k][1]
        if period is not None:
            if (year[k], mm) not in period:
                continue
        elif year[k] not in (2026,) or mm not in months:
            continue
        start = markers[k][0]
        end = _block_end(rows, start)
        for i in range(start, end):
            row = rows[i]
            pay = str(PR._cell(row, C_EXP_PAY) or "").strip()
            if pay not in pay_methods:
                continue
            item = PR._cell(row, C_EXP_ITEM)
            desc = PR._cell(row, C_EXP_DESC)
            amt = PR._num(PR._cell(row, C_EXP_AMOUNT))
            if not (item or desc) or amt <= 0:
                continue
            vendor = PR._cell(row, C_EXP_VENDOR)
            # 내부이체(자계좌이동)·과제금은 반복 지출 학습에서 제외 — 애드혹 자금이동/정부 집행
            # 스케줄이라 예측 불가(대상 밖 합의). 송금 학습 확장 시 자계좌 스윕이 섞이는 것 방지.
            if _is_nonrecurring_income(str(desc or ""), f"{item or ''} {vendor or ''}"):
                continue
            key = (_norm(vendor), _norm(item), _desc_core(str(desc or "")))
            groups[key].append({
                "year": year[k], "month": mm, "day": markers[k][2], "amount": amt,
                "item": str(item or ""), "desc": str(desc or ""),
                "pay": pay, "account": str(PR._cell(row, C_EXP_ACCOUNT) or ""),
                "vendor": str(vendor or ""), "acctg": str(PR._cell(row, C_EXP_ACCTG) or ""),
            })

    # 신규 월반복 자동이체 예외: 자동이체/자동출금이 lookback 최근 2개월 연속 출현이면 2회만으로
    # 인정(예: 4월 시작한 대출 이자 0036/0037). 자동이체는 본디 반복이라 2개월 연속이면 신뢰.
    # 분기·불규칙 항목(월[3,12] 등 최근월 미포함)은 이 조건에 안 걸려 여전히 제외 → 오배치 방지.
    recent2 = set(sorted(period)[-2:]) if period else set()

    templates: list[RecurTemplate] = []
    for key, occ in groups.items():
        occ_months = sorted({o["month"] for o in occ})
        if len(occ_months) < min_months:
            occ_ym = {(o["year"], o["month"]) for o in occ}
            is_auto = any(o["pay"] in AUTO_PAY_METHODS for o in occ)
            if not (is_auto and recent2 and recent2 <= occ_ym):
                continue
        anchor = _anchor_from_days([o["day"] for o in occ])
        # 1원칙: 지난 6개월 데이터가 있으면 가장 최신(연-월) 출현값을 그대로 반영.
        # 금액도 변동이면 공란이 아니라 최신값을 넣는다(어차피 미확정 분홍셀로 표시).
        latest = max(occ, key=lambda o: (o["year"], o["month"]))
        templates.append(RecurTemplate(
            item=latest["item"], desc_tmpl=latest["desc"], pay=latest["pay"],
            account=latest["account"], vendor=latest["vendor"], acctg=latest["acctg"],
            anchor_day=anchor, fixed_amount=round(latest["amount"], 2),
            months=occ_months, source_month=latest["month"],
        ))
    # 안정적 정렬: 결제일 → 거래처
    templates.sort(key=lambda t: (t.anchor_day, t.vendor))
    return templates


def extract_income_templates(
    plan_path: Path,
    sheet: str = PR.SHEET_NAME,
    *,
    months: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
    min_months: int = 4,
    period: set[tuple[int, int]] | None = None,
) -> list[IncomeTemplate]:
    """반복 수입 템플릿 추출 — (거래처, 내역 식별핵심)별 min_months 개월 이상 출현.

    수입은 납부방법 컬럼이 없어 거래처·내역으로만 묶는다. 전자어음 만기·외상대 회수
    등은 금액·일자가 대부분 변동이므로 fixed_amount=None(공란)로 배치하는 드래프트.
    """
    import openpyxl

    wb = openpyxl.load_workbook(plan_path, data_only=True, read_only=True)
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    markers, year = _resolve_marker_years(rows)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for k in range(len(markers)):
        if period is not None:
            if (year[k], markers[k][1]) not in period:
                continue
        elif year[k] != 2026 or markers[k][1] not in months:
            continue
        start = markers[k][0]
        for i in range(start, _block_end(rows, start)):
            row = rows[i]
            det = PR._cell(row, C_INC_DETAIL)
            amt = PR._num(PR._cell(row, C_INC_AMOUNT))
            if not det or amt <= 0:
                continue
            vendor = PR._cell(row, C_INC_VENDOR)
            # 내부이체·과제금·환급·환전(비대상)은 반복 수금으로 학습하지 않는다(이자수익은 포함).
            if _is_nonrecurring_income(str(det), str(vendor or "")):
                continue
            key = (_norm(vendor), _income_group_core(str(det)))
            groups[key].append({
                "year": year[k], "month": markers[k][1], "day": markers[k][2], "amount": amt,
                "detail": str(det), "vendor": str(vendor or ""),
                "account": str(PR._cell(row, C_INC_ACCOUNT) or ""),
                "note": str(PR._cell(row, C_INC_NOTE) or ""),
            })

    out: list[IncomeTemplate] = []
    for occ in groups.values():
        occ_months = sorted({o["month"] for o in occ})
        if len(occ_months) < min_months:
            continue
        anchor = _anchor_from_days([o["day"] for o in occ])
        # 수입도 지출과 동일 '최신값 기입'(1원칙, 2026-07 사용자 재확정): 변동이어도 공란이 아니라
        # 가장 최근(연-월) 출현 금액을 그대로 넣는다 — 정기적으로 들어오는 금액은 전월값이 최선 추정.
        latest = max(occ, key=lambda o: (o["year"], o["month"]))
        fixed = round(latest["amount"], 2)
        det_tmpl = latest["detail"]
        if "이자" in det_tmpl:  # 이자 드래프트는 스테일 정산기간 제거(이율·구분만 유지).
            det_tmpl = _clean_interest_detail(det_tmpl)
        out.append(IncomeTemplate(
            detail_tmpl=det_tmpl, vendor=latest["vendor"],
            account=latest["account"], note=latest["note"],
            anchor_day=anchor, fixed_amount=fixed,
            months=occ_months, source_month=latest["month"],
        ))
    out.sort(key=lambda t: (t.anchor_day, t.vendor))
    return out


def _resolve_marker_years(rows) -> tuple[list[list], list[int | None]]:
    markers: list[list] = []
    for i, row in enumerate(rows):
        md = PR._marker_md(PR._cell(row, C_DATE))
        if md is not None:
            markers.append([i, md[0], md[1], md[2]])
    n = len(markers)
    year: list[int | None] = [None] * n
    cur = None
    prev_m = None
    for k in range(n):
        m, ey = markers[k][1], markers[k][3]
        if ey is not None:
            cur = ey
        elif cur is not None and prev_m is not None and m < prev_m:
            cur += 1
        year[k] = cur
        prev_m = m
    fk = next((k for k in range(n) if year[k] is not None), None)
    if fk is not None:
        for k in range(fk - 1, -1, -1):
            year[k] = year[k + 1] - (1 if markers[k][1] > markers[k + 1][1] else 0)
    return markers, year


def _block_end(rows, start: int) -> int:
    for j in range(start + 1, len(rows)):
        b = PR._cell(rows[j], C_DATE)
        if b is not None and str(b).strip() == PR._END_MARK:
            return j
    return len(rows)


# ── 블록 전개 ─────────────────────────────────────────────────────────────────
def build_day_blocks(
    templates: list[RecurTemplate],
    year: int,
    month: int,
    holidays: set[datetime.date] = HOLIDAYS_2026,
    income_templates: list[IncomeTemplate] | None = None,
    vendor_map: dict[str, dict] | None = None,
) -> list[DayBlock]:
    """대상 월의 모든 평일 블록을 만들고, 반복 템플릿을 보정된 날짜에 배치한다.

    블록은 평일마다 생성하되(공휴일 포함), 거래 배치는 영업일(공휴일 제외)만 대상으로 한다
    → 공휴일 블록은 데이터 없이 빈 채로 남는다(정답지 관행과 동일). 방향성 보정: 지출(집행)=
    직전 영업일(당김)/자동이체는 다음 영업일. 수입은 거래처별 방향(vendor_map)에 따라 후진
    (당월 마지막 영업일)·전진(다음 영업일). vendor_map 이 있으면 거래처명을 canonical 로
    치환한다. 단월 함수라 월 경계는 당월 경계 영업일로 클램프.
    """
    from .vendor_registry import lookup_rule

    bdays = business_days(year, month, holidays)  # 거래 배치 대상(공휴일 제외)
    all_days = weekdays(year, month)              # 블록 생성 대상(공휴일 포함 — 빈 블록)
    blocks = {d: DayBlock(date=d) for d in all_days}
    first_bday, last_bday = bdays[0], bdays[-1]

    def _expense_day(anchor_day: int, pay: str = "") -> datetime.date:
        day = min(anchor_day, _last_day_of_month(year, month))
        base = datetime.date(year, month, day)
        if pay in AUTO_PAY_METHODS:
            # 자동이체/자동출금: 비영업일이면 다음 영업일(밀기), 익월로 넘으면 당월 마지막 영업일 클램프.
            t = next_business_day(base, holidays)
            return last_bday if t > last_bday else t
        # 일반 집행(송금 등): 주말 전 미리 — 직전 영업일(당김).
        t = prev_business_day(base, holidays)
        return first_bday if t < first_bday else t

    def _income_day(anchor_day: int, direction: str = "전진") -> datetime.date:
        day = min(anchor_day, _last_day_of_month(year, month))
        base = datetime.date(year, month, day)
        if direction == "후진":  # 거래처 회수 관행이 후진(당월 마지막 영업일).
            t = prev_business_day(base, holidays)
            return first_bday if t < first_bday else t
        t = next_business_day(base, holidays)
        return last_bday if t > last_bday else t

    for t in templates:
        target = _expense_day(t.anchor_day, t.pay)
        delta = (year * 12 + month) - (2026 * 12 + t.source_month)
        rule = lookup_rule(vendor_map, t.vendor)
        blocks[target].expenses.append(PlanLineOut(
            item=t.item, desc=shift_desc_month(t.desc_tmpl, delta), pay=t.pay,
            account=t.account, vendor=(rule["canonical"] if rule else t.vendor),
            acctg=t.acctg, amount=t.fixed_amount,
        ))
    for it in (income_templates or []):
        rule = lookup_rule(vendor_map, it.vendor)
        direction = rule["direction"] if rule else "전진"
        target = _income_day(it.anchor_day, direction)
        delta = (year * 12 + month) - (2026 * 12 + it.source_month)
        blocks[target].gen_income.append(IncomeLineOut(
            detail=shift_desc_month(it.detail_tmpl, delta),
            vendor=(rule["canonical"] if rule else it.vendor),
            account=it.account, amount=it.fixed_amount, note=it.note,
        ))

    return [blocks[d] for d in all_days]


def place_closing_lines(
    blocks_by_date: dict[datetime.date, DayBlock],
    closing_income: "list | None",
    closing_expense: "list | None",
    holidays: set[datetime.date],
    fallback_date: datetime.date,
    vendor_map: dict[str, dict] | None = None,
) -> None:
    """마감 확정 입금/결제 라인을 전역 블록맵에 방향성 보정으로 배치(월 경계 허용).

    수입 방향은 거래처별(vendor_map): 후진(당월 마지막 영업일)·전진(익월 첫 영업일). 규칙 없으면
    전진(기본값). 지출=직전 영업일(→해당 월 마지막 영업일). vendor_map 있으면 거래처명 canonical 치환.
    범위 밖은 경계 영업일로 클램프, 날짜미상은 fallback_date.
    """
    from .vendor_registry import lookup_rule

    if not blocks_by_date:
        return
    days = sorted(blocks_by_date)
    lo, hi = days[0], days[-1]

    def _place(d: datetime.date, side: str, direction: str = "전진") -> datetime.date:
        if side == "income":
            t = prev_business_day(d, holidays) if direction == "후진" \
                else next_business_day(d, holidays)
        else:
            t = adjust_payment_day(d, side, holidays)
        if t < lo:
            return lo
        if t > hi:
            return hi
        if t not in blocks_by_date:
            after = [x for x in days if x >= t]
            return after[0] if (side == "income" and after) else \
                next((x for x in reversed(days) if x <= t), lo)
        return t

    for line in (closing_income or []):
        rule = lookup_rule(vendor_map, line.vendor)
        direction = rule["direction"] if rule else "전진"
        target = _place(line.date or fallback_date, "income", direction)
        blocks_by_date[target].closing_income.append(
            _income_from_closing(line, rule["canonical"] if rule else None))
    for line in (closing_expense or []):
        rule = lookup_rule(vendor_map, line.vendor)
        target = _place(line.date or fallback_date, "expense")
        blocks_by_date[target].closing_expense.append(
            _expense_from_closing(line, rule["canonical"] if rule else None))


def _income_from_closing(line, canonical: str | None = None) -> IncomeLineOut:
    """마감 입금예정 1줄 → 수입 라인. 거래처는 canonical 치환(있으면). 비고에 원본 일정 보존."""
    vendor = canonical or line.vendor
    return IncomeLineOut(
        detail=f"{vendor}/마감 입금예정", vendor=vendor,
        account="", amount=line.amount,
        note=line.schedule_text or ("날짜미상(검토)" if line.date is None else ""),
    )


def _expense_from_closing(line, canonical: str | None = None) -> PlanLineOut:
    """마감 결제예정 1줄 → 지출 라인. 거래처 canonical 치환(있으면). 항목/적요는 거래처·일정 구성."""
    vendor = canonical or line.vendor
    note_extra = "" if line.date is not None else " [날짜미상-검토]"
    return PlanLineOut(
        item="결제예정", desc=f"{vendor}/마감 결제예정{note_extra}", pay="송금",
        account=line.bank or "", vendor=vendor, acctg="외상매입금",
        amount=line.amount,
    )


def dedup_income_templates(
    income_templates: list[IncomeTemplate], closing_lines: "list",
    vendor_map: dict[str, dict] | None = None,
) -> list[IncomeTemplate]:
    """마감 입금예정에 이미 잡힌 거래처의 반복 수입 템플릿은 제외(중복 배치 방지).

    마감 확정 수입이 우선이므로, 같은 거래처의 반복 수입 드래프트는 버린다. 거래처 비교는
    vendor_map(레지스트리) canonical 로 정규화 후 수행 — 마감/계획 표기가 달라도(예: 마감 'Gana'
    vs 계획 '주식회사 가나') 같은 거래처로 인식해 중복을 제거한다. 마감에 없는 반복 수입은 보존.
    """
    from .vendor_registry import lookup_rule

    def _canon(v: str) -> str:
        rule = lookup_rule(vendor_map, v)
        return _norm(rule["canonical"]) if rule else _norm(v)

    closing_vendors = {_canon(l.vendor) for l in closing_lines}
    return [t for t in income_templates if _canon(t.vendor) not in closing_vendors]


# ── 복사본 기입 ───────────────────────────────────────────────────────────────
_AMOUNT_FMT = "#,##0"


@dataclass
class WriteResult:
    out_path: Path
    sheet: str
    start_row: int
    blocks_written: int
    rows_written: int
    lines_placed: int



# ── 외과적(zip/XML) 기입 — 외부데이터·댓글·프린터설정 전부 보존 ─────────────────
@dataclass
class _PlanMeta:
    start_row: int
    prev_gye_row: int
    blocks: list[DayBlock]
    entry_rows: int
    project_cols: bool  # 원화=True(S/V 과제열 포함), 외화=False


def _prepare_meta(
    src_path: Path, sheet: str, targets: list[tuple[int, int]],
    templates: list[RecurTemplate] | None, holidays: set[datetime.date],
    entry_rows: int, income_templates: list[IncomeTemplate] | None = None,
    closing_income: "list | None" = None, closing_expense: "list | None" = None,
    vendor_map: dict[str, dict] | None = None,
) -> _PlanMeta:
    """openpyxl(read-only)로 시작행·직전 계행·이월 수입·블록을 산출(워크북 미수정).

    closing_income/closing_expense 가 주어지면 마감 확정 입금/결제 라인을 날짜별 블록에 배치.
    """
    import openpyxl

    if templates is None:
        templates = extract_templates(src_path, sheet)
    if income_templates is None:
        income_templates = extract_income_templates(src_path, sheet)
    # 마감 확정 수입과 거래처가 겹치는 반복 수입 드래프트는 제외(중복 방지). 레지스트리 canonical 로 비교.
    if closing_income:
        income_templates = dedup_income_templates(
            income_templates, closing_income, vendor_map)
    project_cols = (sheet == PR.SHEET_NAME)

    wb = openpyxl.load_workbook(src_path, data_only=True, read_only=True)
    rows = list(wb[sheet].iter_rows(values_only=True))
    wb.close()

    markers, year = _resolve_marker_years(rows)
    first_y, first_m = targets[0]
    start_idx = None
    for k in range(len(markers)):
        if year[k] == first_y and markers[k][1] == first_m and markers[k][2] == 1:
            start_idx = markers[k][0]
            break
    if start_idx is None:
        start_idx = max(i for i in range(len(rows))
                        if str(PR._cell(rows[i], C_DATE) or "").strip() == PR._END_MARK) + 1

    prev_gye_row = None
    for i in range(start_idx - 1, -1, -1):
        if str(PR._cell(rows[i], C_DATE) or "").strip() == PR._END_MARK:
            prev_gye_row = i + 1
            break
    if prev_gye_row is None:
        raise ValueError("직전 '계'행을 찾지 못했습니다.")

    carry_by_day: dict[datetime.date, list[tuple]] = defaultdict(list)
    blk_end = _block_end(rows, start_idx)
    for i in range(start_idx, blk_end):
        row = rows[i]
        det = PR._cell(row, C_INC_DETAIL)
        amt = PR._num(PR._cell(row, C_INC_AMOUNT))
        if det and amt > 0:
            d0 = datetime.date(first_y, first_m, 1)
            carry_by_day[d0].append((
                str(det), str(PR._cell(row, C_INC_VENDOR) or ""),
                str(PR._cell(row, C_INC_ACCOUNT) or ""), amt,
                str(PR._cell(row, C_INC_NOTE) or ""),
            ))

    # 날짜 미상 마감 라인의 배치 기준일 = 첫 대상월의 첫 영업일.
    first_y, first_m = targets[0]
    fallback_date = business_days(first_y, first_m, holidays)[0]

    # 대상 월들의 반복 템플릿 블록을 만들고 전역 맵으로 모은다(수입 월경계 보정용).
    blocks: list[DayBlock] = []
    by_date: dict[datetime.date, DayBlock] = {}
    for (y, m) in targets:
        day_blocks = build_day_blocks(templates, y, m, holidays, income_templates, vendor_map)
        for b in day_blocks:
            b.carry_income = carry_by_day.get(b.date, [])
            by_date[b.date] = b
        blocks.extend(day_blocks)

    # 마감 확정 입금/결제를 거래처별 방향으로 전역 배치(수입=거래처방향, 지출=직전영업일).
    place_closing_lines(by_date, closing_income, closing_expense, holidays,
                        fallback_date, vendor_map)

    return _PlanMeta(start_idx + 1, prev_gye_row, blocks, entry_rows, project_cols)


_EXCEL_EPOCH = datetime.date(1899, 12, 30)


def _date_serial(d: datetime.date) -> int:
    return (d - _EXCEL_EPOCH).days


def _capture_styles(xml: str, gye_row: int, start_row: int) -> tuple[dict, dict, str]:
    """기존 시트 XML에서 컬럼별 스타일 인덱스를 채집(신규 셀에 동일 서식 적용)."""
    import re as _re
    row_re = _re.compile(r'<row r="(\d+)"[^>]*>(.*?)</row>', _re.DOTALL)
    cell_re = _re.compile(r'<c r="([A-Z]+)\d+"((?:\s+[\w:]+="[^"]*")*)\s*(?:/>|>(.*?)</c>)', _re.DOTALL)

    data_styles: dict[str, str] = {}
    gye_styles: dict[str, str] = {}
    date_style = ""
    for rm in row_re.finditer(xml):
        rnum = int(rm.group(1))
        if not (start_row - 120 <= rnum < start_row) and rnum != gye_row:
            continue
        body = rm.group(2)
        # 계행 판정: SUM 수식 보유(공유문자열 '계'는 직접 못 보므로 구조로 판정).
        is_gye = (rnum == gye_row) or ("<f>" in body and "SUM(" in body)
        target = gye_styles if is_gye else data_styles
        for cm in cell_re.finditer(body):
            col, attrs, inner = cm.group(1), cm.group(2), cm.group(3) or ""
            sm = _re.search(r'\bs="(\d+)"', attrs)
            s = sm.group(1) if sm else ""
            target.setdefault(col, s)
            # B열 날짜 스타일: 계행 아님 + t 속성 없는 순수 숫자셀(=날짜 serial)에서만 채집.
            if (col == "B" and not is_gye and 't="' not in attrs
                    and "<v>" in inner and not date_style):
                date_style = s
    return data_styles, gye_styles, date_style


# 미확정(자동생성) 데이터 셀 강조색 — 분홍. 사용자가 확정 시 색을 지운다.
_PINK_RGB = "FFF8BBD0"


def _set_xf_fill(xf_open: str, fill_id: int) -> str:
    """<xf ...> 여는 태그의 fillId/applyFill 를 분홍으로 교체·추가."""
    import re as _re
    t = xf_open
    if 'fillId="' in t:
        t = _re.sub(r'fillId="\d+"', f'fillId="{fill_id}"', t)
    else:
        t = t.replace("<xf", f'<xf fillId="{fill_id}"', 1)
    if 'applyFill="' in t:
        t = _re.sub(r'applyFill="[^"]*"', 'applyFill="1"', t)
    else:
        t = t.replace("<xf", '<xf applyFill="1"', 1)
    return t


def _inject_pink_styles(styles_xml: str, base_indices: set[str]) -> tuple[str, dict[str, str]]:
    """styles.xml 에 분홍 fill + 각 base 스타일의 분홍 변형 xf 를 추가.

    반환: (수정된 styles_xml, {base_index_str: pink_index_str}). base 는 데이터 셀이 쓰는
    컬럼별 스타일 인덱스. 테두리 등 기존 서식은 유지하고 fillId 만 분홍으로 바꾼 xf 를 신설한다.
    """
    import re as _re

    # 1) 분홍 fill 추가 → fillId 확보.
    fm = _re.search(r'<fills count="(\d+)">(.*?)</fills>', styles_xml, _re.DOTALL)
    if not fm:
        return styles_xml, {}
    fills_body = fm.group(2)
    fill_id = len(_re.findall(r"<fill\b", fills_body))
    pink_fill = (f'<fill><patternFill patternType="solid">'
                 f'<fgColor rgb="{_PINK_RGB}"/><bgColor indexed="64"/></patternFill></fill>')
    new_fills = f'<fills count="{fill_id + 1}">{fills_body}{pink_fill}</fills>'
    styles_xml = styles_xml[:fm.start()] + new_fills + styles_xml[fm.end():]

    # 2) cellXfs 파싱 → base xf 복제(분홍) 추가.
    xm = _re.search(r'<cellXfs count="(\d+)">(.*?)</cellXfs>', styles_xml, _re.DOTALL)
    if not xm:
        return styles_xml, {}
    xfs_body = xm.group(2)
    xf_list = _re.findall(r"<xf\b[^>]*?(?:/>|>.*?</xf>)", xfs_body, _re.DOTALL)
    next_idx = len(xf_list)
    pink_map: dict[str, str] = {}
    additions = []
    for base in sorted(base_indices, key=lambda s: int(s) if s.isdigit() else -1):
        if not base.isdigit() or int(base) >= len(xf_list):
            continue
        src = xf_list[int(base)]
        # 여는 태그만 수정(자식 alignment 등은 보존).
        om = _re.match(r"<xf\b[^>]*?(/?>)", src)
        open_tag = src[:om.end()]
        rest = src[om.end():]
        # 자체닫힘이면 그대로, 아니면 여는 태그 교체.
        if open_tag.endswith("/>"):
            new_xf = _set_xf_fill(open_tag[:-2] + ">", fill_id)[:-1] + "/>"
        else:
            new_xf = _set_xf_fill(open_tag, fill_id) + rest
        additions.append(new_xf)
        pink_map[base] = str(next_idx)
        next_idx += 1
    new_count = len(xf_list) + len(additions)
    new_xfs = f'<cellXfs count="{new_count}">{xfs_body}{"".join(additions)}</cellXfs>'
    styles_xml = styles_xml[:xm.start()] + new_xfs + styles_xml[xm.end():]
    return styles_xml, pink_map


def _inject_nofill_styles(styles_xml: str, base_indices: set[str]) -> tuple[str, dict[str, str]]:
    """각 base 스타일의 '채우기 없음'(fillId=0) 변형 xf 를 styles.xml 에 추가.

    생성 블록 entry 셀이 원본의 회색 채우기 대신 '채우기 없음'을 쓰도록 한다.
    테두리·글꼴·정렬 등 나머지 서식은 base 그대로 두고 fillId 만 0(OOXML 예약값=none)으로
    바꾼 xf 를 신설한다(<fills> 는 손대지 않는다 — fillId 0 은 어느 워크북이나 none 이다).
    반환: (수정된 styles_xml, {base_index_str: nofill_index_str}).
    """
    import re as _re

    xm = _re.search(r'<cellXfs count="(\d+)">(.*?)</cellXfs>', styles_xml, _re.DOTALL)
    if not xm:
        return styles_xml, {}
    xfs_body = xm.group(2)
    xf_list = _re.findall(r"<xf\b[^>]*?(?:/>|>.*?</xf>)", xfs_body, _re.DOTALL)
    next_idx = len(xf_list)
    nofill_map: dict[str, str] = {}
    additions = []
    for base in sorted(base_indices, key=lambda s: int(s) if s.isdigit() else -1):
        if not base.isdigit() or int(base) >= len(xf_list):
            continue
        src = xf_list[int(base)]
        om = _re.match(r"<xf\b[^>]*?(/?>)", src)
        open_tag = src[:om.end()]
        rest = src[om.end():]
        if open_tag.endswith("/>"):
            new_xf = _set_xf_fill(open_tag[:-2] + ">", 0)[:-1] + "/>"
        else:
            new_xf = _set_xf_fill(open_tag, 0) + rest
        additions.append(new_xf)
        nofill_map[base] = str(next_idx)
        next_idx += 1
    new_count = len(xf_list) + len(additions)
    new_xfs = f'<cellXfs count="{new_count}">{xfs_body}{"".join(additions)}</cellXfs>'
    styles_xml = styles_xml[:xm.start()] + new_xfs + styles_xml[xm.end():]
    return styles_xml, nofill_map


def _cell_xml(col: str, row: int, value, kind: str, style: str) -> str:
    """단일 셀 XML. kind ∈ {text, num, formula, date}."""
    from . import xlsx_patch as XP
    s = f' s="{style}"' if style else ""
    ref = f"{col}{row}"
    if value is None:
        return ""
    if kind == "text":
        return (f'<c r="{ref}"{s} t="inlineStr"><is>'
                f'<t xml:space="preserve">{XP._esc(value)}</t></is></c>')
    if kind == "formula":
        return f'<c r="{ref}"{s}><f>{XP._esc(value)}</f></c>'
    # num / date 모두 숫자 <v>
    return f'<c r="{ref}"{s}><v>{XP._fmt_num(value)}</v></c>'


# 미리 서식(테두리)을 입힐 그리드 컬럼 — 데이터 유무와 무관하게 항상 그린다.
# entry 셀 채우기는 '채우기 없음'(회색 제거) — 아래 _inject_nofill_styles 로 처리.
_ENTRY_GRID = ("B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q")


def _empty_cell(col: str, row: int, style: str) -> str:
    """값 없는 스타일 전용 셀 — 빈 칸에도 그리드 테두리를 유지한다(채우기는 없음)."""
    return f'<c r="{col}{row}" s="{style}"/>' if style else ""


def _emit_grid(row: int, data: dict[str, str], styles: dict, *, extra: str = "") -> str:
    """한 줄을 B~Q 전 컬럼으로 렌더(데이터 있으면 데이터, 없으면 스타일만). A·과제열은 extra/data.

    data: {col: 완성된 셀 XML}. styles: 컬럼별 스타일 인덱스(빈 칸용).
    """
    cells = []
    if data.get("A"):
        cells.append(data["A"])
    for col in _ENTRY_GRID:
        v = data.get(col)
        cells.append(v if v else _empty_cell(col, row, styles.get(col, "")))
    body = "".join(c for c in cells if c) + extra
    return f'<row r="{row}">{body}</row>'


def _render_rows(meta: _PlanMeta, data_styles, gye_styles, date_style,
                 pink_styles: dict[str, str] | None = None) -> str:
    """블록들을 <row> XML 문자열로 렌더(start_row 부터 순차).

    블록은 '미리 다 채워진 빈 양식'으로 만든다 — 모든 entry 줄·계 줄에 B~Q 전 그리드 컬럼을
    기존 테두리 서식으로 깔되 채우기는 없음(회색 제거), 데이터가 있는 칸만 값을 기입한다.
    pink_styles 가 주어지면 자동생성 데이터 값 셀(C~O)에 분홍(미확정) 스타일을 적용한다.
    """
    pink = pink_styles or {}

    def ds(col: str) -> str:  # 데이터 값 셀 스타일 — 분홍 우선, 없으면 기본.
        return pink.get(col, data_styles.get(col, ""))

    out = []
    cur = meta.start_row
    seq = 1
    prev_gye = meta.prev_gye_row
    for b in meta.blocks:
        top = cur
        # 수입 = 이월(carry) + 마감 확정 입금 + 반복 수입 드래프트.
        income = (
            [IncomeLineOut(det, ven, acc, amt, note)
             for (det, ven, acc, amt, note) in b.carry_income]
            + list(b.closing_income) + list(b.gen_income)
        )
        # 지출 = 마감 확정 결제 + 반복 자동이체 드래프트.
        expenses = list(b.closing_expense) + list(b.expenses)
        # 블록 행수: 표준 15줄, 단 라인이 많으면 그 블록만 확장(계행 SUM 범위 적응).
        block_rows = max(meta.entry_rows, len(income), len(expenses))
        # entry 영역 — 매 줄 전 그리드 서식, 데이터만 기입.
        for off in range(block_rows):
            r = top + off
            data: dict[str, str] = {}
            if off == 0:
                data["A"] = _cell_xml("A", r, seq, "num", data_styles.get("A", ""))
                data["B"] = _cell_xml("B", r, _date_serial(b.date), "date",
                                      date_style or data_styles.get("B", ""))
            if off < len(income):
                inc = income[off]
                data["C"] = _cell_xml("C", r, inc.detail, "text", ds("C"))
                data["D"] = _cell_xml("D", r, inc.vendor, "text", ds("D"))
                data["E"] = _cell_xml("E", r, inc.account, "text", ds("E"))
                if inc.amount is not None:
                    data["F"] = _cell_xml("F", r, inc.amount, "num", ds("F"))
                if inc.note:
                    data["G"] = _cell_xml("G", r, inc.note, "text", ds("G"))
            if off < len(expenses):
                e = expenses[off]
                data["H"] = _cell_xml("H", r, e.item, "text", ds("H"))
                data["I"] = _cell_xml("I", r, e.desc, "text", ds("I"))
                data["J"] = _cell_xml("J", r, e.pay, "text", ds("J"))
                data["K"] = _cell_xml("K", r, e.account, "text", ds("K"))
                data["L"] = _cell_xml("L", r, e.vendor, "text", ds("L"))
                data["M"] = _cell_xml("M", r, e.acctg, "text", ds("M"))
                if e.amount is not None:
                    data["N"] = _cell_xml("N", r, e.amount, "num", ds("N"))
            out.append(_emit_grid(r, data, data_styles))
        # 계행 — entry 영역(동적 block_rows) 직후. 전 그리드 서식 + 합계/잔액 수식.
        gye = top + block_rows
        last_entry = gye - 1
        gdata: dict[str, str] = {
            "B": _cell_xml("B", gye, "계", "text", gye_styles.get("B", "")),
            "G": _cell_xml("G", gye, f"SUM(F{top}:F{last_entry})", "formula", gye_styles.get("G", "")),
            "H": _cell_xml("H", gye, "지출소계 :", "text", gye_styles.get("H", "")),
            "O": _cell_xml("O", gye, f"SUM(N{top}:N{last_entry})", "formula", gye_styles.get("O", "")),
            "P": _cell_xml("P", gye, f"P{prev_gye}+SUM(G{gye})-SUM(O{gye})", "formula", gye_styles.get("P", "")),
        }
        extra = ""
        if meta.project_cols:
            # 원화: 사용가능액=총잔액−과제계좌, 과제잔액 carry, 검증(P=U).
            gdata["Q"] = _cell_xml("Q", gye, f"P{gye}-S{gye}", "formula", gye_styles.get("Q", ""))
            extra = (_cell_xml("S", gye, f"S{prev_gye}", "formula", gye_styles.get("S", ""))
                     + _cell_xml("V", gye, f"P{gye}=U{gye}", "formula", gye_styles.get("V", "")))
        else:
            # 외화: 과제 구분 없음 → 사용가능금액=총잔액.
            gdata["Q"] = _cell_xml("Q", gye, f"P{gye}", "formula", gye_styles.get("Q", ""))
        out.append(_emit_grid(gye, gdata, gye_styles, extra=extra))
        prev_gye = gye
        cur = gye + 1
        seq += 1
    return "".join(out), cur - 1


def write_plan_surgical(
    src_path: Path,
    out_path: Path,
    targets: list[tuple[int, int]],
    *,
    sheet: str = PR.SHEET_NAME,
    templates: list[RecurTemplate] | None = None,
    income_templates: list[IncomeTemplate] | None = None,
    closing_income: "list | None" = None,
    closing_expense: "list | None" = None,
    entry_rows: int = ENTRY_ROWS_PER_DAY,
    holidays: set[datetime.date] = HOLIDAYS_2026,
    fresh: bool = True,
    vendor_map: dict[str, dict] | None = None,
) -> WriteResult:
    """외과적(zip/XML) 기입 — 외부데이터 연결·댓글·프린터설정 등 모든 zip 엔트리 보존.

    openpyxl 라운드트립의 드롭 문제를 피하기 위해 대상 시트 XML에만 행을 추가하고
    나머지 엔트리는 바이트 그대로 복사한다. calcChain 은 제거하고 fullCalcOnLoad 를
    켜서 Excel 이 열 때 신규 수식을 재계산하게 한다. 원화·외화 시트 모두 지원
    (외화는 과제열 S/V 없이 계행 생성).

    fresh=True: 원본을 out 으로 새로 복사 후 기입(첫 시트). fresh=False: 기존 out 을
    그대로 두고 다른 시트만 추가 기입(원화→외화 연쇄 시 두 번째 호출).
    """
    import re as _re
    import shutil
    import zipfile
    from . import xlsx_patch as XP

    if fresh:
        shutil.copyfile(src_path, out_path)
    meta = _prepare_meta(src_path, sheet, targets, templates, holidays,
                         entry_rows, income_templates,
                         closing_income=closing_income, closing_expense=closing_expense,
                         vendor_map=vendor_map)

    with zipfile.ZipFile(out_path) as zf:
        sheet_path = XP._sheet_xml_path(zf, sheet)
        xml = zf.read(sheet_path).decode("utf-8")
        infos = zf.infolist()
        data = {zi.filename: zf.read(zi.filename) for zi in infos}

    data_styles, gye_styles, date_style = _capture_styles(xml, meta.prev_gye_row, meta.start_row)

    pink_styles: dict[str, str] = {}
    styles_key = "xl/styles.xml"
    if styles_key in data:
        styles_xml = data[styles_key].decode("utf-8")
        # 1) 생성 블록 entry 셀은 '채우기 없음'(원본 회색 채우기 제거) — 테두리만 유지.
        #    A/B(순번·일자) + B~Q 그리드 스타일과 날짜 스타일을 no-fill 변형으로 치환한다.
        grid_cols = ("A",) + _ENTRY_GRID
        nofill_base = {data_styles[c] for c in grid_cols if data_styles.get(c)}
        if date_style:
            nofill_base.add(date_style)
        styles_xml, nofill_map = _inject_nofill_styles(styles_xml, nofill_base)
        data_styles = {c: nofill_map.get(s, s) for c, s in data_styles.items()}
        if date_style:
            date_style = nofill_map.get(date_style, date_style)

        # 2) 미확정(자동생성) 데이터 값 셀 분홍 — no-fill base 에서 파생(테두리 유지, fill=분홍).
        data_cols = ("C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O")
        base_idx = {data_styles[c] for c in data_cols if data_styles.get(c)}
        styles_xml, pink_map = _inject_pink_styles(styles_xml, base_idx)
        data[styles_key] = styles_xml.encode("utf-8")
        if pink_map:
            pink_styles = {c: pink_map[data_styles[c]]
                           for c in data_cols
                           if data_styles.get(c) in pink_map}

    # 기존 start_row 이상 행 삭제(sheetData 내).
    def _del_row(m):
        return "" if int(m.group(1)) >= meta.start_row else m.group(0)
    xml = _re.sub(r'<row r="(\d+)"[^>]*?(?:/>|>.*?</row>)', _del_row, xml, flags=_re.DOTALL)

    rows_xml, last_row = _render_rows(meta, data_styles, gye_styles, date_style, pink_styles)
    sd = xml.index("</sheetData>")
    xml = xml[:sd] + rows_xml + xml[sd:]

    # dimension 갱신(끝 행만 키움).
    xml = _re.sub(r'(<dimension ref="[A-Z]+\d+:[A-Z]+)\d+("/>)',
                  rf'\g<1>{last_row}\g<2>', xml, count=1)

    data[sheet_path] = xml.encode("utf-8")
    if "xl/workbook.xml" in data:
        data["xl/workbook.xml"] = XP._set_full_recalc(
            data["xl/workbook.xml"].decode("utf-8")).encode("utf-8")

    # calcChain 제거 + 참조 정리(낡은 체인이 삭제 셀을 가리켜 복구창 뜨는 것 방지).
    drop = {"xl/calcChain.xml"}
    ct_key = "[Content_Types].xml"
    if ct_key in data:
        ct = data[ct_key].decode("utf-8")
        ct = _re.sub(r'<Override PartName="/xl/calcChain\.xml"[^>]*/>', "", ct)
        data[ct_key] = ct.encode("utf-8")
    rels_key = "xl/_rels/workbook.xml.rels"
    if rels_key in data:
        rl = data[rels_key].decode("utf-8")
        rl = _re.sub(r'<Relationship[^>]*Target="calcChain\.xml"[^>]*/>', "", rl)
        data[rels_key] = rl.encode("utf-8")

    tmp = Path(str(out_path) + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for zi in infos:
            if zi.filename in drop:
                continue
            zout.writestr(zi, data[zi.filename])
    tmp.replace(out_path)

    lines = sum(len(b.expenses) + len(b.closing_expense) for b in meta.blocks)
    return WriteResult(
        out_path=Path(out_path), sheet=sheet, start_row=meta.start_row,
        blocks_written=len(meta.blocks), rows_written=last_row - meta.start_row + 1,
        lines_placed=lines,
    )


# ── 미래 블록 절단(복사본에서 마감 기준일 이후 표 제거) ────────────────────────
def last_business_day_on_or_before(d: datetime.date,
                                   holidays: set[datetime.date] = HOLIDAYS_2026) -> datetime.date:
    """d 이하의 마지막 영업일(5/31 일요일 → 5/29 금요일)."""
    return prev_business_day(d, holidays)


def next_n_months(base: datetime.date, n: int = 2) -> list[tuple[int, int]]:
    """base 월 다음 달부터 n개월의 (연, 월) 목록. 5월 기준 → [(2026,6),(2026,7)]."""
    out: list[tuple[int, int]] = []
    y, m = base.year, base.month
    for _ in range(n):
        m += 1
        if m == 13:
            m = 1
            y += 1
        out.append((y, m))
    return out


def _truncate_sheet_xml(xml: str, from_row: int) -> str:
    """sheetData 내 from_row(1-based) 이상 <row> 를 모두 제거하고 dimension 보정."""
    import re as _re

    def _del(m):
        return "" if int(m.group(1)) >= from_row else m.group(0)

    xml = _re.sub(r'<row r="(\d+)"[^>]*?(?:/>|>.*?</row>)', _del, xml, flags=_re.DOTALL)
    last = from_row - 1
    xml = _re.sub(r'(<dimension ref="[A-Z]+\d+:[A-Z]+)\d+("/>)',
                  rf'\g<1>{last}\g<2>', xml, count=1)
    return xml


def truncate_plan_after(
    src_path: Path, out_path: Path, boundary: datetime.date,
    *, sheets: tuple[str, ...] = (PR.SHEET_NAME, PR.SHEET_NAME_FX),
) -> dict[str, int]:
    """boundary 일자 이후(>boundary)의 모든 일자 블록을 복사본에서 제거.

    각 시트에서 boundary 보다 큰 첫 일자 마커 행부터 끝까지 삭제한다. 5월 마감(5/31)
    기준이면 5/29 블록까지 남기고 6/1 블록부터 제거. 외부데이터·서식 보존(zip/XML).
    반환: {시트명: 제거시작행(없으면 0)}.
    """
    import re as _re
    import shutil
    import zipfile
    from . import xlsx_patch as XP

    shutil.copyfile(src_path, out_path)
    result: dict[str, int] = {}

    with zipfile.ZipFile(out_path) as zf:
        infos = zf.infolist()
        data = {zi.filename: zf.read(zi.filename) for zi in infos}
        sheet_paths = {}
        import openpyxl
        wb = openpyxl.load_workbook(src_path, data_only=True, read_only=True)
        present = set(wb.sheetnames)
        wb.close()
        for sh in sheets:
            if sh in present:
                sheet_paths[sh] = XP._sheet_xml_path(zf, sh)

    for sh, spath in sheet_paths.items():
        import openpyxl
        wb = openpyxl.load_workbook(src_path, data_only=True, read_only=True)
        rows = list(wb[sh].iter_rows(values_only=True))
        wb.close()
        markers, year = _resolve_marker_years(rows)
        from_row = 0
        for k in range(len(markers)):
            if year[k] is None:
                continue
            try:
                d = datetime.date(year[k], markers[k][1], markers[k][2])
            except ValueError:
                continue
            if d > boundary:
                from_row = markers[k][0] + 1  # 1-based
                break
        if from_row:
            xml = data[spath].decode("utf-8")
            data[spath] = _truncate_sheet_xml(xml, from_row).encode("utf-8")
        result[sh] = from_row

    if "xl/workbook.xml" in data:
        data["xl/workbook.xml"] = XP._set_full_recalc(
            data["xl/workbook.xml"].decode("utf-8")).encode("utf-8")
    drop = {"xl/calcChain.xml"}
    ct_key = "[Content_Types].xml"
    if ct_key in data:
        ct = _re.sub(r'<Override PartName="/xl/calcChain\.xml"[^>]*/>', "",
                     data[ct_key].decode("utf-8"))
        data[ct_key] = ct.encode("utf-8")
    rels_key = "xl/_rels/workbook.xml.rels"
    if rels_key in data:
        rl = _re.sub(r'<Relationship[^>]*Target="calcChain\.xml"[^>]*/>', "",
                     data[rels_key].decode("utf-8"))
        data[rels_key] = rl.encode("utf-8")

    tmp = Path(str(out_path) + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for zi in infos:
            if zi.filename in drop:
                continue
            zout.writestr(zi, data[zi.filename])
    tmp.replace(out_path)
    return result


# ── 오케스트레이터: 마감자료 기반 자금계획 자동작성 ────────────────────────────
@dataclass
class PlanFromClosingResult:
    out_path: Path
    base_date: datetime.date
    targets: list[tuple[int, int]]
    sheets: list[WriteResult]
    closing_counts: dict[str, int]   # {'krw_income':7, 'krw_expense':7, ...}
    undated: int


def generate_from_closing(
    plan_path: Path,
    closing_path: Path,
    out_path: Path,
    *,
    n_target_months: int = 2,
    lookback_months: int = 6,   # 6개월 유지 — 18개월 시 대출이자 표기드리프트로 이중계상(2026-07-02 검증)
    krw_only: bool = False,
    holidays: set[datetime.date] | None = None,
    vendor_map: dict[str, dict] | None = None,
) -> PlanFromClosingResult:
    """월마감 자료 + 과거 자동이체로 자금계획(원화·외화)을 자동작성한다.

    흐름: 마감자료 읽기 → 기준일/대상월 산출 → 미래 블록 절단 → 자동이체 템플릿 학습
    → 마감 입금/결제 라인을 날짜별 블록에 배치하며 외과적 기입(원화·외화 연쇄).

    holidays=None(기본): 기준일·대상월 연도의 공휴일을 정부 API/캐시(`holidays.get_holidays`)로
    해석한다(무키·오프라인이면 검증된 정적 세트로 폴백). 명시 전달 시 그 집합을 그대로 쓴다.
    """
    from . import closing_reader as CR

    plan_path, closing_path, out_path = Path(plan_path), Path(closing_path), Path(out_path)
    closing = CR.read_closing(closing_path)
    base = closing.base_date
    targets = next_n_months(base, n_target_months)
    if holidays is None:
        from . import holidays as _hol
        holidays = _hol.get_holidays(base.year, *{y for y, _ in targets})
    if vendor_map is None:
        from .vendor_registry import vendor_map_from_seed
        vendor_map = vendor_map_from_seed()
    boundary = last_business_day_on_or_before(base, holidays)
    period = lookback_period(base, lookback_months)

    # 1) 미래(>마감 영업일) 블록 절단 → 중간 파일.
    truncated = Path(str(out_path) + ".trunc.xlsx")
    sheets = (PR.SHEET_NAME,) if krw_only else (PR.SHEET_NAME, PR.SHEET_NAME_FX)
    truncate_plan_after(plan_path, truncated, boundary, sheets=sheets)

    # 2) 각 시트별로 자동이체 학습 + 마감 라인 배치 → 외과적 기입(연쇄).
    written: list[WriteResult] = []
    for idx, sh in enumerate(sheets):
        if sh == PR.SHEET_NAME:
            c_inc, c_exp = closing.krw_income, closing.krw_expense
        else:
            c_inc, c_exp = closing.usd_income, closing.usd_expense
        exp_t = extract_templates(truncated, sh, period=period)
        inc_t = extract_income_templates(truncated, sh, period=period)
        res = write_plan_surgical(
            truncated, out_path, targets, sheet=sh,
            templates=exp_t, income_templates=inc_t,
            closing_income=c_inc, closing_expense=c_exp,
            holidays=holidays, fresh=(idx == 0), vendor_map=vendor_map,
        )
        written.append(res)

    try:
        truncated.unlink()
    except OSError:
        pass

    return PlanFromClosingResult(
        out_path=out_path, base_date=base, targets=targets, sheets=written,
        closing_counts={
            "krw_income": len(closing.krw_income), "krw_expense": len(closing.krw_expense),
            "usd_income": len(closing.usd_income), "usd_expense": len(closing.usd_expense),
        },
        undated=closing.undated,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    """자금계획 자동생성 CLI — 원본 복사본에 대상 월 영업일 블록을 외과적 기입."""
    import argparse

    ap = argparse.ArgumentParser(description="자금계획 자동생성(자동이체/자동출금 반복 지출 + 반복 수입)")
    ap.add_argument("--src", required=True, help="원본 자금계획 .xlsx (수정하지 않음)")
    ap.add_argument("--out", required=True, help="생성 복사본 출력 경로 .xlsx")
    ap.add_argument("--months", default="2026-07,2026-08",
                    help="대상 월 목록 YYYY-MM (쉼표 구분)")
    ap.add_argument("--krw-only", action="store_true", help="원화 시트만 생성(외화 생략)")
    args = ap.parse_args(argv)

    src = Path(args.src)
    out = Path(args.out)
    if not src.exists():
        print(f"원본 없음: {src}")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    targets: list[tuple[int, int]] = []
    for tok in args.months.split(","):
        y, m = tok.strip().split("-")
        targets.append((int(y), int(m)))

    print(f"원본: {src.name}")
    sheets = [PR.SHEET_NAME] if args.krw_only else [PR.SHEET_NAME, PR.SHEET_NAME_FX]
    for idx, sh in enumerate(sheets):
        exp_t = extract_templates(src, sh)
        inc_t = extract_income_templates(src, sh)
        res = write_plan_surgical(src, out, targets, sheet=sh,
                                  templates=exp_t, income_templates=inc_t,
                                  fresh=(idx == 0))
        print(f"  [{sh}] 지출템플릿 {len(exp_t)}종·수입템플릿 {len(inc_t)}종 "
              f"→ 블록 {res.blocks_written}·기입행 {res.rows_written}·지출배치 {res.lines_placed}건")
    print(f"생성: {out}  (방식: 외과적 zip/XML, 외부데이터 보존)")
    print("  Excel에서 열면 fullCalcOnLoad로 잔액 수식이 재계산됩니다(변동 항목 금액은 수기 입력).")
    return 0


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(main(_sys.argv[1:]))
