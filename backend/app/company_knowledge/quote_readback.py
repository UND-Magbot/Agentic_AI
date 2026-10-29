"""올린 견적서 파일 → 견적 작성 상태(사용자 2026-10-15, 고객 지적).

AI 가 만든 견적서를 엑셀에서 고쳐 올려 발행(또는 같은 번호로 파일 교체)하면, 그 뒤 [견적서 고치기]·미리보기·AI 대화가
올린 파일 내용에서 이어지게 작성 상태(state)를 맞춘다. 예전에는 발행 기록만 바뀌고 작성 상태는 AI 가 만든 판 그대로였다.

- 회사 양식(quote_xlsx.build_xlsx 로 만든 엑셀)을 고친 파일은 칸 위치를 알므로 AI 없이 코드로 읽는다(parse).
  행을 넣거나 지워도 찾을 수 있게 고정 행 번호가 아니라 항목명(Customer·Comments·Commodity·Total…)으로 칸을 찾는다.
- 읽은 내용과 지금 작성 상태를 맞춰 새 작성 상태를 만든다(merge). 품목 줄은 지금 상태가 그리던 줄과 순서·글로 짝지어
  같은 줄이면 바뀐 칸만 고치고(줄 id·종류·사진 유지), 새 줄은 추가, 없어진 줄은 뺀다. 묶음·NEGO·머리 문구·주석도.
- 회사 양식이 아니거나 PDF 면 담당자가 확인한 품목·금액만 품목 줄에 맞춘다(머리 문구·묶음·NEGO 는 그대로).
- 엑셀에서만 한 서식(글꼴·색·병합·도형)은 작성 상태에 없는 것이라 여기서는 옮기지 않는다.
  Remark(G) 칸의 품목 사진은 줄마다 읽어(pic = 사진 지문, pics = 지문 → 바이트) 작성 상태의 품목 사진으로 잇는다
  (사용자 2026-10-29 — 저장은 product_recommend._take_photos).
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import io
import re
from typing import Any

from openpyxl.cell.rich_text import CellRichText

from . import product_prices as pp
from . import quote_session as qs
from . import quote_xlsx as qx

_GENERATED_KEYS = {"MASTER T.C", "T.P", *qx._DESC}      # 규격 글을 코드가 만드는 줄(본체·액세서리)
_UNIT_RE = re.compile(r'"\s*([^"]+?)\s*"')


_WHITE = {"FFFFFFFF", "00FFFFFF", "FFFFFF"}


def _hidden(run: Any) -> bool:
    """흰 글자로 칠해 숨긴 글 조각(엑셀에서 글 일부만 흰색으로 지운 것처럼 보이게 한 경우)."""
    color = getattr(getattr(run, "font", None), "color", None)
    return str(getattr(color, "rgb", "") or "").upper() in _WHITE


def _s(v: Any) -> str:
    if isinstance(v, CellRichText):                          # 글 조각마다 서식 — 보이는 조각만(사용자 2026-10-15, 고객 파일 CC)
        v = "".join(x if isinstance(x, str) else x.text for x in v if isinstance(x, str) or not _hidden(x))
    return "" if v is None else str(v).replace("\r\n", "\n").replace("\r", "\n").strip()


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = re.sub(r"[,\s₩$]", "", _s(v))
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)[A-Za-z가-힣]{0,4}", t)   # '3 ea'·'2SET'·'5개'처럼 글자로 적은 수량(사용자 2026-10-16)
    return float(m.group(1)) if m else None


def _same_num(a: Any, b: Any) -> bool:
    x, y = _num(a), _num(b)
    return x == y if x is None or y is None else abs(x - y) < 0.005


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip().lower()


def _sum_rows(formula: Any) -> set[int] | None:
    """Total 칸 수식(=SUM(F17:F20,F23)) → 더하는 행 번호. 수식이 아니면 None(알 수 없음)."""
    if not (isinstance(formula, str) and formula.startswith("=")):
        return None
    out: set[int] = set()
    for a, b in re.findall(r"\$?F\$?(\d+)(?::\$?F\$?(\d+))?", formula.upper()):
        out.update(range(int(a), int(b or a) + 1))
    return out


# ── 읽기 ─────────────────────────────────────────────────────────────────────

def photo_sig(data: bytes) -> str:
    """사진 지문 — 올린 파일의 사진과 작성 상태 사진이 같은지 볼 때."""
    return hashlib.sha1(data).hexdigest()


def _pics(ws) -> tuple[dict[int, str], dict[str, bytes]]:
    """Remark(G) 칸에 걸친 그림 → ({엑셀 행: 지문}, {지문: 바이트}). 한 행에 여럿이면 첫 그림."""
    at: dict[int, str] = {}
    pics: dict[str, bytes] = {}
    for img in getattr(ws, "_images", []):
        fr = getattr(img.anchor, "_from", None)
        if fr is None or fr.col != 6:
            continue
        try:
            raw = img._data()
        except Exception:  # noqa: BLE001 — 깨진 그림은 없는 것으로
            continue
        sig = photo_sig(raw)
        at.setdefault(fr.row + 1, sig)
        pics[sig] = raw
    return at, pics


def parse(data: bytes) -> dict[str, Any] | None:
    """회사 양식 견적서 엑셀 → {lang, quote_no, form, rows, nego, pics}. 회사 양식이 아니면 None.
    rows: [{row: item|option|section|subtotal|gap|spec|text, ...}] — 표 머리 아래 ~ Total 위. 품목 줄의 pic = Remark 칸 사진 지문."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), rich_text=True)       # 흰 글자로 숨긴 조각을 가려내려고(_s)
        wv = load_workbook(io.BytesIO(data), data_only=True)        # 수식 칸의 저장된 값
    except Exception:
        return None
    ws, vs = wb.worksheets[0], wv.worksheets[0]
    merged = {(m.min_row, m.min_col): m for m in ws.merged_cells.ranges}
    pic_at, pics = _pics(ws)

    def val(r: int, c: int) -> Any:
        v = ws.cell(r, c).value
        return vs.cell(r, c).value if isinstance(v, str) and v.startswith("=") else v

    def span(r: int, c: int) -> int:
        m = merged.get((r, c))
        return m.max_col if m else c

    def find(col: int, labels: set[str], start: int = 1) -> int | None:
        for r in range(start, ws.max_row + 1):
            if _s(ws.cell(r, col).value) in labels:
                return r
        return None

    head = next((r for r in range(1, ws.max_row + 1)
                 if _s(ws.cell(r, 1).value) == "Commodity" and _s(ws.cell(r, 4).value) == "Quantity"), None)
    if head is None:
        return None
    total = next((r for r in range(head + 1, ws.max_row + 1) if _s(ws.cell(r, 1).value).startswith("Total")), None)
    if total is None:
        return None
    lang = "en" if _s(ws.cell(total, 1).value).startswith(pp.QUOTE_LABELS["en"]["total"]) else "ko"
    lab = pp.QUOTE_LABELS[lang]

    form: dict[str, Any] = {}
    # 칸 위치 — 올린 파일 위에서 이어서 고칠 때(quote_patch) 어느 칸을 고칠지
    pos: dict[str, Any] = {"head": head, "total": total, "by": find(1, {f" {lab['by']}", lab["by"]}),
                           "date": find(1, {lab["date"]}), "no": find(4, {lab["no"]})}
    r = find(1, {lab["customer"]})
    pos["customer"] = r + 1 if r else None
    if r:
        cust = _s(ws.cell(r + 1, 1).value).split("\n")
        form["customer"] = cust[0].strip() if cust else ""
        for x in cust[1:]:
            k, _, v = x.strip().partition(":")
            if k.strip() in ("To", "CC"):
                form["to" if k.strip() == "To" else "cc"] = v.strip().rstrip(",").strip()
    r = find(4, {lab["comments"]})
    pos["comments"] = r
    if r:
        form["comments"] = _s(ws.cell(r, 5).value)
    for label, key in (("Bank", "bank"), ("Account No.", "account_no"), ("Account Holder", "account_holder")):
        r = find(4, {label})
        if r and r < head:
            form[key] = _s(ws.cell(r, 5).value)
            pos[key] = r

    rows: list[dict[str, Any]] = []
    nums: list[int] = []                                     # rows 와 같은 순서의 엑셀 행 번호
    # 대여 양식(사용자 2026-10-22) — 비고 칸이 월 납입금 =ROUNDUP(F30/36,-3), 총액 SUM 은 금액 칸
    rent = re.match(r"=ROUNDUP\(\$?F\$?\d+/(\d+)", str(ws.cell(total, 7).value or "").upper().replace(" ", ""))
    in_sum = _sum_rows(ws.cell(total, 6 if rent else 7).value)   # Total 에 더하는 행 — 금액은 있어도 빠진 줄 = 미반영 옵션
    for r in range(head + 1, total):
        nums.append(r)
        a_end = span(r, 1)
        a = _s(ws.cell(r, 1).value)
        if a_end >= 7:                                       # 한 줄 전체를 합친 칸 — 구분줄·소제목·문구
            if not a:
                rows.append({"row": "gap"})
            elif (ws.cell(r, 1).alignment.horizontal or "") == "left":
                rows.append({"row": "text", "text": a})
            else:
                rows.append({"row": "section", "text": a})
            continue
        if a_end == 5:                                       # A:E 합침 — 소계 또는 NEGO
            amt = _num(val(r, 6))
            if a.upper().startswith("NEGO"):
                rows.append({"row": "nego", "desc": a, "amount": amt, "remark": _s(ws.cell(r, 7).value)})
            else:
                rows.append({"row": "subtotal", "text": a})
            continue
        if a_end == 2 and span(r, 3) >= 6:                   # 항목명 A:B / 내용 C:F — 사양 줄
            rows.append({"row": "spec", "label": a, "value": _s(ws.cell(r, 3).value)})
            continue
        desc = _s(ws.cell(r, 2).value)
        qty, price = _num(val(r, 4)), _num(val(r, 5))
        if not (a or desc or qty is not None or price is not None):
            nums.pop()
            continue                                         # 빈 줄(엑셀에서 넣고 비워 둔 행)
        m = _UNIT_RE.search(ws.cell(r, 4).number_format or "")
        unit = m.group(1) if m else ""
        f_raw = ws.cell(r, 6).value
        # 공급가액 칸이 빈 줄 = 예전 옵션, 금액은 있는데 Total 에 안 더하는 줄 = 미반영 옵션(사용자 2026-10-19)
        flag = {} if f_raw in (None, "") or in_sum is None or r in in_sum else {"in_total": False}
        rows.append({"row": "option" if f_raw in (None, "") or flag else "item", **flag, "commodity": a, "desc": desc, "qty": qty,
                     "unit": "" if unit.lower() in ("ea", "") else unit, "unit_price": price,
                     "remark": _s(ws.cell(r, 7).value), **({"pic": pic_at[r]} if r in pic_at else {})})

    t = _s(ws.cell(total, 1).value).split("\n", 1)
    form["total_note"] = t[1].strip() if len(t) > 1 else ""
    if lang == "ko":                                          # 'Total (부가세 포함)' — 부가세 포함 견적서(사용자 2026-10-28)
        form["vat"] = "included" if "포함" in t[0] else "excluded"
    if rent:                                                  # 대여 견적서 — 월 납입금 식의 개월 수
        form.update(purpose="rental", rental_months=int(rent[1]))
    elif re.search(r"월 납입금|Monthly", _s(ws.cell(head, 5).value)):   # 식은 지웠어도 단가 칸 머리가 월 납입금이면 대여(개월 수 모름)
        form["purpose"] = "rental"
    else:
        form["purpose"] = "sale"
    r = find(1, {lab["delivery"]}, total)
    pos["delivery"] = r
    if r:
        form["delivery"] = _s(ws.cell(r, 2).value)
        form["place"] = _s(ws.cell(r, 7).value)
    r = find(1, {lab["validity"]}, total)
    pos["validity"] = r
    if r:
        form["payment"] = _s(ws.cell(r, 7).value)
    r = find(1, {lab["note"]}, total)
    pos["note"] = r
    if r:
        notes = [x.strip() for x in _s(ws.cell(r, 2).value).split("\n") if x.strip()]
        form["notes"] = notes
    order = find(1, {lab["order"]}, total)
    pos["order"], pos["foot"] = order, []
    if order:
        fixed = _s((pp.QUOTE_FIXED_TERMS_EN if lang == "en" else pp.QUOTE_FIXED_TERMS)["option_note"])
        foot = []
        for rr in range(order + 1, ws.max_row + 1):
            if span(rr, 1) < 7:
                break
            x = _s(ws.cell(rr, 1).value)
            pos["foot"].append(rr)
            if x and x != fixed:
                foot.append(re.sub(r"^\*\s*", "", x))
        form["foot_notes"] = foot
    return {"lang": lang, "quote_no": _s(ws.cell(pos["no"], 5).value) if pos["no"] else "", "form": form, "rows": rows,
            "rownums": nums, "pos": pos, "pics": {k: v for k, v in pics.items() if k in {x.get("pic") for x in rows}}}


def items_of(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """읽은 견적서의 품목 줄(옵션 제외) → 올리기 확인 창의 품목 {name, spec, qty, unit_price, amount}. NEGO 는 음수 단가 한 줄."""
    out = []
    for r in parsed["rows"]:
        if r["row"] == "item":
            first, _, rest = r["desc"].partition("\n")
            out.append({"name": r["commodity"] or first, "spec": (rest if not r["commodity"] else r["desc"]).replace("\n", " ")[:120],
                        "qty": r["qty"], "unit_price": r["unit_price"],
                        "amount": round((r["qty"] or 0) * (r["unit_price"] or 0), 2)})
    return out


# ── 맞추기 ───────────────────────────────────────────────────────────────────

def _to_supply(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """부가세 포함 견적서에서 읽은 단가·NEGO 금액 → 공급가액(÷ 1.1, 원 단위). 작성 상태는 늘 공급가액이다."""
    out = []
    for r in rows:
        r = dict(r)
        for k in ("unit_price", "amount") if r.get("row") == "nego" else ("unit_price",):
            if isinstance(r.get(k), (int, float)):
                r[k] = round(r[k] / 1.1)
        out.append(r)
    return out


def _render(state: dict[str, Any]) -> list[dict[str, Any]]:
    """지금 상태가 그리는 견적서 줄(품목 줄엔 상태 줄 id). 단가가 비어 그릴 수 없는 줄은 단가 0 으로 본다."""
    st = copy.deepcopy(state)
    f = st["form"]
    f["vat"] = "excluded"          # 공급가액으로 그려 짝짓는다 — 올린 파일 금액도 공급가액으로 바꿔 비교(_to_supply, 사용자 2026-10-28)
    en = f.get("lang") == "en"
    if en and not f.get("fx_rate"):                          # 환율 없는 해외 견적 — 원화로만 있는 물류비·인건비 줄은 그릴 수 없다
        st["lines"] = [ln for ln in st["lines"] if ln.get("kind") not in ("work", "freight")]
    for ln in st["lines"]:
        if ln.get("kind") != "note" and not ln.get("unit_price") and not ln.get("usd"):
            ln["usd" if en else "unit_price"] = 0.0001
    try:
        return qs.render_lines(st, f.get("lang") or "ko", f.get("fx_rate"))
    except (ValueError, TypeError, KeyError):
        return []


def _pairs(old: list[str], new: list[str], similar: float = 0.45) -> list[tuple[int | None, int | None]]:
    """두 줄 목록 짝짓기 — 같은 글은 그대로, 바뀐 구간에선 글이 비슷한 줄끼리(순서 유지) 수정으로 보고, 나머지는 추가·삭제.
    similar=0 이면 바뀐 구간을 앞에서부터 하나씩 짝짓는다(표기가 제각각인 AI 판독 품목)."""
    out: list[tuple[int | None, int | None]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if op == "equal":
            out += [(i1 + k, j1 + k) for k in range(i2 - i1)]
        elif op == "replace":
            last = j1 - 1
            for i in range(i1, i2):
                rest = range(last + 1, j2)
                if not rest:
                    out.append((i, None))
                    continue
                if similar == 0:
                    score, j = 1.0, rest[0]
                else:
                    score, j = max((difflib.SequenceMatcher(a=old[i], b=new[k]).ratio(), k) for k in rest)
                if score >= similar:
                    out += [(None, k) for k in range(last + 1, j)]        # 건너뛴 새 줄은 추가
                    out.append((i, j))
                    last = j
                else:
                    out.append((i, None))
            out += [(None, k) for k in range(last + 1, j2)]
        elif op == "delete":
            out += [(i, None) for i in range(i1, i2)]
        else:
            out += [(None, j) for j in range(j1, j2)]
    return out


def _sections(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """읽은 줄 → 묶음 [{type, title, subtotal, rows}] + NEGO 줄. 회색 구분줄로 나눈다."""
    nego = next((r for r in rows if r["row"] == "nego"), None)
    groups: list[list[dict[str, Any]]] = [[]]
    for r in rows:
        if r["row"] == "gap":
            groups.append([])
        elif r["row"] != "nego":
            groups[-1].append(r)
    out = []
    for g in [g for g in groups if g]:
        title = g[0]["text"] if g[0]["row"] == "section" else ""
        body = g[1:] if title else g
        kinds = {r["row"] for r in body}
        if title == qs.OPTION_TITLE or (kinds and kinds <= {"option"}) or any(r.get("in_total") is False for r in body):
            typ = "option"
            # 옵션 묶음에서 Total 에 더하는 줄 = 반영 옵션
            body = [{**r, "row": "option", "in_total": True} if r["row"] == "item" else r for r in body]
        elif kinds and kinds <= {"spec"}:
            typ = "spec"
        elif kinds and kinds <= {"text"} and not title:
            typ = "text"
        else:
            typ = "items"
        out.append({"type": typ, "title": "" if typ == "option" and title == qs.OPTION_TITLE else title,
                    "subtotal": any(r["row"] == "subtotal" for r in body), "rows": [r for r in body if r["row"] != "subtotal"]})
    return out, nego


def _label(ln: dict[str, Any]) -> str:
    return (ln.get("name") or ln.get("key") or "").split("\n")[0][:30]


def merge(state: dict[str, Any], parsed: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """작성 상태 + 읽은 견적서 → (새 작성 상태, 바뀐 점, 이어지지 않는 점). 원래 state 는 바꾸지 않는다."""
    st = copy.deepcopy(state)
    f = st["form"]
    lang = f.get("lang") or "ko"
    changes: list[str] = []
    warns: list[str] = []
    if parsed["lang"] != lang:
        return st, [], [f"올린 견적서가 {'해외(영문)' if parsed['lang'] == 'en' else '국내'} 양식이라 작성 상태는 바꾸지 않았습니다."]
    en = lang == "en"
    if not en and parsed["form"].get("vat") == "included":
        parsed = {**parsed, "rows": _to_supply(parsed["rows"])}
    rendered = _render(state)
    by_id = {ln["id"]: ln for ln in st["lines"]}

    # 묶음 — 지금 그리는 묶음(내용 있는 것)과 순서·종류로 짝짓는다. 첫 품목 묶음은 늘 기본 묶음(B0).
    secs, nego_row = _sections(parsed["rows"])
    old_blocks = qs.blocks(st)
    shown = [b for b in old_blocks if any(r.get("block") == b["id"] for r in rendered if not r.get("row") or r.get("row") == "option")
             or (b["type"] in ("spec", "text") and (b.get("rows") or b.get("text")))]
    used, new_blocks, sec_block = set(), [], []
    nid = 1 + max([int(b["id"][1:]) for b in old_blocks if re.fullmatch(r"B\d+", b["id"])] or [0])
    for i, sec in enumerate(secs):
        if i == 0 and sec["type"] == "items":
            b = next(b for b in old_blocks if b["id"] == qs.MAIN_BLOCK)
        else:
            b = next((x for x in shown if x["id"] not in used and x["type"] == sec["type"] and x["id"] != qs.MAIN_BLOCK), None)
        if b is None:
            b = {"id": f"B{nid}", "type": sec["type"]}
            nid += 1
            changes.append(f"새 묶음: {sec['title'] or {'option': '< OPTION >', 'spec': '사양', 'text': '문구'}.get(sec['type'], '품목')}")
        used.add(b["id"])
        nb = {"id": b["id"], "type": sec["type"], "title": sec["title"]}
        if sec["type"] == "option" and (b.get("title") or qs.OPTION_TITLE) == (sec["title"] or qs.OPTION_TITLE):
            nb["title"] = b.get("title") or ""             # '< OPTION >' 와 빈 제목은 같은 제목(견적서엔 둘 다 < OPTION >)
        if sec["type"] == "items":
            nb["subtotal"] = bool(b.get("subtotal"))
        if sec["type"] == "spec":
            nb["rows"] = [{"label": r["label"][:40], "value": qs.clean_spec(r["value"])} for r in sec["rows"] if r["row"] == "spec"]
        if sec["type"] == "text":
            nb["text"] = qs.clean_spec("\n".join(r["text"] for r in sec["rows"]))
        if (b.get("title") or "") != nb["title"] and b in old_blocks:
            changes.append(f"묶음 제목: {b.get('title') or '(없음)'} → {nb['title'] or '(없음)'}")
        if sec["type"] in ("spec", "text") and b in old_blocks and (b.get("rows"), b.get("text")) != (nb.get("rows"), nb.get("text")):
            changes.append(f"{'사양' if sec['type'] == 'spec' else '문구'} 묶음 내용 변경")
        new_blocks.append(nb)
        sec_block.append(nb["id"])
    if not any(b["id"] == qs.MAIN_BLOCK for b in new_blocks):
        new_blocks.insert(0, {"id": qs.MAIN_BLOCK, "type": "items", "title": "", "subtotal": False})
    gone_blocks = [b for b in shown if b["id"] not in used]
    for b in gone_blocks:
        changes.append(f"빠진 묶음: {b.get('title') or b['id']}")

    # 품목 줄 — 읽은 줄(묶음 정보 포함) ↔ 지금 그리는 줄
    new_rows = [(sec_block[i], r) for i, sec in enumerate(secs) for r in sec["rows"] if r["row"] in ("item", "option", "spec")
                and secs[i]["type"] in ("items", "option")]
    old_rows = [r for r in rendered if (not r.get("row") or r.get("row") in ("option", "spec")) and (r.get("id") or r.get("note_id"))]
    key = lambda r: _norm(r.get("desc") if r.get("row") != "spec" else f"{r.get('label')}|{r.get('value')}")  # noqa: E731
    keep: list[dict[str, Any]] = []
    for oi, ni in _pairs([key(r) for r in old_rows], [key(r) for _, r in new_rows]):
        if ni is None:
            o = old_rows[oi]
            ln = by_id.get(o.get("id") or o.get("note_id"))
            if ln and ln["kind"] in ("work", "freight"):
                warns.append(f"{'물류비' if ln['kind'] == 'freight' else '설치 인건비'} 줄을 지운 것은 이어지지 않습니다 — 납품 방식에서 바꿔 주세요.")
                keep.append(ln)
            elif ln:
                changes.append(f"빠진 품목: {_label(ln)}")
            continue
        blk, n = new_rows[ni]
        if oi is None:
            keep.append(_with_pic(_new_line(st, n, blk, en, changes), n, changes))
            continue
        o = old_rows[oi]
        ln = by_id[o.get("id") or o.get("note_id")]
        keep.append(_with_pic(_update_line(st, ln, o, n, blk, en, changes, warns), n, changes))
    # 그리지 못해 짝짓기에 안 들어간 물류비·인건비 줄(환율 없는 해외)은 그대로 둔다 — 납품 방식이 다시 만든다
    seen = {o.get("id") or o.get("note_id") for o in old_rows}
    keep += [ln for ln in st["lines"] if ln["kind"] in ("work", "freight") and ln["id"] not in seen]
    st["lines"] = [ln for ln in keep if ln["kind"] not in ("work", "freight")] + [ln for ln in keep if ln["kind"] in ("work", "freight")]
    st["blocks"] = new_blocks
    auto = st.pop("_auto_check", [])
    if auto:
        now = {_norm(r.get("desc")): r for r in _render(st) if r.get("kind") in ("work", "freight")}
        for n in auto:
            r = now.get(_norm(n["desc"]))
            if r is None or _norm(r.get("remark") or "") != _norm(n["remark"]):
                warns.append(f"{n['desc'][:20]} 줄의 이름·비고 변경은 이어지지 않습니다(납품 방식이 다시 씁니다).")

    # NEGO
    old_nego = st.get("nego")
    if nego_row and nego_row.get("amount"):
        cut = abs(nego_row["amount"])
        m = re.fullmatch(r"NEGO\s*\((\d+(?:\.\d+)?)%\)", nego_row["desc"].strip(), re.I)
        lab = re.fullmatch(r"NEGO\s*\((.+)\)", nego_row["desc"].strip(), re.I)
        new_nego = {"mode": "percent", "value": float(m.group(1)), "label": ""} if m else \
            {"mode": "amount", "value": cut, "label": (lab.group(1).strip() if lab else "")[:40]}
        st["nego"] = new_nego
        if m and not _same_num(qs.nego_amount(st, sum(r["amount"] for r in _render(st) if not r.get("row") or r.get("in_total"))), cut):
            st["nego"] = {"mode": "amount", "value": cut, "label": ""}
        if (old_nego or {}) != st["nego"]:
            changes.append(f"NEGO: {qs.nego_label(state) if old_nego else '없음'} → {qs.nego_label(st)} {cut:,.2f}".rstrip("0").rstrip("."))
    elif old_nego:
        st["nego"] = None
        changes.append("NEGO 삭제")

    # 머리 문구·조건·주석
    pf = parsed["form"]
    d = qs.term_defaults(state, lang)
    shown_form = {k: _s(f.get(k)) or d.get(k, "") for k in ("comments", "delivery", "place", "payment")}
    labels = {k: v[0] for k, v in qs.FORM_FIELDS.items()}
    for k in ("customer", "to", "cc", "comments", "delivery", "place", "payment", "total_note", "bank", "account_no", "account_holder"):
        if k not in pf:
            continue
        now = shown_form.get(k, _s(f.get(k)))
        if _norm(pf[k]) != _norm(now):
            f[k] = qs.clean_comments(pf[k]) if k == "comments" else pf[k]
            changes.append(f"{labels[k]}: {now[:20] or '(없음)'} → {pf[k][:20] or '(없음)'}")
    if pf.get("vat") and pf["vat"] != (f.get("vat") or "excluded"):
        changes.append(f"부가세: {qs.VAT_MODES[f.get('vat') or 'excluded']} → {qs.VAT_MODES[pf['vat']]}")
        f["vat"] = pf["vat"]
    if pf.get("rental_months") and (f.get("purpose") != "rental" or f.get("rental_months") != pf["rental_months"]):
        changes.append(f"대여 개월 수: {f.get('rental_months') or '(없음)'} → {pf['rental_months']}")
        f.update(purpose="rental", rental_months=pf["rental_months"])
    if "notes" in pf:
        scope = qx._SCOPE[lang]
        pogo = any(ln.get("key") in ("PPM", "PPF") for ln in st["lines"])
        note = "\n".join(x for x in pf["notes"] if not (pogo and x == scope))
        if _norm(note) != _norm(_s(f.get("note"))):
            f["note"] = note
            changes.append(f"비고: {_s(state['form'].get('note'))[:20] or '(없음)'} → {note[:20] or '(없음)'}")
    bare = lambda xs: [re.sub(r"^\*\s*", "", x) for x in qs.clean_notes(xs)]     # noqa: E731 — 견적서엔 '* ' 를 붙여 찍는다
    if "foot_notes" in pf and bare(pf["foot_notes"]) != bare(f.get("foot_notes")):
        f["foot_notes"] = qs.clean_notes(pf["foot_notes"])
        changes.append(f"표 아래 주석 {len(f['foot_notes'])}개로 변경")
    return st, changes, warns


def _with_pic(ln: dict[str, Any], n: dict[str, Any], changes: list[str]) -> dict[str, Any]:
    """품목 줄에 올린 파일의 사진 지문을 달아 둔다(_pic, 없으면 None = 사진 뺌) — 저장소에 옮기는 것은 부르는 쪽.
    서술형·물류비·인건비 줄은 사진을 쓰지 않는다."""
    if n["row"] == "spec" or ln.get("kind") in ("note", "work", "freight") or not (n.get("pic") or ln.get("photo")):
        return ln
    ln["_pic"] = n.get("pic")
    if n.get("pic") and not ln.get("photo"):
        changes.append(f"{_label(ln)} 사진 추가")
    elif not n.get("pic") and ln.get("photo"):
        changes.append(f"{_label(ln)} 사진 삭제")
    return ln


def _new_line(st: dict[str, Any], n: dict[str, Any], blk: str, en: bool, changes: list[str]) -> dict[str, Any]:
    if n["row"] == "spec":
        ln = {"id": qs._new_id(st), "kind": "note", "key": "note", "commodity": n["label"][:60], "name": qs.clean_spec(n["value"]),
              "qty": None, "unit_price": None, "remark": "", "block": blk}
        changes.append(f"새 서술형 줄: {n['label'][:20]}")
        return ln
    qty = n["qty"] or 1
    ln = {"id": qs._new_id(st), "kind": "extra", "key": "extra", "name": qs.clean_spec(n["desc"]) or n["commodity"] or "항목",
          "commodity": n["commodity"][:60], "qty": int(round(qty)), "remark": n["remark"][:100], "block": blk,
          "price_status": "set", "unit_price": None if en else n["unit_price"], **({"in_total": True} if n.get("in_total") else {})}
    if en:
        ln["usd"] = n["unit_price"]
    if n["unit"]:
        ln["unit"] = qs.clean_unit(qs._UNIT_SINGULAR.get(n["unit"], n["unit"]))
    changes.append(f"새 품목: {_label(ln)} {ln['qty']} × {n['unit_price'] or 0:,.0f}")
    return ln


def _update_line(st: dict[str, Any], ln: dict[str, Any], o: dict[str, Any], n: dict[str, Any], blk: str, en: bool,
                 changes: list[str], warns: list[str]) -> dict[str, Any]:
    """짝지은 줄 — 바뀐 칸만 상태 줄에 옮긴다. 물류비·인건비는 금액·수량만(납품 방식 값으로)."""
    name = _label(ln)
    if n["row"] == "spec":
        if ln["kind"] == "note":
            ln["commodity"], ln["name"], ln["block"] = n["label"][:60], qs.clean_spec(n["value"]), blk
        return ln
    if ln["kind"] in ("work", "freight"):
        fx = st["form"].get("fx_rate")
        krw = n["unit_price"] if not en else (round(n["unit_price"] * float(fx)) if fx and n["unit_price"] else None)
        if not _same_num(n["unit_price"], o["unit_price"]):
            if krw is None:
                warns.append(f"{name} 단가 변경은 환율이 없어 이어지지 않습니다.")
            elif ln["kind"] == "freight":
                st["delivery"]["freight"] = krw
                changes.append(f"물류비: {o['unit_price']:,.0f} → {n['unit_price']:,.0f}")
            else:
                st["work"]["rate"] = krw
                changes.append(f"설치 인건비 1인 1일 단가: {o['unit_price']:,.0f} → {n['unit_price']:,.0f}")
        if ln["kind"] == "work" and n["qty"] and not _same_num(n["qty"], o["qty"]):
            days = int(st["work"].get("days") or 1)
            q = int(round(n["qty"]))
            st["work"]["people"], st["work"]["days"] = (q // days, days) if q % days == 0 else (q, 1)
            changes.append(f"설치 인건비 수량: {o['qty']} → {q}")
        st.setdefault("_auto_check", []).append(n)          # 이름·비고는 다 맞춘 뒤 새 상태가 그리는 글과 비교(merge 끝)
        return ln
    if (ln.get("block") or qs.MAIN_BLOCK) != blk:
        ln["block"] = blk
    if "in_total" in n and n["in_total"] != bool(ln.get("in_total")):
        ln["in_total"] = n["in_total"]
        changes.append(f"{name} 옵션 {'Total 반영' if n['in_total'] else 'Total 미반영'}")
    if n["commodity"] != (o.get("commodity") or ""):
        ln["commodity"] = n["commodity"][:60]
        changes.append(f"{name} 품목: {o.get('commodity') or '(없음)'} → {n['commodity'] or '(없음)'}")
    if _norm(n["desc"]) != _norm(o["desc"]):
        if ln.get("key") in _GENERATED_KEYS:                 # 코드가 만들던 규격을 고쳤다 — 이제부터 적힌 글 그대로
            ln["key"] = "custom"
        ln["name"] = qs.clean_spec(n["desc"]) or ln["name"]
        changes.append(f"{name} 규격 → {ln['name'].splitlines()[0][:30]}")
    if n["qty"] is not None and not _same_num(n["qty"], o["qty"]):
        q = int(round(n["qty"]))
        if q != n["qty"]:
            warns.append(f"{name} 수량 {n['qty']:g} 은 정수로({q}) 맞췄습니다.")
        ln["qty"] = q
        changes.append(f"{name} 수량: {o['qty']} → {q}")
    if ln.get("conditional") and not ln.get("confirmed"):
        ln["confirmed"] = True                               # 담당자가 이 수량으로 고객에게 보냈다
        changes.append(f"{name} 조건부 수량 확인됨(발행한 파일 기준)")
    if n["unit_price"] is not None and not _same_num(n["unit_price"], o["unit_price"]):
        if en:
            ln["usd"] = n["unit_price"]
        else:
            ln["unit_price"], ln["price_status"] = n["unit_price"], "set"
        changes.append(f"{name} 단가: {o['unit_price']:,.2f} → {n['unit_price']:,.2f}".replace(".00", ""))
    if _norm(n["remark"]) != _norm(o.get("remark") or ""):
        ln["remark"] = n["remark"][:100]
        changes.append(f"{name} 비고 → {n['remark'][:30] or '(없음)'}")
    o_unit = (o.get("unit") or "").lower()
    if n["unit"].lower() != o_unit and not (n["unit"] == "" and o_unit in ("", "ea")):
        ln["unit"] = qs.clean_unit(qs._UNIT_SINGULAR.get(n["unit"], n["unit"]))
        changes.append(f"{name} 단위 → {n['unit'] or 'ea'}")
    return ln


def merge_items(state: dict[str, Any], items: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str], list[str]]:
    """회사 양식이 아닌 파일·PDF — 담당자가 확인한 품목(name·spec·qty·unit_price)만 품목 줄에 맞춘다(머리·묶음·NEGO 그대로)."""
    rows = [{"row": "item", "commodity": _s(it.get("name")), "desc": _s(it.get("spec")) or _s(it.get("name")),
             "qty": _num(it.get("qty")), "unit": "", "unit_price": _num(it.get("unit_price")), "remark": ""}
            for it in items if _num(it.get("unit_price")) is None or _num(it.get("unit_price")) >= 0]
    if qs.vat_included(state):            # 부가세 포함 견적의 금액으로 확인한 품목 — 작성 상태엔 공급가액으로
        rows = _to_supply(rows)
    # 합계에 드는 품목 줄만 짝짓는다 — 옵션·서술형 줄·묶음 구조는 그대로
    cur_rows = [r for r in _render(state) if r.get("row") is None and r.get("id")]
    st = copy.deepcopy(state)
    by_id = {ln["id"]: ln for ln in st["lines"]}
    changes: list[str] = []
    warns: list[str] = ["회사 양식이 아닌 파일이라 품목·금액만 이어집니다(머리 문구·묶음·NEGO 는 그대로)."]
    key = lambda r: _norm(r["desc"])                         # noqa: E731
    keep = []
    en = (st["form"].get("lang") or "ko") == "en"
    for oi, ni in _pairs([key(r) for r in cur_rows], [key(r) for r in rows], similar=0):
        if ni is None:
            ln = by_id.get(cur_rows[oi].get("id"))
            if ln and ln["kind"] in ("work", "freight"):
                keep.append(ln)
            elif ln:
                changes.append(f"빠진 품목: {_label(ln)}")
            continue
        if oi is None:
            keep.append(_new_line(st, rows[ni], qs.MAIN_BLOCK, en, changes))
            continue
        o = cur_rows[oi]
        ln = by_id[o["id"]]
        # AI 가 읽은 글은 표기가 조금씩 달라 규격·품목·비고는 그대로 두고 수량·단가만 옮긴다
        n = {**rows[ni], "desc": o["desc"], "commodity": o.get("commodity") or "", "remark": o.get("remark") or "",
             "unit": o.get("unit") or ""}
        keep.append(_update_line(st, ln, o, n, ln.get("block") or qs.MAIN_BLOCK, en, changes, warns))
    seen = {r["id"] for r in cur_rows}
    others = [ln for ln in st["lines"] if ln["id"] not in seen]            # 옵션·서술형 줄·그리지 못한 줄
    st["lines"] = [*[ln for ln in keep if ln["kind"] not in ("work", "freight")], *others,
                   *[ln for ln in keep if ln["kind"] in ("work", "freight")]]
    st.pop("_auto_check", None)
    return st, changes, warns
