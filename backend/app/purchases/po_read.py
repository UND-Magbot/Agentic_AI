# -*- coding: utf-8 -*-
"""고친 발주서 파일 읽기(사용자 2026-11-11 — 견적서 '고친 파일로 발행'과 같게).

회사에 내기 전에 엑셀에서 고친 발주서(또는 그 PDF)를 올리면 발주번호(Order No.)·발주일·품목을 읽어 확인 창에 채운다.
- 엑셀(xlsx): 회사 발주서 양식(po_xlsx)이면 칸 이름(Order No.·Commodity·Description·MOQ·Q'ty·Price)으로 바로 읽는다 — AI 없이 정확.
  예전 양식(사용자 2026-11-11 — LHTC·Autoxing 해외, 크레켄 국내)도 읽는다:
  · 'Commodity & Description' 한 칸 → 첫 줄이 모델명처럼 보이면(짧고 띄어쓰기 없음, 또는 다음 줄이 '- 규격'·'(…)') Commodity·Description 으로
    나누고, 아니면(긴 품명이 칸 폭에서 줄바꿈된 것 — 크레켄 '…커\n넥터') 통째로 Description.
  · 날짜·번호 칸 이름이 'Quotation Date / Quotation No.'(해외 양식이 견적서에서 옮긴 흔적)여도 읽는다.
  · 수량이 '1ea'·'3 pcs'·'2개'처럼 글이면 숫자만.
  · 머리 칸(공급사·Bank·Address·Swift Code·Account No.·Account Holder·Comments)도 읽어 발주서 머리에 채운다 —
    고친 파일로 발행한 뒤 [발주서 고치기]에서 그대로 고쳐 다시 발행할 수 있게.
- 양식이 아니거나 PDF·사진이면 견적서 읽기(quote_read)와 같은 사내 AI 판독 + 글자에서 발주번호·날짜를 찾는다.
  회사 양식 PDF(예전 판 포함 — 사용자 2026-11-11)는 머리 칸(Order No.·Order Date·공급사·Bank·Address·Swift Code·Account No.·
  Account Holder·Comments)을 글자 위치로 읽는다(PDF 글자 순서는 칸 순서와 달라 — 칸 이름 오른쪽·아래의 글자를 모은다).
결과는 확인 창에 채울 값일 뿐 — 담당자가 고친 값으로 발행한다(service.upload_po).
"""
from __future__ import annotations

import datetime as dt
import io
import re
from typing import Any

from . import quote_read

NO_RE = re.compile(r"\b((?:UND|PO)-[A-Z]{2,4}\d{8}-\d{2,})\b", re.I)
DATE_RE = re.compile(r"\b(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\b")
MAX_ROWS = 60


def _text(v: Any) -> str:
    return re.sub(r"[ \t]+\n", "\n", str(v)).strip() if v is not None else ""


QTY_UNIT = re.compile(r"^([\d.]+)(?:ea|pcs?|sets?|units?|개|대|세트|ea\.)?$", re.I)


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = re.sub(r"[₩$,\s]|US", "", str(v or ""))
    m = QTY_UNIT.match(t)                              # '1ea'·'3pcs'·'2개' — 예전 해외 양식은 수량을 글로 적었다
    try:
        return float(m.group(1)) if m else (float(t) if t else None)
    except ValueError:
        return None


MODEL = re.compile(r"^[A-Za-z0-9][\w./+#-]{1,39}$")


def split_merged(text: str) -> tuple[str, str]:
    """예전 'Commodity & Description' 한 칸 → (Commodity, Description). 첫 줄이 모델명(짧고 띄어쓰기 없음)이거나 다음 줄이
    '- 규격'·'(…)' 이면 첫 줄을 Commodity 로, 아니면 통째로 Description(칸 폭에서 줄바꿈된 긴 품명 — 단어 중간이 잘림)."""
    lines = [x.strip() for x in text.split("\n")]
    if len(lines) < 2:
        return (lines[0], "") if MODEL.match(lines[0] or "") or len(lines[0]) <= 40 else ("", lines[0])
    first, rest = lines[0], [x for x in lines[1:] if x]
    nxt = rest[0] if rest else ""
    if MODEL.match(first) or (len(first) <= 40 and nxt[:1] in ("-", "·", "(", "（", "[")):
        return first, "\n".join(x.strip(" -·\t") for x in rest if x.strip(" -·\t"))
    return "", "\n".join(x for x in lines if x)          # 원래 줄바꿈 그대로(고칠 사람이 보고 고침)


HEAD_LABELS = {"bank": "bank", "address": "address", "swift code": "swift", "account no": "account_no",
               "account holder": "account_holder", "comments": "comments"}


def _supplier(text: str) -> dict[str, str]:
    """Supplier 칸 글 → {name, contact, email, tel}. 'To : 담당 (메일)'·'Tel :'·'e-mail :' 줄을 알아보고, 나머지 줄은 담당자."""
    out = {"name": "", "contact": "", "email": "", "tel": ""}
    for i, line in enumerate(x.strip() for x in text.split("\n") if x.strip()):
        low = line.lower()
        val = line.split(":", 1)[1].strip() if ":" in line else line
        if i == 0:
            out["name"] = line
        elif low.startswith("tel"):
            out["tel"] = val
        elif low.startswith(("e-mail", "email", "mail")):
            out["email"] = val
        else:                                           # 'To : Echo (echo@x.com)' · '영업부 홍길동 과장'
            m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", val)
            if m and not out["email"]:
                out["email"] = m.group(0)
                val = re.sub(r"\(?\s*" + re.escape(m.group(0)) + r"\s*\)?", "", val).strip()
            if val and not out["contact"]:
                out["contact"] = val
    return out


def _header(ws, head_row: int) -> dict[str, Any]:
    """품목 머리 줄 위의 칸 이름(Supplier and Shipper by·Bank·…·Comments)으로 머리 값을 읽는다. 빈 값은 넣지 않는다."""
    out: dict[str, Any] = {}
    for r in range(1, head_row):
        for c in range(1, min(ws.max_column, 12) + 1):
            low = _text(ws.cell(r, c).value).lower().rstrip(" :.")
            if low.startswith("supplier and shipper"):
                below = next((ws.cell(r + 1, cc).value for cc in range(1, 4) if ws.cell(r + 1, cc).value not in (None, "")), None)
                sup = _supplier(_text(below)) if below else {}
                if any(sup.values()):
                    out["supplier"] = sup
            elif low in HEAD_LABELS and HEAD_LABELS[low] not in out:
                v = _right_of(ws, r, c)
                if isinstance(v, float) and v.is_integer():
                    v = int(v)
                t = re.sub(r"^swift\s*code\s*:\s*", "", _text(v), flags=re.I).strip()
                if t and t != "-":
                    out[HEAD_LABELS[low]] = t
    return out


def _date(v: Any) -> str | None:
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    m = DATE_RE.search(str(v or ""))
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _right_of(ws, row: int, col: int) -> Any:
    """칸 이름 오른쪽의 첫 값(병합 칸이면 병합 왼쪽 위 칸에 값이 있다)."""
    for c in range(col + 1, min(col + 6, ws.max_column + 1)):
        v = ws.cell(row, c).value
        if v not in (None, ""):
            return v
    return None


def read_template(data: bytes) -> dict[str, Any] | None:
    """회사 발주서 양식 엑셀 → {doc_no, date, items}. 양식이 아니면(품목 머리 줄을 못 찾으면) None."""
    from openpyxl import load_workbook

    try:
        ws = load_workbook(io.BytesIO(data), data_only=True).worksheets[0]
    except Exception:
        return None
    doc_no = date = None
    head = cols = None
    for r in range(1, min(ws.max_row, 80) + 1):
        for c in range(1, min(ws.max_column, 12) + 1):
            t = _text(ws.cell(r, c).value)
            low = t.lower()
            if low.startswith(("order no", "quotation no")) and doc_no is None:
                doc_no = _text(_right_of(ws, r, c)) or None
            elif low.startswith(("order date", "quotation date")) and date is None:
                date = _date(_right_of(ws, r, c))
            elif low.startswith("commodity") and head is None:
                head, cols = r, {"commodity": c, "merged": "description" in low}
        if head:
            break
    if not head:
        return None
    for c in range(1, min(ws.max_column, 12) + 1):
        low = _text(ws.cell(head, c).value).lower()
        if low.startswith("description"):
            cols["description"] = c
        elif low.startswith("moq"):
            cols["moq"] = c
        elif low.startswith("q'ty") or low.startswith("qty") or low.startswith("quantity"):
            cols["qty"] = c
        elif low.startswith("price") or low.startswith("unit price"):
            cols["price"] = c
        elif low.startswith("remark"):
            cols["remark"] = c
    if "qty" not in cols or "price" not in cols:
        return None
    items = []
    for r in range(head + 1, min(head + 1 + MAX_ROWS, ws.max_row + 1)):
        first = _text(ws.cell(r, cols["commodity"]).value)
        if first.upper().startswith("TOTAL"):
            break
        com, desc = first, _text(ws.cell(r, cols["description"]).value) if "description" in cols else ""
        if cols["merged"] and not desc and com:
            com, desc = split_merged(com)
        qty, price = _num(ws.cell(r, cols["qty"]).value), _num(ws.cell(r, cols["price"]).value)
        if not com and not desc and qty is None and price is None:
            continue
        items.append({"commodity": com.strip(), "description": desc.strip(),
                      "moq": _num(ws.cell(r, cols["moq"]).value) if "moq" in cols else None, "qty": qty, "price": price,
                      "remark": _text(ws.cell(r, cols["remark"]).value) if "remark" in cols else ""})
    return {"doc_no": doc_no, "date": date, "items": items, "header": _header(ws, head)}


_PDF_LABELS = {"bank": "bank", "address": "address", "swift code": "swift", "account no": "account_no",
               "account holder": "account_holder"}


def _pdf_lines(data: bytes) -> tuple[list[tuple[float, float, float, float, str]], list[tuple[float, float, float]]]:
    """PDF 첫 쪽의 글자 줄 [(x0, y0, x1, y1, 글자)] + 가로 칸 선 [(y, x0, x1)] — 칸 하나의 한 줄이 한 항목."""
    import pymupdf

    out, rules = [], []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        if not doc.page_count:
            return out, rules
        page = doc[0]
        for b in page.get_text("dict").get("blocks", []):
            for ln in b.get("lines", []):
                t = "".join(sp.get("text", "") for sp in ln.get("spans", [])).strip()
                if t:
                    x0, y0, x1, y1 = ln["bbox"]
                    out.append((x0, y0, x1, y1, t))
        for dr in page.get_drawings():                 # 표 테두리 — 선은 가로선만, 칸(사각형)은 위·아래 변
            for it in dr.get("items", []):
                if it[0] == "l" and abs(it[1].y - it[2].y) < 0.6 and abs(it[1].x - it[2].x) > 8:
                    rules.append((it[1].y, min(it[1].x, it[2].x), max(it[1].x, it[2].x)))
                elif it[0] == "re":
                    r = it[1]
                    if r.width > 8:
                        if r.height < 1.2:             # 얇은 사각형 = 선
                            rules.append(((r.y0 + r.y1) / 2, r.x0, r.x1))
                        else:
                            rules += [(r.y0, r.x0, r.x1), (r.y1, r.x0, r.x1)]
    return out, rules


def _cell_band(lb: tuple, rules: list[tuple[float, float, float]], fallback: tuple[float, float]) -> tuple[float, float]:
    """칸 이름이 든 칸의 위·아래 경계(칸 이름 가운데 x 를 지나는 가로선 중 바로 위·아래). 선이 없으면 fallback."""
    cx = (lb[0] + lb[2]) / 2
    ys = [y for y, x0, x1 in rules if x0 - 1 <= cx <= x1 + 1]
    above = [y for y in ys if y <= lb[1] + 1]
    below = [y for y in ys if y >= lb[3] - 1]
    return (max(above) if above else fallback[0], min(below) if below else fallback[1])


def read_pdf_header(data: bytes) -> dict[str, Any]:
    """회사 발주서 양식 PDF → {doc_no, date, header{supplier, bank, …, comments}}. 양식이 아니면(칸 이름이 없으면) 빈 dict."""
    try:
        lines, rules = _pdf_lines(data)
    except Exception:                                  # 깨진 PDF — AI 판독만
        return {}
    yc = lambda ln: (ln[1] + ln[3]) / 2                # noqa: E731
    key = lambda t: t.lower().strip().rstrip(" :.")    # noqa: E731
    lab: dict[str, tuple] = {}
    for ln in lines:
        k = key(ln[4])
        if k in ("supplier and shipper by", "issued the order by", "goods description & details", "comments",
                 "order no", "order date", "quotation no", "quotation date", *_PDF_LABELS) and k not in lab:
            lab[k] = ln
    # 좁은 칸 이름과 값이 한 줄로 붙어 나오는 경우('Account Holder Autoxing' — 국내 양식 PDF) — 그 칸 이름이 따로 없을 때만 나눈다
    # (해외 양식은 값 칸에 'Swift Code: …' 처럼 칸 이름이 다시 적혀 있어, 칸 이름이 따로 있으면 나누지 않는다)
    for k in (*_PDF_LABELS, "order no", "order date"):
        if k in lab:
            continue
        for i, ln in enumerate(lines):
            m = re.match(rf"^({re.escape(k)})\.?\s*:?\s+(\S.*)$", ln[4], re.I)
            if m and len(k.split()) > 1:                 # 한 단어 칸 이름(Bank·Address)은 'Bank of China' 같은 값과 헷갈려 빼고
                cut = ln[0] + (ln[2] - ln[0]) * len(m.group(1)) / len(ln[4])
                lab[k] = (ln[0], ln[1], cut, ln[3], m.group(1))
                lines[i] = (cut + 1, ln[1], ln[2], ln[3], m.group(2))
                break
    sup, issued = lab.get("supplier and shipper by"), lab.get("issued the order by")
    if not sup or not issued:
        return {}
    labels = {id(v) for v in lab.values()}
    free = [ln for ln in lines if id(ln) not in labels]
    out: dict[str, Any] = {}
    # 발주번호·발주일 — 같은 줄, 칸 이름 오른쪽(다음 칸 이름 앞까지)
    row_labels = sorted((v for k, v in lab.items() if k in ("order no", "order date", "quotation no", "quotation date")), key=lambda v: v[0])
    for i, lb in enumerate(row_labels):
        right = row_labels[i + 1][0] if i + 1 < len(row_labels) else 1e9
        vals = [ln[4] for ln in free if abs(yc(ln) - yc(lb)) < 6 and lb[2] - 1 <= ln[0] < right]
        if vals:
            k = key(lb[4])
            if k.endswith("no"):
                out["doc_no"] = vals[0].strip()
            else:
                out["date"] = _date(vals[0])
    head: dict[str, Any] = {}
    # 오른쪽 계좌 칸 — 칸 이름이 든 칸(표 가로선 사이)과 같은 높이의 오른쪽 글자. 선이 없으면 이웃 칸 이름의 가운데까지
    #   (칸 이름이 칸 위에 붙고 값이 칸 가운데에 오는 해외 양식에서 가운데 나누기는 값이 옆 칸으로 새어 — 2026-11-11 렌더 확인)
    right_labels = sorted((v for k, v in lab.items() if k in _PDF_LABELS), key=yc)
    left_edge = min((v[0] for v in right_labels), default=1e9)
    for i, lb in enumerate(right_labels):
        mid = ((yc(right_labels[i - 1]) + yc(lb)) / 2 if i else sup[3],
               (yc(lb) + yc(right_labels[i + 1])) / 2 if i + 1 < len(right_labels) else issued[1])
        top, bot = _cell_band(lb, rules, mid)
        vals = [ln for ln in free if ln[0] >= lb[2] - 1 and top - 0.5 <= yc(ln) < bot + 0.5]
        t = " ".join(ln[4] for ln in sorted(vals, key=lambda v: (v[1], v[0]))).strip()
        t = re.sub(r"^swift\s*code\s*:\s*", "", t, flags=re.I).strip()
        if t and t != "-":
            head[_PDF_LABELS[key(lb[4])]] = t
    # 공급사 — 'Supplier and Shipper by' 아래, 'Issued the order by' 위, 계좌 칸 이름 왼쪽
    blk = [ln for ln in free if sup[3] - 1 <= ln[1] and ln[3] <= issued[1] + 1 and ln[2] <= left_edge + 1]
    if blk:
        s = _supplier("\n".join(ln[4] for ln in sorted(blk, key=lambda v: (round(v[1]), v[0]))))
        if any(s.values()):
            head["supplier"] = s
    # Comments — 칸 이름 오른쪽, 발주처 칸 높이 안
    cm, goods = lab.get("comments"), lab.get("goods description & details")
    if cm:
        bottom = goods[1] if goods else 1e9
        vals = [ln for ln in free if ln[0] >= cm[2] - 1 and issued[3] - 1 <= ln[1] and ln[3] <= bottom]
        t = "\n".join(ln[4] for ln in sorted(vals, key=lambda v: (round(v[1]), v[0]))).strip()
        if t:
            head["comments"] = t
    if head:
        out["header"] = head
    return out


async def read(data: bytes, filename: str, mime: str) -> dict[str, Any]:
    """고친 발주서 한 개 → {status, summary, doc_no, date, items[{commodity, description, moq, qty, price}]}."""
    if filename.lower().endswith(".xlsx"):
        t = read_template(data)
        if t is not None:
            blanks = sum(1 for it in t["items"] for k in ("qty", "price") if it[k] is None)
            summary = (f"발주서 양식에서 품목 {len(t['items'])}개를 읽었습니다." if t["items"]
                       else "발주서 양식에서 품목을 찾지 못했습니다. 직접 적어 주세요.")
            if blanks:
                summary += f" 비어 있는 수량·단가 {blanks}칸을 채워 주세요."
            return {"status": "ok" if t["items"] else "empty", "summary": summary, "source": "template", **t}
    r = await quote_read.read(data, filename, mime)
    text = r.get("text") or ""
    m = NO_RE.search(text)
    # 회사 양식 PDF 면 머리 칸을 글자 위치로(번호·날짜도 칸 기준이 먼저 — 'UND-Lu20261013' 처럼 끝 번호 없는 예전 번호도)
    pos = read_pdf_header(data) if filename.lower().endswith(".pdf") or mime == "application/pdf" else {}
    doc_no = pos.get("doc_no") or (m.group(1).upper() if m else r.get("doc_no"))
    summary = str(r.get("summary") or "").replace("견적서에서", "발주서에서").replace("견적서 단가", "단가")
    return {"status": r.get("status"), "summary": summary, "source": "ai", "doc_no": doc_no,
            "date": pos.get("date") or _date(text), "items": r.get("items") or [], "header": pos.get("header") or {}}
