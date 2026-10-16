"""관리자 승인 요청(사용자 2026-10-13, 영업팀 요청 8번) — 지금은 입금 예정일 연장.

- 입금 예정일이 지난 회차의 예정일은 관리자만 바꾼다(extend_due). 일반 직원은 받을 관리자를 골라 연장 요청(request_extend).
- 관리자는 [검토·승인 > 승인 요청]에서 승인(날짜를 고쳐서도) 또는 거절(decide). 승인하면 그 회차 예정일이 바로 바뀐다.
- 받는 관리자(recipients)에게 온 대기 요청 수가 탭의 빨간 숫자(pending_count). 처리는 어느 관리자든 할 수 있다.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import flow
from . import service as svc

KIND_DUE = "pay_due_extend"
REASON_MAX = 500
# 영업을 볼 수 있는 관리자 — 최고 관리자, 영업(또는 전체) 담당 관리자(api_sales_deals.is_admin·require_sales 와 같은 기준)
_ADMIN_SQL = ("is_active AND (role = 'superadmin' OR (role = 'domain_admin' AND domain IN ('sales', 'all')))")


async def admins(db: AsyncSession) -> list[dict[str, Any]]:
    """요청을 받을 수 있는 관리자 목록 — 요청 창의 체크 목록."""
    rs = (await db.execute(text(f"SELECT id, COALESCE(NULLIF(alias, ''), username) AS name, role FROM users WHERE {_ADMIN_SQL} "
                                "ORDER BY role DESC, id"))).mappings().all()
    return [{"id": r["id"], "name": r["name"], "role": r["role"]} for r in rs]


def _clean_reason(v: Any) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()[:REASON_MAX]


def _overdue_term(d: dict, index: int, today: dt.date) -> dict:
    """그 회차가 입금 예정일이 지났고 아직 입금 확인 전인지 — 아니면 DealError."""
    t = next((x for x in flow.overdue(d, today) if x["index"] == index), None)
    if not t:
        raise svc.DealError("입금 예정일이 지난(입금 확인 전) 회차만 연장할 수 있습니다.")
    return t


def _check_new_due(new_due: dt.date, cur: str, today: dt.date) -> None:
    if new_due <= today:
        raise svc.DealError("새 입금 예정일은 오늘 이후 날짜로 정해 주세요.")
    if cur and new_due.isoformat() <= cur:
        raise svc.DealError("새 입금 예정일은 지금 예정일보다 뒤여야 합니다.")


async def _apply_due(db: AsyncSession, d: dict, index: int, new_due: dt.date, user_id: int | None,
                     reason: str, request_id: int | None = None) -> None:
    """회차 예정일을 바꾸고 변경 이력에 남긴다(커밋 안 함)."""
    terms = [dict(t) for t in d.get("pay_terms") or []]
    old = terms[index].get("expected_date")
    terms[index]["expected_date"] = new_due.isoformat()
    await svc._save(db, d["id"], pay_terms=terms)
    await svc._log(db, d["id"], user_id, "due_extend", {"index": index, "label": terms[index].get("label"), "from": old,
                                                         "to": new_due.isoformat(), "reason": reason,
                                                         **({"request": request_id} if request_id else {})})


async def extend_due(db: AsyncSession, deal_id: int, admin_id: int, index: int, new_due: dt.date, reason: str) -> None:
    """관리자가 바로 연장 — 고객사와 협의한 새 예정일. 같은 회차의 대기 요청은 이 날짜로 승인 처리."""
    today = svc.kst_today()
    d = await svc._lock(db, deal_id)
    t = _overdue_term(d, index, today)
    _check_new_due(new_due, t.get("expected_date") or "", today)
    reason = _clean_reason(reason)
    await _apply_due(db, d, index, new_due, admin_id, reason)
    await db.execute(text("UPDATE approval_requests SET status = 'approved', decided_by = :u, decided_due = :due, "
                          "decision_note = :n, decided_at = NOW() WHERE deal_id = :d AND term_index = :i AND status = 'pending' "
                          "AND kind = :k"),
                     {"u": admin_id, "due": new_due, "n": reason or "관리자가 직접 연장", "d": deal_id, "i": index, "k": KIND_DUE})
    await db.commit()


async def request_extend(db: AsyncSession, deal_id: int, user_id: int, index: int, new_due: dt.date, reason: str,
                         recipients: list[int]) -> int:
    """일반 직원의 연장 요청 — 받을 관리자를 하나 이상 골라야 한다. 같은 회차에 대기 중인 요청이 있으면 거절."""
    today = svc.kst_today()
    d = await svc._lock(db, deal_id)
    t = _overdue_term(d, index, today)
    _check_new_due(new_due, t.get("expected_date") or "", today)
    reason = _clean_reason(reason)
    if not reason:
        raise svc.DealError("연장 사유를 적어 주세요(예: 고객사와 협의 — 11월 말 입금 예정).")
    ok = {a["id"] for a in await admins(db)}
    to = sorted({int(x) for x in recipients if int(x) in ok})
    if not to:
        raise svc.DealError("요청을 받을 관리자를 한 명 이상 골라 주세요.")
    dup = (await db.execute(text("SELECT 1 FROM approval_requests WHERE deal_id = :d AND term_index = :i AND status = 'pending' "
                                 "AND kind = :k"), {"d": deal_id, "i": index, "k": KIND_DUE})).first()
    if dup:
        raise svc.DealError("이 회차는 이미 연장 요청을 보내 관리자 확인을 기다리는 중입니다.")
    rid = (await db.execute(text(
        "INSERT INTO approval_requests (kind, deal_id, term_index, current_due, requested_due, reason, recipients, requested_by) "
        "VALUES (:k, :d, :i, :cur, :due, :r, :to, :u) RETURNING id"),
        {"k": KIND_DUE, "d": deal_id, "i": index, "cur": dt.date.fromisoformat(t["expected_date"]), "due": new_due,
         "r": reason, "to": to, "u": user_id})).scalar_one()
    await svc._log(db, deal_id, user_id, "due_request", {"index": index, "label": t.get("label"), "from": t["expected_date"],
                                                          "to": new_due.isoformat(), "reason": reason, "request": rid})
    await db.commit()
    return rid


async def decide(db: AsyncSession, request_id: int, admin_id: int, approve: bool, new_due: dt.date | None, note: str) -> None:
    """승인(희망 날짜 그대로 또는 고친 날짜) · 거절(사유). 이미 처리한 요청이면 거절."""
    r = (await db.execute(text("SELECT * FROM approval_requests WHERE id = :i FOR UPDATE"), {"i": request_id})).mappings().first()
    if not r:
        raise svc.DealError("요청을 찾을 수 없습니다.", 404)
    if r["status"] != "pending":
        raise svc.DealError("이미 처리한 요청입니다.")
    note = _clean_reason(note)
    if approve:
        due = new_due or r["requested_due"]
        today = svc.kst_today()
        d = await svc._lock(db, r["deal_id"])
        terms = d.get("pay_terms") or []
        if r["term_index"] is None or r["term_index"] >= len(terms):
            raise svc.DealError("요청한 회차가 결제 조건에 없습니다(결제 조건이 바뀌었을 수 있음). 거절 후 다시 요청받아 주세요.")
        # 요청 뒤 결제 조건이 바뀌어 그 자리에 다른 회차가 오면 승인하지 않는다(검증 DL-064, 2026-10-15)
        cur_due = r["current_due"].isoformat() if r["current_due"] else None
        if (terms[r["term_index"]].get("expected_date") or None) != cur_due:
            raise svc.DealError("요청한 뒤 결제 조건(회차·예정일)이 바뀌었습니다. 거절 후 다시 요청받아 주세요.")
        _check_new_due(due, terms[r["term_index"]].get("expected_date") or "", today)
        await _apply_due(db, d, r["term_index"], due, admin_id, note or r["reason"], request_id)
    else:
        if not note:
            raise svc.DealError("거절 사유를 적어 주세요.")
        due = None
        await svc._log(db, r["deal_id"], admin_id, "due_reject", {"index": r["term_index"], "reason": note, "request": request_id})
    await db.execute(text("UPDATE approval_requests SET status = :s, decided_by = :u, decided_due = :due, decision_note = :n, "
                          "decided_at = NOW() WHERE id = :i"),
                     {"s": "approved" if approve else "rejected", "u": admin_id, "due": due, "n": note, "i": request_id})
    await db.commit()


def _row(r: Any, me: int | None) -> dict[str, Any]:
    return {"id": r["id"], "kind": r["kind"], "status": r["status"], "deal_id": r["deal_id"], "term_index": r["term_index"],
            "term_label": r["term_label"], "deal_no": r["deal_no"], "customer": r["customer"], "title": r["title"],
            "current_due": r["current_due"].isoformat() if r["current_due"] else None,
            "requested_due": r["requested_due"].isoformat() if r["requested_due"] else None,
            "decided_due": r["decided_due"].isoformat() if r["decided_due"] else None,
            "reason": r["reason"], "decision_note": r["decision_note"],
            "requested_by": r["requester"], "decided_by": r["decider"], "recipients": list(r["recipient_names"] or []),
            "to_me": me is not None and me in (r["recipients"] or []),
            "created_at": r["created_at"].isoformat(), "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None}


_LIST_SQL = """
SELECT a.*, d.deal_no, d.customer, d.title,
       d.pay_terms -> a.term_index ->> 'label' AS term_label,
       COALESCE(NULLIF(ru.alias, ''), ru.username) AS requester, COALESCE(NULLIF(du.alias, ''), du.username) AS decider,
       ARRAY(SELECT COALESCE(NULLIF(u.alias, ''), u.username) FROM users u WHERE u.id = ANY(a.recipients) ORDER BY u.id) AS recipient_names
FROM approval_requests a JOIN sales_deals d ON d.id = a.deal_id
LEFT JOIN users ru ON ru.id = a.requested_by LEFT JOIN users du ON du.id = a.decided_by
"""


async def list_requests(db: AsyncSession, me: int, *, done_limit: int = 50) -> dict[str, Any]:
    """[검토·승인 > 승인 요청] — 대기 중(나에게 온 것 먼저) + 최근 처리 내역."""
    pend = (await db.execute(text(_LIST_SQL + " WHERE a.status = 'pending' ORDER BY (:me = ANY(a.recipients)) DESC, a.created_at"),
                             {"me": me})).mappings().all()
    done = (await db.execute(text(_LIST_SQL + " WHERE a.status <> 'pending' ORDER BY a.decided_at DESC NULLS LAST LIMIT :n"),
                             {"n": done_limit})).mappings().all()
    return {"pending": [_row(r, me) for r in pend], "done": [_row(r, me) for r in done]}


async def deal_requests(db: AsyncSession, deal_id: int) -> list[dict[str, Any]]:
    """한 영업 건의 요청(회차별 최근 것) — 상세 화면의 '연장 요청 중'·'승인됨'·'거절됨' 표시."""
    rs = (await db.execute(text(_LIST_SQL + " WHERE a.deal_id = :d ORDER BY a.created_at DESC"), {"d": deal_id})).mappings().all()
    seen: set[int] = set()
    out = []
    for r in rs:
        if r["term_index"] in seen:
            continue
        seen.add(r["term_index"])
        out.append(_row(r, None))
    return out


async def pending_brief(db: AsyncSession, me: int, limit: int = 10) -> list[dict[str, Any]]:
    """나에게 온 대기 요청 중 팝업을 아직 안 본 것(최근 먼저) — 관리자 화면 팝업(요청마다 한 번, 사용자 2026-10-13).
    본 기록은 DB(alerted_to) — 어느 기기·브라우저에서 봤든 다시 뜨지 않는다."""
    rs = (await db.execute(text(_LIST_SQL + " WHERE a.status = 'pending' AND :me = ANY(a.recipients) AND NOT (:me = ANY(a.alerted_to)) "
                                "ORDER BY a.created_at DESC LIMIT :n"), {"me": me, "n": limit})).mappings().all()
    return [{k: v for k, v in _row(r, me).items() if k in ("id", "kind", "deal_id", "deal_no", "customer", "term_label", "requested_due",
                                                          "requested_by", "reason", "created_at")} for r in rs]


async def mark_alerted(db: AsyncSession, me: int, ids: list[int]) -> int:
    """팝업을 본 요청에 나를 기록 — 나에게 온 요청만. 바뀐 개수."""
    if not ids:
        return 0
    n = (await db.execute(text("UPDATE approval_requests SET alerted_to = array_append(alerted_to, :me) "
                               "WHERE id = ANY(:ids) AND :me = ANY(recipients) AND NOT (:me = ANY(alerted_to)) RETURNING id"),
                          {"me": me, "ids": [int(x) for x in ids][:100]})).all()
    await db.commit()
    return len(n)


async def pending_count(db: AsyncSession, me: int) -> int:
    """나에게 온 대기 요청 수 — 탭의 빨간 숫자."""
    return int((await db.execute(text("SELECT COUNT(*) FROM approval_requests WHERE status = 'pending' AND :me = ANY(recipients)"),
                                 {"me": me})).scalar() or 0)
