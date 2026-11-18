"""되돌리기 점검(사용자 2026-11-18) — 영업팀 일반 계정(관리자 아님)으로 영업 건·구매 건을 끝까지 진행한 뒤,
상세 화면의 되돌리기 버튼이 부르는 API 만으로 한 단계씩 거슬러 처음 단계까지 돌아갈 수 있는지 본다.
막히는 단계가 있으면 FAIL 과 서버 문장을 남긴다. 테스트 건은 끝에 지운다(관리자 토큰으로).

    MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/qa_sales/rollback_check.py
"""
import asyncio
import datetime as dt
import sys

import httpx
from sqlalchemy import text

from app.database import SessionLocal
from app.security import create_access_token

D = "http://frontend:3000/api/sales-deals"
P = "http://frontend:3000/api/purchases"
MARK = "__QA_되돌리기__"
MEMBER = "mason"          # 영업팀 일반 계정(관리자 아님)
ADMIN = "alex"
TODAY = dt.date.today()
fails: list[str] = []


def report(ok: bool, name: str, *more) -> None:
    print("OK  " if ok else "FAIL", name, *more)
    if not ok:
        fails.append(name)


async def tokens() -> tuple[str, str]:
    async with SessionLocal() as db:
        ids = dict((await db.execute(text("SELECT username, id FROM users WHERE username = ANY(:u)"), {"u": [MEMBER, ADMIN]})).all())
        await db.execute(text("DELETE FROM purchase_events WHERE purchase_id IN (SELECT id FROM purchases WHERE supplier LIKE :m)"), {"m": MARK + "%"})
        await db.execute(text("DELETE FROM purchases WHERE supplier LIKE :m"), {"m": MARK + "%"})
        await db.commit()
    return create_access_token(str(ids[MEMBER]))[0], create_access_token(str(ids[ADMIN]))[0]


def client(tok: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=120, cookies={"und_cortex_token": tok})


async def call(c: httpx.AsyncClient, url: str, body=None, files=None):
    r = await c.post(url, json=body if files is None else None, files=files)
    try:
        js = r.json()
    except Exception:
        js = None
    return r.status_code, js


def detail(js) -> str:
    return (js or {}).get("detail", "") if isinstance(js, dict) else str(js)


async def step(c, name: str, url: str, body=None, *, expect_stage: str | None = None, key: str = "stage", files=None):
    st, js = await call(c, url, body, files)
    ok = st == 200 and (expect_stage is None or (js or {}).get(key) == expect_stage)
    report(ok, name, f"[{st}]", (js or {}).get(key) if st == 200 else detail(js)[:160])
    return js if st == 200 else None


# ────────────────────────── 영업 건 ──────────────────────────
async def deal_flow(c, admin_c) -> int | None:
    print("\n== 영업 건 (일반 계정 mason) ==")
    st, d = await call(c, D, {"customer": MARK + "고객사", "title": "되돌리기 점검", "quote_date": TODAY.isoformat(), "owner": "Lucas",
                              "items": [{"name": "MG10 그리퍼", "unit_price": 1_000_000, "qty": 2}]})
    report(st in (200, 201), "새 건 등록(견적 단계)", st, detail(d)[:120] if st not in (200, 201) else d.get("stage"))
    if st not in (200, 201):
        return None
    did = d["id"]
    first_stage = d["stage"]
    # ── 끝까지 진행 ──
    await step(c, "→ 수주 확정", f"{D}/{did}/won", {"won": True, "date": TODAY.isoformat()})
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    st, js = await call(c, f"{D}/{did}/po", files={"file": ("발주서.pdf", pdf, "application/pdf")})
    report(st == 200, "→ 발주서 첨부", st, detail(js)[:120] if st != 200 else "")
    await step(c, "→ 거래명세서 발급", f"{D}/{did}/statement",
               {"initials": "ZZ", "date": TODAY.isoformat(), "buyer": {"reg_no": "123-45-67890", "name": MARK + "고객사", "address": "서울"},
                "items": [{"name": "MG10 그리퍼", "qty": 2, "unit_price": 1_000_000}]})
    d = await step(c, "→ 결제 조건", f"{D}/{did}/terms", {"terms": [{"label": "선금", "pct": 50}, {"label": "잔금", "pct": 50}]})
    terms = (d or {}).get("pay_terms") or []
    for i, t in enumerate(terms):
        d = await step(c, f"→ {t['label']} 입금 확인", f"{D}/{did}/confirm-term", {"index": i, "date": TODAY.isoformat(), "expect": {"label": t["label"], "pct": t["pct"]}})
    d = await step(c, "→ 출하 기록", f"{D}/{did}/shipments", {"date": TODAY.isoformat(), "carrier": "직납", "lines": [{"name": "MG10 그리퍼", "qty": 2}], "force": True})
    print("   끝까지 진행한 단계:", (d or {}).get("stage"))
    st, js = await call(c, f"{D}/{did}/statement/cancel", {})
    report(st == 409 and (js or {}).get("code") == "STATEMENT_PAID", "(순서 안내) 입금이 남은 채 명세서 취소 → 막고 입금 칸 안내", st, (js or {}).get("code"))
    # ── 거꾸로 되돌리기(상세 화면 버튼 순서) ──
    print("  -- 되돌리기 --")
    st, d = await call(c, f"{D}/{did}")      # 최신
    d = (await c.get(f"{D}/{did}")).json()
    if d.get("shipments"):
        d = await step(c, "← 출하 취소", f"{D}/{did}/remove", {"kind": "shipments", "index": 0, "expect": d["shipments"][0]})
    d = (await c.get(f"{D}/{did}")).json()
    for i, t in reversed(list(enumerate(d.get("pay_terms") or []))):
        await step(c, f"← {t['label']} 입금 확인 취소", f"{D}/{did}/unconfirm-term", {"index": i, "expect": {"label": t["label"], "pct": t["pct"]}})
    d = (await c.get(f"{D}/{did}")).json()
    if d.get("paid_full"):
        await step(c, "← 완납 해제", f"{D}/{did}/paid-full", {"full": False})
    d = (await c.get(f"{D}/{did}")).json()
    d = await step(c, "← 결제 조건 지우기", f"{D}/{did}/terms/clear", {})
    report(bool(d) and not d.get("pay_case") and not d.get("pay_terms"), "   결제 조건 비었음")
    d = await step(c, "← 거래명세서 발급 취소", f"{D}/{did}/statement/cancel", {})
    report(bool(d) and not d.get("statement"), "   거래명세서 비었음")
    for i in reversed(range(len(d.get("po_files") or []))):
        await step(c, "← 발주서 파일 지우기", f"{D}/{did}/remove", {"kind": "po_files", "index": i, "expect": d["po_files"][i]})
    d = await step(c, "← 수주 미확인으로 되돌리기", f"{D}/{did}/won", {"won": None})
    d = (await c.get(f"{D}/{did}")).json()
    report(d.get("stage") == first_stage, "처음 단계로 돌아옴", f"처음={first_stage} 지금={d.get('stage')}",
           "| 남은 것:", ", ".join(k for k in ("statement", "pay_terms", "payments", "shipments", "po_files") if d.get(k)) or "없음")
    # 비교용 — 관리자 전용 '테스트용 전부 되돌리기'를 일반 계정이 부르면
    st, js = await call(c, f"{D}/{did}/reset-to-quote", {})
    report(True, "(참고) 일반 계정의 '테스트용 전부 되돌리기'", st, detail(js)[:80])
    return did


# ────────────────────────── 구매 건 ──────────────────────────
async def purchase_flow(c, region: str) -> int | None:
    over = region == "overseas"
    print(f"\n== 구매 건 {'해외' if over else '국내'} (일반 계정 mason) ==")
    st, p = await call(c, P, {"supplier": MARK + ("해외" if over else "국내"), "title": "되돌리기 점검", "region": region})
    report(st in (200, 201), "새 구매 건", st, (p or {}).get("stage"))
    if st not in (200, 201):
        return None
    pid = p["id"]
    first_stage = p["stage"]
    await step(c, "→ 견적서 없이 넘어가기", f"{P}/{pid}/quote/skip", {"value": True})
    await step(c, "→ 재가 완료", f"{P}/{pid}/approve", {"date": TODAY.isoformat()})
    draft = (await c.get(f"{P}/{pid}/po/draft")).json()
    hdr = {**draft["header"], "issuer": {**draft["header"].get("issuer", {}), "name": "QA 담당", "attn": "QA"},
           "supplier": {**(draft["header"].get("supplier") or {}), "name": MARK}}
    p = await step(c, "→ 발주서 발행", f"{P}/{pid}/po/issue",
                   {"initials": "ZZ", "expected_date": (TODAY + dt.timedelta(days=10)).isoformat(), "header": hdr,
                    "items": [{"description": "TEST\n- item", "qty": 2, "price": 100}]})
    if over:
        await step(c, "→ 제조사 송금 확인", f"{P}/{pid}/paid", {"date": TODAY.isoformat(), "note": ""})
    else:
        p = await step(c, "→ 결제 조건", f"{P}/{pid}/terms", {"terms": [{"label": "선금", "pct": 50}, {"label": "잔금", "pct": 50}]})
        for i in range(2):
            await step(c, f"→ {i + 1}회차 송금 확인", f"{P}/{pid}/terms/{i}/confirm", {"date": TODAY.isoformat()})
    await step(c, "→ 배송 시작", f"{P}/{pid}/ship", {"date": TODAY.isoformat(), "note": ""})
    if over:
        await step(c, "→ 원산지 증명서 없음", f"{P}/{pid}/customs/no-origin", {"value": True})
        pdf = b"%PDF-1.4\n%%EOF"
        st, js = await call(c, f"{P}/{pid}/files/customs_etc", files={"file": ("ci.pdf", pdf, "application/pdf")})
        await step(c, "→ 통관 서류 제출 완료", f"{P}/{pid}/customs", {"date": TODAY.isoformat(), "note": ""})
        await step(c, "→ 관세사 송금 확인", f"{P}/{pid}/broker", {"date": TODAY.isoformat(), "note": ""})
    p = await step(c, "→ 입고 기록(입고 완료)", f"{P}/{pid}/receipt", {"date": TODAY.isoformat(), "lines": [{"line_no": 0, "qty": 2}], "final": True})
    if not over:
        await step(c, "→ 명세서 선결제 기록", f"{P}/{pid}/statement", {"mode": "prepaid", "date": TODAY.isoformat(), "note": ""})
    print("   끝까지 진행한 단계:", (p or {}).get("stage"))
    st, js = await call(c, f"{P}/{pid}/po/cancel", {})
    det = (js or {}).get("detail") if isinstance(js, dict) else None
    report(st == 409 and isinstance(det, dict) and det.get("code") == "PO_LOCKED" and det.get("step") == "receipt",
           "(순서 안내) 입고가 남은 채 발주서 취소 → 막고 입고 칸 안내", st, det.get("step") if isinstance(det, dict) else det)
    print("  -- 되돌리기 --")
    g = lambda: c.get(f"{P}/{pid}")
    p = (await g()).json()
    if not over:
        await step(c, "← 명세서 기록 되돌리기", f"{P}/{pid}/undo/statement")
    p = (await g()).json()
    for i in reversed(range(len(p["receipt"]["records"]))):
        await step(c, "← 입고 기록 지우기", f"{P}/{pid}/receipt/{i}/remove", {"expect": {"at": p["receipt"]["records"][i]["at"]}})
    if over:
        await step(c, "← 관세사 송금 확인 취소", f"{P}/{pid}/broker/undo")
        await step(c, "← 통관 서류 제출 완료 취소", f"{P}/{pid}/undo/customs")
        p = (await g()).json()
        for n in reversed(range(len(p["customs"]["etc_files"]))):
            f = p["customs"]["etc_files"][n]
            await step(c, "← 통관 기타 서류 지우기", f"{P}/{pid}/files/customs_etc/{n}/remove", {"expect": {"filename": f["filename"], "uploaded_at": f["uploaded_at"]}})
        await step(c, "← 원산지 증명서 없음 되돌리기", f"{P}/{pid}/customs/no-origin", {"value": False})
    await step(c, "← 배송 시작 취소", f"{P}/{pid}/ship/undo")
    if over:
        await step(c, "← 제조사 송금 확인 취소", f"{P}/{pid}/paid/undo")
    else:
        for i in (1, 0):
            await step(c, f"← {i + 1}회차 송금 확인 취소", f"{P}/{pid}/terms/{i}/unconfirm")
    p = await step(c, "← 발주서 발행 취소", f"{P}/{pid}/po/cancel", {})
    report(bool(p) and not p["po"].get("no") and p["po"]["has_draft"] and not p["po"]["history"], "   발주 전으로 · 초안으로 돌아옴 · 이력 비움(번호 다시 씀)")
    st, js = await call(c, f"{P}/{pid}/unapprove")
    report(st == 200, "← 재가 취소", f"[{st}]", detail(js)[:140] if st != 200 else (js or {}).get("stage"))
    await step(c, "← 견적서 없이 넘어감 되돌리기", f"{P}/{pid}/quote/skip", {"value": False})
    p = (await g()).json()
    report(p["stage"] == first_stage, "처음 단계로 돌아옴", f"처음={first_stage} 지금={p['stage']}")
    # 다시 처음부터 진행 — 견적 넘기기 → 재가 → 발주서 다시 발행(새 번호·다음 판)
    await step(c, "↻ 다시: 견적서 없이 넘어가기", f"{P}/{pid}/quote/skip", {"value": True})
    await step(c, "↻ 다시: 재가 완료", f"{P}/{pid}/approve", {"date": TODAY.isoformat()})
    draft = (await c.get(f"{P}/{pid}/po/draft")).json()
    report(draft.get("source") == "draft" and bool(draft.get("items")), "↻ 발주서 작성 창에 취소한 내용이 채워짐", draft.get("source"))
    p = await step(c, "↻ 다시: 발주서 발행", f"{P}/{pid}/po/issue",
                   {"initials": "ZZ", "expected_date": draft.get("expected_date") or (TODAY + dt.timedelta(days=10)).isoformat(),
                    "header": draft["header"], "items": draft["items"]})
    report(bool(p) and p["po"]["revision"] == 1 and not p["po"]["history"], "↻ 다시 발행 = 첫 이력(v1, 취소한 번호 다시 씀)", (p or {}).get("po", {}).get("no"))
    return pid


async def main() -> None:
    member, admin = await tokens()
    async with client(member) as c, client(admin) as a:
        did = await deal_flow(c, a)
        pids = [await purchase_flow(c, "domestic"), await purchase_flow(c, "overseas")]
        # 정리 — 관리자 삭제
        if did:
            await call(a, f"{D}/delete", {"ids": [did]})
        await call(a, f"{P}/delete", {"ids": [x for x in pids if x]})
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM doc_numbers WHERE no LIKE '%-ZZ%'")) if False else None
    print("\n" + ("모두 통과" if not fails else f"FAIL {len(fails)}건: " + " / ".join(fails)))
    sys.exit(1 if fails else 0)


asyncio.run(main())
