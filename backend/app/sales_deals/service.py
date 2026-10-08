# -*- coding: utf-8 -*-
"""영업 건 DB 읽기·쓰기. 상태 변경은 모두 sales_deal_events 에 남긴다.

한 건의 계산서·입금·출하는 JSONB 배열로 둔다(건 하나를 통째로 보고 고치는 화면이라).
배열을 고칠 때는 SELECT … FOR UPDATE 로 잠가 동시에 두 사람이 고쳐도 기록이 사라지지 않게 한다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..company_knowledge.product_prices import clean_quote_file_name
from . import flow
from . import statement_xlsx as sx
from .legacy_import import PARTIAL_RATIO, VAT_RATE, parse_pay_terms
from .status import STAGES, derive

COLS = ("id, deal_no, meeting, kind, customer, contact, owner, title, base_date, quote, pay_terms, won, won_date, "
        "invoices, payments, paid_full, shipments, shipped_note, note, review_alerts, review_done, imported, "
        "legacy, proposal_id, po_files, statement, pay_case, version, cur_no, dropped, drop_reason, created_at, updated_at")
NEW_NO = re.compile(r"^[A-Z]{2,4}\d{8}[B-Z]?$")       # 새 건 번호(AL20261008 — 프로젝트마다 같을 수 있음, 예전 B·C 붙은 번호도 인정)
KST = ZoneInfo("Asia/Seoul")
# 히스토리 줄마다 남기는 그때 건 내용 — 되돌리기는 이 값으로 건을 되돌린다(사용자 2026-10-08)
STATE_KEYS = ("meeting", "kind", "customer", "contact", "owner", "title", "base_date", "quote", "pay_terms", "won", "won_date",
              "invoices", "payments", "paid_full", "shipments", "shipped_note", "note", "review_done", "po_files", "statement",
              "pay_case", "dropped", "drop_reason")
# 되돌릴 수 없는 줄 — 견적서 발행·재발행은 견적서 기록(product_quotes)과 같이 움직여야 해서
_NO_ROLLBACK = ("create", "quote", "quote_revision")
JSON_COLS = ("quote", "pay_terms", "invoices", "payments", "shipments", "review_alerts", "legacy", "po_files", "statement",
             "meeting")
ENTRY_KINDS = ("invoices", "payments", "shipments", "po_files")


class DealError(Exception):
    """사용자에게 그대로 보여줄 업무 오류(예: 입금 확인 전 출하). code 는 화면이 따로 처리할 경우(INITIALS_REQUIRED)."""

    def __init__(self, message: str, status: int = 400, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v


def _row(r: Any) -> dict:
    d = dict(r)
    for k in JSON_COLS:
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    for k in ("base_date", "won_date", "created_at", "updated_at"):
        d[k] = _iso(d[k])
    return d


def view(d: dict, today: dt.date) -> dict:
    """저장된 건 → 화면 행(단계·계산서 표시·알림 포함). 확인 완료한 건은 이관 확인 알림을 숨긴다."""
    v = derive(d, today)
    reviews = [] if d["review_done"] else d["review_alerts"]
    legacy = d.get("legacy") or {}
    po = [{k: f.get(k) for k in ("filename", "mime", "size", "uploaded_at", "uploaded_by", "compare")} for f in d.get("po_files") or []]
    ts = flow.terms(d)
    fl = {"steps": flow.steps(d), "pay_case": d.get("pay_case"), "terms": ts,
          "pay_case_label": " · ".join(f"{t['label']} {t['pct']:g}%" + (f"({t['when']})" if t.get("when") else "") for t in ts) or None,
          "total": flow.expected_total(d), "paid": flow.paid(d), "due": flow.next_due(d),
          "overdue": flow.overdue(d, today)}
    # 표시 번호 = 히스토리 마지막 번호(AL20261008-01 → -02 … 날짜가 바뀌면 AL20261010-05, 사용자 2026-10-08).
    # 엑셀에서 옮긴 건·예전 번호(S26-…)는 그대로. 번호는 견적서 첫 발행 때 부여 — 그 전(제품 추천 확정)은 None. 사내용이라 견적서에는 안 씀
    d = {**d, "display_no": d.get("cur_no") or d["deal_no"]}
    return {**d, "po_files": po, "flow": fl, "stage": v["stage"], "shipped": v["shipped"], "invoice_status": v["invoice_status"],
            "alerts": reviews + v["alerts"], "legacy_no": legacy.get("legacy_no")}


def kst_today() -> dt.date:
    return dt.datetime.now(KST).date()


def _hist_row(e: Any) -> dict:
    snap = e["snap"] if isinstance(e["snap"], dict) else json.loads(e["snap"] or "null") or {}
    detail = json.loads(e["detail"]) if isinstance(e["detail"], str) else e["detail"]
    return {"id": e["id"], "ver_no": e["ver_no"], "action": e["action"], "detail": detail, "created_at": _iso(e["created_at"]),
            "user_name": e["user_name"], "stage": snap.get("stage"), "quote_rev": snap.get("quote_rev"), "amount": snap.get("amount"),
            # 되돌리기: 지울 수 있는 줄(견적서 발행 줄 제외) · 돌아갈 수 있는 줄(그때 내용이 남아 있음)
            "removable": e["action"] not in _NO_ROLLBACK, "restorable": "state" in snap}


async def _history(db: AsyncSession, deal_id: int | None = None) -> dict[int, list[dict]]:
    """건(프로젝트)별 히스토리 — 번호가 붙은 이력만, 숨긴 줄 빼고, 최신 먼저(사용자 2026-10-08)."""
    rs = (await db.execute(text(
        "SELECT e.id, e.deal_id, e.action, e.detail, e.ver_no, e.snap, e.created_at, COALESCE(u.alias, u.username) AS user_name "
        "FROM sales_deal_events e LEFT JOIN users u ON u.id = e.user_id "
        "WHERE e.ver_no IS NOT NULL AND e.hidden_at IS NULL AND (CAST(:d AS BIGINT) IS NULL OR e.deal_id = :d) "
        "ORDER BY e.id DESC"), {"d": deal_id})).mappings().all()
    out: dict[int, list[dict]] = {}
    for e in rs:
        out.setdefault(e["deal_id"], []).append(_hist_row(e))
    return out


def _mark_updated(row: dict, hist: list[dict]) -> list[dict]:
    """견적서가 새 판으로 바뀌면 옛 판(견적 V1) 줄에 '버전 업데이트됨'(사용자 2026-10-08)."""
    cur = (row.get("quote") or {}).get("revision")
    return [{**h, "superseded": bool(cur and h["quote_rev"] and h["stage"] == "견적" and h["quote_rev"] < cur)} for h in hist]


async def list_deals(db: AsyncSession, today: dt.date) -> dict:
    rs = (await db.execute(text(
        f"SELECT {COLS} FROM sales_deals ORDER BY updated_at DESC, base_date DESC NULLS LAST, id DESC"))).mappings().all()
    hist = await _history(db)
    rows = []
    for r in rs:
        row = view(_row(r), today)
        rows.append({**row, "history": _mark_updated(row, hist.get(r["id"], []))})
    summary = {s: sum(1 for r in rows if r["stage"] == s) for s in STAGES}
    summary["확인 필요"] = sum(1 for r in rows if any(a["level"] == "review" for a in r["alerts"]))
    summary["알림"] = sum(1 for r in rows if any(a["level"] == "alert" for a in r["alerts"]))
    return {"today": today.isoformat(), "summary": summary, "rows": rows, "kpi": kpi(rows, today),
            "counts": {"total": len(rows), "imported": sum(1 for r in rows if r["imported"])}}


def _won_day(r: dict) -> tuple[str | None, bool]:
    """수주일 — 없으면(엑셀에서 옮긴 건) 첫 세금계산서 발행일 → 첫 출하일 → 견적일 순으로 대신 쓴다. (날짜, 대신 썼는지)."""
    if r.get("won_date"):
        return r["won_date"], False
    for dates in ([i.get("date") for i in r.get("invoices") or []], [s.get("date") for s in r.get("shipments") or []]):
        ds = sorted(x for x in dates if x)
        if ds:
            return ds[0], True
    return r.get("base_date"), True


def kpi(rows: list[dict], today: dt.date) -> dict:
    """올해 분기별 수주금액(수주일 기준)·올해 매출액(출하일 기준) — 공급가액(사용자 2026-10-08). 드랍 제외.
    수주일이 없는 옮긴 건은 _won_day 로 대신 셈하고 그 건수를 won_estimated 로 알린다(화면에 표시)."""
    y = str(today.year)
    quarters = [0.0, 0.0, 0.0, 0.0]
    sales = 0.0
    estimated = 0
    for r in rows:
        if r.get("dropped") or r.get("won") is False:
            continue
        amt = flow.supply_total(r) or 0
        day, guessed = _won_day(r) if r.get("won") else (None, False)
        if day and day.startswith(y):
            quarters[(int(day[5:7]) - 1) // 3] += amt
            estimated += guessed
        ship_dates = sorted(s.get("date") for s in r.get("shipments") or [] if s.get("date"))
        if ship_dates and ship_dates[0].startswith(y):
            sales += amt
    return {"year": today.year, "quarter": (today.month - 1) // 3 + 1, "won_by_quarter": [round(q) for q in quarters],
            "won_total": round(sum(quarters)), "sales": round(sales), "won_estimated": estimated}


async def get_deal(db: AsyncSession, deal_id: int, today: dt.date) -> dict:
    r = (await db.execute(text(f"SELECT {COLS} FROM sales_deals WHERE id = :i"), {"i": deal_id})).mappings().first()
    if not r:
        raise DealError("건을 찾을 수 없습니다.", 404)
    ev = (await db.execute(text(
        "SELECT e.action, e.detail, e.created_at, e.ver_no, COALESCE(u.alias, u.username) AS user_name "
        "FROM sales_deal_events e LEFT JOIN users u ON u.id = e.user_id "
        "WHERE e.deal_id = :i ORDER BY e.created_at DESC, e.id DESC LIMIT 100"), {"i": deal_id})).mappings().all()
    events = [{**dict(e), "created_at": _iso(e["created_at"]),
               "detail": json.loads(e["detail"]) if isinstance(e["detail"], str) else e["detail"]} for e in ev]
    # 견적서 발행 전(제품 추천 확정) 건에서 바로 [견적서 작성]을 열 수 있게 추천 기록 번호를 함께(사용자 2026-10-08)
    rec_id = (await db.execute(text("SELECT recommendation_id FROM product_proposals WHERE id = :p"),
                               {"p": r["proposal_id"]})).scalar() if r["proposal_id"] else None
    row = view(_row(r), today)
    return {**row, "events": events, "quotes": await deal_quotes(db, r["proposal_id"], r["id"]),
            "recommendation_id": rec_id, "history": _mark_updated(row, (await _history(db, r["id"])).get(r["id"], []))}


async def deal_quotes(db: AsyncSession, proposal_id: int | None, deal_id: int | None = None) -> list[dict]:
    """이 건(최종 제안 확정 기록)으로 만든 견적서 — 발행한 판 목록 포함(사용자 2026-10-07: 발행한 견적은 영업 건에서 관리).
    견적서 탭은 작성 중인 것만 보이고, 발행본 다시 받기·고치기는 영업 건 상세에서 한다."""
    # AI 추천 건은 추천 기록으로, 수기 견적 건은 product_quotes.deal_id 로 찾는다
    if not proposal_id and not deal_id:
        return []
    rs = (await db.execute(text(
        "SELECT q.id, q.status, q.quote_no, q.revision, q.history, q.updated_at, COALESCE(u.alias, u.username) AS writer "
        "FROM product_quotes q LEFT JOIN product_proposals p ON p.recommendation_id = q.recommendation_id "
        "LEFT JOIN users u ON u.id = q.user_id WHERE (CAST(:p AS BIGINT) IS NOT NULL AND p.id = :p) OR q.deal_id = :d "
        "ORDER BY q.id"), {"p": proposal_id, "d": deal_id})).mappings().all()
    out = []
    for q in rs:
        hist = json.loads(q["history"]) if isinstance(q["history"], str) else (q["history"] or [])
        out.append({"id": q["id"], "status": q["status"], "quote_no": q["quote_no"], "revision": q["revision"],
                    "writer": q["writer"], "updated_at": _iso(q["updated_at"]),
                    "issues": [{**{k: h.get(k) for k in ("revision", "issued_at", "total", "currency")},
                                # 예전 발행본 파일명('_Revn'·이니셜·날짜 중복)은 지금 이름 규칙으로(사용자 2026-10-08)
                                "file_name": clean_quote_file_name(str(h.get("file_name") or ""))} for h in hist]})
    return out


async def _lock(db: AsyncSession, deal_id: int) -> dict:
    r = (await db.execute(text(f"SELECT {COLS} FROM sales_deals WHERE id = :i FOR UPDATE"),
                          {"i": deal_id})).mappings().first()
    if not r:
        raise DealError("건을 찾을 수 없습니다.", 404)
    return _row(r)


def _ver_prefix(deal_no: str, day: dt.date) -> str:
    """히스토리 번호 앞부분 — 건 번호의 이니셜 + 바뀐 날짜(AL20261010). 프로젝트마다 따로 -01부터라 다른 건과 같아도 된다."""
    return f"{re.match(r'[A-Z]+', deal_no).group(0)}{day:%Y%m%d}"


async def _log(db: AsyncSession, deal_id: int, user_id: int | None, action: str, detail: dict) -> None:
    """변경 이력 한 줄. 번호가 있는 새 건은 바뀔 때마다 히스토리 번호를 하나씩(사용자 2026-10-08):
    견적서 첫 발행 AL20261008-01 → -02·-03 …, 날짜가 바뀌면 그 날짜로(AL20261010-05). 그때 단계·견적 판도 같이 남긴다.
    번호 없는 건(제품 추천 확정)·엑셀에서 옮긴 건(S26-…)은 이력만."""
    d = _row((await db.execute(text(f"SELECT {COLS} FROM sales_deals WHERE id = :i"), {"i": deal_id})).mappings().one())
    ver_no = snap = None
    if d["deal_no"] and NEW_NO.match(d["deal_no"]) and action not in ("import", "recommend_new"):
        seq = (d["version"] or 0) + 1 if d.get("cur_no") else 1
        day = kst_today()
        ver_no = f"{_ver_prefix(d['deal_no'], day)}-{seq:02d}"
        row = view(d, day)
        snap = {"stage": row["stage"], "quote_rev": (d.get("quote") or {}).get("revision"), "amount": row["flow"]["total"],
                "state": {k: d.get(k) for k in STATE_KEYS}}
        await db.execute(text("UPDATE sales_deals SET version = :v, cur_no = :n, updated_at = NOW() WHERE id = :d"),
                         {"v": seq, "n": ver_no, "d": deal_id})
    await db.execute(text("INSERT INTO sales_deal_events (deal_id, user_id, action, detail, ver_no, snap) "
                          "VALUES (:d, :u, :a, CAST(:x AS jsonb), :n, CAST(:s AS jsonb))"),
                     {"d": deal_id, "u": user_id, "a": action, "x": json.dumps(detail, ensure_ascii=False),
                      "n": ver_no, "s": json.dumps(snap, ensure_ascii=False) if snap else None})


async def rollback_history(db: AsyncSession, deal_id: int, keep_id: int, user_id: int | None) -> str:
    """히스토리 되돌리기(사용자 2026-10-08) — keep_id 줄보다 위(최신 쪽) 줄을 모두 지우고 건 내용을 그 줄 때로 되돌린다.
    번호도 그 줄 다음부터 다시(-04 까지 지우면 다음 변경은 -04). 견적서 발행·재발행 줄은 되돌리지 않는다.
    지운 이력·되돌리기 전 내용은 sales_deal_rollbacks 에 보관. 반환 = 돌아간 번호."""
    d = await _lock(db, deal_id)
    evs = (await db.execute(text("SELECT id, action, ver_no, snap FROM sales_deal_events WHERE deal_id = :d AND ver_no IS NOT NULL "
                                 "AND hidden_at IS NULL ORDER BY id DESC"), {"d": deal_id})).mappings().all()
    idx = next((i for i, e in enumerate(evs) if e["id"] == keep_id), None)
    if idx is None:
        raise DealError("돌아갈 히스토리가 없습니다.", 404)
    if idx == 0:
        raise DealError("되돌릴 히스토리를 골라 주세요(맨 위 줄부터).")
    if any(e["action"] in _NO_ROLLBACK for e in evs[:idx]):
        raise DealError("견적서 발행·재발행 줄은 되돌릴 수 없습니다 — 견적서는 견적서 탭에서 고쳐 다시 발행해 주세요.")
    keep = evs[idx]
    snap = keep["snap"] if isinstance(keep["snap"], dict) else json.loads(keep["snap"] or "{}")
    state = snap.get("state")
    if not state:
        raise DealError(f"{keep['ver_no']} 은(는) 되돌리기 기능을 만들기 전에 남은 기록이라 그때 내용으로 돌아갈 수 없습니다.")
    removed = (await db.execute(text("SELECT to_jsonb(e) FROM sales_deal_events e WHERE deal_id = :d AND id > :k ORDER BY id"),
                                {"d": deal_id, "k": keep_id})).scalars().all()
    await db.execute(text("INSERT INTO sales_deal_rollbacks (deal_id, to_ver_no, removed, before_state, user_id) "
                          "VALUES (:d, :n, CAST(:r AS jsonb), CAST(:b AS jsonb), :u)"),
                     {"d": deal_id, "n": keep["ver_no"], "u": user_id,
                      "r": json.dumps(removed, ensure_ascii=False, default=str),
                      "b": json.dumps({k: d.get(k) for k in STATE_KEYS}, ensure_ascii=False, default=str)})
    fields = {k: state.get(k) for k in STATE_KEYS if k in state}
    for k in ("base_date", "won_date"):
        if isinstance(fields.get(k), str):
            fields[k] = dt.date.fromisoformat(fields[k][:10])
    await _save(db, deal_id, **fields)
    await db.execute(text("DELETE FROM sales_deal_events WHERE deal_id = :d AND id > :k"), {"d": deal_id, "k": keep_id})
    await db.execute(text("UPDATE sales_deals SET version = :v, cur_no = :n WHERE id = :d"),
                     {"v": int(keep["ver_no"].rsplit("-", 1)[1]), "n": keep["ver_no"], "d": deal_id})
    await db.commit()
    return keep["ver_no"]


async def _save(db: AsyncSession, deal_id: int, **fields: Any) -> None:
    sets, params = [], {"i": deal_id}
    for k, v in fields.items():
        if k in JSON_COLS:
            sets.append(f"{k} = CAST(:{k} AS jsonb)")
            params[k] = json.dumps(v, ensure_ascii=False)
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v
    await db.execute(text(f"UPDATE sales_deals SET {', '.join(sets)}, updated_at = NOW() WHERE id = :i"), params)


async def initials_for(db: AsyncSession, owner: str | None) -> str | None:
    """담당자 이름 → 건 번호 이니셜(sales_initials). 없으면 None — 화면이 한 번 물어 저장한다."""
    key = str(owner or "").strip().lower()
    if not key:
        return None
    return (await db.execute(text("SELECT initials FROM sales_initials WHERE name_key = :k"), {"k": key})).scalar()


async def set_initials(db: AsyncSession, name: str, initials: str, *, overwrite: bool) -> None:
    """담당자 이니셜 저장(대문자 영문 2~4자). 이미 있는 이름을 바꾸는 것은 관리자만(overwrite)."""
    ini = str(initials or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,4}", ini):
        raise DealError("이니셜은 영문 2~4자로 적어 주세요(예: AL).")
    key = str(name or "").strip().lower()
    if not key:
        raise DealError("담당자 이름이 없습니다.")
    sql = ("INSERT INTO sales_initials (name_key, name, initials) VALUES (:k, :n, :i) ON CONFLICT (name_key) DO "
           + ("UPDATE SET initials = EXCLUDED.initials, updated_at = NOW()" if overwrite else "NOTHING"))
    await db.execute(text(sql), {"k": key, "n": name.strip(), "i": ini})
    await db.commit()


async def deal_no_for(db: AsyncSession, owner: str | None, day: dt.date, initials: str | None = None) -> str:
    """새 건 번호 — 담당 이니셜 + 날짜(AL20261008). 같은 날 같은 담당의 다른 프로젝트도 같은 번호(사용자 2026-10-08: B·C 안 붙임,
    프로젝트는 고객사·건명으로 구분, 히스토리는 프로젝트마다 -01부터). 이니셜을 모르면 DealError(INITIALS_REQUIRED)."""
    ini = (initials or "").strip().upper() or await initials_for(db, owner)
    if not ini:
        raise DealError(f"'{owner or '담당자'}'의 건 번호 이니셜을 먼저 정해 주세요(예: AL).", 409, code="INITIALS_REQUIRED")
    return f"{ini}{day:%Y%m%d}"


async def next_deal_no(db: AsyncSession, day: dt.date) -> str:
    """(예전) S{YY}-{순번} — 엑셀에서 옮긴 건 번호 체계. 새 건은 deal_no_for(사용자 2026-10-08)."""
    prefix = f"S{day:%y}-"
    # 지운 건 번호도 본다 — 지운 번호를 다시 쓰면 이미 발행한 견적서 번호와 겹친다
    last = (await db.execute(text("SELECT MAX(no) FROM (SELECT deal_no AS no FROM sales_deals WHERE deal_no LIKE :p "
                                  "UNION ALL SELECT deal_no FROM sales_deal_trash WHERE deal_no LIKE :p) x"),
                             {"p": prefix + "%"})).scalar()
    seq = int(last.split("-")[1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def _quote_from_items(items: list[dict], quote_date: dt.date, vat_included: bool, currency: str) -> dict:
    lines = []
    for it in items:
        amount = it["unit_price"] * it["qty"] if it.get("unit_price") is not None and it.get("qty") is not None else None
        lines.append({"name": it["name"], "unit_price": it.get("unit_price"), "qty": it.get("qty"),
                      "qty_text": None, "amount": amount})
    amounts = [x["amount"] for x in lines if x["amount"] is not None]
    return {"revision": 1, "date": quote_date.isoformat(), "sent_date": None,
            "amount": sum(amounts) if amounts else None, "vat_included": vat_included,
            "currency": currency, "items": lines}


async def create_deal(db: AsyncSession, user_id: int | None, p: dict) -> int:
    """새 견적 건. 품목 합계로 견적액, 결제조건 글로 결제 회차를 만든다."""
    new_id, _ = await insert_quote_deal(db, user_id, p)
    await db.commit()
    return new_id


async def insert_quote_deal(db: AsyncSession, user_id: int | None, p: dict) -> tuple[int, str]:
    """새 견적 건을 넣고 (id, 건 번호)를 돌려준다 — 커밋하지 않는다(견적서 발행처럼 다른 저장과 한 번에 커밋하려고).
    건 번호는 프로젝트마다 같을 수 있다(사용자 2026-10-08)."""
    quote_date: dt.date = p["quote_date"]
    if p.get("initials") and p.get("owner"):          # 견적서 담당 이니셜을 이 담당자 이니셜로 기억(처음 한 번)
        await db.execute(text("INSERT INTO sales_initials (name_key, name, initials) VALUES (:k, :n, :i) ON CONFLICT DO NOTHING"),
                         {"k": p["owner"].strip().lower(), "n": p["owner"].strip(), "i": p["initials"].strip().upper()})
    deal_no = await deal_no_for(db, p.get("owner"), quote_date, p.get("initials"))
    quote = _quote_from_items(p["items"], quote_date, p["vat_included"], p["currency"])
    first = (p["items"][0]["name"].split("\n")[0] if p["items"] else "")[:40]
    title = p.get("title") or (first + (f" 외 {len(p['items']) - 1}" if len(p["items"]) > 1 else ""))
    new_id = (await db.execute(text(
        "INSERT INTO sales_deals (deal_no, kind, customer, contact, owner, title, base_date, quote, pay_terms, note, "
        "created_by) VALUES (:no, 'quote', :c, :ct, :o, :t, :d, CAST(:q AS jsonb), CAST(:pt AS jsonb), :n, :u) "
        "RETURNING id"),
        {"no": deal_no, "c": p["customer"], "ct": p.get("contact"), "o": p.get("owner"), "t": title,
         "d": quote_date, "q": json.dumps(quote, ensure_ascii=False),
         "pt": json.dumps(parse_pay_terms(p.get("pay_terms_text") or ""), ensure_ascii=False),
         "n": p.get("note") or "", "u": user_id})).scalar_one()
    await _log(db, new_id, user_id, "create", {"deal_no": deal_no, "amount": quote["amount"]})
    return new_id, deal_no


async def _proposal_deal(db: AsyncSession, proposal_id: int) -> Any:
    return (await db.execute(text("SELECT id, deal_no, kind FROM sales_deals WHERE proposal_id = :p FOR UPDATE"),
                             {"p": proposal_id})).first()


async def recommend_deal(db: AsyncSession, user_id: int | None, proposal_id: int, p: dict) -> tuple[int, str | None]:
    """[최종 제안 확정] → 영업 건(단계 '제품 추천 확정'). 같은 확정을 다시 하면 그 건의 고객·건명만 고친다.
    커밋하지 않는다(확정 저장과 한 번에). p: {day, customer, owner, title}. 반환 (id, 건 번호)."""
    r = await _proposal_deal(db, proposal_id)
    if r is not None:
        if r.kind == "recommend":
            await _save(db, r.id, customer=p.get("customer") or None, title=p["title"])
        await _log(db, r.id, user_id, "recommend", {"title": p["title"]})
        return r.id, r.deal_no
    # 건 번호는 견적서 첫 발행 때(quote_deal) — 확정만 한 건은 번호 없이 '제품 추천 확정' 단계로 관리(사용자 2026-10-08)
    new_id = (await db.execute(text(
        "INSERT INTO sales_deals (deal_no, kind, customer, owner, title, base_date, proposal_id, created_by) "
        "VALUES (NULL, 'recommend', :c, :o, :t, :d, :p, :u) RETURNING id"),
        {"c": p.get("customer") or None, "o": p.get("owner"), "t": p["title"], "d": p["day"],
         "p": proposal_id, "u": user_id})).scalar_one()
    await _log(db, new_id, user_id, "recommend_new", {"title": p["title"]})
    return new_id, None


async def quote_deal(db: AsyncSession, user_id: int | None, proposal_id: int | None, p: dict,
                     revision: int, *, deal_id: int | None = None) -> tuple[int, str]:
    """견적서 발행 → 그 영업 건에 견적(품목·금액·판 번호)을 넣는다. '제품 추천 확정' 건이면 견적 단계로 바뀌고, 번호가 없으면
    이때 건 번호를 부여한다(사용자 2026-10-08). 건은 추천 기록(proposal_id) 또는 수기 견적이 이어 둔 건(deal_id)으로 찾고,
    없으면 새 견적 건. 커밋하지 않는다. 반환 (id, 건 번호) — 건 번호가 견적번호."""
    if deal_id:
        r = (await db.execute(text("SELECT id, deal_no, kind FROM sales_deals WHERE id = :i FOR UPDATE"), {"i": deal_id})).first()
    else:
        r = await _proposal_deal(db, proposal_id) if proposal_id else None
    if r is None:
        new_id, deal_no = await insert_quote_deal(db, user_id, p)
        if proposal_id:
            await _save(db, new_id, proposal_id=proposal_id)
        return new_id, deal_no
    quote = {**_quote_from_items(p["items"], p["quote_date"], p["vat_included"], p["currency"]), "revision": revision}
    first = (p["items"][0]["name"].split("\n")[0] if p["items"] else "")[:40]
    title = first + (f" 외 {len(p['items']) - 1}" if len(p["items"]) > 1 else "")
    # 견적서 판(첫 발행 1, 고쳐 다시 발행 2 …)은 quote.revision — 히스토리 번호(version·cur_no)는 _log 가 매긴다
    fields: dict[str, Any] = {"quote": quote, "customer": p["customer"], "contact": p.get("contact"), "title": title}
    if r.kind == "recommend":
        fields.update(kind="quote", base_date=p["quote_date"], owner=p.get("owner"))
    deal_no = r.deal_no
    if not deal_no:                                       # 제품 추천 확정만 한 건 — 첫 견적 발행 때 번호
        if p.get("initials") and p.get("owner"):
            await db.execute(text("INSERT INTO sales_initials (name_key, name, initials) VALUES (:k, :n, :i) ON CONFLICT DO NOTHING"),
                             {"k": p["owner"].strip().lower(), "n": p["owner"].strip(), "i": p["initials"].strip().upper()})
        deal_no = await deal_no_for(db, p.get("owner"), p["quote_date"], p.get("initials"))
        fields["deal_no"] = deal_no
    await _save(db, r.id, **fields)
    await _log(db, r.id, user_id, "quote" if r.kind == "recommend" else "quote_revision",
               {"revision": revision, "amount": quote["amount"], **({"deal_no": deal_no} if not r.deal_no else {})})
    return r.id, deal_no


async def set_won(db: AsyncSession, deal_id: int, user_id: int | None, won: bool | None, day: dt.date | None) -> None:
    d = await _lock(db, deal_id)
    if d["kind"] == "shipment_only":
        raise DealError("견적 없는 출하 건은 수주 상태가 없습니다.")
    await _save(db, deal_id, won=won, won_date=(day or dt.date.today()) if won else None)
    await _log(db, deal_id, user_id, "won", {"from": d["won"], "to": won, "date": _iso(day)})
    await db.commit()


def _expected_supply(d: dict) -> float | None:
    q = d.get("quote") or {}
    amount = q.get("amount")
    if not amount:
        return None
    return amount / (1 + VAT_RATE) if q.get("vat_included") else amount


async def add_invoice(db: AsyncSession, deal_id: int, user_id: int | None, p: dict) -> None:
    d = await _lock(db, deal_id)
    vat = p["vat"] if p.get("vat") is not None else round(p["supply"] * VAT_RATE)
    entry = {"date": p["date"].isoformat(), "supply": p["supply"], "vat": vat, "total": p["supply"] + vat,
             "issuer_note": p.get("issuer_note") or None, "partial": False}
    invoices = d["invoices"] + [entry]
    expected = _expected_supply(d)
    issued = sum(i.get("supply") or 0 for i in invoices)
    for i in invoices:  # 지금까지 발행한 공급가액이 견적보다 적으면 일부 청구
        i["partial"] = bool(expected and issued < expected * PARTIAL_RATIO)
    fields: dict[str, Any] = {"invoices": invoices}
    if not d["won"]:  # 계산서를 끊었다면 수주된 건이다
        fields.update(won=True, won_date=p["date"])
    await _save(db, deal_id, **fields)
    await _log(db, deal_id, user_id, "invoice", entry)
    await db.commit()


async def add_payment(db: AsyncSession, deal_id: int, user_id: int | None, p: dict) -> None:
    d = await _lock(db, deal_id)
    entry = {"date": p["date"].isoformat(), "amount": p["amount"], "note": p.get("note") or None}
    payments = d["payments"] + [entry]
    full = bool(p["full"])
    if d.get("pay_case"):                                  # 결제 방식을 고른 건은 받을 금액을 다 받으면 완납
        total = flow.expected_total(d)
        full = full or bool(total and sum(x.get("amount") or 0 for x in payments) >= total - flow.PAY_TOLERANCE)
    await _save(db, deal_id, payments=payments, paid_full=full)
    await _log(db, deal_id, user_id, "payment", {**entry, "full": full})
    await db.commit()


# ── 거래명세서 · 결제 방식(수주 뒤, 사용자 2026-10-07) ─────────────────────────

async def statement_buyer(db: AsyncSession, customer: str | None) -> dict | None:
    """같은 고객사의 마지막 거래명세서 공급받는자 정보(등록번호·상호·성명·주소) — 한 번 적으면 다음부터 채워 둔다."""
    from ..finance.vendor_registry import normalize_vendor

    key = normalize_vendor(customer)
    if not key:
        return None
    rs = (await db.execute(text("SELECT customer, statement FROM sales_deals WHERE statement IS NOT NULL "
                                "ORDER BY updated_at DESC LIMIT 500"))).all()
    for c, st in rs:
        st = json.loads(st) if isinstance(st, str) else st
        if normalize_vendor(c) == key or normalize_vendor((st.get("buyer") or {}).get("name")) == key:
            return st.get("buyer")
    return None


async def statement_draft(db: AsyncSession, deal_id: int) -> dict:
    """발급 창에 채워 둘 값 — 이미 발급했으면 그 내용, 아니면 견적 품목(품명·규격 나눔)·기억한 공급받는자·오늘 날짜."""
    d = _row((await db.execute(text(f"SELECT {COLS} FROM sales_deals WHERE id = :i"), {"i": deal_id})).mappings().first()
             or _missing())
    if d.get("statement"):
        return d["statement"]
    saved = await _order_state(db, deal_id)                # 수주 진행 탭에서 고친 초안이 있으면 그것
    if saved and saved.get("draft"):
        return saved["draft"]
    items = []
    for it in (d.get("quote") or {}).get("items") or []:
        name, spec = sx.split_name(it.get("name") or "")
        items.append({"name": name, "spec": spec, "qty": it.get("qty"), "unit_price": it.get("unit_price"), "note": ""})
    buyer = await statement_buyer(db, d.get("customer")) or {"reg_no": "", "name": d.get("customer") or "", "ceo": "", "address": ""}
    return {"no": d["deal_no"] or "", "date": dt.date.today().isoformat(), "buyer": buyer, "items": items}


def _missing():
    raise DealError("건을 찾을 수 없습니다.", 404)


def _st_item(it: dict) -> dict:
    return {"name": str(it.get("name") or "").strip()[:80], "spec": str(it.get("spec") or "").strip()[:80],
            "qty": it.get("qty"), "unit_price": it.get("unit_price"), "note": str(it.get("note") or "").strip()[:60]}


async def set_statement(db: AsyncSession, deal_id: int, user_id: int | None, p: dict) -> None:
    """거래명세서 발급(다시 하면 덮어씀). 빠진 값이 있으면 DealError. 합계는 코드가 다시 계산한다."""
    d = await _lock(db, deal_id)
    if not d.get("won"):
        raise DealError("수주 확인 뒤에 거래명세서를 발급합니다.")
    st = {"no": str(p.get("no") or d["deal_no"] or "").strip()[:40], "date": p["date"].isoformat(),
          "buyer": {k: str((p.get("buyer") or {}).get(k) or "").strip()[:200] for k in ("reg_no", "name", "ceo", "address")},
          "items": [_st_item(it) | ({"unit": u} if (u := sx.unit_of(it)) else {}) for it in p.get("items") or []]}
    parts = [_st_item(it) for it in p.get("parts") or [] if str(it.get("name") or "").strip()]
    if parts and len(st["items"]) == 1 and sx.unit_of(st["items"][0]):
        st["parts"] = parts                                 # 세트로 묶어 발급 — 원래 품목은 참고로
    try:
        sx.check(st)
    except sx.StatementError as e:
        raise DealError(str(e)) from e
    st["totals"] = sx.totals(st["items"])
    st["issued_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    await _save(db, deal_id, statement=st)
    await _log(db, deal_id, user_id, "statement", {"no": st["no"], "date": st["date"], "total": st["totals"]["total"],
                                                   "again": bool(d.get("statement"))})
    await db.commit()


async def statement_file(db: AsyncSession, deal_id: int) -> tuple[str, bytes]:
    """발급한 거래명세서 → (파일 이름(확장자 없음), 엑셀 바이트)."""
    r = (await db.execute(text("SELECT statement FROM sales_deals WHERE id = :i"), {"i": deal_id})).first()
    if r is None:
        raise DealError("건을 찾을 수 없습니다.", 404)
    st = json.loads(r[0]) if isinstance(r[0], str) else r[0]
    if not st:
        raise DealError("아직 거래명세서를 발급하지 않았습니다.", 404)
    return sx.file_name(st), sx.build_xlsx(st)


# ── 수주 진행 탭(AI 대화) 상태 — 초안·출하 정보·입금 후보·대화 ─────────────────────

async def _order_state(db: AsyncSession, deal_id: int) -> dict | None:
    r = (await db.execute(text("SELECT order_state FROM sales_deals WHERE id = :i"), {"i": deal_id})).first()
    if r is None:
        raise DealError("건을 찾을 수 없습니다.", 404)
    return json.loads(r[0]) if isinstance(r[0], str) else r[0]


async def save_order_state(db: AsyncSession, deal_id: int, state: dict) -> None:
    await db.execute(text("UPDATE sales_deals SET order_state = CAST(:s AS jsonb) WHERE id = :i"),
                     {"s": json.dumps(state, ensure_ascii=False), "i": deal_id})
    await db.commit()


async def order_open(db: AsyncSession, deal_id: int) -> dict:
    """수주 진행 화면 — {deal, state}. 처음 열면 견적 품목으로 명세서 초안을 만들고 AI 첫 안내를 남긴다. 수주 확인 전이면 DealError."""
    from . import order_agent

    deal = await get_deal(db, deal_id, dt.date.today())
    if not deal.get("won"):
        raise DealError("수주 확인 뒤에 진행합니다 — 영업 건에서 [수주 확정]을 먼저 눌러 주세요.")
    state = await _order_state(db, deal_id)
    if not state:
        state = order_agent.initial_state(deal, await statement_draft(db, deal_id))
        await save_order_state(db, deal_id, state)
    return {"deal": deal, "state": state}


async def order_note(db: AsyncSession, deal_id: int, done_text: str) -> dict:
    """버튼으로 확정한 뒤(발급·입금·출하·결제 방식) AI 대화에 '한 일 + 다음 할 일'을 남긴다."""
    from . import order_agent

    res = await order_open(db, deal_id)
    deal, state = res["deal"], res["state"]
    state["chat"] = [*(state.get("chat") or []),
                     {"role": "ai", "think": "", "steps": [], "answer": f"{done_text} {order_agent.next_hint(deal, state.get('draft'))}"}
                     ][-order_agent.CHAT_MAX:]
    await save_order_state(db, deal_id, state)
    return {"deal": deal, "state": state}


# 견적서 발행까지의 기록(제품 추천 확정·견적) — 견적 상태로 되돌릴 때 남기는 이력
_KEEP_EVENTS = ("import", "create", "recommend", "quote", "quote_revision")


async def reset_to_quote(db: AsyncSession, deal_id: int, user_id: int | None) -> list[str]:
    """(테스트용, 사용자 2026-10-07) 수주 확인 이후에 남긴 것을 모두 지우고 '견적서 발행 후 수주 대기'로 되돌린다.
    수주·발주서·거래명세서·결제 방식·입금·출하·계산서 기록·수주 진행 대화를 지우고, 그 이력도 지운 뒤 '되돌림' 한 줄을 남긴다.
    제품 추천 확정·견적서(발행본 포함)는 그대로. 엑셀에서 옮긴 건은 되돌리지 않는다. 반환 = 지울 저장소 파일(발주서) 키."""
    d = await _lock(db, deal_id)
    if d.get("imported"):
        raise DealError("엑셀에서 옮긴 건은 되돌리지 않습니다(테스트용 기능은 새로 만든 건만).")
    if d["kind"] not in ("quote", "recommend"):
        raise DealError("되돌릴 수 없는 건입니다.")
    keys = [f["key"] for f in d.get("po_files") or [] if f.get("key")]
    await db.execute(text(
        "UPDATE sales_deals SET won = NULL, won_date = NULL, pay_case = NULL, statement = NULL, order_state = NULL, "
        "pay_terms = '[]'::jsonb, payments = '[]'::jsonb, shipments = '[]'::jsonb, invoices = '[]'::jsonb, "
        "po_files = '[]'::jsonb, paid_full = FALSE, shipped_note = FALSE, dropped = FALSE, drop_reason = NULL, "
        "updated_at = NOW() WHERE id = :i"), {"i": deal_id})
    gone = (await db.execute(text("DELETE FROM sales_deal_events WHERE deal_id = :i AND NOT (action = ANY(:keep))"),
                             {"i": deal_id, "keep": list(_KEEP_EVENTS)})).rowcount
    await _log(db, deal_id, user_id, "reset_to_quote", {"removed_events": gone, "files": len(keys)})
    await db.commit()
    return keys


async def set_terms(db: AsyncSession, deal_id: int, user_id: int | None, raw: list[dict]) -> None:
    """결제 조건(선금·중도금·잔금 %·시점·예정 입금일) 저장 — 사용자 2026-10-08. 이미 확인한 회차의 % 는 바꾸지 않는다."""
    d = await _lock(db, deal_id)
    if not d.get("statement"):
        raise DealError("거래명세서를 먼저 발급해 주세요.")
    try:
        ts = flow.clean_terms(raw)
    except ValueError as e:
        raise DealError(str(e)) from e
    done = {p.get("term") for p in d["payments"] if p.get("term") is not None}
    old = d.get("pay_terms") or []
    for i in done:
        if i >= len(ts) or i >= len(old) or ts[i]["pct"] != old[i].get("pct") or ts[i]["label"] != old[i].get("label"):
            raise DealError("입금 확인한 회차의 구분·비율은 바꿀 수 없습니다(예정일만 바꿀 수 있음). 확인을 먼저 취소해 주세요.")
    await _save(db, deal_id, pay_case="terms", pay_terms=ts)
    await _log(db, deal_id, user_id, "pay_terms", {"terms": [f"{t['label']} {t['pct']:g}%" + (f" {t['when']}" if t["when"] else "") + (f" ~{t['expected_date']}" if t["expected_date"] else "")
                                                           for t in ts]})
    await db.commit()


async def confirm_term(db: AsyncSession, deal_id: int, user_id: int | None, index: int, day: dt.date) -> None:
    """회차 입금 확인 — 회사가 따로 확인한 뒤 버튼으로(사용자 2026-10-08). 금액 = 그 회차 공급가액. 모두 확인되면 완납."""
    d = await _lock(db, deal_id)
    ts = flow.terms(d)
    if not 0 <= index < len(ts):
        raise DealError("결제 조건 회차가 없습니다.", 404)
    t = ts[index]
    if t["confirmed"]:
        raise DealError("이미 입금 확인한 회차입니다.")
    entry = {"date": day.isoformat(), "amount": t["amount"], "note": f"{t['label']} {t['pct']:g}% 입금 확인", "term": index}
    payments = d["payments"] + [entry]
    full = all(x["confirmed"] or x["index"] == index for x in ts)
    await _save(db, deal_id, payments=payments, paid_full=full)
    await _log(db, deal_id, user_id, "payment", {**entry, "full": full})
    await db.commit()


async def unconfirm_term(db: AsyncSession, deal_id: int, user_id: int | None, index: int) -> None:
    """회차 입금 확인 취소(관리자) — 그 회차의 입금 기록을 지운다."""
    d = await _lock(db, deal_id)
    keep = [p for p in d["payments"] if p.get("term") != index]
    if len(keep) == len(d["payments"]):
        raise DealError("확인한 입금이 없는 회차입니다.", 404)
    await _save(db, deal_id, payments=keep, paid_full=False)
    await _log(db, deal_id, user_id, "remove_payment", {"term": index})
    await db.commit()


MEETING_FIELDS = ("customer", "contact", "meeting_date", "writer", "category", "robot", "requirements", "notes")


async def set_meeting(db: AsyncSession, deal_id: int, user_id: int | None, data: dict) -> None:
    """미팅 정보 직접 입력(AI 제품 추천 기록이 없는 건 — 수기 견적으로 시작한 그리퍼·AMR 등, 사용자 2026-10-08).
    고객사를 적었는데 건 고객사가 비어 있으면 건에도 넣는다."""
    d = await _lock(db, deal_id)
    meeting = {k: (str(data.get(k)).strip()[:2000] if data.get(k) not in (None, "") else None) for k in MEETING_FIELDS}
    meeting["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    fields: dict[str, Any] = {"meeting": meeting}
    if meeting["customer"] and not d.get("customer"):
        fields["customer"] = meeting["customer"][:200]
    await _save(db, deal_id, **fields)
    await _log(db, deal_id, user_id, "meeting", {"customer": meeting["customer"], "category": meeting["category"]})
    await db.commit()


async def set_dropped(db: AsyncSession, deal_id: int, user_id: int | None, dropped: bool, reason: str | None) -> None:
    """드랍(수주까지 못 간 건·진행을 접음, 사용자 2026-10-08 — 실주는 없애고 드랍 하나로) / 되살리기."""
    d = await _lock(db, deal_id)
    await _save(db, deal_id, dropped=dropped, drop_reason=(reason or "").strip()[:200] or None if dropped else None)
    await _log(db, deal_id, user_id, "drop" if dropped else "undrop", {"from": d.get("dropped"), "reason": reason})
    await db.commit()


def _ship_entry(d: dict, p: dict, forced: bool) -> dict:
    """출하 입력 → 출하 기록 한 줄(추가·수정 공용)."""
    tracking = [t for t in re.split(r"[,\s]+", p.get("tracking") or "") if t]
    # 제품별 출하 줄(사용자 2026-10-07: 제품마다 사진) — 예전 출하 시트처럼 품목·수량 글도 함께 남긴다
    lines = [{"name": ln["name"], "qty": ln.get("qty"), "qty_text": ln.get("qty_text") or None, "photos": list(ln.get("photos") or [])}
             for ln in p.get("lines") or [] if str(ln.get("name") or "").strip()]
    items_text = p.get("items") or "\n".join(
        f"{ln['name']} {ln['qty_text']}" if ln.get("qty_text") else f"{ln['name']} × {ln['qty']:g}" if ln.get("qty") else ln["name"]
        for ln in lines)
    entry = {"date": p["date"].isoformat(), "date_guessed": False, "date_text": None, "customer": d["customer"],
             "items": items_text, "qty_text": p.get("qty_text") or None, "unit_price": None,
             "amount": None, "carrier": p.get("carrier") or None, "tracking": tracking,
             "receiver": p.get("receiver") or None, "address": p.get("address") or None,
             "owner": p.get("owner") or d["owner"], "purpose": p.get("purpose") or "판매", "photos": [],
             "lines": lines, "linked_by_amount": True, "source": None, "forced": forced}
    return entry


async def update_shipment(db: AsyncSession, deal_id: int, user_id: int | None, index: int, p: dict) -> None:
    """이미 남긴 출하 기록 고치기(사용자 2026-10-07: 출하 뒤 다시 열면 방금 기록이 나와 고칠 수 있어야).
    입금 조건 검사는 처음 기록할 때만 — '예외 출하' 표시는 처음 값을 그대로 둔다."""
    d = await _lock(db, deal_id)
    if not 0 <= index < len(d["shipments"]):
        raise DealError("고칠 출하 기록이 없습니다.", 404)
    old = d["shipments"][index]
    entry = {**_ship_entry(d, p, bool(old.get("forced"))), "owner": old.get("owner") or d["owner"],
             "photos": old.get("photos") or [], "source": old.get("source")}
    shipments = list(d["shipments"])
    shipments[index] = entry
    await _save(db, deal_id, shipments=shipments)
    await _log(db, deal_id, user_id, "shipment_edit", {"index": index, **{k: entry[k] for k in ("date", "carrier", "tracking", "items")}})
    await db.commit()


async def add_shipment(db: AsyncSession, deal_id: int, user_id: int | None, p: dict) -> None:
    """출하 기록 — 입금 확인과 상관없이 언제든(사용자 2026-10-08: 입금 확인 전 출하 막기·예외 체크 없앰)."""
    d = await _lock(db, deal_id)
    entry = _ship_entry(d, p, False)
    fields: dict[str, Any] = {"shipments": d["shipments"] + [entry]}
    if d["kind"] == "quote" and not d["won"]:
        fields.update(won=True, won_date=p["date"])
    await _save(db, deal_id, **fields)
    await _log(db, deal_id, user_id, "shipment", {k: entry[k] for k in ("date", "carrier", "tracking", "items", "forced")})
    await db.commit()


async def add_po_file(db: AsyncSession, deal_id: int, user_id: int | None, entry: dict) -> None:
    """발주서 파일 기록 추가(파일 자체는 호출부가 저장소에 먼저 올린다). 수주 미확인이면 발주서를 받았으니 수주로 본다."""
    d = await _lock(db, deal_id)
    if d["kind"] == "shipment_only":
        raise DealError("견적 없는 출하 건에는 발주서를 붙이지 않습니다.")
    fields: dict[str, Any] = {"po_files": d["po_files"] + [entry]}
    if not d["won"]:
        fields.update(won=True, won_date=dt.date.today())
    await _save(db, deal_id, **fields)
    await _log(db, deal_id, user_id, "po_upload", {"filename": entry["filename"], "size": entry["size"]})
    await db.commit()


async def set_po_compare(db: AsyncSession, deal_id: int, user_id: int | None, index: int, key: str, result: dict) -> None:
    """발주서 품목 대조 결과 저장(po_compare.run). 대조하는 동안 그 발주서가 지워졌거나 바뀌었으면 저장하지 않는다."""
    d = await _lock(db, deal_id)
    files = d["po_files"]
    if not 0 <= index < len(files) or files[index].get("key") != key:
        raise DealError("대조하는 동안 발주서가 바뀌었습니다. 다시 시도해 주세요.", 409)
    files[index] = {**files[index], "compare": result}
    await _save(db, deal_id, po_files=files)
    await _log(db, deal_id, user_id, "po_compare", {"filename": files[index]["filename"], "summary": result.get("summary")})
    await db.commit()


async def get_po_file(db: AsyncSession, deal_id: int, index: int) -> dict:
    r = (await db.execute(text("SELECT po_files FROM sales_deals WHERE id = :i"), {"i": deal_id})).first()
    if not r:
        raise DealError("건을 찾을 수 없습니다.", 404)
    files = json.loads(r[0]) if isinstance(r[0], str) else r[0]
    if not 0 <= index < len(files):
        raise DealError("발주서 파일을 찾을 수 없습니다.", 404)
    return files[index]


async def remove_entry(db: AsyncSession, deal_id: int, user_id: int | None, kind: str, index: int) -> dict:
    """잘못 입력한 계산서·입금·출하·발주서 한 줄 삭제(이력에는 지운 내용을 남긴다). 지운 항목을 돌려준다.
    발주서 파일은 저장소에 남긴다 — 히스토리 되돌리기로 그 줄이 다시 살아날 수 있어서(사용자 2026-10-08)."""
    if kind not in ENTRY_KINDS:
        raise DealError("지울 수 없는 항목입니다.")
    d = await _lock(db, deal_id)
    items = d[kind]
    if not 0 <= index < len(items):
        raise DealError("이미 지워졌거나 없는 항목입니다.", 404)
    removed = items.pop(index)
    fields: dict[str, Any] = {kind: items}
    if kind == "payments" and not items:
        fields["paid_full"] = False
    await _save(db, deal_id, **fields)
    await _log(db, deal_id, user_id, f"remove_{kind[:-1]}", removed)
    await db.commit()
    return removed


async def set_paid_full(db: AsyncSession, deal_id: int, user_id: int | None, full: bool) -> None:
    d = await _lock(db, deal_id)
    await _save(db, deal_id, paid_full=full)
    await _log(db, deal_id, user_id, "paid_full", {"from": d["paid_full"], "to": full})
    await db.commit()


DELETE_MAX = 200


async def delete_deals(db: AsyncSession, ids: list[int], user_id: int | None) -> list[str]:
    """여러 건 삭제 — 건 전체와 변경 이력을 sales_deal_trash 에 그대로 옮긴 뒤 지운다(되살릴 수 있게).
    칸이 늘어나도 빠짐없이 옮기도록 행을 통째로(to_jsonb) 저장한다. 지운 건번호들을 돌려준다."""
    ids = sorted({i for i in ids if isinstance(i, int) and i > 0})
    if not ids:
        raise DealError("지울 건을 골라 주세요.")
    if len(ids) > DELETE_MAX:
        raise DealError(f"한 번에 {DELETE_MAX}건까지 지울 수 있습니다.")
    found = (await db.execute(text("SELECT id FROM sales_deals WHERE id = ANY(:ids) FOR UPDATE"), {"ids": ids})).scalars().all()
    if len(found) != len(ids):
        raise DealError("이미 지워졌거나 없는 건이 있습니다. 리스트를 새로 고친 뒤 다시 골라 주세요.", 409)
    await db.execute(text(
        "INSERT INTO sales_deal_trash (deal_id, deal_no, deal, events, deleted_by) "
        "SELECT d.id, d.deal_no, to_jsonb(d), "
        "       COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id) FROM sales_deal_events e WHERE e.deal_id = d.id), "
        "                '[]'::jsonb), :u "
        "FROM sales_deals d WHERE d.id = ANY(:ids)"), {"ids": ids, "u": user_id})
    nos = (await db.execute(text("DELETE FROM sales_deals WHERE id = ANY(:ids) RETURNING deal_no"),
                            {"ids": ids})).scalars().all()
    await db.commit()
    return sorted(nos)


async def set_review_done(db: AsyncSession, deal_id: int, user_id: int | None, done: bool) -> None:
    await _lock(db, deal_id)
    await _save(db, deal_id, review_done=done)
    await _log(db, deal_id, user_id, "review_done" if done else "review_reopen", {})
    await db.commit()


async def import_preview_rows(db: AsyncSession, rows: list[dict], sources: dict) -> int:
    """이관 미리보기 행(build_preview 결과) → sales_deals. 이미 이관한 적이 있으면 거부(두 번 들어가지 않게)."""
    # 옛 데이터 보관함(trash_bin)으로 옮긴 이관 건도 센다 — 옮긴 뒤 다시 들어오지 않게(사용자 2026-10-08)
    done = (await db.execute(text(
        "SELECT (SELECT COUNT(*) FROM sales_deals WHERE imported) + (SELECT COUNT(*) FROM trash_bin "
        "WHERE source_table = 'sales_deals' AND (data->>'imported')::boolean)"))).scalar()
    if done:
        raise DealError(f"이미 이관된 건이 {done}개 있습니다. 다시 넣지 않습니다.", 409)
    for r in reversed(rows):  # 오래된 건부터 넣어 같은 날짜면 나중 건(큰 id)이 위로 오게
        legacy = {"legacy_no": r["legacy_no"], "source": r["source"], "file": sources[r["source"]["file"]]}
        new_id = (await db.execute(text(
            "INSERT INTO sales_deals (deal_no, kind, customer, contact, owner, title, base_date, quote, pay_terms, won, "
            "invoices, payments, paid_full, shipments, shipped_note, note, review_alerts, imported, legacy) VALUES "
            "(:no, :k, :c, :ct, :o, :t, :d, CAST(:q AS jsonb), CAST(:pt AS jsonb), :w, CAST(:iv AS jsonb), "
            "CAST(:pm AS jsonb), :pf, CAST(:sh AS jsonb), :sn, :n, CAST(:ra AS jsonb), TRUE, CAST(:lg AS jsonb)) "
            "RETURNING id"),
            {"no": r["id"], "k": r["kind"], "c": r["customer"], "ct": r["contact"], "o": r["owner"], "t": r["title"],
             "d": dt.date.fromisoformat(r["base_date"]) if r["base_date"] else None,
             "q": json.dumps(r["quote"], ensure_ascii=False) if r["quote"] else None,
             "pt": json.dumps(r["pay_terms"], ensure_ascii=False), "w": r["won"],
             "iv": json.dumps(r["invoices"], ensure_ascii=False), "pm": json.dumps(r["payments"], ensure_ascii=False),
             "pf": r["paid_full"], "sh": json.dumps(r["shipments"], ensure_ascii=False), "sn": r["shipped_note"],
             "n": r["note"], "ra": json.dumps(r["review_alerts"], ensure_ascii=False),
             "lg": json.dumps(legacy, ensure_ascii=False)})).scalar_one()
        await _log(db, new_id, None, "import", {"legacy_no": r["legacy_no"], **legacy})
    await db.commit()
    return len(rows)
