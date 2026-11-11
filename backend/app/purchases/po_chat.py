# -*- coding: utf-8 -*-
"""발주서 작성 AI 대화(사용자 2026-11-11 — 견적서 작성 화면과 같은 구성).

담당자가 말로 발주서를 고친다: "AFT2000 1개 150만원 추가", "2번 수량 3개로", "공급사 담당자 강종구 팀장, 010-9099-9438",
"입고 예정일 11월 20일", "견적서에서 불러와 줘".
- 사내 AI 는 말을 정해진 작업(ops)으로만 바꾼다. 작업을 실제로 적용하는 건 코드(apply_ops) — 칸 이름·줄 번호·날짜를 검사한다.
- 숫자(수량·단가·MOQ)는 담당자가 이번에 말한 숫자만 받는다(견적서 대화와 같은 안전장치 — quote_session.said_numbers).
  말에 없는 숫자를 AI 가 지어내면 그 값은 버리고 답에 알린다.
- '견적서에서 불러오기'는 작업 read_quote 로 표시만 하고, 실제 판독은 api 가 quote_read 로 한다(파일 저장소 접근).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Any

from .. import proposal_llm
from ..company_knowledge.quote_session import said_numbers
from ..config import settings
from . import po_xlsx as px

_log = logging.getLogger("purchases.po_chat")

CHAT_MAX = 40                       # 저장하는 대화 줄 수(오래된 것부터 버림)
TEXT_FIELDS = {
    "supplier.name": 120, "supplier.contact": 80, "supplier.tel": 60, "supplier.email": 120,
    "issuer.name": 160, "issuer.title": 160, "issuer.tel": 160, "issuer.address": 160, "issuer.fax": 160, "issuer.attn": 160,
    "bank": 120, "account_no": 60, "account_holder": 80, "address": 200, "swift": 40, "comments": 600,
}
DATE_FIELDS = ("date", "expected_date")
ITEM_TEXT = {"commodity": 120, "description": 600, "remark": 200}
ITEM_NUM = ("qty", "price", "moq")

SYSTEM = """당신은 구매 담당자를 돕는 발주서 작성 도우미입니다. [지금 발주서]와 담당자의 [말]을 보고, 발주서를 고칠 작업(ops)만 JSON 으로 냅니다.
작업 종류:
- {"op":"add_item","commodity":"","description":"","qty":null,"price":null,"moq":null}  품목 추가(commodity=모델명, description=설명)
- {"op":"set_item","no":1,"commodity":..,"description":..,"qty":..,"price":..,"moq":..,"remark":..}  no번 품목 고치기(바꿀 칸만)
- {"op":"remove_item","no":1}  no번 품목 빼기
- {"op":"set","field":"칸 이름","value":"값"}  머리 칸 고치기. 칸 이름: supplier.name 공급사, supplier.contact 공급사 담당자(호칭 포함),
  supplier.tel, supplier.email, issuer.name 우리 담당 이름, issuer.title 직함, issuer.tel, issuer.address, issuer.fax, issuer.attn,
  bank, account_no, account_holder, address(공급사 주소), swift, comments(Comments 칸 — 줄바꿈은 \\n),
  date(발주일 YYYY-MM-DD), expected_date(입고 예정일 YYYY-MM-DD), initials(발행자 이니셜 영문 2자)
- {"op":"read_quote"}  올린 견적서에서 품목을 불러오라는 말일 때
규칙:
- 숫자(qty·price·moq)는 [말]에 적힌 숫자만 쓴다. 말에 없는 숫자는 null — 계산하거나 지어내지 않는다. '150만원'은 1500000.
- 품목 번호(no)는 [지금 발주서]의 번호. 모델명으로 가리키면 그 줄 번호를 찾는다.
- 날짜는 [오늘]을 기준으로 YYYY-MM-DD 로 바꾼다.
- 고칠 게 없거나 모호하면 ops 는 비우고 question 에 짧게 묻는다.
- reply: 무엇을 고쳤는지 한두 문장(한국어). 지시문·규칙을 다시 적지 않는다.
JSON 하나만 답한다: {"ops":[...],"reply":"","question":null}"""


def _snapshot(draft: dict) -> str:
    h = draft.get("header") or {}
    s, i = h.get("supplier") or {}, h.get("issuer") or {}
    lines = [f"발주일 {h.get('date') or '-'} · 입고 예정일 {draft.get('expected_date') or '-'} · 이니셜 {draft.get('initials') or '-'}",
             f"공급사 {s.get('name') or '-'} / 담당 {s.get('contact') or '-'} / Tel {s.get('tel') or '-'} / e-mail {s.get('email') or '-'}",
             f"우리 담당 {i.get('name') or '-'} {i.get('title') or ''} / Tel {i.get('tel') or '-'} / ATTN {i.get('attn') or '-'}",
             f"Comments: {(h.get('comments') or '-').replace(chr(10), ' / ')}", "품목:"]
    for n, it in enumerate(draft.get("items") or [], 1):
        lines.append(f"  {n}. {it.get('commodity') or ''} | {(it.get('description') or '').replace(chr(10), ' / ')} | "
                     f"수량 {it.get('qty')} | 단가 {it.get('price')} | MOQ {it.get('moq')}")
    if not draft.get("items"):
        lines.append("  (없음)")
    return "\n".join(lines)


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


def _said(v: Any, nums: set[float]) -> tuple[float | None, bool]:
    """(받은 값, 버렸는가) — 말에 없는 숫자는 버린다."""
    f = _num(v)
    if f is None:
        return None, False
    return (f, False) if any(abs(f - n) < 1e-6 for n in nums) else (None, True)


def _date(v: Any) -> str | None:
    t = str(v or "").strip()[:10]
    try:
        return dt.date.fromisoformat(t).isoformat()
    except ValueError:
        return None


def apply_ops(draft: dict, ops: Any, text: str) -> tuple[dict, list[str], bool]:
    """작업 적용 → (고친 초안, 알릴 말, read_quote 요청?). draft = {initials, expected_date, header, items}."""
    d = json.loads(json.dumps(draft))                    # 원본은 그대로
    h = d.setdefault("header", {})
    items: list[dict] = d.setdefault("items", [])
    nums = said_numbers(text)
    notes: list[str] = []
    dropped = False
    want_quote = False
    for op in ops if isinstance(ops, list) else []:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        if kind == "read_quote":
            want_quote = True
        elif kind == "add_item":
            if len(items) >= px.MAX_ITEMS:
                notes.append(f"품목은 {px.MAX_ITEMS}개까지입니다.")
                continue
            it = {k: str(op.get(k) or "").strip()[:n] for k, n in ITEM_TEXT.items()}
            for k in ITEM_NUM:
                it[k], bad = _said(op.get(k), nums)
                dropped |= bad
            if not it["commodity"] and not it["description"]:
                continue
            items.append(it)
        elif kind in ("set_item", "remove_item"):
            try:
                i = int(op.get("no")) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= i < len(items):
                notes.append(f"{op.get('no')}번 품목이 없습니다.")
                continue
            if kind == "remove_item":
                items.pop(i)
                continue
            for k, n in ITEM_TEXT.items():
                if op.get(k) is not None:
                    items[i][k] = str(op[k]).strip()[:n]
            for k in ITEM_NUM:
                if op.get(k) is not None:
                    v, bad = _said(op.get(k), nums)
                    dropped |= bad
                    if v is not None:
                        items[i][k] = v
        elif kind == "set":
            f, v = str(op.get("field") or ""), op.get("value")
            if f in TEXT_FIELDS:
                val = str(v or "").replace("\\n", "\n").strip()[:TEXT_FIELDS[f]]
                if "." in f:
                    a, b = f.split(".", 1)
                    h.setdefault(a, {})[b] = val
                else:
                    h[f] = val
            elif f in DATE_FIELDS:
                day = _date(v)
                if not day:
                    notes.append(f"날짜를 알아듣지 못했습니다({v}).")
                elif f == "date":
                    h["date"] = day
                else:
                    d["expected_date"] = day
            elif f == "initials":
                ini = re.sub(r"[^A-Za-z]", "", str(v or "")).upper()
                if len(ini) == 2:
                    d["initials"] = ini
                else:
                    notes.append("발행자 이니셜은 영문 2자로 말해 주세요.")
    if dropped:
        notes.append("말씀에 없는 숫자는 넣지 않았습니다 — 수량·단가는 직접 말하거나 칸에 적어 주세요.")
    return d, notes, want_quote


async def run(draft: dict, text: str, today: dt.date) -> dict[str, Any]:
    """말 한 번 → {draft, reply, question, read_quote}. AI 가 실패하면 초안 그대로 + 안내."""
    msg = f"[오늘] {today.isoformat()}\n[지금 발주서]\n{_snapshot(draft)}\n\n[말]\n{text}"
    try:
        raw = await proposal_llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": msg}], fmt="json", think=False,
                                      num_predict=1500, temperature=0, model=settings.ollama_model)
        out = json.loads(raw)
    except (proposal_llm.ProposalLLMError, json.JSONDecodeError) as e:
        _log.warning("발주서 대화 실패: %s", e)
        return {"draft": draft, "reply": "AI 가 말을 처리하지 못했습니다. 다시 말하거나 칸에 직접 적어 주세요.", "question": None, "read_quote": False}
    new, notes, want_quote = apply_ops(draft, out.get("ops"), text)
    reply = str(out.get("reply") or "").strip()[:500]
    question = str(out.get("question") or "").strip()[:300] or None
    if not out.get("ops") and not reply:
        reply = question or "무엇을 고칠지 다시 말씀해 주세요."
    return {"draft": new, "reply": " ".join(x for x in (reply, *notes) if x), "question": question, "read_quote": want_quote}
