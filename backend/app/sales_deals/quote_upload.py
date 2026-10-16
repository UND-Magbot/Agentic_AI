# -*- coding: utf-8 -*-
"""밖에서 고친 견적서 파일 첨부(사용자 2026-10-12, 영업팀 요청).

AI 가 만든 견적서를 내려받아 엑셀에서 고쳤거나 프로그램 밖에서 만든 견적서를, 고객에게 실제로 나간 파일 그대로 영업 건에 남긴다.
- 받는 형식: 엑셀(xlsx)·PDF.
- 읽기: 발주서 대조와 같은 읽기(po_compare.read_po — PDF 글자·스캔 PDF 그림·엑셀 셀) + 사내 AI 가 품목·금액을 뽑는다.
  글자로 읽은 파일은 원문에 있는 숫자만 받는다(지어낸 숫자 차단). 담당자가 확인·수정한 품목으로 발행한다.
- 발행: 견적서 발행 한 번으로 본다 — 판 +1, 영업 건 견적(품목·금액) 갱신(product_recommend.quote_issue_upload).
  발행번호는 견적서에 적힌 번호를 따른다(사용자 2026-10-12: 담당자가 고치면서 번호도 정했을 것). 적힌 번호가 없을 때만 새 번호.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

from .. import proposal_llm
from ..company_knowledge.quote_session import said_numbers
from ..config import settings
from . import po_compare

_log = logging.getLogger("quote_upload")

TYPES = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".pdf": "application/pdf"}
MAX_BYTES = 20 * 1024 * 1024
MAX_ITEMS = 60

SYSTEM = """당신은 영업 담당자를 돕는 견적서 판독 도우미입니다. [견적서]에서 견적 품목을 빠짐없이 뽑습니다.
규칙:
- 품명은 품목(제품군) 이름, 규격은 모델·세부 사양. 둘이 한 칸에 있으면 첫 줄을 품명, 나머지를 규격으로.
- 숫자(수량·단가·금액)는 견적서에 적힌 값만. 없으면 null. 계산해서 채우지 않는다.
- 물류비·설치비처럼 금액이 있는 줄도 품목으로 넣는다. 합계·부가세·소계·NEGO 합계 줄은 품목이 아니다.
- supply_total: 공급가액(부가세 제외) 합계, vat_included: 단가가 부가세 포함으로 적혀 있으면 true.
JSON 하나만 답한다:
{"items":[{"name":"","spec":"","qty":null,"unit_price":null,"amount":null}],"supply_total":null,"vat_included":null,"doc_no":null}"""


def read_template(data: bytes, filename: str) -> dict[str, Any] | None:
    """회사 양식(우리가 만든 견적서 엑셀)을 고친 파일이면 칸 그대로 읽는다(AI 없음, 사용자 2026-10-15) — 아니면 None.
    _parsed = 작성 상태 되읽기(quote_readback.merge)에 쓰는 원본 — API 가 화면에 보내기 전에 뺀다."""
    from ..company_knowledge import quote_readback as rb

    if ext_of(filename) != ".xlsx":
        return None
    parsed = rb.parse(data)
    if parsed is None:
        return None
    items = rb.items_of(parsed)
    total = round(sum((x["qty"] or 0) * (x["unit_price"] or 0) for x in items), 2)
    nego = next((r["amount"] for r in parsed["rows"] if r["row"] == "nego" and r.get("amount")), None)
    if nego:                                                 # NEGO 는 음수 단가 한 줄로(영업 건 금액 = 견적서 Total)
        items.append({"name": "NEGO", "spec": "", "qty": 1, "unit_price": nego, "amount": nego})
    return {"status": "ok", "source": "template", "template": True, "items": items, "supply_total": round(total + (nego or 0), 2),
            "vat_included": False, "doc_no": parsed["quote_no"] or None, "_parsed": parsed,
            "summary": f"회사 견적서 양식이라 칸 그대로 읽었습니다 — 품목 {len([x for x in items if x['name'] != 'NEGO'])}줄"
                       + (", NEGO 포함" if nego else "") + "." + _missing_note(items)}


def _missing_note(items: list[dict[str, Any]]) -> str:
    """수량·단가를 못 읽은 줄 안내 — 그대로 두면 합계가 0원·모자란 금액이 된다(검증 13번 변형본, 2026-10-16)."""
    n = sum(1 for x in items if x["qty"] is None or x["unit_price"] is None)
    return f" 수량·단가를 읽지 못한 줄이 {n}개 있습니다 — 확인 창에서 채워 주세요." if n else ""


async def read(data: bytes, filename: str, mime: str) -> dict[str, Any]:
    """파일 → {status, items, supply_total, vat_included, doc_no, summary}. 읽을 수 없거나 AI 오류도 예외 없이 status 로."""
    tpl = read_template(data, filename)
    if tpl is not None:
        return tpl
    try:
        text, images = po_compare.read_po(data, filename, mime)
    except Exception as e:  # 깨진 파일
        _log.warning("견적서 읽기 실패 %s: %s", filename, e)
        text, images = "", []
    if not text.strip() and not images:
        return {"status": "unreadable", "items": [], "summary": "파일 내용을 읽지 못했습니다. 품목을 직접 적어 주세요."}
    body = "[견적서]\n" + (text[:po_compare.MAX_TEXT] if text else "(첨부 그림)")
    msg: dict[str, Any] = {"role": "user", "content": body}
    if images:
        msg["images"] = [base64.b64encode(b).decode() for b in images]
    try:
        raw = await proposal_llm.chat([{"role": "system", "content": SYSTEM}, msg], fmt="json", think=False,
                                      num_predict=3000, temperature=0, model=settings.ollama_model)
        out = json.loads(raw)
    except (proposal_llm.ProposalLLMError, ValueError) as e:
        _log.warning("견적서 판독 실패 %s: %s", filename, e)
        return {"status": "failed", "items": [], "summary": "AI 가 견적서를 읽지 못했습니다. 품목을 직접 적어 주세요."}
    seen = said_numbers(text) if text else None
    items = []
    for it in (out.get("items") or [])[:MAX_ITEMS]:
        if not isinstance(it, dict) or not str(it.get("name") or "").strip():
            continue
        row = {"name": str(it["name"]).strip()[:120], "spec": str(it.get("spec") or "").strip()[:120]}
        for k in ("qty", "unit_price", "amount"):
            row[k] = po_compare._checked(it.get(k), seen)
        items.append(row)
    supply = po_compare._checked(out.get("supply_total"), seen)
    vat = out.get("vat_included") if isinstance(out.get("vat_included"), bool) else None
    total = sum((x["qty"] or 0) * (x["unit_price"] or 0) for x in items)
    if not items:
        summary = "견적서에서 품목을 찾지 못했습니다. 품목을 직접 적어 주세요."
    elif supply is not None and abs(total - supply) > 1:
        summary = f"품목 {len(items)}줄을 읽었습니다 — 품목 합계 {total:,.0f}원이 견적서 공급가액 {supply:,.0f}원과 다릅니다. 확인해 주세요."
    else:
        summary = f"품목 {len(items)}줄을 읽었습니다. 값을 확인한 뒤 발행해 주세요." + _missing_note(items)
    return {"status": "ok", "source": "image" if images else "text", "items": items, "supply_total": supply,
            "vat_included": vat, "doc_no": po_compare._in_text(out.get("doc_no"), text), "summary": summary}


def clean_items(items: Any) -> list[dict[str, Any]]:
    """담당자가 확인한 품목 → 영업 건 품목({name, unit_price, qty}). 품명은 '품명\\n- 규격'(견적서 표기·거래명세서 나눔과 같게).
    틀리면 ValueError(무엇이 틀렸는지)."""
    if not isinstance(items, list) or not items:
        raise ValueError("품목을 한 줄 이상 적어 주세요.")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"품목은 {MAX_ITEMS}줄까지입니다.")
    out = []
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            raise ValueError(f"{i}번째 품목을 읽지 못했습니다.")
        name = re.sub(r"\s+", " ", str(it.get("name") or "")).strip()[:120]
        spec = re.sub(r"\s+", " ", str(it.get("spec") or "")).strip()[:120]
        try:
            qty = float(it.get("qty"))
            price = float(it.get("unit_price"))
        except (TypeError, ValueError):
            raise ValueError(f"{i}번째 품목({name or '이름 없음'})의 수량·단가를 숫자로 적어 주세요.") from None
        if not name:
            raise ValueError(f"{i}번째 품목의 품명을 적어 주세요.")
        nego = name.upper().startswith("NEGO")             # NEGO(할인) 한 줄은 음수 단가 — 견적서 발행과 같게(사용자 2026-10-15)
        if qty <= 0 or (price < 0 and not nego) or (nego and price >= 0):
            raise ValueError(f"{i}번째 품목({name})의 수량은 0보다, 단가는 0 이상이어야 합니다(NEGO 는 음수)." if not nego
                             else f"{i}번째 NEGO 줄의 단가는 할인 금액을 음수로 적어 주세요.")
        out.append({"name": f"{name}\n- {spec}" if spec else name, "qty": int(qty) if qty.is_integer() else qty,
                    "unit_price": int(price) if price.is_integer() else price})
    if sum(x["qty"] * x["unit_price"] for x in out) <= 0:     # 0원 견적 발행 막음(검증 QT-066, 2026-10-15)
        raise ValueError("합계가 0원 이하입니다 — 품목 단가·NEGO 를 확인해 주세요.")
    return out


def ext_of(filename: str) -> str:
    m = re.search(r"\.[A-Za-z0-9]+$", filename or "")
    return m.group(0).lower() if m else ""


# ── 올리기(API 공용) ─────────────────────────────────────────────────────────

from pydantic import BaseModel, Field  # noqa: E402


class UploadError(Exception):
    """화면에 그대로 보여 줄 올리기 오류(status = HTTP 코드)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class UploadRef(BaseModel):
    key: str = Field(min_length=1, max_length=300)
    filename: str = Field(default="", max_length=200)
    mime: str = Field(default="", max_length=120)
    size: int = Field(default=0, ge=0)


class UploadItem(BaseModel):
    name: str = Field(default="", max_length=120)
    spec: str = Field(default="", max_length=120)
    qty: float | None = Field(default=None, ge=0, le=1_000_000)
    unit_price: float | None = Field(default=None, gt=-1e11, lt=1e11)       # 음수는 NEGO 줄만(clean_items)


class UploadConfirmIn(BaseModel):
    """담당자가 확인한 품목으로 발행. doc_no = 견적서에 적힌 발행번호(AI 가 읽은 값, 고칠 수 있음) — 비우면 발행자 이니셜로 새 번호."""
    upload: UploadRef
    doc_no: str = Field(default="", max_length=40)
    initials: str = Field(default="", max_length=4)
    items: list[UploadItem] = Field(min_length=1, max_length=MAX_ITEMS)
    # 같은 영업 건에 같은 번호의 판이 있을 때, 덮어쓰기 확인을 받고 다시 보낸 요청(사용자 2026-10-13)
    overwrite: bool = False
    # 작성 상태도 올린 파일 내용으로 맞추기(사용자 2026-10-15) — 끄면 예전처럼 발행 기록만
    sync: bool = True


async def store_and_read(file, folder: str) -> dict[str, Any]:
    """올린 파일(UploadFile) → 저장소 {folder}{무작위}.{확장자} 에 두고 AI 로 읽는다. 반환 {upload, read}."""
    import io
    import uuid

    from ..storage import put_object

    name = (file.filename or "견적서").strip()[:200]
    ext = ext_of(name)
    if ext not in TYPES:
        raise UploadError(415, "견적서는 엑셀(xlsx)·PDF 파일만 올릴 수 있습니다.")
    data = await file.read()
    if not data:
        raise UploadError(400, "빈 파일입니다.")
    if len(data) > MAX_BYTES:
        raise UploadError(413, "견적서 파일은 20MB 까지 올릴 수 있습니다.")
    key = f"{folder}{uuid.uuid4().hex}{ext}"
    try:
        put_object(key=key, data=io.BytesIO(data), length=len(data), mime=TYPES[ext])
    except Exception as e:  # pragma: no cover — 저장소 장애
        raise UploadError(502, "파일 저장에 실패했습니다. 잠시 뒤 다시 시도해 주세요.") from e
    return {"upload": {"key": key, "filename": name, "mime": TYPES[ext], "size": len(data)},
            "read": await read(data, name, TYPES[ext])}
