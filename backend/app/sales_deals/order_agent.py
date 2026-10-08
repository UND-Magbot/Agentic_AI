# -*- coding: utf-8 -*-
"""수주 진행 — AI 와 대화하며 수주 뒤 절차를 밟는다(사용자 2026-10-07, 견적서 작성과 같은 'GPT 식' 대화).

절차 자체는 flow.py(거래명세서 → 결제 방식 → 입금 확인 → 출하 → 잔금)를 따르고, 이 모듈은 대화만 맡는다:
사내 AI 한 번 호출(스트리밍)로 '생각 / === / JSON ops / === / 답' 을 쓰게 하고(atc_agent 와 같은 형식), 코드가 ops 를 검증해 실행한다.
  - 명세서 초안 고치기(공급받는자·품목·발행번호·발행일) — 숫자·글은 사용자가 말한 것만(지어내기 차단)
  - 결제 방식 고르기 — 말로 분명히 고르면 바로 적용(출하 전까지 바꿀 수 있음)
  - 입금 후보 고르기·출하 정보 채우기·명세서 발급 제안 — 확정은 담당자가 버튼으로(돈·발급·출하는 사람이 누른다)
사업자 정보는 사용자가 직접 입력(사용자 2026-10-07: 사업자등록증 읽기 안 함). 등록번호는 검증 숫자만 확인해 알려 준다.
초안·출하 정보·입금 후보·대화는 sales_deals.order_state 에 남는다.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..company_knowledge.atc_agent import _Splitter, json_objects
from ..company_knowledge.product_recommend import ANSWER_FORMAT
from ..company_knowledge.quote_session import said_numbers
from . import flow
from . import statement_xlsx as sx

CHAT_MAX = 60
CARRIERS = ("로젠택배", "경동택배", "직납", "배차", "기타")
OPS = ("buyer", "item", "add_item", "remove_item", "statement", "terms", "confirm", "ship", "issue")

SYSTEM = """너는 유엔디로보틱스 영업 담당을 돕는 AI 다. 수주가 확인된 건의 뒷절차를 담당자와 대화로 진행한다.
절차: 거래명세서 발급 → 결제 조건(선금·중도금·잔금 %, 예정 입금일) → 출하(제품별 사진)·회차별 입금 확인(순서 없음 — 출하는 입금 확인 없이도 된다).
입금 확인은 회사가 따로 확인한 뒤 [입금 확인] 버튼으로 한다. 금액은 모두 공급가액(부가세 별도).
반드시 아래 세 부분을 이 순서로 쓴다.
1) 생각 — "- " 로 시작하는 짧은 한국어 문장 2~4줄. 사용자 말의 뜻, [상황]의 어떤 근거를 보는지, 무엇을 할지.
2) 한 줄에 === 만 쓰고, 다음 줄에 JSON 한 개: {"ops": [...]}
   ops 는 사용자의 [말]에 분명히 있는 것만. 숫자·이름·주소·운송장은 [말]에 적힌 그대로(지어내기 금지). 없으면 [].
   {"op": "buyer", "reg_no": "...", "name": "...", "ceo": "...", "address": "..."}  공급받는자(말한 칸만)
   {"op": "item", "index": 0, "qty": 2, "unit_price": 250000, "name": "...", "spec": "...", "note": "..."}  명세서 품목 고치기(말한 칸만)
   {"op": "add_item", "name": "...", "spec": "...", "qty": 1, "unit_price": 50000}
   {"op": "remove_item", "index": 2}
   {"op": "statement", "no": "...", "date": "YYYY-MM-DD"}  발행번호·발행일
   {"op": "statement", "bundle": true, "bundle_qty": 1, "bundle_name": "..."}  명세서를 '세트로/1SET/묶어서' 발급한다고 할 때만
      (품목을 세트 한 줄로 묶음, 이름은 말했을 때만). 품목별로 되돌리라고 하면 "bundle": false
   {"op": "issue"}  사용자가 명세서를 발급해 달라고 할 때(발급 버튼을 보여 준다)
   {"op": "terms", "items": [{"label": "선금", "pct": 30, "when": "발주 시", "expected_date": "YYYY-MM-DD"},
                             {"label": "잔금", "pct": 70, "when": "", "expected_date": null}]}
      사용자가 결제 조건을 말할 때 — label 은 선금·중도금·잔금·전액, % 합 100. when(시점)은 말에 있을 때만 그 글 그대로, 없으면 "".
      '나머지'처럼 말하면 마지막 회차 % 는 100 에서 뺀 값. 예정 입금일은 말했을 때만.
   {"op": "confirm", "index": 0}  [결제 조건]의 회차 번호 — 사용자가 그 회차 입금이 들어왔다고 할 때(확인 버튼을 보여 준다)
   {"op": "ship", "date": "YYYY-MM-DD", "carrier": "로젠택배|경동택배|직납|배차|기타", "tracking": "...", "receiver": "...", "address": "...",
    "bundle": true, "bundle_qty": 1}  — bundle 은 사용자가 '세트로/1SET/묶어서' 보낸다고 할 때만(품목별이 아니라 세트 한 줄로 기록)
3) 다시 한 줄에 === 만 쓰고, 담당자에게 하는 답 2~4문장(존댓말). [상황]에 있는 것만 말하고 수치를 지어내지 않는다.
   무엇을 반영했는지, 또는 물은 것에 대한 답만 쓴다. 다음 할 일은 쓰지 않는다(시스템이 절차에 맞춰 붙인다).
   영어 코드는 답에 쓰지 않는다. 금액은 공급가액으로 말한다.
""" + ANSWER_FORMAT


def default_set_name(deal: dict) -> str:
    """세트 이름 기본값 — 건명 첫 모델(예: 'TCC1 외 6' → 'TCC1 툴체인저(ATC)'). 화면 deal-flow.defaultSetName 과 같게."""
    model = re.split(r"\s+외\s+|\s", str(deal.get("title") or "").strip())[0]
    return f"{model} 툴체인저(ATC)" if model else "툴체인저(ATC)"


def reg_no_ok(v: str) -> bool:
    """사업자등록번호 검증 숫자(국세청 방식)."""
    d = [int(c) for c in v if c.isdigit()]
    if len(d) != 10:
        return False
    s = sum(a * b for a, b in zip(d, (1, 3, 7, 1, 3, 7, 1, 3, 5))) + d[8] * 5 // 10
    return (10 - s % 10) % 10 == d[9]


def _squash(s: Any) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _in_msg(v: Any, message: str) -> str | None:
    """글 값은 말에 그대로 있어야 받는다(띄어쓰기 무시)."""
    t = str(v or "").strip()
    return t if t and _squash(t) in _squash(message) else None


def _num(v: Any, nums: set[float]) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if any(abs(f - n) < 1e-6 for n in nums) else None


def initial_state(deal: dict, draft: dict) -> dict:
    return {"draft": draft, "ship": {}, "chat": [{"role": "ai", "answer": next_hint(deal, draft, opening=True),
                                                                     "think": "", "steps": []}]}


def next_hint(deal: dict, draft: dict | None, opening: bool = False) -> str:
    """지금 할 일 한두 문장(규칙) — 첫 인사와 [다음 할 일]에 쓴다."""
    head = f"**{deal['deal_no'] or '번호 미부여'} {deal.get('customer') or ''}** 건의 수주 진행입니다.\n" if opening else "**다음:** "
    steps = (deal.get("flow") or {}).get("steps") or []
    cur = next((s for s in steps if s["ready"]), None)
    if not deal.get("statement"):
        b = (draft or {}).get("buyer") or {}
        miss = [lab for k, lab in (("reg_no", "등록번호"), ("address", "주소")) if not str(b.get(k) or "").strip()]
        ask = (f"고객사 **{'·'.join(miss)}**를 알려 주시거나 왼쪽 칸에 입력해 주세요." if miss
               else "거래명세서 내용을 확인하고 **[미리보기]**로 본 뒤 **[거래명세서 발급]**을 눌러 주세요.")
        return head + ("**거래명세서**부터 만들겠습니다. 견적 품목으로 왼쪽에 초안을 채워 두었습니다.\n" if opening else "") + ask
    if cur is None:
        return head + "**모든 절차가 끝났습니다** — 출하와 완납까지 확인했습니다."
    return head + ({
        # 선택지는 한 줄에 하나씩(사용자 2026-10-07: 더 잘 보이게)
        "case": "**결제 조건**을 정해 주세요 — 회차마다 비율(%)·시점·예정 입금일.\n"
                "- 예: **선금 30% 10월 15일**, **잔금 70% 11월 30일 예정**\n- 왼쪽 결제 조건 칸에서 직접 적어도 됩니다.",
        "ship": "**출하**할 차례입니다. 출하일·배송·운송장을 알려 주시고, 제품 사진은 **[출하 기록]**에서 올려 주세요.",
    }.get(cur["key"]) or _pay_hint(deal, cur))


def _pay_hint(deal: dict, cur: dict) -> str:
    """회차 입금 확인 차례 안내 — 회사가 따로 확인한 뒤 버튼으로(사용자 2026-10-08)."""
    if not cur["key"].startswith("pay:"):
        return f"다음 할 일: {cur['label']}"
    t = next((x for x in (deal.get("flow") or {}).get("terms") or [] if f"pay:{x['index']}" == cur["key"]), None)
    if t is None:
        return f"**{cur['label']}** 차례입니다."
    due = f", 예정 {t['expected_date']}" if t.get("expected_date") else ""
    return f"**{t['label']} {t['pct']:g}% 입금**(공급가액 {t['amount']:,.0f}원{due})을 확인할 차례입니다 — 입금되면 **[입금 확인]**을 눌러 주세요."


def _situation(deal: dict, state: dict) -> str:
    fl = deal.get("flow") or {}
    d = state.get("draft") or {}
    lines = [f"[건] {deal['deal_no'] or '번호 미부여'} · 고객사 {deal.get('customer') or '-'} · 단계 {deal.get('stage')} · "
             f"결제 조건 {fl.get('pay_case_label') or '미정'} · 받을 금액(공급가액) {fl.get('total') or 0:,.0f}원 · "
             f"확인한 입금 {fl.get('paid') or 0:,.0f}원",
             "[진행] " + " → ".join(f"{s['label']}{'(완료)' if s['done'] else '(지금)' if s['ready'] else ''}" for s in fl.get("steps") or []),
             f"[명세서] {'발급함' if deal.get('statement') else '초안(발급 전)'} · 발행번호 {d.get('no') or '-'} · 발행일 {d.get('date') or '-'}",
             "[공급받는자] " + json.dumps(d.get("buyer") or {}, ensure_ascii=False),
             "[명세서 품목]"]
    lines += [f"- {i}: {it.get('name')} / {it.get('spec') or ''} / 수량 {it.get('qty')}{' ' + sx.unit_of(it) if sx.unit_of(it) else ''}"
              f" / 단가 {it.get('unit_price')}"
              for i, it in enumerate(d.get("items") or [])] or ["(없음)"]
    if d.get("parts"):
        lines.append("[세트 구성(참고, 명세서에는 세트 한 줄)] " + ", ".join(f"{p.get('name')} × {p.get('qty')}" for p in d["parts"]))
    lines.append("[결제 조건]")
    lines += [f"- {t['index']}: {t['label']} {t['pct']:g}%{' ' + t['when'] if t.get('when') else ''} · 공급가액 {t['amount']:,.0f}원 · 예정 {t.get('expected_date') or '-'} · "
              f"{'입금 확인함 ' + str(t.get('paid_date')) if t['confirmed'] else '확인 전'}"
              for t in fl.get("terms") or []] or ["(아직 안 정함)"]
    lines.append("[출하 정보] " + json.dumps(state.get("ship") or {}, ensure_ascii=False))
    lines.append("[다음 할 일] " + next_hint(deal, d))
    return "\n".join(lines)


def build_messages(deal: dict, state: dict, message: str) -> list[dict[str, str]]:
    talk = "\n".join(f"{'담당자' if h['role'] == 'user' else 'AI'}: {str(h.get('text') or h.get('answer') or '')[:300]}"
                     for h in (state.get("chat") or [])[-8:])
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"[상황]\n{_situation(deal, state)}\n\n[대화]\n{talk or '(없음)'}\n\n[말]\n{message}"}]


def apply_ops(deal: dict, state: dict, ops: Any, message: str) -> tuple[dict, list[tuple[bool, str]], list[dict], str | None]:
    """AI 가 쓴 ops → 검증해 초안·출하 정보에 반영. 반환 (새 state, 단계[(ok, 글)], 버튼[actions], 결제 방식(적용할 것))."""
    st = json.loads(json.dumps(state))
    d = st.setdefault("draft", {"buyer": {}, "items": []})
    d.setdefault("buyer", {})
    items = d.setdefault("items", [])
    nums = said_numbers(message)
    steps: list[tuple[bool, str]] = []
    actions: list[dict] = []
    case: str | None = None
    for op in (ops if isinstance(ops, list) else [])[:20]:
        if not isinstance(op, dict) or op.get("op") not in OPS:
            continue
        kind = op["op"]
        if kind == "buyer":
            for k, lab in (("reg_no", "등록번호"), ("name", "상호"), ("ceo", "대표"), ("address", "주소")):
                if op.get(k) is None or _squash(op[k]) == _squash(d["buyer"].get(k)):
                    continue                                 # 이미 같은 값 — 따로 알리지 않는다
                v = _in_msg(op[k], message)
                if v is None:
                    steps.append((False, f"공급받는자 {lab}: 말씀에 없는 값이라 넣지 않았습니다"))
                    continue
                d["buyer"][k] = v
                note = "" if k != "reg_no" or reg_no_ok(v) else " — 검증 숫자가 맞지 않습니다. 번호를 확인해 주세요"
                steps.append((not note, f"공급받는자 {lab} → {v}{note}"))
        elif kind in ("item", "remove_item"):
            try:
                i = int(op.get("index"))
            except (TypeError, ValueError):
                continue
            if not 0 <= i < len(items):
                steps.append((False, f"{i}번 품목이 없습니다"))
                continue
            if kind == "remove_item":
                gone = items.pop(i)
                steps.append((True, f"품목 빼기 — {gone.get('name')}"))
                continue
            it = items[i]
            for k, lab in (("qty", "수량"), ("unit_price", "단가")):
                if op.get(k) is None or (isinstance(op[k], (int, float)) and it.get(k) is not None and float(op[k]) == float(it[k])):
                    continue
                v = _num(op[k], nums)
                if v is None:
                    steps.append((False, f"{it.get('name')} {lab}: 말씀에 없는 숫자라 넣지 않았습니다"))
                    continue
                it[k] = int(v) if v == int(v) else v
                steps.append((True, f"{it.get('name')} {lab} → {it[k]:,}"))
            for k, lab in (("name", "품명"), ("spec", "규격"), ("note", "비고")):
                if op.get(k) is not None and (v := _in_msg(op[k], message)):
                    it[k] = v
                    steps.append((True, f"{lab} → {v}"))
        elif kind == "add_item":
            name, qty, price = _in_msg(op.get("name"), message), _num(op.get("qty"), nums), _num(op.get("unit_price"), nums)
            if not name or qty is None or price is None:
                steps.append((False, "품목 추가: 품명·수량·단가를 말씀해 주시면 넣겠습니다"))
                continue
            items.append({"name": name, "spec": _in_msg(op.get("spec"), message) or "", "qty": qty, "unit_price": price, "note": ""})
            steps.append((True, f"품목 추가 — {name} × {qty:g}, {price:,.0f}원"))
        elif kind == "statement":
            if op.get("bundle") is True and not d.get("parts") and re.search(r"세트|set|묶", message, re.I):
                n = _num(op.get("bundle_qty"), nums) or 1
                name = _in_msg(op.get("bundle_name"), message) or default_set_name(deal)
                d["items"], d["parts"] = sx.bundle(items, name, n)
                items = d["items"]
                steps.append((True, f"명세서를 세트로 묶음 → {name} {n:g} SET (구성 {len(d['parts'])}개)"))
            elif op.get("bundle") is False and d.get("parts"):
                d["items"] = items = d.pop("parts")
                steps.append((True, f"명세서를 품목별로 되돌림 → {len(items)}줄"))
            if op.get("no") and (v := _in_msg(op["no"], message)):
                d["no"] = v
                steps.append((True, f"발행번호 → {v}"))
            if op.get("date") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(op["date"])):
                y, mo, dd = (int(x) for x in str(op["date"]).split("-"))
                if {float(mo), float(dd)} <= nums:
                    d["date"] = op["date"]
                    steps.append((True, f"발행일 → {op['date']}"))
        elif kind == "issue":
            actions.append({"type": "issue"})
        elif kind == "terms":
            got = _terms_from(op.get("items"), nums, message)
            if isinstance(got, str):
                steps.append((False, f"결제 조건: {got}"))
            else:
                case = got                               # 저장은 agent_turn 에서(set_terms — 거래명세서 전이면 거절)
        elif kind == "confirm":
            ts = (deal.get("flow") or {}).get("terms") or []
            try:
                t = ts[int(op.get("index"))]
            except (TypeError, ValueError, IndexError):
                steps.append((False, "그 회차가 없습니다 — 결제 조건을 먼저 정해 주세요"))
                continue
            if t["confirmed"]:
                steps.append((False, f"{t['label']}은(는) 이미 입금 확인했습니다"))
            else:
                actions.append({"type": "confirm", "index": t["index"], "label": f"{t['label']} {t['pct']:g}%", "amount": t["amount"]})
        elif kind == "ship":
            sh = st.setdefault("ship", {})
            for k, lab in (("tracking", "운송장"), ("receiver", "받는 사람"), ("address", "주소")):
                if op.get(k) and (v := _in_msg(op[k], message)):
                    sh[k] = v
                    steps.append((True, f"출하 {lab} → {v}"))
            if op.get("carrier") in CARRIERS and _in_msg(op["carrier"], message):
                sh["carrier"] = op["carrier"]
                steps.append((True, f"배송 → {op['carrier']}"))
            if op.get("date") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(op["date"])):
                y, mo, dd = (int(x) for x in str(op["date"]).split("-"))
                if {float(mo), float(dd)} <= nums:
                    sh["date"] = op["date"]
                    steps.append((True, f"출하일 → {op['date']}"))
            if op.get("bundle") and re.search(r"세트|set|묶", message, re.I):
                n = _num(op.get("bundle_qty"), nums) or 1
                sh["mode"], sh["set_qty"] = "set", f"{n:g} SET"
                steps.append((True, f"세트로 묶어서 → {sh['set_qty']}"))
            actions.append({"type": "ship"})
    if any(a["type"] == "issue" for a in actions):
        try:
            sx.check(d)
        except sx.StatementError as e:
            actions = [a for a in actions if a["type"] != "issue"]
            steps.append((False, f"아직 발급할 수 없습니다 — {e}"))
    return st, steps, actions, case


_CODE_KO = {"after": "1. 발주 후 바로 납품", "split": "2. 선금 50% 받고 납품", "prepay": "3. 선금·잔금 다 받고 납품"}


def _plain(answer: str) -> str:
    """답 다듬기 — 모델이 그래도 쓴 영어 코드를 한국어로, 코드 목록 괄호는 뺀다."""
    a = re.sub(r"\((?:[^()]*\b(?:after|split|prepay)\b[^()]*)\)", "", answer.strip())
    return re.sub(r"\b(after|split|prepay)\b", lambda m: _CODE_KO[m.group(1)], a).strip()


def _terms_from(items: Any, nums: set[float], message: str) -> list[dict] | str:
    """대화에서 받은 결제 조건 → 검증된 회차 목록, 아니면 이유(글). % 는 말에 있는 숫자만 — 하나는 '나머지'(100 에서 뺀 값) 허용.
    예정 입금일은 월·일 숫자가 말에 있을 때만."""
    if not isinstance(items, list) or not items:
        return "회차를 읽지 못했습니다"
    out, unsaid = [], []
    for i, t in enumerate(items[:6]):
        if not isinstance(t, dict):
            continue
        pct = _num(t.get("pct"), nums)
        if pct is None:
            unsaid.append(i)
        due = t.get("expected_date")
        if due and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(due)):
            _, mo, dd = (int(x) for x in str(due).split("-"))
            due = due if {float(mo), float(dd)} <= nums else None
        else:
            due = None
        out.append({"label": str(t.get("label") or "").strip(), "pct": pct, "when": _in_msg(t.get("when"), message) or "", "expected_date": due})
    if len(unsaid) > 1:
        return "비율(%)은 말씀하신 숫자만 넣습니다 — 회차별 % 를 알려 주세요"
    if unsaid:
        out[unsaid[0]]["pct"] = 100 - sum(t["pct"] for t in out if t["pct"] is not None)
    try:
        return flow.clean_terms(out)
    except ValueError as e:
        return str(e)


def _ops(raw: str, data: dict) -> list:
    """ops 모으기 — {"ops": [...]} 가 여러 개이거나 op 객체를 하나씩 따로 쓴 경우도 받는다."""
    objs = json_objects(raw) if raw else [data]
    ops: list = []
    for o in objs:
        if isinstance(o.get("ops"), list):
            ops += o["ops"]
        elif o.get("op"):
            ops.append(o)
    return ops


async def agent_turn(deal: dict, state: dict, message: str, *, stream: Callable[..., AsyncIterator[str]],
                     set_case: Callable[[list], Any]) -> AsyncIterator[dict[str, Any]]:
    """대화 한 번 — 생각·답을 흘려 보내고, ops 를 검증해 반영한 뒤 done(state·actions)을 보낸다.
    set_case: 결제 조건 저장(검증·저장은 service.set_terms — 실패하면 DealError)."""
    sp, answer, think = _Splitter(), "", ""
    async for piece in stream(build_messages(deal, state, message), num_predict=900):
        for kind, text in sp.feed(piece):
            if kind == "answer":
                answer += text
            else:
                think += text
            yield {"t": kind, "text": text}
    data, tail = sp.finish()
    for kind, text in tail:
        if kind == "answer":
            answer += text
        yield {"t": kind, "text": text}
    st, steps, actions, case = apply_ops(deal, state, _ops(sp.json, data), message)
    if case:
        try:
            await set_case(case)
            steps.append((True, "결제 조건 → " + " · ".join(
                f"{t['label']} {t['pct']:g}%" + (f" {t['when']}" if t.get("when") else "")
                + (f"({t['expected_date']} 예정)" if t.get("expected_date") else "") for t in case)))
        except Exception as e:                            # noqa: BLE001 — 업무 오류(명세서 전 등)는 이유를 보인다
            steps.append((False, f"결제 조건을 저장하지 못했습니다 — {e}"))
    for ok, text in steps:
        yield {"t": "step", "ok": ok, "text": text}
    # 다음 할 일은 코드가 절차대로 붙인다(사용자 2026-10-07: 발급 전인데 결제 방식을 안내하던 일) — 바뀐 초안·결제 방식 기준
    after = deal
    if case and any(ok and t.startswith("결제 조건 →") for ok, t in steps):
        after = {**deal, "pay_case": "terms", "pay_terms": case}
        after = {**after, "flow": {**(deal.get("flow") or {}), "steps": flow.steps(after), "terms": flow.terms(after)}}
    answer = _plain(answer) or ("말씀하신 내용을 반영했습니다." if steps else "")
    answer = (answer + "\n\n" if answer else "") + next_hint(after, st.get("draft"))
    yield {"t": "answer_set", "text": answer}
    st["chat"] = [*(st.get("chat") or []), {"role": "user", "text": message},
                  {"role": "ai", "think": think.strip(), "answer": answer.strip(),
                   "steps": [{"ok": ok, "text": t} for ok, t in steps], "actions": actions}][-CHAT_MAX:]
    yield {"t": "done", "state": st, "actions": actions}
