"""영업 건 관리(/v1/sales-deals) API 검증 — cases/03_영업건관리.md 의 API 케이스.

    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \
      -v ./backend/app:/app/app -v ./test_cases:/tc backend python /tc/runners/api/deals.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import sys

import openpyxl
import pymupdf
from sqlalchemy import text

sys.path.insert(0, "/tc/runners")
import tc  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.sales_deals import service as svc  # noqa: E402

TODAY = tc.kst_today()
D = TODAY.isoformat()
YMD = TODAY.strftime("%Y%m%d")
BUYER = {"reg_no": "123-45-67890", "name": f"{tc.MARK}고객(주)", "ceo": "홍길동", "address": "서울시 테스트로 1"}


def pdf_bytes() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "PO TEST")
    return doc.tobytes()


def jpg_bytes() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    pix.clear_with(200)
    return pix.tobytes("jpg")


class Deals:
    def __init__(self, s: tc.Suite) -> None:
        self.s = s

    async def new(self, user: str = "lucas", *, items=None, customer: str = "고객", **over) -> dict:
        body = {"customer": f"{tc.MARK}{customer}", "owner": "lucas", "quote_date": D,
                "items": items or [{"name": "TCV1\n- MASTER T.C (Payload 10Kg)", "unit_price": 1000000, "qty": 2}], **over}
        r = await self.s.call("POST", "/v1/sales-deals", user, json=body)
        assert r.status_code == 201, f"건 만들기 실패 {r.status_code} {r.text[:200]}"
        return r.json()

    async def post(self, did: int, path: str, user: str = "alex", **kw):
        return await self.s.call("POST", f"/v1/sales-deals/{did}{path}", user, **kw)

    async def won(self, did: int, user: str = "alex") -> dict:
        r = await self.post(did, "/won", user, json={"won": True, "date": D})
        assert r.status_code == 200, f"수주 실패 {r.status_code} {r.text[:200]}"
        return r.json()

    async def statement(self, did: int, items=None, initials: str = "LU", user: str = "lucas", date: str = D):
        return await self.post(did, "/statement", user, json={
            "initials": initials, "date": date, "buyer": BUYER,
            "items": items or [{"name": "TCV1 MASTER T.C", "spec": "Payload 10Kg", "qty": 2, "unit_price": 1000000}]})

    async def ready(self, user: str = "lucas", **kw) -> dict:
        """수주 + 명세서 발급까지 된 건."""
        d = await self.new(user, **kw)
        await self.won(d["id"])
        r = await self.statement(d["id"])
        assert r.status_code == 200, f"명세서 실패 {r.status_code} {r.text[:200]}"
        return r.json()

    async def terms(self, did: int, terms: list[dict], user: str = "alex"):
        return await self.post(did, "/terms", user, json={"terms": terms})

    async def get(self, did: int, user: str = "alex") -> dict:
        return (await self.s.call("GET", f"/v1/sales-deals/{did}", user)).json()


def codes(rs) -> list[int]:
    return [r.status_code for r in rs]


async def body(s: tc.Suite) -> None:
    x = Deals(s)
    uid = s.user_ids

    # ── 만들기·입력 검사 ──
    async def dl001():
        d = await x.new()
        top = (await s.call("GET", "/v1/sales-deals", "lucas")).json()["rows"][0]
        ok = (d["deal_no"] == f"LU{YMD}" and d["quote"]["amount"] == 2000000 and d["stage"] == "견적" and top["id"] == d["id"])
        return ok, f"deal_no={d['deal_no']} amount={d['quote']['amount']} stage={d['stage']} top={top['id']}"
    await s.case("DL-001", "새 견적 건 등록(번호·금액·단계·맨 위)", dl001)

    async def dl003():
        a = await x.new(customer="오래된건")
        await x.new(customer="새건")
        await x.post(a["id"], "/meeting", "lucas", json={"notes": "메모 변경"})
        top = (await s.call("GET", "/v1/sales-deals", "lucas")).json()["rows"][0]
        return top["id"] == a["id"], f"맨 위 id={top['id']} 기대 {a['id']}"
    await s.case("DL-003", "최근 변경한 건이 맨 위", dl003)

    async def dl004():
        r = await s.call("POST", "/v1/sales-deals", "lucas", json={
            "customer": f"{tc.MARK}큰금액", "owner": "lucas", "quote_date": D,
            "items": [{"name": "A", "unit_price": 100_000_000_000, "qty": 1000}]})
        amt = r.json().get("quote", {}).get("amount") if r.status_code == 201 else None
        return r.status_code in (400, 422), f"{r.status_code} 저장된 합계={amt}"
    await s.case("DL-004", "합계 1,000억 초과 거절", dl004)

    async def dl005():
        base = {"customer": f"{tc.MARK}입력", "owner": "lucas", "quote_date": D, "items": [{"name": "A", "unit_price": 1, "qty": 1}]}
        bad = [{"items": []}, {"items": [{"name": "A", "unit_price": 1, "qty": 1}] * 51},
               {"items": [{"name": "A", "unit_price": 1, "qty": 0}]}, {"items": [{"name": "A", "unit_price": -1, "qty": 1}]},
               {"currency": "JPY"}, {"quote_date": "2026-02-30"}]
        rs = [await s.call("POST", "/v1/sales-deals", "lucas", json={**base, **b}) for b in bad]
        return all(c == 422 for c in codes(rs)), f"상태 {codes(rs)}"
    await s.case("DL-005", "잘못된 입력 6종 422", dl005)

    async def dl006():
        r = await s.call("POST", "/v1/sales-deals", "lucas", json={
            "customer": f"{tc.MARK}이니셜없음", "owner": "tc검증신입", "quote_date": D, "items": [{"name": "A", "unit_price": 1, "qty": 1}]})
        return r.status_code == 409 and "이니셜" in r.text, f"{r.status_code} {r.text[:120]}"
    await s.case("DL-006", "이니셜 없는 담당자 → 409 안내", dl006)

    async def dl007():
        try:
            r1 = await s.call("POST", "/v1/sales-deals/initials", "mason", json={"name": "lucas", "initials": "MS"})
            after_member = {i["name"].lower(): i["initials"] for i in (await s.call("GET", "/v1/sales-deals/initials", "mason")).json()["items"]}
            r2 = await s.call("POST", "/v1/sales-deals/initials", "alex", json={"name": "lucas", "initials": "LX"})
            after_admin = {i["name"].lower(): i["initials"] for i in r2.json()["items"]} if r2.status_code == 200 else {}
            ok = after_member.get("lucas") == "LU" and after_admin.get("lucas") == "LX"
            return ok, f"멤버 요청 {r1.status_code} → {after_member.get('lucas')}, 관리자 {r2.status_code} → {after_admin.get('lucas')}"
        finally:
            await s.call("POST", "/v1/sales-deals/initials", "alex", json={"name": "lucas", "initials": "LU"})
    await s.case("DL-007", "기존 이니셜 변경은 관리자만", dl007)

    async def dl008():
        rs = [await s.call("POST", "/v1/sales-deals/initials", "lucas", json={"name": "tc검증이니셜", "initials": v})
              for v in ("L1", "A", "ABC", "ABCDE")]
        return all(c in (400, 422) for c in codes(rs)), f"상태 {codes(rs)}"
    await s.case("DL-008", "잘못된 이니셜 거절", dl008)

    # ── 발주서 ──
    async def dl009():
        d = await x.new()
        r = await x.post(d["id"], "/po", "lucas", files={"file": ("po.pdf", pdf_bytes(), "application/pdf")})
        j = r.json()
        ok = r.status_code == 200 and len(j["po_files"]) == 1 and j["won"] is True and j["won_date"] == D
        return ok, f"{r.status_code} won={j.get('won')} won_date={j.get('won_date')} (한국 오늘 {D})"
    await s.case("DL-009", "발주서 첨부 → 수주로, 수주일 = 오늘(한국)", dl009)

    async def dl010():
        d = await x.new()
        exe = b"MZ\x90\x00" + b"\x00" * 200
        cases = [("virus.exe", exe), ("virus.pdf", exe), ("empty.pdf", b""), ("big.pdf", b"%PDF" + b"0" * (21 * 1024 * 1024)), ("noext", b"abc")]
        rs = [await x.post(d["id"], "/po", "lucas", files={"file": (n, b, "application/octet-stream")}) for n, b in cases]
        got = codes(rs)
        ok = got[0] == 415 and got[2] == 400 and got[3] == 413 and got[4] == 415 and got[1] in (400, 415)
        return ok, f"exe/이름만pdf/빈/21MB/확장자없음 = {got} (이름만 바꾼 exe 가 200 이면 내용 검사 없음)"
    await s.case("DL-010", "발주서 파일 형식·크기 검사", dl010)

    async def dl011():
        d = await x.new()
        names = ["../../etc/passwd.pdf", 'a"b\r\nX-Evil: 1.pdf', "발주서 (최종).pdf"]
        bad = []
        for i, n in enumerate(names):
            r = await x.post(d["id"], "/po", "lucas", files={"file": (n, pdf_bytes(), "application/pdf")})
            if r.status_code != 200:
                bad.append(f"{n!r} 업로드 {r.status_code}")
                continue
            idx = len(r.json()["po_files"]) - 1
            async with SessionLocal() as db:       # 저장 키는 화면 응답에 안 실려 DB 에서 본다
                key = (await db.execute(text("SELECT po_files->CAST(:i AS int)->>'key' FROM sales_deals WHERE id = :d"),
                                        {"i": idx, "d": d["id"]})).scalar() or ""
            g = await s.call("GET", f"/v1/sales-deals/{d['id']}/po/{idx}", "lucas")
            cd = g.headers.get("content-disposition", "")
            if not key.startswith(f"sales-deals/{d['id']}/po/") or "\n" in cd or "\r" in cd or "X-Evil" in g.headers:
                bad.append(f"{n!r} key={key} cd={cd!r}")
        return not bad, "; ".join(bad)
    await s.case("DL-011", "발주서 파일명 조작·한글 이름", dl011)

    async def dl014():
        d = await x.new()
        await x.post(d["id"], "/po", "lucas", files={"file": ("po.pdf", pdf_bytes(), "application/pdf")})
        rs = [await s.call("GET", f"/v1/sales-deals/{d['id']}/po/5", "lucas"),
              await s.call("GET", f"/v1/sales-deals/{d['id']}/po/-1", "lucas"),
              await x.post(d["id"], "/po/9/compare", "lucas")]
        return all(c in (404, 422) for c in codes(rs)) and codes(rs)[0] == 404, f"상태 {codes(rs)}"
    await s.case("DL-014", "없는 발주서 번호 404", dl014)

    # ── 거래명세서 ──
    async def dl015():
        d = await x.new()
        await x.won(d["id"])
        r = await x.statement(d["id"])
        st = r.json().get("statement") or {}
        no, tot = st.get("no", ""), st.get("totals", {})
        ok = (r.status_code == 200 and no.startswith(f"TS-LU{YMD}-")
              and tot.get("supply") == 2000000 and tot.get("vat") == 200000 and tot.get("total") == 2200000)
        return ok, f"{r.status_code} no={no} totals={tot}"
    await s.case("DL-015", "명세서 발급(번호·공급가액·부가세)", dl015)

    async def dl016():
        d1, d2 = await x.ready(), await x.ready()
        n1 = (await x.statement(d1["id"])).json()["statement"]["no"]
        n2 = (await x.statement(d2["id"])).json()["statement"]["no"]
        n3 = (await x.statement(d2["id"], initials="AL")).json()["statement"]["no"]
        n4 = (await x.statement(d2["id"], date="2000-01-04")).json()["statement"]["no"]
        s1, s2 = int(n1.rsplit("-", 1)[1]), int(n2.rsplit("-", 1)[1])
        ok = s2 == s1 + 1 and n3.startswith(f"TS-AL{YMD}-") and n4 == "TS-LU20000104-01"
        return ok, f"{n1} → {n2}, AL={n3}, 날짜변경={n4}"
    await s.case("DL-016", "명세서 번호: 발행자·날짜별 순번", dl016)

    async def dl017():
        ds = [await x.new() for _ in range(2)]
        for d in ds:
            await x.won(d["id"])
        rs = await asyncio.gather(*(x.statement(ds[i % 2]["id"], date="2000-01-05") for i in range(10)))
        nos = [r.json()["statement"]["no"] for r in rs if r.status_code == 200]
        seqs = sorted(int(n.rsplit("-", 1)[1]) for n in nos)
        return codes(rs).count(200) == 10 and seqs == list(range(1, 11)), f"상태 {codes(rs)} 순번 {seqs}"
    await s.case("DL-017", "동시 발급 10건 번호 중복 없음", dl017)

    async def dl019():
        d = await x.new()
        await x.won(d["id"])
        r = await x.statement(d["id"], items=[{"name": "A", "spec": "", "qty": 1, "unit_price": 125}])
        tot = r.json()["statement"]["totals"]
        # 인쇄물은 엑셀 ROUND(사사오입) → 부가세 13원, 합계 138원
        return tot.get("vat") == 13 and tot.get("total") == 138, f"서버 totals={tot} (엑셀 인쇄 기준 vat 13 / total 138)"
    await s.case("DL-019", "부가세 반올림이 인쇄물(엑셀 ROUND)과 같음", dl019)

    async def dl020():
        d = await x.new()
        await x.won(d["id"])
        await x.statement(d["id"], items=[{"name": "A", "spec": "", "qty": 3, "unit_price": 1000, "unit": "EA"}])
        f = await s.call("GET", f"/v1/sales-deals/{d['id']}/statement/file?fmt=xlsx", "alex")
        wb = openpyxl.load_workbook(io.BytesIO(f.content))
        hits = [f"{ws.title}!{c.coordinate}={c.value!r}" for ws in wb for row in ws.iter_rows() for c in row
                if isinstance(c.value, str) and "EA" in c.value]
        return f.status_code == 200 and not hits, f"{f.status_code} 단위가 찍힌 칸: {hits[:3]}"
    await s.case("DL-020", "명세서 수량 칸에 단위 안 찍힘(EA)", dl020)

    async def dl022():
        d = await x.new()
        await x.won(d["id"])
        r = await x.statement(d["id"], items=[{"name": "A", "spec": "", "qty": 1, "unit_price": 100000},
                                               {"name": "NEGO", "spec": "", "qty": 1, "unit_price": -200000}])
        tot = (r.json().get("statement") or {}).get("totals") if r.status_code == 200 else None
        return r.status_code == 400, f"{r.status_code} 발급된 totals={tot}"
    await s.case("DL-022", "합계 0 이하 명세서 거절", dl022)

    async def dl023():
        d = await x.new()
        await x.won(d["id"])
        item = {"name": "A", "spec": "", "qty": 1, "unit_price": 1}
        reqs = [{"initials": ""}, {"initials": "L1"}, {"buyer": {**BUYER, "reg_no": ""}}, {"items": [item] * 31},
                {"items": [{**item, "name": "가" * 81}]}]
        rs = []
        for over in reqs:
            body_ = {"initials": "LU", "date": D, "buyer": BUYER, "items": [item], **over}
            rs.append(await x.post(d["id"], "/statement", "lucas", json=body_))
        got = codes(rs)
        return got[0] in (400, 422) and got[1] in (400, 422) and got[2:] == [422, 422, 422], f"상태 {got}"
    await s.case("DL-023", "명세서 잘못된 입력 거절", dl023)

    async def dl025():
        d = await x.new()
        r1 = await x.statement(d["id"])
        r2 = await s.call("GET", f"/v1/sales-deals/{d['id']}/order", "lucas")
        return codes([r1, r2]) == [400, 400], f"상태 {codes([r1, r2])}"
    await s.case("DL-025", "수주 전 명세서·수주 진행 막힘", dl025)

    async def dl026():
        d = await x.new()
        await x.won(d["id"])
        r = await x.statement(d["id"], initials="AL", user="lucas")
        no = (r.json().get("statement") or {}).get("no") if r.status_code == 200 else None
        return ("CHECK", f"lucas 가 AL 이니셜로 발급 → {r.status_code} {no} (다른 사람 순번 소진 허용 여부 정책 확인)")
    await s.case("DL-026", "다른 사람 이니셜로 명세서 발급", dl026)

    # ── 결제 조건·입금 ──
    async def dl027():
        d = await x.ready(items=[{"name": "A", "unit_price": 2000001, "qty": 1}])
        await x.statement(d["id"], items=[{"name": "A", "spec": "", "qty": 1, "unit_price": 2000001}])
        r = await x.terms(d["id"], [{"label": "선금", "pct": 30, "expected_date": "2026-12-20"}, {"label": "잔금", "pct": 70}])
        amts = [t["amount"] for t in r.json()["flow"]["terms"]]
        return amts == [600000, 1400001], f"{r.status_code} 회차 금액 {amts}"
    await s.case("DL-027", "회차 금액, 끝전은 마지막 회차", dl027)

    async def dl028():
        d = await x.ready()
        sets = [[30, 60], [50, 60], [33.333, 33.333, 33.334], [0, 100], [10] * 6 + [40], None]
        rs = []
        for p in sets:
            ts = [{"label": "잔금" * 6, "pct": 100}] if p is None else [{"label": f"회차{i}", "pct": v} for i, v in enumerate(p)]
            rs.append(await x.terms(d["id"], ts))
        got = codes(rs)
        ok = got[0] == 400 and got[1] == 400 and got[3] == 422 and got[4] == 422 and got[5] == 422 and got[2] == 200
        return ok, f"90%/110%/3분할(33.333…)/0%/7회차/구분11자 = {got} — 3분할이 400 이면 정상 비율을 거절"
    await s.case("DL-028", "결제 조건 비율 검사", dl028)

    async def dl029():
        d = await x.new()
        await x.won(d["id"])
        r = await x.terms(d["id"], [{"label": "잔금", "pct": 100}])
        return r.status_code == 400 and "명세서" in r.text, f"{r.status_code} {r.text[:100]}"
    await s.case("DL-029", "명세서 전 결제 조건 막힘", dl029)

    async def dl030():
        d = await x.ready()
        await x.terms(d["id"], [{"label": "선금", "pct": 30, "expected_date": "2026-12-01"}, {"label": "잔금", "pct": 70}])
        await x.post(d["id"], "/confirm-term", "lucas", json={"index": 0})
        r1 = await x.terms(d["id"], [{"label": "선금", "pct": 40}, {"label": "잔금", "pct": 60}])
        r2 = await x.terms(d["id"], [{"label": "선금", "pct": 30, "expected_date": "2026-12-05"}, {"label": "잔금", "pct": 70}])
        return codes([r1, r2]) == [400, 200], f"비율 변경/예정일만 = {codes([r1, r2])}"
    await s.case("DL-030", "입금 확인한 회차 비율 못 바꿈", dl030)

    async def dl031():
        d = await x.ready()
        await x.terms(d["id"], [{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70}])
        r1 = await x.post(d["id"], "/confirm-term", "lucas", json={"index": 0})
        r2 = await x.post(d["id"], "/confirm-term", "lucas", json={"index": 0})
        r3 = await x.post(d["id"], "/confirm-term", "lucas", json={"index": 5})
        j = r1.json()
        ok = codes([r1, r2, r3]) == [200, 400, 404] and j["payments"][-1]["amount"] == 600000 and not j["paid_full"]
        return ok, f"상태 {codes([r1, r2, r3])} 입금 {j['payments'][-1].get('amount')} 완납 {j['paid_full']}"
    await s.case("DL-031", "회차 입금 확인·중복·없는 회차", dl031)

    async def dl032():
        d = await x.ready()
        await x.terms(d["id"], [{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70}])
        await x.post(d["id"], "/confirm-term", "lucas", json={"index": 0})
        r1 = await x.post(d["id"], "/unconfirm-term", "lucas", json={"index": 0})
        r2 = await x.post(d["id"], "/unconfirm-term", "alex", json={"index": 0})
        return codes([r1, r2]) == [403, 200] and r2.json()["payments"] == [], f"상태 {codes([r1, r2])}"
    await s.case("DL-032", "입금 확인 취소는 관리자만", dl032)

    async def dl033():
        d = await x.ready()
        await x.terms(d["id"], [{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70}])
        await x.post(d["id"], "/confirm-term", "lucas", json={"index": 0})
        j = (await x.post(d["id"], "/confirm-term", "lucas", json={"index": 1})).json()
        assert j["paid_full"], "두 회차 확인 후 완납이어야 함"
        r = await x.post(d["id"], "/remove", "alex", json={"kind": "payments", "index": 0})
        j = r.json()
        return r.status_code == 200 and not j["paid_full"], f"{r.status_code} 회차 입금 1줄 삭제 후 완납={j.get('paid_full')} 단계={j.get('stage')}"
    await s.case("DL-033", "회차 입금 지우면 완납 풀림", dl033)

    async def dl034():
        d = await x.ready(items=[{"name": "A", "unit_price": 1000000, "qty": 1}])
        r = await x.post(d["id"], "/payments", "lucas", json={"date": D, "amount": 5000000, "full": False})
        return ("CHECK", f"공급가액 100만 건에 500만 입금 → {r.status_code} (초과 입금 경고·거절 정책 확인)")
    await s.case("DL-034", "받을 금액보다 많은 입금", dl034)

    async def dl035():
        d = await x.new(items=[{"name": "A", "unit_price": 10000, "qty": 1}], currency="USD")
        await x.won(d["id"])
        r = await x.post(d["id"], "/payments", "lucas", json={"date": D, "amount": 9001, "full": False})
        j = r.json()
        return r.status_code == 200 and not j["paid_full"], f"$10,000 중 $9,001 입금 → 완납={j.get('paid_full')}"
    await s.case("DL-035", "USD 건 일부 입금이 완납 처리 안 됨", dl035)

    async def dl037():
        d = await x.ready()
        bads = [{"amount": 0}, {"amount": -1}, {"amount": 100000000001}, {"date": "2026-13-01"}]
        rs = [await x.post(d["id"], "/payments", "lucas", json={"date": D, "amount": 1000, **b}) for b in bads]
        return all(c == 422 for c in codes(rs)), f"상태 {codes(rs)}"
    await s.case("DL-037", "입금 잘못된 값 422", dl037)

    async def dl038():
        d = await x.ready()
        await x.post(d["id"], "/paid-full", "alex", json={"full": True})
        r1 = await x.post(d["id"], "/paid-full", "lucas", json={"full": False})
        r2 = await x.post(d["id"], "/paid-full", "lucas", json={"full": True})
        return codes([r1, r2]) == [403, 200], f"상태 {codes([r1, r2])}"
    await s.case("DL-038", "완납 해제는 관리자만", dl038)

    # ── 출하 ──
    async def dl039():
        d = await x.new()
        r = await x.post(d["id"], "/shipments", "lucas", json={"date": D, "carrier": "CJ", "tracking": "1234 5678",
                                                               "receiver": "김수령", "address": "부산", "purpose": "판매"})
        j = r.json()
        return r.status_code == 200 and j["won"] is True and j["won_date"] == D, f"{r.status_code} won={j.get('won')} {j.get('won_date')} 단계={j.get('stage')}"
    await s.case("DL-039", "수주 전 출하 → 자동 수주", dl039)

    async def dl040():
        d = await x.ready()
        ship = {"date": D, "purpose": "판매"}
        rs = [await x.post(d["id"], "/shipments", "lucas", json={**ship, "purpose": "반품"}),
              await x.post(d["id"], "/shipments", "lucas", json={**ship, "lines": [{"name": "A", "photos": ["../x.jpg"]}]}),
              await x.post(d["id"], "/shipments", "lucas", json={**ship, "lines": [{"name": "A", "photos": ["a" * 32 + ".jpg"] * 21}]})]
        r4 = await x.post(d["id"], "/shipments", "lucas", json={**ship, "date": "2020-01-01"})
        got = codes(rs)
        if got != [422, 400, 422]:
            return False, f"반품/경로/21장 = {got}"
        return ("CHECK", f"반품/경로/21장 = {got} 정상. 수주일 이전 출하(2020-01-01) → {r4.status_code} (경고 정책 확인)")
    await s.case("DL-040", "출하 잘못된 입력", dl040)

    async def dl041():
        d = await x.ready()
        did = d["id"]
        rs = [await x.post(did, "/ship-photos", "lucas", files={"file": ("a.gif", b"GIF89a", "image/gif")}),
              await x.post(did, "/ship-photos", "lucas", files={"file": ("big.jpg", b"\xff\xd8" + b"0" * (16 * 1024 * 1024), "image/jpeg")}),
              await x.post(did, "/ship-photos", "lucas", files={"file": ("zero.jpg", b"", "image/jpeg")})]
        ok_up = await x.post(did, "/ship-photos", "lucas", files={"file": ("ok.jpg", jpg_bytes(), "image/jpeg")})
        trav = [await s.call("GET", f"/v1/sales-deals/{did}/ship-photos/..%2F..%2Fx.jpg", "lucas"),
                await s.call("GET", "/v1/sales-deals/photos/..%2Fapp.py", "lucas"),
                await s.call("GET", "/v1/sales-deals/photos/ship_r1_1.jpg.exe", "lucas")]
        got, gt = codes(rs), codes(trav)
        pid = ok_up.json().get("id", "")
        ok = got[0] == 415 and got[1] == 413 and got[2] == 413 and len(pid) == 36 and all(c == 404 for c in gt)
        return ok, f"gif/16MB/0B = {got}, 정상 id={pid}, 경로 조작 = {gt}"
    await s.case("DL-041", "출하 사진 형식·크기·경로 조작", dl041)

    async def dl042():
        d = await x.ready()
        for c in ("A", "B"):
            await x.post(d["id"], "/shipments", "lucas", json={"date": D, "carrier": c, "purpose": "판매"})
        r1 = await x.post(d["id"], "/remove", "alex", json={"kind": "shipments", "index": 0})
        r2 = await x.post(d["id"], "/remove", "victor", json={"kind": "shipments", "index": 0})   # 오래된 화면에서 같은 줄
        left = (await x.get(d["id"]))["shipments"]
        return ("CHECK", f"출하 A·B 에서 0번 삭제를 두 번 → {codes([r1, r2])}, 남은 출하 {len(left)}개 "
                         f"(두 번째 요청이 B 까지 지움 = 줄 번호 기반 삭제, 동시 편집 보호 필요 여부 확인)")
    await s.case("DL-042", "같은 줄 삭제 연속 요청", dl042)

    async def dl043():
        d = await x.ready()
        rs = [await x.post(d["id"], "/remove", "alex", json=b) for b in
              ({"kind": "payments", "index": -1}, {"kind": "payments", "index": 99}, {"kind": "quote", "index": 0})]
        return codes(rs) == [422, 404, 422], f"상태 {codes(rs)}"
    await s.case("DL-043", "줄 삭제 잘못된 번호·종류", dl043)

    async def dl044():
        d = await x.ready()
        await x.post(d["id"], "/shipments", "lucas", json={"date": D, "carrier": "CJ", "purpose": "판매"})
        r1 = await x.post(d["id"], "/shipment-edit", "lucas", json={"index": 0, "date": "2026-10-16", "carrier": "CJ", "purpose": "판매"})
        r2 = await x.post(d["id"], "/shipment-edit", "lucas", json={"index": 3, "date": "2026-10-16", "purpose": "판매"})
        ok = codes([r1, r2]) == [200, 404] and r1.json()["shipments"][0]["date"] == "2026-10-16"
        return ok, f"상태 {codes([r1, r2])}"
    await s.case("DL-044", "출하 기록 고치기", dl044)

    # ── 드랍·수주 상태 ──
    async def dl045():
        d = await x.new()
        r = await x.post(d["id"], "/drop", "lucas", json={"dropped": True, "reason": "예산 취소"})
        after = [await x.post(d["id"], "/payments", "lucas", json={"date": D, "amount": 1000}),
                 await x.post(d["id"], "/shipments", "lucas", json={"date": D, "purpose": "판매"})]
        j = await x.get(d["id"])
        return ("CHECK", f"드랍 {r.status_code}, 드랍 뒤 입금·출하 = {codes(after)}, 지금 won={j['won']} 단계={j['stage']} "
                         "(드랍 건에 기록 허용 여부 정책 확인)")
    await s.case("DL-045", "드랍한 건에 입금·출하 기록", dl045)

    async def dl046():
        d = await x.new()
        await x.post(d["id"], "/drop", "lucas", json={"dropped": True, "reason": "취소"})
        r1 = await x.post(d["id"], "/drop", "lucas", json={"dropped": False})
        r2 = await x.post(d["id"], "/drop", "alex", json={"dropped": False})
        return codes([r1, r2]) == [403, 200] and r2.json()["stage"] != "드랍", f"상태 {codes([r1, r2])}"
    await s.case("DL-046", "드랍 되살리기는 관리자만", dl046)

    async def dl048():
        d = await x.new()
        await x.won(d["id"])
        r1 = await x.post(d["id"], "/won", "lucas", json={"won": None})
        r2 = await x.post(d["id"], "/won", "lucas", json={"won": False})
        return codes([r1, r2]) == [403, 400], f"상태 {codes([r1, r2])}"
    await s.case("DL-048", "미확인 되돌리기 관리자만·실주 없음", dl048)

    async def dl049():
        d = await x.new()
        await x.post(d["id"], "/won", "alex", json={"won": True, "date": "2026-10-01"})
        j = (await x.post(d["id"], "/won", "lucas", json={"won": True})).json()
        return j["won_date"] == "2026-10-01", f"수주일 2026-10-01 인 건에 날짜 없이 수주 다시 → {j['won_date']}"
    await s.case("DL-049", "수주 다시 눌러도 수주일 유지", dl049)

    # ── 되돌리기·삭제·권한 ──
    async def dl052():
        d = await x.ready()
        r1 = await x.post(d["id"], "/reset-to-quote", "lucas")
        async with SessionLocal() as db:
            imp = (await db.execute(text("SELECT id FROM sales_deals WHERE imported LIMIT 1"))).scalar()
        if imp is None:
            return r1.status_code == 403, f"멤버 {r1.status_code} (이관 건 없음 — 이관 건 확인은 생략)"
        r2 = await s.call("POST", f"/v1/sales-deals/{imp}/reset-to-quote", "alex")
        return codes([r1, r2]) == [403, 400], f"멤버/이관 건 = {codes([r1, r2])}"
    await s.case("DL-052", "견적으로 되돌리기 권한·이관 건 보호", dl052)

    async def dl053():
        got = {}
        for u in ("lucas", "mason", "charles", "finance_admin", "alex", "victor", "superadmin"):
            d = await x.new()
            r = await s.call("POST", "/v1/sales-deals/delete", u, json={"ids": [d["id"]]})
            got[u] = r.status_code
        async with SessionLocal() as db:
            trash = (await db.execute(text("SELECT count(*) FROM sales_deal_trash WHERE deal->>'customer' LIKE :l"),
                                      {"l": f"%{tc.MARK}%"})).scalar()
        want = {"lucas": 403, "mason": 403, "charles": 403, "finance_admin": 403, "alex": 200, "victor": 200, "superadmin": 200}
        return got == want and trash >= 3, f"{got} 보관함 {trash}건"
    await s.case("DL-053", "삭제 권한표·보관함", dl053)

    async def dl054():
        a = await x.new()
        rs = [await s.call("POST", "/v1/sales-deals/delete", "alex", json={"ids": []}),
              await s.call("POST", "/v1/sales-deals/delete", "alex", json={"ids": [a["id"], 999999999]}),
              await s.call("POST", "/v1/sales-deals/delete", "alex", json={"ids": [-1]}),
              await s.call("POST", "/v1/sales-deals/delete", "alex", json={"ids": list(range(1, 202))})]
        still = (await s.call("GET", f"/v1/sales-deals/{a['id']}", "alex")).status_code
        got = codes(rs)
        return got[0] == 422 and got[1] == 409 and got[2] in (400, 409, 422) and got[3] == 422 and still == 200, f"상태 {got}, a 남음={still}"
    await s.case("DL-054", "삭제 잘못된 요청(없는 건 섞이면 하나도 안 지움)", dl054)

    async def dl056():
        d = await x.new()
        await s.call("POST", "/v1/sales-deals/delete", "alex", json={"ids": [d["id"]]})
        rs = [await s.call("GET", f"/v1/sales-deals/{d['id']}", "alex"),
              await x.post(d["id"], "/payments", "alex", json={"date": D, "amount": 1}),
              await s.call("GET", f"/v1/sales-deals/{d['id']}/statement/file", "alex"),
              await s.call("GET", "/v1/sales-deals/abc", "alex")]
        got = codes(rs)
        return got[:3] == [404, 404, 404] and got[3] in (404, 422), f"상태 {got}"
    await s.case("DL-056", "지운 건·문자 id 접근", dl056)

    async def dl057():
        d = await x.new()
        paths = ["/v1/sales-deals", "/v1/sales-deals/export", f"/v1/sales-deals/{d['id']}", "/v1/sales-deals/approvals/count"]
        fin = [(await s.call("GET", p, "finance_admin")).status_code for p in paths]
        sup = [(await s.call("GET", p, "superadmin")).status_code for p in paths]
        return fin == [403] * 4 and sup == [200] * 4, f"finance_admin {fin} / superadmin {sup}"
    await s.case("DL-057", "재무 관리자 차단·최고 관리자 허용", dl057)

    async def dl057b():
        r = await s.page("/api/sales-deals", "finance_admin")
        r2 = await s.page("/api/sales-deals", None)
        # 로그인 없으면 401 또는 로그인 화면으로 보냄(307) — 둘 다 막힌 것
        return r.status_code == 403 and r2.status_code in (401, 307), f"화면 경유: finance_admin {r.status_code} / 로그인 없음 {r2.status_code}"
    await s.case("DL-057b", "화면 경유(BFF)에서도 같은 권한", dl057b)

    # ── 입금 예정일 연장 승인 ──
    async def overdue_deal() -> dict:
        d = await x.ready()
        yday = (TODAY - dt.timedelta(days=1)).isoformat()
        r = await x.terms(d["id"], [{"label": "선금", "pct": 30, "expected_date": yday}, {"label": "잔금", "pct": 70}])
        assert r.status_code == 200, f"지난 예정일 조건 저장 실패 {r.status_code} {r.text[:120]}"
        return d

    async def dl059():
        d = await overdue_deal()
        new_due = (TODAY + dt.timedelta(days=30)).isoformat()
        r = await x.post(d["id"], "/due-request", "lucas", json={"index": 0, "date": new_due, "reason": f"{tc.MARK} 고객 협의",
                                                                  "recipients": [uid["alex"]]})
        c1 = (await s.call("GET", "/v1/sales-deals/approvals/count", "alex")).json()
        rid = next((i["id"] for i in c1.get("items", []) if i.get("deal_id") == d["id"]), None)
        await s.call("POST", "/v1/sales-deals/approvals/seen", "alex", json={"ids": [rid]} if rid else {"ids": []})
        c2 = (await s.call("GET", "/v1/sales-deals/approvals/count", "alex")).json()
        cv = (await s.call("GET", "/v1/sales-deals/approvals/count", "victor")).json()
        seen_gone = all(i.get("id") != rid for i in c2.get("items", []))
        ok = r.status_code == 200 and rid is not None and seen_gone and c2.get("pending", 0) >= 1
        return ok, f"요청 {r.status_code}, alex 알림 rid={rid}, 본 뒤 알림 사라짐={seen_gone} 대기={c2.get('pending')}, victor 대기={cv.get('pending')}"
    await s.case("DL-059", "연장 요청 → 관리자 알림·확인", dl059)

    async def dl060():
        d = await overdue_deal()
        nd = (TODAY + dt.timedelta(days=30)).isoformat()
        base = {"index": 0, "date": nd, "reason": "협의", "recipients": [uid["alex"]]}
        rs = [await x.post(d["id"], "/due-request", "lucas", json={**base, "reason": ""}),
              await x.post(d["id"], "/due-request", "lucas", json={**base, "recipients": [uid["lucas"]]}),
              await x.post(d["id"], "/due-request", "lucas", json={**base, "date": D}),
              await x.post(d["id"], "/due-request", "lucas", json={**base, "index": 1}),
              await x.post(d["id"], "/due-request", "lucas", json={**base, "index": 11})]
        await x.post(d["id"], "/due-request", "lucas", json=base)
        dup = await x.post(d["id"], "/due-request", "lucas", json=base)
        got = codes(rs) + [dup.status_code]
        return got == [400, 400, 400, 400, 422, 400], f"사유없음/관리자아님/오늘/예정일안지남/11번/중복 = {got}"
    await s.case("DL-060", "연장 요청 잘못된 입력", dl060)

    async def dl061():
        d = await overdue_deal()
        nd = (TODAY + dt.timedelta(days=30)).isoformat()
        await x.post(d["id"], "/due-request", "lucas", json={"index": 0, "date": nd, "reason": "협의", "recipients": [uid["alex"]]})
        async with SessionLocal() as db:
            rid = (await db.execute(text("SELECT id FROM approval_requests WHERE deal_id = :d"), {"d": d["id"]})).scalar()
        r1 = await s.call("POST", f"/v1/sales-deals/approvals/{rid}/decide", "lucas", json={"approve": True})
        r2 = await s.call("POST", f"/v1/sales-deals/approvals/{rid}/decide", "victor", json={"approve": True})
        r3 = await s.call("POST", f"/v1/sales-deals/approvals/{rid}/decide", "alex", json={"approve": True})
        due = (await x.get(d["id"]))["pay_terms"][0]["expected_date"]
        return codes([r1, r2, r3]) == [403, 200, 400] and due == nd, f"멤버/다른 관리자/재처리 = {codes([r1, r2, r3])}, 예정일 {due}"
    await s.case("DL-061", "승인은 관리자만, 한 번만", dl061)

    async def dl062():
        d = await overdue_deal()
        nd = (TODAY + dt.timedelta(days=30)).isoformat()
        r = await x.post(d["id"], "/due-request", "alex", json={"index": 0, "date": nd, "reason": "협의", "recipients": [uid["alex"]]})
        async with SessionLocal() as db:
            rid = (await db.execute(text("SELECT id FROM approval_requests WHERE deal_id = :d"), {"d": d["id"]})).scalar()
        r2 = await s.call("POST", f"/v1/sales-deals/approvals/{rid}/decide", "alex", json={"approve": True}) if rid else None
        return ("CHECK", f"관리자 본인 요청 {r.status_code} → 본인 승인 {r2.status_code if r2 else '-'} (셀프 승인 허용 여부 정책 확인)")
    await s.case("DL-062", "본인 요청을 본인이 승인", dl062)

    async def dl063():
        d = await overdue_deal()
        nd = (TODAY + dt.timedelta(days=30)).isoformat()
        await x.post(d["id"], "/due-request", "lucas", json={"index": 0, "date": nd, "reason": "협의", "recipients": [uid["alex"]]})
        async with SessionLocal() as db:
            rid = (await db.execute(text("SELECT id FROM approval_requests WHERE deal_id = :d"), {"d": d["id"]})).scalar()
        p = f"/v1/sales-deals/approvals/{rid}/decide"
        rs = [await s.call("POST", p, "alex", json={"approve": False, "note": ""}),
              await s.call("POST", p, "alex", json={"approve": True, "date": "2026-10-01"})]
        async with SessionLocal() as db:
            st = (await db.execute(text("SELECT status FROM approval_requests WHERE id = :i"), {"i": rid})).scalar()
        return codes(rs) == [400, 400] and st == "pending", f"사유없는 거절/과거 날짜 = {codes(rs)}, 상태 {st}"
    await s.case("DL-063", "승인·거절 입력 검사", dl063)

    async def dl064():
        d = await x.ready()
        yday = (TODAY - dt.timedelta(days=1)).isoformat()
        await x.terms(d["id"], [{"label": "선금", "pct": 30}, {"label": "중도금", "pct": 30}, {"label": "잔금", "pct": 40, "expected_date": yday}])
        nd = (TODAY + dt.timedelta(days=30)).isoformat()
        await x.post(d["id"], "/due-request", "lucas", json={"index": 2, "date": nd, "reason": "협의", "recipients": [uid["alex"]]})
        await x.terms(d["id"], [{"label": "선금", "pct": 50}, {"label": "잔금", "pct": 50}])
        await x.terms(d["id"], [{"label": "계약금", "pct": 10}, {"label": "중도금", "pct": 10}, {"label": "검수", "pct": 80}])
        async with SessionLocal() as db:
            rid = (await db.execute(text("SELECT id FROM approval_requests WHERE deal_id = :d"), {"d": d["id"]})).scalar()
        r = await s.call("POST", f"/v1/sales-deals/approvals/{rid}/decide", "alex", json={"approve": True})
        t = (await x.get(d["id"]))["pay_terms"][2]
        return r.status_code != 200 or t.get("expected_date") != nd, \
            f"잔금 연장 요청 후 조건을 바꿔 3번째가 '{t['label']}' 인데 승인 {r.status_code} → 그 회차 예정일 {t.get('expected_date')}"
    await s.case("DL-064", "결제 조건 바뀐 뒤 옛 연장 요청 승인 막힘", dl064)

    async def dl065():
        d = await overdue_deal()
        nd = (TODAY + dt.timedelta(days=30)).isoformat()
        await x.post(d["id"], "/due-request", "lucas", json={"index": 0, "date": nd, "reason": "협의", "recipients": [uid["alex"]]})
        await x.post(d["id"], "/confirm-term", "lucas", json={"index": 0})
        async with SessionLocal() as db:
            rid = (await db.execute(text("SELECT id FROM approval_requests WHERE deal_id = :d"), {"d": d["id"]})).scalar()
        r = await s.call("POST", f"/v1/sales-deals/approvals/{rid}/decide", "alex", json={"approve": True})
        return ("CHECK", f"이미 입금 확인한 회차의 연장 요청 승인 → {r.status_code} (안내·자동 정리 필요 여부 확인)")
    await s.case("DL-065", "입금 확인한 회차의 연장 요청 승인", dl065)

    # ── 엑셀 내보내기·KPI·시간대 ──
    async def dl066():
        await x.new(customer='=HYPERLINK("http://x","클릭")', title='한글·特殊©"쉼표,')
        r = await s.call("GET", "/v1/sales-deals/export", "alex")
        cd = r.headers.get("content-disposition", "")
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        formula = [f"{ws.title}!{c.coordinate}" for ws in wb for row in ws.iter_rows() for c in row
                   if c.data_type == "f" and "HYPERLINK" in str(c.value)]
        return r.status_code == 200 and not formula and len(wb.sheetnames) == 3, \
            f"{r.status_code} 시트 {wb.sheetnames}, 수식으로 저장된 고객사 칸 {formula[:3]}, 파일명 {cd[-60:]}"
    await s.case("DL-066", "엑셀 내보내기: 수식 주입 안 됨", dl066)

    async def dl067():
        d = await x.new(customer="제어\u0001문자")
        try:
            r = await s.call("GET", "/v1/sales-deals/export", "alex")
            return r.status_code == 200, f"제어문자 고객사 한 건 있을 때 내보내기 {r.status_code} (500 이면 모든 사용자 내보내기가 막힘)"
        finally:                                    # 뒤 케이스가 내보내기를 쓰므로 바로 지운다
            await s.call("POST", "/v1/sales-deals/delete", "alex", json={"ids": [d["id"]]})
    await s.case("DL-067", "고객사명 제어문자 → 내보내기 실패 안 함", dl067)

    async def dl068():
        d = await overdue_deal()
        nd = (TODAY + dt.timedelta(days=30)).isoformat()
        await x.post(d["id"], "/due-request", "lucas", json={"index": 0, "date": nd, "reason": "협의", "recipients": [uid["alex"]]})
        r = await s.call("GET", "/v1/sales-deals/export", "alex")
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        raw = sorted({str(c.value) for ws in wb for row in ws.iter_rows() for c in row
                      if str(c.value) in ("due_extend", "due_request", "due_reject", "quote_replace", "recommend_new")})
        return not raw, f"엑셀에 영어 코드 그대로: {raw}"
    await s.case("DL-068", "엑셀 이력에 영어 코드 안 나옴", dl068)

    async def dl069():
        async with SessionLocal() as db:
            mixed = (await db.execute(text("SELECT count(DISTINCT quote->>'currency') FROM sales_deals WHERE won"))).scalar()
        k = (await x.get_list())["kpi"] if hasattr(x, "get_list") else (await s.call("GET", "/v1/sales-deals", "alex")).json()["kpi"]
        return ("CHECK", f"수주 건 통화 종류 {mixed}개, KPI 키 {list(k)[:6]} — 원화·달러를 따로 보여 주는지 화면에서 확인(코드상 단순 합산)")
    await s.case("DL-069", "분기 KPI 원화·달러 합산", dl069)

    async def dl070():
        utc_today = dt.datetime.now(dt.timezone.utc).date()
        lst = (await s.call("GET", "/v1/sales-deals", "alex")).json()
        if utc_today == TODAY:
            return ("CHECK", f"지금은 UTC·한국 날짜가 같음({TODAY}) — 서버 시계 UTC 확인됨. 한국 오전 9시 전 실행 시 리스트 today={lst.get('today')} 가 하루 전이면 결함")
        return lst.get("today") == D, f"리스트 today={lst.get('today')} 한국 {D}"
    await s.case("DL-070", "서버 시계(UTC)와 한국 날짜", dl070)

    async def dl071():
        d = await x.ready()
        did = d["id"]
        try:
            async with SessionLocal() as db:            # 수주 진행 대화가 넘기는 값과 같은 경로(set_terms)로 없는 날짜
                try:
                    await svc.set_terms(db, did, None, [{"label": "선금", "pct": 30, "expected_date": "2026-02-30"},
                                                        {"label": "잔금", "pct": 70}])
                    saved = True
                except svc.DealError:
                    saved = False
                except ValueError as e:
                    return False, f"없는 날짜가 검사를 통과해 기록 단계에서 예외({e}) — 저장은 안 되지만 대화에 안내 대신 오류"
            if not saved:
                return True, "없는 날짜 거절됨"
            r = await s.call("GET", "/v1/sales-deals", "lucas")
            return False, f"'2026-02-30' 이 저장됨 → 리스트 {r.status_code} (500 이면 모든 사용자 리스트가 깨짐)"
        finally:
            async with SessionLocal() as db:
                await db.execute(text("UPDATE sales_deals SET pay_terms = '[]'::jsonb WHERE id = :i"), {"i": did})
                await db.commit()
    await s.case("DL-071", "없는 날짜(2월 30일) 결제 조건 거절", dl071)

    async def dl074():
        d = await x.ready()
        r1 = await x.post(d["id"], "/order/ship", "lucas", json={"ship": {"lines": [{"photos": ["../../a.jpg"]}]}})
        r2 = await x.post(d["id"], "/order/ship", "lucas", json={"ship": {"address": "가" * 21000}})
        return codes([r1, r2]) == [400, 400], f"경로 사진/20KB 초과 = {codes([r1, r2])}"
    await s.case("DL-074", "수주 진행 출하 정보 검사", dl074)

    async def dl077():
        d = await x.ready()
        a = await x.terms(d["id"], [{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70}], user="alex")
        b = await x.terms(d["id"], [{"label": "잔금", "pct": 100}], user="victor")
        v = (await x.get(d["id"]))["version"]
        return ("CHECK", f"두 사람이 차례로 저장 → {codes([a, b])}, 마지막 저장이 그대로 덮어씀(version={v}) — 충돌 안내 필요 여부 확인")
    await s.case("DL-077", "동시 편집 충돌 안내", dl077)


if __name__ == "__main__":
    tc.main("deals", body)
