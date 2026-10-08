"""사용자 expense 메시지의 콤마 구분 형식을 직접 파싱해 compose_expense_report 인자 생성.

LLM 을 거치지 않는 fast-path. 다음 사건들의 근본 해결:
- expense_error_v6/v7/v8/v9: LLM 이 tool_call 안 쓰고 prose/bare JSON 으로 인자 흘림
- expense_error_v10: 입력 토큰 7836/8192 로 output 부족 → LLM 이 args 잘라먹음

표준 사용자 입력 형식(parser 가 인식하는 형식):
    법인카드 지출 X                              ← 섹션 헤더 + '없음' 마커
                                                  (또는 빈 줄로 다음 섹션 진입)
    개인카드 지출
    해당없음, 2/7 토, 안전화 구매, 72000, 워크업 대구 반야월점
    여비교통비, 2/9 월, 평택지제역 → 동대구역 SRT, 29500, 주식회사 에스알
    여비교통비, 2/10 화, 숙소 근처 → 평택지제역 콜택시, 30000, 미래 대리우전

    2026년 2월 배재병 익스펜스 보고 작성해줘     ← year/month/author 메타 추출

파싱 실패 시 None 반환 → 호출자는 LLM 흐름으로 fallback.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import Any

_log = logging.getLogger("expense_parser")

# 도구 description 과 동기 — 변경 시 양쪽 같이 업데이트.
_EXPENSE_CATEGORIES = (
    "접대비", "복리후생비", "여비교통비", "소모품비",
    "차량유지비", "지급수수료", "도서인쇄비", "해당없음",
)

# 섹션 헤더 — '법인카드 지출' '개인카드 지출' (공백/콜론 변형 허용).
# 사용자가 흔히 쓰는 '[ 법인카드 지출 내역 - Total ... ]' 형태도 흡수:
# - 앞 '[' 0~1개, 뒤 '내역' 어휘, ':' '=' '-' 같은 구분자 흡수.
# - tail(잉여 'Total 333,050원 ]' 등)은 _parse_line 시도 후 None → 자연 무시.
_CORP_SECTION_RE = re.compile(
    r"^\s*\[?\s*법인\s*카드\s*(?:지출)?(?:\s*내역)?\s*[:=\-]?\s*",
    re.IGNORECASE,
)
_PERSONAL_SECTION_RE = re.compile(
    r"^\s*\[?\s*개인\s*카드\s*(?:지출)?(?:\s*내역)?\s*[:=\-]?\s*",
    re.IGNORECASE,
)

# 섹션 비어있음 마커 — 헤더 뒤에 오면 그 섹션은 0건.
_EMPTY_MARKERS = ("X", "x", "없음", "-", "0", "없", "없습니다", "no", "none", "skip")

# 메타 추출 — 요청 메시지 어딘가에서 'YYYY년 M월', '이름 익스펜스/expense/보고' 패턴.
_YM_RE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월")
# 작성자 이름 추출 — 두 가지 흔한 위치를 모두 매치:
#   (a) trigger *앞* 에 이름: "백동주 익스펜스/expense/지출 보고/보고서 작성/영수증"
#   (b) trigger *뒤* 에 키워드+이름: "이름 백동주" / "작성자: 백동주" / "by 백동주"
# group(1) 또는 group(2) 둘 중 하나에 매치된 이름이 들어간다.
#
# 주의: `\s*` 가 아니라 `[ \t]*` — 줄 경계를 넘어가 본문(vendor 등)을 author 로
# 오인하는 사고 차단(`가맹점\nexpense` 가 `가맹점 expense` 로 매치되던 회귀).
# "작성" 단독 키워드는 의도치 않게 "작성해" 의 "해줘" 같은 토큰을 잡아서 제외.
_AUTHOR_RE = re.compile(
    r"(?:"
    r"([가-힣]{2,5})[ \t]*(?:익스펜스|expense|지출[ \t]*보고|보고서?[ \t]*작성|영수증)"
    r"|"
    r"(?:이름|작성자|by|담당자)[ \t]*[:\-=]?[ \t]*([가-힣]{2,5})"
    r")",
    re.IGNORECASE,
)

# 카테고리 alias — tools.py 의 _CATEGORY_ALIASES 와 동기. 중복 정의지만 expense_parser
# 가 tools.py 에 의존하면 circular import 위험이라 미러링.
_CATEGORY_ALIASES: dict[str, str] = {
    "교통비": "여비교통비", "택시비": "여비교통비", "택시": "여비교통비",
    "srt": "여비교통비", "ktx": "여비교통비", "지하철": "여비교통비",
    "버스": "여비교통비", "항공료": "여비교통비", "철도": "여비교통비",
    "유류비": "차량유지비", "주유": "차량유지비", "주유비": "차량유지비", "세차": "차량유지비",
    "점심": "복리후생비", "저녁": "복리후생비", "회식": "복리후생비",
    "식사": "복리후생비", "간식": "복리후생비", "다과": "복리후생비",
    "접대": "접대비", "거래처식사": "접대비",
    "사무용품": "소모품비", "문구": "소모품비", "비품": "소모품비",
    "책": "도서인쇄비", "도서": "도서인쇄비", "인쇄": "도서인쇄비", "복사": "도서인쇄비",
    "은행수수료": "지급수수료", "수수료": "지급수수료",
    "잡비": "해당없음", "기타": "해당없음",
}


def _normalize_category(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s in _EXPENSE_CATEGORIES:
        return s
    low = s.lower().replace(" ", "")
    for alias, target in _CATEGORY_ALIASES.items():
        if alias.lower().replace(" ", "") in low:
            return target
    return None


def _normalize_amount(raw: str) -> float | None:
    """'29,500원' → 29500.0. 0/음수/비숫자 → None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    cleaned = re.sub(r"[,\s원₩\$₩]", "", s)
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
        return None
    try:
        v = float(cleaned)
    except ValueError:
        return None
    if v <= 0:
        return None
    return v


def _parse_date(raw: str, default_year: int) -> str | None:
    """'2/9 월' → '2026-02-09' (default_year 사용). 'YYYY-MM-DD' 도 흡수."""
    s = (raw or "").strip()
    if not s:
        return None
    # 요일 글자 제거(토/월/화/수/목/금/일, 다중 글자도).
    s = re.sub(r"\s*[월화수목금토일]+\s*$", "", s).strip()

    # 1) YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
    m = re.match(r"^(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})$", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        # 2) M/D 또는 MM/DD (연도 누락) → default_year 사용
        m = re.match(r"^(\d{1,2})\s*[/.-]\s*(\d{1,2})$", s)
        if not m:
            return None
        mo, d = int(m.group(1)), int(m.group(2))
        y = default_year

    try:
        return _dt.date(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _merge_thousand_separators(text: str) -> str:
    """숫자 안의 천 단위 콤마를 제거 — '29,500' → '29500', '1,234,567' → '1234567'.

    필드 구분자인 콤마는 보존(숫자 뒤에 정확히 3자리 숫자가 따라올 때만 매치).
    """
    prev = None
    cur = text
    while prev != cur:
        prev = cur
        cur = re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", cur)
    return cur


def _looks_like_data_line(raw: str) -> bool:
    """5필드 구조(구분자로 5+ 토큰)를 가진 '지출 라인 의도'인지 판별.

    chatter/메타 라인(구분자 부족, < 5 토큰)과 구분한다. _parse_line 이 None 을 반환했을 때
    이 함수가 True 면 '데이터 라인인데 날짜/금액 등이 깨져 누락된 것'으로 보고 경고 카운트에 넣는다.
    기존 _parse_line 시그니처/동작은 건드리지 않는다(원본 보존).
    """
    merged = _merge_thousand_separators(raw)
    parts = [p.strip() for p in re.split(r"\s*,\s*|\s+/\s+", merged) if p.strip()]
    return len(parts) >= 5


def _parse_line(raw: str, source: str, default_year: int) -> dict | None:
    r"""한 줄을 5필드(category, date, purpose, amount, vendor) line 객체로 변환.

    구분자: 콤마(',') 또는 공백-슬래시-공백(' / '). 둘 다 인정 — 사용자가 표 형식으로
    `… / 19,800 / 탐앤탐스 / 선행개발팀` 처럼 슬래시로 적어 보내는 케이스를 흡수.
    날짜 내부의 '2/11' 처럼 공백 없는 '/' 는 \s+/\s+ 가 매치 안 해 보존됨.

    필드 수가 5 초과면 — 두 가지 케이스를 함께 다룸:
      (a) purpose 안에 콤마/슬래시가 섞여 중간이 잘린 경우
      (b) vendor 뒤에 부서/팀명 같은 잉여 트레일링 컬럼이 1개 이상 붙은 경우
    amount 위치를 *뒤에서부터 숫자처럼 보이는 첫 토큰* 으로 검출. amount 의 바로 다음
    1개를 vendor 로, 그 뒤 잉여 토큰은 폐기. date 다음 ~ amount 전 토큰들은 purpose 로
    ' / ' join — 원문 어휘 보존이 목적이라 구분자 통일.

    필드 수 < 5 이면 파싱 실패 → None.

    숫자의 천 단위 콤마(`29,500`)는 split 전에 제거해 콤마 split 으로부터 보호.
    """
    raw = _merge_thousand_separators(raw)
    # 콤마 또는 공백-슬래시-공백 — 둘 다 필드 구분자로 인정.
    parts = [p.strip() for p in re.split(r"\s*,\s*|\s+/\s+", raw) if p.strip()]
    if len(parts) < 5:
        return None

    if len(parts) > 5:
        # amount 위치를 뒤에서부터 숫자처럼 보이는 첫 토큰으로 검출.
        # category(0), date(1) 는 고정이므로 i > 1 까지만 스캔.
        amount_idx: int | None = None
        for i in range(len(parts) - 1, 1, -1):
            if _normalize_amount(parts[i]) is not None:
                amount_idx = i
                break
        # amount 다음 자리가 vendor — amount 가 맨 끝이면 vendor 가 없어 실패.
        if amount_idx is None or amount_idx + 1 >= len(parts):
            return None
        category_raw = parts[0]
        date_raw = parts[1]
        amount_raw = parts[amount_idx]
        vendor_raw = parts[amount_idx + 1]
        # amount 뒤의 잉여 컬럼(부서/팀명 등)은 폐기.
        purpose_raw = " / ".join(parts[2:amount_idx]).strip()
    else:
        category_raw, date_raw, purpose_raw, amount_raw, vendor_raw = parts

    category = _normalize_category(category_raw)
    if category is None:
        return None

    date = _parse_date(date_raw, default_year)
    if date is None:
        return None

    amount = _normalize_amount(amount_raw)
    if amount is None:
        return None

    purpose = purpose_raw.strip()
    if not purpose:
        return None

    vendor = vendor_raw.strip()
    if not vendor:
        return None

    return {
        "source": source,
        "category": category,
        "date": date,
        "purpose": purpose,
        "amount": amount,
        "vendor": vendor,
        "user": "",
    }


def parse_expense_message(
    text: str,
    *,
    default_year: int,
    default_month: int,
    default_author: str = "",
    attachment_ids: list[int] | None = None,
) -> dict[str, Any] | None:
    """사용자 메시지를 compose_expense_report 인자로 직접 변환.

    파싱 성공 조건:
      - 최소 한 줄 이상의 valid line (5필드 구조)
      - author 가 default_author 또는 메시지에서 추출 가능
      - year/month 가 default 또는 메시지에서 추출 가능

    실패 시 None 반환 — 호출자는 LLM 흐름으로 fallback.
    """
    if not text or not text.strip():
        return None

    # 1) 메타 추출 (메시지 어디에서든).
    ym_m = _YM_RE.search(text)
    if ym_m:
        year = int(ym_m.group(1))
        month = int(ym_m.group(2))
    else:
        year = default_year
        month = default_month

    if not (1 <= month <= 12):
        return None

    author_m = _AUTHOR_RE.search(text)
    if author_m:
        # group(1) = trigger 앞 이름, group(2) = "이름/작성자" 키워드 뒤 이름.
        author = (author_m.group(1) or author_m.group(2) or "").strip()
    else:
        author = ""
    if not author:
        author = (default_author or "").strip()
    if not author:
        return None  # 작성자 미상 — fallback

    # 2) 섹션별 라인 파싱.
    lines: list[dict] = []
    current_source: str | None = None
    # 데이터 라인 모양(5+ 토큰)인데 날짜/금액 등이 깨져 파싱 실패한 줄 수.
    # >0 이면 호출자가 사용자에게 '일부 라인 해석 실패'를 알려 무음 누락을 방지한다.
    dropped = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            # 빈 줄로 섹션을 닫지 않는다 — 사용자가 헤더와 본문 사이, 또는 본문 중간에
            # 가독성용으로 빈 줄을 넣는 패턴이 흔하다(expense_error_v11 회귀).
            # 섹션 전환은 *다른* 섹션 헤더가 명시될 때만 발생.
            continue

        # 섹션 헤더 매치 시도.
        # tail 이 비어있으면(헤더 단독 라인) → 다음 라인부터 body 로 받음 = source 설정.
        # tail 이 'X'/'없음' 등 마커면 → 그 섹션 0건.
        # tail 이 콤마/슬래시 라인 같으면 → source 설정 + 그 라인도 파싱 시도(드문 케이스).
        m_corp = _CORP_SECTION_RE.match(line)
        if m_corp:
            tail = line[m_corp.end():].strip()
            if tail.lower() in (m.lower() for m in _EMPTY_MARKERS):
                current_source = None
            else:
                current_source = "법인카드"
                if tail:
                    parsed = _parse_line(tail, "법인카드", year)
                    if parsed:
                        lines.append(parsed)
                    elif _looks_like_data_line(tail):
                        dropped += 1
            continue

        m_personal = _PERSONAL_SECTION_RE.match(line)
        if m_personal:
            tail = line[m_personal.end():].strip()
            if tail.lower() in (m.lower() for m in _EMPTY_MARKERS):
                current_source = None
            else:
                current_source = "개인카드"
                if tail:
                    parsed = _parse_line(tail, "개인카드", year)
                    if parsed:
                        lines.append(parsed)
                    elif _looks_like_data_line(tail):
                        dropped += 1
            continue

        # 본문 라인 — 현재 섹션이 열려 있을 때만 파싱. 실패해도 source 유지.
        # 잡담/메타 라인은 _parse_line 에서 None 반환되어 자연 무시됨 — 섹션을
        # 강제로 닫지 않아 같은 섹션의 후속 라인이 유실되지 않는다.
        if current_source is not None:
            parsed = _parse_line(line, current_source, year)
            if parsed:
                lines.append(parsed)
            elif _looks_like_data_line(line):
                dropped += 1

    if not lines:
        return None

    # 법인카드 라인은 user 를 author 로 채움.
    for ln in lines:
        if ln["source"] == "법인카드" and not ln["user"]:
            ln["user"] = author

    args: dict[str, Any] = {
        "author": author,
        "year": year,
        "month": month,
        "lines": lines,
    }
    if attachment_ids:
        args["receipt_attachment_ids"] = list(attachment_ids)
    else:
        args["receipt_attachment_ids"] = []

    # 해석 실패한 데이터 라인이 있으면 선택 키로 전달(없으면 키 자체를 넣지 않아 기존 호출부 영향 0).
    # 호출자(_stream_fast_expense)가 dispatch 전에 pop 하여 사용자 경고로 노출한다.
    if dropped:
        args["_dropped_lines"] = dropped

    _log.info(
        "[expense_parser] OK — author=%s y=%d m=%d lines=%d receipts=%d dropped=%d",
        author, year, month, len(lines), len(args["receipt_attachment_ids"]), dropped,
    )
    return args


# ── 시스템 프롬프트에서 컨텍스트 추출 ────────────────────────────────────
# 프론트가 inject 한 '## 현재 요청 컨텍스트' 블록에서 메타 회수.
_SYS_DATE_RE = re.compile(r"현재\s*날짜\s*:\s*(\d{4})-(\d{1,2})-(\d{1,2})")
_SYS_USER_RE = re.compile(r"현재\s*사용자[^:\n]*:\s*([가-힣A-Za-z][가-힣A-Za-z\s]{0,30}?)\s*\.")
_SYS_ATTACHMENT_IDS_RE = re.compile(r"전달\s*:\s*\[\s*([\d,\s]+?)\s*\]")


def extract_today_from_system(prompt: str) -> tuple[int, int, int] | None:
    """'현재 날짜: 2026-05-15' 패턴에서 (y, m, d) 회수."""
    if not prompt:
        return None
    m = _SYS_DATE_RE.search(prompt)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def extract_user_name_from_system(prompt: str) -> str | None:
    """'현재 사용자(작성자 후보): 배재병.' 패턴에서 이름 회수."""
    if not prompt:
        return None
    m = _SYS_USER_RE.search(prompt)
    if m:
        return m.group(1).strip()
    return None


def extract_attachment_ids_from_system(prompt: str) -> list[int]:
    """'전달: [17, 18, 19].' 패턴에서 ID 목록 회수."""
    if not prompt:
        return []
    m = _SYS_ATTACHMENT_IDS_RE.search(prompt)
    if not m:
        return []
    ids: list[int] = []
    for tok in m.group(1).split(","):
        t = tok.strip()
        if t.isdigit():
            ids.append(int(t))
    return ids


__all__ = [
    "parse_expense_message",
    "extract_today_from_system",
    "extract_user_name_from_system",
    "extract_attachment_ids_from_system",
]
