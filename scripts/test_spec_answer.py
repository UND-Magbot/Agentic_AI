# -*- coding: utf-8 -*-
"""채팅에서 제품 사양 바로 답하기(사용자 2026-10-28) — 사양 질문 알아듣기·모델 찾기·물은 항목만·근거·가로채지 않을 말.

    MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_spec_answer.py

개발 DB(agent_ai_dev)의 사양 DB(맥봇 공식 스펙 카드·타사 로봇 비교표)를 읽기만 한다.
"""
from __future__ import annotations

import asyncio
import sys

from app.company_knowledge import spec_answer as sa
from app.database import SessionLocal

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
OFFICIAL = "맥봇 제품 스펙 요약표 (2026-09-30)"


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def ask(db, t: str) -> str | None:
    q = sa.detect([{"role": "user", "content": t}])
    return await sa.answer(db, q) if q else None


async def cases() -> None:
    async with SessionLocal() as db:
        out = await ask(db, "TCV1 페이로드 몇 kg이야?") or ""
        check("10 kgf" in out and f"근거: {OFFICIAL}" in out and "소개서" not in out, "TCV1 가반하중 — 공식 사양 근거", out)
        check("제품 무게" not in out and "IoT" not in out, "물은 항목만(무게·관계없는 경고 빼고)", out)
        out = await ask(db, "TCV1 IP 등급") or ""
        check("IP45" in out, "띄어 쓴 'TCV1 IP 등급'도 모델 찾기", out)
        out = await ask(db, "TCV2 사양 알려줘") or ""
        check(all(k in out for k in ("가반하중", "제품 무게", "IP 등급", "전기 사양")), "사양 전체", out)
        out = await ask(db, "TCV1이랑 TCV2 가반하중 비교해줘") or ""
        check("■ TCV1" in out and "■ TCV2" in out and "16 kgf" in out, "두 모델", out)
        out = await ask(db, "MG10 무게 알려줘") or ""
        check("■ MG10 " in out and "MG100" not in out, "MG10 이 MG100 에 걸리지 않음", out)
        out = await ask(db, "MG100 가반하중") or ""
        check(out.count("적용 조건") == 1, "적용 조건 한 번만", out)
        out = await ask(db, "M-LTC-0040A 무게") or ""
        check("Weight (Main product)" in out and "Payload" not in out, "무게에 Payload(Weight Capability) 섞지 않음", out)
        out = await ask(db, "시프트락 그리퍼 크기") or ""
        check("mSMG-1S25D" in out, "다른 이름(시프트락 그리퍼)으로 찾기", out)
        out = await ask(db, "UR5e 도달거리 얼마야?") or ""
        check("850mm" in out and "6축 로봇 비교표" in out and "타사" in out, "타사 로봇 — 비교표 근거", out)
        out = await ask(db, "UR5 가반하중") or ""
        check("UR5e" in out, "모델 앞부분(UR5)으로 찾기", out)
        out = await ask(db, "리프팅 AMR 가반하중") or ""
        check("자료에 없습니다" in out and "회사 소개서" in out, "없는 항목은 '자료에 없습니다' + 소개서 근거 표시", out)
        for t in ("페이로드 10kg 로봇에 맞는 툴체인저 추천해줘", "TCV3 가격 얼마야?", "TCV1 무게 견적서에 넣어줘",
                  "연차 쓰려면 어떻게 해?", "로봇 가반하중이 뭐야?", "XYZ999 무게", "퍼씨엠 견적 금액 얼마였지?"):
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
