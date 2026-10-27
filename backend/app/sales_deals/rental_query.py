"""대여 나간 제품 묻기(사용자 2026-10-22) — AI 대화에서 '10월 이후 대여 나간 제품 시리얼이랑 알려줘'.

AI 가 숫자·시리얼을 지어내지 않게 /v1/generate 의 결정론 fast-path 로 DB 를 바로 읽어 표로 답한다(자금·회의록 fast-path 와 같은 방식).
- 대상: 시리얼 상태 R(대여)인 제품 — 대여 견적서로 낸 건은 시리얼 등록 때 R 이 기본.
- 기준 날짜: 대여 시작일(건의 대여 조건) → 없으면 그 제품 출하일(무상 기간 시작) → 건의 첫 출하일. 날짜를 모르면 '날짜 미정'으로 따로 보인다.
- 시리얼이 아직 없는 대여 건(대여 견적서로 수주까지 했지만 등록 전)은 아래에 건만 따로 알린다.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_RENT = re.compile(r"대여|렌탈|임대|rental|빌려\s*(준|간|줬|가\s*있)", re.I)
# 무엇이 나가 있는지 묻는 말 — 제품·S/N 같은 대상이나 '나간·목록' 같은 말이 있어야 한다(검증 2026-10-26:
# '알려·현황' 한 마디만으로 잡아 재무팀 '임대료 현황 알려줘'·'대여 견적서 양식 알려줘'까지 가로챘다)
_TARGET = re.compile(r"제품|로봇|장비|기기|품목|시리얼|S\s*/\s*N|\bSN\b|나간|나가\s*있|중인|목록|리스트|몇\s*(대|개|건)", re.I)
# 돈·서류·견적 이야기는 이 질문이 아니다(자금·경비·견적서 대화로 넘긴다)
_NOT = re.compile(r"견적|발행|추천|임대료|대여료|렌탈료|사용료|위약금|계약서|양식|방법|계산|거래\s*내역|첨부|단가|가격|금액|세금|부가세")


def detect(messages: list[dict]) -> bool:
    if not messages or messages[-1].get("role") != "user":
        return False
    t = str(messages[-1].get("content") or "")
    return bool(_RENT.search(t) and _TARGET.search(t) and not _NOT.search(t))


def _date_in(text_: str, today: dt.date) -> tuple[dt.date, str, str] | None:
    """말 속 첫 날짜 → (날짜, 단위 'year'|'month'|'day', 날짜 뒤에 붙은 말). 연도가 없고 그 달이 아직 안 왔으면 작년."""
    m = re.search(r"(20\d{2})\s*[년./-]\s*(\d{1,2})\s*(?:월\s*(?:(\d{1,2})\s*일)?|[./-]\s*(\d{1,2}))?", text_)
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3] or m[4] or 1)), "day" if (m[3] or m[4]) else "month", text_[m.end():]
        except ValueError:
            return None
    m = re.search(r"(20\d{2})\s*년", text_)
    if m:
        return dt.date(int(m[1]), 1, 1), "year", text_[m.end():]
    m = re.search(r"(?<![\d.])(\d{1,2})\s*(?:월\s*(?:(\d{1,2})\s*일)?|[/.]\s*(\d{1,2})(?![\d.]))", text_)
    if m and 1 <= int(m[1]) <= 12:
        month, day = int(m[1]), int(m[2] or m[3] or 1)
        year = today.year if month <= today.month else today.year - 1
        try:
            return dt.date(year, month, day), "day" if (m[2] or m[3]) else "month", text_[m.end():]
        except ValueError:
            return dt.date(year, month, 1), "month", text_[m.end():]
    return None


def _end_of(day: dt.date, unit: str) -> dt.date:
    if unit == "year":
        return dt.date(day.year, 12, 31)
    if unit == "month":
        return dt.date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
    return day


# 기간을 말한 것 같은 말 — 알아듣지 못했으면 전체 목록이라고 알린다
_PERIOD_HINT = re.compile(r"지난|저번|최근|이번\s*(달|주|해|년)|작년|올해|금년|금월|이전|이후|부터|까지|\d+\s*(월|년|일|주|개월)|분기|상반기|하반기")


def period(text_: str, today: dt.date) -> dict[str, Any]:
    """대여 시작일 기간(사용자 2026-10-26) → {since, until(그날 포함), unclear}.
    '10월 이후·부터' → 그날부터 · '10월 이전·전' → 그 전날까지 · '10월까지' → 그달 끝까지 · '10월' 만 → 그달 ·
    '9월부터 10월까지' → 둘 다 · '지난달'·'이번 달'·'최근 3개월'·'올해'·'작년'. 기간 같은 말인데 못 알아들으면 unclear."""
    t = text_
    out: dict[str, Any] = {"since": None, "until": None, "unclear": False}
    m = re.search(r"최근\s*(\d{1,3})\s*(개월|달|주|일|년)", t)
    if m:
        n = int(m[1])
        days = {"주": 7 * n, "일": n}.get(m[2])
        out["since"] = today - dt.timedelta(days=days) if days else _add_months(today, -(12 * n if m[2] == "년" else n))
        return out
    first = today.replace(day=1)
    if re.search(r"지난\s*달|저번\s*달|전월|지난\s*월", t):
        prev = _add_months(first, -1)
        return {**out, "since": prev, "until": first - dt.timedelta(days=1)}
    if re.search(r"이번\s*달|이달|금월", t):
        return {**out, "since": first}
    if re.search(r"작년|지난\s*해|전년", t):
        return {**out, "since": dt.date(today.year - 1, 1, 1), "until": dt.date(today.year - 1, 12, 31)}
    if re.search(r"올해|금년|이번\s*해|올\s*한\s*해", t):
        return {**out, "since": dt.date(today.year, 1, 1)}
    hit = _date_in(t, today)
    if hit:
        day, unit, rest = hit
        after = rest.lstrip()[:12]
        if re.match(r"(이전|전\b|전에|전까지|전으로|before)", after):
            return {**out, "until": day - dt.timedelta(days=1)}
        if re.match(r"까지", after):
            return {**out, "until": _end_of(day, unit)}
        if re.match(r"(이후|부터|후|since|after)", after):
            nxt = _date_in(rest, today)
            if nxt and re.match(r"(까지|이전|전)", nxt[2].lstrip()):
                until = _end_of(nxt[0], nxt[1]) if nxt[2].lstrip().startswith("까지") else nxt[0] - dt.timedelta(days=1)
                return {**out, "since": day, "until": until}
            return {**out, "since": day}
        return {**out, "since": day, "until": _end_of(day, unit)}   # '10월 대여 나간' = 그달
    out["unclear"] = bool(_PERIOD_HINT.search(t))
    return out


def _add_months(day: dt.date, months: int) -> dt.date:
    y, m = divmod(day.month - 1 + months, 12)
    y, m = day.year + y, m + 1
    return dt.date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def since_date(text_: str, today: dt.date) -> dt.date | None:
    """기간의 시작일만(예전 호출부·테스트 호환)."""
    return period(text_, today)["since"]


def _j(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _start(unit: dict, quote: dict, shipments: list) -> str | None:
    rent = (quote or {}).get("rental") or {}
    if rent.get("start"):
        return rent["start"]
    if unit.get("warranty_start"):
        return str(unit["warranty_start"])[:10]
    dates = sorted(s.get("date") for s in shipments or [] if s.get("date"))
    return dates[0] if dates else None


async def rented_units(db: AsyncSession, since: dt.date | None, until: dt.date | None = None) -> dict[str, Any]:
    """{rows: [{serial, part, line, customer, deal_no, title, start, end, status, as_now}], undated: [...],
    waiting: [출하 전 R], unregistered: [건]}
    대여 단계는 S/N 진행으로 읽는다(사용자 2026-10-27): 등록 = 대여 전, 출하 = 대여 중, 회수 = 대여 완료.
    '대여 나간 제품' = 대여 중만 — R 이거나, 대여 건에서 나가 A/S 중(A)인 제품. 대여 전은 waiting 으로 따로."""
    rs = (await db.execute(text(
        "SELECT u.serial, u.part_name, u.line_name, u.status, u.purpose, u.warranty_start, u.deal_id, "
        "       d.customer, d.title, d.deal_no, d.quote, d.shipments "
        "FROM product_units u JOIN sales_deals d ON d.id = u.deal_id "
        "WHERE COALESCE(d.dropped, false) = false AND ("
        "  (u.status = '출하' AND (u.purpose = 'R' OR (u.purpose = 'A' AND d.quote->>'biz' = 'rental')))"
        # 출하 전 R — 회수를 마친 건은 빼고(그 건에서 끝내 안 나간 S/N)
        "  OR (u.status = '등록' AND u.purpose = 'R' AND d.quote->'rental'->'return'->>'done' IS NULL)) "
        "ORDER BY d.customer, u.deal_id, u.line_no, u.id"))).mappings().all()
    rows, undated, waiting = [], [], []
    for r in rs:
        q = _j(r["quote"]) or {}
        start = _start(dict(r), q, _j(r["shipments"]) or [])
        item = {"serial": r["serial"] or "(S/N 없음)", "part": r["part_name"] or "", "line": (r["line_name"] or "").split("\n")[0],
                "customer": r["customer"] or "", "deal_no": q.get("no") or r["deal_no"] or "", "title": r["title"] or "",
                "start": start, "end": ((q.get("rental") or {}).get("end")), "status": r["status"], "as_now": r["purpose"] == "A"}
        if r["status"] == "등록":
            waiting.append(item)
        elif start is None:
            undated.append(item)
        elif (since is None or start >= since.isoformat()) and (until is None or start[:10] <= until.isoformat()):
            rows.append(item)
    rows.sort(key=lambda x: (x["start"], x["customer"]))
    # 대여 견적서로 낸 건인데 R 시리얼이 아직 없는 건
    # (회수를 마친 건·R 시리얼을 이미 등록한 건은 빼고)
    reg = set((await db.execute(text("SELECT DISTINCT deal_id FROM product_units WHERE purpose IN ('R', 'A') AND deal_id IS NOT NULL"))).scalars().all())
    ds = (await db.execute(text(
        "SELECT id, customer, title, deal_no, quote, won FROM sales_deals "
        "WHERE quote->>'biz' = 'rental' AND COALESCE(dropped, false) = false "
        "AND quote->'rental'->'return'->>'done' IS NULL ORDER BY id"))).mappings().all()
    unregistered = [{"customer": d["customer"] or "", "deal_no": (_j(d["quote"]) or {}).get("no") or d["deal_no"] or "",
                     "title": d["title"] or "", "won": bool(d["won"]), "rental": (_j(d["quote"]) or {}).get("rental")}
                    for d in ds if d["id"] not in reg]
    return {"rows": rows, "undated": undated, "waiting": waiting, "unregistered": unregistered}


def answer(data: dict[str, Any], since: dt.date | None, until: dt.date | None = None, unclear: bool = False) -> str:
    """채팅에 보일 답 — 채팅창은 표·굵은 글씨 문법을 그리지 않으므로 번호 목록의 일반 글로(사용자 2026-10-22 확인).
    unclear = 기간을 말한 것 같은데 알아듣지 못함 → 전체 목록이라고 먼저 알린다(검증 2026-10-26)."""
    if since and until:
        head = f"{since:%Y-%m-%d} ~ {until:%Y-%m-%d} 대여 나간 제품"
    elif until:
        head = f"{until:%Y-%m-%d}까지 대여 나간 제품"
    else:
        head = f"{since:%Y-%m-%d} 이후 대여 나간 제품" if since else "지금 대여 중인 제품"
    basis = "(S/N 상태 R · 대여 중(출하) · 대여 시작일 기준, 없으면 출하일)"
    out = ["말씀하신 기간을 알아듣지 못해 전체 목록을 보여 드립니다. '10월 이후', '9월부터 10월까지', '지난달', '최근 3개월'처럼 "
           "말씀해 주시면 그 기간으로 거릅니다.\n"] if unclear else []
    rows = data["rows"]
    if rows:
        out.append(f"{head} — {len(rows)}개 {basis}")
        for i, x in enumerate(rows, 1):
            part = x["part"] if not x["line"] or x["part"] == x["line"] else f"{x['line']} · {x['part']}"
            proj = " · ".join(v for v in (x["customer"], x["title"]) if v)
            out.append(f"\n{i}. {x['serial']} — {part}")
            out.append(f"   프로젝트: {proj}" + (f" ({x['deal_no']})" if x["deal_no"] else ""))
            out.append(f"   대여: {x['start']} ~ {x['end'] or '끝나는 날 미정'} · 대여 중" + (" · A/S 중" if x.get("as_now") else ""))
    else:
        out.append(f"{head}은 없습니다. {basis}")
    if data["undated"]:
        out.append(f"\n대여 시작일·출하일을 몰라 기간으로 거르지 못한 R 제품 {len(data['undated'])}개: "
                   + ", ".join(f"{x['serial']}({x['customer']})" for x in data["undated"][:20]))
    if data.get("waiting"):
        out.append(f"\n아직 출하 전인 R 제품(대여 전) {len(data['waiting'])}개 — 출하하면 위 목록에 나옵니다: "
                   + ", ".join(f"{x['serial']}({x['customer']})" for x in data["waiting"][:20]))
    if data["unregistered"]:
        out.append(f"\nS/N을 아직 등록하지 않은 대여 건 {len(data['unregistered'])}건 — [제품 대여 관리]에서 출하 기록 때 S/N을 등록하면 위 목록에 나옵니다:")
        for d in data["unregistered"][:20]:
            rent = d["rental"] or {}
            period = f"{rent.get('start')} ~ {rent.get('end')}" if rent.get("start") else f"{rent.get('months', '?')}개월 · 시작일 미정"
            out.append(f"- {d['customer']} · {d['title']} ({d['deal_no'] or '번호 없음'}) · {period} · {'수주' if d['won'] else '견적'}")
    return "\n".join(out)
