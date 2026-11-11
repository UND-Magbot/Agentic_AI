# -*- coding: utf-8 -*-
"""제품 구매 관리 DB 흐름 점검(purchases/service.py) — 개발 DB(agent_ai_dev)에 테스트 건을 만들어 끝까지 돌리고 지운다.

견적 첨부·교체 → 재가(서류 없으면 거절) → 발주서 발행(번호 PO-…) → 결제 조건 → 지출내역 대조(일치·불일치) → 송금 확인 →
입고 → 거래명세서(선결제 잔액, 첨부 없음) → 세금계산서(첨부 없음) → 완료 → 취소·삭제. 파일은 저장소에 올리지 않고 기록만 만든다.

    python scripts/test_purchases_db.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.migrations import run_migrations  # noqa: E402
from app.purchases import expense_check as ec  # noqa: E402
from app.purchases import po_read
from app.purchases import service as svc  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
TODAY = svc.kst_today()
SUP = "__테스트_구매건__"
SUP_EN = "__테스트_해외구매건__"


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def fentry(name: str) -> dict:
    return {"key": f"purchases/test/{name}", "filename": name, "mime": "application/pdf", "size": 10,
            "uploaded_at": "2026-11-02T00:00:00+00:00", "uploaded_by": "test"}


async def expect_err(coro, label: str, needle: str = "") -> None:
    try:
        await coro
        check(False, label, "오류가 나야 함")
    except svc.PurchaseError as e:
        check(needle in str(e), label, str(e))


async def run() -> None:
    async with SessionLocal() as db:
        await run_migrations(db)
        await db.execute(text("DELETE FROM doc_numbers WHERE doc_type = 'PO' AND issuer = 'ZZ'"))
        await db.execute(text("DELETE FROM purchases WHERE supplier = ANY(:s)"), {"s": [SUP, SUP_EN]})
        await db.commit()
        uid = (await db.execute(text("SELECT id FROM users ORDER BY id LIMIT 1"))).scalar()

        pid = await svc.create(db, uid, {"supplier": SUP, "title": "M8 커넥터", "owner": "Lucas"})
        p = await svc.get(db, pid, TODAY)
        check(p["stage"] == "견적 대기" and p["history"][0]["action"] == "create", "새 건 — 견적 대기, 이력")
        top = (await svc.list_purchases(db, TODAY))["rows"][0]
        check(top["id"] == pid, "새 건이 리스트 맨 위")

        await expect_err(svc.add_file(db, pid, uid, "approval", fentry("a.pdf")), "견적서 전 재가 서류 거절", "견적서")
        await db.rollback()
        await svc.add_file(db, pid, uid, "quote", fentry("q1.pdf"))
        await svc.add_file(db, pid, uid, "quote", fentry("q2.pdf"))
        p = await svc.get(db, pid, TODAY)
        check([f["filename"] for f in p["quote_files"]] == ["q1.pdf", "q2.pdf"] and p["stage"] == "재가 대기"
              and "key" not in p["quote_files"][0], "견적서 교체 — 마지막이 지금 견적서, 키는 숨김")
        check(p["history"][0]["detail"].get("replaced") is True, "교체 이력")
        await expect_err(svc.remove_file(db, pid, uid, "quote", 0, admin=False), "견적서 지우기는 관리자만", "관리자")
        await db.rollback()

        await expect_err(svc.issue_po(db, pid, uid, "test", {"initials": "ZZ", "header": {}, "items": []}), "재가 전 발주 거절")
        await db.rollback()
        await svc.add_file(db, pid, uid, "approval", fentry("재가.pdf"))
        await svc.approve(db, pid, uid, "test", TODAY)
        p = await svc.get(db, pid, TODAY)
        check(p["stage"] == "발주 대기" and p["approval"]["done_by"] == "test", "재가 완료 → 발주 대기")

        draft = await svc.po_draft(db, pid, {"initials": "ZZ", "name_ko": "이승학", "title": "주임", "mobile": "010-1234-5678",
                                             "email": "lucas@unde.co.kr", "name_en_first": "Lucas"})
        check(draft["source"] == "new" and draft["header"]["supplier"]["name"] == SUP and draft["header"]["issuer"]["tel"] == "82-10-1234-5678"
              and draft["header"]["issuer"]["attn"] == "Lucas (lucas@unde.co.kr)", "발주서 기본값(공급사·내 정보)", str(draft["header"]))
        body = {"initials": "zz", "header": {**draft["header"], "supplier": {"name": SUP, "contact": "조 과장님", "email": "a@b.c"}},
                "items": [{"description": "M8 커넥터", "qty": 200, "price": 10800}, {"description": "", "qty": "", "price": ""}]}
        await svc.save_po_draft(db, pid, uid, body)
        check((await svc.po_draft(db, pid, {}))["source"] == "draft", "작성 중 저장 → 다시 열면 초안")
        await expect_err(svc.issue_po(db, pid, uid, "test", {**body, "items": [], "expected_date": "2099-01-01"}), "품목 없으면 발행 거절", "품목")
        await db.rollback()
        await expect_err(svc.issue_po(db, pid, uid, "test", body), "입고 예정일 없으면 발행 거절(사용자 2026-11-02)", "입고 예정일")
        await db.rollback()
        await expect_err(svc.issue_po(db, pid, uid, "test", {**body, "expected_date": "2020-01-01"}), "예정일이 발주일보다 앞이면 거절", "앞")
        await db.rollback()
        due = (TODAY + dt.timedelta(days=14)).isoformat()
        body = {**body, "expected_date": due}
        po = await svc.issue_po(db, pid, uid, "test", body)
        check(po["no"] == f"UND-ZZ{TODAY:%Y%m%d}-01" and po["total"] == 2_160_000 and len(po["items"]) == 1, "발주서 발행 — 번호·합계·빈 줄 제거", str(po["no"]))
        p = await svc.get(db, pid, TODAY)
        check(p["stage"] == "송금 대기" and p["po"]["has_draft"] is False and p["expected_date"] == due, "발행 → 송금 대기, 초안 비움, 입고 예정일")
        row = next(r for r in (await svc.list_purchases(db, TODAY))["rows"] if r["id"] == pid)
        check(row["expected_date"] == due and row["currency"] == "KRW", "리스트에도 입고 예정일")
        later = TODAY + dt.timedelta(days=21)
        await svc.set_expected_date(db, pid, uid, "test", later, "공급사 납기 지연")
        p = await svc.get(db, pid, TODAY)
        check(p["expected_date"] == later.isoformat() and p["expected_history"][-1]["before"] == due
              and p["history"][0]["action"] == "expected_date", "입고 예정일 바꾸기 — 이전 날짜·사유 기록")
        name, data = await svc.po_file(db, pid)
        check(name.endswith(po["no"]) and data[:2] == b"PK", "발행본 엑셀 다시 만들기")
        # 발주서 AI 대화 기록(사용자 2026-11-11) — 초안과 같이 저장, 발행해도 남음
        chat = [{"role": "user", "text": "단가 그대로"}, {"role": "assistant", "text": "네"}]
        await svc.save_po_draft(db, pid, uid, body, chat=chat)
        dr = await svc.po_draft(db, pid, {})
        check(dr["chat"] == chat and dr["source"] == "draft", "대화 기록 저장 → 작성 창에 이어서")
        await svc.save_po_draft(db, pid, uid, body)
        check((await svc.po_draft(db, pid, {}))["chat"] == chat, "그냥 저장해도 대화는 그대로")

        # ── 고친 발주서 파일로 발행(사용자 2026-11-11) — 번호는 파일의 Order No., 같은 번호면 그 판을 바꿈 ──
        later_s = later.isoformat()
        up_items = [{"commodity": "M8 커넥터", "description": "300mm", "qty": 200, "price": 10800}]
        up = {**fentry("발주서_고침.xlsx"), "key": f"purchases/{pid}/po/a.xlsx"}
        await svc.upload_po(db, pid, uid, "test", up, {"doc_no": po["no"].lower(), "expected_date": later_s, "items": up_items}, {})
        p = await svc.get(db, pid, TODAY)
        check(p["po"]["no"] == po["no"] and p["po"]["revision"] == 1 and not p["po"]["history"]
              and p["po"]["upload"]["filename"] == "발주서_고침.xlsx" and p["po"]["items"][0]["commodity"] == "M8 커넥터"
              and p["history"][0]["action"] == "po_upload", "같은 번호 — 지금 판을 고친 파일로(판 그대로)", str(p["po"]["no"]))
        src = await svc.po_source(db, pid)
        check(src["upload"]["key"] == up["key"] and src["file_name"] == "발주서_고침", "내려받기는 올린 파일")
        other = f"UND-ZZ{TODAY:%Y%m%d}-07"
        await svc.upload_po(db, pid, uid, "test", up, {"doc_no": other, "expected_date": later_s, "items": up_items}, {})
        p = await svc.get(db, pid, TODAY)
        reg = (await db.execute(text("SELECT doc_type FROM doc_numbers WHERE doc_no = :n"), {"n": other})).scalar()
        check(p["po"]["no"] == other and p["po"]["revision"] == 2 and p["po"]["history"][0]["no"] == po["no"] and reg == "PO",
              "다른 번호 — 새 판, 예전 판은 history, 번호 기록(PO)", f"{p['po']['no']} {reg}")
        await expect_err(svc.upload_po(db, pid, uid, "test", up, {"doc_no": po["no"], "expected_date": later_s, "items": up_items}, {}),
                         "이미 쓴 번호(예전 판) 거절", "이미")
        await db.rollback()
        await expect_err(svc.upload_po(db, pid, uid, "test", up, {"doc_no": "", "expected_date": later_s, "items": up_items}, {}),
                         "번호를 비우면 이니셜 필요", "이니셜")
        await db.rollback()
        await expect_err(svc.upload_po(db, pid, uid, "test", up, {"doc_no": "X1", "expected_date": "", "items": up_items}, {}),
                         "입고 예정일 없으면 거절", "입고 예정일")
        await db.rollback()
        await expect_err(svc.upload_po(db, pid, uid, "test", up, {"doc_no": "X1", "expected_date": later_s, "items": []}, {}),
                         "품목 없으면 거절", "품목")
        await db.rollback()
        await svc.upload_po(db, pid, uid, "test", up, {"doc_no": "", "initials": "zz", "expected_date": later_s, "items": up_items}, {})
        p = await svc.get(db, pid, TODAY)
        check(p["po"]["no"] == f"UND-ZZ{TODAY:%Y%m%d}-08" and p["po"]["revision"] == 3,
              "번호 비우면 새 번호(기록된 07 다음)", p["po"]["no"])

        await svc.set_terms(db, pid, uid, [{"label": "선금", "pct": 50, "when": "발주 시"}, {"label": "잔금", "pct": 50}], admin=False)
        p = await svc.get(db, pid, TODAY)
        check([t["amount"] for t in p["terms"]] == [1_080_000, 1_080_000], "회차 금액(공급가액 × %)")
        # 회차별 [송금 확인] — 지출내역 대조 없이 버튼(사용자 2026-11-11, 영업 건 입금 확인과 같게)
        await svc.confirm_term(db, pid, uid, "test", 0, TODAY, None)
        p = await svc.get(db, pid, TODAY)
        check(p["terms"][0]["confirmed"] and not p["terms"][1]["confirmed"] and p["stage"] == "송금 대기",
              "선금만 송금 확인 → 아직 송금 대기(잔금 남음)")
        await expect_err(svc.confirm_term(db, pid, uid, "test", 0, TODAY, None), "같은 회차 두 번 거절", "이미")
        await db.rollback()
        # 예전에 지출내역 대조로 불일치였던 회차 — 사유를 적으면 불일치 기록과 같이 확인
        rows = [{"text": f"2026-11-02 | {SUP} | 잔금 | 123,000", "amounts": [123_000]}]
        bad = {**ec.judge(rows, SUP, 1_080_000, 1_188_000), "status": "mismatch", "summary": "불일치", "expected": {"supply": 1_080_000}}
        await svc.set_check(db, pid, uid, 1, bad, fentry("지출2.xlsx"))
        await svc.confirm_term(db, pid, uid, "test", 1, TODAY, "카드 결제로 일부 지급")
        p = await svc.get(db, pid, TODAY)
        check(all(t["confirmed"] for t in p["terms"]) and p["terms"][1]["mismatch"] and p["steps"][3]["done"] and p["stage"] == "배송 대기",
              "회차 모두 확인(불일치 기록 포함) → 배송 대기", p["stage"])
        check(any(a["code"] == "PAY_MISMATCH" for a in p["alerts"]), "불일치 알림")
        await expect_err(svc.set_terms(db, pid, uid, [{"label": "전액", "pct": 100}], admin=False), "송금 확인 뒤 결제 조건 변경 거절 — [확인 취소] 안내", "확인 취소")
        await db.rollback()
        changed = {**body, "items": [{"description": "M8 커넥터", "qty": 100, "price": 10800}]}
        await expect_err(svc.issue_po(db, pid, uid, "test", changed), "송금 확인 뒤 발주 금액 변경 거절", "확인 취소")
        await db.rollback()
        # 금액이 바뀌는 경우(사용자 2026-11-11) — 송금 확인 취소(누구나) → 다시 확인
        await svc.unconfirm_term(db, pid, uid, 1)
        p = await svc.get(db, pid, TODAY)
        check(not p["terms"][1]["confirmed"] and p["terms"][0]["confirmed"] and p["stage"] == "송금 대기", "회차 송금 확인 취소 → 송금 대기", p["stage"])
        await expect_err(svc.unconfirm_term(db, pid, uid, 1), "확인 안 한 회차 취소 거절", "확인하지 않은")
        await db.rollback()
        await svc.confirm_term(db, pid, uid, "test", 1, TODAY, None)
        await db.rollback()

        # 배송(사용자 2026-11-11) — [배송 시작] → 배송 중
        await expect_err(svc.start_shipping(db, pid, uid, "test", TODAY - dt.timedelta(days=30), ""), "발주일 앞 배송 시작 거절", "발주일")
        await db.rollback()
        await svc.start_shipping(db, pid, uid, "test", TODAY, "공급사 전화 — 경동택배")
        p = await svc.get(db, pid, TODAY)
        check(p["stage"] == "배송 중" and p["in_transit"] and p["shipping"]["note"] == "공급사 전화 — 경동택배", "배송 시작 → 배송 중", p["stage"])
        await expect_err(svc.start_shipping(db, pid, uid, "test", TODAY, ""), "배송 시작 두 번 거절", "이미")
        await db.rollback()
        await expect_err(svc.add_receipt(db, pid, uid, "test", {"date": TODAY, "lines": [{"line_no": 0, "qty": 300}]}),
                         "발주 수량보다 많이 입고 거절", "넘습니다")
        await db.rollback()
        await expect_err(svc.add_receipt(db, pid, uid, "test", {"date": TODAY, "lines": []}), "수량 없는 입고 거절", "수량")
        await db.rollback()
        await expect_err(svc.add_receipt(db, pid, uid, "test", {"date": TODAY, "lines": [{"line_no": 0, "qty": 2, "serials": ["A1", "a1"]}]}),
                         "같은 S/N 두 번 거절", "두 번")
        await db.rollback()
        await expect_err(svc.add_receipt(db, pid, uid, "test", {"date": TODAY, "lines": [{"line_no": 0, "qty": 1, "serials": ["A1", "A2"]}]}),
                         "입고 수량보다 많은 S/N 거절", "많습니다")
        await db.rollback()
        await svc.add_receipt(db, pid, uid, "test", {"date": TODAY, "carrier": "경동택배", "tracking": "123 456", "receiver": "이승학",
                                                      "location": "2층 창고", "lines": [{"line_no": 0, "qty": 120, "photos": ["a" * 32 + ".jpg", "../x"],
                                                                                         "serials": [" SN-001 ", "", "SN-002"]}]})
        p = await svc.get(db, pid, TODAY)
        r0 = p["receipt"]["records"][0]
        check(not p["receipt"]["done"] and p["receipt"]["lines"][0]["received"] == 120 and r0["tracking"] == ["123", "456"]
              and r0["lines"][0]["photos"] == ["a" * 32 + ".jpg"], "부분 입고 기록(운송장·사진, 잘못된 사진 이름 버림)")
        check(r0["lines"][0]["serials"] == ["SN-001", "SN-002"] and p["stage"] == "입고 대기" and not p["in_transit"],
              "입고 S/N(빈 칸 버림), 입고 기록이 들어오면 배송 중 → 입고 대기", p["stage"])
        await svc.update_receipt(db, pid, uid, "test", 0, {"date": TODAY, "carrier": "경동택배", "lines": [{"line_no": 0, "qty": 100}]})
        await svc.add_receipt(db, pid, uid, "test", {"date": TODAY, "lines": [{"line_no": 0, "qty": 100}]})
        p = await svc.get(db, pid, TODAY)
        st = {x["key"]: x for x in p["steps"]}
        check(p["receipt"]["done"] and len(p["receipt"]["records"]) == 2 and st["receipt"]["done"] and p["stage"] == "완료",
              "입고 기록 수정 + 나머지 입고 → 입고 완료 → 구매 완료(명세서·계산서 없어도)", p["stage"])
        # 입고 기록을 남긴 뒤(입고 완료 뒤에도) S/N 따로 등록
        await svc.set_receipt_serials(db, pid, uid, 1, [{"line_no": 0, "serials": ["SN-101", "SN-102"]}])
        p = await svc.get(db, pid, TODAY)
        check(p["receipt"]["records"][1]["lines"][0]["serials"] == ["SN-101", "SN-102"] and p["receipt"]["records"][1]["lines"][0]["qty"] == 100,
              "입고 완료 뒤 S/N 따로 등록(수량은 그대로)")
        await expect_err(svc.set_receipt_serials(db, pid, uid, 9, [{"line_no": 0, "serials": ["x"]}]), "없는 입고 기록 S/N 거절", "입고 기록")
        await db.rollback()
        await expect_err(svc.add_receipt(db, pid, uid, "test", {"date": TODAY, "lines": [{"line_no": 0, "qty": 1}]}), "입고 완료 뒤 추가 거절", "이미")
        await db.rollback()
        await expect_err(svc.confirm_paid(db, pid, uid, "test", TODAY, ""), "이미 송금 확인한 건은 버튼 거절", "이미")
        await db.rollback()
        # 거래명세서·세금계산서(선택, 사용자 2026-11-11) — 못 끊는 건은 선결제·선발행 사유 기록
        await svc.confirm_doc(db, pid, uid, "test", "statement", "prepaid", TODAY, "10월분 선결제")
        p = await svc.get(db, pid, TODAY)
        check(p["docs"]["statement"] == {"state": "skip", "label": "선결제", "mode": "prepaid", "note": "10월분 선결제"}
              and p["prepaid"]["statement"] and p["stage"] == "완료", "거래명세서 선결제 — 첨부 없이 사유 기록")
        await expect_err(svc.confirm_doc(db, pid, uid, "test", "tax", "normal", TODAY), "사유 구분 아닌 값 거절", "선결제")
        await db.rollback()
        await svc.confirm_doc(db, pid, uid, "test", "tax", "pre_issued", TODAY)
        await svc.add_file(db, pid, uid, "tax", fentry("세금계산서.pdf"))
        p = await svc.get(db, pid, TODAY)
        check(p["docs"]["tax"]["state"] == "file" and p["docs"]["tax"]["files"] == 1, "사유를 남겨도 나중에 첨부하면 '첨부'")
        await svc.undo_step(db, pid, uid, "statement")
        p = await svc.get(db, pid, TODAY)
        check(p["docs"]["statement"]["state"] == "none" and p["stage"] == "완료", "선결제 기록 되돌리기 — 완료는 그대로")

        await svc.set_dropped(db, pid, uid, True, "테스트 취소")
        p = await svc.get(db, pid, TODAY)
        check(p["stage"] == "취소" and p["drop_reason"] == "테스트 취소", "구매 취소")
        await expect_err(svc.add_receipt(db, pid, uid, "test", {"date": TODAY, "lines": [], "final": True}), "취소 건은 기록 거절", "취소")
        await db.rollback()

        n = await svc.delete(db, [pid])
        left = (await db.execute(text("SELECT COUNT(*) FROM purchase_events WHERE purchase_id = :p"), {"p": pid})).scalar()
        kept = (await db.execute(text("SELECT COUNT(*) FROM doc_numbers WHERE doc_no = :n"), {"n": po["no"]})).scalar()
        check(n == 1 and left == 0 and kept == 1, "삭제 — 이력도 지움, 발주 번호는 남김")

        # ── 국내 송금 = [송금 확인] 버튼 한 번(사용자 2026-11-11 — 지출내역 대조 뺌) ──
        kid = await svc.create(db, uid, {"supplier": SUP, "title": "버튼 송금"})
        # 첨부 없이 버튼으로 넘어가기(사용자 2026-11-11) — 견적서 없이 → 재가 서류 없이 재가 완료
        await expect_err(svc.approve(db, kid, uid, "test", TODAY), "견적 전 재가 완료 거절", "견적서")
        await db.rollback()
        await svc.skip_quote(db, kid, uid, "test", True)
        k = await svc.get(db, kid, TODAY)
        check(k["stage"] == "재가 대기" and k["approval"]["quote_skip"]["by"] == "test", "견적서 없이 넘어가기 → 재가 대기", k["stage"])
        await svc.skip_quote(db, kid, uid, "test", False)
        check((await svc.get(db, kid, TODAY))["stage"] == "견적 대기", "견적서 없이 넘어가기 되돌리기")
        await svc.skip_quote(db, kid, uid, "test", True)
        await svc.approve(db, kid, uid, "test", TODAY)
        k = await svc.get(db, kid, TODAY)
        check(k["stage"] == "발주 대기" and k["approval"]["no_file"], "재가 서류 없이 재가 완료 → 발주 대기(첨부 없이 기록)", k["stage"])
        await expect_err(svc.skip_quote(db, kid, uid, "test", False), "재가 뒤엔 견적서 없이 되돌리기 거절", "재가")
        await db.rollback()
        await svc.issue_po(db, kid, uid, "test", body)
        await svc.confirm_paid(db, kid, uid, "test", TODAY, "선금·잔금 한 번에")
        k = await svc.get(db, kid, TODAY)
        check(k["steps"][3]["done"] and k["stage"] == "배송 대기" and k["payment"]["note"] == "선금·잔금 한 번에",
              "예전 국내 [송금 확인] 한 번 기록 → 송금 끝·배송 대기", k["stage"])
        await svc.unconfirm_paid(db, kid, uid)
        check((await svc.get(db, kid, TODAY))["stage"] == "송금 대기", "송금 확인 취소(누구나)")
        await svc.delete(db, [kid])

        # ── 해외(사용자 2026-11-02): 견적 → 재가 → 발주(달러·입고 예정일) → 송금 완료 → 통관(원산지 증명서) → 입고 ──
        oid = await svc.create(db, uid, {"supplier": SUP_EN, "title": "AMR", "region": "overseas"})
        await svc.add_file(db, oid, uid, "quote", fentry("quote.pdf"))
        await svc.add_file(db, oid, uid, "approval", fentry("재가.pdf"))
        await svc.approve(db, oid, uid, "test", TODAY)
        dr = await svc.po_draft(db, oid, {"initials": "ZZ", "name_en": "Alex Jeon", "honorific": "Mr.", "email": "alex@unde.co.kr",
                                          "mobile": "010-9515-5466"})
        check(dr["lang"] == "en" and dr["currency"] == "USD" and dr["header"]["issuer"]["attn"] == "Mr. Alex Jeon (alex@unde.co.kr)"
              and "Gumi-si" in dr["header"]["issuer"]["address"], "해외 발주서 기본값(영문)", str(dr["header"]["issuer"]))
        # 내 정보로 채우기(사용자 2026-11-11) — 저장한 초안에서 비어 있는 사람 칸만 채우고, 적어 둔 값은 그대로
        me = {"initials": "ZZ", "name_en": "Alex Jeon", "honorific": "Mr.", "email": "alex@unde.co.kr", "mobile": "010-9515-5466"}
        blank = {**dr["header"], "issuer": {**dr["header"]["issuer"], "name": "", "title": "", "tel": "", "attn": "Ms. Kim (kim@x.com)"}}
        await svc.save_po_draft(db, oid, uid, {"initials": "", "header": blank, "items": []})
        d3 = await svc.po_draft(db, oid, me)
        check(d3["source"] == "draft" and d3["header"]["issuer"]["name"] == "Alex Jeon" and d3["header"]["issuer"]["title"] == "Mr."
              and d3["header"]["issuer"]["tel"].startswith("82-10") and d3["header"]["issuer"]["attn"] == "Ms. Kim (kim@x.com)"
              and d3["initials"] == "ZZ" and d3["mine"]["issuer"]["attn"] == "Mr. Alex Jeon (alex@unde.co.kr)",
              "초안 — 빈 담당 칸·이니셜은 내 정보로, 적어 둔 ATTN 은 그대로, mine 은 [내 정보로 채우기]용", str(d3["header"]["issuer"]))
        await db.execute(text("UPDATE purchases SET po = po - 'draft' WHERE id = :i"), {"i": oid})
        await db.commit()
        await expect_err(svc.add_file(db, oid, uid, "statement", fentry("st.pdf")), "해외엔 거래명세서 없음", "해외 구매에는 없는")
        await db.rollback()
        ob = {"initials": "ZZ", "expected_date": (TODAY + dt.timedelta(days=40)).isoformat(),
              "header": {**dr["header"], "swift": "BKCHCNBJ"}, "items": [{"description": "AX8114", "qty": 2, "price": 4600.5, "remark": "Sea"}]}
        # 해외 납기일은 선택(사용자 2026-11-11) — 비워도 발행되고 다음 단계로
        await svc.issue_po(db, oid, uid, "test", {**ob, "expected_date": ""})
        o = await svc.get(db, oid, TODAY)
        check(o["stage"] == "제조사 송금 대기" and o["expected_date"] is None, "해외 — 납기일 없이 발행해도 다음 단계로", o["stage"])
        opo = await svc.issue_po(db, oid, uid, "test", ob)
        check(opo["currency"] == "USD" and opo["total"] == 9201.0 and "Purchasing" in opo["file_name"], "해외 발주서 — 달러·파일명", str(opo["total"]))
        name, data = await svc.po_file(db, oid)
        check(data[:2] == b"PK", "해외 발주서 엑셀")
        # 예전 양식(LHTC) 파일로 고쳐 발행 → [발주서 고치기]에 머리·품목이 그대로 → 고쳐 저장 → 다시 발행(사용자 2026-11-11)
        ex = ROOT / "docs" / "examples" / "quotes" / "유엔디로보틱스_LHTC_Purchasing_Lu20261008.xlsx"
        if ex.exists():
            rd = po_read.read_template(ex.read_bytes())
            items = [{**x, "qty": x["qty"] or 1} for x in rd["items"]]
            await svc.upload_po(db, oid, uid, "test", {**fentry("LHTC.xlsx"), "key": "purchases/x/po/a.xlsx"},
                                {"doc_no": "", "initials": "ZZ", "items": items, "header": rd["header"], "expected_date": ""}, dr)
            o = await svc.get(db, oid, TODAY)
            d2 = await svc.po_draft(db, oid, {"initials": "ZZ"})
            check(d2["header"]["supplier"]["name"] == "Zhengzhou Linghang Robot Co.,Ltd." and d2["header"]["swift"] == "BKCHCNBJ530"
                  and d2["header"]["account_no"] == "250790357823" and d2["items"][0]["commodity"] == "LMO-E04-S11-M"
                  and d2["header"]["issuer"]["attn"] == dr["header"]["issuer"]["attn"],
                  "예전 양식으로 발행 → 발주서 고치기에 머리(공급사·계좌)·품목이 채워짐(발행처는 그대로)", str(d2["header"])[:200])
            fixed = {"initials": "ZZ", "expected_date": "", "header": d2["header"],
                     "items": [{**d2["items"][0], "qty": 5}, *d2["items"][1:]]}
            await svc.save_po_draft(db, oid, uid, fixed)
            check((await svc.po_draft(db, oid, {}))["items"][0]["qty"] == 5, "발주서 고치기 — 고친 값 저장")
            rev0 = o["po"]["revision"]
            opo2 = await svc.issue_po(db, oid, uid, "test", fixed)
            o = await svc.get(db, oid, TODAY)
            check(opo2["revision"] == rev0 + 1 and o["po"]["items"][0]["qty"] == 5 and o["po"]["upload"] is None
                  and o["po"]["history"][-1]["upload"] is not None, "고친 값으로 다시 발행 — 새 판, 예전 판(올린 파일)은 기록에",
                  f'{rev0}->{opo2["revision"]} hist={[h.get("upload") for h in o["po"]["history"]]}')
            # AI 판독 실패 — [버그 신고 후 넘어가기](사용자 2026-11-11): 품목이 비어도 파일 그대로 발행
            await expect_err(svc.upload_po(db, oid, uid, "test", {**fentry("scan.pdf"), "key": "purchases/x/po/b.pdf"},
                                           {"doc_no": "", "initials": "ZZ", "items": [{"qty": 1}]}, dr), "판독 실패 그대로는 거절", "품목")
            await db.rollback()
            await svc.upload_po(db, oid, uid, "test", {**fentry("scan.pdf"), "key": "purchases/x/po/b.pdf"},
                                {"doc_no": "", "initials": "ZZ", "items": [{"qty": 1}, {"commodity": "LMO", "price": None}],
                                 "forced": True, "bug_report": "UND-99999"}, dr)
            o = await svc.get(db, oid, TODAY)
            check(o["po"]["forced"]["bug_report"] == "UND-99999" and [x["commodity"] for x in o["po"]["items"]] == ["LMO"]
                  and o["po"]["upload"]["filename"] == "scan.pdf" and o["stage"] == "제조사 송금 대기",
                  "버그 신고 후 넘어가기 — 파일 그대로 발행(빈 줄 버림), 다음 단계로", str(o["po"]["items"]))
            await svc.issue_po(db, oid, uid, "test", ob)                     # 아래 테스트는 처음 품목 기준
            check(not (await svc.get(db, oid, TODAY))["po"]["forced"], "발주서 고치기로 다시 발행하면 판독 실패 표시 없어짐")
        await expect_err(svc.set_terms(db, oid, uid, [{"label": "전액", "pct": 100}], admin=False), "해외엔 % 회차 없음", "해외")
        await db.rollback()
        # 제조사 송금(확인증 첨부는 선택) → 배송 → 통관 서류 → 관세사 송금 → 입고(사용자 2026-11-11)
        await svc.add_file(db, oid, uid, "remit", fentry("송금확인증.pdf"))
        await svc.confirm_paid(db, oid, uid, "test", TODAY, "T/T")
        o = await svc.get(db, oid, TODAY)
        check(o["stage"] == "배송 대기" and o["customs"]["remit_files"][0]["filename"] == "송금확인증.pdf", "제조사 송금 확인 + 확인증 → 배송 대기", o["stage"])
        await svc.start_shipping(db, oid, uid, "test", TODAY, "")
        o = await svc.get(db, oid, TODAY)
        check(o["stage"] == "배송 중" and o["stage_note"] == "통관 서류 · 관세사 송금 남음", "해외 배송 중 — 남은 일 표시", o["stage_note"])
        await svc.set_no_origin(db, oid, uid, True)
        await expect_err(svc.add_file(db, oid, uid, "customs_etc", fentry("ci.pdf")), "기타 서류는 이름 필요", "서류 이름")
        await db.rollback()
        await svc.add_file(db, oid, uid, "customs_etc", fentry("ci.pdf"), "CI")
        await svc.add_file(db, oid, uid, "customs_etc", fentry("pl.pdf"), "Packing List")
        await svc.confirm_customs(db, oid, uid, "test", TODAY, "")
        o = await svc.get(db, oid, TODAY)
        check(o["customs"]["no_origin"] and [f["label"] for f in o["customs"]["etc_files"]] == ["CI", "Packing List"]
              and o["customs"]["done_at"] and not o["customs"]["files"], "원산지 증명서 없음 → 기타 서류 2개 → 통관 서류 제출 완료")
        await svc.confirm_broker(db, oid, uid, "test", TODAY, "관세 120,000원")
        await svc.add_receipt(db, oid, uid, "test", {"date": TODAY, "lines": [{"line_no": 0, "qty": 1, "serials": ["AX-1"]}], "final": True})
        o = await svc.get(db, oid, TODAY)
        check(o["stage"] == "완료" and [s["key"] for s in o["steps"]] == ["quote", "approval", "order", "pay", "ship", "customs", "broker", "receipt"],
              "해외 — 관세사 송금·입고까지 → 완료")
        await svc.undo_step(db, oid, uid, "customs")
        o = await svc.get(db, oid, TODAY)
        check(o["customs"]["broker"] and len(o["customs"]["etc_files"]) == 2 and o["stage"] == "통관 서류 대기",
              "통관 서류 제출 취소 — 관세사 송금·첨부는 그대로", o["stage"])
        await svc.delete(db, [oid])
        await db.execute(text("DELETE FROM doc_numbers WHERE doc_type = 'PO' AND issuer = 'ZZ'"))
        await db.commit()


asyncio.run(run())
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
