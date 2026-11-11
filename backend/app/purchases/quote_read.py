# -*- coding: utf-8 -*-
"""공급사 견적서 → 발주서 초안(품목·공급사 담당자) — [견적서에서 불러오기](사용자 2026-11-02: 견적서 작성 기능처럼 발주서 작성).

- 읽기는 발주서 대조(sales_deals/po_compare.read_po)와 같다: PDF 글자(스캔본이면 그림)·엑셀 셀·워드·사진.
- 사내 AI 가 품목(품명·규격·MOQ·수량·단가)과 공급사 담당자를 뽑는다.
- 글자로 읽은 견적서는 원문에 있는 숫자만 받는다(지어낸 숫자 차단) — 없는 값은 비워 두고 담당자가 채운다.
결과는 화면 폼에 채울 값일 뿐 저장하지 않는다(담당자가 확인·수정 후 발행).
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from .. import proposal_llm
from ..company_knowledge.quote_session import said_numbers
from ..config import settings
from ..sales_deals.po_compare import MAX_TEXT, _checked, _in_text, read_po

_log = logging.getLogger("purchases.quote_read")

SYSTEM = """당신은 구매 담당자를 돕는 견적서 판독 도우미입니다. 공급사가 우리 회사(유엔디로보틱스)에 보낸 [견적서]에서
우리가 발주할 품목을 빠짐없이 뽑습니다.
규칙:
- 품명·규격은 견적서에 적힌 글자 그대로. 숫자(MOQ·수량·단가)도 견적서에 적힌 값만. 없으면 null. 계산해서 채우지 않는다.
- name 은 모델명·제품명(품목 칸의 첫 줄, 예: 'TC700'), spec 은 그 아래 설명 줄들(예: 'LTE Router', '전원 어답터 포함').
- moq 는 'MOQ'·'최소 주문 수량' 칸에 값이 적혀 있을 때만. 칸이 비었거나 없으면 null — 주문 수량(qty)을 moq 에 넣지 않는다.
- 단가는 부가세 제외(공급가) 단가. 견적서가 부가세 포함 단가만 적었으면 그 값을 그대로 쓰고 vat_included 를 true.
- 운송비·설치비처럼 금액이 있는 줄도 품목으로 넣는다. 합계·부가세·소계 줄은 품목이 아니다.
- supplier: 견적서를 보낸 회사(공급사) 이름, contact: 공급사 담당자 이름과 직함(예: '홍길동 과장'), email: 공급사 담당자 이메일.
  우리 회사(유엔디로보틱스·UND) 정보는 넣지 않는다. 모르면 빈 글자.
JSON 하나만 답한다:
{"items":[{"name":"","spec":"","moq":null,"qty":null,"unit_price":null}],
 "supplier":"","contact":"","email":"","vat_included":null,"doc_no":null}"""


def description(name: str, spec: str) -> str:
    """발주서 Description 칸 — 규격 줄들(품명은 Commodity 칸, 사용자 2026-11-11 에이딘로보틱스 예시: '200N 15Nm, CAN Protocol')."""
    lines = [s for s in (x.strip(" -·\t") for x in str(spec or "").replace(" / ", "\n").split("\n")) if s and s != name.strip()]
    return "\n".join(lines)


async def read(data: bytes, filename: str, mime: str) -> dict[str, Any]:
    """견적서 한 개 → {status, items[{commodity, description, moq, qty, price}], supplier{name,contact,email}, vat_included,
    summary, doc_no, text}. text = 읽은 글자(고친 발주서 읽기에서 번호·날짜 찾기에 씀)."""
    try:
        text, images = read_po(data, filename, mime)
    except Exception as e:  # 깨진 파일
        _log.warning("견적서 읽기 실패 %s: %s", filename, e)
        text, images = "", []
    if not text.strip() and not images:
        return {"status": "unreadable", "items": [],
                "summary": "이 파일 형식은 자동으로 읽지 못합니다(PDF·사진·엑셀(xlsx)·워드(docx)만). 품목을 직접 적어 주세요."}
    msg: dict[str, Any] = {"role": "user", "content": "[견적서]\n" + (text[:MAX_TEXT] if text else "(첨부 그림)")}
    if images:
        msg["images"] = [base64.b64encode(b).decode() for b in images]
    try:
        raw = await proposal_llm.chat([{"role": "system", "content": SYSTEM}, msg], fmt="json", think=False,
                                      num_predict=3000, temperature=0, model=settings.ollama_model)
        out = json.loads(raw)
    except (proposal_llm.ProposalLLMError, json.JSONDecodeError) as e:
        _log.warning("견적서 판독 실패 %s: %s", filename, e)
        return {"status": "failed", "items": [], "summary": "AI 판독에 실패했습니다. 잠시 뒤 다시 누르거나 품목을 직접 적어 주세요."}
    seen = said_numbers(text) if text else None
    items = []
    for it in out.get("items") or []:
        if not isinstance(it, dict) or not str(it.get("name") or "").strip():
            continue
        qty, moq = _checked(it.get("qty"), seen), _checked(it.get("moq"), seen)
        # 모델이 빈 MOQ 칸에 주문 수량을 그대로 넣는 경우가 있다(점검 2026-11-02) — 같으면 비운다(MOQ 는 참고 칸이라 비워도 무방)
        if moq is not None and moq == qty:
            moq = None
        items.append({"commodity": str(it["name"]).strip()[:120], "description": description(str(it["name"])[:120], str(it.get("spec") or "")[:300]),
                      "moq": moq, "qty": qty, "price": _checked(it.get("unit_price"), seen)})
    sup = {k: _in_text(out.get(k), text) or "" for k in ("supplier", "contact", "email")}
    blanks = sum(1 for it in items for k in ("qty", "price") if it[k] is None)
    summary = (f"견적서에서 품목 {len(items)}개를 읽었습니다." if items else "견적서에서 품목을 찾지 못했습니다. 직접 적어 주세요.")
    if blanks:
        summary += f" 견적서에서 확인되지 않은 수량·단가 {blanks}칸은 비워 두었습니다."
    if out.get("vat_included") is True:
        summary += " 견적서 단가가 부가세 포함으로 보입니다 — 발주서는 VAT 별도이니 단가를 확인해 주세요."
    return {"status": "ok" if items else "empty", "items": items,
            "supplier": {"name": sup["supplier"], "contact": sup["contact"], "email": sup["email"]},
            "vat_included": out.get("vat_included") if isinstance(out.get("vat_included"), bool) else None,
            "source": "image" if images else "text", "summary": summary,
            "doc_no": _in_text(out.get("doc_no"), text) or None, "text": text}
