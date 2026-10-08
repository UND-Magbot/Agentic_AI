"""견적서 작성 대화(company_knowledge/quote_session.py) 점검 — 초안·질문 순서·말 읽기·숫자 검증·직접 수정·발행 검사·엑셀 줄.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts -v ./docs:/docs backend \\
        python /scripts/test_quote_session.py
"""
from __future__ import annotations

import io
import sys
from datetime import date

import openpyxl

from app.company_knowledge import product_prices as pp
from app.company_knowledge import quote_session as qs
from app.company_knowledge import quote_xlsx as qx

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


rows = pp.parse_book()


def acc(model: str = "TCV1") -> dict:
    items = [
        {"name": f"{model} MASTER T.C", "code": None, "qty": 1, "price_model": model, "price_item": "MASTER T.C", "optional": False},
        {"name": "IB (Interface Bracket)", "code": None, "qty": 1, "price_model": model, "price_item": "IB", "optional": True},
        {"name": f"{model} T.P", "code": None, "qty": 2, "price_model": model, "price_item": "T.P", "optional": False},
        {"name": "Pogo Pin Male (PPM)", "code": "PPM", "qty": 2, "price_model": model, "price_item": "PPM/PPF", "optional": False},
        {"name": "Pogo Pin Female (PPF)", "code": "PPF", "qty": 2, "price_model": model, "price_item": "PPM/PPF", "optional": False},
        {"name": "Cable 0.3m", "code": None, "qty": 4, "price_model": model, "price_item": "Cable 0.3m", "optional": False},
    ]
    a = {"applicable": True, "items": items}
    pp.attach_prices(items, rows)
    return a


# 1) 초안 — 추천 구성품 그대로, 체크 안 한 IB 는 넣을 수 있는 옵션으로
st = qs.draft_state(acc(), model="TCV1", payload_kg=10, form={"customer": "", "initials": "VT"})
check([ln["key"] for ln in st["lines"]] == ["MASTER T.C", "T.P", "PPM", "PPF", "Cable 0.3m"] and [o["key"] for o in st["options"]] == ["IB"]
      and qs.subtotal(st) == 2_300_000 + 800_000 + 500_000 + 500_000 + 200_000 and st["form"]["delivery"] == "통상 3 ~ 4주"
      and st["form"]["initials"] == "VT",
      "초안: 추천 품목·단가표 가격, IB 는 옵션 목록, 조건 기본값, 미리 채운 담당자 이니셜", str(qs.subtotal(st)))

# 2) 질문 순서 — 고객사 → 납품 방식(4가지) → 물류비 / 설치 인원·일수 → 1인 1일 단가 → 추가 항목 → 담당자
check(qs.next_question(st)["id"] == "customer", "첫 질문: 고객사")
st, done = qs.apply_ops(st, [{"op": "set", "field": "customer", "value": "가나테크"}, {"op": "set", "field": "to", "value": "김철수 대표님"}],
                        "가나테크, 김철수 대표님")
q = qs.next_question(st)
check(st["form"]["customer"] == "가나테크" and q["id"] == "delivery" and len(q["choices"]) == 5 and len(done) == 2
      and q["choices"][-1] == "5. 별도 협의",
      "고객사 반영 → 다음은 납품 방식(선택 버튼 5개, 5. 별도 협의)", str(q))

# 납품 방식 4가지 — 품목 줄이 방식에 맞춰진다(영업팀 기준: 1 그대로 / 2 물류비 / 3 인건비 / 4 물류비+인건비)
base = qs.subtotal(st)
check([qs.delivery_case(x) for x in ("1", "2번", "3. UND 인원 설치 납품", "4. 화물 발송 + UND 인원 설치", "택배로 보내요", "화물로 직납",
                                     "저희 인원이 설치하면서 납품", "화물로 보내고 설치하러 가요", "글쎄요")] == [1, 2, 3, 4, 1, 2, 3, 4, None],
      "납품 방식 읽기: 번호·버튼 글·말 → 1~4, 모호하면 묻기")
check([qs.delivery_case(x) for x in ("화물은 안 써요, 저희가 직접 가서 설치해요", "택배 말고 화물로", "설치 없이 화물로만 보내요",
                                     "택배로 보내고 설치는 안 해요")] == [3, 2, 2, 1],
      "부정 표현: '화물은 안 써요'·'택배 말고'·'설치 없이'·'설치는 안 해요'를 반대로 읽지 않음")
check([qs.delivery_case(x) for x in ("5", "5. 별도 협의", "납품은 별도 협의할게요", "추후 협의")] == [5, 5, 5, 5],
      "5 별도 협의: 번호·버튼 글·말")
c5, _ = qs.apply_ops(st, qs.quick_ops(st, "5. 별도 협의"), "5. 별도 협의")
check(qs.subtotal(c5) == base and not any(ln["kind"] in ("freight", "work") for ln in c5["lines"])
      and qs.next_question(c5)["id"] == "extra" and not any("납품 방식" in x for x in qs.problems(c5)),
      "5 별도 협의: 품목 그대로(물류비·인건비 없음), 다음 질문으로")
c1, _ = qs.apply_ops(st, qs.quick_ops(st, "1. 택배 발송"), "1. 택배 발송")
check(qs.subtotal(c1) == base and not any(ln["kind"] in ("freight", "work") for ln in c1["lines"])
      and qs.next_question(c1)["id"] == "extra", "1 택배: 품목 그대로, 바로 추가 항목 질문")
PLACE = "직납 및 배차 비용 별도\n(화물 비용 별도)"
check(st["form"]["place"] == PLACE and all(c["form"]["place"] == PLACE for c in (c1,)),
      "납품장소 = '직납 및 배차 비용 별도 (화물 비용 별도)' — 초안부터, 방식과 관계없이")
c2, _ = qs.apply_ops(st, qs.quick_ops(st, "2"), "2")
check(qs.next_question(c2)["id"] == "freight" and "물류비 금액 필요" in qs.problems(c2), "2 화물 직납: 물류비 금액을 묻는다")
c2, _ = qs.apply_ops(c2, qs.quick_ops(c2, "15만원이요"), "15만원이요")
check(qs.subtotal(c2) == base + 150_000 and any(ln["kind"] == "freight" and ln["unit_price"] == 150_000 for ln in c2["lines"])
      and c2["form"]["place"] == PLACE and qs.next_question(c2)["id"] == "extra",
      "2 화물 직납: 물류비 150,000원 줄, 납품장소 문구는 그대로")
c3, _ = qs.apply_ops(st, qs.quick_ops(st, "3번 2명 3일"), "3번 2명 3일")
check(qs.next_question(c3)["id"] == "extra" and not any(ln["kind"] == "freight" for ln in c3["lines"])
      and any(ln["kind"] == "work" and ln["qty"] == 6 and ln["unit_price"] == qs.WORK_RATE for ln in c3["lines"])
      and qs.subtotal(c3) == base + 6 * 800_000,
      "3 설치 납품: '3번 2명 3일' → 인건비 6 인·일 × 회사 기준 80만원(1인 8H), 단가는 묻지 않음")
check(any(ln["key"] == "work" and "1인 8H 기준" in ln["remark"] for ln in qs.render_lines(c3, "ko", None)), "견적서 비고에 '1인 8H 기준'")
c3, _ = qs.apply_ops(c3, [{"op": "work", "rate": 500000}], "인건비 단가 50만원으로 해 줘")
check(qs.subtotal(c3) == base + 3_000_000 and qs.next_question(c3)["id"] == "extra"
      and not any("8H" in ln["remark"] for ln in qs.render_lines(c3, "ko", None) if ln["key"] == "work"),
      "말하면 단가 바꿀 수 있음: 2명×3일×50만원 = 3,000,000원(기준 아님 → 비고에 8H 안 붙음)")
c4, _ = qs.apply_ops(st, qs.quick_ops(st, "화물로 보내고 설치하러 가요"), "화물로 보내고 설치하러 가요")
for m in ("10만원", "1명 2일"):
    c4, _ = qs.apply_ops(c4, qs.quick_ops(c4, m), m)
check(qs.subtotal(c4) == base + 100_000 + 1_600_000 and {ln["kind"] for ln in c4["lines"]} >= {"freight", "work"}
      and qs.next_question(c4)["id"] == "extra", "4 화물 + 설치: 물류비 100,000 + 인건비 1명×2일×80만원(회사 기준)")
# 대화로 방식 바꾸기 — 설치 빼기(4→2), 물류비 추가(1→2), 인건비 줄 삭제(3→1)
c4b, d4 = qs.apply_ops(c4, [{"op": "work", "needed": False}], "설치는 빼 주세요")
check(c4b["delivery"]["case"] == 2 and not any(ln["kind"] == "work" for ln in c4b["lines"])
      and any(ln["kind"] == "freight" for ln in c4b["lines"]), "'설치는 빼 주세요' → 4에서 2(화물 직납)로", str(d4))
c1b, _ = qs.apply_ops(c1, [{"op": "add", "name": "물류비", "qty": 1, "unit_price": 80000}], "물류비 8만원 추가")
check(c1b["delivery"]["case"] == 2 and qs.subtotal(c1b) == base + 80_000 and not any(ln["kind"] == "extra" for ln in c1b["lines"]),
      "택배(1)에 '물류비 8만원 추가' → 화물 직납(2)으로, 물류비 줄 하나")
wl = next(ln for ln in c3["lines"] if ln["kind"] == "work")
c3b, _ = qs.apply_ops(c3, [{"op": "remove", "line": wl["id"]}], "인건비 빼 줘")
check(c3b["delivery"]["case"] == 1 and qs.subtotal(c3b) == base, "인건비 줄 삭제 → 3에서 1(택배)로")

st = c4
st, _ = qs.apply_ops(st, [{"op": "add", "name": "교육비", "qty": 1, "unit_price": 100000}], "교육비 10만원 추가해 줘")
check(any(ln["name"] == "교육비" and ln["kind"] == "extra" for ln in st["lines"]) and qs.next_question(st)["id"] == "contact",
      "대화 '교육비 10만원 추가' → 추가 항목 줄, 추가 항목 질문 끝, 다음은 담당자")
st2, done2 = qs.apply_ops(st, [{"op": "add", "name": "출장비", "qty": 1, "unit_price": 300000}], "출장비도 넣어 줘")
check(not any(ln["name"] == "출장비" for ln in st2["lines"]) and not done2, "말에 없는 금액(30만원)은 AI 가 지어내도 넣지 않음")

early = qs.draft_state(acc(), model="TCV1", payload_kg=10, form={"customer": "가나테크"})       # 아직 납품 방식 질문 단계
guard, gd = qs.apply_ops(early, [{"op": "set", "field": "contact_name", "value": "김철수"}], "김철수 대표님께 보낼 거예요")
check(not gd and not guard["form"]["contact_name"], "고객 쪽 받는 분을 AI 가 담당자로 넣어도 막음(담당자 질문 전·'담당' 말 없음)")

# 3) 품목 수정·삭제·옵션
tp = next(ln for ln in st["lines"] if ln["key"] == "T.P")
st, _ = qs.apply_ops(st, [{"op": "update", "line": tp["id"], "qty": 3}, {"op": "option", "name": "IB", "include": True}],
                     "T.P 3개로 바꾸고 IB 넣어 줘")
check(next(ln for ln in st["lines"] if ln["key"] == "T.P")["qty"] == 3 and any(ln["key"] == "IB" for ln in st["lines"]) and not st["options"],
      "대화로 T.P 3개·IB 포함")
ppf = next(ln for ln in st["lines"] if ln["key"] == "PPF")
st, _ = qs.apply_ops(st, [{"op": "remove", "line": ppf["id"]}], "PPF 빼 줘")
check(not any(ln["key"] == "PPF" for ln in st["lines"]), "대화로 품목 삭제")
st, _ = qs.apply_ops(st, [{"op": "set", "field": "contact_name", "value": "홍길동"}, {"op": "set", "field": "contact_title", "value": "이사"},
                          {"op": "set", "field": "contact_mobile", "value": "010-0000-0000"},
                          {"op": "set", "field": "contact_email", "value": "name@example.com"}], "홍길동 이사 010-0000-0000 name@example.com")
check(qs.next_question(st) is None and qs.problems(st) == [], "담당자까지 채우면 질문 끝, 발행 가능", str(qs.problems(st)))

# 4) 직접 수정(왼쪽 화면) — 바뀐 것만 변경으로
lines_in = [{"id": ln["id"], "name": ln["name"], "qty": ln["qty"], "unit_price": ln["unit_price"], "remark": ln.get("remark", "")}
            for ln in st["lines"] if ln["kind"] not in ("work", "freight")]
lines_in[0]["unit_price"] = 2_100_000                                   # 마스터 단가 직접 조정
lines_in.append({"name": "시운전 교육", "qty": 1, "unit_price": 200000, "remark": "1회"})
ops = qs.edit_ops(st, {"form": {"payment": "발주시 100%"}, "lines": lines_in, "delivery": {"case": 4, "freight": 120000},
                      "work": {"people": 2, "days": 4, "rate": 500000}})
st, done = qs.apply_ops(st, ops, None)
check(st["form"]["payment"] == "발주시 100%" and st["lines"][0]["unit_price"] == 2_100_000
      and any(ln["name"] == "시운전 교육" for ln in st["lines"]) and next(ln for ln in st["lines"] if ln["kind"] == "work")["qty"] == 8
      and next(ln for ln in st["lines"] if ln["kind"] == "freight")["unit_price"] == 120_000,
      "직접 수정: 결제조건·마스터 단가·새 항목·물류비 120,000·설치 2명 4일 반영", str(done))

# 5) 발행 검사(C13)
bad = qs.draft_state(acc("TCK100"), model="TCK100", payload_kg=None, form={"customer": "x"})
check(any("가격 미정" in p for p in qs.problems(bad)) and any("이니셜" in p for p in qs.problems(bad))
      and any("납품 방식" in p for p in qs.problems(bad)),
      "단가표 0원·이니셜 없음·납품 방식 미선택 → 발행 막음", str(qs.problems(bad)))

# 6) 엑셀 줄 — 본체 → 옵션 → 물류비 → 설치 인건비 → 추가 항목, 언어별 문구, 해외는 달러
rl = qs.render_lines(st, "ko", None)
kinds = [ln["key"] for ln in rl]
check(kinds[:2] == ["MASTER T.C", "T.P"] and kinds[-4:-2] == ["freight", "work"] and rl[-3]["unit"] == "인·일"
      and rl[-3]["remark"] == "2명 × 4일" and rl[-4]["desc"] == "물류비" and rl[-3]["desc"] == "설치 인건비"
      and {rl[-2]["desc"], rl[-1]["desc"]} == {"교육비", "시운전 교육"} and rl[0]["desc"] == "TCV1\n- MASTER T.C (Payload 10Kg)",
      "엑셀 줄 순서(물류비 → 설치 인건비 → 추가 항목)·'인·일'·비고", str(kinds))
re_ = qs.render_lines(st, "en", 1380)
check(re_[-3]["desc"] == "Installation labor" and re_[-4]["desc"] == "Freight" and re_[-3]["unit"] == "man-day"
      and re_[0]["unit_price"] == round(2_100_000 / 1380, 2), "영문: 물류비·인건비 문구·단위, 달러 환산")
h = qx.quote_header(st["form"] | {"initials": "VT"}, lang="ko", quote_no="UND-VT20261006-01", day=date(2026, 10, 6), pogo=True)
ws = openpyxl.load_workbook(io.BytesIO(qx.build_xlsx(h, rl))).active
vals = {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}
work_row = next(k for k, v in vals.items() if v == "설치 인건비")
place_row = next(k for k, v in vals.items() if v == "납품장소")
check(ws[f"D{work_row[1:]}"].number_format == '0" 인·일"' and ws[f"D{work_row[1:]}"].value == 8
      and vals.get(f"G{place_row[1:]}") == "직납 및 배차 비용 별도\n(화물 비용 별도)",
      "엑셀: 인건비 수량 '8 인·일', 납품장소 '직납 및 배차 비용 별도 (화물 비용 별도)'")
en_st, _ = qs.apply_ops(st, [{"op": "set", "field": "lang", "value": "en"}], None)
check(en_st["form"]["place"] == "Direct delivery & dispatch costs excluded\n(Freight costs excluded)", "영문 납품장소 문구")

# v1.2 액세서리 — 수량 미정 줄은 빼지 않고 발행을 막고, 조건부 수량은 담당자 확인 전 발행 불가(PGR24)
ca = acc()
for it in ca["items"]:
    if it["price_item"] in ("PPM/PPF", "Cable 0.3m"):
        it.update(conditional=True, basis="조건부 근거")
ca["items"].append({"name": "Pneumatic Male (PMM)", "code": "PMM", "qty": None, "price_model": "TCV1", "price_item": "PMM/PMF", "optional": False})
pp.attach_prices(ca["items"], rows)
cs = qs.draft_state(ca, model="TCV1", payload_kg=10)
pr = qs.problems(cs)
check(any(ln["key"] == "PMM" and ln["qty"] is None for ln in cs["lines"]) and any("PMM" in x and "수량 미정" in x for x in pr)
      and sum("조건부 수량" in x for x in pr) == 3,
      "v1.2: 수량 미정(PMM) 줄은 남겨 발행을 막고, 조건부 PPM·PPF·케이블은 확인 전 발행 불가", str(pr))
ppm_id = next(ln["id"] for ln in cs["lines"] if ln["key"] == "PPM")
ppf_id = next(ln["id"] for ln in cs["lines"] if ln["key"] == "PPF")
cs2, done2 = qs.apply_ops(cs, qs.edit_ops(cs, {"lines": [{**{k: ln.get(k) for k in ("id", "name", "qty", "unit_price", "remark")},
                                                       **({"confirmed": True} if ln["id"] == ppm_id else {})}
                                                      for ln in cs["lines"] if ln["kind"] not in ("work", "freight")]}), None)
cs3, _ = qs.apply_ops(cs2, [{"op": "update", "line": ppf_id, "qty": 3}], "PPF 3개로")
pr3 = qs.problems(cs3)
check(not any("Pogo Pin Male" in x and "조건부" in x for x in pr3) and not any("Pogo Pin Female" in x and "조건부" in x for x in pr3)
      and any("조건부" in x for x in pr3) and any("확인" in d for d in done2),
      "[수량 확인]·수량 수정 → 그 줄만 확인됨, 남은 조건부 줄(케이블)은 계속 막음", str(pr3))

# 입금 계좌(사용자 2026-10-08) — 기본은 회사 계좌, 대화·직접 수정으로 바꿈, 말에 없는 계좌번호는 안 받음, 언어 바꾸면 그 언어 기본 계좌
bk = qs.draft_state(acc(), model="TCV1", payload_kg=10, form={"customer": "x"})
check(bk["form"]["bank"] == pp.QUOTE_ISSUER["bank"] and bk["form"]["account_no"] == pp.QUOTE_ISSUER["account_no"]
      and bk["form"]["account_holder"] == pp.QUOTE_ISSUER["account_holder"], "입금 계좌 기본값 = 회사 계좌", str({k: bk["form"][k] for k in qs.BANK_FIELDS}))
msg = "입금 계좌를 신한은행 110-123-456789 로 바꿔 줘"
b2, done = qs.apply_ops(bk, [{"op": "set", "field": "bank", "value": "신한은행"}, {"op": "set", "field": "account_no", "value": "110-123-456789"}], msg)
check(b2["form"]["bank"] == "신한은행" and b2["form"]["account_no"] == "110-123-456789" and any("입금 계좌번호" in x for x in done),
      "대화로 입금 은행·계좌번호 변경", str(done))
b3, _ = qs.apply_ops(bk, [{"op": "set", "field": "account_no", "value": "999-999-9999"}], "계좌 바꾸고 싶어요")
check(b3["form"]["account_no"] == pp.QUOTE_ISSUER["account_no"], "말에 없는 계좌번호는 넣지 않음")
b4, _ = qs.apply_ops(b2, [{"op": "set", "field": "lang", "value": "en"}], None)
check(b4["form"]["account_no"] == pp.QUOTE_ISSUER_EN["account_no"] and b4["form"]["bank"] == pp.QUOTE_ISSUER_EN["bank"],
      "해외로 바꾸면 영문 기본 계좌로", str({k: b4["form"][k] for k in qs.BANK_FIELDS}))
hf = {**b2["form"], "customer": "x", "contact_name": "a", "contact_mobile": "1", "contact_email": "e"}
hd = qx.quote_header(hf, lang="ko", quote_no="VT20261008", day=date(2026, 10, 8), pogo=False)
hd0 = qx.quote_header({**hf, "bank": "", "account_no": "", "account_holder": ""}, lang="ko", quote_no="VT20261008", day=date(2026, 10, 8), pogo=False)
check(hd["issuer"]["account_no"] == "110-123-456789" and hd["issuer"]["bank"] == "신한은행"
      and hd0["issuer"]["account_no"] == pp.QUOTE_ISSUER["account_no"], "견적서 머리: 바꾼 계좌, 비우면 회사 기본 계좌")
check("account_no" in qs.QUOTE_SYSTEM and "결제조건(payment)과 다르다" in qs.QUOTE_SYSTEM, "AI 지시: 입금 계좌 ≠ 결제조건, 거절·지어내기 금지")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
