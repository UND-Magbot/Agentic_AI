# -*- coding: utf-8 -*-
"""발주서 ↔ 견적 품목 대조(사용자 2026-10-08) — 수주 확인 때 붙인 발주서가 견적서와 같은지.

발주서는 고객사 전용 양식일 수도, 우리가 준 견적서 양식 그대로일 수도 있다.
- 읽기: PDF 글자(스캔본이면 페이지 그림) · 엑셀 셀 · 워드 · 사진. 한글(hwp)·옛 엑셀/워드(xls·doc)는 읽지 않는다.
- 사내 AI: 발주서 품목(품명·규격·수량·단가·금액)을 뽑고, 각 품목이 견적 몇 번째 줄인지(품명이 달라도) 고른다.
- 코드: 수량·단가·금액 비교. 글자로 읽은 발주서는 원문에 있는 숫자만 받는다(지어낸 숫자 차단).
결과는 발주서 기록(po_files[i].compare)에 남겨 다시 열어도 보인다.
"""
from __future__ import annotations

import base64
import datetime as dt
import difflib
import io
import json
import logging
import re
from typing import Any

from .. import proposal_llm
from ..company_knowledge.quote_session import said_numbers
from ..config import settings

_log = logging.getLogger("po_compare")

MAX_TEXT = 12_000          # AI 에 넘길 발주서 글자 상한
MAX_PAGES = 3              # 스캔 PDF 는 앞 3쪽만 그림으로
VAT = 1.1

SYSTEM = """당신은 영업 담당자를 돕는 발주서 판독 도우미입니다.
[발주서]에서 주문 품목을 빠짐없이 뽑고, 각 품목이 [견적 품목] 몇 번째 줄과 같은 물건인지 고릅니다.
규칙:
- 품명·규격은 발주서에 적힌 글자 그대로. 숫자(수량·단가·금액)도 발주서에 적힌 값만. 없으면 null. 계산해서 채우지 않는다.
- 운송비·설치비처럼 금액이 있는 줄도 품목으로 넣는다. 합계·부가세·소계 줄은 품목이 아니다.
- match: 같은 물건으로 보이는 견적 줄 번호(1부터). 모델명·품목 코드(TCV1, PPM 등)·뜻이 같으면 같은 물건. 확실하지 않으면 null.
- supply_total: 발주서의 공급가액(부가세 제외) 합계, vat_included: 단가가 부가세 포함으로 적혀 있으면 true.
JSON 하나만 답한다:
{"items":[{"name":"","spec":"","qty":null,"unit_price":null,"amount":null,"match":null}],
 "supply_total":null,"vat_included":null,"doc_no":null}"""


# ── 읽기 ─────────────────────────────────────────────────────────────────────

def read_po(data: bytes, filename: str, mime: str) -> tuple[str, list[bytes]]:
    """발주서 → (글자, 그림들). 둘 다 비면 읽을 수 없는 형식."""
    name = (filename or "").lower()
    if mime.startswith("image/"):
        return "", [data]
    if name.endswith(".pdf"):
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:
            text = "\n".join(p.get_text() for p in doc)
            if len(re.sub(r"\s", "", text)) >= 40:
                return text, []
            # 스캔본(글자 없음) — 앞 몇 쪽을 그림으로
            return "", [doc[i].get_pixmap(dpi=150).tobytes("png") for i in range(min(len(doc), MAX_PAGES))]
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        lines = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = [_cell(v) for v in row if v not in (None, "")]
                if cells:
                    lines.append(" | ".join(cells))
        return "\n".join(lines), []
    if name.endswith(".docx"):
        from ..proposal_project.extract import read_document

        return read_document(data, filename, mime) or "", []
    return "", []


def _cell(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    return str(v).strip()


# ── AI 판독 ──────────────────────────────────────────────────────────────────

def _quote_block(quote_items: list[dict]) -> str:
    return "\n".join(f"{i}. {str(it.get('name') or '').splitlines()[0]} / 수량 {it.get('qty')} / 단가 {it.get('unit_price')}"
                     for i, it in enumerate(quote_items, 1)) or "(없음)"


async def extract_items(text: str, images: list[bytes], quote_items: list[dict]) -> dict:
    """사내 AI 로 발주서 품목을 뽑는다. 글자로 읽었으면 원문에 없는 숫자는 지운다(null)."""
    body = f"[견적 품목]\n{_quote_block(quote_items)}\n\n[발주서]\n" + (text[:MAX_TEXT] if text else "(첨부 그림)")
    msg: dict[str, Any] = {"role": "user", "content": body}
    if images:
        msg["images"] = [base64.b64encode(b).decode() for b in images]
    raw = await proposal_llm.chat([{"role": "system", "content": SYSTEM}, msg], fmt="json", think=False,
                                  num_predict=3000, temperature=0, model=settings.ollama_model)
    try:
        out = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError("발주서 판독 결과를 읽지 못했습니다.") from e
    seen = said_numbers(text) if text else None
    items = []
    for it in out.get("items") or []:
        if not isinstance(it, dict) or not str(it.get("name") or "").strip():
            continue
        row = {"name": str(it["name"]).strip()[:120], "spec": str(it.get("spec") or "").strip()[:120]}
        for k in ("qty", "unit_price", "amount"):
            row[k] = _checked(it.get(k), seen)
        m = it.get("match")
        row["match"] = m if isinstance(m, int) and 1 <= m <= len(quote_items) else None
        items.append(row)
    return {"items": items, "supply_total": _checked(out.get("supply_total"), seen),
            "vat_included": out.get("vat_included") if isinstance(out.get("vat_included"), bool) else None,
            "doc_no": _in_text(out.get("doc_no"), text)}


def _checked(v: Any, seen: set[float] | None) -> float | None:
    """숫자 — 글자로 읽은 발주서면 원문에 있는 값만(그림이면 확인할 원문이 없어 그대로)."""
    try:
        f = float(str(v).replace(",", "")) if v is not None else None
    except ValueError:
        return None
    if f is None or f < 0:
        return None
    if seen is not None and not any(abs(f - n) < 1e-6 for n in seen):
        return None
    return f


def _in_text(v: Any, text: str) -> str | None:
    t = str(v or "").strip()
    if not t:
        return None
    return t if not text or re.sub(r"\s", "", t) in re.sub(r"\s", "", text) else None


# ── 코드 대조 ────────────────────────────────────────────────────────────────

_CODE = re.compile(r"[A-Z]{2,}[0-9]*[A-Z0-9-]*|[A-Z]+[0-9]+[A-Z0-9-]*")


def _codes(s: str) -> set[str]:
    return set(_CODE.findall(str(s or "").upper()))


def _norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(s or "")).upper()


def _guess_match(po: dict, quote_items: list[dict], taken: set[int]) -> int | None:
    """AI 가 못 고른 품목 — 모델 코드가 같거나 품명이 많이 비슷한 견적 줄(코드 기준)."""
    best, best_score = None, 0.0
    for i, q in enumerate(quote_items, 1):
        if i in taken:
            continue
        qn = str(q.get("name") or "")
        shared = _codes(qn) & _codes(po["name"] + " " + po["spec"])
        score = 1.0 if shared else difflib.SequenceMatcher(None, _norm(qn.splitlines()[0] if qn else ""), _norm(po["name"])).ratio()
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 0.6 else None


def _same(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) < 0.5


def compare(quote_items: list[dict], po: dict) -> dict:
    """견적 줄마다 발주서 품목을 맞춰 수량·단가를 비교한다. 반환 {lines, counts, totals, notes}."""
    po_items = po["items"]
    taken: set[int] = set()
    pairs: dict[int, dict] = {}
    extra: list[dict] = []
    for it in po_items:                                  # AI 가 고른 짝 먼저, 겹치면 뒤의 것은 코드로 다시
        m = it["match"] if it["match"] not in taken else None
        m = m or _guess_match(it, quote_items, taken)
        if m:
            taken.add(m)
            pairs[m] = it
        else:
            extra.append(it)
    vat = bool(po.get("vat_included"))
    lines = []
    for i, q in enumerate(quote_items, 1):
        p = pairs.get(i)
        qname = str(q.get("name") or "").splitlines()[0] if q.get("name") else ""
        if p is None:
            lines.append({"quote": {"name": qname, "qty": q.get("qty"), "unit_price": q.get("unit_price")}, "po": None,
                          "verdict": "발주서에 없음", "notes": []})
            continue
        notes = []
        if p["qty"] is None:
            notes.append("발주서 수량을 읽지 못함")
        elif not _same(p["qty"], q.get("qty")):
            notes.append(f"수량 다름(견적 {q.get('qty')} · 발주 {p['qty']:g})")
        up = p["unit_price"]
        if up is None:
            notes.append("발주서 단가를 읽지 못함")
        elif not _same(up, q.get("unit_price")):
            if q.get("unit_price") and _same(up, q["unit_price"] * VAT):
                notes.append("단가가 부가세 포함으로 적힘(공급가는 같음)")
            elif vat and q.get("unit_price") and _same(up / VAT, q["unit_price"]):
                notes.append("단가가 부가세 포함으로 적힘(공급가는 같음)")
            else:
                notes.append(f"단가 다름(견적 {q.get('unit_price'):,.0f} · 발주 {up:,.0f})")
        bad = [n for n in notes if n.endswith(")") and "다름" in n]
        unread = [n for n in notes if "읽지 못함" in n]
        lines.append({"quote": {"name": qname, "qty": q.get("qty"), "unit_price": q.get("unit_price")},
                      "po": {k: p[k] for k in ("name", "spec", "qty", "unit_price", "amount")},
                      "verdict": "다름" if bad else "확인 필요" if unread else "일치", "notes": notes})
    for p in extra:
        lines.append({"quote": None, "po": {k: p[k] for k in ("name", "spec", "qty", "unit_price", "amount")},
                      "verdict": "견적에 없음", "notes": []})
    quote_total = sum((q.get("unit_price") or 0) * (q.get("qty") or 0) for q in quote_items)
    po_total = po.get("supply_total")
    if po_total is None and po_items and all(p["amount"] is not None for p in po_items):
        po_total = sum(p["amount"] for p in po_items) / (VAT if vat else 1)
    counts = {k: sum(1 for ln in lines if ln["verdict"] == k) for k in ("일치", "다름", "확인 필요", "발주서에 없음", "견적에 없음")}
    return {"lines": lines, "counts": counts,
            "totals": {"quote": quote_total, "po": po_total, "same": _same(quote_total, po_total) if po_total is not None else None}}


def summary(result: dict) -> str:
    c = result["counts"]
    if not result["lines"]:
        return "발주서에서 품목을 찾지 못했습니다."
    if c["일치"] == len(result["lines"]) and result["totals"]["same"] is not False:
        return f"견적 품목 {c['일치']}개가 발주서와 모두 일치합니다."
    parts = [f"{k} {v}" for k, v in c.items() if v]
    if result["totals"]["same"] is False:
        parts.append(f"합계 다름(견적 {result['totals']['quote']:,.0f} · 발주 {result['totals']['po']:,.0f})")
    return "확인이 필요합니다 — " + " · ".join(parts)


async def run(data: bytes, filename: str, mime: str, quote_items: list[dict]) -> dict:
    """발주서 한 개 대조 → 저장할 결과. 읽을 수 없는 형식·AI 오류는 status 로 알린다(예외 없이)."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if not quote_items:
        return {"status": "no_quote", "checked_at": now, "summary": "대조할 견적 품목이 없습니다."}
    try:
        text, images = read_po(data, filename, mime)
    except Exception as e:  # 깨진 파일
        _log.warning("발주서 읽기 실패 %s: %s", filename, e)
        text, images = "", []
    if not text.strip() and not images:
        return {"status": "unreadable", "checked_at": now,
                "summary": "이 파일 형식은 자동 대조를 못 합니다(PDF·사진·엑셀(xlsx)·워드(docx)만). 직접 확인해 주세요."}
    try:
        po = await extract_items(text, images, quote_items)
    except (proposal_llm.ProposalLLMError, ValueError) as e:
        _log.warning("발주서 판독 실패 %s: %s", filename, e)
        return {"status": "failed", "checked_at": now, "summary": "AI 판독에 실패했습니다. 잠시 뒤 [다시 대조]를 눌러 주세요."}
    res = compare(quote_items, po)
    return {"status": "ok", "checked_at": now, "source": "image" if images else "text", "doc_no": po["doc_no"],
            **res, "summary": summary(res)}
