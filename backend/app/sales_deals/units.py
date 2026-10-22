# -*- coding: utf-8 -*-
"""제품 시리얼 대장·무상 기간·A/S 기록(사용자 2026-10-19, 영업팀 요청 20번 '맥봇 제품 A/S 이력 관리').

- 시리얼은 부품마다, 영업팀이 최종 고객과 협의된 견적서 판 기준으로 직접 등록한다. 한 줄 = 부품 하나(product_units).
  어느 견적서의 몇 번째 판, 몇 번째 품목 줄인지 같이 남긴다(품목 줄 id 가 없는 견적도 있어 줄 위치 + 그때 이름).
- 미등록 = 아무것도 적지 않은 칸. 시리얼이 없는 품목은 '특주'처럼 적는다 — 숫자가 없는 글은 시리얼 없음으로 보고
  같은 글이 여러 번 나와도 겹침 검사를 하지 않는다.
- 품목 줄은 수량만큼 등록하면 다 한 것(한 줄을 부품 여러 개로 나눠 더 적어도 된다). 설치비·물류비·서술형 줄은 대상이 아니다.
- 출하 기록 전에 수주 판의 시리얼을 다 적어야 한다(사용자 2026-10-19). 출하에 묶이면 출하일 = 무상 기간 시작일,
  종료일을 그때 확정 저장한다(무상 기간 기본값이 나중에 바뀌어도 이미 나간 제품은 그대로).
- 무상 기간은 제품마다 정하기 나름 — warranty_policies(keyword → 개월)로 기본값, 건마다 고칠 수 있고 비우면 미정.
- 범위: 자사 제품 + 타사 재판매(제조사 칸).
- 상태 = 영문 이니셜 한 글자(사용자 2026-10-19, 영업팀 요청) — product_units.purpose 에 코드로 저장한다. UNIT_STATES 참고.
- A/S 는 기록만(유상 A/S 견적은 영업팀이 프로그램 밖에서 수기). 무상·유상은 접수일이 무상 기간 안인지로 제안한다.
- A/S 접수서(고객사 문의를 받아 우리가 작성) = 고객사·고객 담당자·고장 원인(구분+내용)·사진·무상/유상(출하일 기준 무상 기간으로
  기본값, 바꿀 수 있음)·진행. 접수자는 로그인 사용자.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .service import COLS, DealError, _iso, _issue_no, _lock, _log, _row, deal_quotes

# 제품 상태 이니셜 — 영문 단어 첫 글자. 시리얼 옆에 'S · 판매'처럼 보인다. 바꾸면 프런트 lib/shared/unit-states.ts 도 같이.
UNIT_STATES = (
    ("S", "Sale", "판매"),
    ("R", "Rental", "대여"),
    ("D", "Demo", "데모·시연"),
    ("A", "After-service", "A/S 중"),
    ("E", "Exchange", "A/S 교체품"),
    ("O", "Other", "기타"),
)
STATE_CODES = tuple(c for c, _, _ in UNIT_STATES)
_STATE_BY_LABEL = {label: c for c, _, label in UNIT_STATES} | {"A/S 교체": "E"}   # 이니셜 도입 전 한글 값


def state_code(value: Any) -> str:
    """코드('s' 소문자 포함)·이니셜 도입 전 한글 값 → 코드. 모르는 값은 S(판매)."""
    v = str(value or "").strip()
    return v.upper() if v.upper() in STATE_CODES else _STATE_BY_LABEL.get(v, "S")
CAUSES = ("확인 중", "제품 결함", "고객 과실", "기타")
CHARGES = ("미정", "무상", "유상")
AS_STATUS = ("접수", "진행 중", "완료")
SERIAL_MAX, PART_MAX, MAKER_MAX, NOTE_MAX = 80, 120, 40, 300
MONTHS_MAX = 120
_NOT_TARGET = ("work", "freight", "note")
_DIGIT = re.compile(r"[0-9]")
AS_PHOTO_ID = re.compile(r"^[0-9a-f]{32}\.(jpg|jpeg|png|webp)$")
AS_PHOTOS_MAX = 10


# ── 대상 품목 줄 ─────────────────────────────────────────────────────────────

def is_no_serial(serial: str) -> bool:
    """숫자가 하나도 없는 글('특주' 등) = 시리얼이 없는 품목이라는 메모."""
    return not _DIGIT.search(serial)


# 수량 칸이 기간인 줄(대여 견적서 '36 개월') — 시리얼 칸은 수량만큼이 아니라 1개
TIME_UNITS = {"개월", "월", "년", "일", "주", "month", "months", "year", "years", "day", "days", "week", "weeks"}


def target_lines(lines: list[dict]) -> list[dict]:
    """견적서 판의 줄 → 시리얼을 적을 품목 줄 [{line_no, name, qty}]. 합계 줄·NEGO·서술형·설치비·물류비·Service 품목은 뺀다.
    Total 에 반영한 옵션 줄(in_total)은 같이 사는 것이라 넣는다. line_no 는 판 안의 줄 위치(0부터)."""
    out = []
    for i, ln in enumerate(lines or []):
        sold = ln.get("row") is None or (ln.get("row") == "option" and ln.get("in_total"))
        if not sold or ln.get("kind") in _NOT_TARGET or ln.get("key") in _NOT_TARGET:
            continue
        if str(ln.get("commodity") or "").strip().lower() == "service":
            continue
        qty = ln.get("qty")
        if not isinstance(qty, (int, float)) or isinstance(qty, bool) or qty <= 0:
            continue
        if str(ln.get("unit") or "").strip().lower() in TIME_UNITS:
            qty = 1                     # '36 개월'은 기간 — 대여 제품 1대(사용자 2026-10-22), 더 있으면 등록 창에서 칸 추가
        name = str(ln.get("name") or ln.get("desc") or "").strip()
        if name:
            out.append({"line_no": i, "name": name, "qty": int(round(qty))})
    return out


def default_part(name: str) -> str:
    """품목 줄 → 부품명 기본값. 'ATC_CORE\\n- TCV1 MASTER T.C (Robot Side)' 처럼 첫 줄이 묶음 이름이면 '- ' 줄."""
    rows = [r.strip() for r in name.splitlines() if r.strip()]
    sub = [r.lstrip("-· ").strip() for r in rows[1:] if r.startswith(("-", "·"))]
    return ((sub[0] if sub else rows[0]) if rows else "")[:PART_MAX]


def add_months(day: dt.date, months: int) -> dt.date:
    y, m = divmod(day.month - 1 + months, 12)
    y, m = day.year + y, m + 1
    return dt.date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def warranty_until(start: dt.date | None, months: int | None) -> dt.date | None:
    """무상 종료일 = 시작일 + 개월 − 하루(1년이면 2026-10-19 → 2027-10-18)."""
    return add_months(start, months) - dt.timedelta(days=1) if start and months else None


# ── 무상 기간 기본값 ─────────────────────────────────────────────────────────

async def policies(db: AsyncSession) -> list[dict]:
    rs = (await db.execute(text("SELECT id, keyword, months, note, sort, updated_at FROM warranty_policies "
                                "ORDER BY sort, id"))).mappings().all()
    return [{**dict(r), "updated_at": _iso(r["updated_at"])} for r in rs]


def match_policy(rows: list[dict], *texts: str) -> dict | None:
    """부품명·품목 줄·제조사 중 하나에 keyword 가 들어 있는 기본값 — 여러 개면 가장 긴 keyword."""
    hay = " ".join(t for t in texts if t).lower()
    hits = [r for r in rows if r["keyword"].strip() and r["keyword"].strip().lower() in hay]
    return max(hits, key=lambda r: len(r["keyword"].strip())) if hits else None


async def save_policies(db: AsyncSession, user_id: int | None, rows: list[dict]) -> list[dict]:
    """기본값 표 통째로 바꾸기(관리자). 이미 등록한 시리얼의 개월 수는 바꾸지 않는다."""
    seen: set[str] = set()
    clean = []
    for r in rows:
        kw = re.sub(r"\s+", " ", str(r.get("keyword") or "")).strip()[:60]
        if not kw:
            continue
        if kw.lower() in seen:
            raise DealError(f"같은 제품 이름이 두 번 있습니다: {kw}")
        seen.add(kw.lower())
        months = r.get("months")
        if months is not None and not (0 <= int(months) <= MONTHS_MAX):
            raise DealError(f"무상 기간은 0~{MONTHS_MAX}개월로 적어 주세요: {kw}")
        clean.append({"k": kw, "m": None if months is None else int(months), "n": str(r.get("note") or "")[:NOTE_MAX]})
    await db.execute(text("DELETE FROM warranty_policies"))
    for i, r in enumerate(clean):
        await db.execute(text("INSERT INTO warranty_policies (keyword, months, note, sort, updated_by) "
                              "VALUES (:k, :m, :n, :s, :u)"), {**r, "s": i, "u": user_id})
    await db.commit()
    return await policies(db)


# ── 견적서 판 찾기 ───────────────────────────────────────────────────────────

async def _issue(db: AsyncSession, d: dict, quote_id: int, revision: int) -> dict:
    docs = await deal_quotes(db, d.get("proposal_id"), d["id"])
    if not any(q["id"] == quote_id for q in docs):
        raise DealError("이 영업 건의 견적서가 아닙니다.")
    raw = (await db.execute(text("SELECT history FROM product_quotes WHERE id = :i"), {"i": quote_id})).scalar()
    hist = json.loads(raw) if isinstance(raw, str) else (raw or [])
    h = next((x for x in hist if x.get("revision") == revision), None)
    if not h:
        raise DealError("견적서 판을 찾을 수 없습니다. 발행한 판에만 시리얼을 등록합니다.")
    return {"quote_id": quote_id, "revision": revision, "issue_no": _issue_no(h), "issued_at": h.get("issued_at"),
            "total": h.get("total"), "currency": h.get("currency"), "lines": target_lines(h.get("lines") or [])}


async def won_issue(db: AsyncSession, d: dict) -> tuple[int, int] | None:
    """수주 중인 판(영업 건 견적의 발행번호와 같은 판) → (견적서 id, 판). 없으면 None(발행 전·예전 이관 건)."""
    no = (d.get("quote") or {}).get("no")
    if not no:
        return None
    for q in await deal_quotes(db, d.get("proposal_id"), d["id"]):
        for h in q["issues"]:
            if h.get("issue_no") == no:
                return q["id"], int(h["revision"])
    return None


# ── 시리얼 읽기·저장 ─────────────────────────────────────────────────────────

def _unit_view(r: Any) -> dict:
    u = dict(r)
    if isinstance(u.get("deal_rental"), str):
        u["deal_rental"] = json.loads(u["deal_rental"])
    for k in ("warranty_start", "warranty_until", "created_at", "updated_at"):
        u[k] = _iso(u.get(k))
    return u


_UNIT_COLS = ("id, deal_id, quote_id, quote_revision, issue_no, line_no, line_name, part_name, serial, no_serial, maker, purpose, "
              "warranty_months, warranty_start, warranty_until, shipment_index, status, note, created_at, updated_at")


# 시리얼이 속한 프로젝트(영업 건) — 제목·판매/대여·대여 조건. 시리얼 찾기에서 [프로젝트 건 열기]로 그 건 상세로 간다(사용자 2026-10-22)
_DEAL_COLS = ("s.title AS deal_title, COALESCE(s.quote->>'no', s.deal_no) AS deal_display_no, "
              "COALESCE(s.quote->>'biz', 'sale') AS deal_biz, s.quote->'rental' AS deal_rental")


def _summary(lines: list[dict]) -> dict:
    """등록 현황 — 줄마다 수량만큼 적었으면 그 줄은 끝(부품으로 나눠 더 적은 건 수량까지만 센다)."""
    total = sum(ln["qty"] for ln in lines)
    done = sum(min(len(ln["units"]), ln["qty"]) for ln in lines)
    return {"total": total, "done": done, "missing": total - done,
            "missing_lines": [ln["name"].splitlines()[0][:40] for ln in lines if len(ln["units"]) < ln["qty"]]}


async def issue_units(db: AsyncSession, deal_id: int, quote_id: int, revision: int) -> dict:
    """견적서 판 하나의 시리얼 등록 화면 값 — 품목 줄마다 등록한 부품, 무상 기간 기본값 제안, 등록 현황."""
    r = (await db.execute(text(f"SELECT {COLS} FROM sales_deals WHERE id = :i"), {"i": deal_id})).mappings().first()
    if not r:
        raise DealError("건을 찾을 수 없습니다.", 404)
    d = _row(r)
    iss = await _issue(db, d, quote_id, revision)
    rs = (await db.execute(text(f"SELECT {_UNIT_COLS} FROM product_units WHERE deal_id = :d AND quote_id = :q "
                                "AND quote_revision = :r ORDER BY line_no, id"),
                           {"d": deal_id, "q": quote_id, "r": revision})).mappings().all()
    pol = await policies(db)
    lines = []
    for ln in iss["lines"]:
        hit = match_policy(pol, default_part(ln["name"]), ln["name"])
        lines.append({**ln, "part": default_part(ln["name"]), "suggest_months": hit["months"] if hit else None,
                      "units": [_unit_view(r) for r in rs if r["line_no"] == ln["line_no"]]})
    won = await won_issue(db, d)
    others = (await db.execute(text("SELECT DISTINCT issue_no FROM product_units WHERE deal_id = :d AND NOT "
                                    "(quote_id = :q AND quote_revision = :r) AND issue_no IS NOT NULL"),
                               {"d": deal_id, "q": quote_id, "r": revision})).scalars().all()
    return {**{k: iss[k] for k in ("quote_id", "revision", "issue_no", "issued_at", "total", "currency")},
            "customer": d.get("customer"), "won": bool(d.get("won")),
            # 대여 견적서로 낸 건(사용자 2026-10-22) — 화면이 새 칸의 상태를 R(대여)로 미리 고른다
            "biz": (d.get("quote") or {}).get("biz") or "sale",
            "is_won_issue": won == (quote_id, revision), "won_issue_no": (d.get("quote") or {}).get("no") if won else None,
            "other_issues": list(others), "lines": lines, "summary": _summary(lines),
            "policies": pol}


async def save_units(db: AsyncSession, deal_id: int, user_id: int | None, quote_id: int, revision: int,
                     payload: list[dict]) -> dict:
    """판 하나의 시리얼을 통째로 저장 — payload = [{line_no, units: [{id?, part_name, serial, maker, purpose,
    warranty_months, note}]}]. 빈 칸(시리얼 없음)은 저장하지 않는다. 이 판에서 빠진 줄은 지우되, 출하했거나 A/S 기록이 있으면 막는다."""
    d = await _lock(db, deal_id)
    iss = await _issue(db, d, quote_id, revision)
    by_line = {ln["line_no"]: ln for ln in iss["lines"]}
    old = {r["id"]: r for r in (await db.execute(text(
        f"SELECT {_UNIT_COLS} FROM product_units WHERE deal_id = :d AND quote_id = :q AND quote_revision = :r FOR UPDATE"),
        {"d": deal_id, "q": quote_id, "r": revision})).mappings().all()}
    keep: list[tuple[int | None, dict]] = []
    seen: dict[tuple[str, str], str] = {}
    for blk in payload:
        ln = by_line.get(blk.get("line_no"))
        if ln is None:
            raise DealError("견적서 판에 없는 품목 줄입니다. 화면을 새로 열어 주세요.")
        for u in blk.get("units") or []:
            serial = re.sub(r"\s+", " ", str(u.get("serial") or "")).strip()[:SERIAL_MAX]
            if not serial:
                continue
            part = re.sub(r"\s+", " ", str(u.get("part_name") or "")).strip()[:PART_MAX] or default_part(ln["name"])
            maker = re.sub(r"\s+", " ", str(u.get("maker") or "")).strip()[:MAKER_MAX] or "UND"
            purpose = state_code(u.get("purpose"))
            months = u.get("warranty_months")
            if months is not None and not (0 <= int(months) <= MONTHS_MAX):
                raise DealError(f"무상 기간은 0~{MONTHS_MAX}개월로 적어 주세요({part}).")
            no_serial = is_no_serial(serial)
            if not no_serial:
                key = (maker.lower(), serial.lower())
                if key in seen:
                    raise DealError(f"같은 시리얼이 두 번 있습니다: {serial} ({seen[key]}, {part})")
                seen[key] = part
            uid = u.get("id") if u.get("id") in old else None
            keep.append((uid, {"line_no": ln["line_no"], "line_name": ln["name"], "part_name": part, "serial": serial,
                               "no_serial": no_serial, "maker": maker, "purpose": purpose,
                               "warranty_months": None if months is None else int(months),
                               "note": str(u.get("note") or "")[:NOTE_MAX]}))
    gone = [i for i in old if i not in {uid for uid, _ in keep}]
    if gone:
        busy = [old[i]["serial"] for i in gone if old[i]["shipment_index"] is not None]
        busy += (await db.execute(text("SELECT DISTINCT serial FROM as_cases WHERE unit_id = ANY(:g)"), {"g": gone})).scalars().all()
        if busy:
            raise DealError("출하했거나 A/S 기록이 있는 시리얼은 지울 수 없습니다: " + ", ".join(dict.fromkeys(busy)))
    # 다른 견적서 판·다른 건에 이미 있는 시리얼(같은 제조사)
    reals = [(v["maker"].lower(), v["serial"].lower(), uid) for uid, v in keep if not v["no_serial"]]
    if reals:
        dup = (await db.execute(text(
            "SELECT u.serial, u.maker, u.issue_no, s.customer FROM product_units u LEFT JOIN sales_deals s ON s.id = u.deal_id "
            "WHERE NOT u.no_serial AND (lower(u.maker), lower(u.serial)) IN (SELECT * FROM unnest(CAST(:m AS text[]), CAST(:s AS text[]))) "
            "AND NOT (u.deal_id IS NOT DISTINCT FROM :d AND u.quote_id IS NOT DISTINCT FROM :q AND u.quote_revision IS NOT DISTINCT FROM :r)"),
            {"m": [m for m, _, _ in reals], "s": [s for _, s, _ in reals], "d": deal_id, "q": quote_id, "r": revision})).mappings().all()
        if dup:
            x = dup[0]
            raise DealError(f"이미 등록된 시리얼입니다: {x['serial']} ({x['maker']}) — {x['customer'] or '고객사 없음'} "
                            f"{x['issue_no'] or ''}".rstrip())
    if gone:
        await db.execute(text("DELETE FROM product_units WHERE id = ANY(:g)"), {"g": gone})
    for uid, v in keep:
        if uid is None:
            continue
        o = old[uid]
        # 출하한 부품은 무상 기간을 바꾸면 종료일도 다시(시작일 = 출하일 그대로)
        until = warranty_until(o["warranty_start"], v["warranty_months"]) if o["warranty_start"] else None
        await db.execute(text(
            "UPDATE product_units SET line_no = :line_no, line_name = :line_name, part_name = :part_name, serial = :serial, "
            "no_serial = :no_serial, maker = :maker, purpose = :purpose, warranty_months = :warranty_months, note = :note, "
            "warranty_until = :until, updated_by = :u, updated_at = NOW() WHERE id = :id"),
            {**v, "until": until, "u": user_id, "id": uid})
    for uid, v in keep:
        if uid is None:
            await db.execute(text(
                "INSERT INTO product_units (deal_id, quote_id, quote_revision, issue_no, line_no, line_name, part_name, serial, "
                "no_serial, maker, purpose, warranty_months, note, created_by, updated_by) VALUES (:d, :q, :r, :no, :line_no, "
                ":line_name, :part_name, :serial, :no_serial, :maker, :purpose, :warranty_months, :note, :u, :u)"),
                {**v, "d": deal_id, "q": quote_id, "r": revision, "no": iss["issue_no"], "u": user_id})
    await _log(db, deal_id, user_id, "units", {"no": iss["issue_no"], "count": len(keep), "removed": len(gone)})
    await db.commit()
    return await issue_units(db, deal_id, quote_id, revision)


async def won_summary(db: AsyncSession, d: dict) -> dict | None:
    """수주 판의 시리얼 등록 현황(영업 건 상세·[AI 와 수주 진행] 안내·출하 전 확인). 수주 판이 없으면 None."""
    won = await won_issue(db, d)
    if not won:
        return None
    raw = (await db.execute(text("SELECT history FROM product_quotes WHERE id = :i"), {"i": won[0]})).scalar()
    hist = json.loads(raw) if isinstance(raw, str) else (raw or [])
    h = next((x for x in hist if x.get("revision") == won[1]), None)
    lines = target_lines((h or {}).get("lines") or [])
    if not lines:
        return None
    counts = dict((await db.execute(text(
        "SELECT line_no, COUNT(*) FROM product_units WHERE deal_id = :d AND quote_id = :q AND quote_revision = :r GROUP BY line_no"),
        {"d": d["id"], "q": won[0], "r": won[1]})).all())
    s = _summary([{**ln, "units": [None] * counts.get(ln["line_no"], 0)} for ln in lines])
    return {"quote_id": won[0], "revision": won[1], "issue_no": (d.get("quote") or {}).get("no"), **s}


async def check_before_ship(db: AsyncSession, d: dict) -> tuple[int, int] | None:
    """출하 전 확인 — 수주 판의 시리얼(시리얼 없는 품목은 '특주' 등)을 다 적었는지. 반환 = 수주 판(없으면 None)."""
    s = await won_summary(db, d)
    if s and s["missing"] > 0:
        raise DealError(f"출하 전에 제품 시리얼 번호를 등록해 주세요 — {s['issue_no']} 기준 {s['total']}개 중 {s['done']}개 등록. "
                        "시리얼이 없는 품목은 '특주'처럼 적으면 됩니다.", code="SERIALS_REQUIRED")
    return (s["quote_id"], s["revision"]) if s else None


async def ship_units(db: AsyncSession, deal_id: int, index: int, day: dt.date, unit_ids: list[int] | None,
                     issue: tuple[int, int] | None) -> int:
    """출하 기록 index 에 부품을 묶는다 — 출하일 = 무상 기간 시작일, 종료일 확정. unit_ids None 이면 수주 판에서 아직 안 나간 것 전부.
    이 출하에 묶였다가 빠진 부품은 '등록'으로 되돌린다. 반환 = 묶은 개수."""
    if unit_ids is None:
        if not issue:
            return 0
        unit_ids = list((await db.execute(text(
            "SELECT id FROM product_units WHERE deal_id = :d AND quote_id = :q AND quote_revision = :r AND shipment_index IS NULL"),
            {"d": deal_id, "q": issue[0], "r": issue[1]})).scalars().all())
    await db.execute(text("UPDATE product_units SET shipment_index = NULL, warranty_start = NULL, warranty_until = NULL, "
                          "status = '등록', updated_at = NOW() WHERE deal_id = :d AND shipment_index = :x AND NOT (id = ANY(:ids))"),
                     {"d": deal_id, "x": index, "ids": unit_ids})
    rs = (await db.execute(text("SELECT id, warranty_months, shipment_index FROM product_units WHERE deal_id = :d AND id = ANY(:ids)"),
                           {"d": deal_id, "ids": unit_ids})).mappings().all()
    if len(rs) != len(set(unit_ids)):
        raise DealError("이 영업 건의 시리얼이 아닌 것이 있습니다. 화면을 새로 열어 주세요.")
    if any(r["shipment_index"] is not None and r["shipment_index"] != index for r in rs):
        raise DealError("이미 다른 출하로 나간 시리얼이 있습니다.")
    for r in rs:
        await db.execute(text("UPDATE product_units SET shipment_index = :x, warranty_start = :s, warranty_until = :w, "
                              "status = '출하', updated_at = NOW() WHERE id = :i"),
                         {"x": index, "s": day, "w": warranty_until(day, r["warranty_months"]), "i": r["id"]})
    return len(rs)


async def shipment_removed(db: AsyncSession, deal_id: int, index: int | None) -> None:
    """출하 기록 삭제(index) 또는 견적 상태로 되돌림(None) — 묶여 있던 부품을 '등록'으로, 뒤 출하 번호는 하나씩 당긴다."""
    cond = "" if index is None else " AND shipment_index = :x"
    await db.execute(text("UPDATE product_units SET shipment_index = NULL, warranty_start = NULL, warranty_until = NULL, "
                          f"status = '등록', updated_at = NOW() WHERE deal_id = :d AND shipment_index IS NOT NULL{cond}"),
                     {"d": deal_id, "x": index})
    if index is not None:
        await db.execute(text("UPDATE product_units SET shipment_index = shipment_index - 1 WHERE deal_id = :d AND shipment_index > :x"),
                         {"d": deal_id, "x": index})


async def deal_units(db: AsyncSession, deal_id: int) -> list[dict]:
    """영업 건의 부품 전부(출하 창 체크 목록)."""
    rs = (await db.execute(text(f"SELECT {_UNIT_COLS} FROM product_units WHERE deal_id = :d ORDER BY quote_id, quote_revision, "
                                "line_no, id"), {"d": deal_id})).mappings().all()
    return [_unit_view(r) for r in rs]


# ── 조회(제품·A/S 탭) ────────────────────────────────────────────────────────

def warranty_state(u: dict, day: dt.date) -> str:
    """'무상 기간 (D-120)' / '무상 기간 지남' / '출하 전' / '무상 기간 미정'."""
    if not u.get("warranty_start"):
        return "출하 전"
    until = u.get("warranty_until")
    if until is None:
        return "무상 기간 미정"
    until = dt.date.fromisoformat(until) if isinstance(until, str) else until
    return f"무상 기간 (D-{(until - day).days})" if until >= day else "무상 기간 지남"


async def search_units(db: AsyncSession, q: str, today: dt.date, limit: int = 50) -> list[dict]:
    """시리얼·부품명·고객사·발행번호로 찾기(비우면 최근 등록 순). A/S 건수·진행 중 건수를 같이."""
    q = q.strip()[:80]
    rs = (await db.execute(text(
        f"SELECT {', '.join('u.' + c.strip() for c in _UNIT_COLS.split(','))}, s.customer, s.deal_no, {_DEAL_COLS}, "
        "  (SELECT COUNT(*) FROM as_cases a WHERE a.unit_id = u.id) AS as_count, "
        "  (SELECT COUNT(*) FROM as_cases a WHERE a.unit_id = u.id AND a.status <> '완료') AS as_open "
        "FROM product_units u LEFT JOIN sales_deals s ON s.id = u.deal_id "
        "WHERE :q = '' OR u.serial ILIKE :p OR u.part_name ILIKE :p OR u.line_name ILIKE :p OR s.customer ILIKE :p "
        "  OR u.issue_no ILIKE :p OR u.maker ILIKE :p "
        "ORDER BY u.updated_at DESC, u.id DESC LIMIT :n"),
        {"q": q, "p": f"%{q}%", "n": limit})).mappings().all()
    out = []
    for r in rs:
        u = _unit_view(r)
        out.append({**u, "warranty_state": warranty_state(u, today)})
    return out


async def unit_detail(db: AsyncSession, unit_id: int, today: dt.date) -> dict:
    r = (await db.execute(text(
        f"SELECT {', '.join('u.' + c.strip() for c in _UNIT_COLS.split(','))}, s.customer, s.deal_no, {_DEAL_COLS}, s.shipments "
        "FROM product_units u LEFT JOIN sales_deals s ON s.id = u.deal_id WHERE u.id = :i"), {"i": unit_id})).mappings().first()
    if not r:
        raise DealError("시리얼을 찾을 수 없습니다.", 404)
    u = _unit_view(r)
    ships = u.pop("shipments") or []
    ships = json.loads(ships) if isinstance(ships, str) else ships
    ship = ships[u["shipment_index"]] if u.get("shipment_index") is not None and u["shipment_index"] < len(ships) else None
    return {**u, "warranty_state": warranty_state(u, today),
            "shipment": {k: ship.get(k) for k in ("date", "carrier", "tracking", "receiver")} if ship else None,
            "cases": await list_cases(db, unit_id=unit_id)}


# ── A/S 기록 ─────────────────────────────────────────────────────────────────

def suggest_charge(unit: dict | None, received: dt.date, cause: str) -> tuple[bool | None, str]:
    """(무상 기간 안인지, 무상·유상 제안). 기간 안 + 고객 과실 아님 = 무상, 기간 지남·고객 과실 = 유상, 그 밖 = 미정."""
    until = (unit or {}).get("warranty_until")
    until = dt.date.fromisoformat(until) if isinstance(until, str) else until
    inside = None if until is None else received <= until
    if cause == "고객 과실" or inside is False:
        return inside, "유상"
    return inside, "무상" if inside else "미정"


_CASE_COLS = ("id, unit_id, deal_id, serial, part_name, customer, contact, photos, received_on, symptom, cause, in_warranty, charge, "
              "status, action, closed_on, note, history, created_at, updated_at, "
              "(SELECT COALESCE(x.alias, x.username) FROM users x WHERE x.id = as_cases.created_by) AS received_by")


def _case_view(r: Any) -> dict:
    c = dict(r)
    for k in ("received_on", "closed_on", "created_at", "updated_at"):
        c[k] = _iso(c.get(k))
    for k in ("history", "photos"):
        if isinstance(c.get(k), str):
            c[k] = json.loads(c[k])
    return c


async def list_cases(db: AsyncSession, *, unit_id: int | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
    rs = (await db.execute(text(
        f"SELECT {_CASE_COLS} FROM as_cases WHERE (CAST(:u AS BIGINT) IS NULL OR unit_id = :u) "
        "AND (CAST(:s AS TEXT) IS NULL OR status = :s) ORDER BY received_on DESC, id DESC LIMIT :n"),
        {"u": unit_id, "s": status, "n": limit})).mappings().all()
    return [_case_view(r) for r in rs]


def _clean_case(p: dict) -> dict:
    out = {}
    if "received_on" in p and p["received_on"] is not None:
        out["received_on"] = p["received_on"]
    if "photos" in p and p["photos"] is not None:
        photos = list(dict.fromkeys(p["photos"]))
        if len(photos) > AS_PHOTOS_MAX or any(not AS_PHOTO_ID.match(str(x)) for x in photos):
            raise DealError(f"사진을 다시 올려 주세요(최대 {AS_PHOTOS_MAX}장).")
        out["photos"] = photos
    for k, n in (("symptom", 1000), ("action", 1000), ("note", NOTE_MAX), ("customer", 100), ("contact", 200)):
        if k in p and p[k] is not None:
            out[k] = str(p[k]).strip()[:n]
    for k, allowed in (("cause", CAUSES), ("charge", CHARGES), ("status", AS_STATUS)):
        if k in p and p[k] is not None:
            if p[k] not in allowed:
                raise DealError(f"알 수 없는 값입니다: {p[k]}")
            out[k] = p[k]
    if "closed_on" in p:
        out["closed_on"] = p["closed_on"]
    return out


async def create_case(db: AsyncSession, user_id: int | None, unit_id: int, p: dict, *, commit: bool = True) -> dict:
    """A/S 접수 — 무상·유상을 고르지 않았으면 접수일·원인으로 제안한 값. commit=False 면 여러 건을 한 번에 저장할 때(create_cases)."""
    u = (await db.execute(text("SELECT u.id, u.deal_id, u.serial, u.part_name, u.warranty_until, s.customer FROM product_units u "
                               "LEFT JOIN sales_deals s ON s.id = u.deal_id WHERE u.id = :i"), {"i": unit_id})).mappings().first()
    if not u:
        raise DealError("시리얼을 찾을 수 없습니다.", 404)
    c = _clean_case(p)
    received = c.get("received_on") or dt.date.today()
    cause = c.get("cause", "확인 중")
    inside, suggested = suggest_charge(dict(u), received, cause)
    status = c.get("status", "접수")
    closed = c.get("closed_on") or (received if status == "완료" else None)
    new_id = (await db.execute(text(
        "INSERT INTO as_cases (unit_id, deal_id, serial, part_name, customer, contact, photos, received_on, symptom, cause, in_warranty, "
        "charge, status, action, closed_on, note, history, created_by) VALUES (:u, :d, :sn, :pn, :c, :ct, CAST(:ph AS jsonb), :r, :sy, "
        ":ca, :iw, :ch, :st, :ac, :cl, :no, CAST(:h AS jsonb), :by) RETURNING id"),
        {"u": unit_id, "d": u["deal_id"], "sn": u["serial"], "pn": u["part_name"], "c": c.get("customer") or u["customer"],
         "ct": c.get("contact", ""), "ph": json.dumps(c.get("photos", [])), "r": received,
         "sy": c.get("symptom", ""), "ca": cause, "iw": inside, "ch": c.get("charge", suggested), "st": status,
         "ac": c.get("action", ""), "cl": closed, "no": c.get("note", ""),
         "h": json.dumps([{"at": dt.datetime.now(dt.timezone.utc).isoformat(), "by": user_id, "what": "접수",
                           "suggested": suggested}], ensure_ascii=False), "by": user_id})).scalar_one()
    if u["deal_id"]:
        await _log(db, u["deal_id"], user_id, "as_case", {"serial": u["serial"], "status": status, "charge": c.get("charge", suggested)})
    if not commit:
        return {"id": new_id}
    await db.commit()
    return await get_case(db, new_id)


async def create_cases(db: AsyncSession, user_id: int | None, unit_ids: list[int], p: dict) -> list[dict]:
    """여러 시리얼을 한 접수서로(사용자 2026-10-22, 영업 건 상세 [A/S 접수]) — 시리얼마다 A/S 기록 한 건, 하나라도 안 되면 모두 안 남긴다.
    무상·유상을 고르지 않았으면 시리얼마다 그 무상 기간으로 제안한 값."""
    ids = list(dict.fromkeys(unit_ids))
    made = [await create_case(db, user_id, uid, p, commit=False) for uid in ids]
    await db.commit()
    return [await get_case(db, m["id"]) for m in made]


async def get_case(db: AsyncSession, case_id: int) -> dict:
    r = (await db.execute(text(f"SELECT {_CASE_COLS} FROM as_cases WHERE id = :i"), {"i": case_id})).mappings().first()
    if not r:
        raise DealError("A/S 기록을 찾을 수 없습니다.", 404)
    return _case_view(r)


async def _follow_unit_state(db: AsyncSession, case: dict, status: str) -> dict:
    """A/S 진행에 맞춰 제품 상태 코드(사용자 2026-10-22) — 진행 중이면 A(A/S 중), 완료·접수로 돌아가면 들어가기 전 상태로.
    같은 제품에 진행 중인 다른 A/S 가 있으면 A 를 그대로 둔다. 바꾼 값은 A/S 이력(unit_state: [전, 후])에 남겨 되돌릴 때 쓴다."""
    uid = case["unit_id"]
    cur = (await db.execute(text("SELECT purpose FROM product_units WHERE id = :i FOR UPDATE"), {"i": uid})).scalar()
    if cur is None:
        return {}
    if status == "진행 중":
        if cur == "A":
            return {}
        new = "A"
    else:
        if cur != "A":
            return {}
        others = (await db.execute(text("SELECT COUNT(*) FROM as_cases WHERE unit_id = :u AND id <> :c AND status = '진행 중'"),
                                   {"u": uid, "c": case["id"]})).scalar()
        if others:
            return {}
        before = next((h["unit_state"][0] for h in reversed(case.get("history") or [])
                       if isinstance(h, dict) and (h.get("unit_state") or [None, None])[1] == "A"), None)
        if before is None:                                   # 기록이 없으면 건의 판매·대여로
            biz = (await db.execute(text("SELECT COALESCE(s.quote->>'biz', 'sale') FROM product_units u "
                                         "LEFT JOIN sales_deals s ON s.id = u.deal_id WHERE u.id = :i"), {"i": uid})).scalar()
            before = "R" if biz == "rental" else "S"
        new = before
    await db.execute(text("UPDATE product_units SET purpose = :p, updated_at = NOW() WHERE id = :i"), {"p": new, "i": uid})
    return {"unit_state": [cur, new]}


async def delete_cases(db: AsyncSession, ids: list[int], user_id: int | None) -> int:
    """A/S 기록 지우기(관리자, 사용자 2026-10-22) — 옛 데이터 보관함(trash_bin)에 통째로 옮긴 뒤 지운다(되살릴 수 있게).
    진행 중이던 A/S 면 제품 상태 A(A/S 중)를 들어가기 전 상태로 되돌린다. 시리얼은 지우지 않는다."""
    ids = sorted({i for i in ids if isinstance(i, int) and i > 0})
    if not ids:
        raise DealError("지울 A/S 기록을 골라 주세요.")
    rs = (await db.execute(text(f"SELECT {_CASE_COLS} FROM as_cases WHERE id = ANY(:i) FOR UPDATE"), {"i": ids})).mappings().all()
    if len(rs) != len(ids):
        raise DealError("이미 지워졌거나 없는 A/S 기록이 있습니다. 목록을 새로 고친 뒤 다시 골라 주세요.", 409)
    for r in rs:
        c = _case_view(r)
        if c["status"] == "진행 중" and c["unit_id"]:
            await db.execute(text("UPDATE as_cases SET status = '완료' WHERE id = :i"), {"i": c["id"]})   # 다른 진행 중 건 판단에서 빼기
            await _follow_unit_state(db, c, "완료")
    await db.execute(text(
        "INSERT INTO trash_bin (source_table, source_id, label, data, reason) "
        "SELECT 'as_cases', a.id, a.serial || ' · ' || COALESCE(a.customer, ''), to_jsonb(a), :why FROM as_cases a WHERE a.id = ANY(:i) "
        "ON CONFLICT (source_table, source_id) DO NOTHING"), {"i": ids, "why": f"관리자 삭제(user {user_id})"})
    await db.execute(text("DELETE FROM as_cases WHERE id = ANY(:i)"), {"i": ids})
    for r in rs:
        if r["deal_id"]:
            await _log(db, r["deal_id"], user_id, "as_case_delete", {"serial": r["serial"], "status": r["status"]})
    await db.commit()
    return len(ids)


async def update_case(db: AsyncSession, user_id: int | None, case_id: int, p: dict) -> dict:
    """A/S 진행 고치기 — 바뀐 칸을 이력에 남긴다. 완료로 바꾸면 완료일(비었으면 오늘)."""
    r = (await db.execute(text(f"SELECT {_CASE_COLS} FROM as_cases WHERE id = :i FOR UPDATE"), {"i": case_id})).mappings().first()
    if not r:
        raise DealError("A/S 기록을 찾을 수 없습니다.", 404)
    old = _case_view(r)
    c = _clean_case(p)
    if c.get("status") == "완료" and not (c.get("closed_on") or old["closed_on"]):
        c["closed_on"] = dt.date.today()
    if c.get("status") in ("접수", "진행 중") and "closed_on" not in p:
        c["closed_on"] = None
    if "received_on" in c:                               # 접수일이 바뀌면 무상 기간 안인지 다시
        until = (await db.execute(text("SELECT warranty_until FROM product_units WHERE id = :i"), {"i": old["unit_id"]})).scalar()
        c["in_warranty"] = None if until is None else c["received_on"] <= until
    changed = {k: [old.get(k), _iso(v)] for k, v in c.items() if _iso(old.get(k)) != _iso(v)}
    if not changed:
        return old
    sets = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k == "photos" else f"{k} = :{k}" for k in c)
    entry: dict[str, Any] = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "by": user_id, "changed": changed}
    if "status" in changed and old["unit_id"]:
        entry.update(await _follow_unit_state(db, old, c["status"]))
    hist = old["history"] + [entry]
    params = {**c, "h": json.dumps(hist, ensure_ascii=False, default=str), "i": case_id}
    if "photos" in c:
        params["photos"] = json.dumps(c["photos"])
    await db.execute(text(f"UPDATE as_cases SET {sets}, history = CAST(:h AS jsonb), updated_at = NOW() WHERE id = :i"), params)
    await db.commit()
    return await get_case(db, case_id)
