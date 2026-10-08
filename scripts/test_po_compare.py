# -*- coding: utf-8 -*-
"""발주서 ↔ 견적 품목 대조(sales_deals/po_compare.py) 점검 — 읽기·지어낸 숫자 차단·짝 맞추기·수량/단가/부가세 판정.
AI 호출은 가짜 답으로 바꿔 코드 쪽 판단만 본다.
    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_po_compare.py
"""
from __future__ import annotations

import asyncio
import io
import json
import sys

import openpyxl

from app.sales_deals import po_compare as pc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


QUOTE = [{"name": "TCV1 MASTER T.C\nPayload 20Kg", "qty": 1, "unit_price": 2_800_000},
         {"name": "PPM", "qty": 1, "unit_price": 250_000},
         {"name": "PPF", "qty": 4, "unit_price": 200_000}]

# 엑셀 발주서(고객사 양식) 읽기
wb = openpyxl.Workbook()
ws = wb.active
ws.append(["발주번호", "PO-2026-1008"])
ws.append(["품명", "규격", "수량", "단가", "금액"])
ws.append(["툴체인저 마스터", "TCV1", 1, 2800000, 2800000])
ws.append(["포고핀 수", "PPM", 1, 250000, 250000])
ws.append(["포고핀 암", "PPF", 3, 200000, 600000])
buf = io.BytesIO()
wb.save(buf)
text, images = pc.read_po(buf.getvalue(), "발주서.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
check("PO-2026-1008" in text and "2800000" in text and not images, "엑셀 발주서 → 셀 글자", text[:200])
check(pc.read_po(b"x", "발주서.hwp", "application/x-hwp") == ("", []), "한글 파일은 읽지 않음(직접 확인 안내)")


async def fake_chat(answer: dict):
    async def _chat(messages, **kw):
        _chat.messages = messages
        return json.dumps(answer, ensure_ascii=False)
    return _chat


async def main() -> None:
    # AI 가 품명이 다른 줄도 짝을 고르고, 원문에 없는 숫자(단가 999)를 지어냄 → 코드가 지운다
    answer = {"items": [{"name": "툴체인저 마스터", "spec": "TCV1", "qty": 1, "unit_price": 2800000, "amount": 2800000, "match": 1},
                        {"name": "포고핀 수", "spec": "PPM", "qty": 1, "unit_price": 999, "amount": 250000, "match": 2},
                        {"name": "포고핀 암", "spec": "PPF", "qty": 3, "unit_price": 200000, "amount": 600000, "match": None}],
              "supply_total": None, "vat_included": False, "doc_no": "PO-2026-1008"}
    pc.proposal_llm.chat = await fake_chat(answer)
    res = await pc.run(buf.getvalue(), "발주서.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", QUOTE)
    lines = res["lines"]
    check(res["status"] == "ok" and res["source"] == "text" and res["doc_no"] == "PO-2026-1008", "대조 결과 저장 형식", str(res)[:300])
    check(lines[0]["verdict"] == "일치", "AI 가 고른 짝(품명 달라도) — 수량·단가 같으면 일치", str(lines[0]))
    check(lines[1]["po"]["unit_price"] is None and lines[1]["verdict"] == "확인 필요",
          "원문에 없는 단가(999)는 지우고 '확인 필요'", str(lines[1]))
    check(lines[2]["po"] is not None and lines[2]["verdict"] == "다름" and "수량 다름(견적 4 · 발주 3)" in lines[2]["notes"],
          "AI 가 짝을 못 고른 줄은 코드(모델 코드 PPF)로 맞추고 수량 다름을 잡음", str(lines[2]))
    check(res["counts"]["다름"] == 1 and "확인이 필요합니다" in res["summary"], "요약: 다른 것이 있으면 확인 필요", res["summary"])

    # 부가세 포함 단가로 적힌 발주서 · 견적에 없는 품목 · 발주서에 빠진 견적 줄
    po = {"items": [{"name": "TCV1 Master", "spec": "", "qty": 1, "unit_price": 3_080_000, "amount": 3_080_000, "match": None},
                    {"name": "설치비", "spec": "", "qty": 1, "unit_price": 500_000, "amount": 500_000, "match": None}],
          "supply_total": None, "vat_included": True}
    r2 = pc.compare(QUOTE, po)
    v = {ln["verdict"] for ln in r2["lines"]}
    check(r2["lines"][0]["verdict"] == "일치" and "단가가 부가세 포함으로 적힘(공급가는 같음)" in r2["lines"][0]["notes"],
          "부가세 포함 단가(×1.1)는 같은 값으로 보고 메모", str(r2["lines"][0]))
    check({"발주서에 없음", "견적에 없음"} <= v and r2["counts"]["발주서에 없음"] == 2 and r2["counts"]["견적에 없음"] == 1,
          "견적에만 있는 줄·발주서에만 있는 줄 표시", str(r2["counts"]))
    check(r2["totals"]["same"] is False, "합계 다름", str(r2["totals"]))

    # 같은 품목 모두 일치
    r3 = pc.compare(QUOTE, {"items": [{"name": n, "spec": "", "qty": q, "unit_price": u, "amount": q * u, "match": i}
                                      for i, (n, q, u) in enumerate((("TCV1", 1, 2_800_000), ("PPM", 1, 250_000), ("PPF", 4, 200_000)), 1)],
                            "supply_total": None, "vat_included": False})
    check(pc.summary(r3) == "견적 품목 3개가 발주서와 모두 일치합니다." and r3["totals"]["same"] is True, "모두 일치 요약", pc.summary(r3))

    # 읽을 수 없는 형식 · 견적 없음
    r4 = await pc.run(b"x", "발주서.hwp", "application/x-hwp", QUOTE)
    check(r4["status"] == "unreadable" and "직접 확인" in r4["summary"], "한글 파일 → 직접 확인 안내", str(r4))
    r5 = await pc.run(b"x", "발주서.pdf", "application/pdf", [])
    check(r5["status"] == "no_quote", "견적 품목이 없으면 대조 안 함", str(r5))


asyncio.run(main())
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
