# -*- coding: utf-8 -*-
"""채팅에서 영업 데이터 바로 답하기(사용자 2026-10-28).

'이번 분기 수주 금액 얼마야?', '미입금 건 알려줘', '선진물류 입금 다 들어왔어?', 'Victor 담당 건 뭐 있어?' 같은 질문에
AI 가 '확인할 수 없습니다'라고 하거나 숫자를 지어내지 않게, /v1/generate 의 결정론 fast-path 로 영업 건 DB 를 읽어 답한다
(대여 목록·건 찾아 이동과 같은 방식). 영업부(또는 전체 권한)만 — 권한은 main 의 _sales_user 로 먼저 본다.

묻는 것:
- 전체: 분기 수주 금액·매출(kpi) · 미입금 건(unpaid) · 단계별 건 수(count) · 담당자별 건(owner) · 최근 발행번호(recent) · A/S 진행 중(as)
- 건 하나(고객사·건 이름·발행번호, 또는 앞 답의 그 건): 금액(amount) · 품목(items) · 출하(ship) · 입금(pay)
고치기·발급·이동·방법 질문은 여기서 답하지 않는다(건 찾아 이동 · AI 대화로).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import deal_goto as dg
from . import flow
from . import service as svc

MAX_LIST = 15

# 무엇을 알고 싶은 질문 — 이 말이 있어야 가로챈다
_ASK = re.compile(r"얼마|언제|몇|뭐|무엇|무슨|어떤|어느|있어|있나|있는지|있니|알려|보여|목록|리스트|현황|정리|"
                  r"들어왔|들어온|받았|받은|됐|되었|했어|했나|했는지|나갔|맞아|인가|\?")
# 고치기·이동·방법 — 여기서 답하지 않는다
_CHANGE = re.compile(r"수정|고치|고쳐|고칠|바꾸|바꿔|변경|재발행|발급해|발급하|발행해|발행하|작성|만들|등록|입력|열어|이동|들어가|"
                     r"하려면|방법|어떻게\s*(해|하)|절차|규칙|기준|어디서|어디에서|양식|추천|단가")
_KPI = re.compile(r"수주\s*(금액|액|실적|합계)|매출|분기\s*(실적|수주|금액)")
_UNPAID = re.compile(r"미입금|미수|입금\s*(안|못|전|대기|예정일\s*(이\s*)?지)|못\s*받|덜\s*받|안\s*들어|예정일\s*(이\s*)?지난")
_AS = re.compile(r"A\s*/\s*S|에이에스|수리")
_RECENT = re.compile(r"(최근|마지막|최신|가장\s*최근|방금).{0,12}(발행\s*번호|견적서|명세서|번호)")
_COUNT = re.compile(r"몇\s*(건|개)|건\s*수|단계별|현황")
_OWNER = re.compile(r"([A-Za-z가-힣]{2,12})\s*(?:님|씨)?\s*(?:이|가|께서)?\s*(?:담당|맡은|맡고)")
# 건 하나에 대해 묻는 것
_F_ITEMS = re.compile(r"품목|구성|내역|무슨\s*(제품|물건|장비)|뭐\s*(들어|넣)|들어간|들어갔")
_F_PAY = re.compile(r"입금|결제|잔금|선금|중도금|받았|들어왔|미수|완납|돈")
_F_SHIP = re.compile(r"출하|납품|배송|나갔|보냈|S\s*/\s*N|시리얼")
_F_AMOUNT = re.compile(r"금액|얼마|가격|총액|합계|공급가")
# 건 이름이 아닌 질문 말(deal_goto 의 이름 거르기에 더해)
_QWORD = re.compile(r"금액|얼마|가격|총액|합계|공급가|입금|결제|잔금|선금|중도금|받았|들어왔|들어온|완납|품목|구성|내역|들어간|들어갔|"
                    r"출하|납품|배송|나갔|했어|했나|언제|됐|되었|제품|물건|목록|현황|정리|알려|다\b|모두|전부|혹시", re.I)
_DONE_STAGES = {"완료 (출하·완납)", "드랍", "회수 완료", "출하 기록만 (이전)"}


def _deal_names(t: str) -> tuple[list[str], bool]:
    ns, lead = dg.names(t)
    return [n for n in ns if not _QWORD.search(n)], lead


def _prev_deal(messages: list[dict]) -> int | None:
    prev = str(messages[-2].get("content") or "") if len(messages) > 1 and messages[-2].get("role") == "assistant" else ""
    ids = {int(x) for x in re.findall(r"[?&]deal=(\d+)", prev)}
    return next(iter(ids)) if len(ids) == 1 else None


def detect(messages: list[dict]) -> dict | None:
    """마지막 사용자 말 → {kind, …} | None(AI·건 찾아 이동으로)."""
    if not messages or messages[-1].get("role") != "user":
        return None
    t = str(messages[-1].get("content") or "").strip()
    if not t or _CHANGE.search(t) or dg._NOT.search(t) or not _ASK.search(t):
        return None
    facet = ("items" if _F_ITEMS.search(t) else "pay" if _F_PAY.search(t) and not _UNPAID.search(t)
             else "ship" if _F_SHIP.search(t) and not _AS.search(t) else "amount" if _F_AMOUNT.search(t) and not _KPI.search(t)
             else None)
    owner = _OWNER.search(t)
    who = dg._strip_josa(owner[1]) if owner else ""
    if who and len(who) >= 2 and not dg._FILLER.search(who) and who not in ("누가", "누구", "제가", "내가"):
        return {"kind": "owner", "name": who}
    if facet:
        ns, lead = _deal_names(t)
        if ns:
            return {"kind": "deal", "facet": facet, "names": ns, "lead": lead}
        prev = _prev_deal(messages)
        if prev and len(t) <= 30:                    # '그 건 입금은?' — 앞 답의 그 건
            return {"kind": "deal", "facet": facet, "names": [], "lead": True, "deal_id": prev}
    if _UNPAID.search(t):
        return {"kind": "unpaid", "overdue_only": bool(re.search(r"예정일\s*(이\s*)?지", t))}
    if _KPI.search(t):
        return {"kind": "kpi", "text": t}
    if _AS.search(t) and re.search(r"진행|중|접수|있|몇|목록|현황", t):
        return {"kind": "as"}
    if _RECENT.search(t):
        return {"kind": "recent", "doc": "statement" if "명세서" in t else "quote"}
    if _COUNT.search(t) and re.search(r"건|영업|프로젝트|딜", t):
        return {"kind": "count", "biz": "rental" if re.search(r"대여|렌탈", t) else None}
    return None


# ── 답 만들기 ─────────────────────────────────────────────────────────────────


def _money(v: Any, cur: str = "KRW") -> str:
    if not isinstance(v, (int, float)):
        return "-"
    return f"${v:,.2f}" if cur == "USD" else f"{round(v):,}원"


def _cur(r: dict) -> str:
    return (r.get("quote") or {}).get("currency") or "KRW"


def _open(r: dict) -> str:
    return dg._btn("건 열기", f"tab={dg._deal_tab(r)}&deal={r['id']}")


def _line(r: dict) -> str:
    amt = flow.supply_total(r)
    return f"{dg._head(r)} — {r['stage']}" + (f" · {_money(amt, _cur(r))}" if amt else "")


def _quarter(t: str, today: dt.date) -> int | None:
    m = re.search(r"([1-4])\s*분기", t)
    if m:
        return int(m[1])
    if re.search(r"이번\s*분기|이\s*분기|현\s*분기|금\s*분기", t):
        return (today.month - 1) // 3 + 1
    if re.search(r"지난\s*분기|저번\s*분기|전\s*분기", t):
        q = (today.month - 1) // 3
        return q if q >= 1 else None
    return None


def _month(t: str, today: dt.date) -> int | None:
    """올해 몇 월을 물었는지 — '이번 달'에 올해 합계로 답하던 문제(검증 2026-10-29)."""
    m = re.search(r"(1[0-2]|[1-9])\s*월", t)
    if m:
        return int(m[1])
    if re.search(r"이번\s*달|이\s*달|이달|금월|당월", t):
        return today.month
    if re.search(r"지난\s*달|저번\s*달|전\s*달|전월", t):
        return today.month - 1 or None
    return None


def _kpi(data: dict, t: str, today: dt.date) -> str:
    k = data["kpi"]
    q = _quarter(t, today)
    mon = None if q else _month(t, today)
    basis = "(공급가액 · 수주일 기준 · 드랍 제외 · 부가세 별도)"
    out = []
    if "매출" in t and not re.search(r"수주", t):
        return (f"{k['year']}년 매출(출하일 기준)은 {_money(k['sales'])}입니다. (공급가액 · 드랍 제외 · 부가세 별도)\n"
                f"참고로 올해 수주 금액은 {_money(k['won_total'])}입니다 — 분기별: "
                + " · ".join(f"{i}분기 {_money(v)}" for i, v in enumerate(k["won_by_quarter"], 1)))
    if mon:
        won = [r for r in data["rows"] if r.get("won") and not r.get("dropped") and _in_month(r, k["year"], mon)]
        out.append(f"{k['year']}년 {mon}월 수주 금액은 {_money(sum(flow.supply_total(r) or 0 for r in won))}입니다. {basis}")
        if won:
            out.append(f"\n{mon}월 수주 건 {len(won)}건:")
            for i, r in enumerate(won[:MAX_LIST], 1):
                out.append(f"{i}. {_line(r)}  {_open(r)}")
        out.append(f"\n올해 수주 금액 합계: {_money(k['won_total'])}")
    elif q:
        out.append(f"{k['year']}년 {q}분기 수주 금액은 {_money(k['won_by_quarter'][q - 1])}입니다. {basis}")
        won = [r for r in data["rows"] if r.get("won") and not r.get("dropped") and _in_quarter(r, k["year"], q)]
        if won:
            out.append(f"\n{q}분기 수주 건 {len(won)}건:")
            for i, r in enumerate(won[:MAX_LIST], 1):
                out.append(f"{i}. {_line(r)}  {_open(r)}")
    else:
        out.append(f"{k['year']}년 수주 금액 합계는 {_money(k['won_total'])}입니다. {basis}")
        out.append("분기별: " + " · ".join(f"{i}분기 {_money(v)}" for i, v in enumerate(k["won_by_quarter"], 1)))
    out.append(f"올해 매출(출하일 기준): {_money(k['sales'])}")
    if k.get("won_estimated"):
        out.append(f"(수주일이 없는 예전 건 {k['won_estimated']}건은 첫 계산서·출하·견적일로 대신 셌습니다)")
    out.append("\n자세한 내용은 [제품 영업 건 관리] 탭 위의 '분기별 수주금액' 카드에서도 볼 수 있습니다.")
    return "\n".join(out)


def _in_month(r: dict, year: int, mon: int) -> bool:
    day, _ = svc._won_day(r)
    return bool(day and day.startswith(f"{year}-{mon:02d}"))


def _in_quarter(r: dict, year: int, q: int) -> bool:
    day, _ = svc._won_day(r)
    return bool(day and day.startswith(str(year)) and (int(day[5:7]) - 1) // 3 + 1 == q)


def _unpaid(rows: list[dict], overdue_only: bool) -> str:
    items = []
    for r in rows:
        if r.get("dropped") or not r.get("won") or r.get("paid_full"):
            continue
        fl = r.get("flow") or {}
        total, paid = fl.get("total") or 0, fl.get("paid") or 0
        if total <= 0 or total - paid <= flow.pay_tolerance(r):
            continue
        od = fl.get("overdue") or []
        if overdue_only and not od:
            continue
        items.append((r, total, paid, od))
    head = "입금 예정일이 지난 건" if overdue_only else "아직 다 받지 못한 건(수주 확정 · 완납 전)"
    if not items:
        return f"{head}이 없습니다. (공급가액 기준)"
    items.sort(key=lambda x: (not x[3], -(x[1] - x[2])))
    out = [f"{head} {len(items)}건입니다. (공급가액 기준 · 남은 금액 많은 순, 예정일 지난 건 먼저)"]
    for i, (r, total, paid, od) in enumerate(items[:MAX_LIST], 1):
        cur = _cur(r)
        out.append(f"\n{i}. {dg._head(r)} — {r['stage']}")
        out.append(f"   받음 {_money(paid, cur)} / 받을 {_money(total, cur)} · 남은 {_money(total - paid, cur)}")
        nxt = flow.next_term(r)
        if od:
            out.append("   ⏰ " + ", ".join(f"{t['label']} {_money(t['amount'], cur)} 예정일 {t['expected_date']} ({t['days']}일 지남)" for t in od))
        elif nxt:
            out.append(f"   다음: {nxt['label']} {_money(nxt['amount'], cur)}" + (f" · 예정일 {nxt['expected_date']}" if nxt.get("expected_date") else " · 예정일 미정"))
        elif not r.get("pay_case"):
            out.append("   결제 조건을 아직 정하지 않았습니다.")
        out.append(f"   {_open(r)}")
    if len(items) > MAX_LIST:
        out.append(f"\n… 외 {len(items) - MAX_LIST}건 — [수주 진행] 탭의 '입금 확인'에서 모두 볼 수 있습니다.")
    return "\n".join(out)


def _count(data: dict, biz: str | None) -> str:
    rows = [r for r in data["rows"] if not biz or r["biz"] == biz]
    active = [r for r in rows if r["stage"] not in _DONE_STAGES]
    by: dict[str, int] = {}
    for r in rows:
        by[r["stage"]] = by.get(r["stage"], 0) + 1
    what = "대여 건" if biz == "rental" else "영업 건"
    out = [f"진행 중인 {what}은 {len(active)}건입니다. (전체 {len(rows)}건 중 완료·드랍·회수 완료 제외)"]
    out.append("단계별: " + " · ".join(f"{s} {n}건" for s, n in by.items()))
    if not biz:
        rent = sum(1 for r in active if r["biz"] == "rental")
        out.append(f"(진행 중 {len(active)}건 = 판매 {len(active) - rent}건 + 대여 {rent}건)")
    for i, r in enumerate(active[:MAX_LIST], 1):
        out.append(f"{i}. {_line(r)}  {_open(r)}")
    if len(active) > MAX_LIST:
        out.append(f"… 외 {len(active) - MAX_LIST}건 — [제품 영업 건 관리] 탭 목록에서 모두 볼 수 있습니다.")
    return "\n".join(out)


async def _owner_names(db: AsyncSession, name: str) -> tuple[set[str], set[str]]:
    """담당자 이름(별명·영문 이름·실명) → (같은 사람으로 보는 이름들, 이니셜들). 건 번호 이니셜로 같은 사람을 묶는다."""
    key = name.strip().lower()
    inis = set((await db.execute(text(
        "SELECT initials FROM sales_initials WHERE name_key = :k "
        "UNION SELECT initials FROM users WHERE initials IS NOT NULL AND (lower(username) = :k OR lower(alias) = :k)"),
        {"k": key})).scalars().all()) - {None}
    names = {key}
    if inis:
        names |= set((await db.execute(text("SELECT name_key FROM sales_initials WHERE initials = ANY(:i)"),
                                       {"i": list(inis)})).scalars().all())
    return names, inis


async def _owner(db: AsyncSession, rows: list[dict], name: str) -> str:
    names, inis = await _owner_names(db, name)
    mine = [r for r in rows if str(r.get("owner") or "").strip().lower() in names]
    if not mine:
        return (f"'{name}' 담당으로 기록된 영업 건이 없습니다. 담당자 이름(예: 실명·영문 이름)을 다시 말씀해 주세요. "
                "[제품 영업 건 관리] 탭의 담당자 거르기로도 볼 수 있습니다.")
    active = [r for r in mine if r["stage"] not in _DONE_STAGES]
    done = len(mine) - len(active)
    who = name + (f"({'·'.join(sorted(inis))})" if inis else "")
    out = [f"{who} 담당 영업 건은 {len(mine)}건입니다 — 진행 중 {len(active)}건, 완료·드랍 {done}건."]
    for i, r in enumerate((active + [r for r in mine if r not in active])[:MAX_LIST], 1):
        out.append(f"{i}. {_line(r)}  {_open(r)}")
    if len(mine) > MAX_LIST:
        out.append(f"… 외 {len(mine) - MAX_LIST}건")
    return "\n".join(out)


def _recent(rows: list[dict], doc: str) -> str:
    acts = svc.QUOTE_ACTIONS if doc == "quote" else ("statement",)
    issued = [(h, r) for r in rows for h in r.get("history") or [] if h["action"] in acts and h.get("doc_no") and not h.get("doc_legacy")]
    what = "견적서" if doc == "quote" else "거래명세서"
    if not issued:
        return f"발행한 {what}가 아직 없습니다."
    issued.sort(key=lambda x: x[0]["created_at"] or "", reverse=True)
    seen: set[str] = set()
    issued = [x for x in issued if not (x[0]["doc_no"] in seen or seen.add(x[0]["doc_no"]))]
    h, r = issued[0]
    out = [f"가장 최근 {what} 발행번호는 {h['doc_no']}입니다. ({dg._head(r)} · {str(h['created_at'])[:16].replace('T', ' ')}"
           + (f" · {h['user_name']}" if h.get("user_name") else "") + f")  {_open(r)}"]
    if len(issued) > 1:
        out.append("\n그 전 발행:")
        for h2, r2 in issued[1:5]:
            out.append(f"- {h2['doc_no']} · {r2.get('customer') or ''} · {str(h2['created_at'])[:10]}")
    return "\n".join(out)


async def _as(db: AsyncSession) -> str:
    rs = (await db.execute(text(
        "SELECT c.serial, c.part_name, c.customer, c.status, c.received_on, c.in_warranty, c.symptom, c.deal_id "
        "FROM as_cases c WHERE c.status <> '완료' ORDER BY c.received_on DESC NULLS LAST, c.id DESC"))).mappings().all()
    if not rs:
        return "지금 진행 중이거나 접수된 A/S는 없습니다. (완료된 A/S는 [제품·A/S] 탭 'A/S 기록'에서 볼 수 있습니다)"
    active = sum(1 for r in rs if r["status"] == "진행 중")
    out = [f"완료되지 않은 A/S가 {len(rs)}건입니다 (진행 중 {active}건 · 그 밖 {len(rs) - active}건)."]
    for i, r in enumerate(rs[:MAX_LIST], 1):
        free = "무상" if r["in_warranty"] else "유상" if r["in_warranty"] is False else "무상 여부 미정"
        out.append(f"{i}. {r['serial'] or '(S/N 없음)'} — {r['part_name'] or ''} · {r['customer'] or ''} · {r['status']}"
                   + (f" · 접수 {r['received_on']}" if r["received_on"] else "") + f" · {free}")
        if r["symptom"]:
            out.append(f"   증상: {str(r['symptom']).splitlines()[0][:60]}")
    return "\n".join(out)


def _facet(r: dict, facet: str) -> list[str]:
    cur = _cur(r)
    q = r.get("quote") or {}
    if facet == "amount":
        amt = flow.supply_total(r)
        if not amt:
            return ["견적 금액이 아직 없습니다(견적서 발행 전)."]
        return [f"견적 금액(공급가액, 부가세 별도): {_money(amt, cur)}" + (f" ({q['no']})" if q.get("no") else ""),
                *( [f"부가세 포함 시: {_money(round(amt * 1.1), cur)}"] if cur == "KRW" else [])]
    if facet == "items":
        items = q.get("items") or []
        if not items:
            return ["견적서에 적힌 품목이 없습니다(견적서 발행 전이거나 예전 기록)."]
        out = [f"견적서 품목 {len(items)}줄" + (f" ({q['no']})" if q.get("no") else "") + ":"]
        for i, it in enumerate(items, 1):
            qty = it.get("qty_text") or (f"{it['qty']:g}" if isinstance(it.get("qty"), (int, float)) else "")
            name = " · ".join(x.strip().lstrip("-").strip() for x in str(it.get("name") or "").splitlines() if x.strip())
            out.append(f"{i}. {name}" + (f" × {qty}" if qty else "") + (f" — {_money(it['amount'], cur)}" if it.get("amount") else ""))
        out.append(f"합계(공급가액): {_money(flow.supply_total(r), cur)}")
        return out
    if facet == "ship":
        ships = r.get("shipments") or []
        if not ships:
            return ["아직 출하 기록이 없습니다." + ("" if r.get("won") else " (수주 확정 전)")]
        out = [f"출하 {len(ships)}회:"]
        for i, s in enumerate(ships, 1):
            lines = s.get("lines") or []
            what = ", ".join(f"{x.get('name')} × {x.get('qty_text') or format(x.get('qty') or 0, 'g')}" for x in lines[:4])
            out.append(f"{i}. {s.get('date') or '날짜 없음'}" + (f" — {what}" + (f" 외 {len(lines) - 4}" if len(lines) > 4 else "") if what else ""))
        return out
    # pay
    fl = r.get("flow") or {}
    if not r.get("won"):
        return ["아직 수주 확정 전이라 입금 기록이 없습니다."]
    total, paid = fl.get("total") or 0, fl.get("paid") or 0
    head = "완납입니다." if r.get("paid_full") or (total and total - paid <= flow.pay_tolerance(r)) else "아직 다 받지 못했습니다."
    out = [f"입금: {head} 받음 {_money(paid, cur)} / 받을 {_money(total, cur)} (공급가액 기준)"]
    terms = fl.get("terms") or []
    if not terms:
        out.append("결제 조건(선금·중도금·잔금)을 아직 정하지 않았습니다.")
    for t in terms:
        state = f"입금 확인 {t['paid_date'] or ''}".strip() if t["confirmed"] else (
            f"미입금 · 예정일 {t['expected_date']}" + (" (지남)" if any(o["index"] == t["index"] for o in fl.get("overdue") or []) else "")
            if t.get("expected_date") else "미입금 · 예정일 미정")
        out.append(f"- {t['label']} {t['pct']:g}% {_money(t['amount'], cur)} — {state}")
    return out


async def _deal(db: AsyncSession, rows: list[dict], intent: dict) -> str | None:
    found = [r for r in rows if r["id"] == intent["deal_id"]] if intent.get("deal_id") else dg.match(rows, intent["names"])
    said = "·".join(intent["names"])
    if not found:
        if not intent.get("lead"):
            return None
        return f"'{said}'(으)로 찾은 영업 건이 없습니다. 고객사·건 이름이나 발행번호(QT-…)로 다시 말씀해 주세요."
    out = []
    if len(found) > 1:
        out.append(f"'{said}'(으)로 {len(found)}건을 찾았습니다.")
    for i, r in enumerate(found[:5], 1):
        quotes = await svc.deal_quotes(db, r.get("proposal_id"), r["id"])
        prefix = f"\n{i}. " if len(found) > 1 else ""
        out.append(f"{prefix}{dg._head(r)}")
        out.append(f"지금 상태: {dg.status_line(r, quotes)}")
        out.extend(_facet(r, intent["facet"]))
        out.append(_open(r))
    if len(found) > 5:
        out.append(f"\n… 외 {len(found) - 5}건 — 더 자세히 말씀해 주시면 좁힙니다.")
    return "\n".join(out)


async def answer(db: AsyncSession, intent: dict, today: dt.date) -> str | None:
    """채팅 답. None = 건을 못 찾았고 이름도 확실하지 않음 — 다음 흐름(건 찾아 이동·AI)으로."""
    data = await svc.list_deals(db, today)
    rows = data["rows"]
    kind = intent["kind"]
    if kind == "kpi":
        return _kpi(data, intent["text"], today)
    if kind == "unpaid":
        return _unpaid(rows, intent["overdue_only"])
    if kind == "count":
        return _count(data, intent["biz"])
    if kind == "owner":
        return await _owner(db, rows, intent["name"])
    if kind == "recent":
        return _recent(rows, intent["doc"])
    if kind == "as":
        return await _as(db)
    return await _deal(db, rows, intent)
