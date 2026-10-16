"""해외 견적서 AI 번역 — 실제 사내 AI 로, 미리보기 때 번역되는 흐름(사용자 2026-10-14). 개발 DB(agent_ai_dev)에 임시 견적을 만들고 지운다.

    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_quote_translate_live.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date

from sqlalchemy import text

from app.company_knowledge import product_recommend as pr
from app.database import SessionLocal

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
C = "__테스트_해외번역__"


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def cleanup() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM product_quotes WHERE customer = :c OR state->'form'->>'customer' = :c"), {"c": C})
        await db.commit()


async def run() -> None:
    await cleanup()
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()
    head = {"initials": "AL", "contact_name": "전알렉스", "contact_mobile": "010-1234-5678", "contact_email": "alex@example.com"}
    q = await pr.quote_manual(user_id=uid, form=head)
    q = await pr.quote_edit(q["id"], user_id=uid, payload={
        "form": {"customer": C, "to": "황 과장님", "lang": "en", "contact_name_en": "Alex Jeon", "contact_honorific": "Mr.",
                 "total_note": "부가세 별도"},
        "delivery": {"case": 5},
        "lines": [{"id": None, "name": "TCC1\n- 페이로드 5kg\n- 마스터 툴체인저 1개", "commodity": "Magbot ATC", "qty": 2, "usd": 1000,
                   "remark": "커플러 포함"},
                  {"id": None, "name": "현장 설치 교육", "commodity": "교육", "qty": 1, "usd": 500, "unit": "식", "remark": "발주 후 일정 협의"},
                  # 지시문 예시에 없는 문장 — 해외 기준 견적서 표기('Payload: 10 kg', '3 Tool Plates')로 일반화되는지
                  {"id": None, "name": "TCV1\n- 페이로드 10kg\n- 툴 플레이트 3개", "commodity": "Magbot ATC", "qty": 1, "usd": 900,
                   "remark": "전원 케이블 포함"}]})
    labels = [c["label"] for c in q["hangul"]]
    check(len(q["hangul"]) >= 6 and any("받는 분" in x for x in labels) and all(c["en"] is None for c in q["hangul"]),
          "해외 견적서의 한글 칸 표시(아직 번역 전)", str(labels))

    # 미리보기 = 번역 전 칸을 사내 AI 가 번역해 저장 → PDF 에는 영어(칸의 한글 원문은 그대로)
    pdf = await pr.quote_preview(q["id"], user_id=uid)
    check(pdf[:4] == b"%PDF", "미리보기 PDF")
    q = await pr.quote_get(q["id"], user_id=uid)
    print("── AI 번역 결과")
    for c in q["hangul"]:
        print(f"  {c['label']}: {c['text']!r} → {c['en']!r}{' (이름 확인)' if c['name'] else ''}")
    left = [c for c in q["hangul"] if c["en"] is None]
    check(len(left) == 0, "미리보기 뒤 모든 한글 칸에 영어(이름 포함)", str(left))
    lines = q["state"]["lines"]
    check(any(ln.get("remark") == "커플러 포함" for ln in lines), "칸에는 한글 원문 그대로")
    en = {c["text"]: c["en"] or "" for c in q["hangul"]}
    tcv = next((v for k, v in en.items() if k.startswith("TCV1")), "")
    check("Payload: 10 kg" in tcv and "3 Tool Plates" in tcv, "해외 기준 견적서 표기로 번역('Payload: 10 kg', '3 Tool Plates')", repr(tcv))
    check("TCC1" in next((v for k, v in en.items() if k.startswith("TCC1")), ""), "모델명 유지(TCC1)")
    to = next(c for c in q["hangul"] if c["path"] == "form.to")
    check(to["name"] and to["en"] and "Hwang" in to["en"], "받는 분 이름도 로마자로(이름 확인 표시)", repr(to["en"]))
    import pymupdf
    txt = "".join(pg.get_text() for pg in pymupdf.open(stream=pdf, filetype="pdf"))
    check("Couplers" in txt or "Coupler" in txt, "미리보기 PDF 에 영어가 찍힘", txt[:200])

    # 담당자가 영어 고치기 · [한글 그대로] → 미리보기 다시 = AI 를 부르지 않고 그 값으로
    q = await pr.quote_en_cell(q["id"], user_id=uid, path="form.to", en="Mr. Sean Hwang", keep=None)
    rm = next(c for c in q["hangul"] if c["text"] == "발주 후 일정 협의")
    q = await pr.quote_en_cell(q["id"], user_id=uid, path=rm["path"], en=None, keep=True)
    check(next(c for c in q["hangul"] if c["path"] == "form.to")["by"] == "user"
          and next(c for c in q["hangul"] if c["path"] == rm["path"])["keep"], "영어 고치기(by=user) · [한글 그대로] 저장")
    pdf2 = await pr.quote_preview(q["id"], user_id=uid)
    txt2 = "".join(pg.get_text() for pg in pymupdf.open(stream=pdf2, filetype="pdf"))
    check("Mr. Sean Hwang" in txt2 and "발주 후 일정 협의" in txt2, "다시 미리보기: 고친 영어 · 한글 그대로 칸이 찍힘")
    try:
        await pr.quote_en_cell(q["id"], user_id=uid, path="form.to", en="황 과장", keep=None)
        check(False, "영어 칸에 한글은 거절")
    except pr.RecommendError:
        check(True, "영어 칸에 한글은 거절")
    await cleanup()


asyncio.run(run())
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
