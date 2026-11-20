"""제품 재고 관리(사용자 2026-11-19) — 구매 입고 기록에서 제품 한 대 = 한 줄로 자동으로 채우는 재고 목록.

- 판매용(sale) / 보유 후 자체 개발용(dev) 두 목록. 줄은 입고 기록이 바뀔 때마다(service._save 의 receipt·owner) sync 로 맞춘다:
  입고 기록에서 오는 칸(제품명·입고일·고객사·우리 측 담당자·S/N 두 줄·S/W 담당자)은 따라 바뀌고,
  재고 화면에서 정하는 칸(구분·상태·상태 메모, 자체 개발용 영업팀 담당자)은 그대로 둔다.
- 줄 열쇠 = (구매 건, 목적, 품목 줄, 몇 번째 대) — 품목 줄은 발주 품목이면 'po:줄번호', 직접 적은 품목이면 'free:이름'.
  입고 수량이 줄거나 기록을 지우면 그만큼의 줄도 지운다.
- 판매용은 S/N 을 일부러 안 받는다(출하 때 등록·검사) — 재고에서 S/N 미등록을 경고하지 않는다(사용자 2026-11-19).
- 알림 칸은 비워 둔다(사용자 2026-11-19 — 필요 없으면 칸 자체를 뺄 예정).
- 자체 개발용 줄은 S/N 표(product_units)에도 한 줄씩(stock_item_id, 상태 SD · 개발, 영업 건 없음) — [제품 S/N 관리]의 S/N 찾기·A/S 로
  추적한다(사용자 2026-11-19). S/N 을 아직 안 적은 대도 품목 이름으로 찾게 넣고(no_serial), [S/N 없는 품목]은 넣지 않는다. 재고 줄이 없어지면 S/N 줄도 지우되,
  A/S 기록이 있으면 '입고 삭제'로 남긴다.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import flow

# 구분 — 로봇 · 우리 제품 계열(제품 카드 family·단가표 category: Magbot ATC·EOAT(그리퍼)·Tool Changer Motor → '모터', 사용자 2026-11-19) · 부품류
CATEGORIES = ("QURI", "AMR", "ACR", "휴머노이드", "4축", "6축", "AGV", "기타 로봇",
              "ATC", "EOAT", "그리퍼", "모터",
              "PC", "컨트롤러", "카메라", "태블릿", "부품", "EP", "기타")
STATES = ("대기 중", "수리 필요", "수리 중", "사용 중", "대여 중", "기타")
FIRST_STATE = "대기 중"
RENTED = "대여 중"       # 영업 건 대여로 나감(사용자 2026-11-20) — 회수하면 자동으로 '대기 중'
SOLD = "판매 완료"       # 영업 건 판매로 나감 — 재고 목록에서 빠지고 제품 S/N 관리에서 그 건으로 추적
PURPOSES = ("sale", "dev")
ACTION_LABEL = {"in": "입고", "receipt": "입고 기록 반영", "status": "상태", "handover": "영업 건으로", "back": "재고로 돌아옴", "category": "구분", "owner": "담당자",
                "name": "표시 이름", "maker": "제조사", "customer": "판매 고객사", "sw_owner": "S/W 담당자", "received_on": "입고일"}
# 입고 기록에서 오는 칸 — sync 가 맞춘다
_FROM_RECEIPT = ("name", "received_on", "customer", "sw_owner", "maker_sn", "und_sn", "no_sn")
_FIELD_LABEL = {"category": "구분", "name": "제품명", "received_on": "입고일", "customer": "고객사", "owner": "담당자", "sw_owner": "S/W 담당자",
                "maker_sn": "제조사 S/N", "und_sn": "UND S/N", "no_sn": "S/N 없는 품목"}


class StockError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _line_key(ln: dict) -> str:
    return f"po:{ln['line_no']}" if isinstance(ln.get("line_no"), int) else "free:" + str(ln.get("name") or "")


def _date(v: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def wanted(receipt: dict) -> dict[tuple[str, str, int], dict]:
    """입고 기록 → 있어야 할 재고 줄 {(목적, 품목 줄, 몇 번째 대): 칸}. 예전에 나눠 남긴 기록도 목적별로 합쳐서 본다."""
    out: dict[tuple[str, str, int], dict] = {}
    for rec in flow.merge_receipts(list((receipt or {}).get("records") or [])):
        purpose = flow.purpose_of(rec)
        day = _date(rec.get("date"))
        # 같은 열쇠의 줄이 한 기록에 여럿이면(직접 적은 같은 이름 품목 두 줄 — 고객사가 달라 나눠 적음) 대 번호를 이어서 매긴다.
        # 예전엔 둘 다 0번부터라 뒤 줄이 앞 줄을 덮어 재고가 모자랐다(2026-11-19)
        start: dict[str, int] = {}
        part_start: dict[str, int] = {}       # 판매용 나눈 줄 번호 — 같은 열쇠 줄이 여럿이어도 이어서
        for ln in rec.get("lines") or []:
            n = math.ceil(float(ln.get("qty") or 0) - 1e-9)
            units = ln.get("units") or []
            parts = flow.units_of_parts(ln)              # 나눈 줄을 한 대씩(판매용·자체 개발용, 사용자 2026-11-19)
            legacy = ln.get("serials") or []               # 예전 한 줄 S/N = 제조사 S/N
            lk = _line_key(ln)
            base = start.get(lk, 0)
            start[lk] = base + max(n, 0)
            pbase = part_start.get(lk, 0)
            # 대마다 나뉜 것이 기본(자체 개발용 2026-11-19, 판매용 2026-11-20) — [한 줄로 합치기](no_sn)를 했거나 예전 꼴 나눈 줄(parts)만 묶는다
            # 판매용인데 units 칸이 아예 없는 줄 = 대마다 S/N 을 받기 전(2026-11-20 전)에 남긴 기록 — 예전처럼 한 줄로 묶고 S/N 표에도 안 올린다.
            # 그 기록을 [수정]으로 다시 저장하면 대마다 칸이 생긴다
            legacy_sale = purpose == "sale" and "units" not in ln
            dev_each = not ln.get("no_sn") and not ln.get("parts") and not legacy_sale
            part_start[lk] = pbase + (max(n, 0) if dev_each else len(flow.parts_of(ln)))
            for k in range(max(n, 0)):
                u = units[k] if k < len(units) else {}
                out[(purpose, lk, base + k)] = {
                    "name": str(ln.get("name") or "")[:200], "received_on": day,
                    "category": str(ln.get("category") or "") if ln.get("category") in CATEGORIES else "",
                    "customer": str(u.get("customer") or (parts[k][1]["customer"] if k < len(parts) else ""))[:200] if purpose == "sale" else "",
                    "line_owner": str(u.get("owner") or (parts[k][1]["owner"] if k < len(parts) else ""))[:100] if purpose == "sale" else "",
                    "part": pbase + (k if dev_each else parts[k][0] if k < len(parts) else 0),   # 목록 묶음 = 나눈 줄(자체 개발용은 대마다)
                    "sw_owner": str(u.get("sw_owner") or (parts[k][1]["sw_owner"] if k < len(parts) else "") or ln.get("sw_owner") or "")[:100]
                    if purpose == "dev" else "",
                    "maker_sn": str(u.get("maker_sn") or (legacy[k] if k < len(legacy) else ""))[:100],
                    "und_sn": str(u.get("und_sn") or "")[:100],
                    "no_sn": bool(ln.get("no_sn")) or legacy_sale,
                    # 그 대의 S/N 사진
                    "photos": [x for x in u.get("photos") or [] if isinstance(x, str)],
                    # 품목 사진 = 품목 줄 사진 + 그 대가 속한 나눈 줄의 사진 + 그 대의 낱개 품목 사진(자체 개발용)
                    "line_photos": [x for x in [*(ln.get("photos") or []), *((parts[k][1].get("photos") or []) if k < len(parts) else []),
                                                *(u.get("item_photos") or [])] if isinstance(x, str)],
                }
    return out


async def _log(db: AsyncSession, item_id: int, user_id: int | None, action: str, detail: dict) -> None:
    await db.execute(text("INSERT INTO stock_events (item_id, user_id, action, detail) VALUES (:i, :u, :a, CAST(:d AS jsonb))"),
                     {"i": item_id, "u": user_id, "a": action, "d": json.dumps(detail, ensure_ascii=False, default=str)})


async def sync(db: AsyncSession, pid: int, user_id: int | None = None) -> None:
    """구매 건 pid 의 재고 줄을 지금 입고 기록에 맞춘다(커밋은 부르는 쪽). 사용자가 정한 칸은 건드리지 않는다."""
    p = (await db.execute(text("SELECT receipt, owner FROM purchases WHERE id = :p"), {"p": pid})).mappings().first()
    if not p:
        return
    receipt = p["receipt"] if isinstance(p["receipt"], dict) else json.loads(p["receipt"] or "{}")
    want = wanted(receipt)
    have = {(r["purpose"], r["line_key"], r["unit_index"]): dict(r) for r in (await db.execute(text(
        "SELECT id, purpose, line_key, unit_index, name, received_on, customer, owner, owner_set, sw_owner, maker_sn, und_sn, no_sn, category "
        "FROM stock_items WHERE purchase_id = :p"), {"p": pid})).mappings().all()}
    gone = [r["id"] for k, r in have.items() if k not in want]
    if gone:
        await _drop_units(db, gone)
        await db.execute(text("DELETE FROM stock_items WHERE id = ANY(:ids)"), {"ids": gone})
    # S/N 표에 올릴 재고 줄 — 자체 개발용('SD · 개발')과 판매용('SP · 판매 예정', 사용자 2026-11-20)
    dev_ids: dict[tuple[str, str, int], int] = {k: r["id"] for k, r in have.items() if k in want}
    for key, w in want.items():
        purpose = key[0]
        owner = w["line_owner"] if purpose == "sale" else str(p["owner"] or "")
        cur = have.get(key)
        if cur is None:
            iid = (await db.execute(text(
                "INSERT INTO stock_items (purchase_id, purpose, line_key, unit_index, name, received_on, customer, owner, sw_owner, "
                "maker_sn, und_sn, no_sn, status, category) VALUES (:p, :pu, :lk, :ui, :n, :d, :c, :o, :sw, :m, :u, :ns, :st, :cat) RETURNING id"),
                {"p": pid, "pu": purpose, "lk": key[1], "ui": key[2], "n": w["name"], "d": w["received_on"], "c": w["customer"],
                 "o": owner, "sw": w["sw_owner"], "m": w["maker_sn"], "u": w["und_sn"], "ns": w["no_sn"], "st": FIRST_STATE,
                 "cat": w["category"]})).scalar_one()
            await _log(db, iid, user_id, "in", {"입고일": w["received_on"]})
            dev_ids[key] = iid
            continue
        new = {k: w[k] for k in _FROM_RECEIPT}
        if purpose == "sale" or not cur["owner_set"]:     # 자체 개발용 영업팀 담당자는 재고에서 고치면 그 값을 지킨다
            new["owner"] = owner
        if w["category"]:                                  # 입고 기록에서 고른 구분(사용자 2026-11-19) — 비워 두면 재고에서 정한 값 유지
            new["category"] = w["category"]
        changed = {k: v for k, v in new.items() if cur.get(k) != v}
        if not changed:
            continue
        sets = ", ".join(f"{k} = :{k}" for k in changed)
        await db.execute(text(f"UPDATE stock_items SET {sets}, updated_at = NOW() WHERE id = :i"), {**changed, "i": cur["id"]})
        await _log(db, cur["id"], user_id, "receipt",
                   {_FIELD_LABEL[k]: (v if not isinstance(v, bool) else ("예" if v else "아니요")) for k, v in changed.items()})
    await _sync_units(db, pid, {iid: {**want[k], "purpose": k[0]} for k, iid in dev_ids.items()})


# ── 자체 개발 제품 → S/N 표(product_units) ─────────────────────────────────────

DEV_STATE = "SD"                 # 상태 코드 'SD · 개발'(영업 건 S/N 의 상태 고르기에는 없음)
SALE_STATE = "SP"                # 판매용 입고 'SP · 판매 예정'(사용자 2026-11-20) — 출하 S/N 과 같으면 그 판매 건으로 넘어간다
DEV_GONE = "입고 삭제"           # 재고 줄이 없어졌지만 A/S 기록이 있어 남긴 S/N 줄


async def _sync_units(db: AsyncSession, pid: int, rows: dict[int, dict]) -> None:
    """자체 개발용 재고 줄마다 S/N 표 한 줄 — 제조사 = 구매 공급사, 제조사 S/N(없으면 UND S/N)·UND S/N·S/N 사진.
    다른 S/N(영업 건 출하·대여, 다른 자체 개발 제품)과 같은 번호면 막는다(S/N 은 한 제품만).
    [S/N 없는 품목](케이블 같은 소모품)은 S/N 으로 추적할 대상이 아니라 넣지 않는다(사용자 2026-11-19 — 'SD 케이블' 100줄이
    S/N 관리를 덮지 않게). 나중에 체크를 풀고 S/N 을 적으면 그때 들어오고, 다시 체크하면 빠진다(A/S 가 있으면 '입고 삭제'로 남김)."""
    await _drop_units(db, [iid for iid, w in rows.items() if w["no_sn"]])
    rows = {iid: w for iid, w in rows.items() if not w["no_sn"]}
    if not rows:
        return
    from .service import PurchaseError                 # service 가 stock 을 부르므로 여기서

    supplier = (await db.execute(text("SELECT supplier FROM purchases WHERE id = :p"), {"p": pid})).scalar() or ""
    # 제조사 = 재고에서 정한 제조사(비면 구매 공급사, 사용자 2026-11-19)
    makers = {r[0]: r[1] for r in (await db.execute(text("SELECT id, maker FROM stock_items WHERE id = ANY(:i)"), {"i": list(rows)})).all()}
    cur = {r["stock_item_id"]: r["deal_id"] for r in (await db.execute(text(
        "SELECT deal_id, stock_item_id FROM product_units WHERE stock_item_id = ANY(:i)"), {"i": list(rows)})).mappings().all()}
    for iid, w in rows.items():
        if iid in cur and cur[iid] is not None:       # 판매·대여로 영업 건에 넘어간 S/N — 그 건의 기록이 기준
            continue
        maker = (makers.get(iid) or supplier)[:40]
        serial = (w["maker_sn"] or w["und_sn"])[:80]
        no_serial = not serial
        und = w["und_sn"][:80]
        if not no_serial:
            clash = (await db.execute(text(
                "SELECT u.serial, u.und_serial, u.stock_item_id, s.customer FROM product_units u LEFT JOIN sales_deals s ON s.id = u.deal_id "
                "WHERE NOT u.no_serial AND u.status NOT IN ('회수', :gone) AND (u.stock_item_id IS NULL OR u.stock_item_id <> :i) "
                "AND ((lower(u.maker) = lower(:m) AND lower(u.serial) = lower(:s)) OR (:und <> '' AND lower(u.und_serial) = lower(:und))) LIMIT 1"),
                {"gone": DEV_GONE, "i": iid, "m": maker, "s": serial, "und": und})).mappings().first()
            if clash:
                where = f"{clash['customer']} 건" if clash["customer"] else "다른 자체 개발 제품" if clash["stock_item_id"] else "다른 곳"
                raise PurchaseError(f"S/N {und or serial} 은(는) 이미 {where}에 등록돼 있습니다. 번호를 확인해 주세요.", 409)
        vals = {"pn": w["name"][:120] or "-", "s": serial or "S/N 없음", "u": und, "ns": no_serial, "m": maker,
                "ph": json.dumps(w["photos"][:200]), "i": iid}
        if iid in cur:
            await db.execute(text(
                "UPDATE product_units SET part_name = :pn, line_name = :pn, serial = :s, und_serial = :u, no_serial = :ns, maker = :m, "
                "photos = CAST(:ph AS jsonb), updated_at = NOW() WHERE stock_item_id = :i AND (part_name, line_name, serial, und_serial, "
                "no_serial, maker, photos) IS DISTINCT FROM (CAST(:pn AS TEXT), CAST(:pn AS TEXT), CAST(:s AS TEXT), CAST(:u AS TEXT), "
                "CAST(:ns AS BOOLEAN), CAST(:m AS TEXT), CAST(:ph AS jsonb))"), vals)
        else:
            await db.execute(text(
                "INSERT INTO product_units (stock_item_id, part_name, line_name, serial, und_serial, no_serial, maker, photos, purpose, status) "
                "VALUES (:i, :pn, :pn, :s, :u, :ns, :m, CAST(:ph AS jsonb), :st, '등록')"),
                {**vals, "st": SALE_STATE if w.get("purpose") == "sale" else DEV_STATE})


async def _drop_units(db: AsyncSession, stock_ids: list[int]) -> None:
    """없어지는 재고 줄의 S/N 줄 — A/S 기록이 없으면 지우고, 있으면 '입고 삭제'로 남겨 이력을 지킨다."""
    if not stock_ids:
        return
    # 영업 건으로 넘어간 S/N 줄은 그 건의 기록 — 재고 줄과의 연결만 끊는다(재고 줄을 지울 때 함께 지워지지 않게)
    await db.execute(text("UPDATE product_units SET stock_item_id = NULL WHERE stock_item_id = ANY(:i) AND deal_id IS NOT NULL"),
                     {"i": stock_ids})
    await db.execute(text(
        "UPDATE product_units u SET status = :g, stock_item_id = NULL, updated_at = NOW(), "
        "note = left(trim(u.note || ' 입고 기록 삭제 — ' || COALESCE(p.supplier, '') || ' ' || COALESCE(p.po->>'no', '')), 300) "
        "FROM stock_items s LEFT JOIN purchases p ON p.id = s.purchase_id "
        "WHERE u.stock_item_id = s.id AND s.id = ANY(:i) AND EXISTS (SELECT 1 FROM as_cases a WHERE a.unit_id = u.id)"),
        {"g": DEV_GONE, "i": stock_ids})
    await db.execute(text("DELETE FROM product_units WHERE stock_item_id = ANY(:i)"), {"i": stock_ids})


async def handed_over(db: AsyncSession, stock_id: int, user_id: int | None, *, rented: bool, where: str) -> None:
    """재고 제품이 영업 건 출하·대여 S/N 으로 넘어감(사용자 2026-11-20) — 대여면 '대여 중'(목록에 남음), 판매면 '판매 완료'(목록에서 빠짐)."""
    st = RENTED if rented else SOLD
    await db.execute(text("UPDATE stock_items SET status = :s, updated_at = NOW() WHERE id = :i"), {"s": st, "i": stock_id})
    await _log(db, stock_id, user_id, "handover", {"상태": st, "건": where})


async def release(db: AsyncSession) -> int:
    """영업 건에서 돌아온 재고 제품 정리(사용자 2026-11-20) — 대여를 회수했거나, 그 건의 S/N·출하·건을 지워 넘어간 S/N 줄이 없어진 것.
    재고 상태를 '대기 중'으로 되돌리고 S/N 표에 다시 'SP · 판매 예정'/'SD · 개발' 줄을 만든다(회수한 대여 줄은 그 건의 이력으로 남김).
    재고 목록·S/N 찾기를 열 때 부른다. 반환 = 돌아온 개수."""
    rs = (await db.execute(text(
        "SELECT s.id, s.purchase_id, s.status, u.id AS unit_id, u.status AS unit_status FROM stock_items s "
        "LEFT JOIN product_units u ON u.stock_item_id = s.id "
        "WHERE s.status IN (:r, :sold) AND (u.id IS NULL OR u.deal_id IS NULL OR u.status IN ('회수', '건 삭제'))"),
        {"r": RENTED, "sold": SOLD})).mappings().all()
    if not rs:
        return 0
    for r in rs:
        if r["unit_id"] is not None:               # 회수한 대여 줄 — 그 건의 이력으로 두고 재고와의 연결만 끊는다
            await db.execute(text("UPDATE product_units SET stock_item_id = NULL WHERE id = :u AND deal_id IS NOT NULL"), {"u": r["unit_id"]})
        await db.execute(text("UPDATE stock_items SET status = :s, updated_at = NOW() WHERE id = :i"), {"s": FIRST_STATE, "i": r["id"]})
        await _log(db, r["id"], None, "back", {"상태": FIRST_STATE,
                                               "까닭": "대여 회수" if r["unit_status"] == "회수" else "영업 건에서 빠짐"})
    for pid in {r["purchase_id"] for r in rs}:
        await sync(db, pid)
    await db.commit()
    return len(rs)


async def purchases_removed(db: AsyncSession, pids: list[int]) -> None:
    """구매 건을 지우기 전에 — 그 재고 줄의 S/N 줄 정리(재고 줄은 구매 건과 함께 지워진다)."""
    ids = (await db.execute(text("SELECT id FROM stock_items WHERE purchase_id = ANY(:p)"), {"p": pids})).scalars().all()
    await _drop_units(db, list(ids))


async def unit_extra(db: AsyncSession, stock_item_id: int) -> dict | None:
    """S/N 상세에 붙일 구매·재고 정보 — 구매 건(공급사·발주번호·입고일), 담당자, 재고 구분·상태·메모, 그 품목의 전체 사진."""
    r = (await db.execute(text(
        "SELECT s.id, s.purchase_id, s.purpose, s.line_key, s.unit_index, s.name, s.display_name, s.maker, s.category, s.status, s.status_note, s.owner, "
        "s.sw_owner, s.customer, s.received_on, "
        "p.supplier, p.po->>'no' AS po_no, p.receipt FROM stock_items s JOIN purchases p ON p.id = s.purchase_id WHERE s.id = :i"),
        {"i": stock_item_id})).mappings().first()
    if not r:
        return None
    receipt = r["receipt"] if isinstance(r["receipt"], dict) else json.loads(r["receipt"] or "{}")
    photos: list[str] = []
    for rec in flow.merge_receipts(list(receipt.get("records") or [])):
        if flow.purpose_of(rec) == r["purpose"]:
            for ln in rec.get("lines") or []:
                if _line_key(ln) == r["line_key"]:
                    us = ln.get("units") or []
                    mine = us[r["unit_index"]] if r["unit_index"] < len(us) else {}
                    photos = [x for x in [*(ln.get("photos") or []), *(mine.get("item_photos") or [])] if isinstance(x, str)]
    return {"stock_id": r["id"], "purchase_id": r["purchase_id"], "supplier": r["supplier"], "po_no": r["po_no"],
            "display_name": r["display_name"], "po_name": r["name"], "maker": r["maker"] or r["supplier"],
            "received_on": _iso(r["received_on"]), "category": r["category"], "status": r["status"], "status_note": r["status_note"],
            "owner": r["owner"], "sw_owner": r["sw_owner"], "line_photos": photos,
            "purpose": r["purpose"], "customer": r["customer"]}   # 판매용 — 판매 고객사·우리 담당자(사용자 2026-11-20)


async def _backfill(db: AsyncSession) -> None:
    """재고 표를 만들기 전에 남긴 입고 기록 — 재고 줄이 하나도 없는 구매 건만 채운다(한 번 채우면 다시 안 돎)."""
    pids = (await db.execute(text(
        "SELECT p.id FROM purchases p WHERE jsonb_array_length(COALESCE(p.receipt->'records', '[]'::jsonb)) > 0 "
        "AND NOT EXISTS (SELECT 1 FROM stock_items s WHERE s.purchase_id = p.id)"))).scalars().all()
    for pid in pids:
        await sync(db, pid)
    if pids:
        await db.commit()


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v


async def list_items(db: AsyncSession, purpose: str) -> dict:
    if purpose not in PURPOSES:
        raise StockError("판매용·보유 후 자체 개발용 중에서 골라 주세요.")
    await _backfill(db)
    await release(db)
    rs = (await db.execute(text(
        "SELECT s.id, s.purchase_id, s.purpose, s.line_key, s.unit_index, s.name, s.display_name, s.maker, s.category, s.customer, s.owner, s.sw_owner, s.maker_sn, s.und_sn, s.no_sn, "
        "s.received_on, s.status, s.status_note, s.updated_at, p.supplier, p.region, p.direct, p.po->>'no' AS po_no, "
        "e.action AS last_action, e.detail AS last_detail, e.created_at AS last_at, COALESCE(u.alias, u.username) AS last_by "
        "FROM stock_items s JOIN purchases p ON p.id = s.purchase_id "
        "LEFT JOIN LATERAL (SELECT * FROM stock_events x WHERE x.item_id = s.id ORDER BY x.id DESC LIMIT 1) e ON TRUE "
        "LEFT JOIN users u ON u.id = e.user_id "
        "WHERE s.purpose = :pu AND NOT p.dropped AND s.status <> :sold ORDER BY s.received_on DESC NULLS LAST, s.purchase_id DESC, s.line_key, s.unit_index"),
        {"pu": purpose, "sold": SOLD})).mappings().all()
    rows = []
    for r in rs:
        detail = r["last_detail"] if isinstance(r["last_detail"], dict) else json.loads(r["last_detail"] or "{}")
        rows.append({
            "id": r["id"], "purchase_id": r["purchase_id"], "purpose": r["purpose"], "line_key": r["line_key"], "unit_index": r["unit_index"],
            "name": r["name"], "display_name": r["display_name"], "category": r["category"],
            "maker": r["maker"] or r["supplier"], "maker_custom": bool(r["maker"]),   # 제조사 — 비면 공급사
            "customer": r["customer"], "owner": r["owner"], "sw_owner": r["sw_owner"], "maker_sn": r["maker_sn"], "und_sn": r["und_sn"],
            "no_sn": r["no_sn"], "received_on": _iso(r["received_on"]), "status": r["status"], "status_note": r["status_note"],
            "supplier": r["supplier"], "region": r["region"], "po_no": r["po_no"], "alerts": [],   # 국내·해외 = 구매 건 구분
            "direct": bool(r["direct"]),                      # 재고 직접 등록(구매 없이 [제품 등록])
            "last": {"label": ACTION_LABEL.get(r["last_action"], r["last_action"]), "detail": detail, "at": _iso(r["last_at"]),
                     "by": r["last_by"]} if r["last_action"] else None,
        })
    # 사진(사용자 2026-11-19) — 저장은 입고 기록에만 두고 목록을 줄 때 읽어 붙인다: 품목 줄의 전체 사진 + (자체 개발용) 그 대의 S/N 사진
    receipts = {r[0]: r[1] for r in (await db.execute(text("SELECT id, receipt FROM purchases WHERE id = ANY(:i)"),
                                                     {"i": list({x["purchase_id"] for x in rows})})).all()}
    want = {pid: wanted(rc if isinstance(rc, dict) else json.loads(rc or "{}")) for pid, rc in receipts.items()}
    for x in rows:
        w = want.get(x["purchase_id"], {}).get((x["purpose"], x["line_key"], x["unit_index"])) or {}
        x["line_photos"], x["photos"] = w.get("line_photos", []), w.get("photos", [])
        x["part"] = w.get("part", 0)                     # 판매용 나눈 줄 번호 — 목록은 같은 줄끼리 묶는다
        if w:                                            # 예전 판매용 기록(대마다 S/N 전)은 묶음 줄로 보이게 — 다음 저장 때 표에도 맞춰짐
            x["no_sn"] = w["no_sn"]
    counts = dict((await db.execute(text(
        "SELECT s.purpose, COUNT(*) FROM stock_items s JOIN purchases p ON p.id = s.purchase_id WHERE NOT p.dropped AND s.status <> :sold "
        "GROUP BY s.purpose"), {"sold": SOLD})).all())
    return {"rows": rows, "counts": {k: counts.get(k, 0) for k in PURPOSES}, "categories": CATEGORIES, "states": STATES}


def _clean(v: Any, n: int) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()[:n]


async def update_item(db: AsyncSession, item_id: int, user_id: int | None, p: dict) -> None:
    """재고 화면에서 정하는 칸 — 구분·상태(+메모)·자체 개발용 영업팀 담당자·S/W 담당자·표시 이름. 영업팀 누구나, 이력에 남는다."""
    await _apply(db, item_id, user_id, p)
    await db.commit()


BULK_MAX = 1000


async def update_items(db: AsyncSession, ids: list[int], user_id: int | None, p: dict) -> int:
    """여러 줄을 한 번에(사용자 2026-11-19 — 판매용 화면에서 같은 품목을 묶어 '100개 중 30개를 사용 중으로'·구분 한꺼번에).
    하나라도 없으면 아무것도 바꾸지 않는다. 반환 = 바꾼 줄 수."""
    ids = list(dict.fromkeys(i for i in ids if isinstance(i, int) and i > 0))
    if not ids:
        raise StockError("바꿀 제품을 골라 주세요.")
    if len(ids) > BULK_MAX:
        raise StockError(f"한 번에 {BULK_MAX}개까지 바꿀 수 있습니다.")
    for i in ids:
        await _apply(db, i, user_id, p)
    await db.commit()
    return len(ids)


async def _apply(db: AsyncSession, item_id: int, user_id: int | None, p: dict) -> None:
    cur = (await db.execute(text("SELECT s.id, s.purchase_id, s.purpose, s.line_key, s.unit_index, s.name, s.display_name, s.maker, "
                                 "s.category, s.status, s.status_note, s.owner, s.sw_owner, s.customer, s.received_on, p.supplier, p.receipt FROM stock_items s "
                                 "JOIN purchases p ON p.id = s.purchase_id WHERE s.id = :i FOR UPDATE OF s"),
                            {"i": item_id})).mappings().first()
    if not cur:
        raise StockError("재고 줄을 찾을 수 없습니다. 화면을 새로 고쳐 주세요.", 404)
    sets: dict[str, Any] = {}
    if p.get("category") is not None:
        cat = str(p["category"])
        if cat and cat not in CATEGORIES:
            raise StockError("구분은 목록에서 골라 주세요.")
        if cat != cur["category"]:
            sets["category"] = cat
            await _log(db, item_id, user_id, "category", {"from": cur["category"] or "-", "to": cat or "-"})
    if p.get("status") is not None or p.get("status_note") is not None:
        st = str(p.get("status") if p.get("status") is not None else cur["status"])
        if st not in STATES:
            raise StockError("상태는 목록에서 골라 주세요.")
        # '대여 중'은 영업 건 대여 S/N 등록으로만 들어가고 회수하면 자동으로 빠진다(사용자 2026-11-20) — 손으로 넣고 빼지 않는다
        if st != cur["status"] and RENTED in (st, cur["status"]):
            raise StockError("'대여 중'은 영업 건에서 대여 S/N 을 등록하면 자동으로 바뀌고, 회수하면 '대기 중'으로 돌아갑니다.")
        note = _clean(p.get("status_note"), 500) if p.get("status_note") is not None else cur["status_note"]
        if st != cur["status"] or note != cur["status_note"]:
            sets.update(status=st, status_note=note)
            await _log(db, item_id, user_id, "status", {"from": cur["status"], "to": st, **({"메모": note} if note else {}),
                                                         **({"방법": p["via"]} if p.get("via") else {})})   # 엑셀 적용(2026-11-20)
    if p.get("display_name") is not None:
        # 표시 이름 — 발주서 품목명과 같거나 비우면 '' (발주서 이름으로 보임). 발주서·입고 기록 이름(name)은 건드리지 않는다
        dn = _clean(p["display_name"], 200)
        dn = "" if dn == cur["name"] else dn
        if dn != cur["display_name"]:
            sets["display_name"] = dn
            await _log(db, item_id, user_id, "name", {"from": cur["display_name"] or cur["name"], "to": dn or cur["name"]})
    maker_changed = False
    if p.get("maker") is not None:
        # 제조사 — 공급사와 같거나 비우면 '' (공급사를 따름)
        mk = _clean(p["maker"], 100)
        mk = "" if mk == (cur["supplier"] or "") else mk
        if mk != cur["maker"]:
            sets["maker"] = mk
            maker_changed = True
            await _log(db, item_id, user_id, "maker", {"from": cur["maker"] or cur["supplier"], "to": mk or cur["supplier"]})
    # 판매 고객사·담당자·입고일도 재고에서 고칠 수 있다(사용자 2026-11-19 — 직접 등록 건 등). 그 입고 기록을 다시 저장하면 입고 기록 값으로 돌아간다.
    # 자체 개발용 영업팀 담당자만은 예전처럼 재고에서 정한 값을 지킨다(owner_set — 입고 기록에 그 칸이 없음)
    if p.get("owner") is not None:
        owner = _clean(p["owner"], 100)
        if owner != cur["owner"]:
            sets.update(owner=owner, **({"owner_set": True} if cur["purpose"] == "dev" else {}))
            await _log(db, item_id, user_id, "owner", {"from": cur["owner"] or "-", "to": owner or "-"})
    if p.get("customer") is not None:
        if cur["purpose"] != "sale":
            raise StockError("판매 고객사는 판매용 재고에만 있습니다.")
        cu = _clean(p["customer"], 200)
        if cu != cur["customer"]:
            sets["customer"] = cu
            await _log(db, item_id, user_id, "customer", {"from": cur["customer"] or "-", "to": cu or "-"})
    if p.get("sw_owner") is not None:
        # S/W 담당자(자체 개발용, 사용자 2026-11-19) — 고객사처럼 입고 기록을 다시 저장하면 입고 기록 값으로 돌아간다
        if cur["purpose"] != "dev":
            raise StockError("S/W 담당자는 자체 개발용 재고에만 있습니다.")
        sw = _clean(p["sw_owner"], 100)
        if sw != cur["sw_owner"]:
            sets["sw_owner"] = sw
            await _log(db, item_id, user_id, "sw_owner", {"from": cur["sw_owner"] or "-", "to": sw or "-"})
    if p.get("received_on") is not None:
        day = _date(p["received_on"])
        if day is None:
            raise StockError("입고일을 달력에서 골라 주세요.")
        if day != cur["received_on"]:
            sets["received_on"] = day
            await _log(db, item_id, user_id, "received_on", {"from": _iso(cur["received_on"]) or "-", "to": day.isoformat()})
    if sets:
        await db.execute(text(f"UPDATE stock_items SET {', '.join(f'{k} = :{k}' for k in sets)}, updated_at = NOW() WHERE id = :i"),
                         {**sets, "i": item_id})
    if maker_changed and cur["purpose"] == "dev":     # 자체 개발 제품의 S/N 표 제조사도(다른 S/N 과 겹치면 막는다)
        from .service import PurchaseError
        receipt = cur["receipt"] if isinstance(cur["receipt"], dict) else json.loads(cur["receipt"] or "{}")
        w = wanted(receipt).get((cur["purpose"], cur["line_key"], cur["unit_index"]))
        if w:
            try:
                await _sync_units(db, cur["purchase_id"], {item_id: w})
            except PurchaseError as e:
                raise StockError(str(e), 409) from e


async def history(db: AsyncSession, item_id: int) -> list[dict]:
    rs = (await db.execute(text(
        "SELECT e.id, e.action, e.detail, e.created_at, COALESCE(u.alias, u.username) AS user_name "
        "FROM stock_events e LEFT JOIN users u ON u.id = e.user_id WHERE e.item_id = :i ORDER BY e.id DESC"), {"i": item_id})).mappings().all()
    return [{"id": e["id"], "label": ACTION_LABEL.get(e["action"], e["action"]),
             "detail": e["detail"] if isinstance(e["detail"], dict) else json.loads(e["detail"] or "{}"),
             "at": _iso(e["created_at"]), "by": e["user_name"]} for e in rs]


# ── 재고에서 사진 넣고 빼기(사용자 2026-11-19 — [수정] 모드) ─────────────────────────────
# 사진은 입고 기록에만 둔다. 재고 줄 하나가 어느 입고 기록·품목 줄·나눈 줄·몇 번째 대인지 찾아 그 자리에 넣고 뺀다.
#   scope "line" = 그 줄(묶음) 전체 사진 — 판매용 나눈 줄이면 그 나눈 줄 사진, 아니면 품목 줄 사진
#   scope "unit" = 그 대 하나 — 자체 개발용 kind "item"(낱개 품목 사진)·"sn"(S/N 사진)


def _locate(records: list[dict], purpose: str, line_key: str, unit_index: int) -> tuple[int, int, int, int] | None:
    """(기록 순번, 품목 줄 순번, 그 줄 안 몇 번째 대, 나눈 줄 순번) — wanted() 와 같은 순서로 센다."""
    for ri, rec in enumerate(records):
        if flow.purpose_of(rec) != purpose:
            continue
        start = 0
        for li, ln in enumerate(rec.get("lines") or []):
            if _line_key(ln) != line_key:
                continue
            n = max(math.ceil(float(ln.get("qty") or 0) - 1e-9), 0)
            if start <= unit_index < start + n:
                k = unit_index - start
                parts = flow.units_of_parts(ln)
                return ri, li, k, (parts[k][0] if k < len(parts) else 0)
            start += n
    return None


async def edit_photos(db: AsyncSession, item_id: int, user_id: int | None, p: dict) -> None:
    """재고 줄의 사진 넣고 빼기 — 입고 기록에 저장(입고 기록 화면에도 같이 보임). add = 넣을 사진, remove = 뺄 사진."""
    from .service import PurchaseError, RECEIPT_PHOTO, _log as plog, _save

    r = (await db.execute(text("SELECT s.purchase_id, s.purpose, s.line_key, s.unit_index, p.receipt FROM stock_items s "
                               "JOIN purchases p ON p.id = s.purchase_id WHERE s.id = :i FOR UPDATE OF p"), {"i": item_id})).mappings().first()
    if not r:
        raise StockError("재고 줄을 찾을 수 없습니다. 화면을 새로 고쳐 주세요.", 404)
    receipt = r["receipt"] if isinstance(r["receipt"], dict) else json.loads(r["receipt"] or "{}")
    records = flow.merge_receipts(list(receipt.get("records") or []))
    at = _locate(records, r["purpose"], r["line_key"], r["unit_index"])
    if at is None:
        raise StockError("이 제품의 입고 기록을 찾지 못했습니다. 화면을 새로 고쳐 주세요.", 409)
    ri, li, k, pi = at
    ln = records[ri]["lines"][li]
    add = [x for x in dict.fromkeys(p.get("add") or []) if RECEIPT_PHOTO.match(str(x))]
    remove = set(p.get("remove") or [])
    if remove:                                   # 어디에 있든 뺀다(품목 줄·나눈 줄·대마다)
        ln["photos"] = [x for x in ln.get("photos") or [] if x not in remove]
        for pt in ln.get("parts") or []:
            pt["photos"] = [x for x in pt.get("photos") or [] if x not in remove]
        for u in ln.get("units") or []:
            u["photos"] = [x for x in u.get("photos") or [] if x not in remove]
            u["item_photos"] = [x for x in u.get("item_photos") or [] if x not in remove]
    if add:
        if p.get("scope") == "unit" and r["purpose"] == "dev" and not ln.get("no_sn"):
            units = list(ln.get("units") or [])
            while len(units) <= k:
                units.append({"maker_sn": "", "und_sn": "", "photos": [], "sw_owner": "", "item_photos": []})
            key = "photos" if p.get("kind") == "sn" else "item_photos"
            units[k] = {**units[k], key: list(dict.fromkeys([*(units[k].get(key) or []), *add]))}
            ln["units"] = units
        elif r["purpose"] == "sale" and len(ln.get("parts") or []) > 1:
            ln["parts"][pi]["photos"] = list(dict.fromkeys([*(ln["parts"][pi].get("photos") or []), *add]))
        else:
            ln["photos"] = list(dict.fromkeys([*(ln.get("photos") or []), *add]))
    if not add and not remove:
        return
    try:
        await _save(db, r["purchase_id"], receipt={**receipt, "records": records})
    except PurchaseError as e:
        raise StockError(str(e), e.status) from e
    await plog(db, r["purchase_id"], user_id, "receipt_photo", {"품목": ln.get("name"), "넣음": len(add), "뺌": len(remove)})
    await db.commit()
