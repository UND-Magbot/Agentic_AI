# -*- coding: utf-8 -*-
"""무상 대여 견적서(사용자 2026-10-30 — 대여는 대부분 무상) 점검.

    MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts -v ./docs:/docs backend python /scripts/test_quote_free_rental.py

대여 견적서에서 [무상 대여]를 고르면 단가 없이 품목·수량·기간만으로 발행되고, 견적서 단가·금액 칸은 '무상', Total 칸은
'무상 대여 (N개월)', 부가세·NEGO·물류비·인건비·결제조건·중도 해지 주석이 빠진다. 유상으로 되돌리면 예전 대여 견적서 그대로.
"""
from __future__ import annotations

import copy
import io
import sys
from datetime import date

import openpyxl

from app.company_knowledge import quote_patch as qp
from app.company_knowledge import quote_readback as rb
from app.company_knowledge import quote_session as qs
from app.company_knowledge import quote_xlsx as qx
from app.sales_deals import service as svc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
DAY = date(2026, 10, 30)


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def parts(state: dict, no: str = "QT-TS20261030-01") -> tuple[list, dict]:
    lang = state["form"].get("lang") or "ko"
    return qs.render_lines(state, lang, state["form"].get("fx_rate")), \
        qx.quote_header(state["form"], lang=lang, quote_no=no, day=DAY, pogo=False)


def build(state: dict) -> bytes:
    lines, header = parts(state)
    return qx.build_xlsx(header, lines)


def edit(state: dict, payload: dict) -> dict:
    new, _ = qs.apply_ops(copy.deepcopy(state), qs.edit_ops(state, payload), None)
    return new


def ws_of(data: bytes):
    return openpyxl.load_workbook(io.BytesIO(data)).active


def total_row(ws) -> int:
    return next(r for r in range(17, ws.max_row + 1) if str(ws.cell(r, 1).value or "").startswith("Total"))


def row_of(ws, text: str) -> int:
    return next(r for r in range(1, ws.max_row + 1) if text in str(ws.cell(r, 2).value or ""))


FORM = {"customer": "[테스트] 무상대여 고객", "to": "김철수 님", "contact_name": "홍길동", "contact_title": "이사", "initials": "TS",
        "contact_mobile": "010-0000-0000", "contact_email": "name@example.com", "purpose": "rental"}
S = qs.draft_state({"applicable": True, "items": []}, model="", payload_kg=None, form=FORM, manual=True)
S.update(asked=["extra"], delivery={"case": 2, "freight": 150_000})
S["form"]["rental_months"] = 12
S["lines"] = [{"id": "L1", "kind": "extra", "key": "extra", "name": "방역 로봇 UV-1", "commodity": "Robot", "qty": 2,
               "unit_price": 500_000, "remark": "", "price_status": "set"}]
S["next_id"] = 2
S["nego"] = {"mode": "amount", "value": 50_000, "label": ""}
qs._sync_work(S)

# 1. 유상 대여(기본) — 예전 그대로: 단가 필요·월 납입금·중도 해지 주석
check(not qs.free_rental(S) and qs.RENTAL_NOTE in S["form"]["foot_notes"], "기본은 유상 대여 — 중도 해지 주석 있음")
P = edit(S, {"lines": [{"id": "L1", "name": "방역 로봇 UV-1", "commodity": "Robot", "qty": 2, "unit_price": 500_000},
                       {"id": None, "name": "충전 스테이션", "commodity": "Dock", "qty": 1, "unit_price": None}]})
check(len(P["lines"]) == len(S["lines"]), "유상 대여 — 단가 없는 새 줄은 지금처럼 빠짐", str([x["name"] for x in P["lines"]]))

# 2. [무상 대여] — 잘못된 값은 무시, 고르면 Comments·주석 바뀜
check(edit(S, {"form": {"rental_fee": "공짜"}})["form"].get("rental_fee") in (None, "paid"), "잘못된 무상/유상 값은 무시")
F = edit(S, {"form": {"rental_fee": "free"}})
check(qs.free_rental(F), "[무상 대여] 저장")
check(F["form"]["comments"] == qs.FREE_RENTAL_COMMENTS, "Comments 기본 문구 = 제품 무상 대여 건", F["form"]["comments"])
check(qs.RENTAL_NOTE not in (F["form"].get("foot_notes") or []), "중도 해지(월 납입료 위약금) 주석 빠짐", str(F["form"].get("foot_notes")))

# 3. 단가 없는 줄도 저장, 품목 단가·NEGO 때문에 막히지 않음(물류비는 받을 수 있다 — 화물 직납 15만원)
F2 = edit(F, {"lines": [{"id": "L1", "name": "방역 로봇 UV-1", "commodity": "Robot", "qty": 2, "unit_price": 500_000},
                        {"id": None, "name": "충전 스테이션", "commodity": "Dock", "qty": 1, "unit_price": None}]})
check([x["name"] for x in F2["lines"] if x["kind"] == "extra"] == ["방역 로봇 UV-1", "충전 스테이션"], "무상 — 단가 없는 새 줄도 저장",
      str([x["name"] for x in F2["lines"]]))
check(qs.problems(F2) == [], "무상 — 품목 단가·NEGO 때문에 막히지 않음", str(qs.problems(F2)))
check(any("개월" in p for p in qs.problems({**F2, "form": {**F2["form"], "rental_months": None}})),
      "무상이어도 대여 개월 수는 필요")
NF = copy.deepcopy(F2)
NF["delivery"]["freight"] = None
qs._sync_work(NF)
check(any("물류비" in p for p in qs.problems(NF)), "무상이어도 화물 직납이면 물류비 금액 필요", str(qs.problems(NF)))
check(qs.subtotal(F2) == 150_000 and "150,000원" in qs.total_text(F2), "합계 = 물류비만(품목 무상)", qs.total_text(F2))
check((qs.next_question(F2) or {}).get("id") != "extra", "AI 가 추가 항목 금액을 묻지 않음", str(qs.next_question(F2)))
FN = copy.deepcopy(F2)                                   # 받을 금액이 없는 무상 대여 — 택배
FN["delivery"] = {"case": 1, "freight": None}
qs._sync_work(FN)
check(qs.problems(FN) == [] and qs.subtotal(FN) == 0 and qs.total_text(FN) == "**무상 대여**", "물류비 없으면 합계 0 · '무상 대여'",
      f"{qs.problems(FN)} {qs.total_text(FN)}")

# 4. 견적서 줄 — 품목 0원(개월 곱 없음), 물류비는 금액, NEGO 줄 없음
lines, head = parts(F2)
items = [r for r in lines if not r.get("row")]
goods = [r for r in items if r.get("kind") not in ("work", "freight")]
check([r["desc"] for r in goods] == ["방역 로봇 UV-1", "충전 스테이션"], "품목 줄", str([r.get("desc") for r in items]))
check(all(r["unit_price"] == 0 and r["amount"] == 0 and "months" not in r for r in goods), "품목 단가·금액 0, 개월 곱 없음")
check(any(r.get("kind") == "freight" and r["amount"] == 150_000 for r in items), "물류비 줄은 150,000원", str(items))
check(not any(r.get("row") == "nego" for r in lines) and qx.total(lines) == 150_000, "NEGO 줄 없음 · Total = 물류비", str(qx.total(lines)))
lv, _ = parts(edit(F2, {"form": {"vat": "included"}}))
check(qx.total(lv) == 165_000, "물류비가 있으면 부가세 포함도 적용(15만 × 1.1)", str(qx.total(lv)))
check(head.get("rental_free") and head["rental_months"] is None, "머리 — 무상·월 납입금 없음", str((head.get("rental_free"), head["rental_months"])))

# 5. 엑셀 — 품목 단가·금액 칸 '무상'(값 0), 물류비는 금액
data = build(F2)
ws = ws_of(data)
r1, tr, rf = row_of(ws, "방역 로봇"), total_row(ws), row_of(ws, "물류비")
check(ws.cell(r1, 5).value == 0 and ws.cell(r1, 5).number_format == '"무상"' and ws.cell(r1, 6).number_format == '"무상"',
      "엑셀 품목 단가·금액 칸 = 무상", str((ws.cell(r1, 5).value, ws.cell(r1, 5).number_format)))
check(ws.cell(rf, 5).value == 150_000 and ws.cell(rf, 5).number_format != '"무상"', "엑셀 물류비 칸은 금액", str(ws.cell(rf, 5).number_format))
check(ws.cell(tr, 7).number_format != '"무상 대여 (12개월)"' and ws["G15"].value == "VAT 별도", "물류비 있으면 Total 은 금액 · VAT 별도",
      str((ws.cell(tr, 7).number_format, ws["G15"].value)))
check(any("발주" in str(ws.cell(r, 7).value or "") for r in range(tr, tr + 4)), "물류비 있으면 결제조건은 적은 문구 그대로")
wn_ = ws_of(build(FN))
trn = total_row(wn_)
check(wn_.cell(trn, 7).number_format == '"무상 대여 (12개월)"' and wn_.cell(trn, 1).value == "Total" and not wn_["G15"].value,
      "물류비 없으면 Total = 무상 대여 (12개월) · VAT 문구 없음", str((wn_.cell(trn, 1).value, wn_.cell(trn, 7).number_format, wn_["G15"].value)))
check(any(wn_.cell(r, 7).value == "무상 대여" for r in range(trn, trn + 4)), "물류비 없으면 결제조건 = 무상 대여")
check("월 납입금" not in str(ws["E16"].value), "단가 머리에 월 납입금 없음", str(ws["E16"].value))

# 6. 되읽기 — 무상 파일은 무상 대여로, 고치지 않은 무상 파일 → 바뀐 점 없음(물류비 있는 것·없는 것 둘 다)
M, ch, wn = rb.merge(S, rb.parse(data))
check(qs.free_rental(M), "무상 파일(물류비 있음)을 유상 상태에 올리면 무상 대여로", f"{ch} {wn}")
check(any("무상 대여" in c for c in ch), "되읽기 바뀐 점에 무상 대여", str(ch))
for st, label in ((F2, "물류비 있음"), (FN, "물류비 없음")):
    _, chx, wnx = rb.merge(st, rb.parse(build(st)))
    check(chx == [] and wnx == [], f"고치지 않은 무상 파일({label}) 되읽기 → 바뀐 점 없음", f"{chx} {wnx}")
MN, _, _ = rb.merge(S, rb.parse(build(FN)))
check(qs.free_rental(MN) and MN["form"].get("rental_months") == 12, "물류비 없는 무상 파일 → 무상 · 12개월")
M3, ch3, _ = rb.merge(F2, rb.parse(build(edit(F2, {"form": {"rental_fee": "paid"}, "lines": [
    {"id": "L1", "name": "방역 로봇 UV-1", "commodity": "Robot", "qty": 2, "unit_price": 500_000}]}))))
check(not qs.free_rental(M3) and qs.is_rental(M3), "유상 대여 파일을 무상 상태에 올리면 유상으로", str(ch3))

# 7. 올린 파일 위에서 고치기는 무상이면 회사 양식으로 새로 그림
bl, bh = parts(S)
try:
    qp.patch(build(S), bl, bh, *parts(F2))
    check(False, "무상 대여는 올린 파일 위에서 고치지 않음")
except qp.PatchError:
    check(True, "무상 대여는 올린 파일 위에서 고치지 않음")

# 8. 영업 건 품목 — 품목 0, 물류비 150,000
deal_items = svc.snapshot_items(lines)
check(sorted(it["unit_price"] for it in deal_items) == [0, 0, 150_000], "영업 건 품목 0원 · 물류비 금액", str(deal_items))

# 9. 유상으로 되돌리면 중도 해지 주석·Comments 복귀, 단가 필요
B = edit(F2, {"form": {"rental_fee": "paid"}})
check(qs.RENTAL_NOTE in B["form"]["foot_notes"] and B["form"]["comments"] == qs.RENTAL_COMMENTS, "유상으로 되돌리면 주석·Comments 복귀")
check(any("단가" in p for p in qs.problems(B)), "유상으로 되돌리면 단가 없는 줄이 다시 막힘", str(qs.problems(B)))

# 10. 판매로 바꾸면 무상 표시 사라짐
SA = edit(F2, {"form": {"purpose": "sale"}})
check(not qs.free_rental(SA) and SA["form"]["comments"] != qs.FREE_RENTAL_COMMENTS, "판매로 바꾸면 무상 대여 아님", SA["form"]["comments"])

# 11. 영업 건 — 합계 0원 대여 = 무상: 수주 → 바로 출하(거래명세서·결제 조건·입금 없음). 물류비를 받는 무상 대여·유상 대여는 지금 절차
from app.sales_deals import flow  # noqa: E402

base = {"kind": "quote", "won": True, "shipments": [], "statement": None, "pay_case": None, "pay_terms": [], "payments": []}
fd = {**base, "quote": {"biz": "rental", "amount": 0}}
check(flow.free(fd) and flow.steps(fd) == [{"key": "ship", "label": "납품(출하)", "done": False, "ready": True}],
      "무상 대여 건 — 수주 뒤 바로 출하만", str(flow.steps(fd)))
check(flow.steps({**fd, "shipments": [{"date": "2026-10-30"}]})[0]["done"], "무상 대여 건 — 출하하면 끝")
for q, label in (({"biz": "rental", "amount": 150_000}, "물류비 받는 무상 대여"), ({"biz": "rental", "amount": 0.0, "currency": "KRW"}, None),
                 ({"biz": "sale", "amount": 0}, "판매 0원")):
    if label:
        d = {**base, "quote": q}
        check(not flow.free(d) and [s["key"] for s in flow.steps(d)][:2] == ["statement", "case"], f"{label} — 거래명세서·결제 조건 절차 그대로",
              str(flow.steps(d)))

for label, detail in FAIL:
    print(f"FAIL {label} :: {detail}")
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
sys.exit(1 if FAIL else 0)
