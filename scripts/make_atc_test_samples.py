"""테스트용 '제품 추천 질문 모두 채우기' 샘플 만들기 — docs/atc_meeting 의 추가 질문까지 모두 채운 샘플 질문지(docx)를
질문지 입력(intake)으로 바꿔 backend/app/company_knowledge/data/atc_test_samples.json 에 저장한다(사용자 2026-10-06).

왜: 최종 제안 이후(견적서 등)를 시험할 때마다 Q&A 를 일일이 채울 수 없어서, 화면 버튼 하나로 정답지 입력을 넣고 바로 추천으로 간다.
로봇 종류·속도·케이블처럼 질문지 표 밖의 답은 scripts/test_atc_meeting.py --db 의 샘플 조건과 같다(미팅 요약 AI 단계 대신).
정답 모델·견적 가능 여부는 만들 때 제품 DB 로 판정해 함께 적는다(규칙이 바뀌면 다시 돌린다).

    (로컬, .env 의 사내 DB 사용) PYTHONPATH=backend python scripts/make_atc_test_samples.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.company_knowledge import atc_meeting as am
from app.company_knowledge import atc_selection as atc

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "atc_meeting"
OUT = ROOT / "backend" / "app" / "company_knowledge" / "data" / "atc_test_samples.json"
OK = {"master_4m_sufficient": True, "system_1m_sufficient": True}


def cobot(model: str, speed: float) -> dict:
    return {"id": "R1", "model": model, "type": "cobot", "quantity": 1, "speed": {"value": speed, "unit": "%"}, "cables": dict(OK)}


# (파일, 화면 이름, 로봇, 교체 툴 수, 특별 환경) — test_atc_meeting.py --db 와 같은 조건, 추가 질문까지 채운 판만
SAMPLES = [
    ("샘플1.1_협동_사출품이송_추가질문완료", "샘플1.1 협동 사출품 이송(툴 2개)", [cobot("M1013", 60)], 2, False),
    ("샘플4.1_압력경계_통신_추가질문완료", "샘플4.1 경계 사례(피크·RS-485·5.5bar)",
     [{**cobot("UR10e", 50), "cables": {"master_4m_sufficient": True, "system_1m_sufficient": False}}], 2, True),
    ("샘플5.1_다수툴_혼합로봇_추가질문완료", "샘플5.1 다수 툴·로봇 2대", [cobot("HCR-12", 60), {**cobot("RB10", 60), "id": "R2"}], 4, True),
    ("샘플6_협동_박스팔레타이징", "샘플6 협동 박스 팔레타이징", [cobot("H2017", 50)], 2, False),
    ("샘플7_협동_대형부품이송", "샘플7 협동 대형 부품 이송", [cobot("CRX-30iA", 40)], 2, False),
    ("샘플8_협동_고중량_범위초과", "샘플8 협동 고중량(자동 범위 초과)", [cobot("CR-35iB", 30)], 2, False),
    ("샘플2.1_산업용_차체패널_추가질문완료", "샘플2.1 산업용 차체 패널",
     [{"id": "R1", "model": "HS220", "type": "industrial", "quantity": 2, "speed": {"value": 40, "unit": "%"},
       "cables": {"master_4m_sufficient": False, "system_1m_sufficient": True}}], 3, True),
    ("샘플9_산업용_도어이송", "샘플9 산업용 도어 이송", [{**cobot("R-2000iC", 45), "type": "industrial"}], 2, False),
    ("샘플10_산업용_초고중량", "샘플10 산업용 초고중량", [{**cobot("KR 500", 40), "type": "industrial"}], 2, False),
]


def header_info(data: bytes, robots: list[dict]) -> dict:
    """질문지 머리(고객사·작성자·미팅일) → project 에 넣을 값, 1번 답의 로봇 제조사 → robots[].manufacturer(바로 채움).
    사용자 2026-10-07: 미팅 정보에 고객사·로봇 제조사가 빠져 '고객 미상'으로 보였음. 표 밖 정보라 atc_meeting.parse_form 이 읽지 않는다."""
    import io
    import re

    import docx

    d = docx.Document(io.BytesIO(data))
    head = {"customer_name": None, "writer": None, "meeting_date": None}
    answer = ""
    for t in d.tables:
        cells = [[c.text.strip() for c in r.cells] for r in t.rows]
        if cells and cells[0][:3] == ["고객사", "작성자", "미팅일"] and len(cells) > 1:
            head = dict(zip(head, cells[1][:3]))
        for r in cells:
            if r and r[0].startswith("1. 어떤 로봇"):
                answer = r[1] if len(r) > 1 else ""
    for rb in robots:   # '두산로보틱스 M1013 1대 / 협동', '한화 HCR-12 1대, 레인보우 RB10 1대' → 모델 바로 앞(쉼표·/ 이후)이 제조사
        m = re.search(r"(?:^|[,/·]\s*)([^,/·]*?)\s*" + re.escape(rb["model"]), answer)
        maker = m.group(1).strip() if m else ""
        if maker and "모름" not in maker:
            rb["manufacturer"] = maker
    return {k: v for k, v in head.items() if v}


async def main() -> int:
    from app.company_knowledge import product_recommend as pr
    from app.database import SessionLocal

    async with SessionLocal() as db:
        catalog = await pr.atc_catalog(db)
    out, bad = [], []
    for i, (fname, label, robots, tool_count, env) in enumerate(SAMPLES, 1):
        intake = {"project": {"process": {"category": "transfer"}, "tool_count": tool_count,
                              "environment": {"has_special_conditions": env}}, "robots": robots, "tools": []}
        raw = (DOCS / f"{fname}.docx").read_bytes()
        intake["project"].update(header_info(raw, robots))
        am.overlay(intake, am.parse_form(raw))
        o = atc.evaluate(intake, catalog)
        first = next((c for c in o["screening_candidates"] if c["models"] and not c.get("out_of_range")), None)
        model = first["models"][0]["name"] if first else None
        if o["pending_questions"]:
            bad.append(f"{fname}: 아직 물을 질문 {len(o['pending_questions'])}개")
        out.append({"id": f"s{i}", "label": label, "file": f"{fname}.docx", "expected_model": model,
                    "quotable": bool(first and first["series"] == "auto" and model in atc.ACC_WIRED_AUTO), "intake": intake})
        print(f"{label}: {model} (견적 {'가능' if out[-1]['quotable'] else '불가 — 단가 없음'})")
    OUT.write_text(json.dumps({"revision": atc.RULES_REVISION, "samples": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {OUT} ({len(out)}개)")
    for b in bad:
        print("  ✗", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
