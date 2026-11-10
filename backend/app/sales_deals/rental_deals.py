# -*- coding: utf-8 -*-
"""[대여 제품 목록] 탭 — 대여 건 단위(사용자 2026-11-10).

- 한 줄 = 대여 건 하나(유상·무상 모두). 줄을 열면 그 건의 제품(발행한 견적서 판의 품목 줄)과 S/N.
- 상태는 수주·출하 단계 없이 간단하게(flow.rental_phase): 견적(유상 수주 전) → 대여 예정 → 대여 중(시작일부터 자동)
  → 회수 중 → 회수 완료, 반납일이 지났는데 회수 완료 전이면 '반납일 지남'.
- 무상 대여 건은 영업 건 관리에 나오지 않고 여기서만 관리한다(매출·가격이 없는 건, service.list_deals).
- S/N 불러오기: 견적서 Remark 칸에 적은 'S/N: …'을 발행한 판 기록(product_quotes.history 의 줄)에서 읽어 그 품목 줄의
  S/N 칸(product_units)에 넣는다. 수량보다 많이 적혔거나 이미 등록된 S/N 처럼 짝짓기 어려운 것은 비워 두고 알린다
  — 빈 칸은 사람이 직접 적는다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import flow, units
from .service import COLS, DealError, _row, deal_quotes, view

# Remark 의 'S/N: A, B' · 'SN : A' · 'S/N A ~ U23'
_SN_LABEL = re.compile(r"S\s*/?\s*N\s*[:：.]?\s*", re.I)
# 'CT-BI3321-U1 ~ U23' · 'PL-BH2130-U1~PL-BH2130-U6' — 같은 머리글에 끝 번호만 다른 범위
_RANGE = re.compile(r"^(.*?)(\d+)\s*~\s*(\S*?)(\d+)$")
RANGE_MAX = 500                       # 범위 하나로 펼칠 최대 개수(잘못 적은 '1 ~ 99999' 막기)
_DIGIT = re.compile(r"\d")


def parse_serials(remark: str | None) -> list[str]:
    """Remark 글 → S/N 목록(적힌 순서, 같은 것은 한 번). 'S/N' 표시가 없으면 빈 목록 — 다른 메모를 S/N 으로 읽지 않게."""
    t = str(remark or "")
    m = _SN_LABEL.search(t)
    if not m:
        return []
    out: list[str] = []
    for piece in re.split(r"[,;\n]+", t[m.end():]):
        piece = _SN_LABEL.sub("", piece).strip()
        if not piece:
            continue
        rg = _RANGE.match(piece)
        if rg and (not rg[3] or rg[1].endswith(rg[3])) and 0 < int(rg[4]) - int(rg[2]) < RANGE_MAX:
            width = len(rg[2]) if rg[2].startswith("0") else 0      # 'U01 ~ U12' 의 자릿수
            out += [f"{rg[1]}{str(n).zfill(width)}" for n in range(int(rg[2]), int(rg[4]) + 1)]
            continue
        out += [x for x in piece.split() if _DIGIT.search(x)]
    return list(dict.fromkeys(out))


def _j(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


# ── 목록 ─────────────────────────────────────────────────────────────────────

async def rental_list(db: AsyncSession, today: dt.date) -> dict[str, Any]:
    """대여 건 목록 — 급한 것이 위(반납일 지남 → 대여 중·회수 중은 반납일 가까운 순 → 대여 예정 → 견적 → 회수 완료).
    줄 = {id, customer, contact, owner, title, no, free, phase, months, start, end, days_left, returned, return_started,
          products(품목 줄 수), qty(제품 수), sn(등록한 S/N 수), serials(검색용), no_start(대여 시작일 미정)}."""
    rs = (await db.execute(text(
        f"SELECT {COLS} FROM sales_deals WHERE quote->>'biz' = 'rental' AND COALESCE(dropped, false) = false "
        "AND kind <> 'shipment_only' ORDER BY created_at DESC, id DESC"))).mappings().all()
    ids = [r["id"] for r in rs]
    sn: dict[int, list[str]] = {}
    if ids:
        for u in (await db.execute(text(
                "SELECT deal_id, serial FROM product_units WHERE deal_id = ANY(:i) AND status <> :gone ORDER BY line_no, id"),
                {"i": ids, "gone": units.DELETED})).mappings().all():
            sn.setdefault(u["deal_id"], []).append(u["serial"])
    rows = []
    for r in rs:
        d = _row(r)
        q = d.get("quote") or {}
        rent = q.get("rental") or {}
        ret = rent.get("return") or {}
        phase = flow.rental_phase(d, today)
        lines = units.target_lines(q.get("items") or [])
        end = rent.get("end")
        rows.append({
            "id": d["id"], "customer": d.get("customer") or "", "contact": d.get("contact") or "", "owner": d.get("owner") or "",
            "title": d.get("title") or "", "no": q.get("no") or d.get("deal_no") or "", "free": flow.free(d), "phase": phase,
            # 국내·해외(사용자 2026-11-10) — 해외 견적서는 달러
            "overseas": (q.get("currency") or "KRW") == "USD",
            "months": rent.get("months"), "start": rent.get("start"), "end": end,
            "days_left": (dt.date.fromisoformat(end) - today).days if end and phase not in ("견적", "회수 완료") else None,
            "returned": ret.get("done"), "return_started": ret.get("started"),
            "products": len(lines), "qty": sum(ln["qty"] for ln in lines), "sn": len(sn.get(d["id"], [])),
            "serials": sn.get(d["id"], []),
            "no_start": phase not in ("견적", "회수 완료") and not rent.get("start"),
            "created_at": d.get("created_at")})
    rank = {"반납일 지남": 0, "대여 중": 1, "회수 중": 1, "대여 예정": 2, "견적": 3, "회수 완료": 4}

    def key(x: dict) -> tuple:
        g = rank[x["phase"]]
        if g in (0, 1):
            return g, x["end"] or "9999", x["customer"]
        if g == 4:
            return g, _neg(x["returned"] or "0000"), x["customer"]
        return g, "", x["customer"]

    rows.sort(key=key)
    counts = {p: sum(1 for x in rows if x["phase"] == p) for p in flow.RENTAL_PHASES}
    return {"today": today.isoformat(), "soon_days": SOON_DAYS, "rows": rows, "counts": counts,
            "free": sum(1 for x in rows if x["free"]), "paid": sum(1 for x in rows if not x["free"])}


async def rental_products(db: AsyncSession, today: dt.date) -> dict[str, Any]:
    """[대여 제품 목록] 탭 — 대여 건의 제품을 품목 줄마다 한 줄씩(사용자 2026-11-10, 건 단위에서 다시 제품 단위로).
    정렬은 건의 급한 순(rental_list) 그대로, 같은 건 안에서는 견적서 줄 순서.
    줄 = {deal_id, line_no, product, part, qty, sn(등록한 S/N 수), serials} + 건 정보(고객사·담당자·번호·유상/무상·국내/해외·상태·기간·반납일)."""
    deals = await rental_list(db, today)
    rows: list[dict[str, Any]] = []
    for dr in deals["rows"]:
        base = {k: dr[k] for k in ("customer", "contact", "owner", "title", "no", "free", "overseas", "phase", "months", "start", "end",
                                   "days_left", "returned", "return_started", "no_start")}
        d = await _deal(db, dr["id"])
        src = await _source(db, d)
        if src:
            lines = [{"line_no": ln["line_no"], "name": ln["name"], "part": ln["part"], "qty": ln["qty"],
                      "serials": [u["serial"] for u in ln["units"]]} for ln in (await units.issue_units(db, d["id"], *src))["lines"]]
        else:                                    # 발행한 견적서 판이 없는 예전 건 — 건 견적 품목으로(S/N 은 판에 묶이므로 없음)
            lines = [{"line_no": None, "name": ln["name"], "part": units.default_part(ln["name"]), "qty": ln["qty"], "serials": []}
                     for ln in units.target_lines((d.get("quote") or {}).get("items") or [])]
        for ln in lines:
            rows.append({**base, "deal_id": dr["id"], "line_no": ln["line_no"], "product": ln["name"].split("\n")[0],
                         "part": ln["part"], "qty": ln["qty"], "sn": min(len(ln["serials"]), ln["qty"]), "serials": ln["serials"]})
    counts = {p: sum(1 for x in rows if x["phase"] == p) for p in flow.RENTAL_PHASES}
    return {"today": today.isoformat(), "soon_days": SOON_DAYS, "rows": rows, "counts": counts,
            "free": sum(1 for x in rows if x["free"]), "paid": sum(1 for x in rows if not x["free"])}


SOON_DAYS = 30                       # 반납일이 이 안이면 '반납 임박'(주황)


def _neg(day: str) -> str:
    """날짜 내림차순 정렬용 — 숫자를 뒤집는다."""
    return "".join(chr(ord("9") - ord(c) + ord("0")) if c.isdigit() else c for c in day)


# ── 상세(제품·S/N) ───────────────────────────────────────────────────────────

async def _deal(db: AsyncSession, deal_id: int) -> dict:
    r = (await db.execute(text(f"SELECT {COLS} FROM sales_deals WHERE id = :i"), {"i": deal_id})).mappings().first()
    if not r:
        raise DealError("건을 찾을 수 없습니다.", 404)
    d = _row(r)
    if (d.get("quote") or {}).get("biz") != "rental":
        raise DealError("대여 건이 아닙니다.")
    return d


async def _source(db: AsyncSession, d: dict) -> tuple[int, int] | None:
    """제품 목록을 읽을 견적서 판 — 건의 견적(수주했으면 수주한 판, 아니면 가장 최근 발행)과 같은 번호의 판. 없으면 가장 최근 판."""
    won = await units.won_issue(db, d)
    if won:
        return won
    docs = [q for q in await deal_quotes(db, d.get("proposal_id"), d["id"]) if q["issues"]]
    if not docs:
        return None
    last = max(((q["id"], h) for q in docs for h in q["issues"]), key=lambda x: str(x[1].get("issued_at") or ""))
    return last[0], int(last[1]["revision"])


async def _raw_lines(db: AsyncSession, quote_id: int, revision: int) -> list[dict]:
    """판 기록의 줄(Remark 포함). 2026-11-10 전에 파일을 올려 낸 판은 줄에 Remark 가 없어 저장해 둔 그 판의 엑셀에서 읽어 붙인다
    (확인 창 품목 = 읽은 품목 줄 순서, NEGO 줄 제외)."""
    raw = (await db.execute(text("SELECT history FROM product_quotes WHERE id = :i"), {"i": quote_id})).scalar()
    h = next((x for x in _j(raw) or [] if x.get("revision") == revision), None) or {}
    lines = h.get("lines") or []
    key = h.get("file_key") or (h.get("upload") or {}).get("key")
    if not lines or any("remark" in ln for ln in lines) or not str(key or "").lower().endswith(".xlsx"):
        return lines
    from ..company_knowledge import product_recommend as pr

    parsed = pr._read_back(key, ".xlsx")
    if not parsed:
        return lines
    rems = [row.get("remark") or "" for row in parsed["rows"] if row["row"] in ("item", "option") and row.get("in_total") is not False]
    goods = [i for i, ln in enumerate(lines) if ln.get("name") != "NEGO"]
    if len(rems) != len(goods):
        return lines
    out = list(lines)
    for i, rem in zip(goods, rems):
        out[i] = {**out[i], "remark": rem}
    return out


async def rental_detail(db: AsyncSession, deal_id: int, today: dt.date) -> dict[str, Any]:
    """건 상세의 제품 목록 — 품목 줄마다 등록한 S/N 과 견적서 Remark 에서 읽은 S/N(hints)."""
    d = await _deal(db, deal_id)
    src = await _source(db, d)
    base = {"deal": view(d, today), "phase": flow.rental_phase(d, today), "started": flow.rental_started(d, today)}
    if not src:
        return {**base, "issue": None}
    iss = await units.issue_units(db, deal_id, *src)
    raw = await _raw_lines(db, *src)
    for ln in iss["lines"]:
        rem = (raw[ln["line_no"]] if ln["line_no"] < len(raw) else {}).get("remark")
        ln["hints"] = parse_serials(rem)
    iss["hint_total"] = sum(len(ln["hints"]) for ln in iss["lines"])
    return {**base, "issue": iss}


async def load_serials(db: AsyncSession, deal_id: int, user_id: int | None, today: dt.date) -> dict[str, Any]:
    """[S/N 불러오기] — Remark 의 S/N 을 품목 줄의 빈 칸에 넣는다. 이미 적은 칸은 그대로.
    비워 두는 경우(skipped): 줄의 S/N 이 수량보다 많음 · 같은 S/N 이 다른 줄에도 적힘 · 이미 다른 곳에 등록된 S/N."""
    det = await rental_detail(db, deal_id, today)
    iss = det["issue"]
    if not iss:
        raise DealError("발행한 견적서가 없어 S/N을 불러올 수 없습니다.")
    skipped: list[dict[str, str]] = []
    picked: dict[int, list[str]] = {}
    taken: set[str] = set()
    owner: dict[str, str] = {}                      # S/N → 처음 적힌 품목 줄 이름
    for ln in iss["lines"]:
        name = ln["name"].splitlines()[0][:40]
        hints = ln["hints"]
        if not hints:
            continue
        have = {u["serial"].lower() for u in ln["units"]}
        if len(hints) > ln["qty"]:
            skipped += [{"serial": s, "line": name, "why": f"수량({ln['qty']})보다 S/N이 많이 적혀 있음"} for s in hints if s.lower() not in have]
            continue
        room = ln["qty"] - len(ln["units"])
        out = []
        for s in hints:
            k = s.lower()
            if k in have:
                continue
            if k in taken:
                skipped.append({"serial": s, "line": name, "why": f"같은 S/N이 다른 줄({owner[k]})에도 적혀 있음"})
                continue
            taken.add(k)
            owner[k] = name
            if len(out) < room:
                out.append(s)
        if out:
            picked[ln["line_no"]] = out
    # 다른 건·다른 판에 이미 등록된 S/N(제조사 UND) — 겹치면 저장이 막히므로 미리 빼고 알린다
    want = [s for xs in picked.values() for s in xs]
    if want:
        dup = (await db.execute(text(
            "SELECT lower(u.serial) AS s, u.status, s.customer FROM product_units u LEFT JOIN sales_deals s ON s.id = u.deal_id "
            "WHERE NOT u.no_serial AND lower(u.maker) = 'und' AND lower(u.serial) = ANY(:w)"),
            {"w": [s.lower() for s in want]})).mappings().all()
        busy = {r["s"]: (r["customer"] or "고객사 없음") + (" · 지운 건" if r["status"] == units.DELETED else "") for r in dup}
        for no, xs in list(picked.items()):
            name = next(ln["name"] for ln in iss["lines"] if ln["line_no"] == no).splitlines()[0][:40]
            keep = []
            for s in xs:
                if s.lower() in busy:
                    skipped.append({"serial": s, "line": name, "why": f"이미 등록된 S/N({busy[s.lower()]})"})
                else:
                    keep.append(s)
            if keep:
                picked[no] = keep
            else:
                picked.pop(no)
    filled = sum(len(xs) for xs in picked.values())
    if filled:
        payload = []
        for ln in iss["lines"]:
            if ln["line_no"] not in picked:
                continue
            cur = [{k: u.get(k) for k in ("id", "part_name", "serial", "maker", "purpose", "note")} for u in ln["units"]]
            new = [{"part_name": ln["part"], "serial": s, "maker": "UND", "purpose": "R", "note": ""} for s in picked[ln["line_no"]]]
            payload.append({"line_no": ln["line_no"], "units": cur + new})
        await units.save_units(db, deal_id, user_id, iss["quote_id"], iss["revision"], payload, partial=True)
    return {"filled": filled, "skipped": skipped, "hint_total": iss["hint_total"],
            "detail": await rental_detail(db, deal_id, today)}
