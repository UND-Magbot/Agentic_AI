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
from . import doc_numbers as dn
from . import statement_xlsx as sx
from .legacy_import import PARTIAL_RATIO, VAT_RATE, parse_pay_terms
from .status import STAGES, derive

COLS = ("id, deal_no, meeting, kind, customer, contact, owner, title, base_date, quote, pay_terms, won, won_date, "
        "invoices, payments, paid_full, shipments, shipped_note, note, review_alerts, review_done, imported, "
        "legacy, proposal_id, po_files, statement, pay_case, version, cur_no, dropped, drop_reason, created_at, updated_at")
KST = ZoneInfo("Asia/Seoul")
# 히스토리에서 문서 발행 줄 — 발행번호를 보여 주고, 같은 문서가 다시 발행되면 예전 줄은 '버전 업데이트됨'(사용자 2026-10-12)
QUOTE_ACTIONS = ("create", "quote", "quote_revision", "quote_upload", "quote_replace")
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
    # 표시 번호(사용자 2026-10-12) = 최근 견적서 발행번호(QT-LU20261012-01), 거래명세서를 발행했으면 최근 명세서 번호도(statement_no).
    # 새 체계 전 건은 그때 견적서에 찍힌 번호 = 건 번호(AL20261008·S26-…). cur_no(AL20261012-02 …)는 예전 히스토리 번호라
    # 보이지 않는다(사용자 2026-10-13). 견적서 발행 전(제품 추천 확정)은 None.
    d = {**d, "display_no": (d.get("quote") or {}).get("no") or d["deal_no"],
         "statement_no": (d.get("statement") or {}).get("no") or None,
         # 판매 / 대여(사용자 2026-10-22) — 대여 견적서로 낸 건은 견적에 biz·rental(기간·월 납입금)
         "biz": (d.get("quote") or {}).get("biz") or "sale", "rental": (d.get("quote") or {}).get("rental")}
    return {**d, "po_files": po, "flow": fl, "stage": v["stage"], "shipped": v["shipped"], "invoice_status": v["invoice_status"],
            "alerts": reviews + v["alerts"], "legacy_no": legacy.get("legacy_no")}


def kst_today() -> dt.date:
    return dt.datetime.now(KST).date()


def _hist_row(e: Any) -> dict:
    snap = e["snap"] if isinstance(e["snap"], dict) else json.loads(e["snap"] or "null") or {}
    detail = json.loads(e["detail"]) if isinstance(e["detail"], str) else e["detail"]
    doc = detail.get("no") if e["action"] in (*QUOTE_ACTIONS, "statement") else None
    # 새 체계(10-13) 전 견적 줄은 번호가 detail 에 없다 — 그때 견적서에 찍힌 발행번호(printed)를 보인다(사용자 2026-10-13).
    # 예전 히스토리 번호(ver_no, AL20261008-01 …)는 발행번호가 아니라 보이지 않는다.
    legacy = not doc and bool(e["printed"])
    return {"id": e["id"], "action": e["action"], "detail": detail, "created_at": _iso(e["created_at"]),
            "user_name": e["user_name"], "stage": snap.get("stage"), "quote_rev": snap.get("quote_rev"), "amount": snap.get("amount"),
            "doc_no": e["printed"] if legacy else doc, "doc_legacy": legacy}


async def _history(db: AsyncSession, deal_id: int | None = None) -> dict[int, list[dict]]:
    """건(프로젝트)별 히스토리 — 시간순 순번 1, 2, 3 …(사용자 2026-10-12: 발행번호로 나열하지 않음), 숨긴 줄·엑셀 이관 줄 빼고,
    최신 먼저. 되돌리기는 없앴다(문제는 고쳐서 재발행) — 기록은 쌓이기만 한다."""
    # 예전 견적 줄(ver_no 가 있는 줄) ↔ 같은 순간(2분 안)에 남은 견적서 판 — 그 판의 견적서에 찍힌 번호
    rs = (await db.execute(text(
        "SELECT e.id, e.deal_id, e.action, e.detail, e.snap, e.created_at, COALESCE(u.alias, u.username) AS user_name, "
        "  CASE WHEN e.ver_no IS NOT NULL AND e.action = ANY(:qa) THEN ("
        "    SELECT regexp_replace(COALESCE(h->>'quote_no', h->'header'->>'quote_no'), '\\s*\\(Rev\\.\\d+\\)$', '') "
        "    FROM product_quotes q, jsonb_array_elements(q.history) h "
        "    WHERE q.deal_id = e.deal_id AND jsonb_typeof(q.history) = 'array' "
        "      AND abs(extract(epoch FROM (CAST(h->>'issued_at' AS timestamptz) - e.created_at))) <= 120 "
        "    ORDER BY abs(extract(epoch FROM (CAST(h->>'issued_at' AS timestamptz) - e.created_at))) LIMIT 1) END AS printed "
        "FROM sales_deal_events e LEFT JOIN users u ON u.id = e.user_id "
        "WHERE e.hidden_at IS NULL AND e.action <> 'import' AND (CAST(:d AS BIGINT) IS NULL OR e.deal_id = :d) "
        "ORDER BY e.id"), {"d": deal_id, "qa": list(QUOTE_ACTIONS)})).mappings().all()
    out: dict[int, list[dict]] = {}
    for e in rs:
        rows = out.setdefault(e["deal_id"], [])
        rows.append({**_hist_row(e), "seq": len(rows) + 1})
    return {k: v[::-1] for k, v in out.items()}


def _mark_updated(row: dict, hist: list[dict]) -> list[dict]:
    """같은 문서(견적서·거래명세서)가 다시 발행되면 예전 발행 줄에 '버전 업데이트됨'(사용자 2026-10-12). hist 는 최신 먼저."""
    seen: set[str] = set()
    out = []
    for h in hist:
        kind = "quote" if h["action"] in QUOTE_ACTIONS else "statement" if h["action"] == "statement" else None
        out.append({**h, "superseded": bool(kind and kind in seen)})
        if kind:
            seen.add(kind)
    return out


async def _as_counts(db: AsyncSession, deal_id: int | None = None) -> dict[int, dict]:
    """건별 A/S 건수 — 완료 아님(접수+진행 중)·진행 중·전체(사용자 2026-10-19). 단계와 따로 보인다(출하·완납 뒤에도 A/S 는 생김).
    as_active = [A/S 들어감 확인]을 누른 '진행 중'만(사용자 2026-10-22) — 리스트에서 그 줄을 연하게, 'A/S 진행 중' 칩·필터."""
    rs = (await db.execute(text(
        "SELECT deal_id, COUNT(*) FILTER (WHERE status <> '완료') AS open, COUNT(*) FILTER (WHERE status = '진행 중') AS active, "
        "COUNT(*) AS total FROM as_cases "
        "WHERE deal_id IS NOT NULL AND (CAST(:d AS BIGINT) IS NULL OR deal_id = :d) GROUP BY deal_id"), {"d": deal_id})).all()
    return {r.deal_id: {"as_open": r.open, "as_active": r.active, "as_total": r.total} for r in rs}


async def list_deals(db: AsyncSession, today: dt.date, biz: str | None = None) -> dict:
    """리스트·단계별 건수·KPI. biz = 'sale'(제품 판매 관리) · 'rental'(제품 대여 관리, 사용자 2026-10-22) · None(전체)."""
    rs = (await db.execute(text(
        # 새로 만든 건이 위 — 단계·내용이 바뀌어도 줄이 움직이지 않는다(사용자 2026-10-22, 10-08 '최근 변경이 위' 대체)
        f"SELECT {COLS} FROM sales_deals ORDER BY created_at DESC, id DESC"))).mappings().all()
    hist = await _history(db)
    asc = await _as_counts(db)
    rows = []
    for r in rs:
        row = view(_row(r), today)
        if biz and row["biz"] != biz:
            continue
        rows.append({**row, **asc.get(r["id"], {"as_open": 0, "as_active": 0, "as_total": 0}), "history": _mark_updated(row, hist.get(r["id"], []))})
    summary = {s: sum(1 for r in rows if r["stage"] == s) for s in STAGES}
    summary["확인 필요"] = sum(1 for r in rows if any(a["level"] == "review" for a in r["alerts"]))
    summary["A/S 진행 중"] = sum(1 for r in rows if r["as_active"])
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
        "SELECT e.action, e.detail, e.created_at, COALESCE(u.alias, u.username) AS user_name "
        "FROM sales_deal_events e LEFT JOIN users u ON u.id = e.user_id "
        "WHERE e.deal_id = :i ORDER BY e.created_at DESC, e.id DESC LIMIT 100"), {"i": deal_id})).mappings().all()
    events = [{**dict(e), "created_at": _iso(e["created_at"]),
               "detail": json.loads(e["detail"]) if isinstance(e["detail"], str) else e["detail"]} for e in ev]
    # 견적서 발행 전(제품 추천 확정) 건에서 바로 [견적서 작성]을 열 수 있게 추천 기록 번호를 함께(사용자 2026-10-08)
    rec_id = (await db.execute(text("SELECT recommendation_id FROM product_proposals WHERE id = :p"),
                               {"p": r["proposal_id"]})).scalar() if r["proposal_id"] else None
    row = view(_row(r), today)
    from . import units                                # 제품 시리얼(사용자 2026-10-19)
    from .approvals import deal_requests              # 입금 예정일 연장 요청(회차별 최근, 사용자 2026-10-13)

    return {**row, "events": events, "quotes": await deal_quotes(db, r["proposal_id"], r["id"]),
            "units": await units.won_summary(db, row), "unit_list": await units.deal_units(db, r["id"]),
            **(await _as_counts(db, r["id"])).get(r["id"], {"as_open": 0, "as_active": 0, "as_total": 0}),
            "due_requests": await deal_requests(db, r["id"]),
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
                    "issues": [{**{k: h.get(k) for k in ("revision", "quote_no", "issued_at", "total", "currency", "replaced_at")},
                                "issue_no": _issue_no(h),
                                "uploaded": bool(h.get("upload")),
                                # 예전 발행본 파일명('_Revn'·이니셜·날짜 중복)은 지금 이름 규칙으로(사용자 2026-10-08)
                                "file_name": clean_quote_file_name(str(h.get("file_name") or ""))} for h in hist]})
    return out


def _issue_no(h: dict) -> str | None:
    """판의 발행번호 — 새 체계 판은 quote_no(QT-…), 예전 판은 그때 견적서에 찍힌 번호(header, 사용자 2026-10-13).
    'VT20261008 (Rev.1)' 같은 예전 표기는 번호만."""
    no = h.get("quote_no") or (h.get("header") or {}).get("quote_no")
    return re.sub(r"\s*\(Rev\.\d+\)$", "", str(no)).strip() if no else None


async def _lock(db: AsyncSession, deal_id: int) -> dict:
    r = (await db.execute(text(f"SELECT {COLS} FROM sales_deals WHERE id = :i FOR UPDATE"),
                          {"i": deal_id})).mappings().first()
    if not r:
        raise DealError("건을 찾을 수 없습니다.", 404)
    return _row(r)


async def _log(db: AsyncSession, deal_id: int, user_id: int | None, action: str, detail: dict) -> None:
    """변경 이력 한 줄 — 그때 단계·견적서 판·공급가액도 같이 남긴다(히스토리 표시용). 히스토리 순번은 보여 줄 때 매긴다."""
    snap = None
    if action not in ("import",):
        d = _row((await db.execute(text(f"SELECT {COLS} FROM sales_deals WHERE id = :i"), {"i": deal_id})).mappings().one())
        row = view(d, kst_today())
        snap = {"stage": row["stage"], "quote_rev": (d.get("quote") or {}).get("revision"), "amount": row["flow"]["total"]}
    await db.execute(text("INSERT INTO sales_deal_events (deal_id, user_id, action, detail, snap) "
                          "VALUES (:d, :u, :a, CAST(:x AS jsonb), CAST(:s AS jsonb))"),
                     {"d": deal_id, "u": user_id, "a": action, "x": json.dumps(detail, ensure_ascii=False),
                      "s": json.dumps(snap, ensure_ascii=False) if snap else None})


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
    """담당자 이니셜 저장(대문자 영문 2자). 이미 있는 이름을 바꾸는 것은 관리자만(overwrite)."""
    ini = str(initials or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", ini):
        raise DealError("이니셜은 영문 2자로 적어 주세요(예: AL).")
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


def _quote_from_items(items: list[dict], quote_date: dt.date, vat_included: bool, currency: str,
                      doc_no: str | None = None) -> dict:
    lines = []
    for it in items:
        amount = it["unit_price"] * it["qty"] if it.get("unit_price") is not None and it.get("qty") is not None else None
        lines.append({"name": it["name"], "unit_price": it.get("unit_price"), "qty": it.get("qty"),
                      "qty_text": None, "amount": amount,
                      **({"commodity": str(it["commodity"])[:60]} if it.get("commodity") else {}),
                      **({"unit": str(it["unit"])[:8]} if it.get("unit") else {}),
                      **({"statement": bool(it["statement"])} if "statement" in it else {})})
    amounts = [x["amount"] for x in lines if x["amount"] is not None]
    return {"revision": 1, "date": quote_date.isoformat(), "sent_date": None,
            "amount": sum(amounts) if amounts else None, "vat_included": vat_included,
            "currency": currency, "items": lines, **({"no": doc_no} if doc_no else {})}


def _add_months(d: dt.date, months: int) -> dt.date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    last = (dt.date(y + m // 12, m % 12 + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(y, m, min(d.day, last))


def term_end(start: dt.date, months: int) -> dt.date:
    """월 단위 기간의 끝나는 날 — 민법 제160조(사용자 2026-10-27 '통상적인 조건으로'): 마지막 달의 시작일과 같은 날의 전날,
    그 달에 같은 날이 없으면 그 달 말일. 1/15 + 1개월 → 2/14, 1/31 + 1개월 → 2/28(전엔 2/27), 0개월 → 시작 전날.
    대여 끝나는 날·무상 종료일이 같이 쓴다."""
    to = _add_months(start, months)
    return to if to.day < start.day else to - dt.timedelta(days=1)


def _rental(p: dict, amount: float | None) -> dict:
    """대여 견적서로 낸 건(사용자 2026-10-22) → 견적에 붙일 {biz, rental}. 판매는 빈 dict(예전 건과 같은 모양).
    rental = {months, start, end, monthly} — 끝나는 날 = 시작일 + 개월 - 1일, 월 납입금 = 총액 ÷ 개월(천 원 올림)."""
    r = p.get("rental")
    if not r or not r.get("months"):
        return {}
    months = int(r["months"])
    start = r.get("start") or None                   # 견적서 발행은 'YYYY-MM-DD', 새 건 등록은 date
    if isinstance(start, str):
        start = dt.date.fromisoformat(start)
    end = term_end(start, months) if start else None
    monthly = -(-round(amount) // (months * 1000)) * 1000 if amount else None
    return {"biz": "rental", "rental": {"months": months, "start": _iso(start), "end": _iso(end), "monthly": monthly}}


async def set_rental_start(db: AsyncSession, deal_id: int, user_id: int | None, day: dt.date) -> None:
    """대여 시작일 정하기(사용자 2026-10-22 — 출하일과 따로 담당자가 정한다) → 끝나는 날을 다시 계산.
    끝나는 날·'회수 필요' 알림·AI '대여 나간 제품'이 이 날짜를 기준으로 한다."""
    if not await put_rental_start(db, deal_id, user_id, day):
        raise DealError("대여 건이 아닙니다.")
    await db.commit()


async def put_rental_start(db: AsyncSession, deal_id: int, user_id: int | None, day: dt.date | None) -> bool:
    """대여 시작일(None = 미정)을 넣고 끝나는 날을 다시 계산 — 바뀌었을 때만 이력. 대여 건이 아니면 False. 커밋하지 않는다."""
    d = await _lock(db, deal_id)
    q = dict(d.get("quote") or {})
    if q.get("biz") != "rental" or not q.get("rental"):
        return False
    rental = dict(q["rental"])
    before = rental.get("start")
    rental["start"] = day.isoformat() if day else None
    rental["end"] = term_end(day, int(rental["months"])).isoformat() if day else None
    if rental["start"] != before:
        q["rental"] = rental
        await _save(db, deal_id, quote=q)
        await _log(db, deal_id, user_id, "rental_start", {"from": before, "to": rental["start"], "end": rental["end"]})
    return True


async def set_rental_return(db: AsyncSession, deal_id: int, user_id: int | None, step: str, day: dt.date | None,
                            *, admin: bool = False) -> None:
    """대여 건 회수(사용자 2026-10-22) — start: 회수 시작(대여 중 → 회수 중), done: 회수 완료(시리얼 출하 상태 → '회수'),
    cancel: 회수 기록 지우기(관리자). 날짜는 견적의 대여 조건(rental.return)에 남긴다 — 수주한 견적은 새 발행에도 그대로라 함께 지켜진다."""
    d = await _lock(db, deal_id)
    q = dict(d.get("quote") or {})
    if q.get("biz") != "rental" or not q.get("rental"):
        raise DealError("대여 건이 아닙니다.")
    shipped = bool(d["shipments"]) or bool(d.get("shipped_note"))
    rental = dict(q["rental"])
    ret = dict(rental.get("return") or {})
    today = day or kst_today()
    if step == "start":
        if not shipped:
            raise DealError("출하(대여 시작) 기록이 있어야 회수를 시작할 수 있습니다.")
        if ret.get("started"):
            raise DealError("이미 회수를 시작했습니다.")
        ret["started"] = today.isoformat()
    elif step == "done":
        if not ret.get("started"):
            if not shipped:
                raise DealError("출하(대여 시작) 기록이 있어야 회수를 마칠 수 있습니다.")
            ret["started"] = today.isoformat()
        if ret.get("done"):
            raise DealError("이미 회수를 마쳤습니다.")
        ret["done"] = today.isoformat()
        await db.execute(text("UPDATE product_units SET status = '회수', updated_at = NOW() WHERE deal_id = :d AND status = '출하'"),
                         {"d": deal_id})
    elif step == "cancel":
        if not admin:
            raise DealError("회수 기록 취소는 관리자만 할 수 있습니다.", 403)
        if ret.get("done"):
            await db.execute(text("UPDATE product_units SET status = '출하', updated_at = NOW() WHERE deal_id = :d AND status = '회수'"),
                             {"d": deal_id})
        ret = {}
    else:
        raise DealError("알 수 없는 회수 단계입니다.")
    rental["return"] = ret
    q["rental"] = rental
    await _save(db, deal_id, quote=q)
    await _log(db, deal_id, user_id, "rental_return", {"step": step, "date": today.isoformat()})
    await db.commit()


async def create_deal(db: AsyncSession, user_id: int | None, p: dict) -> int:
    """새 견적 건. 품목 합계로 견적액, 결제조건 글로 결제 회차를 만든다."""
    new_id, _ = await insert_quote_deal(db, user_id, p)
    await db.commit()
    return new_id


async def insert_quote_deal(db: AsyncSession, user_id: int | None, p: dict) -> tuple[int, str]:
    """새 견적 건을 넣고 (id, 건 번호)를 돌려준다 — 커밋하지 않는다(견적서 발행처럼 다른 저장과 한 번에 커밋하려고).
    견적서 발행으로 만드는 건은 건 번호 = 첫 견적서 발행번호(p["doc_no"], QT-…). 직접 등록한 건은 예전처럼 이니셜+날짜."""
    quote_date: dt.date = p["quote_date"]
    if p.get("initials") and p.get("owner"):          # 견적서 담당 이니셜을 이 담당자 이니셜로 기억(처음 한 번)
        await db.execute(text("INSERT INTO sales_initials (name_key, name, initials) VALUES (:k, :n, :i) ON CONFLICT DO NOTHING"),
                         {"k": p["owner"].strip().lower(), "n": p["owner"].strip(), "i": p["initials"].strip().upper()})
    deal_no = p.get("doc_no") or await deal_no_for(db, p.get("owner"), quote_date, p.get("initials"))
    quote = _quote_from_items(p["items"], quote_date, p["vat_included"], p["currency"], p.get("doc_no"))
    quote.update(_rental(p, quote["amount"]))
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
    await _log(db, new_id, user_id, "create", {"deal_no": deal_no, "amount": quote["amount"],
                                               **({"no": p["doc_no"]} if p.get("doc_no") else {})})
    return new_id, deal_no


async def _proposal_deal(db: AsyncSession, proposal_id: int) -> Any:
    return (await db.execute(text("SELECT id, deal_no, kind, won, paid_full FROM sales_deals WHERE proposal_id = :p FOR UPDATE"),
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
                     revision: int, *, deal_id: int | None = None, action: str | None = None) -> tuple[int, str]:
    """견적서 발행 → 그 영업 건에 견적(품목·금액·판·발행번호)을 넣는다. '제품 추천 확정' 건이면 견적 단계로 바뀌고, 번호가 없으면
    첫 견적서 발행번호가 건 번호. 발행번호(p["doc_no"], QT-LU20261012-01)는 발행마다 새로(사용자 2026-10-12). 건은 추천 기록
    (proposal_id) 또는 수기 견적이 이어 둔 건(deal_id)으로 찾고, 없으면 새 견적 건. 커밋하지 않는다. 반환 (id, 건 번호)."""
    if deal_id:
        r = (await db.execute(text("SELECT id, deal_no, kind, won, paid_full FROM sales_deals WHERE id = :i FOR UPDATE"),
                              {"i": deal_id})).first()
    else:
        r = await _proposal_deal(db, proposal_id) if proposal_id else None
    if r is None:
        new_id, deal_no = await insert_quote_deal(db, user_id, p)
        if proposal_id:
            await _save(db, new_id, proposal_id=proposal_id)
        return new_id, deal_no
    quote = {**_quote_from_items(p["items"], p["quote_date"], p["vat_included"], p["currency"], p.get("doc_no")),
             "revision": revision}
    # 대여 시작일은 건에서 담당자가 정한 값이 먼저(사용자 2026-10-27) — 재발행이 지우거나 견적서의 옛 날짜로 되돌리지 않게
    kept = (await db.execute(text("SELECT quote->'rental'->>'start' FROM sales_deals WHERE id = :i"), {"i": r.id})).scalar()
    quote.update(_rental({**p, "rental": {**p["rental"], "start": kept}} if kept and p.get("rental") else p, quote["amount"]))
    first = (p["items"][0]["name"].split("\n")[0] if p["items"] else "")[:40]
    title = first + (f" 외 {len(p['items']) - 1}" if len(p["items"]) > 1 else "")
    # 견적서 판(첫 발행 1, 고쳐 다시 발행 2 …)은 quote.revision(사내용), 문서 번호는 quote.no
    fields: dict[str, Any] = {"quote": quote, "customer": p["customer"], "contact": p.get("contact"), "title": title}
    if r.won or r.paid_full:   # 완납한 건도 견적은 그대로(사용자 2026-10-26). 수주한 건 — 수주한 견적서(품목·금액·번호)는 그대로, 새 발행은 견적서 목록·이력에만(사용자 2026-10-22)
        fields = {}
    if r.kind == "recommend":
        fields.update(kind="quote", base_date=p["quote_date"], owner=p.get("owner"))
    deal_no = r.deal_no
    if not deal_no:                                       # 제품 추천 확정만 한 건 — 첫 견적서 발행번호가 건 번호
        deal_no = p.get("doc_no") or await deal_no_for(db, p.get("owner"), p["quote_date"], p.get("initials"))
        fields["deal_no"] = deal_no
    if fields:
        await _save(db, r.id, **fields)
    await _log(db, r.id, user_id, action or ("quote" if r.kind == "recommend" else "quote_revision"),
               {"revision": revision, "amount": quote["amount"], **({"no": p["doc_no"]} if p.get("doc_no") else {}),
                **({"file": p["file"]} if p.get("file") else {}),
                **({"deal_no": deal_no} if not r.deal_no else {})})
    return r.id, deal_no


PAID_QUOTE_LOCKED = "완납한 건은 견적(품목·금액)을 바꿀 수 없습니다. 바꿔야 하면 관리자가 완납을 먼저 풀어 주세요."


async def quote_replaced(db: AsyncSession, deal_id: int, user_id: int | None, p: dict, *, latest: bool) -> None:
    """견적서 파일 교체(사용자 2026-10-13) — 같은 발행번호의 판을 밖에서 고친 파일로 바꿨다. 번호·판·견적일은 그대로.
    가장 최근 판이면 건 견적의 품목·금액도 그 파일 기준으로(예전 판이면 건의 현재 견적은 그대로). 이력 'quote_replace'.
    p = {doc_no, revision(사내 판 1·2 …), items, total, currency, file, rental?{months, start}}. 커밋하지 않는다."""
    d = await _lock(db, deal_id)
    q = d.get("quote") or {}
    # 건의 현재 견적(수주한 건이면 수주한 판)과 같은 번호를 바꿨을 때만 품목·금액을 따라간다 — 번호 없는 예전 건은 최근 판 기준
    if (str(q["no"]).upper() == str(p["doc_no"]).upper()) if q.get("no") else latest:
        if d.get("paid_full"):                   # 완납 뒤에는 견적을 못 바꾼다(사용자 2026-10-26) — 받은 돈과 금액이 어긋나지 않게
            raise DealError(PAID_QUOTE_LOCKED, 409)
        if (q.get("biz") == "rental") != bool(p.get("rental")):
            raise DealError("올린 파일과 이 영업 건의 판매/대여 구분이 다릅니다. 판매/대여를 맞춘 뒤 다시 올려 주세요.", 409)
        day = dt.date.fromisoformat(q["date"]) if q.get("date") else kst_today()
        new = _quote_from_items(p["items"], day, bool(q.get("vat_included")), p["currency"])
        fresh = {**q, "items": new["items"], "amount": new["amount"], "currency": p["currency"]}
        if p.get("rental"):   # 대여 — 개월 수는 파일 기준, 시작일은 담당자가 정한 그대로, 끝나는 날·월 납입금 다시 계산(사용자 2026-10-27)
            fresh.update(_rental({"rental": {"months": p["rental"]["months"], "start": (q.get("rental") or {}).get("start")}},
                                 new["amount"]))
        await _save(db, deal_id, quote=fresh)
    await _log(db, deal_id, user_id, "quote_replace", {"no": p["doc_no"], "revision": p["revision"], "amount": p["total"],
                                                       "file": p["file"], "latest": latest})


def for_statement(ln: dict) -> bool:
    """거래명세서에 넣을 줄인지 — 기본 품목 묶음의 제품 줄만(사용자 2026-10-13). 사용자 지정 묶음·옵션·물류비·설치 인건비·
    서술형·NEGO 는 빼고, 묶음·종류 표시가 없는 예전 견적 줄은 품목 줄이면 넣는다."""
    return ln.get("row") is None and ln.get("block", "B0") == "B0" and ln.get("kind") not in ("work", "freight", "note")


def _supply_unit(ln: dict) -> float | None:
    return ln["supply_unit"] if ln.get("supply_unit") is not None else ln.get("unit_price")


def snapshot_items(lines: list[dict]) -> list[dict]:
    """발행 기록의 줄 → 영업 건 품목 — 합계에 들어가는 줄(품목·Total 반영 옵션·NEGO)만. AI 견적은 desc, 첨부 견적은 name."""
    return [{"name": ln.get("name") or ln.get("desc") or "",
             # 대여 견적서 줄은 단가(월 납입금) × 개월 = 1대당 계약 금액(사용자 2026-10-22)
             # 부가세 포함 견적서의 줄은 공급가액(supply_unit)으로 — 영업 건 금액은 늘 공급가액(사용자 2026-10-28)
             "unit_price": (_supply_unit(ln) or 0) * ln["months"] if ln.get("months") and _supply_unit(ln) is not None
             else _supply_unit(ln), "qty": ln.get("qty"),
             **({"commodity": ln["commodity"]} if ln.get("commodity") else {}), **({"unit": ln["unit"]} if ln.get("unit") else {}),
             **({"statement": for_statement(ln)} if "block" in ln or ln.get("row") == "nego" else {})}
            for ln in lines or [] if ln.get("row") in (None, "nego") or (ln.get("row") == "option" and ln.get("in_total"))]


async def set_won(db: AsyncSession, deal_id: int, user_id: int | None, won: bool | None, day: dt.date | None,
                  pick: dict | None = None) -> None:
    """수주 상태. 수주 확정 때 pick = {quote_id, revision} 이면 그 판으로 수주(사용자 2026-10-13, 영업팀 요청 15번 —
    2차를 냈어도 고객이 1차로 발주하면 1차): 영업 건 견적(품목·금액·번호)을 그 판으로 바꾸고, 그 판이 '수주 중'으로 보인다."""
    d = await _lock(db, deal_id)
    if d["kind"] == "shipment_only":
        raise DealError("견적 없는 출하 건은 수주 상태가 없습니다.")
    # 이미 수주한 건에 날짜 없이 수주를 다시 누르면 원래 수주일 유지 — 분기 KPI 가 옮겨 가던 문제(검증 DL-049, 2026-10-15).
    # 오늘은 한국 날짜로(서버 시계는 UTC)
    keep = dt.date.fromisoformat(d["won_date"]) if won and d.get("won") and d.get("won_date") and day is None else None
    fields: dict[str, Any] = {"won": won, "won_date": (day or keep or kst_today()) if won else None}
    picked = None
    if won and pick and pick.get("quote_id") is not None:
        docs = await deal_quotes(db, d.get("proposal_id"), deal_id)
        doc = next((q for q in docs if q["id"] == pick["quote_id"]), None)
        if not doc:
            raise DealError("이 영업 건의 견적서가 아닙니다.")
        raw = (await db.execute(text("SELECT history FROM product_quotes WHERE id = :i"), {"i": doc["id"]})).scalar()
        hist = json.loads(raw) if isinstance(raw, str) else (raw or [])
        h = next((x for x in hist if x.get("revision") == pick.get("revision")), None)
        if not h:
            raise DealError("고른 견적서 판을 찾을 수 없습니다.")
        picked = _issue_no(h)
        cur = d.get("quote") or {}
        if picked and picked != cur.get("no"):
            if d.get("paid_full"):
                raise DealError(PAID_QUOTE_LOCKED, 409)
            items = snapshot_items(h.get("lines") or [])
            if not items:
                raise DealError("고른 견적서 판에 품목이 없습니다.")
            issued = str(h.get("issued_at") or "")[:10]
            q = _quote_from_items(items, dt.date.fromisoformat(issued) if issued else kst_today(), False,
                                  h.get("currency") or cur.get("currency") or "KRW", picked)
            fields["quote"] = {**q, "revision": int(h.get("revision") or 0) + 1, "sent_date": cur.get("sent_date"),
                               **_picked_rental(h, cur, q["amount"])}
    await _save(db, deal_id, **fields)
    await _log(db, deal_id, user_id, "won", {"from": d["won"], "to": won, "date": _iso(day),
                                              **({"no": picked} if picked else {})})
    await db.commit()


def _picked_rental(h: dict, cur: dict, amount: float | None) -> dict:
    """고른 판의 판매·대여 구분과 대여 조건(사용자 2026-10-26) — 판 기록에 있으면 그 판 기준으로 개월 수·월 납입금·끝나는 날을
    다시 계산한다(대여 시작일은 담당자가 정해 둔 값이 있으면 그대로). 기록이 없는 예전 판은 건의 값을 그대로 두고 확인 알림."""
    old = cur.get("rental") or {}
    if h.get("biz") == "rental" and (h.get("rental") or {}).get("months"):
        start = (old.get("start") if cur.get("biz") == "rental" else None) or h["rental"].get("start")
        out = _rental({"rental": {"months": h["rental"]["months"], "start": start}}, amount)
        if old.get("return"):
            out["rental"]["return"] = old["return"]
        return out
    if h.get("biz") == "sale":
        return {}
    keep = {k: cur[k] for k in ("biz", "rental") if k in cur}
    if keep.get("rental"):
        keep["rental"] = {**keep["rental"], "unverified": _issue_no(h) or True}
    return keep


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
    # 입금 한 줄을 더해도 완납은 풀리지 않는다 — 완납 해제는 관리자만([완납 해제], 검증 2026-10-26: 일반 직원 입금 추가로 풀리던 문제)
    full = bool(p["full"]) or bool(d.get("paid_full"))
    if d.get("pay_case"):                                  # 결제 방식을 고른 건은 받을 금액을 다 받으면 완납
        total = flow.expected_total(d)
        full = full or bool(total and sum(x.get("amount") or 0 for x in payments) >= total - flow.pay_tolerance(d))
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


async def statement_draft(db: AsyncSession, deal_id: int, initials: str | None = None) -> dict:
    """발급 창에 채워 둘 값 — 이미 발급했으면 그 내용, 아니면 견적 품목(품명·규격 나눔)·기억한 공급받는자·오늘 날짜.
    발행자 이니셜(initials)은 비어 있으면 지금 로그인한 계정 값으로(폼에서 고칠 수 있음, 사용자 2026-10-12)."""
    st = dict(await _statement_draft(db, deal_id))
    typed = st.pop("_typed", False) and dn.clean_initials(st.get("initials"))   # 수주 진행 탭 초안에 직접 적은 값은 그대로
    return {**st, "initials": typed or dn.clean_initials(initials) or dn.clean_initials(st.get("initials")) or ""}


def statement_name(it: dict) -> tuple[str, str]:
    """영업 건 견적 품목 → 거래명세서 (품명, 규격). 견적서 품목 칸(commodity)이 있으면 품명 = 품목, 규격 = 견적서 규격
    (사용자 2026-10-13: 방역 로봇 등 수기 견적이 명세서에서 '교육 포함'처럼 나오던 것). 툴체인저 기본 품목(Magbot ATC 등)이거나
    품목이 없으면(예전 견적·첨부 견적) 지금 명세서 양식대로 규격 글에서 나눈다('TCV1 MASTER T.C' / 'Payload 10Kg')."""
    from ..company_knowledge.quote_xlsx import ATC_ACC_COMMODITY, ATC_COMMODITY, SERVICE_COMMODITY

    com = str(it.get("commodity") or "").strip()
    if not com or com in (ATC_COMMODITY, ATC_ACC_COMMODITY, SERVICE_COMMODITY):
        return sx.split_name(it.get("name") or "")
    spec = " ".join(x.strip().lstrip("-").strip() for x in sx.tidy_name(it.get("name") or "").splitlines() if x.strip())
    return com, spec


async def _statement_draft(db: AsyncSession, deal_id: int) -> dict:
    d = _row((await db.execute(text(f"SELECT {COLS} FROM sales_deals WHERE id = :i"), {"i": deal_id})).mappings().first()
             or _missing())
    if d.get("statement"):
        return d["statement"]
    saved = await _order_state(db, deal_id)                # 수주 진행 탭에서 고친 초안이 있으면 그것
    if saved and saved.get("draft"):
        return {**saved["draft"], "_typed": True}
    items = []
    for it in (d.get("quote") or {}).get("items") or []:
        if it.get("statement") is False:                  # 기본 품목 묶음의 제품 줄만(사용자 2026-10-13)
            continue
        name, spec = statement_name(it)
        items.append({"name": name, "spec": spec, "qty": it.get("qty"), "unit_price": it.get("unit_price"), "note": ""})   # 단위는 명세서에 안 넣음(사용자 2026-10-13)
    buyer = await statement_buyer(db, d.get("customer")) or {"reg_no": "", "name": d.get("customer") or "", "ceo": "", "address": ""}
    return {"no": "", "date": kst_today().isoformat(), "buyer": buyer, "items": items}


def _missing():
    raise DealError("건을 찾을 수 없습니다.", 404)


def _st_item(it: dict) -> dict:
    return {"name": str(it.get("name") or "").strip()[:80], "spec": str(it.get("spec") or "").strip()[:80],
            "qty": it.get("qty"), "unit_price": it.get("unit_price"), "note": str(it.get("note") or "").strip()[:60]}


async def set_statement(db: AsyncSession, deal_id: int, user_id: int | None, p: dict) -> None:
    """거래명세서 발급 — 발급할 때마다 새 발행번호 TS-{이니셜}{발행일}-NN(사용자 2026-10-12, 그 발행자의 그날 명세서 순서).
    다시 발급하면 명세서 내용은 새 것으로 바뀌고 예전 번호는 히스토리에 남는다. 빠진 값이 있으면 DealError. 합계는 코드가 다시 계산."""
    d = await _lock(db, deal_id)
    if not d.get("won"):
        raise DealError("수주 확인 뒤에 거래명세서를 발급합니다.")
    ini = dn.clean_initials(p.get("initials"))
    if not ini:
        raise DealError("발행자 이니셜을 영문 2자로 적어 주세요(예: LU) — 발행번호 TS-LU20261012-01 에 들어갑니다.")
    st = {"no": "", "initials": ini, "date": p["date"].isoformat(),
          "buyer": {k: str((p.get("buyer") or {}).get(k) or "").strip()[:200] for k in ("reg_no", "name", "ceo", "address")},
          "items": [_st_item(it) | ({"unit": u} if (u := sx.unit_of(it)) else {}) for it in p.get("items") or []]}
    parts = [_st_item(it) for it in p.get("parts") or [] if str(it.get("name") or "").strip()]
    if parts and len(st["items"]) == 1 and sx.unit_of(st["items"][0]):
        st["parts"] = parts                                 # 세트로 묶어 발급 — 원래 품목은 참고로
    try:
        sx.check(st)
    except sx.StatementError as e:
        raise DealError(str(e)) from e
    st["no"] = await dn.allocate(db, dn.STATEMENT, ini, p["date"], deal_id=deal_id, user_id=user_id)
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


async def order_open(db: AsyncSession, deal_id: int, initials: str | None = None) -> dict:
    """수주 진행 화면 — {deal, state}. 처음 열면 견적 품목으로 명세서 초안을 만들고 AI 첫 안내를 남긴다. 수주 확인 전이면 DealError."""
    from . import order_agent

    deal = await get_deal(db, deal_id, kst_today())
    if not deal.get("won"):
        raise DealError("수주 확인 뒤에 진행합니다 — 영업 건에서 [수주 확정]을 먼저 눌러 주세요.")
    state = await _order_state(db, deal_id)
    if not state:
        state = order_agent.initial_state(deal, await statement_draft(db, deal_id, initials))
        await save_order_state(db, deal_id, state)
    elif state.get("draft") is not None and not dn.clean_initials(state["draft"].get("initials")) and dn.clean_initials(initials):
        state["draft"]["initials"] = dn.clean_initials(initials)      # 예전 초안·빈 칸 — 지금 계정 이니셜(저장은 고칠 때)
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
_KEEP_EVENTS = ("import", "create", "recommend", "quote", "quote_revision", "units", "as_case")   # 시리얼·A/S 기록은 남김


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
    from . import units                                # 시리얼 기록은 남기고 출하만 풀기(사용자 2026-10-19)

    await units.shipment_removed(db, deal_id, None)
    gone = (await db.execute(text("DELETE FROM sales_deal_events WHERE deal_id = :i AND NOT (action = ANY(:keep))"),
                             {"i": deal_id, "keep": list(_KEEP_EVENTS)})).rowcount
    await _log(db, deal_id, user_id, "reset_to_quote", {"removed_events": gone, "files": len(keys)})
    await db.commit()
    return keys


async def set_terms(db: AsyncSession, deal_id: int, user_id: int | None, raw: list[dict], *, admin: bool = True) -> None:
    """결제 조건(선금·중도금·잔금 %·시점·예정 입금일) 저장 — 사용자 2026-10-08. 이미 확인한 회차의 % 는 바꾸지 않는다.
    입금 예정일이 지난 회차의 예정일은 관리자만 바꾼다(admin=False 면 거절 — 일반 직원은 연장 요청, 사용자 2026-10-13).
    거래명세서 발급 전에도 정할 수 있다 — 수주 진행 칸은 순서대로 안내만 하고 막지 않는다(사용자 2026-10-23)."""
    d = await _lock(db, deal_id)
    try:
        ts = flow.clean_terms(raw)
    except ValueError as e:
        raise DealError(str(e)) from e
    done = {p.get("term") for p in d["payments"] if p.get("term") is not None}
    old = d.get("pay_terms") or []
    if not admin:
        for t in flow.overdue(d, kst_today()):
            i = t["index"]
            if i >= len(ts) or ts[i].get("expected_date") != t.get("expected_date"):
                raise DealError(f"{t['label']} 회차는 입금 예정일({t['expected_date']})이 지나 관리자만 바꿀 수 있습니다. "
                                "[기한 연장 요청]으로 관리자에게 요청해 주세요.", 403)
    for i in done:
        if i >= len(ts) or i >= len(old) or ts[i]["pct"] != old[i].get("pct") or ts[i]["label"] != old[i].get("label"):
            raise DealError("입금 확인한 회차의 구분·비율은 바꿀 수 없습니다(예정일만 바꿀 수 있음). 확인을 먼저 취소해 주세요.")
    # 아직 입금 확인 안 한 회차에 지난 날짜를 새로 넣으면 막는다(검증 2026-10-26 — AI 가 연도를 잘못 넣으면 바로 '입금일 지남').
    # 이미 저장돼 있던 지난 예정일(그대로 둔 것)은 그대로 — 연장은 [기한 연장]으로
    today = kst_today().isoformat()
    for i, t in enumerate(ts):
        due = t.get("expected_date")
        if due and due < today and i not in done and (i >= len(old) or old[i].get("expected_date") != due):
            raise DealError(f"{t['label']} 회차의 입금 예정일({due})이 오늘({today})보다 앞입니다. 날짜(연도)를 확인해 주세요.")
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


DROP_LOCKED = "출하나 입금 기록이 있는 건은 관리자만 드랍할 수 있습니다."


def drop_locked(d: dict) -> bool:
    """출하·입금 기록이 있는 건 — 드랍은 관리자만(사용자 2026-10-27)."""
    return bool(d.get("shipments") or d.get("payments") or d.get("shipped_note") or d.get("paid_full"))


async def set_dropped(db: AsyncSession, deal_id: int, user_id: int | None, dropped: bool, reason: str | None,
                      *, admin: bool = True) -> None:
    """드랍(수주까지 못 간 건·진행을 접음, 사용자 2026-10-08 — 실주는 없애고 드랍 하나로) / 되살리기.
    출하·입금 기록이 있는 건의 드랍은 관리자만(사용자 2026-10-27)."""
    d = await _lock(db, deal_id)
    if dropped and not admin and drop_locked(d):
        raise DealError(DROP_LOCKED, 403)
    extra = {}
    if not dropped and d.get("won") is False:
        # 예전 '실주'(won=false)도 드랍으로 보인다(status.derive) — 되살리면 수주 결과를 '미확인'으로(사용자 2026-10-28)
        extra["won"] = None
    await _save(db, deal_id, dropped=dropped, drop_reason=(reason or "").strip()[:200] or None if dropped else None, **extra)
    await _log(db, deal_id, user_id, "drop" if dropped else "undrop",
               {"from": d.get("dropped"), "reason": reason, **({"won_from": False} if extra else {})})
    await db.commit()


def _ship_entry(d: dict, p: dict, forced: bool) -> dict:
    """출하 입력 → 출하 기록 한 줄(추가·수정 공용)."""
    tracking = [t for t in re.split(r"[,\s]+", p.get("tracking") or "") if t]
    # 제품별 출하 줄(사용자 2026-10-07: 제품마다 사진) — 예전 출하 시트처럼 품목·수량 글도 함께 남긴다
    lines = [{"name": ln["name"], "qty": ln.get("qty"), "qty_text": ln.get("qty_text") or None, "photos": list(ln.get("photos") or []),
              **({"line_no": ln["line_no"]} if ln.get("line_no") is not None else {})}
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
    new = _ship_entry(d, p, bool(old.get("forced")))
    # 고치는 화면에 있는 칸만 바꾸고 나머지(구분·담당·사진·출처·예외 출하 표시 등)는 처음 값 그대로(검증 2026-10-26)
    entry = {**old, **{k: new[k] for k in ("date", "date_guessed", "carrier", "tracking", "receiver", "address",
                                           "items", "lines", "qty_text")}}
    shipments = list(d["shipments"])
    shipments[index] = entry
    await _save(db, deal_id, shipments=shipments)
    # 묶은 시리얼(사용자 2026-10-19) — 고른 목록이 오면 그대로, 안 오면 묶여 있던 것을 새 출하일로(무상 기간 시작일도)
    from . import units

    ids = p.get("unit_ids")
    if ids is not None:
        await units.check_ship_over(db, d, p)
    else:
        ids = list((await db.execute(text("SELECT id FROM product_units WHERE deal_id = :d AND shipment_index = :x"),
                                     {"d": deal_id, "x": index})).scalars().all())
    await units.ship_units(db, deal_id, index, p["date"], ids, None)
    await _log(db, deal_id, user_id, "shipment_edit", {"index": index, **{k: entry[k] for k in ("date", "carrier", "tracking", "items")}})
    await db.commit()


async def add_shipment(db: AsyncSession, deal_id: int, user_id: int | None, p: dict) -> None:
    """출하 기록 — 입금 확인과 상관없이 언제든(사용자 2026-10-08: 입금 확인 전 출하 막기·예외 체크 없앰)."""
    d = await _lock(db, deal_id)
    # 출하 전에 이 출하로 나가는 품목의 S/N 을 적어야 한다(사용자 2026-10-19·10-23 — S/N 없는 품목은 '특주'·'실주'처럼)
    from . import units

    issue = await units.check_before_ship(db, d, p) if d["kind"] == "quote" else None
    entry = _ship_entry(d, p, False)
    fields: dict[str, Any] = {"shipments": d["shipments"] + [entry]}
    if d["kind"] == "quote" and not d["won"]:
        fields.update(won=True, won_date=p["date"])
    await _save(db, deal_id, **fields)
    n = await units.ship_units(db, deal_id, len(d["shipments"]), p["date"], p.get("unit_ids"), issue)
    await _log(db, deal_id, user_id, "shipment", {**{k: entry[k] for k in ("date", "carrier", "tracking", "items", "forced")},
                                                  **({"units": n} if n else {})})
    await db.commit()


async def add_po_file(db: AsyncSession, deal_id: int, user_id: int | None, entry: dict) -> None:
    """발주서 파일 기록 추가(파일 자체는 호출부가 저장소에 먼저 올린다). 수주 미확인이면 발주서를 받았으니 수주로 본다."""
    d = await _lock(db, deal_id)
    if d["kind"] == "shipment_only":
        raise DealError("견적 없는 출하 건에는 발주서를 붙이지 않습니다.")
    fields: dict[str, Any] = {"po_files": d["po_files"] + [entry]}
    if not d["won"]:
        fields.update(won=True, won_date=kst_today())
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


STALE_ENTRY = "다른 사람이 이 건을 먼저 고쳐 목록이 바뀌었습니다. 화면을 새로 고친 뒤 다시 해 주세요."


async def check_entry(db: AsyncSession, deal_id: int, kind: str, index: int, expect: dict | None) -> None:
    """줄 번호(index)로 고치는 요청 — 그 자리의 기록이 화면에서 본 것(expect)과 같은지(검증 2026-10-26: 여러 명이 같이 쓰다
    앞 줄이 지워지면 엉뚱한 줄이 바뀌던 문제). expect 에 있는 칸 중 저장된 칸만 비교한다(화면용으로 덧붙인 칸은 빼고).
    expect 가 없으면(예전 화면) 그냥 둔다. 건을 잠그므로 같은 트랜잭션의 다음 작업까지 안 바뀐다. 커밋하지 않는다."""
    if expect is None:
        return
    d = await _lock(db, deal_id)
    items = d.get(kind) or []
    cur = items[index] if 0 <= index < len(items) else None
    keys = [k for k in expect if cur is not None and k in cur]
    if not keys or any(cur[k] != expect[k] for k in keys):
        raise DealError(STALE_ENTRY, 409)


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
    if kind == "payments":       # 입금 한 줄이라도 지우면 완납은 풀린다(회차 입금 확인 취소와 같게, 검증 DL-033 2026-10-15)
        fields["paid_full"] = False
    if kind == "shipments":      # 그 출하에 묶인 시리얼은 출하 전으로(사용자 2026-10-19)
        from . import units

        await units.shipment_removed(db, deal_id, index)
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
    from . import units

    # 그 건의 S/N 은 지우지 않고 남긴다 — 어느 건·고객사 것이었는지 메모에 적고 상태 '건 삭제'(사용자 2026-10-26:
    # 버린 S/N 도 기록, S/N 은 겹치지 않으므로 같은 번호 다시 등록은 막는다). 건이 지워지면 deal_id 는 비게 된다(ON DELETE SET NULL)
    await db.execute(text(
        "UPDATE product_units u SET status = :st, updated_at = NOW(), note = btrim("
        "  '[' || :tag || ' ' || :day || '] ' || concat_ws(' · ', COALESCE(d.quote->>'no', d.deal_no), d.customer, d.title) "
        "  || CASE WHEN u.note <> '' THEN ' / ' || u.note ELSE '' END) "
        "FROM sales_deals d WHERE d.id = u.deal_id AND u.deal_id = ANY(:ids)"),
        {"ids": ids, "st": units.DELETED, "tag": "지운 건", "day": kst_today().isoformat()})
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
    """이관 미리보기 행(build_preview 결과) → sales_deals. 이미 이관한 적이 있으면 거부(두 번 들어가지 않게).
    엑셀 이관은 꺼 두었다(사용자 2026-10-27, legacy_import.ENABLED)."""
    from . import legacy_import

    if not legacy_import.ENABLED:
        raise DealError(legacy_import.DISABLED_MSG, 409)
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
