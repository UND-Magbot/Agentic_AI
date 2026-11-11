# -*- coding: utf-8 -*-
"""송금 확인 — 회사 지출내역 문서 ↔ 발주서 회차 금액 대조(사용자 2026-11-02).

지출내역 양식은 정해지지 않았다(회사가 뽑아 주는 엑셀·PDF·사진 등). 그래서 양식에 기대지 않고 '한 줄(행)' 단위로 본다.
- 엑셀(xlsx)·CSV: 행마다 셀을 읽는다. PDF·워드: 글자 줄. 스캔 PDF·사진: 사내 AI 가 지출 줄(날짜·거래처·금액·적요)을 뽑는다.
- 코드 판정(AI 아님): 그 줄에 공급사 이름이 있고 금액이 회차 금액(공급가액) 또는 부가세 포함 금액과 같으면(1,000원 차이까지) 일치.
  · 일치 → [송금 확인]  · 공급사 줄은 있는데 금액이 다름 / 금액은 맞는데 공급사가 없음 / 아무것도 없음 → 불일치(오류 보고).
  불일치여도 담당자가 사유를 남기고 넘어갈 수 있다(화면). 결과는 회차 기록에 남아 다시 열어도 보인다.
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import io
import json
import logging
import re
from typing import Any

from .. import proposal_llm
from ..config import settings
from ..sales_deals.po_compare import read_po
from .flow import PAY_TOLERANCE

_log = logging.getLogger("purchases.expense_check")

MAX_ROWS = 5_000
# 금액 — 쉼표 숫자(1,080,000) 또는 4자리 이상 숫자. 날짜·전화·계좌(2026-11-03, 2026.11.03, 616101-04-…)의 조각은 숫자로 보지 않는다
_NUM = re.compile(r"(?<![\d.\-/])\d{1,3}(?:,\d{3})+(?![\d,.\-/])|(?<![\d.,\-/])\d{4,}(?![\d,.\-/])")
_CORP = re.compile(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(재\)|co\.?,?\s*ltd\.?|inc\.?|corp\.?", re.I)

SYSTEM = """당신은 회사 지출내역(출금·송금 내역) 문서를 읽는 도우미입니다. 문서에 있는 지출 줄을 빠짐없이 뽑습니다.
규칙: 거래처(받는 사람)·적요·금액·날짜는 문서에 적힌 그대로. 금액은 숫자만(원 단위, 쉼표 없이). 없으면 null.
JSON 하나만 답한다: {"rows":[{"date":null,"payee":"","memo":"","amount":null}]}"""


def norm_name(s: str) -> str:
    """회사 이름 비교용 — (주)·㈜·주식회사·공백·기호를 뺀다. '㈜크레켄' == '주식회사 크레켄' == '크레켄'."""
    return re.sub(r"[^0-9a-z가-힣]", "", _CORP.sub("", str(s or "").lower()))


def _nums(text: str) -> list[float]:
    out = []
    for n in _NUM.findall(text or ""):
        try:
            out.append(abs(float(n.replace(",", ""))))
        except ValueError:
            pass
    return out


def _cell(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    return str(v).strip()


def read_rows(data: bytes, filename: str, mime: str) -> tuple[list[dict[str, Any]], list[bytes]]:
    """문서 → (줄 목록 [{text, amounts}], AI 로 읽을 그림). 줄의 amounts 는 그 줄의 숫자 셀(4자리 이상·쉼표 숫자)."""
    name = (filename or "").lower()
    rows: list[dict[str, Any]] = []
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = [v for v in row if v not in (None, "")]
                if not cells:
                    continue
                amounts = [abs(float(v)) for v in cells if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) >= 1000]
                txt = " | ".join(_cell(v) for v in cells)
                rows.append({"text": txt, "amounts": amounts + [n for n in _nums(" ".join(str(v) for v in cells if isinstance(v, str)))]})
                if len(rows) >= MAX_ROWS:
                    return rows, []
        return rows, []
    if name.endswith(".csv"):
        for enc in ("utf-8-sig", "cp949"):
            try:
                txt = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return [], []
        for rec in list(csv.reader(io.StringIO(txt)))[:MAX_ROWS]:
            line = " | ".join(c.strip() for c in rec if c.strip())
            if line:
                rows.append({"text": line, "amounts": _nums(line)})
        return rows, []
    text, images = read_po(data, filename, mime)
    if images:
        return [], images
    for line in text.splitlines()[:MAX_ROWS]:
        if line.strip():
            rows.append({"text": line.strip(), "amounts": _nums(line)})
    return rows, []


async def ai_rows(images: list[bytes]) -> list[dict[str, Any]]:
    """그림(스캔·사진) 지출내역 → 줄 목록. 그림이라 원문 숫자 확인은 못 한다(결과에 source='image' 로 표시)."""
    raw = await proposal_llm.chat([{"role": "system", "content": SYSTEM},
                                   {"role": "user", "content": "[지출내역]", "images": [base64.b64encode(b).decode() for b in images]}],
                                  fmt="json", think=False, num_predict=3000, temperature=0, model=settings.ollama_model)
    out = json.loads(raw)
    rows = []
    for r in out.get("rows") or []:
        if not isinstance(r, dict):
            continue
        try:
            amt = abs(float(str(r.get("amount")).replace(",", ""))) if r.get("amount") is not None else None
        except ValueError:
            amt = None
        txt = " | ".join(str(r.get(k) or "").strip() for k in ("date", "payee", "memo") if str(r.get(k) or "").strip())
        rows.append({"text": f"{txt} | {amt:,.0f}" if amt else txt, "amounts": [amt] if amt else []})
    return rows


def judge(rows: list[dict[str, Any]], supplier: str, amount: float, amount_vat: float) -> dict[str, Any]:
    """코드 판정 — 공급사 이름 + 금액(공급가액 또는 부가세 포함, PAY_TOLERANCE 차이까지)."""
    key = norm_name(supplier)

    def basis(n: float) -> str | None:
        if abs(n - amount) <= PAY_TOLERANCE:
            return "supply"
        if abs(n - amount_vat) <= PAY_TOLERANCE:
            return "vat"
        return None

    def has_supplier(txt: str) -> bool:
        """줄에 공급사가 있나 — 줄 안에 공급사 이름이 통째로 있거나, 셀 하나가 공급사 이름의 일부(3자 이상)
        (등록 이름 '㈜나우티앤에스 구미지점' ↔ 지출내역 '나우티앤에스')."""
        if not key:
            return False
        if key in norm_name(txt):
            return True
        return any(len(c) >= 3 and c in key for c in (norm_name(x) for x in txt.split("|")))

    named = [r for r in rows if has_supplier(r["text"])]
    hits = [{"text": r["text"][:300], "amount": n, "basis": b} for r in named for n in r["amounts"] if (b := basis(n))]
    exp = {"supply": amount, "vat": amount_vat}
    if hits:
        h = hits[0]
        return {"status": "ok", "match": h, "expected": exp,
                "summary": f"일치 — 지출내역 '{supplier}' {h['amount']:,.0f}원 = 발주 회차 "
                           + ("공급가액" if h["basis"] == "supply" else "부가세 포함 금액") + "."}
    amount_only = [{"text": r["text"][:300], "amount": n, "basis": b} for r in rows for n in r["amounts"] if (b := basis(n))]
    if named:
        seen = sorted({n for r in named for n in r["amounts"]})
        return {"status": "mismatch", "reason": "amount", "expected": exp, "found": [r["text"][:300] for r in named[:5]],
                "summary": f"불일치 — 지출내역에 '{supplier}' 줄은 있지만 금액이 다릅니다"
                           f"(지출내역 {', '.join(f'{n:,.0f}' for n in seen[:5]) or '금액 없음'}원 · "
                           f"발주 회차 {amount:,.0f}원 / 부가세 포함 {amount_vat:,.0f}원)."}
    if amount_only:
        return {"status": "mismatch", "reason": "supplier", "expected": exp, "found": [h["text"] for h in amount_only[:5]],
                "summary": f"불일치 — 금액 {amount_only[0]['amount']:,.0f}원 줄은 있지만 거래처가 '{supplier}'가 아닙니다."}
    return {"status": "mismatch", "reason": "none", "expected": exp, "found": [],
            "summary": f"불일치 — 지출내역에서 '{supplier}' 지출이나 회차 금액({amount:,.0f}원 / 부가세 포함 {amount_vat:,.0f}원)을 찾지 못했습니다."}


async def run(data: bytes, filename: str, mime: str, supplier: str, amount: float, amount_vat: float) -> dict[str, Any]:
    """지출내역 한 개 대조 → 저장할 결과(예외 없이 status 로 알림)."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    try:
        rows, images = read_rows(data, filename, mime)
    except Exception as e:  # 깨진 파일
        _log.warning("지출내역 읽기 실패 %s: %s", filename, e)
        rows, images = [], []
    source = "text"
    if images:
        source = "image"
        try:
            rows = await ai_rows(images)
        except (proposal_llm.ProposalLLMError, json.JSONDecodeError, ValueError) as e:
            _log.warning("지출내역 AI 판독 실패 %s: %s", filename, e)
            return {"status": "failed", "checked_at": now, "summary": "AI 판독에 실패했습니다. 잠시 뒤 다시 대조해 주세요."}
    if not rows:
        return {"status": "unreadable", "checked_at": now,
                "summary": "이 파일에서 지출 내용을 읽지 못했습니다(엑셀(xlsx)·CSV·PDF·사진·워드(docx)만). 내용을 확인해 주세요."}
    return {**judge(rows, supplier, amount, amount_vat), "checked_at": now, "source": source, "rows": len(rows)}
