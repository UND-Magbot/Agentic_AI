# -*- coding: utf-8 -*-
"""구매 건 DB 읽기·쓰기. 바뀔 때마다 purchase_events 에 누가·무엇을 남긴다(sales_deals/service.py 와 같은 방식).

파일(견적서·재가 서류·지출내역·거래명세서·세금계산서)은 저장소(MinIO purchases/{id}/…)에 두고 기록만 JSONB 에 둔다.
배열을 고칠 때는 SELECT … FOR UPDATE 로 잠가 두 사람이 같이 고쳐도 기록이 사라지지 않게 한다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..sales_deals import doc_numbers as dn
from ..sales_deals.service import kst_today
from .. import money
from . import flow
from . import po_xlsx as px
from . import stock

COLS = ("id, supplier, title, owner, region, quote_files, approval, po, pay_terms, payments, receipt, statement, tax_invoice, "
        "customs, note, reason, dropped, drop_reason, created_by, created_at, updated_at, direct")
JSON_COLS = ("quote_files", "approval", "po", "pay_terms", "payments", "receipt", "statement", "tax_invoice", "customs")
REGIONS = {"domestic": "국내", "overseas": "해외"}
CURRENCY = {"domestic": "KRW", "overseas": "USD"}
# 파일을 붙이는 칸 — kind → (컬럼, 그 안의 파일 목록 키). quote 는 컬럼 자체가 목록.
# 해외 제조사 송금 확인증·통관 기타 서류(CI·Packing List 등)는 해외 전용 customs 칸에 둔다(사용자 2026-11-11, 표 구조는 그대로)
FILE_KINDS = {"quote": "견적서", "approval": "재가 서류", "statement": "거래명세서", "tax": "세금계산서", "customs": "원산지 증명서",
              "remit": "제조사 송금 확인증", "customs_etc": "통관 기타 서류"}
_FILE_COL = {"approval": ("approval", "files"), "statement": ("statement", "files"), "tax": ("tax_invoice", "files"),
             "customs": ("customs", "files"), "remit": ("customs", "remit_files"), "customs_etc": ("customs", "etc_files")}
OVERSEAS_KINDS = ("customs", "remit", "customs_etc", "broker")
SERIAL_MAX = 80
FILE_KEYS = ("key", "filename", "mime", "size", "uploaded_at", "uploaded_by")
CHAT_MAX = 40                                         # 발주서 AI 대화 저장 줄 수
ACTION_LABEL = {"receipt_photo": "입고 사진", "direct": "재고 직접 등록", "direct_edit": "재고 직접 등록 고침", "create": "구매 건 등록", "info": "기본 정보 수정", "reason": "구매 사유", "file_quote": "견적서 첨부", "file_approval": "재가 서류 첨부",
                "file_statement": "거래명세서 첨부", "file_tax": "세금계산서 첨부", "remove_file": "첨부 삭제",
                "approve": "재가 완료", "unapprove": "재가 취소", "po_issue": "발주서 발행", "po_cancel": "발주서 발행 취소", "terms": "결제 조건",
                "pay_check": "지출내역 대조", "pay_confirm": "송금 확인", "pay_unconfirm": "송금 확인 취소",
                "receipt": "입고 확인", "receipt_undo": "입고 취소", "statement": "거래명세서 기록", "statement_undo": "거래명세서 기록 취소",
                "tax": "세금계산서 기록", "tax_undo": "세금계산서 기록 취소", "drop": "구매 취소", "undrop": "구매 되살리기",
                "file_customs": "원산지 증명서 첨부", "customs": "통관 서류 제출 완료", "customs_undo": "통관 서류 제출 취소",
                "expected_date": "입고 예정일", "paid": "송금 완료", "receipt_edit": "입고 기록 수정",
                "po_upload": "고친 발주서 파일로 발행", "file_remit": "제조사 송금 확인증 첨부",
                "file_customs_etc": "통관 기타 서류 첨부", "ship": "배송 시작", "ship_undo": "배송 시작 취소",
                "broker": "관세사 송금 확인", "broker_undo": "관세사 송금 취소", "no_origin": "원산지 증명서 없음",
                "receipt_sn": "입고 S/N 등록", "quote_skip": "견적서 없이 넘어감", "quote_skip_undo": "견적서 없이 넘어감 취소"}


class PurchaseError(Exception):
    """사용자에게 그대로 보여줄 업무 오류. data = 화면이 묻는 데 쓰는 값(있으면 detail 에 같이 — api._fail)."""

    def __init__(self, message: str, status: int = 400, data: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.data = data


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v


def _row(r: Any) -> dict:
    d = dict(r)
    for k in JSON_COLS:
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    # 입고 기록은 한 건 — 예전에 나눠 남긴 기록은 읽을 때 하나로(다음 저장 때 한 건으로 남는다, 사용자 2026-11-17)
    rc = d.get("receipt")
    if isinstance(rc, dict) and len(rc.get("records") or []) > 1:
        d["receipt"] = {**rc, "records": flow.merge_receipts(rc["records"])}
    for k in ("created_at", "updated_at"):
        d[k] = _iso(d[k])
    return d


def _file_view(f: dict) -> dict:
    return {k: f.get(k) for k in FILE_KEYS if k != "key"}


def _upload_view(f: dict | None) -> dict | None:
    return {"filename": f.get("filename"), "mime": f.get("mime")} if f else None


def _replaced_view(v: dict) -> list[dict]:
    """같은 번호로 교체하기 전 내용 목록(사용자 2026-11-16) — 화면의 [교체 전 파일 받기]."""
    return [{"upload": _upload_view(r.get("upload")), "file_name": r.get("file_name"), "total": r.get("total"),
             "currency": r.get("currency"), "replaced_at": r.get("replaced_at"), "replaced_by": r.get("replaced_by")}
            for r in v.get("replaced") or []]


def view(d: dict, today: dt.date) -> dict:
    """저장된 건 → 화면 행(단계·상태·알림·회차 금액). 저장소 키는 내보내지 않는다."""
    po = d.get("po") or {}
    st, tx, cu = d.get("statement") or {}, d.get("tax_invoice") or {}, d.get("customs") or {}
    rc = d.get("receipt") or {}
    got = flow.received(d)
    return {
        "id": d["id"], "supplier": d["supplier"], "title": d["title"], "owner": d["owner"], "region": d["region"],
        "currency": po.get("currency") or CURRENCY.get(d["region"], "KRW"),
        # 입고 예정일 — 발주 단계에서 꼭 적는다(사용자 2026-11-02), 리스트·상세 표시
        "expected_date": flow.expected_date(d), "expected_history": po.get("expected_history") or [],
        "note": d["note"], "reason": d.get("reason") or "", "dropped": d["dropped"], "drop_reason": d["drop_reason"],
        "created_at": d["created_at"], "updated_at": d["updated_at"],
        "quote_files": [_file_view(f) for f in d.get("quote_files") or []],
        "approval": {**{k: v for k, v in (d.get("approval") or {}).items() if k != "files"},
                     "files": [_file_view(f) for f in (d.get("approval") or {}).get("files") or []]},
        "po": {"no": po.get("no"), "date": po.get("date"), "total": po.get("total"), "issued_at": po.get("issued_at"),
               "forced": po.get("forced"),
               "vat_included": flow.vat_included(d),          # 국내 발주서 금액 VAT 포함(사용자 2026-11-20) — total 은 발주서에 찍힌 합계
               "supply": flow.po_total(d),                     # 발주 금액 = 늘 공급가액(VAT 별도, 사용자 2026-11-20)
               "issued_by": po.get("issued_by"), "revision": po.get("revision"), "file_name": po.get("file_name"),
               "items": po.get("items") or [], "has_draft": bool(po.get("draft")), "currency": po.get("currency"),
               # 고친 발주서 파일로 발행했으면 그 파일(사용자 2026-11-11) — 내려받기는 그 파일 그대로
               "upload": _upload_view(po.get("upload")),
               "replaced": _replaced_view(po),
               "history": [{**{k: h.get(k) for k in ("no", "date", "total", "issued_at", "issued_by", "revision", "file_name",
                                                     "cancelled_at", "cancelled_by")},
                            "vat_included": bool((h.get("header") or {}).get("vat_included")),
                            "supply": (float(money.supply_of(h["total"])) if (h.get("header") or {}).get("vat_included")
                                       and h.get("total") is not None else h.get("total")),
                            "upload": _upload_view(h.get("upload")), "replaced": _replaced_view(h)} for h in po.get("history") or []]},
        "pay_terms": [{k: v for k, v in t.items() if k != "check"} for t in d.get("pay_terms") or []],   # 대조 결과는 terms 에
        "terms": flow.terms(d),
        "receipt": {"done": flow.receipt_done(d),
                    "records": [{**r, "purpose": flow.purpose_of(r), "lines": r.get("lines") or []} for r in rc.get("records") or []],
                    # 품목 줄별 발주·입고 수량(입고 기록 창의 기본값)
                    "lines": [{"line_no": i, "name": px.item_name(it), "ordered": it.get("qty"),
                               "received": got.get(i, 0)} for i, it in enumerate(po.get("items") or [])]},
        # 해외 통관 서류 — 원산지 증명서(files)·증빙 자료 없음(no_origin)·기타 서류(etc_files, 이름 label)·관세사 송금(broker)
        # · 제조사 송금 확인증(remit_files)
        "customs": {**{k: v for k, v in cu.items() if k not in ("files", "etc_files", "remit_files")},
                    "files": [_file_view(f) for f in cu.get("files") or []],
                    "etc_files": [{**_file_view(f), "label": f.get("label") or ""} for f in cu.get("etc_files") or []],
                    "remit_files": [_file_view(f) for f in cu.get("remit_files") or []]},
        "shipping": flow.shipping(d) or None, "in_transit": flow.in_transit(d),
        "payment": next(({k: p.get(k) for k in ("date", "by", "note")} for p in d.get("payments") or [] if p.get("simple")), None),
        "statement": {**{k: v for k, v in st.items() if k != "files"}, "files": [_file_view(f) for f in st.get("files") or []]},
        "tax_invoice": {**{k: v for k, v in tx.items() if k != "files"}, "files": [_file_view(f) for f in tx.get("files") or []]},
        "steps": flow.steps(d), "stage": flow.stage(d), "stage_note": flow.stage_note(d), "alerts": flow.alerts(d, today),
        "total": flow.po_total(d),
        # 거래명세서·세금계산서 상태(첨부·선결제·선발행·미등록, 사용자 2026-11-11 — 리스트에도 표시)
        "docs": {"statement": flow.doc_state(d, "statement"), "tax": flow.doc_state(d, "tax")},
        "prepaid": {"statement": flow.doc_state(d, "statement")["state"] == "skip",
                    "tax": flow.doc_state(d, "tax")["state"] == "skip"},
    }


async def _hist(db: AsyncSession, pid: int, overseas: bool = False) -> list[dict]:
    """변경 이력 — 해외 건의 예정일은 '납기일'로 보인다(QA 2026-11-16)."""
    labels = {**ACTION_LABEL, "expected_date": "납기일"} if overseas else ACTION_LABEL
    rs = (await db.execute(text(
        "SELECT e.id, e.action, e.detail, e.created_at, COALESCE(u.alias, u.username) AS user_name "
        "FROM purchase_events e LEFT JOIN users u ON u.id = e.user_id WHERE e.purchase_id = :p ORDER BY e.id DESC"),
        {"p": pid})).mappings().all()
    return [{"id": e["id"], "action": e["action"], "label": labels.get(e["action"], e["action"]),
             "detail": json.loads(e["detail"]) if isinstance(e["detail"], str) else e["detail"],
             "created_at": _iso(e["created_at"]), "user_name": e["user_name"]} for e in rs]


async def list_purchases(db: AsyncSession, today: dt.date) -> dict:
    # 재고 직접 등록 건(direct)은 구매 건이 아니라 빼고 보인다(사용자 2026-11-19)
    rs = (await db.execute(text(f"SELECT {COLS} FROM purchases WHERE NOT direct ORDER BY updated_at DESC, id DESC"))).mappings().all()
    rows = [view(_row(r), today) for r in rs]
    counts: dict[str, int] = {s: 0 for s in flow.STAGES}
    for r in rows:
        counts[r["stage"]] = counts.get(r["stage"], 0) + 1
    return {"rows": rows, "counts": counts, "stages": flow.STAGES}


async def get(db: AsyncSession, pid: int, today: dt.date) -> dict:
    r = (await db.execute(text(f"SELECT {COLS} FROM purchases WHERE id = :i"), {"i": pid})).mappings().first()
    if not r:
        raise PurchaseError("구매 건을 찾을 수 없습니다.", 404)
    d = _row(r)
    return {**view(d, today), "history": await _hist(db, pid, flow.overseas(d))}


async def raw(db: AsyncSession, pid: int) -> dict:
    r = (await db.execute(text(f"SELECT {COLS} FROM purchases WHERE id = :i"), {"i": pid})).mappings().first()
    if not r:
        raise PurchaseError("구매 건을 찾을 수 없습니다.", 404)
    return _row(r)


async def _lock(db: AsyncSession, pid: int) -> dict:
    r = (await db.execute(text(f"SELECT {COLS} FROM purchases WHERE id = :i FOR UPDATE"), {"i": pid})).mappings().first()
    if not r:
        raise PurchaseError("구매 건을 찾을 수 없습니다.", 404)
    return _row(r)


async def _save(db: AsyncSession, pid: int, **fields: Any) -> None:
    sets, params = [], {"i": pid}
    for k, v in fields.items():
        if k in JSON_COLS:
            sets.append(f"{k} = CAST(:{k} AS jsonb)")
            params[k] = json.dumps(v, ensure_ascii=False)
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v
    await db.execute(text(f"UPDATE purchases SET {', '.join(sets)}, updated_at = NOW() WHERE id = :i"), params)
    if "receipt" in fields or "owner" in fields or "supplier" in fields:   # 제품 재고 관리 — 입고 기록·담당·공급사(제조사 기본값)가 바뀌면 재고·S/N 도(2026-11-19)
        await stock.sync(db, pid)


async def _log(db: AsyncSession, pid: int, user_id: int | None, action: str, detail: dict) -> None:
    await db.execute(text("INSERT INTO purchase_events (purchase_id, user_id, action, detail) VALUES (:p, :u, :a, CAST(:d AS jsonb))"),
                     {"p": pid, "u": user_id, "a": action, "d": json.dumps(detail, ensure_ascii=False)})


def _clean(v: Any, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


STALE_ENTRY = "다른 사람이 이 건을 먼저 고쳐 목록이 바뀌었습니다. 화면을 새로 고친 뒤 다시 해 주세요."


def _same_entry(items: list, index: int, expect: dict | None) -> None:
    """순번(index)으로 지우거나 고치는 요청 — 그 자리의 기록이 화면에서 본 것(expect)과 같을 때만(QA 2026-11-16, 영업 건 check_entry 와 같게).
    다른 사람이 앞 기록을 먼저 지우면 순번이 당겨져 엉뚱한 파일·입고 기록이 지워지거나 덮어써지던 문제. expect 에 있는 칸 중
    저장된 칸만 비교하고, expect 가 없으면(예전 화면) 그냥 둔다. 호출 전에 건을 잠가 두어야 한다."""
    if expect is None:
        return
    cur = items[index] if 0 <= index < len(items) else None
    keys = [k for k in expect if isinstance(cur, dict) and k in cur]
    if not keys or any(cur[k] != expect[k] for k in keys):
        raise PurchaseError(STALE_ENTRY, 409)


def _alive(d: dict) -> None:
    if d.get("dropped"):
        raise PurchaseError("취소한 구매 건입니다. 되살린 뒤 진행해 주세요.")


def _need_order(d: dict, what: str) -> None:
    """발주 단계가 끝나야 — 발주서 발행 + 입고 예정일(국내 — 사용자 2026-11-02). 해외 납기일은 선택(2026-11-11)."""
    po = d.get("po") or {}
    if not po.get("no"):
        raise PurchaseError(f"{what}은(는) 발주서를 발행한 뒤에 기록합니다.")
    if not po.get("expected_date") and not flow.overseas(d):
        raise PurchaseError("입고 예정일을 먼저 적어 주세요 — 발주 단계에서 꼭 기록해야 다음 단계로 넘어갑니다.")


def _need_items(d: dict, what: str) -> None:
    """판독 실패로 품목 없이(덜 채워) 발행한 발주서는 품목·금액을 채운 뒤에 송금·입고를 기록한다(QA 2026-11-16) — 금액 0 으로
    송금 확인하면 그 뒤 [발주서 고치기]가 '송금 확인한 건이라 금액을 못 바꿈'에 막히고, 입고는 품목 줄이 없어 수량·S/N 을 못 적는다."""
    if ((d.get("po") or {}).get("forced")):
        raise PurchaseError(f"품목이 확인되지 않은 발주서입니다(판독 실패로 파일만 발행). [발주서 고치기]에서 품목·금액을 채워 다시 발행한 뒤 "
                            f"{what}을(를) 기록해 주세요.", 409)


def _check_kind(d: dict, kind: str) -> None:
    """국내 = 결제 조건·거래명세서·세금계산서, 해외 = 제조사 송금 확인증·통관 서류·관세사 송금. 배송·입고는 둘 다."""
    over = flow.overseas(d)
    if kind in ("statement", "tax", "terms") and over:
        raise PurchaseError("해외 구매에는 없는 단계입니다(해외: 제조사 송금 → 배송 → 통관 서류 → 관세사 송금 → 입고).")
    if kind in OVERSEAS_KINDS and not over:
        raise PurchaseError("국내 구매에는 없는 단계입니다.")


# ── 건 만들기·고치기 ─────────────────────────────────────────────────────────────

async def create(db: AsyncSession, user_id: int | None, p: dict) -> int:
    supplier = _clean(p.get("supplier"), 200)
    if not supplier:
        raise PurchaseError("공급사를 적어 주세요.")
    region = p.get("region") or "domestic"
    if region not in REGIONS:
        raise PurchaseError("국내·해외 중에서 골라 주세요.")
    pid = (await db.execute(text("INSERT INTO purchases (supplier, title, owner, note, reason, region, created_by) "
                                 "VALUES (:s, :t, :o, :n, :why, :r, :u) RETURNING id"),
                            {"s": supplier, "t": _clean(p.get("title"), 300), "o": _clean(p.get("owner"), 100),
                             "n": str(p.get("note") or "").strip()[:2000], "why": _reason(p.get("reason")),
                             "r": region, "u": user_id})).scalar_one()
    await _log(db, pid, user_id, "create", {"supplier": supplier, "title": _clean(p.get("title"), 300), "region": REGIONS[region]})
    await db.commit()
    return pid


REASON_MAX = 1000


def _reason(v: Any) -> str:
    """구매 사유 — 줄바꿈은 살리고 앞뒤 빈칸만 정리."""
    return str(v or "").strip()[:REASON_MAX]


async def set_reason(db: AsyncSession, pid: int, user_id: int | None, reason: str) -> None:
    """구매 사유 등록·고치기(사용자 2026-11-17 — 목록 [등록] 팝업). 영업팀 누구나, 이력에 남는다. 취소한 건도 적을 수 있다."""
    d = await _lock(db, pid)
    new = _reason(reason)
    if (d.get("reason") or "") == new:
        return
    await _save(db, pid, reason=new)
    await _log(db, pid, user_id, "reason", {"reason": new, "before": d.get("reason") or ""})
    await db.commit()


async def set_info(db: AsyncSession, pid: int, user_id: int | None, p: dict) -> None:
    d = await _lock(db, pid)
    supplier = _clean(p.get("supplier"), 200)
    if not supplier:
        raise PurchaseError("공급사를 적어 주세요.")
    new = {"supplier": supplier, "title": _clean(p.get("title"), 300), "owner": _clean(p.get("owner"), 100),
           "note": str(p.get("note") or "").strip()[:2000], "region": p.get("region") or d["region"]}
    if p.get("reason") is not None:                       # 기본 정보 수정에서 구매 사유도(사용자 2026-11-18 — 등록 때와 같은 항목)
        new["reason"] = str(p["reason"]).strip()[:1000]
    if new["region"] not in REGIONS:
        raise PurchaseError("국내·해외 중에서 골라 주세요.")
    if new["region"] != d["region"] and (d.get("po") or {}).get("no"):
        raise PurchaseError("발주서를 발행한 건은 국내·해외를 바꿀 수 없습니다(발주서 양식·통화가 달라짐).", 409)
    changed = {k: v for k, v in new.items() if d.get(k) != v}
    if not changed:
        return
    await _save(db, pid, **new)
    await _log(db, pid, user_id, "info", changed)
    await db.commit()


async def set_dropped(db: AsyncSession, pid: int, user_id: int | None, dropped: bool, reason: str) -> None:
    d = await _lock(db, pid)
    if d["dropped"] == dropped:
        return
    await _save(db, pid, dropped=dropped, drop_reason=_clean(reason, 500) if dropped else "")
    await _log(db, pid, user_id, "drop" if dropped else "undrop", {"reason": _clean(reason, 500)} if dropped else {})
    await db.commit()


async def delete(db: AsyncSession, ids: list[int]) -> int:
    """관리자 — 구매 건 지우기(이력도 함께). 발행한 발주서 번호(지금 판·예전 판)는 다시 쓸 수 있게 놓아 준다(사용자 2026-11-18). 지운 개수."""
    rows = (await db.execute(text("SELECT po FROM purchases WHERE id = ANY(:ids)"), {"ids": ids})).scalars().all()
    nos: list[str | None] = []
    for po in rows:
        po = json.loads(po) if isinstance(po, str) else (po or {})
        nos += [po.get("no"), *[h.get("no") for h in po.get("history") or []]]
    await dn.release(db, nos)
    await stock.purchases_removed(db, ids)            # 자체 개발 제품의 S/N 줄(사용자 2026-11-19)
    n = (await db.execute(text("DELETE FROM purchases WHERE id = ANY(:ids)"), {"ids": ids})).rowcount
    await db.commit()
    return n or 0


# ── 파일 ─────────────────────────────────────────────────────────────────────

async def add_file(db: AsyncSession, pid: int, user_id: int | None, kind: str, entry: dict, label: str = "") -> None:
    """파일 기록 추가(파일은 호출부가 저장소에 먼저 올린다). 견적서는 '교체' — 마지막 파일이 지금 견적서, 앞 파일은 이전 견적서.
    통관 기타 서류는 서류 이름(label: CI·Packing List 등)을 같이 남긴다."""
    if kind not in FILE_KINDS:
        raise PurchaseError("붙일 수 없는 파일 종류입니다.")
    d = await _lock(db, pid)
    _alive(d)
    if kind == "quote":
        await _save(db, pid, quote_files=(d.get("quote_files") or []) + [entry])
    else:
        if kind == "approval" and not flow.quote_done(d):
            raise PurchaseError("견적서를 먼저 올리거나 [견적서 없이 넘어가기]를 눌러 주세요.")
        if kind != "approval":
            _check_kind(d, kind)
            _need_order(d, FILE_KINDS[kind])
        if kind == "customs_etc":
            label = _clean(label, 40)
            if not label:
                raise PurchaseError("서류 이름을 적어 주세요(예: CI, Packing List).")
            entry = {**entry, "label": label}
        col, key = _FILE_COL[kind]
        obj = d.get(col) or {}
        await _save(db, pid, **{col: {**obj, key: (obj.get(key) or []) + [entry]}})
    await _log(db, pid, user_id, f"file_{kind}", {"filename": entry["filename"], "size": entry["size"],
                                                  **({"서류": entry["label"]} if entry.get("label") else {}),
                                                  **({"replaced": True} if kind == "quote" and d.get("quote_files") else {})})
    await db.commit()


def files_of(d: dict, kind: str) -> list[dict]:
    if kind == "quote":
        return d.get("quote_files") or []
    if kind in _FILE_COL:
        col, key = _FILE_COL[kind]
        return (d.get(col) or {}).get(key) or []
    raise PurchaseError("파일 종류가 틀렸습니다.", 404)


async def get_file(db: AsyncSession, pid: int, kind: str, index: int) -> dict:
    if kind == "expense":
        ts = (await raw(db, pid)).get("pay_terms") or []
        f = (ts[index].get("check") or {}).get("file") if 0 <= index < len(ts) else None
        if not f:
            raise PurchaseError("지출내역 파일을 찾을 수 없습니다.", 404)
        return f
    files = files_of(await raw(db, pid), kind)
    if not 0 <= index < len(files):
        raise PurchaseError("파일을 찾을 수 없습니다.", 404)
    return files[index]


async def remove_file(db: AsyncSession, pid: int, user_id: int | None, kind: str, index: int, *, admin: bool,
                      expect: dict | None = None) -> dict:
    """잘못 올린 첨부 한 개 지우기. 끝난 단계(재가 완료·확인 끝)의 파일과 견적서(교체만 — 지우기는 관리자)는 관리자만.
    expect = 화면에서 본 그 파일(filename·uploaded_at) — 다르면 409(_same_entry)."""
    d = await _lock(db, pid)
    _alive(d)                                         # 취소한 건은 되살린 뒤에(QA 2026-11-16)
    files = files_of(d, kind)
    if not 0 <= index < len(files):
        raise PurchaseError("이미 지워졌거나 없는 파일입니다.", 404)
    _same_entry(files, index, expect)
    # 끝난 단계의 첨부 — 재가 완료·통관 서류 제출 완료·제조사 송금 확인 뒤(명세서·계산서는 선택 서류라 누구나 고칠 수 있다)
    done = (kind == "quote" or (kind == "approval" and bool((d.get("approval") or {}).get("done_at")))
            or (kind in ("customs", "customs_etc") and bool((d.get("customs") or {}).get("done_at")))
            or (kind == "remit" and flow.done_map(d)["pay"]))
    if done and not admin:
        raise PurchaseError("견적서·확인을 마친 단계의 첨부는 관리자만 지울 수 있습니다. 견적서는 새 파일을 올리면 교체됩니다.", 403)
    removed = files.pop(index)
    if kind == "quote":
        await _save(db, pid, quote_files=files)
    else:
        col, key = _FILE_COL[kind]
        await _save(db, pid, **{col: {**(d.get(col) or {}), key: files}})
    await _log(db, pid, user_id, "remove_file", {"kind": FILE_KINDS[kind], "filename": removed.get("filename")})
    await db.commit()
    return removed


# ── 재가 ─────────────────────────────────────────────────────────────────────

async def approve(db: AsyncSession, pid: int, user_id: int | None, user_name: str, day: dt.date) -> None:
    """[재가 완료] — 재가 서류 첨부가 없어도 된다(사용자 2026-11-11: 첨부 없이도 넘어가게). 첨부 없이 눌렀으면 no_file 로 남긴다."""
    d = await _lock(db, pid)
    _alive(d)
    if not flow.quote_done(d):
        raise PurchaseError("견적서를 먼저 올리거나 [견적서 없이 넘어가기]를 눌러 주세요.")
    ap = d.get("approval") or {}
    if ap.get("done_at"):
        raise PurchaseError("이미 재가를 마쳤습니다.", 409)
    no_file = not ap.get("files")
    await _save(db, pid, approval={**ap, "date": day.isoformat(), "done_at": _now(), "done_by": user_name, "no_file": no_file})
    await _log(db, pid, user_id, "approve", {"date": day.isoformat(), **({"첨부": "없이"} if no_file else {})})
    await db.commit()


async def skip_quote(db: AsyncSession, pid: int, user_id: int | None, user_name: str, skip: bool) -> None:
    """[견적서 없이 넘어가기](사용자 2026-11-11) / 되돌리기(재가 완료 전까지). 나중에 견적서를 올려도 된다."""
    d = await _lock(db, pid)
    _alive(d)
    ap = d.get("approval") or {}
    if bool(ap.get("quote_skip")) == skip:
        return
    if not skip and ap.get("done_at"):
        raise PurchaseError("재가를 마친 건은 되돌릴 수 없습니다. 견적서는 지금 올려도 됩니다.", 409)
    new = {**ap, "quote_skip": {"by": user_name, "at": _now()}} if skip else {k: v for k, v in ap.items() if k != "quote_skip"}
    await _save(db, pid, approval=new)
    await _log(db, pid, user_id, "quote_skip" if skip else "quote_skip_undo", {})
    await db.commit()


async def unapprove(db: AsyncSession, pid: int, user_id: int | None) -> None:
    """관리자 — 재가 취소(발주서를 발행했으면 안 됨)."""
    d = await _lock(db, pid)
    _alive(d)                                         # 취소한 건은 되살린 뒤에(QA 2026-11-16)
    if (d.get("po") or {}).get("no"):
        raise PurchaseError("발주서를 발행한 건은 재가를 취소할 수 없습니다.", 409)
    ap = {k: v for k, v in (d.get("approval") or {}).items() if k not in ("date", "done_at", "done_by", "no_file")}
    await _save(db, pid, approval=ap)
    await _log(db, pid, user_id, "unapprove", {})
    await db.commit()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ── 발주서 ───────────────────────────────────────────────────────────────────

def _lang(d: dict) -> str:
    return "en" if flow.overseas(d) else "ko"


def _date(v: Any, what: str) -> str:
    t = str(v or "").strip()[:10]
    try:
        dt.date.fromisoformat(t)
    except ValueError as e:
        raise PurchaseError(f"{what}은(는) 날짜로 적어 주세요.") from e
    return t


def clean_po(p: dict, lang: str = "ko") -> tuple[dict, list[dict]]:
    """화면에서 받은 발주서 머리·품목 정리(길이 제한·숫자). 검사(빠진 값)는 px.check. 해외는 Swift Code·공급사 Tel·품목 Remark 도."""
    h = p.get("header") or {}
    s, i = h.get("supplier") or {}, h.get("issuer") or {}
    header = {"lang": lang, "date": _date(h.get("date") or kst_today().isoformat(), "발주일"),
              "supplier": {"name": _clean(s.get("name"), 120), "contact": _clean(s.get("contact"), 80),
                           "email": _clean(s.get("email"), 120), "tel": _clean(s.get("tel"), 60)},
              "bank": _clean(h.get("bank"), 120), "address": str(h.get("address") or "").strip()[:200],
              "swift": _clean(h.get("swift"), 40),
              "account_no": _clean(h.get("account_no"), 60), "account_holder": _clean(h.get("account_holder"), 80),
              "issuer": {k: _clean(i.get(k), 160) for k in ("name", "title", "address", "tel", "fax", "attn")},
              "comments": str(h.get("comments") or "").strip()[:600],
              # 국내 발주서 금액 VAT 포함/별도(사용자 2026-11-20) — 해외는 늘 별도(표기 없음)
              "vat_included": bool(h.get("vat_included")) and lang == "ko"}
    return header, clean_items(p.get("items"))


def clean_items(raw_items: Any) -> list[dict]:
    """발주서 품목 정리 — Commodity(모델명)·Description(설명)·MOQ·수량·단가(사용자 2026-11-11: 두 칸으로 나눔)."""
    items = []
    for it in (raw_items or [])[: px.MAX_ITEMS + 1]:
        if not isinstance(it, dict):
            continue
        com = str(it.get("commodity") or "").strip()[:120]
        desc = "\n".join(x.rstrip() for x in str(it.get("description") or "").strip().splitlines())[:600]
        if not com and not desc and _num(it.get("qty")) is None and _num(it.get("price")) is None:
            continue                                   # 완전히 빈 줄은 버린다
        items.append({"commodity": com, "description": desc, "moq": _num(it.get("moq")), "qty": _num(it.get("qty")),
                      "price": _num(it.get("price")), "remark": str(it.get("remark") or "").strip()[:200]})
    return items


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


PERSON_KEYS = ("name", "title", "tel", "attn")      # 우리 담당 중 사람마다 다른 칸(주소·Fax 는 회사 값)


def profile_issuer(lang: str, profile: dict) -> dict:
    """내 정보(계정 기본 정보) → 발주서 '우리 담당' 칸. 해외는 영문 이름·호칭·'Mr. Alex Jeon (email)', 국내는 한글 이름·직함·'Lucas (email)'."""
    from ..company_knowledge.product_prices import QUOTE_ISSUER, QUOTE_ISSUER_EN
    from ..company_knowledge.quote_xlsx import intl_mobile

    email = str(profile.get("email") or "").strip()
    name_en = str(profile.get("name_en") or "").strip()
    first = str(profile.get("name_en_first") or "").strip() or name_en.split(" ")[0]
    if lang == "en":
        who = " ".join(x for x in (str(profile.get("honorific") or "").strip(), name_en) if x)
        return {"name": name_en or str(profile.get("name_ko") or ""), "title": str(profile.get("honorific") or ""),
                "address": QUOTE_ISSUER_EN.get("address") or "", "tel": intl_mobile(profile.get("mobile") or ""), "fax": "",
                "attn": f"{who} ({email})" if who and email else who or email}
    return {"name": profile.get("name_ko") or "", "title": profile.get("title") or "",
            "address": QUOTE_ISSUER.get("address") or "", "tel": intl_mobile(profile.get("mobile") or ""),
            "fax": px.DEFAULT_FAX, "attn": f"{first} ({email})" if first and email else first or email}


def _fill_person(issuer: dict, mine: dict) -> dict:
    """저장된 '우리 담당'에서 비어 있는 사람 칸(이름·직함·Tel·ATTN)만 내 정보로 채운다 — 적어 둔 값은 그대로(사용자 2026-11-11)."""
    return {**issuer, **{k: mine[k] for k in PERSON_KEYS if not str(issuer.get(k) or "").strip() and mine.get(k)}}


async def po_draft(db: AsyncSession, pid: int, profile: dict) -> dict:
    """발주서 작성 창에 채울 값 — 저장한 초안 → 마지막 발행본 → 기본값(공급사·내 계정 정보·회사 주소).
    해외는 영문 회사 주소·영문 이름·'Mr. Alex Jeon (email)'(해외 발주서 양식 표기).
    내 정보(사용자 2026-11-11): 새로 쓸 때는 우리 담당·이니셜을 내 정보로, 초안·발행본은 비어 있는 칸만 내 정보로 채운다(고칠 수 있음).
    mine = 내 정보로 만든 우리 담당·이니셜 — 비어 있으면 화면이 [내 정보] 넣는 곳을 안내한다(버튼 없이 자동, 사용자 2026-11-11)."""
    d = await raw(db, pid)
    po = d.get("po") or {}
    lang = _lang(d)
    mine = {"issuer": profile_issuer(lang, profile), "initials": dn.clean_initials(profile.get("initials")) or ""}
    # 발주서 AI 대화(사용자 2026-11-11) — 발행해도 이어서 보이게 po.chat 에 둔다
    base = {"region": d["region"], "lang": lang, "currency": CURRENCY[d["region"]], "chat": po.get("chat") or [], "mine": mine}
    if po.get("draft") and (po["draft"].get("header") or {}).get("lang", "ko") == lang:
        dr = po["draft"]
        hd = dr.get("header") or {}
        return {**base, **dr, "header": {**hd, "issuer": _fill_person(hd.get("issuer") or {}, mine["issuer"])}, "source": "draft",
                "initials": dr.get("initials") or mine["initials"],
                "expected_date": dr.get("expected_date") or po.get("expected_date") or ""}
    if po.get("no") and (po.get("header") or {}).get("lang", "ko") == lang:
        hd = po["header"]
        return {**base, "header": {**hd, "date": kst_today().isoformat(), "issuer": _fill_person(hd.get("issuer") or {}, mine["issuer"])},
                "items": po.get("items") or [], "source": "issued",
                "initials": mine["initials"] or po.get("initials") or "", "expected_date": po.get("expected_date") or ""}
    header = {"lang": lang, "date": kst_today().isoformat(), "supplier": {"name": d["supplier"], "contact": "", "email": "", "tel": ""},
              "bank": "", "address": "", "swift": "", "account_no": "", "account_holder": "", "issuer": dict(mine["issuer"]), "comments": ""}
    return {**base, "header": header, "items": [], "source": "new", "initials": mine["initials"],
            "expected_date": po.get("expected_date") or ""}


async def save_po_draft(db: AsyncSession, pid: int, user_id: int | None, p: dict, chat: list[dict] | None = None) -> None:
    """작성 중 발주서 저장(발행 전 — 번호 없음). 이력은 남기지 않는다(발행만 기록). chat = AI 대화(있으면 같이 저장)."""
    d = await _lock(db, pid)
    _alive(d)
    header, items = clean_po(p, _lang(d))
    exp = _date(p["expected_date"], "입고 예정일") if str(p.get("expected_date") or "").strip() else ""
    po = d.get("po") or {}
    extra = {"chat": [{"role": str(m.get("role")), "text": str(m.get("text") or "")[:2000]} for m in chat][-CHAT_MAX:]} if chat is not None else {}
    await _save(db, pid, po={**po, **extra, "draft": {"header": header, "items": items, "initials": _clean(p.get("initials"), 4).upper(),
                                                      "expected_date": exp, "saved_at": _now()}})
    await db.commit()


def _name_key(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().upper()


def _reopen_check(d: dict, rc: dict, items: list[dict], ok: bool) -> list[str]:
    """입고를 마친 건을 이 품목으로 다시 발행하면 입고가 다시 열리는 줄(품목 추가·수량 늘림, 사용자 2026-11-16) — 담당자 확인(ok) 없이는
    409 + reopen 목록(화면 팝업). 단가·설명만 바뀌면 입고는 그대로. 돌려주는 값은 변경 이력 문구.
    rc = 새 품목 줄로 다시 짝지은 입고 기록(_keep_received_lines)."""
    if not flow.receipt_done(d):
        return []
    po = d.get("po") or {}
    nd = {**d, "receipt": rc, "po": {**po, "items": items}}
    got = flow.received(nd)
    olds = {_name_key(px.item_name(x)) for x in po.get("items") or []}
    # 예전 발주서에 없던 품목이면 '추가', 있던 품목이면 '수량 늘어남'
    rows = [{"name": px.item_name(items[i]), "got": got.get(i, 0), "ordered": float(items[i].get("qty") or 0),
             "kind": "수량 늘어남" if _name_key(px.item_name(items[i])) in olds else "추가"} for i in flow.receipt_open(nd)]
    if rows and not ok:
        raise PurchaseError("이 발주서로 발행하면 입고가 다시 진행됩니다: "
                            + ", ".join(f"{r['name']} {r['got']:g}/{r['ordered']:g}개({r['kind']})" for r in rows), 409, {"reopen": rows})
    return [f"{r['name']} {r['got']:g}/{r['ordered']:g}개({r['kind']})" for r in rows]


def _keep_received_lines(d: dict, items: list[dict]) -> dict:
    """입고 기록은 발주 품목을 '몇 번째 줄'(line_no)로 가리킨다 — 다시 발행할 때 줄 순서·구성이 바뀌면 입고 수량이 엉뚱한 품목에
    붙었다(QA 2026-11-16). 품목 이름(대소문자·띄어쓰기 무시)으로 새 줄에 다시 짝지은 입고 기록을 돌려준다(같은 이름이 여러 줄이면
    앞에서부터) — 안 받은 품목은 어디에 있든 빼거나 순서를 바꿀 수 있다(사용자 2026-11-16). [입고 완료]로 닫은 줄의 기억도 옮긴다.
    받은 품목을 빼거나 이름을 바꾸는 것, 받은 수량보다 적게 발주하는 것은 막는다(입고 기록을 먼저 고치면 된다)."""
    rc = dict(d.get("receipt") or {})
    recs = rc.get("records") or []
    if not recs:
        return rc
    free: dict[str, list[int]] = {}
    for i, it in enumerate(items):
        free.setdefault(_name_key(px.item_name(it)), []).append(i)
    mapping: dict[int, int] = {}
    missing: list[str] = []
    for r in recs:
        for ln in r.get("lines") or []:
            i = ln.get("line_no")
            if not isinstance(i, int) or i in mapping:
                continue
            name = str(ln.get("name") or "").strip()
            cand = free.get(_name_key(name)) or []
            if cand:
                mapping[i] = cand.pop(0)
            elif name not in missing:
                missing.append(name or f"{i + 1}번째 줄")
    if missing:
        raise PurchaseError(f"입고 기록이 있는 품목({', '.join(missing)})은 빼거나 이름을 바꿀 수 없습니다. 이름은 그대로 두고 고쳐 주세요"
                            "(꼭 빼야 하면 그 품목의 입고 기록을 먼저 고치거나 지움).", 409,
                            {"code": "RECEIPT_LOCK"})    # 화면: 팝업 → [입고 칸으로 가기](사용자 2026-11-17)
    names = {i: _name_key(px.item_name(it)) for i, it in enumerate(items)}
    out = []
    for r in recs:
        new = {**r, "lines": [{**ln, "line_no": mapping.get(ln.get("line_no"), ln.get("line_no"))} for ln in r.get("lines") or []]}
        if isinstance(r.get("closed"), dict):            # [입고 완료]로 닫은 줄 — 이름이 같은 새 줄로(빠진 품목은 버림, 이름 없는 예전 꼴은 그대로)
            closed: dict[str, Any] = {}
            for k, v in r["closed"].items():
                if not isinstance(v, dict):
                    closed[k] = v
                    continue
                j = mapping.get(int(k))
                if j is None or names.get(j) != _name_key(v.get("name")):
                    j = next((x for x, nm in names.items() if nm == _name_key(v.get("name")) and str(x) not in closed), None)
                if j is not None:
                    closed[str(j)] = v
            new["closed"] = closed
        out.append(new)
    rc["records"] = out
    # 받은 수량보다 적게 발주하면 기록이 실제와 어긋난다(반품한 S/N 이 입고품으로 남음) — 입고 기록부터 고치게 한다(사용자 2026-11-16)
    short = [f"{px.item_name(items[i])}: 입고 {got:g}개 → 발주 {float(items[i].get('qty') or 0):g}개"
             for i, got in sorted(flow.received({"receipt": rc}).items())
             if 0 <= i < len(items) and float(items[i].get("qty") or 0) < got]
    if short:
        raise PurchaseError(f"이미 입고한 수량보다 적게 발주할 수 없습니다({', '.join(short)}). "
                            "반품했거나 잘못 적었다면 입고 기록의 수량(S/N 포함)을 먼저 고친 뒤 발행해 주세요.", 409,
                            {"code": "RECEIPT_LOCK"})
    return rc


async def issue_po(db: AsyncSession, pid: int, user_id: int | None, user_name: str, p: dict) -> dict:
    """발주서 발행 — 새 번호 PO-{이니셜}{발주일}-NN(발행·재발행마다 새 번호). 다시 발행하면 예전 판은 history 로.
    입고 예정일은 국내만 꼭(사용자 2026-11-02), 해외는 납기일로 선택(2026-11-11)."""
    d = await _lock(db, pid)
    _alive(d)
    lang = _lang(d)
    header, items = clean_po(p, lang)
    ini = dn.clean_initials(p.get("initials"))
    if not ini:
        raise PurchaseError("발행자 이니셜은 영문 2자로 적어 주세요(예: LU).")
    exp = _expected(d, p.get("expected_date"), header["date"])
    miss = px.check(header, items)
    if miss:
        raise PurchaseError("발주서에 꼭 필요한 값이 비어 있습니다: " + ", ".join(miss))
    if not (d.get("approval") or {}).get("done_at"):
        raise PurchaseError("재가를 마친 뒤 발주서를 발행합니다.")
    rc = _keep_received_lines(d, items)
    reopen = _reopen_check(d, rc, items, bool(p.get("reopen_ok")))
    old = d.get("po") or {}
    cur = CURRENCY[d["region"]]
    paid = any(t["confirmed"] for t in flow.terms(d)) or any(x.get("simple") for x in d.get("payments") or [])
    if old.get("no") and paid and (abs(px.total(items, cur) - float(old.get("total") or 0)) > 0.005
                                   or bool(header.get("vat_included")) != flow.vat_included(d)):
        raise PurchaseError("송금 확인을 한 건이라 발주 금액을 바꿀 수 없습니다. 송금 칸의 [확인 취소]로 송금 확인을 먼저 취소해 주세요.", 409,
                            {"code": "PAID_LOCK"})       # 화면: 팝업 → [송금 칸으로 가기]
    day = dt.date.fromisoformat(header["date"])
    no = await dn.allocate(db, dn.PURCHASE, ini, day, user_id=user_id)
    header["no"] = no
    rev = int(old.get("revision") or 0) + 1
    hist = list(old.get("history") or [])
    if old.get("no"):
        # 올린 파일로 발행한 판이면 그 파일도 같이 남긴다 — 예전 판 [다시 다운로드]가 올린 파일 그대로 나오게(2026-11-11 발견)
        hist.append({k: old.get(k) for k in ("no", "date", "total", "issued_at", "issued_by", "revision", "header", "items",
                                              "file_name", "currency", "upload", "replaced")})
    exp_hist = list(old.get("expected_history") or [])
    if exp and old.get("expected_date") != exp:
        exp_hist.append({"date": exp, "before": old.get("expected_date"), "at": _now(), "by": user_name, "reason": "발주서 발행"})
    po = {"no": no, "date": header["date"], "header": header, "items": items, "total": px.total(items, cur), "currency": cur,
          "revision": rev, "issued_at": _now(), "issued_by": user_name, "initials": ini, "history": hist,
          "expected_date": exp, "expected_history": exp_hist, "chat": old.get("chat") or [],
          "file_name": px.file_name(header["supplier"]["name"], no, lang)}
    await _save(db, pid, po=po, **({"receipt": rc} if rc.get("records") else {}))   # 새 품목 줄로 짝지은 입고 기록
    await _log(db, pid, user_id, "po_issue", {"no": no, "total": po["total"], "currency": cur, "revision": rev,
                                              **({"납기일" if flow.overseas(d) else "입고 예정일": exp} if exp else {}),
                                              **({"입고 다시 진행": reopen} if reopen else {})})
    await db.commit()
    return po


# 발주서 발행 취소를 막는 뒤 단계 — 그 단계부터 되돌린다(뒤에서 앞으로). 화면은 팝업 + [그 칸으로 가기]
_AFTER_PO = ("receipt", "broker", "customs", "ship", "pay")


async def cancel_po(db: AsyncSession, pid: int, user_id: int | None, user_name: str) -> None:
    """발주서 발행 취소(사용자 2026-11-18 — 잘못됐을 때 영업팀 누구나 처음 단계까지 거슬러 다시 작성). 마지막 발행을 없던 일로:
    예전 판이 있으면 그 판이 다시 지금 발주서, 없으면 발주 전(내용은 작성 중 초안으로 돌려 고쳐 다시 발행). 취소한 번호는 다시 쓴다
    (오늘 01 을 취소하고 다시 내면 01 — 첫 이력으로). 취소 기록은 변경 이력(po_cancel)에만 남는다.
    송금·배송·통관·관세사·입고 기록이 남아 있으면 그것부터 되돌리게 막는다(뒤 단계가 발주서 금액·품목에 묶여 있음)."""
    d = await _lock(db, pid)
    _alive(d)
    po = d.get("po") or {}
    if not po.get("no"):
        raise PurchaseError("발행한 발주서가 없습니다.", 409)
    lab = flow.labels(d)
    cu = d.get("customs") or {}
    left = {"receipt": bool((d.get("receipt") or {}).get("records")),
            "broker": bool((cu.get("broker") or {}).get("date")),
            "customs": bool(cu.get("done_at")),
            "ship": bool(flow.shipping(d).get("date")),
            "pay": any(t["confirmed"] for t in flow.terms(d)) or any(x.get("simple") for x in d.get("payments") or [])}
    todo = [k for k in _AFTER_PO if left[k]]
    if todo:
        raise PurchaseError(f"뒤 단계 기록이 남아 있어 발주서 발행을 취소할 수 없습니다 — 먼저 되돌려 주세요: "
                            f"{', '.join(lab[k] for k in todo)}.", 409, {"code": "PO_LOCKED", "step": todo[0], "label": lab[todo[0]]})
    hist = list(po.get("history") or [])
    keep = {k: po[k] for k in ("expected_date", "expected_history", "chat") if po.get(k) is not None}
    if hist:                                              # 예전 판으로 돌아간다(그 판의 번호·판 그대로)
        new = {**keep, **hist[-1], "history": hist[:-1]}
    else:                                                 # 발주 전 — 내용은 초안으로(판 번호는 0 부터 다시 → 다시 내면 v1)
        header = {k: v for k, v in (po.get("header") or {}).items() if k != "no"}
        new = {**keep, "history": [], "draft": po.get("draft") or {
            "header": header, "items": po.get("items") or [], "initials": po.get("initials") or "",
            "expected_date": po.get("expected_date") or "", "saved_at": _now()}}
    await dn.release(db, [po["no"]])
    await _save(db, pid, po=new)
    await _log(db, pid, user_id, "po_cancel", {"no": po["no"], "total": po.get("total"), "currency": po.get("currency"),
                                               **({"back_to": hist[-1].get("no")} if hist else {})})
    await db.commit()


def _merge_header(cur: dict, read: dict, lang: str) -> dict:
    """발주서 머리 + 고친 파일에서 읽은 값(빈 값은 지금 값 유지) → 길이 정리(clean_po 와 같게)."""
    sup = {**(cur.get("supplier") or {}), **{k: v for k, v in (read.get("supplier") or {}).items() if str(v or "").strip()}}
    top = {k: v for k, v in read.items() if k in ("bank", "address", "swift", "account_no", "account_holder", "comments")
           and str(v or "").strip()}
    merged = {**cur, **top, "supplier": sup}
    if isinstance(read.get("vat_included"), bool):    # 화면에서 고른 값(처음엔 파일에서 읽은 'VAT 포함/별도')
        merged["vat_included"] = read["vat_included"]
    header, _ = clean_po({"header": {**merged, "date": cur.get("date") or kst_today().isoformat()}}, lang)
    return header


def _expected(d: dict, raw_v: Any, order_day: str) -> str:
    """발행할 때의 입고 예정일 — 국내는 꼭(사용자 2026-11-02), 해외는 '납기일'로 선택(2026-11-11). 비우면 예전 값을 이어 쓴다."""
    over = flow.overseas(d)
    what = "납기일" if over else "입고 예정일"
    if not str(raw_v or "").strip():
        if over:
            return (d.get("po") or {}).get("expected_date") or ""
        raise PurchaseError("입고 예정일을 적어 주세요 — 발주 단계에서 꼭 기록해야 다음 단계로 넘어갑니다.")
    exp = _date(raw_v, what)
    # 다시 발행할 때 예전 예정일을 그대로 두면 발주일(보통 오늘)보다 앞이어도 받는다(QA 2026-11-16 — 이미 입고한 건의 단가만 고쳐
    # 다시 내려 해도 막혔음). 새로 적거나 바꾼 예정일만 발주일과 비교한다(영업 건 '그대로 둔 지난 예정일'과 같게)
    if exp < order_day and exp != (d.get("po") or {}).get("expected_date"):
        raise PurchaseError(f"{what}이 발주일보다 앞입니다.")
    return exp


async def _po_no_used(db: AsyncSession, pid: int, no: str) -> str | None:
    """이 번호를 이미 쓴 곳 — 다른 구매 건의 발주서(지금 판·예전 판), 이 건의 예전 판, 견적서·명세서 번호 기록. 없으면 None."""
    n = no.strip().upper()
    row = (await db.execute(text(
        "SELECT id, supplier FROM purchases WHERE (upper(po->>'no') = :n AND id <> :pid) "
        "OR (jsonb_typeof(po->'history') = 'array' AND EXISTS "
        "    (SELECT 1 FROM jsonb_array_elements(po->'history') h WHERE upper(h->>'no') = :n)) LIMIT 1"),
        {"n": n, "pid": pid})).mappings().first()
    if row:
        return f"구매 건 '{row['supplier']}'" + (" 의 예전 발주서" if row["id"] == pid else "")
    hit = await dn.issued_where(db, n)
    return ("견적서·거래명세서 번호 기록" + (f"(영업 건 {hit['deal_no']})" if hit.get("deal_no") else "")) if hit else None


async def upload_po(db: AsyncSession, pid: int, user_id: int | None, user_name: str, entry: dict, p: dict, base: dict) -> dict:
    """고친 발주서 파일로 발행(사용자 2026-11-11 — 견적서 '고친 파일로 발행'과 같게). 회사에 내기 전에 엑셀에서 고친 발주서를
    그대로 남긴다: 내려받기는 그 파일, 품목·금액은 담당자가 확인한 값(송금 대조·입고 수량에 쓰임).
    발주번호는 파일의 Order No. 를 따르고(담당자가 고칠 수 있음), 비우면 새 번호(이니셜). 다른 번호면 새 판(예전 판은 history).
    지금 발주서와 같은 번호면 교체 확인(same_no="replace")을 받고 지금 판을 이 파일로 바꾼다 — 번호 하나 = 발주서 하나(사용자 2026-11-16,
    같은 번호의 판을 둘 만들지 않음). 판·발행 시각은 그대로, 교체 전 파일·품목·금액은 replaced 에 보관(다시 받을 수 있음, 견적서와 같게).
    따로 남기려면 번호를 바꿔 올린다(새 판). 다른 곳에서 쓴 번호는 받지 않는다. base = 발주서 머리 기본값(po_draft)."""
    d = await _lock(db, pid)
    _alive(d)
    if not (d.get("approval") or {}).get("done_at"):
        raise PurchaseError("재가를 마친 뒤 발주서를 발행합니다.")
    items = clean_items(p.get("items"))
    forced = bool(p.get("forced"))                    # [버그 신고 후 넘어가기](사용자 2026-11-11 — AI 판독이 실패하거나 이상해도 [버그 신고 후 넘어가기]로 이 파일 그대로 발행하고 다음 단계로)
    if forced:                                        # 이름도 단가도 없는 줄(판독 실패로 남은 빈 줄)은 버린다
        items = [it for it in items if px.item_name(it) or it.get("price") is not None]
    miss = [] if forced else px.check_items(items)
    if miss:
        raise PurchaseError("발주서 품목에 꼭 필요한 값이 비어 있습니다: " + ", ".join(miss))
    rc = _keep_received_lines(d, items)               # 판독 실패로 품목이 비어도 — 입고 기록이 있으면 품목 줄을 지킨다
    reopen = _reopen_check(d, rc, items, bool(p.get("reopen_ok")))
    day = _date(p.get("date") or kst_today().isoformat(), "발주일")
    exp = _expected(d, p.get("expected_date"), day)
    old = d.get("po") or {}
    cur = CURRENCY[d["region"]]
    paid = any(t["confirmed"] for t in flow.terms(d)) or any(x.get("simple") for x in d.get("payments") or [])
    # 고친 파일 발행 — 화면에서 고른 VAT 포함/별도(안 보냈으면 지금 값). 해외는 늘 별도
    new_vat = (bool((p.get("header") or {}).get("vat_included", flow.vat_included(d)))) and not flow.overseas(d)
    if old.get("no") and paid and (abs(px.total(items, cur) - float(old.get("total") or 0)) > 0.005
                                   or new_vat != flow.vat_included(d)):
        raise PurchaseError("송금 확인을 한 건이라 발주 금액을 바꿀 수 없습니다. 송금 칸의 [확인 취소]로 송금 확인을 먼저 취소해 주세요.", 409,
                            {"code": "PAID_LOCK"})       # 화면: 팝업 → [송금 칸으로 가기]
    given = str(p.get("doc_no") or "").strip()
    no = dn.clean_doc_no(given)
    if given and not no:
        raise PurchaseError("발주번호는 영문·숫자·-_.() 로 40자까지 적어 주세요.")
    same = bool(no and old.get("no") and no.upper() == str(old["no"]).upper())
    if same and p.get("same_no") != "replace":
        raise PurchaseError("지금 발주서와 같은 발주번호입니다. 지금 발주서를 이 파일로 교체할지 확인해 주세요"
                            "(따로 남기려면 발주번호를 바꿔 올려 주세요).", 409)
    if no and not same:
        used = await _po_no_used(db, pid, no)
        if used:
            raise PurchaseError(f"발주번호 {no} 는 이미 {used}에서 쓴 번호입니다. 다른 번호를 적거나 비워 새 번호를 받으세요.", 409)
        await dn.register(db, no, user_id=user_id)
    elif not no:
        ini = dn.clean_initials(p.get("initials"))
        if not ini:
            raise PurchaseError("발주번호를 비우면 새 번호를 붙입니다 — 발행자 이니셜(영문 2자)을 적어 주세요.")
        no = await dn.allocate(db, dn.PURCHASE, ini, dt.date.fromisoformat(day), user_id=user_id)
    else:
        no = str(old["no"])                               # 같은 번호 — 기록된 표기 그대로
    lang = _lang(d)
    # 파일에서 읽은 머리(공급사·계좌·Comments — 예전 양식 포함, 사용자 2026-11-11)를 지금 머리 위에 — [발주서 고치기]에서 그대로 고치게
    header = _merge_header(old.get("header") or base.get("header") or {}, p.get("header") or {}, lang)
    header.update(lang=lang, date=day, no=no)
    hist = list(old.get("history") or [])
    rev = int(old.get("revision") or 0) + (0 if same else 1)
    if old.get("no") and not same:
        hist.append({k: old.get(k) for k in ("no", "date", "total", "issued_at", "issued_by", "revision", "header", "items",
                                              "file_name", "currency", "upload", "replaced")})
    exp_hist = list(old.get("expected_history") or [])
    if exp and old.get("expected_date") != exp:
        exp_hist.append({"date": exp, "before": old.get("expected_date"), "at": _now(), "by": user_name, "reason": "고친 발주서 파일로 발행"})
    stem = re.sub(r"\.(xlsx|pdf)$", "", str(entry.get("filename") or ""), flags=re.I).strip()[:150]
    po = {"no": no, "date": day, "header": header, "items": items, "total": px.total(items, cur), "currency": cur,
          "revision": max(rev, 1), "issued_at": _now(), "issued_by": user_name, "initials": old.get("initials") or "",
          "history": hist, "expected_date": exp, "expected_history": exp_hist, "chat": old.get("chat") or [],
          "file_name": stem or px.file_name(d["supplier"], no, lang), "upload": {k: entry.get(k) for k in FILE_KEYS},
          # 판독 실패로 파일만 발행 — 화면에 '품목은 [발주서 고치기]에서' 안내, 버그 접수 번호
          **({"forced": {"bug_report": _clean(p.get("bug_report"), 20), "at": _now(), "by": user_name}} if forced else {})}
    if same:                                              # 교체 — 판·발행 시각은 그대로, 교체 전 내용은 보관
        keep = {k: old.get(k) for k in ("date", "header", "items", "total", "currency", "file_name", "upload", "issued_at", "issued_by")}
        po.update(issued_at=old.get("issued_at"), issued_by=old.get("issued_by"), replaced_at=_now(), replaced_by=user_name,
                  replaced=[*(old.get("replaced") or []), {**keep, "replaced_at": _now(), "replaced_by": user_name}])
    await _save(db, pid, po=po, **({"receipt": rc} if rc.get("records") else {}))   # 새 품목 줄로 짝지은 입고 기록
    await _log(db, pid, user_id, "po_upload", {"no": no, "filename": entry.get("filename"), "total": po["total"], "currency": cur,
                                               "revision": po["revision"],
                                               **({"같은 번호": "지금 판을 이 파일로 교체(교체 전 파일 보관)",
                                                   "교체 전": f"{(old.get('upload') or {}).get('filename') or '회사 양식 발주서'} · "
                                                              f"{float(old.get('total') or 0):,.2f} {old.get('currency') or cur}".replace(".00 ", " ")}
                                                  if same else {}),
                                               **({"판독 실패로 파일만 발행": po["forced"]["bug_report"] or "버그 신고"} if forced else {}),
                                               **({"입고 다시 진행": reopen} if reopen else {}),
                                               **({"납기일" if flow.overseas(d) else "입고 예정일": exp} if exp else {})})
    await db.commit()
    return po


async def po_source(db: AsyncSession, pid: int, revision: int | None = None, replaced: int | None = None) -> dict:
    """발행한 발주서(지금 판 또는 예전 판)의 기록 — upload 가 있으면 그 파일을, 없으면 머리·품목으로 다시 만든다(api)."""
    po = (await raw(db, pid)).get("po") or {}
    cur = po if po.get("no") else None                    # 발행 취소한 건은 지금 판이 없고 예전 판만(사용자 2026-11-18)
    if revision is None and not cur:
        raise PurchaseError("발행한 발주서가 없습니다.", 404)
    src = cur if revision is None or (cur and revision == po.get("revision"))         else next((h for h in po.get("history") or [] if h.get("revision") == revision), None)
    if not src:
        raise PurchaseError("그 판의 발주서를 찾을 수 없습니다.", 404)
    if replaced is not None:                              # 같은 번호로 교체하기 전 파일(사용자 2026-11-16)
        rs = src.get("replaced") or []
        if not 0 <= replaced < len(rs):
            raise PurchaseError("교체 전 발주서를 찾을 수 없습니다.", 404)
        return {"no": src.get("no"), **rs[replaced]}
    return src


async def set_expected_date(db: AsyncSession, pid: int, user_id: int | None, user_name: str, day: dt.date | None, reason: str) -> None:
    """입고 예정일(해외 = 납기일) 바꾸기(발주서 발행 뒤). 바뀐 기록은 남긴다 — 리스트·상세의 예정일과 지남 알림이 이 값을 따른다.
    해외 납기일은 선택이라 비울 수 있다(day=None, QA 2026-11-16), 국내 입고 예정일은 꼭."""
    d = await _lock(db, pid)
    _alive(d)
    po = d.get("po") or {}
    over = flow.overseas(d)
    what = "납기일" if over else "입고 예정일"
    if not po.get("no"):
        raise PurchaseError(f"{what}은 발주서를 발행한 뒤에 적습니다.")
    if day is None and not over:
        raise PurchaseError("국내 구매의 입고 예정일은 비울 수 없습니다 — 다음 단계로 넘어가려면 꼭 있어야 합니다.")
    new, old = (day.isoformat() if day else None), po.get("expected_date") or None
    if new == old:
        return
    if new and new < str(po.get("date") or new):
        raise PurchaseError(f"{what}이 발주일보다 앞입니다.")
    hist = list(po.get("expected_history") or []) + [{"date": new, "before": old, "at": _now(), "by": user_name,
                                                       "reason": _clean(reason, 200)}]
    await _save(db, pid, po={**po, "expected_date": new, "expected_history": hist})
    await _log(db, pid, user_id, "expected_date", {what: new or "비움", **({"before": old} if old else {}),
                                                   **({"reason": _clean(reason, 200)} if reason.strip() else {})})
    await db.commit()


async def po_file(db: AsyncSession, pid: int, revision: int | None = None, replaced: int | None = None) -> tuple[str, bytes]:
    """발행한 발주서(지금 판·예전 판·교체 전) → (파일 이름, 엑셀 바이트). 저장한 머리·품목으로 같은 파일을 다시 만든다."""
    src = await po_source(db, pid, revision, replaced)
    lang = (src.get("header") or {}).get("lang", "ko")
    return (src.get("file_name") or px.file_name(src["header"]["supplier"]["name"], src["no"], lang),
            px.build_xlsx(src["header"], src["items"]))


# ── 송금(결제 조건 · 지출내역 대조 · 확인) ─────────────────────────────────────────

async def set_terms(db: AsyncSession, pid: int, user_id: int | None, raw_terms: list[dict], *, admin: bool) -> None:
    terms = flow.clean_terms(raw_terms)
    d = await _lock(db, pid)
    _alive(d)
    _check_kind(d, "terms")
    _need_order(d, "결제 조건")
    old = d.get("pay_terms") or []
    # 송금 확인한 회차의 구분·비율·금액은 누구도 못 바꾼다(QA 2026-11-16 — 관리자가 바꾸면 확인 84 인데 화면엔 140 이 '확인'으로 보였음).
    # 확인 안 한 회차·[확인 취소]로 되돌린 회차는 누구나 바꾼다(사용자 2026-11-16), 예정일은 확인한 회차도 바꿀 수 있다.
    # 영업 건 결제 조건과 같은 규칙 + 금액 비교. admin 은 API 호환으로 받기만 한다(예전: 직원은 확인 회차가 하나라도 있으면 전부 막음)
    new_amt = {t["index"]: t["amount"] for t in flow.terms({**d, "pay_terms": terms})}
    for p in d.get("payments") or []:
        i = p.get("term")
        if i is None:
            continue
        o = old[i] if i < len(old) else {}
        if i >= len(terms) or terms[i]["pct"] != o.get("pct") or terms[i]["label"] != o.get("label") or new_amt[i] != p.get("amount"):
            raise PurchaseError(f"송금 확인한 회차({o.get('label') or f'{i + 1}회차'} {float(p.get('amount') or 0):,.0f}원)의 구분·비율·금액이 "
                                "바뀌는 변경입니다. 그 회차의 [확인 취소]를 먼저 해 주세요(예정일은 바꿀 수 있음).", 409)
    # 대조 결과는 같은 자리·같은 비율일 때만 이어 둔다(금액이 바뀌면 다시 대조)
    for i, t in enumerate(terms):
        if i < len(old) and old[i].get("pct") == t["pct"] and old[i].get("check"):
            t["check"] = old[i]["check"]
    pays = [p for p in d.get("payments") or [] if p.get("term", 0) < len(terms)]
    await _save(db, pid, pay_terms=terms, payments=pays)
    await _log(db, pid, user_id, "terms", {"terms": [f"{t['label']} {t['pct']:g}%" for t in terms]})
    await db.commit()


def term_at(d: dict, index: int) -> dict:
    ts = flow.terms(d)
    if not 0 <= index < len(ts):
        raise PurchaseError("결제 회차를 찾을 수 없습니다.", 404)
    return ts[index]


async def set_check(db: AsyncSession, pid: int, user_id: int | None, index: int, result: dict, file_entry: dict) -> None:
    """지출내역 대조 결과를 회차에 저장(대조하는 동안 결제 조건이 바뀌었으면 저장하지 않는다)."""
    d = await _lock(db, pid)
    _alive(d)                                         # 취소한 건은 되살린 뒤에(QA 2026-11-16)
    ts = d.get("pay_terms") or []
    if not 0 <= index < len(ts):
        raise PurchaseError("대조하는 동안 결제 조건이 바뀌었습니다. 다시 해 주세요.", 409)
    t = term_at(d, index)
    if abs(float((result.get("expected") or {}).get("supply", t["amount"])) - t["amount"]) > 0.5:
        raise PurchaseError("대조하는 동안 회차 금액이 바뀌었습니다. 다시 해 주세요.", 409)
    ts[index] = {**ts[index], "check": {**result, "file": file_entry}}
    await _save(db, pid, pay_terms=ts)
    await _log(db, pid, user_id, "pay_check", {"term": f"{t['label']} {t['pct']:g}%", "status": result.get("status"),
                                                "summary": result.get("summary"), "filename": file_entry.get("filename")})
    await db.commit()


async def confirm_term(db: AsyncSession, pid: int, user_id: int | None, user_name: str, index: int, day: dt.date,
                       mismatch_note: str | None) -> None:
    """회차 송금 확인 — [송금 확인] 버튼(사용자 2026-11-11: 국내 건은 지출내역 자료가 없어 대조 없이, 영업 건 입금 확인과 같게).
    예전에 지출내역을 대조해 불일치였던 회차는 그 기록을 같이 남긴다(사유가 있으면)."""
    d = await _lock(db, pid)
    _alive(d)
    _need_order(d, "송금")
    _need_items(d, "송금 확인")
    t = term_at(d, index)
    if t["confirmed"]:
        raise PurchaseError("이미 송금 확인한 회차입니다.", 409)
    if any(p.get("simple") for p in d.get("payments") or []):
        raise PurchaseError("이미 송금 확인(한 번에)으로 기록한 건입니다.", 409)
    chk = t.get("check") or {}
    note = _clean(mismatch_note, 500)
    mismatch = bool(chk) and chk.get("status") != "ok" and bool(note)
    entry = {"term": index, "date": day.isoformat(), "amount": t["amount"], "by": user_name, "at": _now()}
    if chk:
        entry.update(check_status=chk.get("status"), check_summary=chk.get("summary"))
    if mismatch:
        entry.update(mismatch=True, mismatch_note=note)
    await _save(db, pid, payments=(d.get("payments") or []) + [entry])
    await _log(db, pid, user_id, "pay_confirm", {"term": f"{t['label']} {t['pct']:g}%", "amount": t["amount"], "date": day.isoformat(),
                                                  **({"mismatch": note} if mismatch else {})})
    await db.commit()


async def unconfirm_term(db: AsyncSession, pid: int, user_id: int | None, index: int) -> None:
    """회차 송금 확인 취소(누구나 — 사용자 2026-11-11, 금액이 바뀔 수 있어서). 이력에 남는다."""
    d = await _lock(db, pid)
    _alive(d)                                         # 취소한 건은 되살린 뒤에(QA 2026-11-16)
    t = term_at(d, index)
    if not t["confirmed"]:
        raise PurchaseError("송금 확인하지 않은 회차입니다.", 404)
    pays = [p for p in d.get("payments") or [] if p.get("term") != index]
    await _save(db, pid, payments=pays)
    await _log(db, pid, user_id, "pay_unconfirm", {"term": f"{t['label']} {t['pct']:g}%"})
    await db.commit()


# ── 해외 송금 · 통관 ─────────────────────────────────────────────────────────────

async def confirm_paid(db: AsyncSession, pid: int, user_id: int | None, user_name: str, day: dt.date, note: str) -> None:
    """송금 버튼 한 번 — 해외 [제조사 송금 확인](사용자 2026-11-11, 송금 확인증 첨부는 선택). 국내는 회차별 [송금 확인]."""
    d = await _lock(db, pid)
    _alive(d)
    _check_kind(d, "paid")
    _need_order(d, "송금")
    _need_items(d, "송금 확인")
    if flow.done_map(d)["pay"]:
        raise PurchaseError("이미 송금을 확인했습니다.", 409)
    entry = {"simple": True, "date": day.isoformat(), "amount": flow.po_total(d), "by": user_name, "at": _now(),
             "note": _clean(note, 300)}
    await _save(db, pid, payments=(d.get("payments") or []) + [entry])
    await _log(db, pid, user_id, "paid", {"date": day.isoformat(), **({"note": entry["note"]} if entry["note"] else {})})
    await db.commit()


async def unconfirm_paid(db: AsyncSession, pid: int, user_id: int | None) -> None:
    """송금 확인(해외 제조사 송금) 취소 — 누구나(사용자 2026-11-11)."""
    d = await _lock(db, pid)
    _alive(d)                                         # 취소한 건은 되살린 뒤에(QA 2026-11-16)
    await _save(db, pid, payments=[p for p in d.get("payments") or [] if not p.get("simple")])
    await _log(db, pid, user_id, "pay_unconfirm", {"term": "송금 완료"})
    await db.commit()


async def confirm_customs(db: AsyncSession, pid: int, user_id: int | None, user_name: str, day: dt.date, note: str) -> None:
    """해외 [통관 서류 제출 완료](사용자 2026-11-11) — 원산지 증명서·기타 서류 첨부가 없어도 넘어간다(첨부 수만 기록)."""
    d = await _lock(db, pid)
    _alive(d)
    _check_kind(d, "customs")
    _need_order(d, "통관 서류")
    cu = d.get("customs") or {}
    if cu.get("done_at"):
        raise PurchaseError("이미 통관 서류 제출 완료를 했습니다.", 409)
    await _save(db, pid, customs={**cu, "date": day.isoformat(), "note": _clean(note, 300), "done_at": _now(), "done_by": user_name})
    await _log(db, pid, user_id, "customs", {"date": day.isoformat(), "원산지 증명서": len(cu.get("files") or []),
                                             "기타 서류": len(cu.get("etc_files") or [])})
    await db.commit()


async def set_no_origin(db: AsyncSession, pid: int, user_id: int | None, value: bool) -> None:
    """원산지 증명서 [증빙 자료 없음] — 켜면 화면에 통관 기타 서류(CI·Packing List 등) 칸이 열린다(사용자 2026-11-11)."""
    d = await _lock(db, pid)
    _alive(d)
    _check_kind(d, "customs")
    cu = d.get("customs") or {}
    if bool(cu.get("no_origin")) == value:
        return
    await _save(db, pid, customs={**cu, "no_origin": value})
    await _log(db, pid, user_id, "no_origin", {"원산지 증명서 없음": value})
    await db.commit()


async def confirm_broker(db: AsyncSession, pid: int, user_id: int | None, user_name: str, day: dt.date, note: str) -> None:
    """해외 [관세사 송금 확인](사용자 2026-11-11) — 관세 때문에 관세사로 보낸 돈."""
    d = await _lock(db, pid)
    _alive(d)
    _check_kind(d, "broker")
    _need_order(d, "관세사 송금")
    cu = d.get("customs") or {}
    if (cu.get("broker") or {}).get("date"):
        raise PurchaseError("이미 관세사 송금을 확인했습니다.", 409)
    await _save(db, pid, customs={**cu, "broker": {"date": day.isoformat(), "note": _clean(note, 300), "by": user_name, "at": _now()}})
    await _log(db, pid, user_id, "broker", {"date": day.isoformat(), **({"note": _clean(note, 300)} if note.strip() else {})})
    await db.commit()


async def undo_broker(db: AsyncSession, pid: int, user_id: int | None) -> None:
    """관세사 송금 확인 취소 — 누구나(사용자 2026-11-11)."""
    d = await _lock(db, pid)
    _alive(d)                                         # 취소한 건은 되살린 뒤에(QA 2026-11-16)
    cu = {k: v for k, v in (d.get("customs") or {}).items() if k != "broker"}
    await _save(db, pid, customs=cu)
    await _log(db, pid, user_id, "broker_undo", {})
    await db.commit()


# ── 배송 ─────────────────────────────────────────────────────────────────────

async def start_shipping(db: AsyncSession, pid: int, user_id: int | None, user_name: str, day: dt.date, note: str) -> None:
    """[배송 시작](사용자 2026-11-11) — 공급사 연락(전화 등)으로 배송이 시작된 걸 알면 배송 시작일을 남긴다 → 상태 '배송 중'."""
    d = await _lock(db, pid)
    _alive(d)
    _need_order(d, "배송")
    rc = d.get("receipt") or {}
    if (rc.get("shipping") or {}).get("date"):
        raise PurchaseError("이미 배송 시작을 기록했습니다.", 409)
    if rc.get("records"):
        raise PurchaseError("이미 입고 기록이 있어 배송 단계는 끝났습니다.", 409)
    if day.isoformat() < str((d.get("po") or {}).get("date") or day.isoformat()):
        raise PurchaseError("배송 시작일이 발주일보다 앞입니다.")
    await _save(db, pid, receipt={**rc, "shipping": {"date": day.isoformat(), "note": _clean(note, 300), "by": user_name, "at": _now()}})
    await _log(db, pid, user_id, "ship", {"date": day.isoformat(), **({"note": _clean(note, 300)} if note.strip() else {})})
    await db.commit()


async def undo_shipping(db: AsyncSession, pid: int, user_id: int | None, *, admin: bool = True) -> None:
    """배송 시작 취소 — 잘못 누른 [배송 시작]을 되돌린다. 배송 중이면 누구나(사용자 2026-11-17), 입고한 뒤에는 관리자만."""
    d = await _lock(db, pid)
    _alive(d)                                         # 취소한 건은 되살린 뒤에(QA 2026-11-16)
    if not admin and (d.get("receipt") or {}).get("records"):
        raise PurchaseError("입고 기록이 있어 배송 시작을 취소할 수 없습니다 — 관리자에게 요청해 주세요.", 403)
    rc = {k: v for k, v in (d.get("receipt") or {}).items() if k != "shipping"}
    await _save(db, pid, receipt=rc)
    await _log(db, pid, user_id, "ship_undo", {})
    await db.commit()


# ── 입고 기록 · 거래명세서 · 세금계산서 ────────────────────────────────────────────

RECEIPT_PHOTO = re.compile(r"^[0-9a-f]{32}\.(jpg|jpeg|png|webp)$")
PHOTO_MAX = 200                                       # 칸 하나의 사진 장수(사실상 제한 없음 — 사용자 2026-11-18)


def _serials(name: str, qty: float, raw_list: Any) -> list[str]:
    """입고 품목 줄의 S/N(사용자 2026-11-11 — 출하처럼 입고할 때 등록). 빈 칸은 버리고, 같은 줄 안 겹침·입고 수량보다 많음은 거절."""
    out: list[str] = []
    for v in raw_list or []:
        t = re.sub(r"\s+", " ", str(v or "")).strip()[:SERIAL_MAX]
        if not t:
            continue
        if t.upper() in (x.upper() for x in out):
            raise PurchaseError(f"'{name}' S/N {t} 를 두 번 적었습니다.")
        out.append(t)
    if len(out) > max(int(-(-qty // 1)), 0):
        raise PurchaseError(f"'{name}' S/N 이 입고 수량({qty:g})보다 많습니다.")
    return out


def _photos(raw: Any) -> list[str]:
    """올린 사진 이름(uuid.확장자)만 — 장수 제한 없이(사용자 2026-11-18, 한 칸 최대 PHOTO_MAX)."""
    out: list[str] = []
    for x in raw or []:
        if RECEIPT_PHOTO.match(str(x)) and x not in out:
            out.append(str(x))
    return out[:PHOTO_MAX]


def _units(name: str, qty: float, raw: Any, purpose: str = "dev") -> list[dict]:
    """입고의 제품 한 대씩 — 제조사 S/N · UND S/N + 낱개 품목 사진 · S/N 사진(사용자 2026-11-18·19).
    자체 개발용은 S/W 담당자, 판매용은 고객사·우리 측 담당자·메모(사용자 2026-11-20 — 판매용도 대마다 S/N 두 줄).
    빈 칸은 나중에 채워도 된다(화면이 저장 전에 알림). 같은 줄 안 S/N 겹침·입고 수량보다 많은 칸은 거절."""
    out: list[dict] = []
    seen: set[str] = set()
    for u in raw or []:
        u = u or {}
        m = re.sub(r"\s+", " ", str(u.get("maker_sn") or "")).strip()[:SERIAL_MAX]
        n = re.sub(r"\s+", " ", str(u.get("und_sn") or "")).strip()[:SERIAL_MAX]
        ph = _photos(u.get("photos"))
        for sn, what in ((m, "제조사"), (n, "UND")):
            if sn:
                key = f"{what}:{sn.upper()}"
                if key in seen:
                    raise PurchaseError(f"'{name}' {what} S/N {sn} 를 두 번 적었습니다.")
                seen.add(key)
        one = {"maker_sn": m, "und_sn": n, "photos": ph, "sw_owner": _clean(u.get("sw_owner"), 100),
               "item_photos": _photos(u.get("item_photos"))}
        if purpose == "sale":
            one = {**one, "sw_owner": "", "customer": _clean(u.get("customer"), 200), "owner": _clean(u.get("owner"), 100),
                   "note": str(u.get("note") or "").strip()[:500]}
        out.append(one)
    while out and not (out[-1]["maker_sn"] or out[-1]["und_sn"] or out[-1]["photos"] or out[-1]["sw_owner"] or out[-1]["item_photos"]
                       or out[-1].get("customer") or out[-1].get("owner") or out[-1].get("note")):
        out.pop()                                     # 뒤쪽 빈 칸은 버린다
    if len(out) > max(int(-(-qty // 1)), 0):
        raise PurchaseError(f"'{name}' S/N 칸이 입고 수량({qty:g})보다 많습니다.")
    return out


def _parts(name: str, qty: float, raw: Any) -> list[dict]:
    """판매용 [같은 품목 줄 나누기](사용자 2026-11-19) — 나눈 줄마다 수량·고객사·우리 측 담당자·메모·사진.
    수량이 0 인 빈 줄은 버리고, 나눈 수량 합이 입고 수량과 다르면 거절. 한 줄만 남으면 나누지 않은 것으로(줄 값으로 올림)."""
    out = []
    for x in raw or []:
        x = x or {}
        q = _num(x.get("qty")) or 0
        if q < 0:
            raise PurchaseError(f"'{name}' 나눈 줄 수량은 0 이상으로 적어 주세요.")
        pt = {"qty": q, "customer": _clean(x.get("customer"), 200), "owner": _clean(x.get("owner"), 100),
              "sw_owner": _clean(x.get("sw_owner"), 100), "note": str(x.get("note") or "").strip()[:500], "photos": _photos(x.get("photos"))}
        if q or pt["customer"] or pt["owner"] or pt["sw_owner"] or pt["note"] or pt["photos"]:
            out.append(pt)
    if not out:
        return []
    total = sum(pt["qty"] for pt in out)
    if abs(total - qty) > 1e-9:
        raise PurchaseError(f"'{name}' 나눈 줄 수량의 합({total:g})이 입고 수량({qty:g})과 다릅니다.")
    if any(not pt["qty"] for pt in out):
        raise PurchaseError(f"'{name}' 나눈 줄마다 수량을 적어 주세요.")
    return out


def _receipt_entry(d: dict, p: dict, others: list[dict], user_name: str) -> dict:
    """입고 입력 → 입고 기록 한 줄(출하 기록과 같은 꼴 — 날짜·운송·받은 사람·보관 위치·품목별 수량·사진, 사용자 2026-11-02).
    목적(사용자 2026-11-18): sale 판매용 — S/N 없이 품목마다 고객사·우리 측 담당자·메모 / dev 보유 후 자체 개발용 — 품목마다
    제조사·UND S/N 두 줄과 S/N 사진, S/W 담당자. 발주 품목 줄(line_no)은 다른 입고 기록과 합쳐 발주 수량을 넘으면 거절(오타 방지).
    [품목 모두 비우기]로 적은 자유 형식 줄(line_no 없음)은 발주 수량과 비교하지 않는다 — 입고 완료는 체크로."""
    purpose = p.get("purpose") if p.get("purpose") in flow.RECEIPT_PURPOSES else "sale"
    items = (d.get("po") or {}).get("items") or []
    got: dict[int, float] = {}
    for r in others:
        for ln in r.get("lines") or []:
            if isinstance(ln.get("line_no"), int):
                got[ln["line_no"]] = got.get(ln["line_no"], 0) + float(ln.get("qty") or 0)
    lines = []
    for ln in p.get("lines") or []:
        i = ln.get("line_no")
        qty = _num(ln.get("qty")) or 0
        photos = _photos(ln.get("photos"))
        if qty < 0:
            raise PurchaseError("입고 수량은 0 이상으로 적어 주세요.")
        if i is None:                                 # 자유 형식 줄
            name = _clean(ln.get("name"), 200)
            if not name:
                if qty or photos:
                    raise PurchaseError("직접 적은 품목의 이름을 적어 주세요.")
                continue
        else:
            if not isinstance(i, int) or not 0 <= i < len(items):
                raise PurchaseError("발주서에 없는 품목 줄입니다. 화면을 새로 고친 뒤 다시 해 주세요.", 409)
            name = px.item_name(items[i])
            ordered = float(items[i].get("qty") or 0)
            if got.get(i, 0) + qty > ordered + 1e-9:
                raise PurchaseError(f"'{name}' 입고 수량이 발주 수량({ordered:g})을 넘습니다(이미 {got.get(i, 0):g} 입고).")
        if not qty and not photos:
            continue
        line = {"line_no": i, "name": name, "qty": qty, "photos": photos}
        # 재고 구분(사용자 2026-11-19) — 입고·제품 등록 때 고를 수 있다. 비우면 재고에서 '-'(재고 화면에서 정한 값 유지)
        cat = str(ln.get("category") or "")
        if cat and cat not in stock.CATEGORIES:
            raise PurchaseError(f"'{name}' 구분은 목록에서 골라 주세요.")
        if cat:
            line["category"] = cat
        if purpose == "sale":
            # 판매용도 대마다 S/N 두 줄 + 고객사·담당자(사용자 2026-11-20). [한 줄로 합치기](no_sn) = S/N 없는 품목 — 줄 값만
            no_sn = bool(ln.get("no_sn"))
            line.update(customer=_clean(ln.get("customer"), 200), owner=_clean(ln.get("owner"), 100),
                        note=str(ln.get("note") or "").strip()[:500], no_sn=no_sn,
                        units=[] if no_sn else _units(name, qty, ln.get("units"), "sale"))
        else:
            no_sn = bool(ln.get("no_sn"))             # S/N 없는 품목 — 낱개 칸 없이 전체 사진만(사용자 2026-11-19)
            line.update(sw_owner=_clean(ln.get("sw_owner"), 100), no_sn=no_sn, units=[] if no_sn else _units(name, qty, ln.get("units")))
        # [같은 품목 줄 나누기](사용자 2026-11-19 — 판매용·자체 개발용 같게): 나눈 줄 하나 = 재고 목록 한 줄
        parts = _parts(name, qty, ln.get("parts"))
        if len(parts) > 1:
            line["parts"] = [{k: v for k, v in pt.items() if purpose == "sale" or k not in ("customer", "owner")} for pt in parts]
        elif parts:                                        # 한 줄만이면 나누지 않은 것 — 그 값을 줄에
            keep = ("customer", "owner", "note") if purpose == "sale" else ("sw_owner",)
            line.update({k: parts[0][k] for k in keep})
            line["photos"] = line["photos"] + [x for x in parts[0]["photos"] if x not in line["photos"]]
        lines.append(line)
    final = bool(p.get("final"))
    if not any(ln["qty"] for ln in lines) and not final:
        raise PurchaseError("입고한 품목 수량을 적어 주세요.")
    # [입고 완료]는 지금 발주 수량까지만 닫는다 — 뒤에 품목을 더하거나 수량을 늘리면 늘어난 만큼 다시 입고 대기(사용자 2026-11-16).
    # 품목 이름도 같이 — 그 자리에 다른 품목이 들어오면 새로 열린 줄로(flow.receipt_open, QA 2026-11-16)
    closed = {"closed": {str(i): {"qty": float(it.get("qty") or 0), "name": px.item_name(it)} for i, it in enumerate(items)}} if final else {}
    return {**closed, "purpose": purpose, "date": p["date"].isoformat(), "carrier": _clean(p.get("carrier"), 50),
            "tracking": [t for t in re.split(r"[,\s]+", str(p.get("tracking") or "")) if t][:10],
            "receiver": _clean(p.get("receiver"), 100), "location": _clean(p.get("location"), 100),
            "note": str(p.get("note") or "").strip()[:500], "lines": lines, "final": final, "by": user_name, "at": _now()}


async def add_receipt(db: AsyncSession, pid: int, user_id: int | None, user_name: str, p: dict) -> None:
    """입고 기록 추가 — 입고 기록은 한 건(사용자 2026-11-17): 이미 있으면 그 기록에 합친다(화면은 [남은 품목 받기]로 기록 고치기).
    발주 수량을 모두 받거나 '입고 완료'로 남기면 입고 단계가 끝난다."""
    d = await _lock(db, pid)
    _alive(d)
    _need_order(d, "입고")
    _need_items(d, "입고")
    rc = d.get("receipt") or {}
    recs = rc.get("records") or []
    if flow.receipt_done(d):
        raise PurchaseError("이미 입고를 마쳤습니다. 기록을 고치려면 그 입고 기록의 [수정]을 눌러 주세요.", 409)
    entry = _receipt_entry(d, p, recs, user_name)
    await _save(db, pid, receipt={**rc, "records": flow.merge_receipts(recs + [entry])})
    await _log(db, pid, user_id, "receipt", {"구분": "판매용" if entry["purpose"] == "sale" else "보유 후 자체 개발용",
                                             "date": entry["date"], "items": ", ".join(f"{ln['name']} {ln['qty']:g}" for ln in entry["lines"]),
                                             **({"입고 완료": True} if entry["final"] else {})})
    await db.commit()


async def update_receipt(db: AsyncSession, pid: int, user_id: int | None, user_name: str, index: int, p: dict) -> None:
    """입고 기록 고치기(출하 기록 수정과 같게 — 새 기록을 늘리지 않음, 품목 줄은 지금 발주서 기준으로 채울 수 있음).
    p["expect"] = 화면에서 본 그 기록(at) — 다르면 409. p["reclose"] = [남은 품목 받기](입고 기록 한 건 관리, 사용자 2026-11-17)
    — 이때는 [입고 완료]를 지금 발주 수량 기준으로 다시 닫는다(덜 받고 끝낼 때)."""
    d = await _lock(db, pid)
    _alive(d)
    rc = d.get("receipt") or {}
    recs = list(rc.get("records") or [])
    if not 0 <= index < len(recs):
        raise PurchaseError("고칠 입고 기록이 없습니다.", 404)
    _same_entry(recs, index, p.get("expect"))
    entry = _receipt_entry(d, {**p, "purpose": flow.purpose_of(recs[index])}, recs[:index] + recs[index + 1:], user_name)
    # 이미 [입고 완료]였던 기록을 고칠 때(메모·받은 사람 오타 등)는 그때 닫은 범위를 그대로 — 지금 발주 수량으로 다시 닫으면
    # 다시 열린 입고(품목 추가·수량 늘림)가 조용히 닫혔다(QA 2026-11-16). 새로 닫으려면 새 입고 기록에서 [입고 완료]
    old = recs[index]
    if old.get("final") and entry.get("final") and not p.get("reclose"):
        entry.pop("closed", None)
        if "closed" in old:
            entry["closed"] = old["closed"]
    recs[index] = {**entry, "by": recs[index].get("by"), "at": recs[index].get("at"), "edited_by": user_name, "edited_at": _now()}
    await _save(db, pid, receipt={**rc, "records": recs})
    await _log(db, pid, user_id, "receipt_edit", {"index": index + 1, "date": entry["date"],
                                                  "items": ", ".join(f"{ln['name']} {ln['qty']:g}" for ln in entry["lines"])})
    await db.commit()


async def set_receipt_serials(db: AsyncSession, pid: int, user_id: int | None, index: int, lines: list[dict],
                              expect: dict | None = None) -> None:
    """입고 기록을 남긴 뒤 S/N 만 따로 등록·고치기(사용자 2026-11-11 — 입고 완료 뒤에도). lines = [{line_no, serials}]."""
    d = await _lock(db, pid)
    _alive(d)                                         # 취소한 건은 되살린 뒤에(QA 2026-11-16)
    rc = d.get("receipt") or {}
    recs = list(rc.get("records") or [])
    if not 0 <= index < len(recs):
        raise PurchaseError("S/N 을 적을 입고 기록이 없습니다. 화면을 새로 고쳐 주세요.", 404)
    _same_entry(recs, index, expect)
    rec = dict(recs[index])
    # lines = [{line_index(기록 안 순번), units, sw_owner?}] — 자체 개발용(두 줄 S/N·사진, 사용자 2026-11-18)
    #       · 예전 꼴 [{line_no, serials}] 도 받는다
    by_idx = {x["line_index"]: x for x in lines if isinstance(x.get("line_index"), int)}
    by_no = {x.get("line_no"): x for x in lines if not isinstance(x.get("line_index"), int)}
    new_lines, changed = [], []
    for n, ln in enumerate(rec.get("lines") or []):
        x = by_idx.get(n) or (by_no.get(ln.get("line_no")) if ln.get("line_no") is not None else None)
        if x is not None:
            if x.get("units") is not None:
                us = _units(ln.get("name") or "", float(ln.get("qty") or 0), x.get("units"))
                sw = x.get("sw_owner")
                if us != (ln.get("units") or []) or (sw is not None and _clean(sw, 100) != (ln.get("sw_owner") or "")):
                    changed.append(f"{ln.get('name')} {sum(1 for u in us if u['maker_sn'] or u['und_sn'])}대")
                ln = {**ln, "units": us, **({"sw_owner": _clean(sw, 100)} if sw is not None else {})}
            else:
                sn = _serials(ln.get("name") or "", float(ln.get("qty") or 0), x.get("serials"))
                if sn != (ln.get("serials") or []):
                    changed.append(f"{ln.get('name')} {len(sn)}개")
                ln = {**ln, "serials": sn}
        new_lines.append(ln)
    if not changed:
        return
    recs[index] = {**rec, "lines": new_lines}
    await _save(db, pid, receipt={**rc, "records": recs})
    await _log(db, pid, user_id, "receipt_sn", {"index": index + 1, "S/N": ", ".join(changed)})
    await db.commit()


async def remove_receipt(db: AsyncSession, pid: int, user_id: int | None, index: int, expect: dict | None = None) -> None:
    """관리자 — 잘못 남긴 입고 기록 한 줄 지우기(이력에 지운 내용을 남긴다)."""
    d = await _lock(db, pid)
    _alive(d)                                         # 취소한 건은 되살린 뒤에(QA 2026-11-16)
    rc = d.get("receipt") or {}
    recs = list(rc.get("records") or [])
    if not 0 <= index < len(recs):
        raise PurchaseError("이미 지워졌거나 없는 입고 기록입니다.", 404)
    _same_entry(recs, index, expect)
    removed = recs.pop(index)
    await _save(db, pid, receipt={**rc, "records": recs})
    await _log(db, pid, user_id, "receipt_undo", {"date": removed.get("date"),
                                                  "items": ", ".join(f"{ln['name']} {ln['qty']:g}" for ln in removed.get("lines") or [])})
    await db.commit()


async def confirm_doc(db: AsyncSession, pid: int, user_id: int | None, user_name: str, kind: str, mode: str, day: dt.date,
                      note: str = "") -> None:
    """거래명세서·세금계산서(국내, 선택 서류 — 사용자 2026-11-11)를 못 끊는 경우 사유 기록: 선결제(전달에 지불)·선발행.
    첨부는 따로 올리면 되고(올리면 '첨부'), 이 기록은 첨부 없이 넘어간 이유를 남긴다. 완료 조건에는 넣지 않는다."""
    if mode not in flow.DOC_SKIP_MODES:
        raise PurchaseError("선결제·선발행 중에서 골라 주세요.")
    col, _ = _FILE_COL[kind]
    d = await _lock(db, pid)
    _alive(d)
    _check_kind(d, kind)
    _need_order(d, FILE_KINDS[kind])
    obj = d.get(col) or {}
    await _save(db, pid, **{col: {**{k: v for k, v in obj.items() if k not in ("no_file",)}, "mode": mode, "date": day.isoformat(),
                                  "note": _clean(note, 300), "done_at": _now(), "done_by": user_name}})
    await _log(db, pid, user_id, kind, {"구분": flow.DOC_SKIP_MODES[mode], "date": day.isoformat(),
                                        **({"note": _clean(note, 300)} if note.strip() else {})})
    await db.commit()


async def undo_step(db: AsyncSession, pid: int, user_id: int | None, kind: str) -> None:
    """확인 되돌리기(첨부는 남긴다) — 통관 서류 제출 완료(관리자), 거래명세서·세금계산서 선결제·선발행 기록(api 가 권한 확인).
    입고는 기록 한 줄씩 지운다(remove_receipt)."""
    col = {"customs": "customs", "statement": "statement", "tax": "tax_invoice"}[kind]
    d = await _lock(db, pid)
    _alive(d)                                         # 취소한 건은 되살린 뒤에(QA 2026-11-16)
    obj = d.get(col) or {}
    keep = {k: v for k, v in obj.items() if k not in ("mode", "date", "note", "done_at", "done_by", "no_file")}
    await _save(db, pid, **{col: keep})
    await _log(db, pid, user_id, f"{kind}_undo", {})
    await db.commit()


# ── 재고 직접 등록(사용자 2026-11-19) ────────────────────────────────────────────
# 구매 없이 이미 있는 제품을 제품 재고 관리 [제품 등록]으로 넣는다. 화면에 안 보이는 '직접 등록' 건(direct) 하나에 입고 기록처럼
# 담아, 재고 줄·상태·제품 S/N 관리·사진이 입고한 제품과 똑같이 동작한다. 구매 건 목록·발주번호·단계에는 나오지 않는다.

DIRECT_TITLE = "재고 직접 등록"


async def direct_draft(db: AsyncSession, user_id: int | None, owner: str) -> int:
    """작성 중인 빈 직접 등록 건 — 같은 사람의 빈 건이 있으면 그것(취소를 반복해도 쌓이지 않게), 없으면 새로. 사진을 미리 올리려고 먼저 잡는다."""
    pid = (await db.execute(text(
        "SELECT id FROM purchases WHERE direct AND created_by IS NOT DISTINCT FROM :u "
        "AND jsonb_array_length(COALESCE(receipt->'records', '[]'::jsonb)) = 0 ORDER BY id DESC LIMIT 1"), {"u": user_id})).scalar()
    if pid:
        return int(pid)
    pid = (await db.execute(text(
        "INSERT INTO purchases (supplier, title, owner, region, direct, created_by) VALUES ('직접 등록', :t, :o, 'domestic', TRUE, :u) RETURNING id"),
        {"t": DIRECT_TITLE, "o": _clean(owner, 100), "u": user_id})).scalar_one()
    await db.commit()
    return int(pid)


async def _direct(db: AsyncSession, pid: int) -> dict:
    d = await _lock(db, pid)
    if not d.get("direct"):
        raise PurchaseError("재고 직접 등록 건이 아닙니다.", 404)
    return d


async def save_direct(db: AsyncSession, pid: int, user_id: int | None, user_name: str, p: dict) -> None:
    """직접 등록 저장·고치기 — 제조사(구매처)·국내/해외·입고 기록 한 건(품목은 직접 적은 줄만, 늘 '입고 완료')."""
    d = await _direct(db, pid)
    recs = (d.get("receipt") or {}).get("records") or []
    if recs and flow.purpose_of(recs[0]) != p.get("purpose"):
        raise PurchaseError("판매용·보유 후 자체 개발용은 등록한 뒤 바꿀 수 없습니다. 지우고 다시 등록해 주세요.", 409)
    region = p.get("region") or "domestic"
    if region not in REGIONS:
        raise PurchaseError("국내·해외 중에서 골라 주세요.")
    lines = [{**ln, "line_no": None} for ln in p.get("lines") or []]       # 발주서가 없으니 모두 직접 적은 줄
    entry = _receipt_entry(d, {**p, "lines": lines, "final": True}, [], user_name)
    if not any(ln["qty"] for ln in entry["lines"]):
        raise PurchaseError("등록할 품목과 수량을 적어 주세요.")
    if recs:
        entry = {**entry, "by": recs[0].get("by"), "at": recs[0].get("at"), "edited_by": user_name, "edited_at": _now()}
    supplier = _clean(p.get("supplier"), 200) or "직접 등록"
    await _save(db, pid, supplier=supplier, region=region, receipt={"records": [entry]})
    await _log(db, pid, user_id, "direct_edit" if recs else "direct", {
        "구분": "판매용" if entry["purpose"] == "sale" else "보유 후 자체 개발용", "date": entry["date"],
        "items": ", ".join(f"{ln['name']} {ln['qty']:g}" for ln in entry["lines"])})
    await db.commit()


async def delete_direct(db: AsyncSession, pid: int) -> None:
    """직접 등록 건 지우기 — 재고 줄·S/N 줄도 함께(구매 건 지우기와 같은 정리)."""
    await _direct(db, pid)
    await db.rollback()
    await delete(db, [pid])
