# -*- coding: utf-8 -*-
"""영업 건 관리 이관 변환(sales_deals/legacy_import.py)과 사진 API 점검. DB 흐름은 test_sales_deals_db.py.

    python scripts/test_sales_deals_preview.py        # 로컬(원본 엑셀 docs/ 필요)
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8")

from app.sales_deals import legacy_import as li  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


# ── 결제조건 읽기 ──
t = li.parse_pay_terms("선금 : 발주 시 50% \n잔금 : 납품 전 50% (잔금 입금 확인 후 출고)")
check([(x["label"], x["pct"], x["when"], x["ship_gate"]) for x in t]
      == [("선금", 50, "발주 시", False), ("잔금", 50, "납품 전", True)], "선금 50 / 잔금 50 + 입금 확인 후 출고", str(t))
t = li.parse_pay_terms("선금 (발주 시) : 70% \n잔금 (납품 전, 잔금 입금 확인 후 출하) : 30%")
check([(x["pct"], x["when"]) for x in t] == [(70, "발주 시"), (30, "납품 전")], "LG CNS 70/30 형식", str(t))
t = li.parse_pay_terms("선금 50% ( 발주 시 )잔금 50% ( 납품 전 )")
check([(x["label"], x["when"]) for x in t] == [("선금", "발주 시"), ("잔금", "납품 전")], "키워드가 % 뒤에 오는 형식", str(t))
t = li.parse_pay_terms("발주시 50%, 납품전 50%")
check([x["when"] for x in t] == ["발주 시", "납품 전"], "붙여 쓴 '발주시·납품전'", str(t))
t = li.parse_pay_terms("대금 지급 조건 : 납품 후 익월말\n( 5월 예정)")
check(t == [{"label": "전액", "pct": 100, "when": "납품 후 익월말", "ship_gate": False}], "납품 후 익월말", str(t))
check(li.parse_pay_terms("별도 협의") == [], "별도 협의는 회차를 지어내지 않음")
check(li.parse_pay_terms("고객사 특별 할인 5%\n선금 100%")[0]["pct"] == 100
      and len(li.parse_pay_terms("고객사 특별 할인 5%\n선금 100%")) == 1, "할인 %는 결제 회차가 아님")

# ── 고객사 비교 ──
check(li.same_customer("㈜포에스텍 대구공장", "주식회사 포에스텍"), "공장·회사형태 표기 차이")
check(li.same_customer("포에그텍", "포에스텍"), "한 글자 오타")
check(li.same_customer("대구경북과학기술원  \n-(디지스트)", "대구경북과학기술원"), "괄호·기호 붙은 이름")
check(not li.same_customer("삼성전자", "삼성웰스토리"), "다른 회사는 다르게")

# ── 실제 엑셀 변환 ──
mgmt = ROOT / "docs" / "sources" / "견적,세금,수주 관리 시트.xlsx"
ship = ROOT / "docs" / "sources" / "영업출하시트파일_ma261006.xlsx"
snap = li.build_snapshot(mgmt, ship)
deals, ships = snap["deals"], snap["shipments"]
check(len(deals) == 172, "관리 시트 건 172개(견적 127 + T-번호 45)", str(len(deals)))
check(sum(d["kind"] == "sales_only" for d in deals) == 45, "견적 없는 매출(T-번호) 45개")
check(len(ships) == 45, "출하 45개(이어진 빈 행은 앞 출하에 붙임)", str(len(ships)))
photo_refs = sum(len(s["photo_refs"]) for s in ships)
check(photo_refs == 45, "출하 사진 45장 모두 출하에 배정(셀 안 28 + 그림 17)", str(photo_refs))
by_no = {d["legacy_no"]: d for d in deals}
check(len(by_no["13"]["items"]) == 5, "13번(제이에스글로벌) 품목 5줄을 한 건으로", str(len(by_no["13"]["items"])))
guessed = [s for s in ships if s["date_guessed"]]
check(guessed and guessed[0]["date"].startswith("2025-09") and any(s["date"] == "2026-01-09" for s in guessed),
      "연도 없는 출하일: 2025-09 → 해가 바뀌면 2026", str([s["date"] for s in guessed]))

for s in ships:  # 스냅샷 JSON 처럼 사진 파일명 칸을 채운다
    s["photos"] = []
view = li.build_preview(json.loads(json.dumps(snap, ensure_ascii=False)), dt.date(2026, 10, 6))
rows = view["rows"]
ids = [r["id"] for r in rows]
check(len(ids) == len(set(ids)) and all(i.startswith("S") for i in ids), "새 건번호는 겹치지 않음")
check(sum(view["summary"][s] for s in ("견적", "수주", "출하 후 입금 대기", "완료 (출하·완납)", "출하 기록만 (이전)")) == len(rows),
      "단계 요약 합계 = 전체 행")
legacy = {}
for r in rows:  # 옛 번호는 관리 시트 기준(출하시트 NO 와 겹친다)
    if r["source"]["file"] == "management":
        legacy.setdefault(r["legacy_no"], []).append(r)
codes = lambda r: {a["code"] for a in r["alerts"]}  # noqa: E731

ani = next(r for r in legacy["10"] if r["customer"] == "이앤아이")
check(ani["stage"] == "수주" and not ani["paid_full"] and "PAID_TEXT" in codes(ani) and "UNPAID" not in codes(ani),
      "이앤아이: 선금 50%만 입금 → 수주(세금계산서는 프로그램 밖 — 청구 중 단계 없음), 미입금 알림 아님", f"{ani['stage']} {codes(ani)}")
check("DUP_NO" in codes(ani), "옛 번호 10 중복 표시")
lib = legacy["12"][0]
check(lib["stage"] == "완료 (출하·완납)" and "INVOICE_DIFF" not in codes(lib), "수성못 12번: 부가세 포함 단가 = 발행액 → 완납", f"{lib['stage']} {codes(lib)}")
andong = legacy["19"][0]
check(andong["stage"] == "완료 (출하·완납)" and "BROKEN_DATE" in codes(andong), "안동 19번: 입금일 깨짐이지만 입금은 된 건", f"{andong['stage']} {codes(andong)}")
kp = legacy["70"][0]
check("REVISION" in codes(kp) and "69" in next(a["text"] for a in kp["alerts"] if a["code"] == "REVISION"),
      "케이피항공 69·70 차수 후보")
bit = next(r for r in rows if r["legacy_no"] == "85" and r["customer"] == "비트센싱")
check(len(bit["shipments"]) == 2, "비트센싱 나눠서 출하 → 한 건에 출하 2회", str(len(bit["shipments"])))
dgist = legacy["45"][0]
check(dgist["shipments"] and dgist["shipments"][0]["linked_by_amount"] and "SHIP_LINK" not in codes(dgist),
      "DGIST 출하는 금액으로 확인되어 확인 표시 없음")
nau = next(r for r in rows if r["kind"] == "shipment_only" and r["customer"] == "나우티앤에스")
check("SHIP_ONLY" not in codes(nau), "용도 '개발' 출하는 견적 없어도 확인 요청 안 함")
hakto = legacy["T-2"][0]
check(hakto["stage"] == "수주" and any(a["code"] == "UNPAID" and "일 미입금" in a["text"] for a in hakto["alerts"]),
      "T-2 하우토: 계산서만 있고 입금 없음 → 미입금 알림")
lgcns = legacy["30"][0]
check(lgcns["stage"] == "완료 (출하·완납)" and [t["pct"] for t in lgcns["pay_terms"]] == [70, 30], "LG CNS 30번: 70/30 완납")

# ── API(인증은 영업 사용자로 대체) ──
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import api_sales_deals as api
    from app.models import UserDomain, UserRole

    class _U:
        def __init__(self, domain: UserDomain, role: UserRole = UserRole.member) -> None:
            self.domain, self.role = domain, role

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_current_user] = lambda: _U(UserDomain.sales)
    c = TestClient(app)
    photos = sorted(api.PHOTO_DIR.glob("ship_*.jpg"))
    check(len(photos) >= 35, "출하 사진 사본 35장 이상", str(len(photos)))
    r = c.get(f"/v1/sales-deals/photos/{photos[0].name}")
    check(r.status_code == 200 and r.headers["content-type"] == "image/jpeg", "사진 내려받기")
    check(c.get("/v1/sales-deals/photos/..%2F..%2Fmain.py").status_code == 404, "사진 이름 외 경로 거부")
    app.dependency_overrides[api.get_current_user] = lambda: _U(UserDomain.finance)
    check(c.get(f"/v1/sales-deals/photos/{photos[0].name}").status_code == 403, "영업 외 부서는 403")
except ImportError as e:
    FAIL.append(("API 점검", f"import 실패: {e}"))

for label in PASS:
    print("PASS", label)
for label, detail in FAIL:
    print("FAIL", label, "—", detail)
print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
