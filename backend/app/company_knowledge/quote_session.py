"""견적서 작성 — AI 와 대화하며 견적 초안을 채우고 고친다(사용자 2026-10-06, 영업부 팀장 요청).

흐름: 제품 추천 [최종 제안 확정] → [견적서 작성] → 추천 결과(선정 모델·구성품·단가표)로 초안 → AI 가 빠진 것을 하나씩 묻는다
(고객사 → 납품 방식 5가지 → 물류비 / 설치 인원·일수·1인 1일 단가 → 다른 추가 항목 → 담당자) → 대화·직접 수정으로 품목 추가·수정·삭제 →
[견적서 발행] = 엑셀(quote_xlsx). 다시 발행하면 같은 견적번호에 수정 차수(revision)를 올리고 이전 판은 이력에 남긴다.

- 질문 순서와 문구는 코드가 정한다(AI 가 질문을 빼먹거나 지어내지 않게). AI 는 사용자의 말을 '변경(ops)'으로 옮기기만 한다.
- 숫자는 사용자가 적은 것만 받는다('50만원' 같은 만·억 단위는 풀어서 대조). 회사 기준이 없는 값(작업 1인 1일 단가 등)은 묻는다.
- 금액은 원화로 다룬다. 해외(영문) 견적은 발행할 때 입력 환율로 달러로 바꾼다(quote_xlsx).
- 최종 견적에 미정·0원을 넣지 않는다(v0.8 C13) — 발행 전 problems 가 비어야 한다.
- 액세서리 수량이 미정(None)이면 줄을 빼지 않고 남겨 발행을 막는다. 조건부 수량(v1.2 conditional_draft)은 담당자가 [수량 확인]하거나
  수량을 고치기 전에는 발행하지 않는다(PGR24 — 조건부 결과를 최종 견적으로 자동 확정하지 않음).
"""
from __future__ import annotations

import copy
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from . import product_prices as pp
from . import quote_xlsx as qx

FORM_FIELDS = {   # 대화·직접 수정으로 바꿀 수 있는 머리 칸 → (라벨, 종류)
    "customer": ("고객사", "text"), "to": ("받는 분(To)", "text"), "cc": ("참조(CC)", "text"), "subject": ("파일명 내용", "text"),
    "initials": ("담당자 이니셜", "text"), "contact_name": ("담당자 이름", "text"), "contact_title": ("직함", "text"),
    "contact_mobile": ("휴대폰", "text"), "contact_email": ("이메일", "text"), "delivery": ("납기", "text"),
    "place": ("납품장소", "text"), "payment": ("결제조건", "text"), "comments": ("Comments", "text"), "note": ("비고", "text"),
    "lang": ("언어", "lang"), "fx_rate": ("환율(원/달러)", "number"),
    # 해외 견적서 담당자 표기 'Mr. Alex Jeon'(사용자 2026-10-14, 해외 기준 견적서) — 기본은 내 정보의 영문 이름·호칭
    "contact_name_en": ("담당자 영문 이름", "text"), "contact_honorific": ("호칭", "text"),
    # 고객사가 우리에게 입금할 계좌(사용자 2026-10-08) — 기본은 회사 계좌(product_prices.QUOTE_ISSUER), 견적마다 바꿀 수 있다
    "bank": ("입금 은행", "text"), "account_no": ("입금 계좌번호", "text"), "account_holder": ("예금주", "text"),
    # 견적서 Total 아래 보조 문구·표 아래 주석(사용자 2026-10-13, 영업팀 요청 14번) — 비울 수 있다
    "total_note": ("Total 아래 문구", "text"), "foot_notes": ("표 아래 주석", "list"),
    # 판매 / 대여(사용자 2026-10-22, 방역 로봇 3년 렌탈 견적서 기준) — 대여면 계약 개월 수로 Total 옆에 월 납입금
    "purpose": ("목적", "purpose"), "rental_months": ("대여 개월 수", "number"), "rental_start": ("대여 시작일", "date"),
}
PURPOSES = {"sale": "제품 판매", "rental": "제품 대여"}
CLEARABLE_FIELDS = ("total_note", "foot_notes")
# 견적서 칸 안에서 줄을 바꿀 수 있는 머리 칸(Alt+Enter, 사용자 2026-10-16). 고객사·받는 분·참조·담당자는 영업 건·파일명에도 쓰여 한 줄 그대로
MULTILINE_FIELDS = ("delivery", "place", "payment", "note", "total_note")
BANK_FIELDS = ("bank", "account_no", "account_holder")


def bank_defaults(lang: str) -> dict[str, str]:
    """언어별 회사 기본 입금 계좌(국내 = 국민은행 원화 계좌, 해외 = 영문 송금 정보)."""
    iss = pp.quote_form(lang)["issuer"]
    return {k: str(iss.get(k) or "") for k in BANK_FIELDS}
WORK_NAME = {"ko": "설치 인건비", "en": "Installation labor"}
# UND 설치 인건비 회사 기준(사용자 2026-10-07): 1인 8시간(1일) 80만원 — 설치 납품이면 단가를 묻지 않고 이 값으로(대화·화면에서 바꿀 수 있음)
WORK_RATE = 800_000
FREIGHT_NAME = {"ko": "물류비", "en": "Freight"}
# 납품 방식(사용자 2026-10-06, 영업팀 기준) — 1 택배: 품목 그대로 / 2 화물 직납: 물류비 추가 / 3 우리 인원이 설치하며 납품: 인건비 추가 /
# 4 화물로 보내고 우리 인원이 설치: 물류비 + 인건비 / 5 별도 협의: 품목 그대로(사용자 2026-10-08). 고르면 품목 줄이 맞춰진다. 납품장소는 방식과 관계없이
# '직납 및 배차 비용 별도 (화물 비용 별도)'(사용자 2026-10-06 — 모든 경우, quote_xlsx.DEFAULT_TERMS).
DELIVERY = {
    1: {"label": "택배 발송", "freight": False, "work": False},
    2: {"label": "화물 직납", "freight": True, "work": False},
    3: {"label": "UND 인원 설치 납품", "freight": False, "work": True},
    4: {"label": "화물 발송 + UND 인원 설치", "freight": True, "work": True},
    5: {"label": "별도 협의", "freight": False, "work": False},
}
DELIVERY_QUESTION = ("제품은 어떻게 납품하나요? 1) 택배로 보냄  2) 화물로 직납  3) 저희 인원이 설치하면서 납품  "
                     "4) 화물로 보내고 저희 인원이 설치하러 감  5) 별도 협의")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_MONEY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(억|천만|백만|만|천)")
_UNIT = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "만": 10_000, "천": 1_000}
# '42만 5천'·'1억 2천만'·'3만5천원' — 단위 붙은 숫자가 이어지면 합친 값도 말한 숫자(사용자 2026-10-13)
_MONEY_SEQ_RE = re.compile(r"(?:\d+(?:\.\d+)?\s*(?:억|천만|백만|만|천)\s*){2,}(?:\d+(?!\s*[.\d]))?")
_NO_RE = re.compile(r"^\s*(없음|없어|없습니다|없어요|아니|아뇨|필요\s*없|안\s*해|no\b)", re.I)


class QuoteSessionError(ValueError):
    """사람이 읽는 이유."""


def said_numbers(text: str) -> set[float]:
    """말에 적힌 숫자 — '1,380'·'2.5'·'50만원'(→500000)·'1.5억' 모두."""
    out = {float(n.replace(",", "")) for n in _NUM_RE.findall(text or "") if n.replace(",", "").replace(".", "", 1).isdigit()}
    for n, u in _MONEY_RE.findall(text or ""):
        out.add(float(n) * _UNIT[u])
    for m in _MONEY_SEQ_RE.finditer(text or ""):
        seq = m.group(0)
        tail = re.search(r"(?:억|천만|백만|만|천)\s*(\d+)\s*$", seq)
        out.add(sum(float(n) * _UNIT[u] for n, u in _MONEY_RE.findall(seq)) + (float(tail.group(1)) if tail else 0))
    return out


# 달러로 말한 금액('1000달러'·'$1,000'·'USD 1000'·'1000불') — 해외 견적 달러 단가(사용자 2026-10-13)
_USD_RE = re.compile(r"\$|달러|USD|US\$|(?<=\d)\s*불", re.I)
_KRW_RE = re.compile(r"\d\s*(만|천만|백만|억)?\s*원|￦|₩|KRW", re.I)


def says_usd(text: str) -> bool:
    """말에 달러 표시가 있고 원화 표시는 없는가 — 금액을 달러 단가(usd)로 받는다."""
    return bool(_USD_RE.search(text or "")) and not _KRW_RE.search(text or "")


def _merge_add_price(state: dict[str, Any], ops: Any) -> Any:
    """AI 가 새 항목을 '단가 없는 add' + '아직 없는 줄 update(단가)'로 나눠 낼 때가 있다 — 한 add 로 합친다."""
    if not isinstance(ops, list):
        return ops
    have = {ln["id"] for ln in state["lines"]}
    out: list[Any] = []
    for op in ops:
        prev = next((x for x in reversed(out) if isinstance(x, dict) and x.get("op") == "add"
                     and x.get("unit_price") in (None, 0, "") and x.get("usd") in (None, 0, "")), None)
        if (isinstance(op, dict) and op.get("op") == "update" and op.get("line") not in have and prev is not None
                and (op.get("unit_price") is not None or op.get("usd") is not None)):
            prev.update({k: op[k] for k in ("unit_price", "usd", "qty") if op.get(k) is not None})
            continue
        out.append(dict(op) if isinstance(op, dict) else op)
    return out


def _said(v: Any, nums: set[float]) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if any(abs(f - n) < 1e-6 for n in nums) else None


# ── 묶음(블록)·NEGO(사용자 2026-10-13, 영업팀 요청 14·9번) ─────────────────────────────
# 견적 본문 = 묶음 여러 개(순서대로). B0 = 맨 위 기본 품목 묶음(기존 견적의 품목 전부). 줄의 block 이 묶음 id.
#   items  — 소제목(선택)·품목·소계(선택), 합계 포함
#   option — '< OPTION >' 아래 단가만, 공급가액 빈칸, 합계·영업 건 금액·거래명세서 제외(LG CNS 견적서 방식)
#   spec   — 항목명 / 내용(가격 없음, 방역 로봇 견적서의 사양 칸)
#   text   — 표 폭 전체 서술형 한 칸
# NEGO = 할인(금액 또는 %, %는 옵션을 뺀 합계 기준) — Total 바로 위 한 줄, 견적서엔 금액.
BLOCK_TYPES = ("items", "option", "spec", "text")
MAIN_BLOCK = "B0"
OPTION_TITLE = "< OPTION >"
BLOCKS_MAX = 12
SPEC_ROWS_MAX = 20


def blocks(state: dict[str, Any]) -> list[dict[str, Any]]:
    """묶음 목록 — 기본 묶음(B0)이 늘 맨 앞. 예전 견적(묶음 없음)은 B0 하나."""
    bl = [b for b in (state.get("blocks") or []) if isinstance(b, dict) and b.get("type") in BLOCK_TYPES and b.get("id")]
    main = next((b for b in bl if b["id"] == MAIN_BLOCK), None) or {"id": MAIN_BLOCK, "type": "items", "title": "", "subtotal": False}
    return [main, *[b for b in bl if b["id"] != MAIN_BLOCK]]


def block_of(ln: dict[str, Any], ids: set[str] | dict[str, Any], auto: str = MAIN_BLOCK) -> str:
    """줄이 속한 묶음 — 물류비·설치 인건비 줄은 납품 방식 칸에서 고른 묶음(auto, 기본 = 기본 묶음), 없는 묶음을 가리키는 줄은 기본 묶음."""
    if ln.get("kind") in ("work", "freight"):
        return auto if auto in ids else MAIN_BLOCK
    b = ln.get("block")
    return b if b in ids else MAIN_BLOCK


def auto_block(state: dict[str, Any]) -> str:
    """물류비·설치 인건비 줄을 넣을 묶음(사용자 2026-10-13) — 사용자 지정 묶음(items)만, 아니면 기본 묶음."""
    b = (state.get("delivery") or {}).get("block")
    return b if any(x["id"] == b and x["type"] == "items" for x in blocks(state)) else MAIN_BLOCK


def _types(state: dict[str, Any]) -> dict[str, str]:
    return {b["id"]: b["type"] for b in blocks(state)}


def billable(state: dict[str, Any]) -> list[dict[str, Any]]:
    """합계에 들어가는 줄 — 옵션 묶음 줄은 [반영](in_total)한 것만(사용자 2026-10-19)."""
    ty = _types(state)
    auto = auto_block(state)
    return [ln for ln in state["lines"] if ln.get("kind") != "note"
            and (ty.get(block_of(ln, ty, auto)) != "option" or ln.get("in_total"))]


def nego_amount(state: dict[str, Any], base: float) -> float:
    """NEGO 할인 금액(양수) — 금액이면 그대로, %면 base(미반영 옵션 뺀 합계) × %. 견적 통화 기준(국내 원·해외 달러)."""
    n = state.get("nego") or {}
    v = n.get("value")
    if not isinstance(v, (int, float)) or v <= 0:
        return 0.0
    amt = base * v / 100 if n.get("mode") == "percent" else float(v)
    # 0.5 는 올림 — 화면(Math.round)·엑셀 ROUND 와 같게(검증 QT-016, 2026-10-15: 파이썬 round 로 1원 달랐다)
    step = Decimal("0.01") if state["form"].get("lang") == "en" else Decimal("1")
    return float(Decimal(str(amt)).quantize(step, rounding=ROUND_HALF_UP))


def nego_label(state: dict[str, Any]) -> str:
    n = state.get("nego") or {}
    lab = re.sub(r"\s+", " ", str(n.get("label") or "")).strip()[:40]
    pct = f" ({n['value']:g}%)" if n.get("mode") == "percent" and isinstance(n.get("value"), (int, float)) else ""
    return f"NEGO ({lab})" if lab else f"NEGO{pct}"


def _clean_block(b: dict[str, Any]) -> dict[str, Any] | None:
    t = b.get("type")
    if t not in BLOCK_TYPES:
        return None
    out = {"id": str(b.get("id") or ""), "type": t, "title": clean_lines(b.get("title"), 60)}
    if t == "items":
        out["subtotal"] = bool(b.get("subtotal"))
    if t == "spec":
        rows = []
        for r in (b.get("rows") or [])[:SPEC_ROWS_MAX]:
            if isinstance(r, dict):
                lab = clean_lines(r.get("label"), 40)
                val = clean_spec(r.get("value"))
                if lab or val:
                    rows.append({"label": lab, "value": val})
        out["rows"] = rows
    if t == "text":
        out["text"] = clean_spec(b.get("text"))
    return out


# ── 초안 ──────────────────────────────────────────────────────────────────────

MANUAL_COMMENTS = "www.magbot.kr\n- 제품 공급 건"
MANUAL_COMMENTS_EN = "www.magbot.kr\nProduct Supply"           # 해외 기준 견적서처럼 줄머리 기호 없이
RENTAL_COMMENTS = "www.magbot.kr\n- 제품 대여 건"
RENTAL_COMMENTS_EN = "www.magbot.kr\nProduct Rental"
# 대여 견적서 기본 주석 — 방역 로봇 3년 렌탈 견적서(Lu260309)의 중도 해지 문구
RENTAL_NOTE = ("* 중도 해지 시, 해지일의 익월부터 잔여 계약기간까지 발생할 예정이었던 로봇 렌탈 비용은\n"
               "잔여 개월 수 × 월 납입료를 기준으로 위약금을 산정하며, 계약자는 해당 위약금을 일시불로 납부하여야 합니다.")
RENTAL_NOTE_EN = ("* In case of early termination, a penalty of (remaining months × monthly fee) from the month after termination\n"
                  "until the end of the contract shall be paid in a lump sum.")


def is_rental(state: dict[str, Any]) -> bool:
    return (state.get("form") or {}).get("purpose") == "rental"


# 단위가 기간인 줄('36 개월' — 예전 방식, 수량이 곧 개월 수)은 개월을 다시 곱하지 않는다
TIME_UNITS = {"개월", "월", "년", "일", "주", "month", "months", "year", "years", "day", "days", "week", "weeks"}
# 단위 '식'(일괄 금액 — 유지보수 1식·설치 1식)과 물류비 이름 줄도 대여 견적서에서 한 번만 낸다(사용자 2026-10-22)
ONCE_UNITS = {"식", "lot", "lots"}
# '비·료'까지 붙어야 물류비로 본다 — '물류 로봇'·'운반 로봇' 같은 제품 이름은 아니다
FREIGHT_RE = re.compile(r"(물류|운송|화물|배송|운반|택배)\s*(비|료)|freight|shipping", re.I)


def month_factor(form: dict[str, Any], ln: dict[str, Any]) -> int:
    """대여 견적서 줄의 곱할 개월 수(사용자 2026-10-22) — 단가 = 1대당 월 납입금이라 금액 = 수량 × 단가 × 대여 개월.
    한 번만 내는 줄(품목 Service · 단위 '식' · 물류비·배송비·설치 인건비 · 서술형)과 단위가 이미 기간인 줄은 1. 판매 견적서는 늘 1."""
    if form.get("purpose") != "rental":
        return 1
    try:
        m = int(form.get("rental_months") or 0)
    except (TypeError, ValueError):
        return 1
    if m <= 0 or ln.get("kind") in ("work", "freight", "note") or ln.get("row") in ("nego", "spec", "text", "gap", "section", "subtotal"):
        return 1
    if str(ln.get("commodity") or "").strip().lower() == "service":
        return 1
    if str(ln.get("unit") or "").strip().lower() in TIME_UNITS | ONCE_UNITS:
        return 1
    if FREIGHT_RE.search(f"{ln.get('commodity') or ''} {ln.get('name') or ''}"):
        return 1
    return m


def rental_monthly(total: float, months: Any) -> int | None:
    """월 납입금 = 총액 ÷ 개월 수, 천 원 단위 올림(방역 로봇 견적서: 35,800,000 ÷ 36 → 995,000)."""
    try:
        m = int(months or 0)
    except (TypeError, ValueError):
        return None
    if m <= 0 or not total:
        return None
    return -(-round(total) // (m * 1000)) * 1000


def clean_date(v: Any) -> str:
    """'2026-11-01' · '2026.11.1' · '2026년 11월 1일' → ISO 날짜. 못 읽으면 ''."""
    m = re.fullmatch(r"\s*(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?\s*", str(v or ""))
    if not m:
        return ""
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return ""


def term_defaults(state: dict[str, Any], lang: str) -> dict[str, str]:
    """언어별 견적 조건 기본 문구(Comments·납기·납품장소·결제조건) — 수기 견적의 Comments 는 제품 공급 문구."""
    d = {k: qx.DEFAULT_TERMS[lang][k] for k in ("comments", "delivery", "place", "payment")}
    if is_rental(state):
        d["comments"] = RENTAL_COMMENTS_EN if lang == "en" else RENTAL_COMMENTS
    elif state.get("manual"):
        d["comments"] = MANUAL_COMMENTS_EN if lang == "en" else MANUAL_COMMENTS
    return d


def draft_state(accessories: dict[str, Any], *, model: str, payload_kg: float | None, form: dict[str, Any] | None = None,
                manual: bool = False) -> dict[str, Any]:
    """추천 구성품(지금 규칙·단가표로 다시 만든 것) → 견적 초안. 체크하지 않은 옵션(IB)은 options 에 두고 대화로 넣을 수 있다."""
    lines, options = [], []
    for it in accessories.get("items") or []:
        if it.get("qty") == 0 and not it.get("optional"):     # 필요 없음이 확인된 0 만 뺀다 — 미정(None)은 남겨 발행을 막는다
            continue
        k = qx._key(it)
        row = {"kind": "option" if it.get("optional") else "product", "key": k, "name": it["name"],
               "commodity": qx.default_commodity({"key": k}),
               "qty": it.get("qty"), "unit_price": it.get("unit_price"), "remark": qx._REMARK["ko"].get(k, ""),
               "price_status": it.get("price_status")}
        if it.get("conditional"):
            row.update(conditional=True, confirmed=False, basis=it.get("basis") or "")
        if it.get("optional") and not it.get("included"):
            options.append(row)
        else:
            lines.append(row)
    for i, ln in enumerate(lines, 1):
        ln["id"] = f"L{i}"
    d = qx.DEFAULT_TERMS["ko"]
    f = {k: "" for k in FORM_FIELDS} | {"lang": "ko", "fx_rate": None, "comments": d["comments"], "delivery": d["delivery"],
                                         "place": d["place"], "payment": d["payment"], "purpose": "sale",
                                         "rental_months": None} | bank_defaults("ko")
    if manual:                    # 수기 견적(사용자 2026-10-08) — 툴체인저 문구 대신 일반 공급 문구, 건명은 담당자가
        f.update(comments=MANUAL_COMMENTS, subject="")
    for k, v in (form or {}).items():
        if k in FORM_FIELDS and v not in (None, ""):
            f[k] = v
    if f.get("purpose") not in PURPOSES:
        f["purpose"] = "sale"
    if f["purpose"] == "rental":            # 대여 견적서(사용자 2026-10-22) — 대여 문구·중도 해지 주석
        f.update(comments=RENTAL_COMMENTS, foot_notes=[RENTAL_NOTE])
    return {"model": model, "payload_kg": payload_kg, "applicable": bool(accessories.get("applicable")) or manual, "form": f,
            "lines": lines, "options": options, "work": {"needed": None, "people": None, "days": None, "rate": None},
            "delivery": {"case": None, "freight": None}, "asked": [], "next_id": len(lines) + 1, "manual": manual}


SPEC_MAX = 300          # 규격 칸 글자 수(여러 줄 — 예: 'TCV1\n- Master T.C\n- Control Box…', 사용자 2026-10-12)
SPEC_LINES = 8


def clean_spec(v: Any) -> str:
    """규격(품목명) — 줄바꿈은 살리고(줄마다 공백 정리·빈 줄 제거) 8줄·300자까지."""
    rows = [re.sub(r"[ \t]+", " ", x).strip() for x in str(v or "").splitlines()]
    return "\n".join([x for x in rows if x][:SPEC_LINES])[:SPEC_MAX]


FOOT_NOTES_MAX = 8


def clean_notes(v: Any) -> list[str]:
    """표 아래 주석 — 칸 하나에 주석 하나(사용자 2026-10-13, 방역 로봇 견적서처럼 칸마다 따로). 칸 안은 3줄·200자까지, 8칸까지.
    예전 값(줄바꿈으로 이은 글)은 줄마다 한 칸으로 읽는다."""
    raw = v if isinstance(v, list) else str(v or "").splitlines()
    out = []
    for x in raw[:FOOT_NOTES_MAX * 2]:
        rows = [re.sub(r"[ \t]+", " ", r).strip() for r in str(x or "").splitlines()]
        t = "\n".join([r for r in rows if r][:3])[:200]
        if t:
            out.append(t)
    return out[:FOOT_NOTES_MAX]


def clean_unit(v: Any) -> str:
    """수량 단위(MD·식·개월·대·set …, 사용자 2026-10-13 — 방역 로봇 견적서). 엑셀 숫자 서식에 들어가 따옴표·역슬래시는 뺀다. 8자까지."""
    return re.sub(r'["\\\s]+', "", str(v or ""))[:8]


# 해외 견적서의 한글 단위 → 영문(사용자 2026-10-14). 담당자가 적은 단위는 그대로 두고 견적서에 찍을 때만 바꾼다.
UNIT_EN = {"식": "lot", "개월": "month", "대": "unit", "개": "ea", "인·일": "man-day", "인일": "man-day", "인/일": "man-day",
           "일": "day", "명": "person", "세트": "set", "식(式)": "lot"}
# 영문 단위 복수형 — 수량이 1이 아니면(해외 기준 견적서 '2 sets'). ea·pcs 는 그대로
_UNIT_PLURAL = {"lot": "lots", "unit": "units", "set": "sets", "man-day": "man-days", "month": "months", "day": "days",
                "person": "persons"}
_UNIT_SINGULAR = {v: k for k, v in _UNIT_PLURAL.items()}


def unit_label(unit: str, lang: str, qty: float | None = None) -> str:
    """견적서에 찍을 수량 단위 — 해외 견적서면 한글 단위를 영문으로, 수량이 1이 아니면 복수형(1 lot / 2 lots)."""
    if lang != "en":
        return unit
    u = UNIT_EN.get(unit, unit)
    if qty is None or u.lower() not in {*_UNIT_PLURAL, *_UNIT_SINGULAR}:
        return u
    base = _UNIT_SINGULAR.get(u.lower(), u.lower())
    return base if qty == 1 else _UNIT_PLURAL[base]


def clean_honorific(v: Any) -> str:
    """호칭 — Mr. / Ms. 만(그 밖은 비움). 이름으로 짐작하지 않고 본인이 고른다(사용자 2026-10-14)."""
    s = re.sub(r"[\s.]", "", str(v or "")).lower()
    return {"mr": "Mr.", "ms": "Ms."}.get(s, "")


COMMENTS_LINES = 6                                         # 견적서 Comments 칸(E9:G14, 6행)에 들어가는 만큼


def clean_comments(v: Any) -> str:
    """Comments — 담당자가 띄운 줄을 그대로 살린다(사용자 2026-10-13, 견적서엔 가운데 정렬). 6줄·200자까지."""
    rows = [re.sub(r"[ \t]+", " ", x).strip() for x in str(v or "").splitlines()]
    return "\n".join([x for x in rows if x][:COMMENTS_LINES])[:200]


TEXT_LINES = 4


def clean_lines(v: Any, limit: int) -> str:
    """견적서 칸 하나의 글 — 담당자가 바꾼 줄(Alt+Enter, 사용자 2026-10-16)은 살리고 줄마다 공백 정리·빈 줄 제거, 4줄까지."""
    rows = [re.sub(r"\s+", " ", x).strip() for x in str(v or "").splitlines()]
    return "\n".join([x for x in rows if x][:TEXT_LINES])[:limit]


def _new_id(state: dict[str, Any]) -> str:
    state["next_id"] = int(state.get("next_id") or len(state["lines"]) + 1) + 1
    return f"L{state['next_id'] - 1}"


def _delivery(state: dict[str, Any]) -> dict[str, Any]:
    return state.setdefault("delivery", {"case": None, "freight": None})


def _sync_work(state: dict[str, Any]) -> None:
    """납품 방식 → 물류비 줄(화물)·'설치 인건비' 줄(인원 × 일수 = 인·일 수량, 1인 1일 단가). 해당 없으면 줄을 뺀다.
    인원·일수·단가 값은 방식을 바꿔도 지우지 않는다(다시 설치로 바꾸면 그대로)."""
    spec = DELIVERY.get(_delivery(state).get("case"))
    w = state["work"]
    if spec:
        w["needed"] = spec["work"]
    state["lines"] = [ln for ln in state["lines"] if ln["kind"] not in ("work", "freight")]
    if spec and spec["freight"]:
        fr = _delivery(state).get("freight")
        state["lines"].append({"id": "F1", "kind": "freight", "key": "freight", "name": FREIGHT_NAME["ko"], "qty": 1,
                               "unit_price": fr, "remark": spec["label"], "price_status": "set" if fr else "none"})
    if w.get("needed") and not w.get("rate"):
        w["rate"] = WORK_RATE
    if w.get("needed") and w.get("people") and w.get("days"):
        state["lines"].append({"id": "W1", "kind": "work", "key": "work", "name": WORK_NAME["ko"],
                               "qty": int(w["people"]) * int(w["days"]), "unit": "인·일", "unit_price": w.get("rate"),
                               "remark": f"{w['people']}명 × {w['days']}일", "price_status": "set" if w.get("rate") else "none"})


# ── 다음 질문 · 발행 가능 여부 ─────────────────────────────────────────────────────

def next_question(state: dict[str, Any]) -> dict[str, Any] | None:
    """AI 가 지금 물을 것 — 순서: 고객사 → 납품 방식(5가지) → 물류비 → 설치 인원·일수 → 1인 1일 단가 → 다른 추가 항목 → 담당자 → 환율.
    choices 가 있으면 화면이 버튼으로 보여 준다(누르면 그 글이 답으로 간다)."""
    f, w, asked = state["form"], state["work"], set(state.get("asked") or [])
    dv = _delivery(state)
    spec = DELIVERY.get(dv.get("case"))
    if not str(f.get("customer") or "").strip():
        return {"id": "customer", "text": "견적서를 받을 고객사와 받는 분(To)을 알려 주세요. 예: 가나테크, 김철수 대표님"}
    if state.get("manual") and not any(ln["kind"] in ("product", "extra", "option") for ln in state["lines"]):
        return {"id": "items", "text": "견적에 넣을 품목을 알려 주세요 — 품명·수량·단가. 예: MG10 그리퍼 2개 150만원. "
                                       "왼쪽 [+ 항목]이나 [단가표에서 추가]로 직접 넣어도 됩니다."}
    if not spec:
        return {"id": "delivery", "text": DELIVERY_QUESTION,
                "choices": [f"{k}. {v['label']}" for k, v in DELIVERY.items()]}
    if spec["freight"] and not dv.get("freight"):
        return {"id": "freight", "text": "화물로 보내는 물류비는 얼마로 할까요? 예: 15만원"}
    if spec["work"] and not (w.get("people") and w.get("days")):
        return {"id": "work_detail", "text": "설치하러 가는 인원과 일수를 알려 주세요. 예: 2명 3일"}
    if spec["work"] and not w.get("rate"):
        return {"id": "work_rate", "text": f"설치 인건비 — 1인 1일 단가는 얼마로 할까요? (회사 기준: 1인 8시간 {WORK_RATE:,}원)"}
    if "extra" not in asked:
        return {"id": "extra", "text": "출장비·교육비 등 다른 추가 항목이 있나요? 있으면 항목·금액을, 없으면 '없음'이라고 해 주세요.",
                "choices": ["없음"]}
    if not all(str(f.get(k) or "").strip() for k in ("initials", "contact_name", "contact_mobile", "contact_email")):
        return {"id": "contact", "text": "견적서에 넣을 담당자 정보를 알려 주세요 — 이니셜 영문 2자(견적번호용)·이름·직함·휴대폰·이메일. "
                                         "예: HG, 홍길동 이사, 010-0000-0000, name@example.com"}
    if f.get("lang") == "en" and not f.get("fx_rate") and _usd_missing(state):
        return {"id": "fx", "text": "해외(달러) 견적입니다 — 품목 단가를 달러로 직접 적어 주시거나, 원화 단가표 가격을 바꿀 환율(원/달러)을 "
                                    "알려 주세요. 예: 1380"}
    return None


def usd_price(ln: dict[str, Any], fx_rate: Any) -> float | None:
    """해외 견적 줄의 달러 단가 — 담당자가 적은 달러 단가(usd)가 먼저, 없으면 원화 단가 ÷ 환율(센트 반올림), 둘 다 없으면 None.
    환율은 선택(사용자 2026-10-13: 해외 견적서는 $ 로만 적는 경우가 많다). 물류비·설치 인건비 줄은 원화로 계산돼 환율로만 바꾼다."""
    usd = ln.get("usd") if ln.get("kind") not in ("work", "freight") else None
    if isinstance(usd, (int, float)) and usd > 0:
        return float(usd)
    try:
        fx = float(fx_rate) if fx_rate not in (None, "") else None
    except (TypeError, ValueError):
        fx = None
    krw = ln.get("unit_price")
    if fx and fx > 0 and isinstance(krw, (int, float)) and krw > 0:
        return round(krw / fx, 2)
    return None


def _usd_missing(state: dict[str, Any]) -> list[dict[str, Any]]:
    fx = state["form"].get("fx_rate")
    return [ln for ln in state["lines"] if ln.get("kind") != "note" and usd_price(ln, fx) is None]


def subtotal_usd(state: dict[str, Any], *, nego: bool = True) -> float | None:
    """해외 견적 달러 합계(VAT 별도, 옵션 묶음 제외, NEGO 차감) — 달러 단가가 없는 줄이 있으면 None."""
    if state["form"].get("lang") != "en" or _usd_missing(state):
        return None
    fx = state["form"].get("fx_rate")
    f = state["form"]
    base = round(sum((ln.get("qty") or 0) * usd_price(ln, fx) * month_factor(f, ln) for ln in billable(state)), 2)
    return round(base - (nego_amount(state, base) if nego else 0), 2)


def problems(state: dict[str, Any]) -> list[str]:
    """발행을 막는 이유(C13 — 미정·0원 금지, 필수 머리 값)."""
    out = []
    if not state.get("applicable") and not state.get("manual"):
        out.append("유선 자동 툴체인저(TCC1~TCV4) 구성이 아니라 제품 단가가 없습니다 — 별도 견적 필요")
    for ln in state["lines"]:
        if ln["kind"] in ("work", "freight", "note"):
            continue                                        # 물류비·인건비는 아래 납품 방식 기준으로, 서술형 줄은 가격 없음
        if not ln.get("qty") or ln["qty"] < 1:
            out.append(f"{ln['name']} — 수량 미정")
        elif ln.get("conditional") and not ln.get("confirmed"):
            out.append(f"{ln['name']} — 조건부 수량 {ln['qty']}(근거 확인 후 [수량 확인] 또는 수량 수정)")
        elif state["form"].get("lang") == "en":
            if usd_price(ln, state["form"].get("fx_rate")) is None:
                out.append(f"{ln['name']} — 달러 단가 없음(직접 적거나 환율 입력)")
        elif not ln.get("unit_price") or ln["unit_price"] <= 0:
            out.append(f"{ln['name']} — " + ("단가표 가격 미정" if ln.get("price_status") == "unset" else "단가 없음"))
    if not state["lines"]:
        out.append("품목이 없습니다")
    f = state["form"]
    en = f.get("lang") == "en"
    for k in ("customer", "contact_name", "contact_mobile", "contact_email"):
        if k == "contact_name" and en:               # 해외 견적서는 영문 이름(없으면 한글 이름 — 한글 칸으로 따로 막힌다)
            if not (str(f.get("contact_name_en") or "").strip() or str(f.get(k) or "").strip()):
                out.append(f"{FORM_FIELDS['contact_name_en'][0]} 비어 있음")
            continue
        if not str(f.get(k) or "").strip():
            out.append(f"{FORM_FIELDS[k][0]} 비어 있음")
    if is_rental(state) and not f.get("rental_months"):
        out.append("대여 개월 수 필요(월 납입금 계산)")
    # 내 정보·발행번호와 같이 영문 2자(사용자 결정 2026-10-16)
    if not re.fullmatch(r"[A-Z]{2}", str(f.get("initials") or "").upper()):
        out.append("담당자 이니셜(영문 2자, 예: VT) 필요")
    spec = DELIVERY.get(_delivery(state).get("case"))
    if not spec:
        out.append("납품 방식(택배·화물 직납·설치 납품·화물+설치·별도 협의) 선택 필요")
    else:
        if spec["freight"] and not _delivery(state).get("freight"):
            out.append("물류비 금액 필요")
        w = state["work"]
        if spec["work"] and not (w.get("people") and w.get("days") and w.get("rate")):
            out.append("설치 인건비 — 인원·일수·1인 1일 단가 필요")
    n = state.get("nego") or {}
    if n.get("mode") == "percent" and isinstance(n.get("value"), (int, float)) and n["value"] >= 100:
        out.append("NEGO 할인율은 100% 미만")
    elif nego_amount(state, 1) > 0 and n.get("mode") != "percent":
        base = (subtotal_usd(state, nego=False) if f.get("lang") == "en" else subtotal(state, nego=False)) or 0
        if base and n["value"] >= base:
            out.append("NEGO 할인 금액이 합계보다 큽니다")
    # 0원 이하 견적은 발행하지 않는다(검증 QT-005, 2026-10-15)
    tot = subtotal_usd(state) if f.get("lang") == "en" else subtotal(state)
    if state["lines"] and tot is not None and tot <= 0 and not any("NEGO" in p for p in out):
        out.append("합계가 0원 이하입니다 — 단가·NEGO 를 확인해 주세요")
    if f.get("lang") == "en":
        if not f.get("fx_rate") and any(ln["kind"] in ("work", "freight") for ln in state["lines"]):
            out.append("물류비·설치 인건비(원화)를 달러로 바꾸려면 환율 필요")
        if pp.quote_form("en")["missing"]:
            out.append("영문 회사 정보 미등록")
    return out


def subtotal(state: dict[str, Any], *, nego: bool = True) -> int:
    """원화 합계(VAT 별도) — 옵션 묶음 줄은 빼고, 국내 견적이면 NEGO 를 뺀다."""
    base = sum((ln.get("qty") or 0) * (ln.get("unit_price") or 0) * month_factor(state["form"], ln) for ln in billable(state))
    cut = nego_amount(state, base) if nego and state["form"].get("lang") != "en" else 0
    return int(base - cut)


# ── 변경 적용(대화·직접 수정 공통) ───────────────────────────────────────────────

def apply_ops(state: dict[str, Any], ops: Any, message: str | None) -> tuple[dict[str, Any], list[str]]:
    """변경 목록 → 새 상태와 사람이 읽는 반영 목록. message 가 있으면(대화) 숫자는 그 말에 적힌 것만 받는다."""
    st = copy.deepcopy(state)
    nums = said_numbers(message) if message is not None else None
    done: list[str] = []
    ops = _merge_add_price(state, ops)
    if message is not None and says_usd(message) and isinstance(ops, list):
        ops = [{**{k: v for k, v in op.items() if k != "unit_price"}, "usd": op["unit_price"]}
               if isinstance(op, dict) and op.get("op") in ("add", "update") and op.get("unit_price") is not None and op.get("usd") is None
               else op for op in ops]

    def num(v: Any) -> float | None:
        if nums is None:
            try:
                return float(v) if v is not None and v != "" else None
            except (TypeError, ValueError):
                return None
        return _said(v, nums)

    def set_case(case: int, why: str | None = None) -> None:
        dv = _delivery(st)
        if dv.get("case") == case:
            return
        dv["case"] = case
        done.append(f"납품 방식: {case}. {DELIVERY[case]['label']}" + (f"({why})" if why else ""))

    alias: dict[str, str] = {}
    made: list[str] = []          # 이 말로 새로 만든 소제목 묶음 — 묶음을 안 가리킨 항목은 여기에(AI 가 group 과 add 를 따로 낼 때)

    def new_block(t: str, title: str = "", subtotal_on: bool = False) -> str:
        bl = blocks(st)
        n = max([int(b["id"][1:]) for b in bl if re.fullmatch(r"B\d+", b["id"])] + [0]) + 1
        b = {"id": f"B{n}", "type": t, "title": title, **({"subtotal": subtotal_on} if t == "items" else {}),
             **({"rows": []} if t == "spec" else {}), **({"text": ""} if t == "text" else {})}
        st["blocks"] = [*bl, b]
        return b["id"]

    def target_block(op: dict[str, Any]) -> str | None:
        """줄을 넣을 묶음 — 화면은 block(id·임시 id), 대화는 option:true(옵션 묶음) 또는 group(소제목)."""
        bl = blocks(st)
        ids = {b["id"] for b in bl}
        b = alias.get(str(op.get("block") or ""), op.get("block"))
        if b in ids:
            return b
        if op.get("option") is True:
            return next((x["id"] for x in bl if x["type"] == "option"), None) or new_block("option", OPTION_TITLE)
        g = re.sub(r"\s+", " ", str(op.get("group") or "")).strip()[:60]
        if g:
            hit = next((x["id"] for x in bl if x["type"] == "items" and x.get("title") == g), None)
            if hit:
                return hit
            done.append(f"묶음 추가: {g}" + (" (소계 표시)" if op.get("subtotal") is not False else ""))
            made.append(new_block("items", g, op.get("subtotal") is not False))
            return made[-1]
        return None

    by_id = {ln["id"]: ln for ln in st["lines"]}
    for op in (ops if isinstance(ops, list) else [])[:20]:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        if kind == "set" and op.get("field") in FORM_FIELDS:
            field, typ = op["field"], FORM_FIELDS[op["field"]][1]
            # 대화에서 우리 쪽 담당자 칸은 담당자를 물을 때나 '담당'이라고 말할 때만 — 고객 쪽 받는 분을 담당자로 넣는 실수 방지
            if (message is not None and (field.startswith("contact_") or field == "initials")
                    and (next_question(state) or {}).get("id") != "contact" and "담당" not in message):
                continue
            v = op.get("value")
            if typ == "number":
                v = num(v)
                if v is None or v <= 0:
                    continue
                if field == "rental_months":
                    v = int(v)
            elif typ == "lang":
                if v not in ("ko", "en"):
                    continue
            elif typ == "purpose":
                if v not in PURPOSES:
                    continue
            elif typ == "date":
                v = clean_date(v)
                if not v:
                    continue
            elif typ == "list":
                v = clean_notes(v)
            else:
                v = (clean_comments(v) if field == "comments" else clean_lines(v, 200) if field in MULTILINE_FIELDS
                     else re.sub(r"\s+", " ", str(v or "")).strip()[:200])
                if field == "initials":
                    v = v.upper()[:2]
                if field == "contact_honorific":
                    v = clean_honorific(v)
                if not v and field not in CLEARABLE_FIELDS:
                    continue
                if (message is not None and field == "account_no"
                        and re.sub(r"\D", "", v) not in re.sub(r"\D", "", message)):
                    continue                                # 말에 없는 계좌번호는 넣지 않는다
            prev = st["form"].get(field)
            st["form"][field] = v
            if field == "purpose" and prev != v:
                # 판매 ↔ 대여 — 기본 문구였던 Comments·주석만 바꾼다(담당자가 고친 글은 그대로)
                lang = st["form"].get("lang") or "ko"
                was = {**st, "form": {**st["form"], "purpose": prev}}
                if (st["form"].get("comments") or "") == term_defaults(was, lang)["comments"]:
                    st["form"]["comments"] = term_defaults(st, lang)["comments"]
                note = RENTAL_NOTE_EN if lang == "en" else RENTAL_NOTE
                notes = [x for x in (st["form"].get("foot_notes") or []) if x not in (RENTAL_NOTE, RENTAL_NOTE_EN)]
                st["form"]["foot_notes"] = [*notes, note][:FOOT_NOTES_MAX] if v == "rental" else notes
            if field == "lang" and prev != v:
                # 국내↔해외 — 조건 문구·입금 계좌를 그 언어의 기본으로(사용자 2026-10-08). 수기 견적 Comments 는 제품 공급 문구
                st["form"].update({**term_defaults(st, v), **bank_defaults(v)})
                # 비고 기본(해외 'Ex-work UND Korea Factory', 해외 기준 견적서) — 담당자가 적은 비고는 그대로
                if (st["form"].get("note") or "") in ("", qx.DEFAULT_TERMS.get(prev or "ko", {}).get("note", "")):
                    st["form"]["note"] = qx.DEFAULT_TERMS[v]["note"]
                swap = RENTAL_NOTE_EN if v == "en" else RENTAL_NOTE     # 대여 중도 해지 주석도 그 언어로
                st["form"]["foot_notes"] = [swap if x in (RENTAL_NOTE, RENTAL_NOTE_EN) else x
                                            for x in (st["form"].get("foot_notes") or [])]
            done.append(f"{FORM_FIELDS[field][0]}: {v}")
        elif kind == "delivery":
            try:
                case = int(op.get("case"))
            except (TypeError, ValueError):
                case = None
            if case in DELIVERY:
                set_case(case)
            if "block" in op:
                b = alias.get(str(op.get("block") or ""), op.get("block")) or MAIN_BLOCK
                if b != (_delivery(st).get("block") or MAIN_BLOCK):
                    _delivery(st)["block"] = b
                    name = next((x.get("title") for x in blocks(st) if x["id"] == b), "") or "기본 품목"
                    done.append(f"물류비·인건비 넣을 묶음: {name}")
            fr = num(op.get("freight"))
            if fr is not None and fr > 0 and DELIVERY.get(_delivery(st).get("case"), {}).get("freight"):
                _delivery(st)["freight"] = fr
                done.append(f"물류비: {fr:,.0f}원")
            _sync_work(st)
            by_id = {ln["id"]: ln for ln in st["lines"]}
        elif kind == "work":
            w = st["work"]
            case = _delivery(st).get("case")
            if op.get("needed") is False and case in (3, 4):          # 설치 빼기 → 3→1, 4→2
                set_case(case - 2, "설치 제외")
            elif op.get("needed") is True and case in (None, 1, 2):     # 설치 넣기 → 1→3, 2→4
                set_case((case or 1) + 2, "설치 포함")
            for k, lab in (("people", "작업 인원"), ("days", "작업 일수"), ("rate", "1인 1일 단가")):
                v = num(op.get(k))
                if v is not None and v > 0:
                    w[k] = int(v) if k != "rate" else v
                    if _delivery(st).get("case") in (None, 1, 2):            # 인원·단가를 말하면 설치가 들어간 방식으로
                        set_case((_delivery(st).get("case") or 1) + 2, "설치 포함")
                    done.append(f"{lab}: {int(v):,}" + ("명" if k == "people" else "일" if k == "days" else "원"))
            _sync_work(st)
            by_id = {ln["id"]: ln for ln in st["lines"]}
        elif kind == "add" and op.get("kind") == "note":          # 서술형 줄(항목명 / 내용, 가격 없음 — 사용자 2026-10-13)
            lab = clean_lines(op.get("commodity"), 40)
            val = clean_spec(op.get("name"))
            if not (lab or val):
                continue
            blk = target_block(op)
            ln = {"id": _new_id(st), "kind": "note", "key": "note", "name": val, "commodity": lab, "qty": None, "unit_price": None,
                  "remark": "", **({"block": blk} if blk and blk != MAIN_BLOCK else {})}
            st["lines"].append(ln)
            by_id[ln["id"]] = ln
            done.append(f"서술형 줄 추가: {lab or val[:20]}")
        elif kind == "add":
            name = clean_spec(op.get("name"))
            price, qty = num(op.get("unit_price")), num(op.get("qty") or 1) if op.get("qty") not in (None, 1) else 1
            usd = num(op.get("usd"))
            usd = usd if usd is not None and usd > 0 else None
            if price is not None and price <= 0:
                price = None
            if not name or (price is None and usd is None) or not qty or qty < 1:
                continue
            blk = target_block(op)
            if blk is None and made and message is not None:
                blk = made[-1]
            main = blk in (None, MAIN_BLOCK)
            if main and price is None and re.search(r"물류|운송|화물|freight", name, re.I):
                continue                                    # 물류비는 원화(납품 방식 칸)로만
            if main and re.search(r"물류|운송|화물|freight", name, re.I):   # 물류비는 납품 방식과 함께 — 1→2, 3→4
                case = _delivery(st).get("case")
                if case in (None, 1, 3):
                    set_case((case or 1) + 1, "물류비 추가")
                _delivery(st)["freight"] = price * int(qty)
                done.append(f"물류비: {price * int(qty):,.0f}원")
                _sync_work(st)
                by_id = {x["id"]: x for x in st["lines"]}
                continue
            ln = {"id": _new_id(st), "kind": "extra", "key": "extra", "name": name, "qty": int(qty), "unit_price": price,
                  **({"usd": usd} if usd else {}), **({"block": blk} if blk and blk != MAIN_BLOCK else {}),
                  **({"unit": clean_unit(op.get("unit"))} if clean_unit(op.get("unit")) else {}),
                  "commodity": clean_lines(op.get("commodity"), 60), "remark": clean_lines(op.get("remark"), 100),
                  **({"in_total": True} if op.get("in_total") is True else {}), "price_status": "set"}
            st["lines"].append(ln)
            by_id[ln["id"]] = ln
            st["asked"] = list({*st.get("asked", []), "extra"})
            is_opt = _types(st).get(blk or MAIN_BLOCK) == "option"
            done.append(("옵션 추가" if is_opt else "추가") + f": {name} {int(qty)} × " + (f"{price:,.0f}원" if price else f"${usd:,.2f}")
                        + ((" (Total 반영)" if ln.get("in_total") else " (Total 미반영)") if is_opt else ""))
        elif kind == "update" and op.get("line") in by_id:
            ln = by_id[op["line"]]
            if ln["kind"] == "work":
                continue                                    # 인건비는 인원·일수·단가로만 바꾼다
            if ln["kind"] == "freight":
                v = num(op.get("unit_price"))
                if v is not None and v > 0:
                    _delivery(st)["freight"] = v
                    done.append(f"물류비: {v:,.0f}원")
                    _sync_work(st)
                    by_id = {x["id"]: x for x in st["lines"]}
                continue
            for k in ("qty", "unit_price"):
                if op.get(k) is not None:
                    v = num(op[k])
                    if v is not None and v > 0:
                        ln[k] = int(v) if k == "qty" else v
                        ln["price_status"] = "set" if k == "unit_price" else ln.get("price_status")
                        if k == "qty" and ln.get("conditional"):
                            ln["confirmed"] = True              # 담당자가 수량을 정했으면 확인한 것
                        done.append(f"{ln['name']} {'수량' if k == 'qty' else '단가'}: {v:,.0f}")
            if "usd" in op:
                v = num(op.get("usd"))
                ln["usd"] = v if v is not None and v > 0 else None
                done.append(f"{ln['name']} 달러 단가: " + (f"${v:,.2f}" if ln["usd"] else "환율 환산"))
            if op.get("confirmed") is True and ln.get("conditional") and not ln.get("confirmed") and ln.get("qty"):
                ln["confirmed"] = True
                done.append(f"{ln['name']} 수량 {ln['qty']} 확인")
            if isinstance(op.get("in_total"), bool) and op["in_total"] != bool(ln.get("in_total")):
                ln["in_total"] = op["in_total"]                # 옵션 줄 Total 반영/미반영(사용자 2026-10-19)
                done.append(f"{ln['name']} 옵션 {'Total 반영' if op['in_total'] else '미반영'}")
            if isinstance(op.get("unit"), str) and clean_unit(op["unit"]) != (ln.get("unit") or ""):
                ln["unit"] = clean_unit(op["unit"])
                done.append(f"{ln['name']} 단위: {ln['unit'] or 'ea'}")
            if "block" in op or op.get("option") is True or op.get("group"):
                blk = target_block(op) or MAIN_BLOCK
                if blk != block_of(ln, _types(st), auto_block(st)):
                    if blk == MAIN_BLOCK:
                        ln.pop("block", None)
                    else:
                        ln["block"] = blk
                    done.append(f"{ln['name']} 묶음 옮김")
            for k in ("name", "remark", "commodity"):
                if isinstance(op.get(k), str) and (ln["kind"] in ("extra", "note") or k != "name"):
                    ln[k] = clean_spec(op[k]) if k == "name" else clean_lines(op[k], 100 if k == "remark" else 60)
                    done.append(f"{ln['name']} {dict(remark='비고', commodity='품목').get(k, '규격')} 수정")
        elif kind == "remove" and op.get("line") in by_id:
            ln = by_id.pop(op["line"])
            st["lines"] = [x for x in st["lines"] if x["id"] != ln["id"]]
            if ln["kind"] == "option":
                st["options"].append({k: v for k, v in ln.items() if k != "id"})
            case = _delivery(st).get("case")
            if ln["kind"] == "work" and case in (3, 4):
                set_case(case - 2, "설치 제외")
            elif ln["kind"] == "freight" and case in (2, 4):
                set_case(case - 1, "물류비 제외")
            if ln["kind"] in ("work", "freight"):
                _sync_work(st)
                by_id = {x["id"]: x for x in st["lines"]}
            done.append(f"삭제: {ln['name']}")
        elif kind == "option":
            name = str(op.get("name") or "")
            if op.get("include") is True:
                opt = next((o for o in st["options"] if name and (name.lower() in o["name"].lower() or name.upper() == o["key"])), None)
                if opt:
                    st["options"].remove(opt)
                    ln = {**opt, "id": _new_id(st)}
                    st["lines"].append(ln)
                    by_id[ln["id"]] = ln
                    done.append(f"옵션 포함: {opt['name']}")
            elif op.get("include") is False:
                ln = next((x for x in st["lines"] if x["kind"] == "option" and name and (name.lower() in x["name"].lower() or name.upper() == x["key"])), None)
                if ln:
                    st["lines"].remove(ln)
                    st["options"].append({k: v for k, v in ln.items() if k != "id"})
                    done.append(f"옵션 제외: {ln['name']}")
        elif kind == "blocks" and isinstance(op.get("blocks"), list):       # 화면에서 고친 묶음 목록(순서·제목·소계·사양·문구)
            old = {b["id"]: b for b in blocks(st)}
            out, used = [], set()
            for b in op["blocks"][:BLOCKS_MAX]:
                if not isinstance(b, dict):
                    continue
                cb = _clean_block(b)
                if not cb:
                    continue
                if cb["id"] in old and cb["id"] not in used and old[cb["id"]]["type"] == cb["type"]:
                    pass
                elif cb["id"] == MAIN_BLOCK:
                    continue
                else:
                    tmp = cb["id"]
                    n = max([int(x[1:]) for x in [*old, *used] if re.fullmatch(r"B\d+", x)] + [0]) + 1
                    cb["id"] = f"B{n}"
                    if tmp:
                        alias[tmp] = cb["id"]
                used.add(cb["id"])
                out.append(cb)
            new = blocks({**st, "blocks": out})
            if new != blocks(st):
                st["blocks"] = new
                done.append("묶음 구성 수정")
            keep = {b["id"] for b in blocks(st)}
            for ln in st["lines"]:                         # 지운 묶음의 줄은 기본 묶음으로
                if ln.get("block") and ln["block"] not in keep:
                    ln.pop("block", None)
        elif kind == "group":                                   # 대화: '설치 및 시운전 묶음 만들어 줘'
            target_block({"group": op.get("title"), "subtotal": op.get("subtotal")})
        elif kind == "nego":
            if op.get("remove") is True:
                if st.get("nego"):
                    st.pop("nego", None)
                    done.append("NEGO 삭제")
                continue
            pct, amt = num(op.get("percent")), num(op.get("amount"))
            lab = re.sub(r"\s+", " ", str(op.get("label") or "")).strip()[:40]
            if not lab and "label" not in op:                  # 대화로 금액·%만 바꾸면 이름은 그대로
                lab = (st.get("nego") or {}).get("label") or ""
            if pct is not None and 0 < pct <= 100:
                st["nego"] = {"mode": "percent", "value": pct, "label": lab}
                done.append(f"NEGO: {pct:g}%")
            elif amt is not None and amt > 0:
                st["nego"] = {"mode": "amount", "value": amt, "label": lab}
                done.append("NEGO: -" + (f"${amt:,.2f}" if st["form"].get("lang") == "en" else f"{amt:,.0f}원"))
            elif op.get("value") is None and op.get("mode") and st.get("nego"):   # 화면에서 값을 비움 → 삭제
                st.pop("nego", None)
                done.append("NEGO 삭제")
        elif kind == "no_extra":
            st["asked"] = list({*st.get("asked", []), "extra"})
            done.append("다른 추가 항목 없음")
    return st, done


def edit_ops(state: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    """화면에서 직접 고친 내용(머리·품목·작업) → 변경 목록. 바뀐 것만, 숫자 대조는 하지 않는다(사람이 직접 입력)."""
    ops: list[dict[str, Any]] = []
    for k, v in (payload.get("form") or {}).items():
        if k in FORM_FIELDS and FORM_FIELDS[k][1] == "list":
            if clean_notes(v) != clean_notes(state["form"].get(k)):
                ops.append({"op": "set", "field": k, "value": clean_notes(v)})
            continue
        if k in FORM_FIELDS and v != (state["form"].get(k) or ("" if k in CLEARABLE_FIELDS else None)) \
                and (v not in (None, "") or k in CLEARABLE_FIELDS):
            ops.append({"op": "set", "field": k, "value": v or ""})
    if isinstance(payload.get("blocks"), list):
        ops.insert(0, {"op": "blocks", "blocks": payload["blocks"]})     # 줄보다 먼저 — 새 묶음 임시 id 를 줄이 가리킨다
    if "nego" in payload:
        nv = payload.get("nego") or {}
        cur = state.get("nego") or {}
        if (nv.get("mode"), nv.get("value"), nv.get("label") or "") != (cur.get("mode"), cur.get("value"), cur.get("label") or ""):
            if nv.get("value"):
                ops.append({"op": "nego", "label": nv.get("label") or "",
                            **({"percent": nv["value"]} if nv.get("mode") == "percent" else {"amount": nv["value"]})})
            elif cur:
                ops.append({"op": "nego", "remove": True})
    dv = payload.get("delivery")
    if isinstance(dv, dict) and (dv.get("case") != _delivery(state).get("case") or dv.get("freight") != _delivery(state).get("freight")
                                 or (dv.get("block") or MAIN_BLOCK) != (_delivery(state).get("block") or MAIN_BLOCK)):
        ops.append({"op": "delivery", "case": dv.get("case"), "freight": dv.get("freight"), "block": dv.get("block") or MAIN_BLOCK})
    w = payload.get("work")
    if isinstance(w, dict) and any(w.get(k) != state["work"].get(k) for k in ("people", "days", "rate")):
        ops.append({"op": "work", **{k: w.get(k) for k in ("people", "days", "rate")}})
    if isinstance(payload.get("lines"), list):
        cur = {ln["id"]: ln for ln in state["lines"] if ln["kind"] not in ("work", "freight")}
        seen = set()
        for x in payload["lines"]:
            if not isinstance(x, dict):
                continue
            if x.get("id") in cur:
                seen.add(x["id"])
                ln = cur[x["id"]]
                ch = {k: x[k] for k in ("qty", "unit_price", "name", "remark", "usd") if k in x and x[k] != ln.get(k)}
                if isinstance(x.get("unit"), str) and clean_unit(x["unit"]) != (ln.get("unit") or ""):
                    ch["unit"] = x["unit"]
                if "block" in x and (x["block"] or MAIN_BLOCK) != (ln.get("block") or MAIN_BLOCK):
                    ch["block"] = x["block"] or MAIN_BLOCK
                if isinstance(x.get("commodity"), str) and x["commodity"] != qx.commodity_of(ln):
                    ch["commodity"] = x["commodity"]
                if x.get("confirmed") is True and ln.get("conditional") and not ln.get("confirmed"):
                    ch["confirmed"] = True
                if isinstance(x.get("in_total"), bool) and x["in_total"] != bool(ln.get("in_total")):
                    ch["in_total"] = x["in_total"]
                if ch:
                    ops.append({"op": "update", "line": x["id"], **ch})
            elif not x.get("id"):
                ops.append({"op": "add", **{k: x.get(k) for k in ("name", "qty", "unit_price", "remark", "commodity", "usd", "block", "unit", "kind",
                                                                  "in_total")}})
        ops += [{"op": "remove", "line": i} for i in cur if i not in seen]
    for name in payload.get("include_options") or []:
        ops.append({"op": "option", "name": name, "include": True})
    return ops


def delivery_case(text: str) -> int | None:
    """'1'·'2번'·'1. 택배 발송'(버튼 글)·'택배로'·'화물 직납'·'설치하면서 납품'·'화물로 보내고 설치'·'별도 협의' → 1~5. 모호하면 None."""
    t = text.strip()
    m = re.match(r"^\s*([1-5])\s*(번|\.|\)|$)", t)
    if m:
        return int(m.group(1))
    def said(word: str) -> bool:                      # 말했고 부정하지 않음('화물은 안 써요'·'설치 없이'·'택배 말고' 제외)
        return bool(re.search(word, t)) and not re.search(rf"({word})\S{{0,3}}\s*(안\s|안해|안\s*써|없|말고|아니|빼|제외)", t)

    if re.search(r"별도\s*협의|추후\s*협의|협의\s*후", t):
        return 5
    freight, install, courier = said(r"화물|직납"), said(r"설치|인원"), said(r"택배")
    if freight and install:
        return 4
    if freight:
        return 2
    if install and not courier:
        return 3
    if courier and not install:
        return 1
    return None


def quick_ops(state: dict[str, Any], message: str) -> list[dict[str, Any]] | None:
    """지금 질문에 대한 확실한 꼴의 답은 코드로 읽는다(AI 없이) — 납품 방식 번호·말, '2명 3일', '50만원', '없음'."""
    q = next_question(state)
    if not q:
        return None
    m = message.strip()
    if q["id"] == "delivery":
        case = delivery_case(m)
        if case:
            p, d = re.search(r"(\d+)\s*명", m), re.search(r"(\d+)\s*일", m)
            ops = [{"op": "delivery", "case": case}]
            if p or d:
                ops.append({"op": "work", "people": p and float(p.group(1)), "days": d and float(d.group(1))})
            return ops
    if q["id"] == "freight":
        n = [x for x in said_numbers(m) if x >= 1000]
        if len(n) == 1:
            return [{"op": "delivery", "freight": n[0]}]
    if q["id"] == "work_detail":
        p, d = re.search(r"(\d+)\s*명", m), re.search(r"(\d+)\s*일", m)
        if p or d:
            return [{"op": "work", "people": p and float(p.group(1)), "days": d and float(d.group(1))}]
    if q["id"] == "work_rate":
        n = sorted(said_numbers(m))
        if n and len({x for x in n if x >= 1000}) == 1:
            return [{"op": "work", "rate": max(n)}]
    if q["id"] == "extra" and _NO_RE.match(m):
        return [{"op": "no_extra"}]
    if q["id"] == "fx" and not _USD_RE.search(m):
        n = [x for x in said_numbers(m) if 100 <= x <= 100000]
        if len(n) == 1:
            return [{"op": "set", "field": "fx_rate", "value": n[0]}]
    return None


# ── AI 에게 줄 상황 ──────────────────────────────────────────────────────────────

QUOTE_SYSTEM = """너는 유엔디로보틱스 견적 담당이다. [견적 상태]와 [지금 묻는 질문]을 보고 사용자의 마지막 [말]을 견적 변경(ops)으로 옮긴다.
JSON 으로만 답한다. [말]에 분명히 있는 내용만 옮기고 지어내지 않는다. 숫자는 [말]에 적힌 그대로(원 단위; '50만원'은 500000).
ops 종류:
- {"op":"set","field":<customer|to|cc|initials|contact_name|contact_title|contact_name_en|contact_honorific|contact_mobile|contact_email|delivery|place|payment|comments|note|lang|fx_rate|bank|account_no|account_holder>,"value":...}
  (lang 은 "ko" 또는 "en". contact_* 는 견적을 '보내는' 우리 회사(유엔디로보틱스) 담당자다 — '담당자는 …'처럼 분명할 때만.
   견적을 '받는' 고객사 쪽 사람('○○ 대표님께', '○○님 앞으로')은 to, 회사 이름은 customer.
   '홍길동 이사'처럼 이름과 직함이 같이 오면 contact_name 과 contact_title 로 나눈다.
   해외 견적서에 찍히는 우리 담당자 영문 이름은 contact_name_en, 호칭은 contact_honorific("Mr." 또는 "Ms." — 말에 있을 때만).
   bank·account_no·account_holder 는 고객사가 우리에게 돈을 보낼 '입금 계좌'(견적서의 Bank·Account No. 칸)다 — 결제조건(payment)과 다르다.
   '계좌 바꿔 줘'·'입금 계좌는 신한은행 …' 이면 이 칸들을 바꾼다. 계좌번호는 [말]에 적힌 그대로. 새 계좌번호를 말하지 않았으면 ops 를 비우고
   answer 로 새 은행·계좌번호·예금주를 물어본다. 계좌 변경을 거절하거나 내부 정책·담당 부서 확인 같은 말을 지어내지 않는다.)
- {"op":"set","field":<purpose|rental_months|rental_start>,"value":...}  — 판매/대여 견적서: purpose 는 "sale"(판매) 또는 "rental"(대여·렌탈),
  rental_months 는 대여 계약 개월 수(정수, '3년'이면 36), rental_start 는 대여 시작일(YYYY-MM-DD) — [말]에 있을 때만.
- {"op":"delivery","case":1|2|3|4|5,"freight":물류비}  — 납품 방식: 1 택배 발송(품목 그대로), 2 화물 직납(물류비 추가),
  3 우리 인원이 설치하며 납품(설치 인건비 추가), 4 화물로 보내고 우리 인원이 설치(물류비+인건비), 5 별도 협의(품목 그대로). freight 는 물류비 금액(말에 있을 때만).
- {"op":"work","people":명,"days":일,"rate":1인1일단가}  — 설치 인건비(인원·일수·단가). 설치를 빼라고 하면 {"op":"work","needed":false}
- {"op":"add","name":"MG10 그리퍼","commodity":"Magbot Gripper","qty":1,"unit_price":100000,"remark":""}  — 새 항목(수기 견적의 제품 품목도 이것으로 — 품명·수량·단가가 [말]에 있을 때만).
  달러로 말한 단가('1000달러'·'$1,000'·'USD 1000'·'1000불')는 unit_price 대신 "usd":1000 (원화로 바꾸지 않는다).
  해외 견적(머리의 언어=en)에서 '원'·'만원' 없이 말한 단가도 달러로 보고 usd 에 넣는다.
  새 항목은 add 하나에 단가(unit_price 또는 usd)까지 같이 넣는다 — add 와 update 로 나누지 않는다.
  수량 단위를 말하면 "unit" 도 넣는다('36개월' → qty 36, unit "개월" / '1식' → unit "식" / '2MD' → unit "MD"). 말 안 하면 빼고(기본 ea).
  견적서 표는 품목(commodity, 예: Magbot ATC)과 규격(name, 모델·세부 사양) 칸이 따로다 — commodity 는 말에 있을 때만, 없으면 "".
- {"op":"update","line":"L3","qty":3}  — 품목 수량·단가·규격(name)·품목(commodity)·비고 바꾸기(line 은 [견적 상태]의 줄 id)
- {"op":"update","line":"L3","usd":1000}  — 달러 단가 바꾸기(해외 견적, 위와 같은 기준). 물류비·설치 인건비 줄은 원화로만
- {"op":"update","line":"L3","confirmed":true}  — 조건부 수량을 담당자가 '맞다'고 확인할 때만
- 견적 본문은 묶음 여러 개다([견적 상태]의 묶음 목록). 줄을 특정 묶음에 넣거나 옮길 때:
  {"op":"add",...,"option":true}  — '옵션으로'·'선택 사항으로' 넣는 품목(견적서 < OPTION > 아래, 공급가액은 보이고 기본은 Total 미반영)
  {"op":"update","line":"L3","in_total":true|false}  — 옵션 줄을 Total 에 반영/미반영('옵션도 합계에 넣어 줘'). 반영한 옵션만 옵션 Sub total·Total 에 들어감
  {"op":"add",...,"group":"설치 및 시운전"}  — 소제목 묶음에 넣기(없으면 소계 표시 묶음으로 새로 만든다)
  {"op":"update","line":"L3","option":true} / {"op":"update","line":"L3","group":"유지보수"} / {"op":"update","line":"L3","block":"B0"}  — 묶음 옮기기
  {"op":"group","title":"유지보수","subtotal":true}  — 빈 소제목 묶음 만들기
- {"op":"nego","amount":800000}  또는 {"op":"nego","percent":5}  — NEGO(할인, '네고'·'할인'·'디스카운트'). 빼라고 하면 {"op":"nego","remove":true}
- {"op":"remove","line":"L5"}  — 품목 빼기
- {"op":"option","name":"IB","include":true|false}  — [넣을 수 있는 옵션]을 넣거나 빼기
- {"op":"no_extra"}  — 다른 추가 항목이 없다고 할 때
질문·잡담이면 ops 를 비우고 answer 에 2~3문장 존댓말로 답한다([견적 상태]에 있는 내용만, 수치를 지어내지 않음).
answer 는 줄바꿈(\\n)·목록·**강조**를 쓸 수 있다 — 한 문단 1~2문장, 항목은 한 줄에 하나, 핵심 단어 2~5곳만 **…**.
{"ops": [], "answer": ""}"""


def describe(state: dict[str, Any]) -> str:
    f = state["form"]
    ty = _types(state)
    names = {b["id"]: (b.get("title") or ("기본" if b["id"] == MAIN_BLOCK else OPTION_TITLE if b["type"] == "option" else ""))
             for b in blocks(state)}
    rows = "\n".join(f"- {ln['id']} [{ln['kind']}] 묶음 {block_of(ln, ty, auto_block(state))}({names.get(block_of(ln, ty, auto_block(state)), '')}"
                     + ((", 옵션·Total 반영" if ln.get("in_total") else ", 옵션·Total 미반영")
                        if ty.get(block_of(ln, ty, auto_block(state))) == "option" else "") + ")"
                     + f" · 품목 {qx.commodity_of(ln) or '(비움)'} · 규격 {ln['name']} · 수량 {ln.get('qty')} · 단가 {ln.get('unit_price')}원"
                     + (f" · 달러 단가 ${ln['usd']:,.2f}" if ln.get("usd") else "")
                     + (f" · 비고 {ln['remark']}" if ln.get("remark") else "") for ln in state["lines"])
    opts = ", ".join(f"{o['name']}({o.get('unit_price')}원)" for o in state["options"]) or "(없음)"
    head = ", ".join(f"{FORM_FIELDS[k][0]}={f.get(k)}" for k in FORM_FIELDS if f.get(k) not in (None, ""))
    w, dv = state["work"], _delivery(state)
    case = DELIVERY.get(dv.get("case"))
    return (f"선정 모델: {state['model'] or '(수기 견적 — 품목을 담당자가 직접 넣음)'}\n머리: {head}\n품목:\n{rows or '(없음)'}\n넣을 수 있는 옵션: {opts}\n"
            f"납품 방식: {(str(dv['case']) + '. ' + case['label']) if case else '(아직 안 정함)'}, 물류비={dv.get('freight')}\n"
            f"설치 인건비: 인원={w.get('people')}, 일수={w.get('days')}, 1인1일단가={w.get('rate')}\n"
            f"묶음: " + ", ".join(f"{b['id']} {b['type']} '{names.get(b['id'], '')}'" + (" 소계" if b.get("subtotal") else "")
                                 for b in blocks(state)) + "\n"
            + (f"NEGO: {nego_label(state)} {(state.get('nego') or {}).get('value')}"
               + ("%" if (state.get("nego") or {}).get("mode") == "percent" else "") + "\n" if state.get("nego") else "")
            + f"합계(원화, 옵션 제외·NEGO 차감): {subtotal(state):,}원"
            + (f"\n해외(달러) 견적 — 환율={f.get('fx_rate') or '(없음)'}, 달러 합계="
               + (f"${subtotal_usd(state):,.2f}" if subtotal_usd(state) is not None else "(달러 단가 빈 줄 있음)")
               if f.get("lang") == "en" else ""))


# ── 발행용 줄(엑셀) ───────────────────────────────────────────────────────────────

def render_lines(state: dict[str, Any], lang: str, fx_rate: float | None) -> list[dict[str, Any]]:
    """견적 상태 → quote_xlsx 줄(언어별 문구, 해외는 달러 환산). 묶음 순서대로(사용자 2026-10-13):
    품목 줄(row 없음 = item) 사이에 묶음 구분(gap)·소제목(section)·소계(subtotal)·옵션 줄(option, 합계 제외)·사양(spec)·문구(text),
    맨 끝에 NEGO(nego, 음수). 발행 기록에 이대로 남아 다시 받아도 같은 견적서가 나온다. 예전 기록(row 없음)은 모두 품목 줄."""
    bl = blocks(state)
    ids = {b["id"] for b in bl}
    groups: dict[str, list[dict[str, Any]]] = {b["id"]: [] for b in bl}
    auto = auto_block(state)
    for ln in state["lines"]:
        groups[block_of(ln, ids, auto)].append(ln)
    # 단락 = 내용이 있는 묶음. 단락이 둘 이상이면 단락마다 끝에 회색 구분줄(gap), 품목 묶음이 둘 이상이면 품목 묶음마다 Sub total
    # (< OPTION > 묶음은 소계 없음) — 사용자 2026-10-13, 방역 로봇 견적서 방식. 기본 묶음 하나뿐이면 예전처럼 둘 다 없음.
    sections: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for b in bl:
        if b["type"] in ("items", "option"):
            rows = _item_rows(state, groups[b["id"]], lang, fx_rate, auto_last=b["id"] != MAIN_BLOCK)
        elif b["type"] == "spec":
            rows = [{"row": "spec", "label": r.get("label") or "", "value": r.get("value") or ""} for r in b.get("rows") or []]
        else:
            rows = [{"row": "text", "text": b["text"]}] if b.get("text") else []
        if rows:
            sections.append((b, rows))
    multi = len(sections) > 1
    # 대여 견적서는 품목 + < OPTION > 이어도 품목 묶음에 Sub total(사용자 2026-10-22). 판매는 LG CNS 견적서처럼 품목 묶음 둘 이상일 때만
    subtotals = (sum(1 for b, _ in sections if b["type"] == "items") > 1
                 or (is_rental(state) and any(b["type"] == "option" for b, _ in sections)))
    out: list[dict[str, Any]] = []
    for b, rows in sections:
        rows = [r if r.get("row") else {**r, "block": b["id"]} for r in rows]
        title = b.get("title") or ""
        if b["type"] == "option":
            # 옵션 줄마다 [반영/미반영](사용자 2026-10-19) — 둘 다 공급가액은 보이고, 반영한 줄만 Sub total·Total 에 들어간다.
            # in_total 이 있는 줄 = 이 방식(없는 예전 발행본은 공급가액 칸을 비운 채 그대로)
            inc = {ln["id"] for ln in groups[b["id"]] if ln.get("in_total")}
            opt = [r if r.get("row") else {**r, "row": "option", "in_total": r.get("id") in inc} for r in rows]
            out.append({"row": "section", "text": title or OPTION_TITLE})
            out += opt
            if any(r.get("in_total") for r in opt):
                out.append({"row": "subtotal", "text": "Sub total",
                            "amount": round(sum(r["amount"] for r in opt if r.get("in_total")), 2)})
        else:
            if title and b["type"] != "text":
                out.append({"row": "section", "text": title})
            out += rows
            if b["type"] == "items" and subtotals:
                out.append({"row": "subtotal", "text": "Sub total", "amount": round(sum(r["amount"] for r in rows if not r.get("row")), 2)})
        if multi:
            out.append({"row": "gap"})
    base = round(sum(r["amount"] for r in out if not r.get("row") or r.get("in_total")), 2)
    cut = nego_amount(state, base)
    if cut > 0:
        out.append({"row": "nego", "key": "nego", "commodity": "", "desc": nego_label(state), "qty": 1,
                    "unit_price": -cut, "amount": -cut, "remark": ""})
    if (multi or cut > 0) and out and out[-1].get("row") != "gap":
        out.append({"row": "gap"})                     # Total 바로 위에도 회색 줄(사용자 2026-10-13)
    return out


def _item_rows(state: dict[str, Any], lines: list[dict[str, Any]], lang: str, fx_rate: float | None,
               auto_last: bool = False) -> list[dict[str, Any]]:
    """묶음 하나의 품목 줄 — 본체·액세서리 → 옵션 → 물류비 → 설치 인건비 → 추가 항목 순.
    사용자 지정 묶음(auto_last)은 직접 넣은 항목·서술형 줄(적은 순서) 다음에 물류비·인건비."""
    kg = state.get("payload_kg")
    pay = "" if kg is None else f" (Payload: {kg:g} kg)" if lang == "en" else f" (Payload {kg:g}Kg)"
    order = {"product": 0, "option": 1, "freight": 2, "work": 3, "extra": 4}
    if auto_last:
        order.update(freight=5, work=6)
    out = []
    for ln in sorted(lines, key=lambda x: order.get(x["kind"], 4)):     # 서술형 줄(note)은 추가 항목과 적은 순서대로
        if ln["kind"] == "note":
            out.append({"row": "spec", "label": ln.get("commodity") or "", "value": ln.get("name") or "", "note_id": ln.get("id")})
            continue
        k = ln["key"]
        if k in ("MASTER T.C", "T.P"):
            desc = f"{state['model']}\n{qx.ATC_DESC[lang][k]}{pay}"
        elif ln["kind"] == "work":
            desc = WORK_NAME[lang]
        elif ln["kind"] == "freight":
            desc = FREIGHT_NAME[lang]
        elif k in qx._DESC:
            desc = (qx._DESC_EN if lang == "en" else qx._DESC)[k]
        else:
            desc = ln["name"]
        if ln["kind"] == "work":
            p, d = state["work"]["people"], state["work"]["days"]
            std = state["work"].get("rate") == WORK_RATE            # 회사 기준 단가면 기준을 비고에
            remark = (f"{p} persons × {d} days" + (" (8h/day per person)" if std else "")) if lang == "en" \
                else f"{p}명 × {d}일" + (" (1인 8H 기준)" if std else "")
        elif ln["kind"] == "freight":
            remark = "Freight transport" if lang == "en" else ln.get("remark") or ""
        elif ln["kind"] in ("product", "option") and ln.get("remark") == qx._REMARK["ko"].get(k, ""):
            remark = qx._REMARK[lang].get(k, "")             # 기본 비고는 언어에 맞게, 사용자가 고친 비고는 그대로
        else:
            remark = ln.get("remark") or ""
        krw = float(ln["unit_price"]) if ln.get("unit_price") else None
        unit = usd_price(ln, fx_rate) if lang == "en" else krw
        if unit is None:
            raise ValueError(f"{ln['name']} — " + ("달러 단가가 없습니다(직접 적거나 환율 입력)" if lang == "en" else "단가가 없습니다"))
        # id — 올린 견적서 파일을 작성 상태로 되읽을 때 줄 짝짓기에 쓴다(quote_readback, 사용자 2026-10-15)
        # months — 대여 견적서에서 단가(월 납입금)에 곱한 개월 수(엑셀 금액 식 =D×E×개월, 사용자 2026-10-22)
        mf = month_factor(state["form"], ln)
        out.append({"id": ln.get("id"), "key": k, "commodity": qx.commodity_of(ln), "desc": desc, "qty": int(ln["qty"]),
                    **({"photo": ln["photo"]} if ln.get("photo") else {}), "unit_price": unit, "unit_price_krw": krw,
                    **({"months": mf} if mf > 1 else {}),
                    "amount": round(int(ln["qty"]) * unit * mf, 2), "remark": remark, "option": ln["kind"] == "option", "kind": ln["kind"],
                    **({"unit": unit_label("인·일", lang, int(ln["qty"]))} if ln["kind"] == "work"
                       else {"unit": unit_label(ln["unit"], lang, int(ln["qty"]))} if ln.get("unit") else {})})
    return out
