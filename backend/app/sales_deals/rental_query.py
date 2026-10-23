"""대여 나간 제품 묻기(사용자 2026-10-22) — AI 대화에서 '10월 이후 대여 나간 제품 시리얼이랑 알려줘'.

AI 가 숫자·시리얼을 지어내지 않게 /v1/generate 의 결정론 fast-path 로 DB 를 바로 읽어 표로 답한다(자금·회의록 fast-path 와 같은 방식).
- 대상: 시리얼 상태 R(대여)인 제품 — 대여 견적서로 낸 건은 시리얼 등록 때 R 이 기본.
- 기준 날짜: 대여 시작일(건의 대여 조건) → 없으면 그 제품 출하일(무상 기간 시작) → 건의 첫 출하일. 날짜를 모르면 '날짜 미정'으로 따로 보인다.
- 시리얼이 아직 없는 대여 건(대여 견적서로 수주까지 했지만 등록 전)은 아래에 건만 따로 알린다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_RENT = re.compile(r"대여|렌탈|임대|rental", re.I)
_ASK = re.compile(r"나간|나가\s*있|나가있|중인|목록|리스트|알려|뭐가|뭐\s*있|어떤|나열|정리|보여|조회|현황|몇\s*(대|개|건)")
# 견적서를 쓰거나 고치는 말은 이 질문이 아니다(견적서 탭 대화)
_NOT = re.compile(r"견적서?\s*(작성|만들|써|고쳐|수정)|발행")


def detect(messages: list[dict]) -> bool:
    if not messages or messages[-1].get("role") != "user":
        return False
    t = str(messages[-1].get("content") or "")
    return bool(_RENT.search(t) and _ASK.search(t) and not _NOT.search(t))


def since_date(text_: str, today: dt.date) -> dt.date | None:
    """'2026년 10월 이후' · '10월 이후' · '10/1부터' · '올해' → 그날부터. 연도가 없고 그 달이 아직 안 왔으면 작년."""
    m = re.search(r"(20\d{2})\s*[년./-]\s*(\d{1,2})\s*(?:월\s*(?:(\d{1,2})\s*일)?|[./-]\s*(\d{1,2}))?", text_)
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3] or m[4] or 1))
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})\s*(?:월\s*(?:(\d{1,2})\s*일)?|/\s*(\d{1,2}))", text_)
    if m and 1 <= int(m[1]) <= 12:
        month, day = int(m[1]), int(m[2] or m[3] or 1)
        year = today.year if month <= today.month else today.year - 1
        try:
            return dt.date(year, month, day)
        except ValueError:
            return dt.date(year, month, 1)
    if "올해" in text_ or "금년" in text_:
        return dt.date(today.year, 1, 1)
    return None


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


async def rented_units(db: AsyncSession, since: dt.date | None) -> dict[str, Any]:
    """{rows: [{serial, part, line, customer, deal_no, title, start, end, status}], undated: [...], unregistered: [건]}"""
    rs = (await db.execute(text(
        "SELECT u.serial, u.part_name, u.line_name, u.status, u.warranty_start, u.deal_id, "
        "       d.customer, d.title, d.deal_no, d.quote, d.shipments "
        "FROM product_units u JOIN sales_deals d ON d.id = u.deal_id "
        # 회수를 마친 제품(status '회수')은 '나가 있는' 대여 제품이 아니다(사용자 2026-10-22)
        "WHERE u.purpose = 'R' AND u.status <> '회수' AND COALESCE(d.dropped, false) = false "
        "ORDER BY d.customer, u.deal_id, u.line_no, u.id"))).mappings().all()
    rows, undated = [], []
    for r in rs:
        q = _j(r["quote"]) or {}
        start = _start(dict(r), q, _j(r["shipments"]) or [])
        item = {"serial": r["serial"] or "(S/N 없음)", "part": r["part_name"] or "", "line": (r["line_name"] or "").split("\n")[0],
                "customer": r["customer"] or "", "deal_no": q.get("no") or r["deal_no"] or "", "title": r["title"] or "",
                "start": start, "end": ((q.get("rental") or {}).get("end")), "status": r["status"]}
        if start is None:
            undated.append(item)
        elif since is None or start >= since.isoformat():
            rows.append(item)
    rows.sort(key=lambda x: (x["start"], x["customer"]))
    # 대여 견적서로 낸 건인데 R 시리얼이 아직 없는 건
    # (회수를 마친 건·R 시리얼을 이미 등록한 건은 빼고)
    reg = set((await db.execute(text("SELECT DISTINCT deal_id FROM product_units WHERE purpose = 'R' AND deal_id IS NOT NULL"))).scalars().all())
    ds = (await db.execute(text(
        "SELECT id, customer, title, deal_no, quote, won FROM sales_deals "
        "WHERE quote->>'biz' = 'rental' AND COALESCE(dropped, false) = false "
        "AND quote->'rental'->'return'->>'done' IS NULL ORDER BY id"))).mappings().all()
    unregistered = [{"customer": d["customer"] or "", "deal_no": (_j(d["quote"]) or {}).get("no") or d["deal_no"] or "",
                     "title": d["title"] or "", "won": bool(d["won"]), "rental": (_j(d["quote"]) or {}).get("rental")}
                    for d in ds if d["id"] not in reg]
    return {"rows": rows, "undated": undated, "unregistered": unregistered}


def answer(data: dict[str, Any], since: dt.date | None) -> str:
    """채팅에 보일 답 — 채팅창은 표·굵은 글씨 문법을 그리지 않으므로 번호 목록의 일반 글로(사용자 2026-10-22 확인)."""
    head = f"{since:%Y-%m-%d} 이후 대여 나간 제품" if since else "대여(R) 상태로 등록된 제품"
    basis = "(S/N 상태 R · 대여 시작일 기준, 없으면 출하일)"
    out = []
    rows = data["rows"]
    if rows:
        out.append(f"{head} — {len(rows)}개 {basis}")
        for i, x in enumerate(rows, 1):
            part = x["part"] if not x["line"] or x["part"] == x["line"] else f"{x['line']} · {x['part']}"
            proj = " · ".join(v for v in (x["customer"], x["title"]) if v)
            out.append(f"\n{i}. {x['serial']} — {part}")
            out.append(f"   프로젝트: {proj}" + (f" ({x['deal_no']})" if x["deal_no"] else ""))
            out.append(f"   대여: {x['start']} ~ {x['end'] or '끝나는 날 미정'} · {x['status']}")
    else:
        out.append(f"{head}은 없습니다. {basis}")
    if data["undated"]:
        out.append(f"\n대여 시작일·출하일을 몰라 기간으로 거르지 못한 R 제품 {len(data['undated'])}개: "
                   + ", ".join(f"{x['serial']}({x['customer']})" for x in data["undated"][:20]))
    if data["unregistered"]:
        out.append(f"\nS/N을 아직 등록하지 않은 대여 건 {len(data['unregistered'])}건 — [제품 대여 관리]에서 출하 기록 때 S/N을 등록하면 위 목록에 나옵니다:")
        for d in data["unregistered"][:20]:
            rent = d["rental"] or {}
            period = f"{rent.get('start')} ~ {rent.get('end')}" if rent.get("start") else f"{rent.get('months', '?')}개월 · 시작일 미정"
            out.append(f"- {d['customer']} · {d['title']} ({d['deal_no'] or '번호 없음'}) · {period} · {'수주' if d['won'] else '견적'}")
    return "\n".join(out)
