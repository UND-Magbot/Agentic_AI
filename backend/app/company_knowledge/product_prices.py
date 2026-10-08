"""맥봇 제품 단가 DB — 회사 단가표(docs/sources/Magbot 단가표_Chris 송부용 261002.xlsx, '대표님 확인 최종 판매가' 시트) → product_prices 표.

견적서 작성용(사용자 2026-10-06: ATC 유선부터 MG 까지만 따로 저장). 범위 = 단가표 'ATC_유선'·'ATC_무선'·'Tool Changer Motor K'·
'Tool Changer Motor'·'MG' 구간. DC Motor·Pneumatic·Shape-Memory·Shiftlock 그리퍼 행은 넣지 않는다.

- 한 행 = (모델, 품목) 하나. 품목은 단가표 머리 그대로: MASTER T.C · T.P · PPM/PPF(0.3m 케이블 포함) · PMM/PMF · Cable 0.3m ·
  Cable 4m · IB · CONTROLBOX. MG 는 C열(머리 'Master T.C')이 그리퍼 본체 가격이라 품목 '본체'로 둔다.
- 0원 칸은 '가격 미정'(price_status='unset', 가격 NULL) — 무상이 아니다. 최종 견적에 0원·미정을 넣지 않는다(v0.8 C13).
- 고객가·대리점가가 모두 빈 칸이면 그 모델에 해당 품목 가격이 없다는 뜻이라 행을 만들지 않는다(TCK50 의 PPM 등).
  고객가만 비고 대리점가가 0(수식 결과)이면 가격 미정으로 둔다(TCK100 의 T.P 등).
- 대리점가는 단가표 값(고객가 × 0.7)을 원 단위로 반올림해 저장한다.
- 같은 (단가표, 모델, 품목)은 다시 적재하면 갱신한다. 단가표가 바뀌면 새 파일로 ingest 하면 된다(이전 단가표 행은 비활성).

적재:
    docker compose run --rm --no-deps -v ./docs:/docs backend python -m app.company_knowledge.product_prices ingest
    (정리 문서: docs/company_knowledge/product_prices.md 를 함께 다시 쓴다)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text

_log = logging.getLogger("company_knowledge.product_prices")

DOCS = Path("/docs") if Path("/docs").is_dir() else Path(__file__).resolve().parents[3] / "docs"
BOOK_NAME = "Magbot 단가표_Chris 송부용 261002.xlsx"
SHEET = "대표님 확인 최종 판매가"
CATEGORIES = ("ATC_유선", "ATC_무선", "Tool Changer Motor K", "Tool Changer Motor", "MG")   # 저장 범위(사용자 2026-10-06)
# 단가표 1행 머리(고객사 열) → 품목 이름. 대리점가는 바로 오른쪽 열.
ITEM_BY_HEADER = {
    "Master T.C": "MASTER T.C",
    "Tool Plate": "T.P",
    "PPM/PPF (개별) + 0.3m cable 포함": "PPM/PPF",
    "PMM/PMF (개별)": "PMM/PMF",
    "Cable 0.3m(meter)": "Cable 0.3m",
    "Cable 4m(meter)": "Cable 4m",
    "IB(Interface Bracket)": "IB",
    "Control Box": "CONTROLBOX",
}
ITEM_NOTE = {
    "PPM/PPF": "PPM 또는 PPF 1개 가격(개별)",
    "Cable 0.3m": "포고핀 모듈(PPM·PPF) 1개당 1EA — 견적 수량 = PPM 수 + PPF 수(v0.8 C16)",
    "PMM/PMF": "PMM 또는 PMF 1개 가격(개별)",
    "Cable 4m": "MASTER T.C 기본 포함 — 견적에 따로 적지 않음(추가·교체 시 단가)",
    "CONTROLBOX": "MASTER T.C 기본 포함 — 견적에 따로 적지 않음(추가·교체 시 단가)",
    "IB": "Interface Bracket — 로봇 플랜지와 툴체인저 마스터 사이 브라켓, 별도 옵션",
}
# TCK 는 단가표 'ATC_유선' 구간에 있지만 산업용 마그네틱 그리퍼(사용자 2026-10-06) — ATC 견적에서 쓰지 않도록 구분을 바꾼다
CATEGORY_BY_MODEL = (("TCK", "산업용 마그네틱 그리퍼(TCK)"),)
# 견적 품목 코드 → 단가표 품목(PPM·PPF 는 같은 개별 단가, PMM·PMF 도 같음)
CODE_TO_ITEM = {"PPM": "PPM/PPF", "PPF": "PPM/PPF", "PMM": "PMM/PMF", "PMF": "PMM/PMF",
                "MASTER": "MASTER T.C", "TP": "T.P", "T.P": "T.P", "CONTROLBOX": "CONTROLBOX"}
_DATE_RE = re.compile(r"(\d{2})(\d{2})(\d{2})(?=\D*\.xlsx$)", re.I)


def _won(v: Any) -> int | None:
    return None if v is None else int(round(float(v)))


def price_list_date(name: str) -> date | None:
    """'…송부용 261002.xlsx' → 2026-10-02."""
    m = _DATE_RE.search(name)
    return date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def parse_sheet(rows: list[list[Any]]) -> list[dict[str, Any]]:
    """시트 값(1행 머리, 2행 고객사/대리점, 3행부터 A=구분·B=모델) → (모델, 품목) 단가 행. 저장 범위 구분만."""
    head = rows[0]
    cols = []                                   # (고객가 열 index, 품목)
    for i, h in enumerate(head):
        key = str(h).strip() if h is not None else ""
        if key in ITEM_BY_HEADER:
            cols.append((i, ITEM_BY_HEADER[key]))
    out: list[dict[str, Any]] = []
    category = None
    for r_no, row in enumerate(rows[2:], start=3):
        row = list(row) + [None] * (len(head) - len(row))
        if row[0] not in (None, ""):
            category = str(row[0]).strip()
        model = str(row[1]).strip() if row[1] not in (None, "") else ""
        if not model or category not in CATEGORIES:
            continue
        row_category = next((c for prefix, c in CATEGORY_BY_MODEL if re.fullmatch(prefix + r"\d+", model)), category)
        for i, item in cols:
            cust = row[i]
            dealer = row[i + 1] if i + 1 < len(row) else None
            if category == "MG":
                if item != "MASTER T.C":
                    continue
                item = "본체"
            # 빈 칸 = 그 모델엔 이 품목 가격 없음. 단 고객가가 비고 대리점가만 0(수식 결과)이면 가격 미정(TCK100 의 T.P 등)
            if cust is None and dealer != 0:
                continue
            unset = cust is None or float(cust) == 0
            out.append({
                "category": row_category, "model": model, "item": item,
                "customer_price": None if unset else _won(cust),
                "dealer_price": None if unset or dealer in (None, 0) else _won(dealer),
                "price_status": "unset" if unset else "set",
                "source_cell": f"R{r_no}",
                "note": ITEM_NOTE.get(item, ""),
            })
    return out


def parse_book(path: Path | None = None) -> list[dict[str, Any]]:
    import openpyxl

    path = path or DOCS / "sources" / BOOK_NAME
    ws = openpyxl.load_workbook(str(path), data_only=True)[SHEET]
    return parse_sheet([list(r) for r in ws.iter_rows(values_only=True)])


def won_text(v: int | None) -> str:
    return "가격 미정" if v is None else f"{v:,}원"


# ── DB ─────────────────────────────────────────────────────────────────────

async def upsert(rows: list[dict[str, Any]], *, price_list: str = BOOK_NAME) -> dict[str, int]:
    """(단가표, 모델, 품목) 기준 넣기/갱신. 다른 단가표의 행은 비활성(최신 단가표만 견적에 쓴다)."""
    from ..database import SessionLocal

    ins = upd = 0
    eff = price_list_date(price_list)
    async with SessionLocal() as db:
        for r in rows:
            new = (await db.execute(text(
                "INSERT INTO product_prices (price_list, effective_date, category, model, item, customer_price, dealer_price, "
                "price_status, source_cell, note) VALUES (:pl, :eff, :category, :model, :item, :customer_price, :dealer_price, "
                ":price_status, :source_cell, :note) "
                "ON CONFLICT (price_list, model, item) DO UPDATE SET effective_date=EXCLUDED.effective_date, "
                "category=EXCLUDED.category, customer_price=EXCLUDED.customer_price, dealer_price=EXCLUDED.dealer_price, "
                "price_status=EXCLUDED.price_status, source_cell=EXCLUDED.source_cell, note=EXCLUDED.note, "
                "active=TRUE, updated_at=NOW() RETURNING (xmax = 0)"),
                {**r, "pl": price_list, "eff": eff})).scalar_one()
            ins, upd = ins + bool(new), upd + (not new)
        await db.execute(text("UPDATE product_prices SET active=FALSE, updated_at=NOW() WHERE price_list <> :pl AND active"),
                         {"pl": price_list})
        await db.commit()
    return {"inserted": ins, "updated": upd}


async def list_prices(*, active_only: bool = True) -> list[dict[str, Any]]:
    from ..database import SessionLocal

    async with SessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT * FROM product_prices" + (" WHERE active" if active_only else "") + " ORDER BY id"))).mappings().all()
    return [dict(r) | {"customer_price": _won(r["customer_price"]), "dealer_price": _won(r["dealer_price"])} for r in rows]


def find(rows: list[dict[str, Any]], model: str, item: str) -> dict[str, Any] | None:
    """견적용 찾기 — find(rows, 'TCV2', 'PPF') → PPM/PPF 개별 단가 행. 없으면 None(가격을 만들지 않음)."""
    want = CODE_TO_ITEM.get(item.upper().replace(" ", ""), item)
    return next((r for r in rows if r["model"].lower() == model.lower() and r["item"] == want), None)


async def price_of(model: str, item: str) -> dict[str, Any] | None:
    return find(await list_prices(), model, item)


def attach_prices(items: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """구성품 줄(price_model·price_item 이 있는 줄)에 단가표 고객사가를 붙인다 — 국내 견적 기준(사용자 2026-10-06).
    줄마다 unit_price·amount·price_status('set' 단가 있음 / 'unset' 단가표 0원=가격 미정 / 'none' 단가표에 없음).
    합계는 선택 품목(optional)을 뺀 기본 줄만, 수량·단가가 다 있어야 계산한다(모르는 값을 0 으로 더하지 않음 — C13).
    반환 {subtotal, complete, missing[], optional_total}."""
    subtotal, missing, opt_total = 0, [], 0
    for it in items:
        counted = not it.get("optional") or it.get("included")      # 선택 품목은 [견적에 포함]을 체크했을 때만 합계에
        if not it.get("price_item"):
            it.update(unit_price=None, amount=None, price_status="none")
            if counted:
                missing.append(f"{it['name']} — 단가표에 없음(견적 확보 후)")
            continue
        r = find(rows, it["price_model"], it["price_item"])
        unit = r["customer_price"] if r and r["price_status"] == "set" else None
        status = "set" if unit is not None else "unset" if r else "none"
        amount = unit * it["qty"] if unit is not None and it.get("qty") is not None else None
        it.update(unit_price=unit, amount=amount, price_status=status)
        if not counted:
            opt_total += amount or 0
            continue
        if amount is None:
            missing.append(f"{it['name']} — " + ("수량 미정" if unit is not None else
                                                 "단가표 가격 미정" if status == "unset" else "단가표에 없음"))
        else:
            subtotal += amount
    return {"subtotal": subtotal, "complete": not missing, "missing": missing, "optional_total": opt_total,
            "currency": "KRW", "vat": "별도", "basis": "회사 단가표 고객사가"}


def apply_options(accessories: dict[str, Any], included: list[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """[견적에 포함] 체크 반영 — 선택 품목(optional) 중 이름이 included 에 있는 것만 포함하고 합계를 다시 계산."""
    want = set(included)
    for it in accessories.get("items") or []:
        if it.get("optional"):
            it["included"] = it["name"] in want
    accessories["pricing"] = attach_prices(accessories.get("items") or [], rows)
    return accessories


# ── 견적서 머리(사용자 2026-10-06) ─────────────────────────────────────────────────────────────────
# 한글·영문(해외) 양식은 같은 구성이고 영문은 문구만 영어로. 위쪽(Quotation by)엔 회사명, 아래쪽 서명란엔 'UND Robotics Co., Ltd'.
# 출처: docs/examples/quotes/(주)유엔디로보틱스_견적서_가나테크_magbot툴체인저_VT260930-2.pdf(한글 문구·회사·계좌),
#       docs/examples/quotes/(주)유엔디_견적서_Realman_TCC1_Lu260514.xlsx(영문 항목명·문구만 — 회사·주소·계좌·세트 가격은 그 건만의 것이라 쓰지 않음).
# 성균관대·Realman 견적은 따로 작성한 건이라 가격 기준에서 제외. 영문 견적의 주소·계좌·은행 주소는 Realman 견적 값을 그대로
# 쓴다(사용자 2026-10-06: 회사명·예금주·주소가 한글 양식과 달라도 상관없음). 통화는 USD.
# 회사 계좌·대표자·주소는 공개 저장소에 두지 않는다 — data/company_profile.json(.gitignore)에서 읽고,
# 없으면 자리표시 값의 company_profile.example.json 으로 동작한다(견적서에 예시 값이 찍힌다).
_PROFILE_DIR = Path(__file__).with_name("data")


def _load_company_profile() -> dict[str, Any]:
    path = _PROFILE_DIR / "company_profile.json"
    if not path.exists():
        _log.warning("company_profile.json 이 없어 예시 회사 정보로 동작합니다: %s", path)
        path = _PROFILE_DIR / "company_profile.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


COMPANY_PROFILE = _load_company_profile()
QUOTE_ISSUER = COMPANY_PROFILE["quote_issuer"]
QUOTE_ISSUER_EN = COMPANY_PROFILE["quote_issuer_en"]
QUOTE_FIXED_TERMS = {"vat": "VAT 별도", "validity": "견적으로부터 30일",
                     "order_note": "※ 발주 시 서명 날인하여 담당자 이메일을 통해 발신해주시기 바랍니다.",
                     "option_note": "* 견적 외 사양 및 추가 요청사항은 옵션 사항이며, 별도 협의 및 추가 견적 대상입니다"}
QUOTE_FIXED_TERMS_EN = {
    "validity": "30 days from the quotation date",
    "order_note": "※ Please sign and stamp this document upon placing the order and send it to the person in charge by email.",
    "order_accept": "We accept the prices and terms stated in this quotation and intend to use this quotation as our official "
                    "purchase order to your company.",
    "option_note": "* Specifications and additional requests not included in this quotation shall be considered optional items "
                   "and subject to separate discussion and additional quotation.",
}
# 양식 항목명 — 한글 양식의 표 머리(영문 그대로 쓰는 칸 포함)와 영문 양식의 같은 자리
QUOTE_LABELS = {
    "ko": {"date": "Quotation Date", "no": "Quotation No.", "by": "Quotation by", "customer": "Customer", "comments": "Comments",
           "goods": "Goods Description & Details", "item": "Commodity & Description", "qty": "Quantity", "price": "Price",
           "amount": "Supply amount", "remark": "Remark", "total": "Total (부가세 별도)", "delivery": "납기 일자",
           "place": "납품장소", "validity": "유효일자", "payment": "결제조건", "note": "비    고", "order": "발주 확인"},
    "en": {"date": "Quotation Date", "no": "Quotation No.", "by": "Quotation by", "customer": "Customer", "comments": "Comments",
           "goods": "Goods Description & Details", "item": "Commodity & Description", "qty": "Quantity", "price": "Price",
           "amount": "Supply amount", "remark": "Remark", "total": "Total Amount", "delivery": "Delivery Schedule",
           "place": "Delivery Place", "validity": "Validity", "payment": "Payment Terms", "note": "Remarks",
           "order": "Purchase Order Confirmation"},
}


def quote_form(lang: str = "ko") -> dict[str, Any]:
    """견적서 양식 한 벌 — 회사(위·아래 표기)·고정 문구·항목명. lang = 'ko'(국내) | 'en'(해외, 같은 구성을 영어로)."""
    if lang not in QUOTE_LABELS:
        raise ValueError(f"견적서 언어는 ko 또는 en: {lang!r}")
    en = lang == "en"
    return {"lang": lang, "issuer": QUOTE_ISSUER_EN if en else QUOTE_ISSUER,
            "terms": QUOTE_FIXED_TERMS_EN if en else QUOTE_FIXED_TERMS, "labels": QUOTE_LABELS[lang],
            "missing": [k for k, v in (QUOTE_ISSUER_EN if en else QUOTE_ISSUER).items() if v is None]}


def quote_file_name(customer: str, subject: str, initials: str, day: date, quote_no: str) -> str:
    """파일명 '(주)유엔디로보틱스_견적서_{고객}_{내용}_{견적번호}' — 예: …_성진물류_magbot툴체인저_VT20261008.
    새 견적번호(건 번호 = 이니셜+날짜, 사용자 2026-10-08)에 이니셜·날짜가 이미 있어 따로 붙이지 않는다.
    예전 번호(S26-0182 …)는 그대로 '…_{이니셜}{YYMMDD}_{견적번호}'(예: …_가나테크_magbot툴체인저_VT260930_S26-0182)."""
    if quote_no.startswith(f"{initials}{day:%Y%m%d}"):
        return f"(주)유엔디로보틱스_견적서_{customer}_{subject}_{quote_no}"
    return f"(주)유엔디로보틱스_견적서_{customer}_{subject}_{initials}{day:%y%m%d}_{quote_no}"


def clean_quote_file_name(name: str) -> str:
    """예전에 저장한 발행본 파일명 정리(사용자 2026-10-08) — 판 표기 '_Revn' 을 빼고,
    새 번호 앞에 겹쳐 붙은 이니셜·날짜('_VT261008_VT20261008')를 하나로. 지금 발행하는 이름(quote_file_name)과 같아진다."""
    name = re.sub(r"_Rev\d+(?=\.xlsx$)", "", name or "")
    return re.sub(r"_([A-Z]{2,4})(\d{6})_\1(20\2[B-Z]?)(?=\.xlsx$)", r"_\1\3", name)


def markdown(rows: list[dict[str, Any]], price_list: str = BOOK_NAME) -> str:
    """사람용 정리 — 구분별 모델 × 품목 표(고객사가 / 대리점가)."""
    items = list(dict.fromkeys(r["item"] for r in rows))
    lines = ["# 맥봇 제품 단가 (product_prices)", "",
             f"원본: docs/sources/{price_list} — '{SHEET}' 시트, 범위 ATC_유선 ~ MG. DB 표 product_prices 와 같은 내용입니다.",
             "칸 = 고객사가 / 대리점가(30%). '가격 미정' = 단가표 0원(무상 아님, 견적 전 확정 필요). '-' = 단가표에 해당 품목 없음.", ""]
    for cat in (*CATEGORIES, *(c for _p, c in CATEGORY_BY_MODEL)):
        part = [r for r in rows if r["category"] == cat]
        if not part:
            continue
        cols = [i for i in items if any(r["item"] == i for r in part)]
        lines += [f"## {cat}", "", "| 모델 | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        for model in dict.fromkeys(r["model"] for r in part):
            cells = []
            for c in cols:
                r = next((x for x in part if x["model"] == model and x["item"] == c), None)
                cells.append("-" if r is None else "가격 미정" if r["price_status"] == "unset"
                             else f"{r['customer_price']:,} / {r['dealer_price']:,}" if r["dealer_price"] is not None
                             else f"{r['customer_price']:,}")
            lines.append(f"| {model} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += ["## 품목 메모", ""] + [f"- **{k}**: {v}" for k, v in ITEM_NOTE.items()] + [""]
    return "\n".join(lines)


async def ingest(path: Path | None = None) -> dict[str, Any]:
    path = path or DOCS / "sources" / BOOK_NAME
    rows = parse_book(path)
    res = await upsert(rows, price_list=path.name)
    doc = DOCS / "company_knowledge" / "product_prices.md"
    try:
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(markdown(await list_prices(), path.name), encoding="utf-8")
    except OSError as e:          # docs 를 마운트하지 않고 돌린 경우 — DB 적재는 끝났다
        _log.warning("정리 문서를 쓰지 못함: %r", e)
    return {"parsed": len(rows), **res, "unset": sum(1 for r in rows if r["price_status"] == "unset")}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ingest":
        print(asyncio.run(ingest(Path(sys.argv[2]) if len(sys.argv) > 2 else None)), flush=True)
    elif cmd == "list":
        for r in asyncio.run(list_prices()):
            print(f"{r['category']} {r['model']} {r['item']}: {won_text(r['customer_price'])} / {won_text(r['dealer_price'])}")
    else:
        print(__doc__)
