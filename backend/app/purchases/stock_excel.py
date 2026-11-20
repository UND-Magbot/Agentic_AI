"""공장 제품 재고 엑셀(사용자 2026-11-20) — 내려받기 · 고친 엑셀을 올려 상태·메모 한 번에 반영.

내려받기: 판매용 / 보유 후 자체 개발용 두 시트 + 안내 시트. 줄마다 '재고 번호'(stock_items.id)가 있어 다시 올리면 그 번호로 정확히
짝을 짓는다. 고치는 칸은 '상태'(목록에서 고르기)와 그 옆 '상태 메모' 두 칸뿐(색칠) — 다른 칸을 고쳐도 반영하지 않는다.

적용(미리보기 → 확인 → 반영):
1. 재고 번호로 짝 → 2. 번호가 없거나 맞지 않으면 제조사 S/N·UND S/N 이 같은 줄 → 3. 그래도 못 찾은 줄(상태·메모를 적은 것만)은
   사내 AI 가 제품명·제조사·고객사·입고일로 후보 중 하나를 고른다('AI 추정 — 확인 필요', 화면에서 처음엔 체크 안 함).
상태가 목록에 없는 값이거나 '대여 중'을 손으로 넣고 빼는 줄은 반영하지 않고 까닭을 보여 준다. 반영은 화면에서 고른 줄만
stock.update_item 으로(이력에 '엑셀' 표시).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import re
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy.ext.asyncio import AsyncSession

from . import stock

log = logging.getLogger(__name__)

SHEETS = {"sale": "판매용", "dev": "보유 후 자체 개발용"}
ID_COL = "재고 번호"
STATUS_COL = "상태"
NOTE_COL = "상태 메모"
# 시트마다 열 — (머리글, 값 꺼내기)
_COMMON_HEAD = [ID_COL, "구분", "제조사", "제품명", "제조사 S/N", "UND S/N"]
_TAIL = ["입고일", "국내/해외", "구매 건", STATUS_COL, NOTE_COL]
HEADS = {
    "sale": _COMMON_HEAD + ["판매 고객사", "우리 담당자"] + _TAIL,
    "dev": _COMMON_HEAD + ["S/W 담당자", "영업팀 담당자"] + _TAIL,
}
WIDTH = {ID_COL: 10, "구분": 10, "제조사": 16, "제품명": 30, "제조사 S/N": 18, "UND S/N": 18, "판매 고객사": 18, "우리 담당자": 12,
         "S/W 담당자": 12, "영업팀 담당자": 12, "입고일": 12, "국내/해외": 9, "구매 건": 22, STATUS_COL: 12, NOTE_COL: 40}
MAX_ROWS = 5000            # 올린 엑셀에서 읽는 줄 수(시트마다)
AI_MAX_ROWS = 40           # AI 로 짝을 찾는 줄 수(번호·S/N 으로 못 찾은 것 중)
AI_MAX_CANDIDATES = 300

HEAD_FILL = PatternFill("solid", fgColor="1F2937")
EDIT_FILL = PatternFill("solid", fgColor="FFF4C2")
EDIT_HEAD_FILL = PatternFill("solid", fgColor="B45309")


class ExcelError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _label(r: dict) -> str:
    return (r.get("display_name") or r.get("name") or "").strip()


def _values(purpose: str, r: dict) -> list[Any]:
    region = "해외" if r.get("region") == "overseas" else "국내"
    source = "직접 등록" if r.get("direct") else " · ".join(x for x in (r.get("supplier") or "", r.get("po_no") or "") if x)
    sn = [] if r.get("no_sn") else [r.get("maker_sn") or "", r.get("und_sn") or ""]
    sn = sn or ["S/N 없는 품목", ""]
    who = [r.get("customer") or "", r.get("owner") or ""] if purpose == "sale" else [r.get("sw_owner") or "", r.get("owner") or ""]
    return [r["id"], r.get("category") or "", r.get("maker") or "", _label(r), *sn, *who,
            r.get("received_on") or "", region, source, r.get("status") or "", r.get("status_note") or ""]


async def export(db: AsyncSession) -> bytes:
    """두 시트(판매용·보유 후 자체 개발용) + 안내. 상태 칸은 고르기 목록, 고치는 칸(상태·상태 메모)은 색칠."""
    wb = Workbook()
    guide = wb.active
    guide.title = "안내"
    lines = [
        "공장 제품 재고 — 엑셀로 상태·메모 고치기",
        "",
        "1. '판매용'·'보유 후 자체 개발용' 시트에서 노란 칸(상태·상태 메모)만 고칩니다. 다른 칸은 고쳐도 반영되지 않습니다.",
        f"2. 상태는 목록에서 고릅니다: {', '.join(s for s in stock.STATES if s != stock.RENTED)}.",
        "   '대여 중'은 영업 건 대여 S/N 등록으로만 바뀌고 회수하면 '대기 중'으로 돌아갑니다(엑셀로 넣고 빼지 않음).",
        "3. 상태에 대한 메모는 바로 옆 '상태 메모' 칸에 적습니다.",
        "4. '재고 번호'는 지우거나 바꾸지 마세요 — 다시 올릴 때 이 번호로 줄을 찾습니다.",
        "   번호가 없는 줄은 S/N 으로, 그래도 못 찾으면 AI 가 제품명·고객사로 찾아 '확인 필요'로 보여 줍니다.",
        "5. 공장 제품 재고 관리 → [재고 엑셀 적용]으로 올리면 바뀌는 줄을 먼저 보여 주고, 확인한 줄만 반영합니다.",
        "",
        f"내려받은 때: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    for i, t in enumerate(lines, start=1):
        c = guide.cell(row=i, column=1, value=t)
        if i == 1:
            c.font = Font(bold=True, size=13)
    guide.column_dimensions["A"].width = 110
    for purpose, title in SHEETS.items():
        ws = wb.create_sheet(title)
        heads = HEADS[purpose]
        ws.append(heads)
        for j, h in enumerate(heads, start=1):
            c = ws.cell(row=1, column=j)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = EDIT_HEAD_FILL if h in (STATUS_COL, NOTE_COL) else HEAD_FILL
            c.alignment = Alignment(vertical="center")
            ws.column_dimensions[c.column_letter].width = WIDTH.get(h, 14)
        rows = (await stock.list_items(db, purpose))["rows"]
        for r in rows:
            ws.append(_values(purpose, r))
        n = ws.max_row
        si, ni = heads.index(STATUS_COL) + 1, heads.index(NOTE_COL) + 1
        for i in range(2, n + 1):
            ws.cell(row=i, column=si).fill = EDIT_FILL
            ws.cell(row=i, column=ni).fill = EDIT_FILL
            ws.cell(row=i, column=ni).alignment = Alignment(wrap_text=True, vertical="top")
        letter = ws.cell(row=1, column=si).column_letter
        dv = DataValidation(type="list", formula1='"' + ",".join(stock.STATES) + '"', allow_blank=True,
                            showErrorMessage=True, errorTitle="상태", error="목록에서 골라 주세요.")
        ws.add_data_validation(dv)
        dv.add(f"{letter}2:{letter}{max(n, 2) + 200}")
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
    wb.active = 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    return re.sub(r"[ \t]+", " ", str(v)).strip()


def parse(data: bytes) -> list[dict]:
    """올린 엑셀 → 줄 목록 {purpose, row, id, name, maker, maker_sn, und_sn, who, received_on, status, note}.
    시트 이름으로 목적을 정하고, 열은 머리글 글자로 찾는다(열 순서를 바꿔도 됨)."""
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 — 엑셀이 아니거나 깨진 파일
        raise ExcelError("엑셀 파일(.xlsx)을 읽지 못했습니다. 내려받은 재고 엑셀을 고쳐 올려 주세요.") from e
    out: list[dict] = []
    found = False
    for purpose, title in SHEETS.items():
        if title not in wb.sheetnames:
            continue
        ws = wb[title]
        rows = ws.iter_rows(values_only=True)
        head = next(rows, None)
        if not head:
            continue
        col = {_text(h): k for k, h in enumerate(head) if _text(h)}
        if STATUS_COL not in col:
            continue
        found = True
        who_col = "판매 고객사" if purpose == "sale" else "S/W 담당자"
        for n, vals in enumerate(rows, start=2):
            if n > MAX_ROWS + 1:
                break

            def get(name: str) -> str:
                k = col.get(name)
                return _text(vals[k]) if k is not None and k < len(vals) else ""

            raw_id = get(ID_COL)
            row = {"purpose": purpose, "sheet": title, "row": n, "id": int(raw_id) if raw_id.isdigit() else None,
                   "name": get("제품명"), "maker": get("제조사"), "maker_sn": get("제조사 S/N"), "und_sn": get("UND S/N"),
                   "who": get(who_col), "received_on": get("입고일"), "status": get(STATUS_COL), "note": get(NOTE_COL)}
            if not any(row[k] for k in ("name", "maker_sn", "und_sn", "status", "note")) and row["id"] is None:
                continue                                   # 빈 줄
            out.append(row)
    if not found:
        raise ExcelError("재고 엑셀의 '판매용'·'보유 후 자체 개발용' 시트(상태 칸)를 찾지 못했습니다. [엑셀 내려받기]로 받은 파일을 고쳐 올려 주세요.")
    return out


async def _ai_match(rows: list[dict], cands: list[dict]) -> dict[int, tuple[int, str]]:
    """번호·S/N 으로 못 찾은 줄 → 사내 AI 가 후보 중 하나(없으면 null). 반환 {줄 순번: (재고 번호, 까닭)}."""
    if not rows or not cands:
        return {}
    from .. import proposal_llm
    from ..config import settings

    system = ("너는 공장 제품 재고 대조 담당이다. [엑셀 줄]마다 [재고 후보] 중 같은 제품 한 대의 id 를 고른다. "
              "제품명·제조사·고객사(또는 담당자)·입고일이 맞아야 한다. 확실하지 않으면 id 를 null 로 둔다. 후보 하나는 한 줄에만 쓴다. "
              'JSON {"matches": [{"row": 줄 번호, "id": 후보 id 또는 null, "why": "짧은 까닭"}]} 로만 답한다.')
    body = {"엑셀 줄": [{"row": i, "제품명": r["name"], "제조사": r["maker"], "고객사·담당자": r["who"], "입고일": r["received_on"],
                        "제조사 S/N": r["maker_sn"], "UND S/N": r["und_sn"]} for i, r in enumerate(rows)],
            "재고 후보": [{"id": c["id"], "제품명": _label(c), "제조사": c.get("maker") or "",
                          "고객사·담당자": (c.get("customer") if c.get("purpose") == "sale" else c.get("sw_owner")) or c.get("owner") or "",
                          "입고일": c.get("received_on") or "", "제조사 S/N": c.get("maker_sn") or "", "UND S/N": c.get("und_sn") or ""}
                         for c in cands]}
    try:
        raw = await proposal_llm.chat([{"role": "system", "content": system},
                                       {"role": "user", "content": json.dumps(body, ensure_ascii=False)}],
                                      fmt="json", think=False, num_predict=3000, temperature=0, model=settings.ollama_model)
        got = json.loads(raw).get("matches") or []
    except Exception as e:  # noqa: BLE001 — AI 가 안 되면 '찾지 못함'으로 둔다
        log.warning("stock excel ai match failed: %s", e)
        return {}
    ok_ids = {c["id"] for c in cands}
    used: set[int] = set()
    out: dict[int, tuple[int, str]] = {}
    for m in got:
        if not isinstance(m, dict):
            continue
        i, cid = m.get("row"), m.get("id")
        if not isinstance(i, int) or not 0 <= i < len(rows) or not isinstance(cid, int) or cid not in ok_ids or cid in used or i in out:
            continue
        used.add(cid)
        out[i] = (cid, str(m.get("why") or "")[:120])
    return out


def _check_status(cur: dict, status: str) -> str | None:
    """바꿀 수 없는 상태면 까닭."""
    if status not in stock.STATES:
        return f"'{status}'는 상태 목록에 없습니다({', '.join(s for s in stock.STATES if s != stock.RENTED)})."
    if status != cur["status"] and stock.RENTED in (status, cur["status"]):
        return "'대여 중'은 영업 건 대여로만 바뀝니다(회수하면 자동으로 '대기 중')."
    return None


async def preview(db: AsyncSession, data: bytes) -> dict:
    """올린 엑셀 → 바뀌는 줄 미리보기. 아무것도 저장하지 않는다."""
    rows = parse(data)
    items = {p: (await stock.list_items(db, p))["rows"] for p in SHEETS}
    by_id = {r["id"]: r for xs in items.values() for r in xs}
    by_sn: dict[tuple[str, str], int] = {}
    for p, xs in items.items():
        for r in xs:
            for sn in (r.get("maker_sn"), r.get("und_sn")):
                if sn and not r.get("no_sn"):
                    by_sn.setdefault((p, sn.lower()), r["id"])
    changes: list[dict] = []
    problems: list[dict] = []
    left: list[dict] = []
    seen: set[int] = set()
    same = 0

    def where(r: dict) -> str:
        return f"{r['sheet']} {r['row']}행"

    def add(r: dict, cur: dict, how: str, why: str = "") -> None:
        nonlocal same
        if cur["id"] in seen:
            problems.append({"where": where(r), "name": r["name"] or _label(cur), "why": "같은 제품이 엑셀에 두 번 있습니다(앞 줄만 반영)."})
            return
        seen.add(cur["id"])
        status = r["status"] or cur["status"]          # 상태 칸을 비웠으면 그대로
        note = r["note"][:500]
        if status == cur["status"] and note == (cur.get("status_note") or ""):
            same += 1
            return
        bad = _check_status(cur, status)
        if bad:
            problems.append({"where": where(r), "name": _label(cur), "why": bad})
            return
        changes.append({"id": cur["id"], "purpose": cur["purpose"], "where": where(r), "how": how, "why": why,
                        "name": _label(cur), "maker": cur.get("maker") or "",
                        "sn": " · ".join(x for x in (cur.get("maker_sn"), cur.get("und_sn")) if x) or ("S/N 없는 품목" if cur.get("no_sn") else ""),
                        "who": (cur.get("customer") if cur["purpose"] == "sale" else cur.get("sw_owner")) or cur.get("owner") or "",
                        "from_status": cur["status"], "to_status": status,
                        "from_note": cur.get("status_note") or "", "to_note": note})

    for r in rows:
        cur = by_id.get(r["id"]) if r["id"] is not None else None
        if cur is not None and cur["purpose"] == r["purpose"]:
            add(r, cur, "재고 번호")
            continue
        sid = next((by_sn[(r["purpose"], sn.lower())] for sn in (r["und_sn"], r["maker_sn"]) if sn and (r["purpose"], sn.lower()) in by_sn), None)
        if sid is not None:
            add(r, by_id[sid], "S/N")
            continue
        if r["status"] or r["note"]:
            left.append(r)
        elif r["id"] is not None:
            problems.append({"where": where(r), "name": r["name"], "why": "이 재고 번호의 제품이 지금 목록에 없습니다(판매로 빠졌거나 지운 것)."})
    ai_rows = left[:AI_MAX_ROWS]
    for r in left[AI_MAX_ROWS:]:
        problems.append({"where": where(r), "name": r["name"], "why": "재고 번호·S/N 으로 찾지 못했습니다(한 번에 AI 로 찾는 줄 수를 넘음)."})
    for purpose in SHEETS:
        mine = [r for r in ai_rows if r["purpose"] == purpose]
        cands = [c for c in items[purpose] if c["id"] not in seen][:AI_MAX_CANDIDATES]
        got = await _ai_match(mine, cands)
        for i, r in enumerate(mine):
            if i in got:
                add(r, by_id[got[i][0]], "AI 추정", got[i][1])
            else:
                problems.append({"where": where(r), "name": r["name"], "why": "재고 번호·S/N 이 없고 AI 도 같은 제품을 찾지 못했습니다."})
    return {"changes": changes, "problems": problems, "same": same, "rows": len(rows)}


async def apply(db: AsyncSession, user_id: int | None, picks: list[dict]) -> dict:
    """화면에서 고른 줄만 반영 {id, status, status_note}. 한 줄씩 — 안 되는 줄은 까닭과 함께 돌려준다."""
    done, failed = 0, []
    for p in picks:
        try:
            await stock.update_item(db, int(p["id"]), user_id, {"status": p["status"], "status_note": p.get("status_note") or "",
                                                                "via": "엑셀"})
            done += 1
        except stock.StockError as e:
            await db.rollback()
            failed.append({"id": p.get("id"), "why": str(e)})
    return {"applied": done, "failed": failed}
