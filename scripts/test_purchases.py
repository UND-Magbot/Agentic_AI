# -*- coding: utf-8 -*-
"""제품 구매 관리(backend/app/purchases) 코드 점검 — DB 없이: 단계 계산·결제 회차 금액·지출내역 대조 판정·발주서 엑셀·견적서 판독.
AI 호출은 가짜 답으로 바꿔 코드 쪽 판단(지어낸 숫자 차단 등)만 본다.

    python scripts/test_purchases.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8")

import openpyxl  # noqa: E402

from app.purchases import expense_check as ec  # noqa: E402
from app.purchases import flow  # noqa: E402
from app.purchases import po_xlsx as px  # noqa: E402
from app.purchases import po_read as po_read_mod  # noqa: E402
from app.purchases import quote_read as qr  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
TODAY = dt.date(2026, 11, 2)


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def base(**kw) -> dict:
    d = {"quote_files": [], "approval": {}, "po": {}, "pay_terms": [], "payments": [], "receipt": {}, "statement": {},
         "tax_invoice": {}, "customs": {}, "dropped": False, "region": "domestic"}
    d.update(kw)
    return d


# ── 단계 ──
d = base()
check(flow.stage(d) == "견적 대기", "처음은 견적 대기", flow.stage(d))
s = {x["key"]: x for x in flow.steps(d)}
check(s["quote"]["ready"] and not s["approval"]["ready"] and not s["order"]["ready"] and not s["pay"]["ready"],
      "견적 전엔 재가·발주·송금 못 함")
d = base(quote_files=[{"filename": "q.pdf"}])
check(flow.stage(d) == "재가 대기" and {x["key"]: x for x in flow.steps(d)}["approval"]["ready"], "견적 첨부 → 재가 대기")
d = base(quote_files=[{"filename": "q.pdf"}], approval={"done_at": "x"})
check(flow.stage(d) == "발주 대기", "재가 완료 → 발주 대기")
po = {"no": "PO-LU20261102-01", "total": 1_000_000, "items": [{"description": "A\n- x", "qty": 2}, {"description": "B", "qty": 1}]}
d = base(quote_files=[{}], approval={"done_at": "x"}, po=po)
s = {x["key"]: x for x in flow.steps(d)}
check(flow.stage(d) == "발주 대기" and not s["pay"]["ready"] and s["order"]["note"] == "입고 예정일 필요",
      "발주서만 내고 입고 예정일이 없으면 다음 단계 못 감(사용자 2026-11-02)")
po = {**po, "expected_date": "2026-11-20"}
d = base(quote_files=[{}], approval={"done_at": "x"}, po=po)
s = {x["key"]: x for x in flow.steps(d)}
check(flow.stage(d) == "송금 대기" and all(s[k]["ready"] for k in ("pay", "ship", "receipt", "statement", "tax")),
      "발주 뒤 송금·배송·입고·명세서·계산서 모두 가능(순서 자유)")
check([x["key"] for x in flow.steps(d)] == ["quote", "approval", "order", "pay", "ship", "receipt", "statement", "tax"]
      and s["statement"]["optional"] and not s["receipt"]["optional"], "국내 단계 — 배송 추가, 명세서·계산서는 선택")
REC_ALL = {"records": [{"lines": [{"line_no": 0, "qty": 2}, {"line_no": 1, "qty": 1}]}]}
d = base(quote_files=[{}], approval={"done_at": "x"}, po=po, receipt=REC_ALL, tax_invoice={"done_at": "x", "mode": "prepaid"})
check(flow.stage(d) == "송금 대기" and flow.done_map(d)["ship"], "입고를 먼저 해도 상태는 남은 첫 단계(송금), 입고 기록이 있으면 배송 끝")
check({x["key"]: x for x in flow.steps(d)}["tax"]["note"] == "선결제", "선결제 표시")
d2 = {**d, "payments": [{"simple": True, "date": "2026-11-11"}]}
check(flow.stage(d2) == "완료" and flow.done_map(d2)["pay"], "송금·입고 끝나면 완료(명세서는 미등록이어도 — 사용자 2026-11-11)", flow.stage(d2))
# 배송(사용자 2026-11-11) — [배송 시작] → 배송 중, 송금이 남아 있으면 상태 아래 '송금 남음'
sh = base(quote_files=[{}], approval={"done_at": "x"}, po=po, receipt={"shipping": {"date": "2026-11-05"}})
check(flow.stage(sh) == "배송 중" and flow.stage_note(sh) == "송금 남음" and flow.in_transit(sh), "배송 시작 → 배송 중(송금 남음 표시)",
      flow.stage(sh) + "/" + flow.stage_note(sh))
sh2 = {**sh, "payments": [{"simple": True}]}
check(flow.stage(sh2) == "배송 중" and flow.stage_note(sh2) == "", "송금 끝 + 배송 중 → 배송 중만")
sh3 = {**sh2, "receipt": {"shipping": {"date": "2026-11-05"}, "records": [{"lines": [{"line_no": 0, "qty": 1}]}]}}
check(flow.stage(sh3) == "입고 대기" and not flow.in_transit(sh3), "부분 입고가 들어오면 배송 중 → 입고 대기", flow.stage(sh3))
paid_no_ship = base(quote_files=[{}], approval={"done_at": "x"}, po=po, payments=[{"simple": True}])
check(flow.stage(paid_no_ship) == "배송 대기", "송금 뒤 배송 시작 전 → 배송 대기", flow.stage(paid_no_ship))
# 잔금이 남았으면 입고해도 완료 아님(권장안 — 입고만으로 완료하지 않음)
left = base(quote_files=[{}], approval={"done_at": "x"}, po=po, receipt=REC_ALL,
            pay_terms=[{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70}], payments=[{"term": 0, "date": "2026-11-03"}])
check(flow.stage(left) == "송금 대기", "입고 끝나도 잔금 남으면 완료 아님", flow.stage(left))
# 거래명세서·세금계산서 상태(첨부·선결제·선발행·미등록)
check(flow.doc_state(base(statement={"files": [{}]}), "statement")["state"] == "file", "명세서 첨부 → 첨부")
check(flow.doc_state(base(tax_invoice={"mode": "pre_issued", "done_at": "x", "note": "10월"}), "tax") == {
    "state": "skip", "label": "선발행", "mode": "pre_issued", "note": "10월"}, "계산서 선발행 기록")
check(flow.doc_state(base(statement={"mode": "prepaid_balance", "done_at": "x"}), "statement")["label"] == "선결제 잔액", "예전 '선결제 잔액' 기록")
check(flow.doc_state(base(), "tax")["state"] == "none", "아무 기록 없으면 미등록")
d = base(dropped=True, quote_files=[{}])
check(flow.stage(d) == "취소" and not any(x["ready"] for x in flow.steps(d)), "취소 건은 아무 단계도 못 함")

# ── 결제 회차 ──
d = base(po={**po, "total": 1_000_001}, pay_terms=[{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70, "expected_date": "2026-10-01"}])
ts = flow.terms(d)
check(ts[0]["amount"] == 300_000 and ts[1]["amount"] == 700_001, "회차 금액 — 마지막이 끝전", str([t["amount"] for t in ts]))
check(ts[0]["amount_vat"] == 330_000, "부가세 포함 금액")
check(len(flow.overdue(d, TODAY)) == 1 and flow.alerts(d, TODAY)[0]["code"] == "OVERDUE", "예정일 지난 회차 알림")
d["payments"] = [{"term": 0, "date": "2026-10-01", "mismatch": True}, {"term": 1, "date": "2026-10-02"}]
ts = flow.terms(d)
check(all(t["confirmed"] for t in ts) and ts[0]["mismatch"] and not flow.overdue(d, TODAY), "확인 → 알림 없음, 불일치 기록")
check(any(a["code"] == "PAY_MISMATCH" for a in flow.alerts(d, TODAY)), "불일치로 넘긴 회차 알림")
d.update(quote_files=[{}], approval={"done_at": "x"}, receipt=REC_ALL)
check(flow.stage(d) == "완료", "모두 끝나면 완료")
check(not any(a["code"] == "NO_FILE" for a in flow.alerts(d, TODAY)), "명세서·계산서 미등록은 알림 아님(선택 서류)")
# ── 입고 기록 · 입고 예정일 · 해외 ──
part = base(po=po, receipt={"records": [{"lines": [{"line_no": 0, "qty": 1}]}]})
check(not flow.receipt_done(part) and flow.received(part) == {0: 1.0}, "부분 입고는 아직 미완료")
check({x["key"]: x for x in flow.steps({**part, "quote_files": [{}], "approval": {"done_at": "x"}})}["receipt"]["note"] == "부분 입고 1회",
      "부분 입고 표시")
check(flow.receipt_done(base(po=po, receipt={"records": [{"lines": [], "final": True}]})), "'입고 완료'로 남기면 끝")
late = base(quote_files=[{}], approval={"done_at": "x"}, po={**po, "expected_date": "2026-10-30"})
check(any(a["code"] == "RECEIPT_DUE" and "2026-10-30" in a["text"] for a in flow.alerts(late, TODAY)), "입고 예정일 지나면 알림")
check(not any(a["code"] == "RECEIPT_DUE" for a in flow.alerts({**late, "receipt": REC_ALL}, TODAY)), "입고 끝나면 알림 없음")
ov = base(region="overseas", quote_files=[{}], approval={"done_at": "x"}, po=po)
check([x["key"] for x in flow.steps(ov)] == ["quote", "approval", "order", "pay", "ship", "customs", "broker", "receipt"],
      "해외 단계 — 제조사 송금·배송·통관 서류·관세사 송금·입고, 명세서·계산서 없음")
check({x["key"]: x for x in flow.steps(ov)}["pay"]["label"] == "제조사 송금" and flow.stage(ov) == "제조사 송금 대기",
      "해외 송금은 '제조사 송금'", flow.stage(ov))
check(all(x["ready"] for x in flow.steps(ov)[3:]), "해외도 발주 뒤 순서 자유")
ov_nodue = {**ov, "po": {k: v for k, v in po.items() if k != "expected_date"}}
check(flow.stage(ov_nodue) == "제조사 송금 대기" and flow.done_map(ov_nodue)["order"], "해외는 납기일 없이도 발주 끝(선택 — 사용자 2026-11-11)")
ov2 = {**ov, "payments": [{"simple": True}], "receipt": {"shipping": {"date": "2026-11-05"}}}
check(flow.stage(ov2) == "배송 중" and flow.stage_note(ov2) == "통관 서류 · 관세사 송금 남음", "해외 배송 중 — 통관 서류·관세사 송금 남음",
      flow.stage(ov2) + "/" + flow.stage_note(ov2))
ov3 = {**ov2, "customs": {"done_at": "x"}, "receipt": {**ov2["receipt"], **REC_ALL}}
check(flow.stage(ov3) == "관세사 송금 대기", "통관 서류 제출(첨부 없이도)·입고 → 관세사 송금 대기", flow.stage(ov3))
check(flow.stage({**ov3, "customs": {"done_at": "x", "broker": {"date": "2026-11-09"}}}) == "완료", "해외 관세사 송금까지 → 완료")
try:
    flow.clean_terms([{"label": "선금", "pct": 50}, {"label": "잔금", "pct": 40}])
    check(False, "비율 합 100 아니면 거절")
except ValueError:
    check(True, "비율 합 100 아니면 거절")

# ── 지출내역 대조 ──
check(ec.norm_name("㈜크레켄") == ec.norm_name("주식회사 크레켄") == ec.norm_name("(주) 크레켄") == "크레켄", "회사 이름 표기 통일")
wb = openpyxl.Workbook()
ws = wb.active
ws.append(["일자", "거래처", "적요", "출금액"])
ws.append([dt.date(2026, 10, 20), "주식회사 크레켄", "커넥터 선금", 1_188_000])
ws.append([dt.date(2026, 10, 21), "다른회사", "기타", 500_000])
buf = io.BytesIO()
wb.save(buf)
rows, imgs = ec.read_rows(buf.getvalue(), "지출.xlsx", "")
check(len(rows) == 3 and not imgs and 1_188_000 in rows[1]["amounts"], "엑셀 지출내역 줄 읽기", str(rows[:2]))
r = ec.judge(rows, "㈜크레켄", 1_080_000, 1_188_000)
check(r["status"] == "ok" and r["match"]["basis"] == "vat", "공급사 + 부가세 포함 금액 → 일치", r["summary"])
r = ec.judge(rows, "㈜크레켄", 1_000_000, 1_100_000)
check(r["status"] == "mismatch" and r["reason"] == "amount", "공급사 줄은 있는데 금액 다름 → 불일치", r["summary"])
r = ec.judge(rows, "㈜나우티앤에스", 500_000, 550_000)
check(r["status"] == "mismatch" and r["reason"] == "supplier", "금액만 맞고 거래처 다름 → 불일치", r["summary"])
r = ec.judge(rows, "없는회사", 7_777_777, 8_555_555)
check(r["status"] == "mismatch" and r["reason"] == "none", "아무것도 없음 → 불일치")
r = ec.judge(rows, "㈜크레켄 구미지점", 1_080_000, 1_188_000)
check(r["status"] == "ok", "등록 이름이 더 길어도(지점명) 지출내역 셀 이름으로 일치", r["summary"])
check(ec.judge([{"text": "a | 크레 | 1,188,000", "amounts": [1_188_000]}], "크레켄", 1, 1_188_000)["reason"] == "supplier",
      "2자짜리 조각은 공급사로 보지 않음")
r = ec.judge(rows, "크레켄", 1_188_500, 1_307_350)
check(r["status"] == "ok", "1,000원 차이까지는 일치(송금 수수료)", r["summary"])
csv_rows, _ = ec.read_rows("일자,거래처,금액\n2026-10-20,㈜크레켄,\"1,080,000\"\n".encode("cp949"), "x.csv", "")
check(ec.judge(csv_rows, "크레켄", 1_080_000, 1_188_000)["status"] == "ok", "CSV(cp949) 지출내역", str(csv_rows))
check(ec._nums("2026-11-03 | 616101-04-395959 | 2026.11.03 | 1,080,000 | 500000") == [1_080_000, 500_000], "날짜·계좌 조각은 금액 아님",
      str(ec._nums("2026-11-03 | 616101-04-395959 | 2026.11.03 | 1,080,000 | 500000")))
res = asyncio.run(ec.run(b"not a real file", "a.hwp", "", "크레켄", 1, 1))
check(res["status"] == "unreadable", "읽을 수 없는 형식은 unreadable", res["summary"])


async def fake_rows(*a, **k):
    return json.dumps({"rows": [{"date": "2026-10-20", "payee": "크레켄", "memo": "선금", "amount": "1,080,000"}]})

ec.proposal_llm.chat = fake_rows
res = asyncio.run(ec.run(b"\x89PNG....", "scan.png", "image/png", "㈜크레켄", 1_080_000, 1_188_000))
check(res["status"] == "ok" and res["source"] == "image", "사진 지출내역은 AI 로 줄을 읽고 코드가 판정", str(res))

# ── 발주서 엑셀 ──
H = {"no": "UND-LU20261102-01", "date": "2026-11-02", "supplier": {"name": "㈜크레켄", "contact": "조용경 과장님", "email": "sales@creken.co.kr", "tel": "010-1"},
     "issuer": {"name": "이승학", "title": "주임", "address": "주소", "tel": "82-10-1", "fax": "82-54-1", "attn": "Lucas (l@u)"},
     "comments": "납기 2주"}
items = [{"commodity": "M8 커넥터", "description": "300mm", "qty": 200, "price": 10800, "moq": None},
         {"description": "케이블", "qty": 2, "price": 5000.5, "moq": 10}]
check(px.total(items) == 2_160_000 + 10_001, "합계(줄마다 반올림)", str(px.total(items)))
check(px.total([{"qty": 3, "price": 10.335}], "USD") == 31.01, "달러는 센트 단위", str(px.total([{"qty": 3, "price": 10.335}], "USD")))
check(px.check(H, []) == ["품목"] and "1번 품목 수량" in px.check(H, [{"description": "a", "qty": 0, "price": 1}]), "빠진 값 검사")
wb = openpyxl.load_workbook(io.BytesIO(px.build_xlsx(H, items)))
ws = wb.active
check(ws["E2"].value == "UND-LU20261102-01" and ws["A21"].value == "M8 커넥터" and ws["B21"].value == "300mm"
      and not ws["A22"].value and ws["B22"].value == "케이블" and ws["G21"].value == "=PRODUCT(E21:F21)"
      and ws["G23"].value == "=SUM(G21:G22)" and ws["A23"].value == "TOTAL", "양식 칸 위치·수식(Commodity·Description 나눔)")
check(ws["A20"].value == "Commodity" and ws["B20"].value == "Description", "품목 머리 — Commodity·Description(에이딘 예시)")
check(ws["A4"].value == "㈜크레켄\n조용경 과장님\nTel : 010-1\ne-mail : sales@creken.co.kr", "공급사 칸 표기(담당자·Tel)", repr(ws["A4"].value))
check("㈜ 유엔디로보틱스 이승학 주임" in str(ws["A12"].value) and "ATTN : Lucas (l@u)" in str(ws["A12"].value), "발주처 칸 표기(이름 붙여 씀)")
check(ws["E25"].value == "UND Robotics", "서명란 회사 표기 UND Robotics", repr(ws["E25"].value))
check(px.item_name({"commodity": "", "description": "A\nB"}) == "A" and px.item_name({"commodity": "X", "description": "A"}) == "X",
      "품목 이름 — Commodity, 없으면 Description 첫 줄")
check(ws["E24"].value.strip().startswith("Signed by President") and ws["A32"].value == "THANK YOU FOR YOUR BUSINESS!", "서명란·인사말")
check(len(ws._images) == 4, "그림 4개(제목·로고·서명·직인)", str(len(ws._images)))
check(str(ws.page_setup.paperSize) == "9" and "$A$1:$G$32" in str(ws.print_area), "A4·인쇄 영역", f"{ws.page_setup.paperSize} {ws.print_area}")
try:
    px.build_xlsx({**H, "supplier": {"name": ""}}, items)
    check(False, "공급사 없으면 발행 거절")
except px.POError as e:
    check("공급사" in str(e), "공급사 없으면 발행 거절")
check(px.file_name("㈜크/레켄", "UND-LU20261102-01") == "유엔디로보틱스_발주서_㈜크 레켄_UND-LU20261102-01", "파일명 — 못 쓰는 글자 제거")
check(px.file_name("Autoxing", "PO-AL20261102-01", "en") == "유엔디로보틱스_Autoxing_Purchasing_PO-AL20261102-01", "해외 파일명")
HE = {"lang": "en", "no": "PO-AL20261102-01", "date": "2026-11-02",
      "supplier": {"name": "Guanzhou Autoxing", "contact": "", "email": "echo@autoxing.com", "tel": "(+86)136"},
      "swift": "BKCHCNBJ", "issuer": {"name": "Alex Jeon", "title": "Mr.", "address": "116-72, Sanho-daero, Gumi-si, Republic of Korea (39377)",
                                      "tel": "82-10-1", "attn": "Mr. Alex Jeon (alex@unde.co.kr)"}}
wb = openpyxl.load_workbook(io.BytesIO(px.build_xlsx(HE, [{"description": "AX8114\n(S300)", "qty": 2, "price": 4600, "remark": "Sea"}])))
ws = wb.active
# 해외 = LHTC 해외 발주서 기준(사용자 2026-11-11), TOTAL 색·Commodity/Description 두 칸은 국내와 같게
check(ws["A4"].value == "Guanzhou Autoxing\nTo : echo@autoxing.com\nTel : (+86)136" and ws["D6"].value == "Swift Code"
      and ws["E6"].value == "BKCHCNBJ" and ws["E2"].value == "PO-AL20261102-01" and ws["A12"].value == "Commodity"
      and ws["B12"].value == "Description" and ws["B13"].value == "AX8114\n(S300)" and ws["E13"].value == "=C13*D13"
      and ws["F13"].value == "Sea" and ws["E14"].value == "=SUM(E13:E13)" and "US$" in ws["D13"].number_format
      and ws["E4"].font.i and ws["E4"].font.sz == 14.5, "해외 양식 칸·수식·달러(LHTC 양식)")
check(ws["A14"].value == "TOTAL" and ws["E14"].fill.fgColor.rgb == "FFF2F2F2" and ws["E14"].font.color.rgb == "FFFF0000",
      "해외 TOTAL 줄 — 회색 바탕·빨간 글씨(국내와 같게)")
check(" UND Robotics Co., Ltd." in ws["A10"].value and " Republic of Korea (39377)" in ws["A10"].value.split("\n")
      and ws["A10"].value.endswith(" Mr. Alex Jeon (alex@unde.co.kr)"), "해외 발주처 칸(국가명 앞 줄바꿈)", repr(ws["A10"].value))
anchors = sorted((im.anchor._from.row, im.width) for im in ws._images)
check(len(anchors) == 2 and anchors[1][0] == 17 and ws.print_area.endswith("$G$19"),
      "해외 그림 2개(제목·서명+직인), 서명은 서명 줄(18행, 0부터 17) 안 — 인쇄 영역 안", f"{anchors} {ws.print_area}")
t = po_read_mod.read_template(px.build_xlsx(HE, [{"description": "AX8114\n(S300)", "qty": 2, "price": 4600, "remark": "Sea"}]))
check(t is not None and t["doc_no"] == "PO-AL20261102-01" and t["items"][0]["remark"] == "Sea" and t["items"][0]["price"] == 4600,
      "해외 양식 엑셀도 다시 읽기(고친 파일로 발행)", str(t))

# ── 견적서 판독(가짜 AI) ──
check(qr.description("TC700", "LTE Router\n전원 어답터 포함") == "LTE Router\n전원 어답터 포함", "Description 칸 — 규격 줄(품명은 Commodity)")

# ── 고친 발주서 읽기(사용자 2026-11-11) — 회사 양식 엑셀은 AI 없이 칸 이름으로 ──
from app.purchases import po_read  # noqa: E402
t = po_read.read_template(px.build_xlsx(H, items))
check(t is not None and t["doc_no"] == "UND-LU20261102-01" and t["date"] == "2026-11-02"
      and [(x["commodity"], x["description"], x["qty"], x["price"]) for x in t["items"]]
      == [("M8 커넥터", "300mm", 200, 10800), ("", "케이블", 2, 5000.5)], "양식 엑셀 → 번호·날짜·품목", str(t))
wb = openpyxl.load_workbook(io.BytesIO(px.build_xlsx(H, items)))
ws = wb.active
ws["E2"] = "UND-LU20261102-07"
ws["E21"] = 150
ws["A20"] = "Commodity & Description"                  # 예전 양식 — 한 칸
ws["A22"] = "케이블\n- 5m"
ws["B22"] = None
buf = io.BytesIO()
wb.save(buf)
t = po_read.read_template(buf.getvalue())
check(t["doc_no"] == "UND-LU20261102-07" and t["items"][0]["qty"] == 150
      and (t["items"][1]["commodity"], t["items"][1]["description"]) == ("케이블", "5m"), "고친 칸·예전 한 칸 양식 나눠 읽기", str(t))
wb = openpyxl.Workbook()
wb.active.append(["아무 표"])
buf = io.BytesIO()
wb.save(buf)
check(po_read.read_template(buf.getvalue()) is None, "양식이 아닌 엑셀은 None(AI 판독으로)")
# 예전 양식 원본(사용자 2026-11-11) — 'Commodity & Description' 한 칸·'Quotation No.'·수량 '1ea'·머리 칸
EX = ROOT / "docs" / "examples" / "quotes"
if EX.exists():
    t = po_read.read_template((EX / "유엔디로보틱스_LHTC_Purchasing_Lu20261008.xlsx").read_bytes())
    check(t["doc_no"] == "UND-Lu20261008-01" and t["date"] == "2026-10-08"
          and [(x["commodity"], x["qty"], x["price"]) for x in t["items"][:2]] == [("LMO-E04-S11-M", 1, 133.82), ("LMO-E04-S11-T", 3, 80.29)],
          "LHTC 예전 해외 양식 — Quotation No.·수량 '1ea'·품목", str(t["items"][:2]))
    h = t["header"]
    check(h["supplier"]["name"] == "Zhengzhou Linghang Robot Co.,Ltd." and h["supplier"]["tel"] == "(+86) 15333867590"
          and h["swift"] == "BKCHCNBJ530" and h["account_no"] == "250790357823" and h["bank"].startswith("BANK OF CHINA"),
          "LHTC 머리 칸(공급사·Bank·Swift·Account No.)", str(h))
    t = po_read.read_template((EX / "유엔디로보틱스_Autoxing_Purchasing_Lu20260102.xlsx").read_bytes())
    check((t["items"][0]["commodity"], t["items"][0]["description"]) == ("AX8114", "（S300）") and t["header"]["supplier"]["email"] == "echo@autoxing.com",
          "Autoxing 예전 양식 — 모델명/(규격) 나누기·To 메일", str(t["items"][0]))
    t = po_read.read_template((EX / "(쥬)유엔디로보틱스_발주서_크레켄_Lu260808 (1).xlsx").read_bytes())
    it = t["items"][0]
    check(t["doc_no"] == "UND-Lu20260808" and it["commodity"] == "" and it["description"].startswith("M8-8핀 패널마운트 커넥터")
          and it["qty"] == 200 and t["header"]["supplier"]["name"] == "㈜크레켄",
          "크레켄 예전 국내 양식 — 칸 폭에서 줄바꿈된 긴 품명은 통째로 Description", str(it))
if EX.exists():
    # 예전 발주서 PDF 머리 칸 — 글자 위치로(사용자 2026-11-11)
    h = po_read.read_pdf_header((EX / "유엔디로보틱스_발주서_에이딘로보틱스_PO_VT261102_1.pdf").read_bytes())
    check(h["doc_no"] == "UND-VT20261102-01" and h["date"] == "2026-11-02" and h["header"]["supplier"]["name"] == "㈜에이딘로보틱스"
          and h["header"]["supplier"]["tel"] == "010-9099-9438" and h["header"]["comments"].startswith("6축 F/T 센서 구매의 건"),
          "PDF 머리 칸 — 에이딘로보틱스(번호·날짜·공급사·Tel·Comments)", str(h))
    h = po_read.read_pdf_header((EX / "(쥬)유엔디로보틱스_발주서_(주)나우티앤에스_Lu261013.pdf").read_bytes())
    check(h["doc_no"] == "UND-Lu20261013" and h["header"]["supplier"] == {"name": "㈜나우티앤에스", "contact": "권창수 부장님",
          "email": "row@nwtns.com", "tel": ""} and "address" not in h["header"], "PDF 머리 칸 — 나우티앤에스(끝 번호 없는 예전 번호, Address '-' 는 빈 값)", str(h))
    check(po_read.read_pdf_header(b"%PDF-1.4 broken") == {}, "깨진 PDF 는 머리 칸 없이")
from app.sales_deals import quote_upload as qu  # noqa: E402
lz = qu.clean_items([{"name": "", "spec": "", "qty": None, "unit_price": None}, {"name": "A", "spec": "x", "qty": None, "unit_price": 5}],
                    check_total=False, lenient=True)
check(lz == [{"name": "A\n- x", "qty": 1, "unit_price": 5}], "견적서 [버그 신고 후 넘어가기] — 빈 줄 버림, 수량 없으면 1", str(lz))
check(qu.clean_items([], lenient=True) == [{"name": qu.LENIENT_NAME, "qty": 1, "unit_price": 0}], "품목이 하나도 없으면 '품목 미확인' 한 줄(0원)")
check(po_read.split_merged("AX8114\n(S300)") == ("AX8114", "(S300)") and po_read.split_merged("커넥터 M8\n- 300mm") == ("커넥터 M8", "300mm")
      and po_read.split_merged("아주 긴 품명이 칸 폭에서 줄바꿈되어 단어 중간이 잘린 경우의 예시 문장 커\n넥터")[0] == "",
      "한 칸 나누기 규칙(모델명·'- 규격'·잘린 긴 품명)")
check(po_read._num("1ea") == 1 and po_read._num("3 pcs") == 3 and po_read._num("2개") == 2 and po_read._num("US$1,200.50") == 1200.5,
      "수량 글('1ea'·'3 pcs'·'2개')·달러 금액")
check(po_read.NO_RE.search("Order No. 2026-11-02 UND-VT20261102-01").group(1) == "UND-VT20261102-01"
      and po_read._date("Order Date 2026-11-02") == "2026-11-02", "PDF 글자에서 번호·날짜 찾기")
wb = openpyxl.Workbook()
ws = wb.active
ws.append(["견적서", "㈜나우티앤에스"])
ws.append(["담당", "권창수 부장", "row@nwtns.com"])
ws.append(["품명", "규격", "수량", "단가"])
ws.append(["TC700", "LTE Router", 8, 95000])
buf = io.BytesIO()
wb.save(buf)


async def fake_quote(*a, **k):
    return json.dumps({"items": [{"name": "TC700", "spec": "LTE Router", "moq": None, "qty": 8, "unit_price": 95000},
                                 {"name": "지어낸 품목", "spec": "", "qty": 3, "unit_price": 123456}],
                       "supplier": "㈜나우티앤에스", "contact": "권창수 부장", "email": "row@nwtns.com", "vat_included": False})

qr.proposal_llm.chat = fake_quote
res = asyncio.run(qr.read(buf.getvalue(), "견적.xlsx", ""))
check(res["status"] == "ok" and res["items"][0] == {"commodity": "TC700", "description": "LTE Router", "moq": None, "qty": 8.0, "price": 95000.0},
      "견적서 품목 읽기", str(res["items"]))
async def fake_moq(*a, **k):
    return json.dumps({"items": [{"name": "TC700", "spec": "", "moq": 8, "qty": 8, "unit_price": 95000}]})

qr.proposal_llm.chat = fake_moq
check(asyncio.run(qr.read(buf.getvalue(), "견적.xlsx", ""))["items"][0]["moq"] is None, "빈 MOQ 칸에 넣은 주문 수량은 비움")
check(res["items"][1]["qty"] is None and res["items"][1]["price"] is None and "비워" in res["summary"], "원문에 없는 숫자는 비움")
check(res["supplier"] == {"name": "㈜나우티앤에스", "contact": "권창수 부장", "email": "row@nwtns.com"}, "공급사 담당자")

# ── 발주서 AI 대화(사용자 2026-11-11) — 작업 적용은 코드, 숫자는 말한 것만 ──
from app.purchases import po_chat as pc  # noqa: E402
DR = {"initials": "", "expected_date": "", "header": {"date": "2026-11-11", "supplier": {"name": "㈜에이딘로보틱스"}, "issuer": {"name": "권성하"}},
      "items": [{"commodity": "AFT2000-D80-C", "description": "200N", "qty": 1, "price": 1500000}]}
ops = [{"op": "add_item", "commodity": "AFT300", "description": "케이블", "qty": 2, "price": 99999},
       {"op": "set_item", "no": 1, "qty": 3},
       {"op": "set", "field": "supplier.contact", "value": "강종구 팀장"},
       {"op": "set", "field": "supplier.tel", "value": "010-9099-9438"},
       {"op": "set", "field": "expected_date", "value": "2026-11-20"},
       {"op": "set", "field": "initials", "value": "vt"},
       {"op": "set", "field": "comments", "value": "센서 구매의 건\\n납품처 : 구미"},
       {"op": "set", "field": "hack", "value": "x"},
       {"op": "remove_item", "no": 9}]
new, notes, rq = pc.apply_ops(DR, ops, "AFT300 케이블 2개 추가, 1번 수량 3개로")
check(new["items"][1] == {"commodity": "AFT300", "description": "케이블", "remark": "", "qty": 2.0, "price": None, "moq": None}
      and new["items"][0]["qty"] == 3.0, "말한 숫자만(수량 2·3), 말 안 한 단가 99999 는 버림", str(new["items"]))
check(any("말씀에 없는 숫자" in n for n in notes) and any("9번 품목이 없습니다" in n for n in notes), "버린 숫자·없는 줄 알림", str(notes))
h = new["header"]
check(h["supplier"]["contact"] == "강종구 팀장" and h["supplier"]["tel"] == "010-9099-9438" and new["expected_date"] == "2026-11-20"
      and new["initials"] == "VT" and h["comments"] == "센서 구매의 건\n납품처 : 구미" and "hack" not in h and not rq,
      "머리 칸·날짜·이니셜·줄바꿈, 모르는 칸은 무시")
check(DR["items"][0]["qty"] == 1, "원본 초안은 그대로")
new, notes, rq = pc.apply_ops(DR, [{"op": "read_quote"}, {"op": "set", "field": "expected_date", "value": "다음주"}], "견적서에서 불러와 줘")
check(rq and any("날짜" in n for n in notes), "견적서 불러오기 요청·알아듣지 못한 날짜 알림")
new, _, _ = pc.apply_ops(DR, [{"op": "add_item", "commodity": "X", "qty": 1, "price": 1500000}], "X 1개 150만원 추가")
check(new["items"][-1]["price"] == 1500000.0 and new["items"][-1]["qty"] == 1.0, "'150만원' → 1,500,000")


async def fake_chat(messages, **kw):  # noqa: ANN001, ANN003
    assert "[지금 발주서]" in messages[1]["content"] and "AFT2000-D80-C" in messages[1]["content"]
    return json.dumps({"ops": [{"op": "set_item", "no": 1, "price": 1400000}], "reply": "1번 단가를 140만원으로 바꿨습니다."})
pc.proposal_llm.chat = fake_chat
res = asyncio.run(pc.run(DR, "1번 단가 140만원", dt.date(2026, 11, 11)))
check(res["draft"]["items"][0]["price"] == 1400000.0 and res["reply"].startswith("1번 단가") and not res["read_quote"], "대화 한 번(가짜 AI)", str(res))


async def broken(messages, **kw):  # noqa: ANN001, ANN003
    return "not json"
pc.proposal_llm.chat = broken
res = asyncio.run(pc.run(DR, "아무 말", dt.date(2026, 11, 11)))
check(res["draft"] == DR and "다시" in res["reply"], "AI 실패 — 초안 그대로·안내")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
