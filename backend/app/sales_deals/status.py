# -*- coding: utf-8 -*-
"""건 기록 → 현재 단계·계산서/입금 표시·업무 알림.

단계는 사람이 고르는 칸이 아니라 기록(수주 확정·계산서·입금·출하)에서 계산한다.
그래서 이관한 건과 새로 입력한 건이 같은 규칙으로 보인다.
"""
from __future__ import annotations

import datetime as dt

NO_RESULT_DAYS = 30   # 견적 후 이 기간이 지나도 결과가 없으면 알림
UNPAID_DAYS = 30      # 거래명세서 발급(이관 건은 옛 계산서 기록) 후 이 기간이 지나도 완납이 아니면 알림

# 세금계산서는 이 프로그램 밖에서 처리한다(사용자 2026-10-07) — '청구 중' 단계는 없앴다(계산서 기록이 있던 이관 건은 '수주').
# 이름은 남은 일이 보이게(사용자 2026-10-07): 출하했지만 완납 전 = 출하 후 입금 대기, 예전 엑셀의 견적 없는 출하 = 출하 기록만 (이전)
# 실주는 없앴다 — 수주까지 못 간 건은 모두 드랍(사용자 2026-10-08)
STAGES = ["제품 추천 확정", "견적", "수주", "출하 후 입금 대기", "완료 (출하·완납)", "드랍", "출하 기록만 (이전)"]


def _days(since: str | None, today: dt.date) -> int | None:
    return (today - dt.date.fromisoformat(since)).days if since else None


def derive(d: dict, today: dt.date) -> dict:
    """건 기록 dict → {stage, invoice_status, alerts(업무 알림)}.

    d 에 필요한 키: kind, won, quote, pay_terms, invoices, payments, shipments, paid_full, shipped_note.
    """
    shipped = bool(d["shipments"]) or bool(d.get("shipped_note"))
    if d.get("dropped") or d["won"] is False:         # 진행을 접은 건 — 예전에 '실주'로 남긴 건(won=False)도 드랍(사용자 2026-10-08)
        stage = "드랍"
    elif d["kind"] == "shipment_only":
        stage = "출하 기록만 (이전)"
    elif d["kind"] == "recommend":                   # 최종 제안 확정 — 견적서 발행 전
        stage = "제품 추천 확정"
    elif d["paid_full"] and (shipped or not d.get("pay_case")):
        stage = "완료 (출하·완납)"                  # 결제 방식을 고른 건(선입금)은 출하까지 끝나야 완료
    elif shipped:
        stage = "출하 후 입금 대기"
    elif d["won"]:
        stage = "수주"
    else:
        stage = "견적"

    alerts: list[dict] = []
    quote = d.get("quote") or {}
    if stage == "견적" and d["kind"] == "quote":
        n = _days(quote.get("sent_date") or quote.get("date"), today)
        if n is not None and n > NO_RESULT_DAYS:
            alerts.append({"code": "NO_RESULT", "level": "alert", "text": f"견적 후 {n}일 결과 미확인"})
    statement = d.get("statement") or {}
    if statement and not d["paid_full"] and stage != "드랍":
        n = _days(statement.get("date"), today)
        if n is not None and n > UNPAID_DAYS:
            alerts.append({"code": "UNPAID", "level": "alert", "text": f"거래명세서 발급 후 {n}일 완납 미확인"})
    elif d["invoices"] and not d["payments"] and stage != "드랍":       # 이관 건 — 옛 계산서 기록 기준
        last = max((i["date"] for i in d["invoices"] if i.get("date")), default=None)
        n = _days(last, today)
        if n is not None and n > UNPAID_DAYS:
            alerts.append({"code": "UNPAID", "level": "alert", "text": f"계산서 발행 후 {n}일 미입금(이전 기록)"})
    from .flow import overdue                         # 예정 입금일이 지난 회차(사용자 2026-10-08: 팝업·리스트 강조)
    # 출하는 입금 확인과 상관없이 할 수 있다(사용자 2026-10-08) — '입금 확인 전 출하' 알림은 없앴다

    if stage != "드랍":
        for t in overdue(d, today):
            alerts.append({"code": "OVERDUE", "level": "alert",
                           "text": f"{t['label']} {t['pct']:g}% 예정 입금일 {t['expected_date']} 지남({t['days']}일)"})
    partial = any(i.get("partial") for i in d["invoices"])
    return {
        "stage": stage,
        "shipped": shipped,
        "invoice_status": {"issued": len(d["invoices"]), "planned": len(d["pay_terms"]) or None, "partial": partial},
        "alerts": alerts,
    }
