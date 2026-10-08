# -*- coding: utf-8 -*-
"""수주 뒤 진행 순서: 수주 확인 → 거래명세서 발급 → 결제 조건 → (조건대로) 입금 확인·출하.

결제 조건(사용자 2026-10-08 — 고정 3방식 대신): 선금·중도금·잔금을 % 로 직접 적고, 회차마다 시점(자유 글, 기본은 비움 — 예: 발주 시)과
예정 입금일을 둔다. 시점은 기록용이라 출하·입금 확인 순서와는 상관없다.
입금 확인은 회사가 따로 관리하고 화면에서는 회차별 [입금 확인] 버튼으로 확정한다(은행 파일 대조는 쓰지 않음).
출하는 입금 확인과 상관없이 언제든 기록할 수 있고(사용자 2026-10-08), 회차 입금 확인도 순서와 상관없이 누른다.
예정 입금일이 지난 회차는 알림. 금액은 거래명세서를 빼고 모두 공급가액. pay_terms = [{label, pct, when, expected_date}],
입금 기록(payments)은 회차 번호(term)를 단다. pay_case 는 결제 조건을 정했다는 표시('terms' — 예전 after·split·prepay 도 같은 뜻).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

VAT_RATE = 0.1
PAY_TOLERANCE = 1_000            # 송금 수수료 등으로 생기는 원 단위 차이까지는 받은 것으로 본다

LABELS = ("선금", "중도금", "잔금", "전액")


def supply_total(d: dict) -> float | None:
    """공급가액 — 거래명세서가 있으면 그 공급가액, 없으면 견적(부가세 포함 견적이면 ÷1.1). 견적 없는 옛 출하 건은 출하 금액 합."""
    st = d.get("statement") or {}
    if (st.get("totals") or {}).get("supply"):
        return float(st["totals"]["supply"])
    q = d.get("quote") or {}
    if q.get("amount"):
        return round(float(q["amount"]) / (1 + VAT_RATE)) if q.get("vat_included") else float(q["amount"])
    inv = [i.get("supply") for i in d.get("invoices") or [] if i.get("supply")]      # 견적 없이 옮긴 건 — 옛 계산서 공급가액
    if inv:
        return float(sum(inv))
    ships = [s.get("amount") for s in d.get("shipments") or [] if s.get("amount")]
    return float(sum(ships)) if ships else None


def expected_total(d: dict) -> float | None:
    """받을 금액 — 공급가액 기준(사용자 2026-10-08: 부가세는 거래명세서에서만)."""
    return supply_total(d)


def terms(d: dict) -> list[dict[str, Any]]:
    """결제 조건 회차 + 금액(공급가액 × %, 마지막 회차가 끝전) + 확인 여부. 조건을 안 정했으면 []."""
    if not d.get("pay_case"):
        return []
    total = supply_total(d) or 0
    raw = d.get("pay_terms") or []
    out, used = [], 0
    pays = d.get("payments") or []
    for i, t in enumerate(raw):
        amt = round(total * (t.get("pct") or 0) / 100) if i < len(raw) - 1 else round(total - used)
        used += amt
        got = [p for p in pays if p.get("term") == i]
        out.append({"label": t.get("label"), "pct": t.get("pct"), "when": t.get("when") or "", "expected_date": t.get("expected_date"),
                    "index": i, "amount": amt, "confirmed": bool(got), "paid_date": got[-1].get("date") if got else None})
    return out


def paid(d: dict) -> float:
    return float(sum(p.get("amount") or 0 for p in d.get("payments") or []))


def next_due(d: dict) -> float | None:
    """다음에 확인할 회차 금액(아직 확인 안 한 첫 회차)."""
    t = next_term(d)
    return float(t["amount"]) if t else None


def next_term(d: dict) -> dict | None:
    return next((t for t in terms(d) if not t["confirmed"]), None)


def overdue(d: dict, today: dt.date) -> list[dict[str, Any]]:
    """예정 입금일이 지났는데 확인 안 된 회차(사용자 2026-10-08: 팝업·리스트 강조)."""
    out = []
    for t in terms(d):
        due = t.get("expected_date")
        if due and not t["confirmed"] and due < today.isoformat():
            out.append({**t, "days": (today - dt.date.fromisoformat(due)).days})
    return out


def steps(d: dict) -> list[dict[str, Any]]:
    """화면의 진행 단계 — [{key, label, done, ready}]. ready = 지금 할 수 있음.
    거래명세서 → 결제 조건 → 출하·회차 입금 확인(서로 순서 없음 — 출하는 입금 확인 없이도, 사용자 2026-10-08)."""
    if d.get("kind") not in ("quote", "recommend") or not d.get("won") or d.get("dropped"):
        return []
    # 엑셀에서 옮긴 건은 예전 방식으로 이미 처리 중·처리 끝 — 새 절차는 여기서 명세서·결제 조건을 시작한 경우만
    if d.get("imported") and not (d.get("statement") or d.get("pay_case")):
        return []
    shipped = bool(d.get("shipments")) or bool(d.get("shipped_note"))
    has_st, has_case = bool(d.get("statement")), bool(d.get("pay_case"))
    out = [{"key": "statement", "label": "거래명세서 발급", "done": has_st, "ready": not has_st},
           {"key": "case", "label": "결제 조건", "done": has_case, "ready": has_st and not has_case},
           {"key": "ship", "label": "납품(출하)", "done": shipped, "ready": has_st and not shipped}]
    out += [{"key": f"pay:{t['index']}", "label": f"{t['label']} {t['pct']:g}% 입금 확인", "done": t["confirmed"],
             "ready": has_case and not t["confirmed"]} for t in terms(d)]
    return out


def clean_terms(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """화면·대화에서 받은 결제 조건 검사 — 구분·%(합 100)·시점(자유 글, 비어도 됨)·예정일. 틀리면 ValueError(무엇이 틀렸는지)."""
    out = []
    for t in raw or []:
        label = str(t.get("label") or "").strip()[:10]
        try:
            pct = float(t.get("pct"))
        except (TypeError, ValueError):
            pct = 0
        due = str(t.get("expected_date") or "").strip() or None
        if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            raise ValueError("예정 입금일은 날짜로 적어 주세요.")
        if not label or pct <= 0:
            raise ValueError("회차마다 구분(선금·중도금·잔금)·비율(%)을 적어 주세요.")
        when = re.sub(r"\s+", " ", str(t.get("when") or "")).strip()[:30]
        out.append({"label": label, "pct": round(pct, 2), "when": when, "expected_date": due})
    if not out:
        raise ValueError("결제 조건을 한 회차 이상 적어 주세요.")
    if abs(sum(t["pct"] for t in out) - 100) > 0.01:
        raise ValueError(f"비율 합이 100% 가 아닙니다(지금 {sum(t['pct'] for t in out):g}%).")
    return out
