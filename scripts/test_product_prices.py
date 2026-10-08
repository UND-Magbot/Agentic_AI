"""맥봇 제품 단가 DB(company_knowledge/product_prices.py) 점검 — 단가표 읽기·범위·0원 처리·견적용 찾기.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts -v ./docs:/docs backend \\
        python /scripts/test_product_prices.py
"""
from __future__ import annotations

import sys
from datetime import date

from app.company_knowledge import product_prices as pp

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


rows = pp.parse_book()
by = {(r["model"], r["item"]): r for r in rows}
models = {r["model"] for r in rows}
cats = {r["category"] for r in rows}

check(cats == {"ATC_유선", "ATC_무선", "Tool Changer Motor K", "Tool Changer Motor", "MG", "산업용 마그네틱 그리퍼(TCK)"},
      "저장 범위 = 단가표 ATC_유선 ~ MG (TCK 는 산업용 마그네틱 그리퍼로 따로 구분)", str(cats))
check(not models & {"mDMG2F", "mDPG-C5", "SMG1S10D", "SMG10S1A ( cylinder type)"},
      "범위 밖(DC Motor·Pneumatic·Shape-Memory·Shiftlock 그리퍼)은 넣지 않음")
check({"TCC1", "TCV1", "TCV2", "TCV3", "TCV4", "TCK50", "TCW1", "TCMK150", "TCM100", "MG5", "MG210"} <= models, "모델 포함")

t = by[("TCC1", "MASTER T.C")]
check(t["customer_price"] == 1_800_000 and t["dealer_price"] == 1_260_000 and t["price_status"] == "set",
      "TCC1 Master 고객 1,800,000 / 대리점 1,260,000", str(t))
check(by[("TCC1", "T.P")]["dealer_price"] == 245_000, "대리점가 소수(244999.99…)는 원 단위 반올림 → 245,000")
check({it for (m, it) in by if m == "TCV2"} ==
      {"MASTER T.C", "T.P", "PPM/PPF", "PMM/PMF", "Cable 0.3m", "Cable 4m", "IB", "CONTROLBOX"},
      "유선 ATC(TCV2) 품목 8종")
check(by[("TCV2", "PPM/PPF")]["customer_price"] == 250_000 and by[("TCV2", "PPM/PPF")]["note"] == "PPM 또는 PPF 1개 가격(개별)"
      and by[("TCV2", "Cable 0.3m")]["customer_price"] == 50_000 and by[("TCV2", "Cable 0.3m")]["dealer_price"] == 35_000,
      "PPM/PPF 개별 250,000, Cable 0.3m 고객 50,000 / 대리점 35,000(단가표 그대로)")
check(by[("TCK100", "MASTER T.C")]["category"] == "산업용 마그네틱 그리퍼(TCK)" and by[("TCMK150", "MASTER T.C")]["category"] == "Tool Changer Motor K"
      and "기본 포함" in by[("TCV2", "CONTROLBOX")]["note"],
      "TCK = 산업용 마그네틱 그리퍼로 구분(TCMK 는 그대로), CONTROLBOX 는 마스터 기본 포함 메모")
check(by[("TCV4", "CONTROLBOX")]["customer_price"] == 1_000_000 and by[("TCV4", "Cable 4m")]["customer_price"] == 100_000,
      "CONTROLBOX 1,000,000 · Cable 4m 100,000")

u = by[("TCK100", "MASTER T.C")]
check(u["price_status"] == "unset" and u["customer_price"] is None and u["dealer_price"] is None,
      "0원 칸 = 가격 미정(NULL), 무상 아님", str(u))
check(by[("TCK100", "T.P")]["price_status"] == "unset", "고객가 빈칸 + 대리점가 0 → T.P 가격 미정")
check(("TCK50", "PPM/PPF") not in by and ("TCK50", "T.P") in by, "고객·대리점 둘 다 빈칸 = 해당 품목 없음(행 없음)")
check(by[("MG5", "본체")]["customer_price"] == 1_650_000 and not any(m.startswith("MG") and it != "본체" for m, it in by),
      "MG 는 본체 가격만(T.P 열의 0 은 무시)")

check(pp.find(rows, "tcv2", "PPF") is by[("TCV2", "PPM/PPF")] and pp.find(rows, "TCV2", "PMM") is by[("TCV2", "PMM/PMF")]
      and pp.find(rows, "TCV2", "1m CABLE") is None,
      "견적용 찾기: PPF→PPM/PPF, PMM→PMM/PMF, 단가표에 없는 1m 케이블은 None(가격 만들지 않음)")
check(pp.price_list_date(pp.BOOK_NAME) == date(2026, 10, 2), "파일명 261002 → 단가표 날짜 2026-10-02")
check("가격 미정" in pp.markdown(rows) and "1,800,000 / 1,260,000" in pp.markdown(rows), "정리 문서 표")

# 견적 단가 붙이기(국내 고객사가) — 선택 품목은 합계에서 빼고, 모르는 값은 0 으로 더하지 않음
items = [{"name": "TCV2 MASTER T.C", "qty": 1, "price_model": "TCV2", "price_item": "MASTER T.C", "optional": False},
         {"name": "PPM", "qty": 2, "price_model": "TCV2", "price_item": "PPM/PPF", "optional": False},
         {"name": "CONTROLBOX", "qty": 1, "price_model": "TCV2", "price_item": "CONTROLBOX", "optional": True},
         {"name": "PPF", "qty": None, "price_model": "TCV2", "price_item": "PPM/PPF", "optional": False}]
pr = pp.attach_prices(items, rows)
check(items[0]["amount"] == 2_800_000 and items[1]["amount"] == 500_000 and items[2]["unit_price"] == 1_000_000
      and pr["subtotal"] == 3_300_000 and pr["optional_total"] == 1_000_000 and not pr["complete"]
      and any("PPF" in m and "수량" in m for m in pr["missing"]),
      "단가: 마스터 2.8M + PPM 250k×2 = 3.3M, CONTROLBOX(선택)는 합계 밖, 수량 모르는 PPF 는 미완성 표시", str(pr))
tk = [{"name": "TCK100 MASTER T.C", "qty": 1, "price_model": "TCK100", "price_item": "MASTER T.C", "optional": False},
      {"name": "로봇측 어댑터", "qty": None, "price_model": None, "price_item": None, "optional": True}]
pk = pp.attach_prices(tk, rows)
check(tk[0]["price_status"] == "unset" and tk[0]["amount"] is None and pk["subtotal"] == 0 and len(pk["missing"]) == 1
      and tk[1]["price_status"] == "none" and pk["optional_total"] == 0,
      "단가표 0원(TCK100) = 가격 미정(0원으로 더하지 않음), 로봇측 어댑터는 선택·견적 후라 합계 미완성 사유 아님", str(pk))
# [견적에 포함] 체크 — 체크한 선택 품목(IB)만 합계에
def acc():
    return {"items": [
        {"name": "TCV2 MASTER T.C", "qty": 1, "price_model": "TCV2", "price_item": "MASTER T.C", "optional": False},
        {"name": "IB (Interface Bracket)", "qty": 1, "price_model": "TCV2", "price_item": "IB", "optional": True}]}


none = pp.apply_options(acc(), [], rows)["pricing"]
check(none["subtotal"] == 2_800_000 and none["optional_total"] == 100_000 and none["complete"] and "remarks" not in none,
      "IB 체크 안 함 → 합계 2.8M(+IB 100,000 은 따로), 미포함 비고 문구 없음", str(none))
ib = pp.apply_options(acc(), ["IB (Interface Bracket)"], rows)
check(ib["pricing"]["subtotal"] == 2_900_000 and ib["pricing"]["optional_total"] == 0 and ib["items"][1]["included"],
      "IB 체크 → 합계 2.9M", str(ib["pricing"]))

check(pp.quote_file_name("가나테크", "magbot툴체인저", "VT", date(2026, 9, 30), "S26-0182")
      == "(주)유엔디로보틱스_견적서_가나테크_magbot툴체인저_VT260930_S26-0182"
      and pp.QUOTE_ISSUER is pp.COMPANY_PROFILE["quote_issuer"] and bool(pp.QUOTE_ISSUER["account_no"]),
      "파일명 = 이니셜·날짜 + 견적번호(영업 건 번호 S26-…), 머리 = 유엔디로보틱스 양식")
check(pp.quote_file_name("(가상) 성진물류", "magbot툴체인저", "VT", date(2026, 10, 8), "VT20261008")
      == "(주)유엔디로보틱스_견적서_(가상) 성진물류_magbot툴체인저_VT20261008"
      and pp.quote_file_name("가나테크", "MG 그리퍼", "AL", date(2026, 10, 8), "AL20261008B")
      == "(주)유엔디로보틱스_견적서_가나테크_MG 그리퍼_AL20261008B",
      "새 견적번호(이니셜+날짜)는 파일명에 이니셜·날짜를 두 번 붙이지 않음")
check([pp.clean_quote_file_name(n) for n in (
          "(주)유엔디로보틱스_견적서_(가상) 성진물류_magbot툴체인저_VT261008_VT20261008.xlsx",
          "(주)유엔디로보틱스_견적서_(가상) 성진물류_magbot툴체인저_VT261008_VT20261008_Rev1.xlsx",
          "(주)유엔디로보틱스_견적서_가나테크_magbot툴체인저_VT260930_S26-0182_Rev2.xlsx")]
      == ["(주)유엔디로보틱스_견적서_(가상) 성진물류_magbot툴체인저_VT20261008.xlsx"] * 2
         + ["(주)유엔디로보틱스_견적서_가나테크_magbot툴체인저_VT260930_S26-0182.xlsx"],
      "예전 발행본 파일명 정리: 이니셜·날짜 중복·_Revn 제거(예전 번호 S26-… 는 이니셜·날짜 유지)")
ko, en = pp.quote_form("ko"), pp.quote_form("en")
check(ko["issuer"] is pp.COMPANY_PROFILE["quote_issuer"] and en["issuer"] is pp.COMPANY_PROFILE["quote_issuer_en"]
      and set(ko["labels"]) == set(en["labels"])
      and en["labels"]["delivery"] == "Delivery Schedule" and ko["missing"] == []
      and en["missing"] == [],
      "양식: 회사 정보 = 회사 프로필(국문·영문), 영문은 같은 칸 구성")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
