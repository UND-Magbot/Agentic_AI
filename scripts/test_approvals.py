"""입금 예정일 연장·관리자 승인 요청(영업팀 요청 8번, 2026-10-13) — 개발 DB(agent_ai_dev)에 만들고 지운다.

    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_approvals.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys

from sqlalchemy import text

from app.database import SessionLocal
from app.sales_deals import approvals as ap
from app.sales_deals import service as svc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
C = "__테스트_기한연장__"


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def cleanup() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": C})      # 요청은 CASCADE 로 같이
        await db.commit()


async def expect_error(coro, label: str, word: str) -> None:
    try:
        await coro
        check(False, label, "거절되지 않음")
    except svc.DealError as e:
        check(word in str(e), label, str(e))


async def run() -> None:
    await cleanup()
    today = svc.kst_today()
    past, future = (today - dt.timedelta(days=5)).isoformat(), (today + dt.timedelta(days=20)).isoformat()
    async with SessionLocal() as db:
        ids = dict((await db.execute(text("SELECT username, id FROM users WHERE username IN ('superadmin','alex','victor','lucas','finance_admin')"))).all())
        deal_id = await svc.create_deal(db, ids["lucas"], {"customer": C, "contact": "홍길동", "owner": "Lucas", "title": "기한 연장 테스트",
                                                           "quote_date": today, "vat_included": False, "currency": "KRW",
                                                           "items": [{"name": "TCV3", "unit_price": 10_000_000, "qty": 1}],
                                                           "pay_terms_text": None, "note": None})
        await svc._save(db, deal_id, won=True, won_date=today, statement={"no": "TS-TEST"}, pay_case="terms",
                        pay_terms=[{"label": "선금", "pct": 50, "when": "발주 시", "expected_date": past},
                                   {"label": "잔금", "pct": 50, "when": "납품 후", "expected_date": future}])
        await db.commit()

    async with SessionLocal() as db:
        adm = await ap.admins(db)
    names = {a["name"] for a in adm}
    check({"Chris", "Alex", "Victor"} <= names and "Jacob" not in names, "받을 관리자 = 최고 관리자 + 영업 관리자(재무 관리자 제외)", str(names))

    # 일반 직원은 지난 회차 예정일을 결제 조건 저장으로 못 바꿈
    async with SessionLocal() as db:
        await expect_error(svc.set_terms(db, deal_id, ids["lucas"], [{"label": "선금", "pct": 50, "when": "", "expected_date": future},
                                                                      {"label": "잔금", "pct": 50, "when": "", "expected_date": future}], admin=False),
                           "일반 직원이 결제 조건 저장으로 지난 예정일을 바꾸면 거절", "관리자만")
    async with SessionLocal() as db:
        await svc.set_terms(db, deal_id, ids["lucas"], [{"label": "선금", "pct": 50, "when": "", "expected_date": past},
                                                       {"label": "잔금", "pct": 50, "when": "", "expected_date": (today + dt.timedelta(days=30)).isoformat()}],
                            admin=False)
        check(True, "일반 직원도 아직 안 지난 회차 예정일은 바꿀 수 있음")

    new_due = today + dt.timedelta(days=14)
    async with SessionLocal() as db:
        await expect_error(ap.request_extend(db, deal_id, ids["lucas"], 1, new_due, "협의", [ids["alex"]]),
                           "안 지난 회차는 연장 요청 대상이 아님", "지난")
    async with SessionLocal() as db:
        await expect_error(ap.request_extend(db, deal_id, ids["lucas"], 0, new_due, "협의", [ids["finance_admin"]]),
                           "받을 관리자를 안 고르면(또는 영업 관리자가 아니면) 거절", "관리자를 한 명 이상")
    async with SessionLocal() as db:
        await expect_error(ap.request_extend(db, deal_id, ids["lucas"], 0, today, "협의", [ids["alex"]]),
                           "새 예정일이 오늘 이하면 거절", "오늘 이후")
    async with SessionLocal() as db:
        await expect_error(ap.request_extend(db, deal_id, ids["lucas"], 0, new_due, "  ", [ids["alex"]]), "사유가 비면 거절", "사유")
    async with SessionLocal() as db:
        rid = await ap.request_extend(db, deal_id, ids["lucas"], 0, new_due, "고객사와 협의 — 2주 뒤 입금", [ids["alex"], ids["victor"]])
    async with SessionLocal() as db:
        await expect_error(ap.request_extend(db, deal_id, ids["lucas"], 0, new_due, "다시", [ids["alex"]]),
                           "같은 회차에 대기 요청이 있으면 또 못 보냄", "기다리는 중")
        cnt = {u: await ap.pending_count(db, ids[u]) for u in ("alex", "victor", "superadmin")}
        lst = await ap.list_requests(db, ids["alex"])
        d = await svc.get_deal(db, deal_id, today)
    mine = next(x for x in lst["pending"] if x["id"] == rid)
    check(cnt["alex"] >= 1 and cnt["victor"] >= 1 and cnt["superadmin"] == cnt["superadmin"] and mine["to_me"] and mine["recipients"] == ["Alex", "Victor"]
          and mine["requested_by"] == "Lucas" and mine["term_label"] == "선금" and mine["customer"] == C,
          "고른 관리자(Alex·Victor)에게만 빨간 숫자, 목록에 영업 건·회차·요청자·받는 사람", str((cnt, mine["recipients"])))
    async with SessionLocal() as db:
        br_alex = await ap.pending_brief(db, ids["alex"])
        br_super = await ap.pending_brief(db, ids["superadmin"])
    b0 = next((x for x in br_alex if x["id"] == rid), None)
    check(b0 is not None and b0["requested_by"] == "Lucas" and b0["customer"] == C and b0["kind"] == "pay_due_extend"
          and not any(x["id"] == rid for x in br_super),
          "관리자 팝업용 요약: 받는 사람으로 고른 관리자에게만(요청자·고객사·종류)", str(b0))
    async with SessionLocal() as db:
        m1 = await ap.mark_alerted(db, ids["alex"], [rid])
        m2 = await ap.mark_alerted(db, ids["alex"], [rid])
        m3 = await ap.mark_alerted(db, ids["superadmin"], [rid])
        br2 = await ap.pending_brief(db, ids["alex"])
        br3 = await ap.pending_brief(db, ids["victor"])
    check(m1 == 1 and m2 == 0 and m3 == 0 and not any(x["id"] == rid for x in br2) and any(x["id"] == rid for x in br3),
          "팝업 본 기록(DB): Alex 가 보면 Alex 에겐 다시 안 뜸(두 번 기록 안 함), 받는 사람이 아닌 관리자는 기록 안 됨, Victor 에겐 아직 뜸",
          str((m1, m2, m3)))
    async with SessionLocal() as db:
        n_super = await ap.pending_count(db, ids["superadmin"])
        sl = await ap.list_requests(db, ids["superadmin"])
    check(not next(x for x in sl["pending"] if x["id"] == rid)["to_me"], "고르지 않은 관리자(Chris)에게는 '나에게 온 요청'이 아님(목록엔 보임)", str(n_super))
    check(d["due_requests"][0]["status"] == "pending" and d["due_requests"][0]["requested_due"] == new_due.isoformat()
          and any(e["action"] == "due_request" for e in d["events"]), "영업 건 상세에 '연장 요청 중' + 변경 이력")

    # 승인 — 날짜를 고쳐서
    fixed = today + dt.timedelta(days=21)
    async with SessionLocal() as db:
        await ap.decide(db, rid, ids["alex"], True, fixed, "3주로 조정")
        d = await svc.get_deal(db, deal_id, today)
        cnt2 = await ap.pending_count(db, ids["alex"])
    check(d["pay_terms"][0]["expected_date"] == fixed.isoformat() and not d["flow"]["overdue"] and d["due_requests"][0]["status"] == "approved"
          and d["due_requests"][0]["decided_due"] == fixed.isoformat() and cnt2 == cnt["alex"] - 1,
          "승인(날짜 고쳐서) → 선금 예정일이 3주 뒤로, 빨간 표시 사라짐, 숫자 줄어듦", str(d["pay_terms"][0]))
    async with SessionLocal() as db:
        await expect_error(ap.decide(db, rid, ids["victor"], False, None, "x"), "이미 처리한 요청은 다시 못 처리", "이미 처리")

    # 거절
    async with SessionLocal() as db:
        await svc._save(db, deal_id, pay_terms=[{"label": "선금", "pct": 50, "when": "", "expected_date": past},
                                                {"label": "잔금", "pct": 50, "when": "", "expected_date": future}])
        await db.commit()
        rid2 = await ap.request_extend(db, deal_id, ids["lucas"], 0, new_due, "한 번 더", [ids["victor"]])
    async with SessionLocal() as db:
        await expect_error(ap.decide(db, rid2, ids["victor"], False, None, ""), "거절 사유가 비면 거절", "거절 사유")
    async with SessionLocal() as db:
        await ap.decide(db, rid2, ids["victor"], False, None, "고객사 확인 필요")
        d = await svc.get_deal(db, deal_id, today)
    check(d["pay_terms"][0]["expected_date"] == past and d["due_requests"][0]["status"] == "rejected"
          and d["due_requests"][0]["decision_note"] == "고객사 확인 필요", "거절 → 예정일 그대로, 상세에 '거절됨(사유)'")

    # 관리자 바로 연장 — 대기 요청은 그 날짜로 승인 처리
    async with SessionLocal() as db:
        rid3 = await ap.request_extend(db, deal_id, ids["lucas"], 0, new_due, "세 번째", [ids["alex"]])
    async with SessionLocal() as db:
        await ap.extend_due(db, deal_id, ids["alex"], 0, fixed, "고객사 협의 완료")
        d = await svc.get_deal(db, deal_id, today)
        st = (await db.execute(text("SELECT status, decided_due FROM approval_requests WHERE id = :i"), {"i": rid3})).first()
    check(d["pay_terms"][0]["expected_date"] == fixed.isoformat() and st[0] == "approved" and st[1] == fixed
          and any(e["action"] == "due_extend" and e["detail"].get("reason") == "고객사 협의 완료" for e in d["events"]),
          "관리자 [기한 연장] → 바로 바뀌고 이력에 사유, 대기 요청은 그 날짜로 승인 처리", str(st))
    await cleanup()
    async with SessionLocal() as db:
        left = (await db.execute(text("SELECT COUNT(*) FROM approval_requests WHERE id IN (:a, :b, :c)"), {"a": rid, "b": rid2, "c": rid3})).scalar()
    check(left == 0, "영업 건을 지우면 요청도 같이 지워짐")


asyncio.run(run())
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
