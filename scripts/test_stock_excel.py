# -*- coding: utf-8 -*-
"""공장 제품 재고 엑셀 — 내려받기 · 고쳐 올리기 미리보기 · 고른 줄만 반영(사용자 2026-11-20).

짝 짓기 순서: 재고 번호 → S/N → AI 추정(이 테스트는 AI 대신 정해 둔 답). 상태 칸을 비우면 상태 그대로, 목록에 없는 상태·'대여 중'은 반영 안 함.
dev DB 전용, 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_stock_excel.py
"""
import asyncio
import io
import os
import sys

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from openpyxl import load_workbook  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

SUP = "__QA_재고엑셀__"
UID = 1
res: list[bool] = []


def check(ok: bool, name: str, *more) -> None:
    res.append(bool(ok))
    print("PASS" if ok else "FAIL", name, *more)


async def cleanup() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM product_units WHERE und_serial LIKE 'QX-%'"))
        await db.execute(text("DELETE FROM purchases WHERE supplier = :s"), {"s": SUP})
        await db.commit()


async def run() -> None:
    from app.purchases import service as ps
    from app.purchases import stock
    from app.purchases import stock_excel as sx

    async with SessionLocal() as db:
        pid = await ps.direct_draft(db, UID, "QA")
        await ps.save_direct(db, pid, UID, "QA", {"purpose": "sale", "date": ps.kst_today(), "supplier": SUP, "region": "domestic", "lines": [
            {"name": "QX 로봇", "qty": 4, "units": [{"und_sn": f"QX-{k}", "customer": "A사"} for k in range(1, 5)]}]})
        mine = sorted(r["id"] for r in (await stock.list_items(db, "sale"))["rows"] if r["purchase_id"] == pid)
        data = await sx.export(db)
        wb = load_workbook(io.BytesIO(data))
        check(wb.sheetnames[:3] == ["안내", "판매용", "보유 후 자체 개발용"], "시트 — 안내·판매용·보유 후 자체 개발용", wb.sheetnames)
        ws = wb["판매용"]
        head = [c.value for c in ws[1]]
        check(head[0] == "재고 번호" and head[-2:] == ["상태", "상태 메모"], "재고 번호 열 + 상태 옆에 상태 메모", head)
        col = {h: i for i, h in enumerate(head)}
        rows = {r[0].value: r for r in ws.iter_rows(min_row=2) if r[0].value in mine}
        check(len(rows) == 4 and all(rows[i][col["상태"]].value == "대기 중" for i in mine), "우리 줄 4개, 상태 '대기 중'", len(rows))
        check(len(ws.data_validations.dataValidation) == 1, "상태 칸 고르기 목록", len(ws.data_validations.dataValidation))
        a, b, c, d = mine
        rows[a][col["상태"]].value, rows[a][col["상태 메모"]].value = "수리 필요", "전원부 불량"     # 번호로
        rows[b][col["재고 번호"]].value, rows[b][col["상태"]].value = None, "사용 중"                  # 번호 지움 → S/N 으로
        rows[c][col["재고 번호"]].value = None                                                       # 번호·S/N 지움 → AI
        rows[c][col["UND S/N"]].value, rows[c][col["상태"]].value = None, "수리 중"
        rows[d][col["상태"]].value = "없는상태"                                                       # 목록에 없는 상태
        buf = io.BytesIO()
        wb.save(buf)

        async def fake_ai(rs, cands):          # AI 대신 — 이름이 같은 후보 중 아직 안 쓴 c 를 고른다
            return {i: (c, "제품명·고객사가 같음") for i, r in enumerate(rs) if r["name"] == "QX 로봇" and any(x["id"] == c for x in cands)}
        sx._ai_match = fake_ai
        pv = await sx.preview(db, buf.getvalue())
        got = {x["id"]: x for x in pv["changes"]}
        check(got.get(a, {}).get("how") == "재고 번호" and got[a]["to_status"] == "수리 필요" and got[a]["to_note"] == "전원부 불량",
              "재고 번호로 짝 — 상태·메모", got.get(a))
        check(got.get(b, {}).get("how") == "S/N" and got[b]["to_status"] == "사용 중", "번호가 없으면 S/N 으로 짝", got.get(b))
        check(got.get(c, {}).get("how") == "AI 추정" and got[c]["to_status"] == "수리 중", "번호·S/N 이 없으면 AI 추정", got.get(c))
        check(d not in got and any("없는상태" in p["why"] for p in pv["problems"]), "목록에 없는 상태는 반영 안 하고 까닭", pv["problems"])
        r0 = (await db.execute(text("SELECT status FROM stock_items WHERE id = :i"), {"i": a})).scalar()
        check(r0 == "대기 중", "미리보기는 저장하지 않음", r0)
        out = await sx.apply(db, UID, [{"id": a, "status": "수리 필요", "status_note": "전원부 불량"}, {"id": b, "status": "사용 중"},
                                       {"id": c, "status": stock.RENTED}])
        st = dict((await db.execute(text("SELECT id, status FROM stock_items WHERE id = ANY(:i)"), {"i": mine})).all())
        check(out["applied"] == 2 and len(out["failed"]) == 1 and st[a] == "수리 필요" and st[b] == "사용 중" and st[c] == "대기 중",
              "고른 줄만 반영, '대여 중'은 거절", (out, st))
        h = await stock.history(db, a)
        check(any((x.get("detail") or {}).get("방법") == "엑셀" for x in h), "이력에 '엑셀'로 바꾼 표시", [x.get("detail") for x in h][:2])
        try:
            sx.parse(b"not an excel")
            check(False, "엑셀이 아니면 거절")
        except sx.ExcelError as e:
            check("엑셀" in str(e), "엑셀이 아니면 거절", str(e)[:50])


async def main() -> None:
    await cleanup()
    try:
        await run()
    finally:
        await cleanup()
    print(f"PASS {sum(res)} / FAIL {len(res) - sum(res)}")
    sys.exit(0 if all(res) else 1)


asyncio.run(main())
