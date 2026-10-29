# -*- coding: utf-8 -*-
"""채팅에서 제품 단가 바로 답하기 — 영업부만(사용자 2026-10-28). 알아듣기·모델·품목·가격 미정·권한·가로채지 않을 말.

    MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_price_answer.py

개발 DB(agent_ai_dev)의 단가 DB(사용 중인 단가표)를 읽기만 한다. 기대 금액은 DB 에서 읽어 맞춘다(단가표가 바뀌어도 깨지지 않게).
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.company_knowledge import price_answer as pa
from app.database import SessionLocal

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def ask(db, t: str, allowed: bool = True) -> str | None:
    q = pa.detect([{"role": "user", "content": t}])
    return await pa.answer(db, q, allowed=allowed) if q else None


async def price(db, model: str, item: str) -> int | None:
    v = (await db.execute(text("SELECT customer_price FROM product_prices WHERE active AND model = :m AND item = :i"),
                          {"m": model, "i": item})).scalar()
    return int(v) if v is not None else None


async def cases() -> None:
    async with SessionLocal() as db:
        out = await ask(db, "TCV3 가격 얼마야?") or ""
        mt = await price(db, "TCV3", "MASTER T.C")
        check("■ TCV3" in out and f"{mt:,}원" in out and "부가세 별도" in out and "근거: Magbot 단가표" in out, "TCV3 전체 품목·근거", out)
        out = await ask(db, "TCV1 가격") or ""
        check("C16" not in out and "v0.8" not in out, "비고에 규칙 번호 안 보임", out)
        out = await ask(db, "TCV1 마스터 단가") or ""
        check("MASTER T.C" in out and "- T.P" not in out, "물은 품목(마스터)만", out)
        out = await ask(db, "TCV1 마스터 대리점가 얼마야") or ""
        check("대리점가" in out, "대리점가", out)
        out = await ask(db, "MG10 가격") or ""
        check("■ MG10 " in out and "MG100" not in out and "본체" in out, "MG10 이 MG100 에 걸리지 않음", out)
        out = await ask(db, "MTC 가격 알려줘") or ""
        check("MTC_L" in out and "MTC_S" in out, "앞부분(MTC)으로 두 모델", out)
        out = await ask(db, "단가표에서 TCV3 가격 얼마야?") or ""
        check("■ TCV3" in out, "'단가표에서' 말이 있어도", out)
        unset = (await db.execute(text("SELECT model, item FROM product_prices WHERE active AND price_status = 'unset' LIMIT 1"))).first()
        if unset:
            out = await ask(db, f"{unset.model} 가격") or ""
            check("가격 미정" in out and "무상이 아니며" in out, f"0원·빈 칸 → 가격 미정({unset.model} {unset.item})", out)
        # 영업부가 아니면 — 모델을 찾았을 때 끊는다(AI 로 넘기지 않음)
        check(await ask(db, "TCV3 가격 얼마야?", allowed=False) == pa.DENIED, "영업부 아님 → 영업부만")
        check(await ask(db, "XYZ999 가격 얼마야?", allowed=False) is None, "영업부 아님 + 모르는 모델 → AI 로")
        for t in ("퍼씨엠 견적 금액 얼마였지?", "TCV1 무게 얼마야?", "TCV1 견적서 금액 얼마야?", "XYZ999 가격", "연차 쓰려면 어떻게 해?",
                  "가격 정책이 어떻게 돼?", "선진물류 입금 금액 얼마야?"):
            check(await ask(db, t) is None, f"가로채지 않음: {t}")


def main() -> None:
    asyncio.run(cases())
    for p in PASS:
        print("PASS", p)
    for f, d in FAIL:
        print("FAIL", f, "\n   ", d.replace("\n", "\n    "))
    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
