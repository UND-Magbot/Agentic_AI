"""견적서(/v1/product-recommend/quotes) API 검증 — cases/02_견적서.md 의 API 케이스.

    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \
      -v ./backend/app:/app/app -v ./test_cases:/tc backend python /tc/runners/api/quotes.py

AI 답을 바꿔 넣어야 하는 케이스(API(stub))는 SKIP 으로 남긴다. 해외 번역 케이스는 실제 사내 AI 를 부른다.
"""
from __future__ import annotations

import asyncio
import io
import sys

import openpyxl
import pymupdf
from sqlalchemy import text

sys.path.insert(0, "/tc/runners")
import tc  # noqa: E402

from app.database import SessionLocal  # noqa: E402

Q = "/v1/product-recommend/quotes"
YMD = tc.kst_today().strftime("%Y%m%d")
LINES = [{"name": "MG10", "commodity": "Magbot Gripper", "qty": 3, "unit_price": 1250000},
         {"name": "TCV1", "qty": 2, "unit_price": 480000}]
PROFILE_COLS = ("initials", "name_ko", "title", "mobile", "email", "name_en", "name_en_first", "name_en_last", "honorific")


def png_bytes(w: int = 16, h: int = 16) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), False)
    pix.clear_with(180)
    return pix.tobytes("png")


def xlsx_bytes(rows: list[tuple]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["품목", "규격", "수량", "단가", "금액"])
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class Quotes:
    def __init__(self, s: tc.Suite) -> None:
        self.s = s

    async def new(self, user: str = "lucas", customer: str = "가나테크", lines=LINES, **edit) -> dict:
        r = await self.s.call("POST", f"{Q}/manual", user, json={"form": {}})
        assert r.status_code == 201, f"수기 견적 실패 {r.status_code} {r.text[:150]}"
        q = r.json()
        body = {"form": {"customer": f"{tc.MARK}{customer}", "to": "김철수 대표님", **edit.pop("form", {})},
                "lines": lines, "delivery": {"case": 1}, **edit}
        e = await self.edit(q["id"], body, user)
        assert e.status_code == 200, f"견적 채우기 실패 {e.status_code} {e.text[:200]}"
        return e.json()

    async def edit(self, qid: int, body: dict, user: str = "lucas"):
        return await self.s.call("POST", f"{Q}/{qid}/edit", user, json=body)

    async def issue(self, qid: int, user: str = "lucas"):
        return await self.s.call("POST", f"{Q}/{qid}/issue", user, json={})

    async def get(self, qid: int, user: str = "lucas") -> dict:
        return (await self.s.call("GET", f"{Q}/{qid}", user)).json()


def ids(lines: list[dict]) -> list[dict]:
    return [{k: v for k, v in ln.items() if k in ("id", "name", "commodity", "qty", "unit_price", "remark", "unit", "kind", "block")}
            for ln in lines]


async def body(s: tc.Suite) -> None:
    x = Quotes(s)
    async with SessionLocal() as db:                 # 담당자 정보(내 정보) — 끝에 되돌린다
        before = dict((await db.execute(text(f"SELECT {', '.join(PROFILE_COLS)} FROM users WHERE username = 'lucas'"))).mappings().one())
    await s.call("PUT", "/v1/me/profile", "lucas", json={"initials": "LU", "name_ko": before["name_ko"] or "루카스",
                                                         "title": before["title"] or "", "mobile": before["mobile"] or "010-1234-5678",
                                                         "email": before["email"] or "", "name_en_first": "Lucas",
                                                         "name_en_last": "Kim", "honorific": "Mr."})
    try:
        await cases(s, x)
    finally:
        async with SessionLocal() as db:
            await db.execute(text(f"UPDATE users SET {', '.join(f'{c} = :{c}' for c in PROFILE_COLS)} WHERE username = 'lucas'"), before)
            await db.commit()


async def cases(s: tc.Suite, x: Quotes) -> None:
    # ── 금액 계산 ──
    async def qt001():
        q = await x.new()
        return q["subtotal"] == 4710000 and q["problems"] == [] and [l["id"] for l in q["state"]["lines"]] == ["L1", "L2"], \
            f"subtotal={q['subtotal']} problems={q['problems']}"
    await s.case("QT-001", "품목 2줄 합계", qt001)

    async def qt002():
        q = await x.new()
        r = (await x.edit(q["id"], {"nego": {"mode": "percent", "value": 10, "label": ""}})).json()
        return r["subtotal"] == 4239000, f"NEGO 10% → subtotal={r['subtotal']} (기대 4,239,000)"
    await s.case("QT-002", "NEGO % 할인", qt002)

    async def qt003():
        q = await x.new()
        r = (await x.edit(q["id"], {"nego": {"mode": "amount", "value": 710000, "label": "특별 할인"}})).json()
        return r["subtotal"] == 4000000, f"NEGO 710,000 → subtotal={r['subtotal']}"
    await s.case("QT-003", "NEGO 금액 할인", qt003)

    async def qt004():
        q = await x.new()
        r = (await x.edit(q["id"], {"nego": {"mode": "amount", "value": 4710000}})).json()
        i = await x.issue(q["id"])
        return any("NEGO" in p for p in r["problems"]) and i.status_code == 400, f"problems={r['problems']} 발행 {i.status_code}"
    await s.case("QT-004", "NEGO 가 합계 이상이면 발행 막힘", qt004)

    async def qt005():
        q = await x.new()
        r = (await x.edit(q["id"], {"nego": {"mode": "percent", "value": 100}})).json()
        i = await x.issue(q["id"])
        return i.status_code == 400, f"NEGO 100% → subtotal={r['subtotal']}, 발행 {i.status_code} (200 이면 0원 견적 발행)"
    await s.case("QT-005", "NEGO 100%(0원) 발행 막힘", qt005)

    async def qt008():
        q = await x.new()
        lines = ids(q["state"]["lines"]) + [{"name": "IB 브라켓", "qty": 1, "unit_price": 300000, "block": "tmp1"}]
        r = await x.edit(q["id"], {"blocks": [{"id": "B0", "type": "items"}, {"id": "tmp1", "type": "option", "title": ""}], "lines": lines})
        j = r.json()
        opt = [b for b in j["state"]["blocks"] if b["type"] == "option"]
        return r.status_code == 200 and j["subtotal"] == 4710000 and opt and opt[0]["id"] != "tmp1", \
            f"{r.status_code} subtotal={j.get('subtotal')} 옵션 묶음={opt}"
    await s.case("QT-008", "옵션 묶음은 합계 제외", qt008)

    async def qt009():
        q = await x.new()
        lines = ids(q["state"]["lines"]) + [{"name": "IB 브라켓", "qty": 1, "unit_price": 300000, "block": "tmp1"}]
        await x.edit(q["id"], {"blocks": [{"id": "B0", "type": "items"}, {"id": "tmp1", "type": "option"}], "lines": lines})
        j = (await x.edit(q["id"], {"nego": {"mode": "percent", "value": 10}})).json()
        return j["subtotal"] == 4239000, f"옵션 있는 견적 NEGO 10% → {j['subtotal']} (옵션이 % 기준에 들어가면 4,209,000)"
    await s.case("QT-009", "NEGO % 기준에 옵션 제외", qt009)

    async def qt011():
        q = await x.new()
        lines = ids(q["state"]["lines"]) + [{"kind": "note", "commodity": "설치 조건", "name": "전원 220V 고객 준비"}]
        j = (await x.edit(q["id"], {"lines": lines})).json()
        return j["subtotal"] == 4710000 and j["problems"] == [], f"subtotal={j['subtotal']} problems={j['problems']}"
    await s.case("QT-011", "서술형 줄은 합계·발행에 영향 없음", qt011)

    async def qt012():
        q = await x.new()
        j = (await x.edit(q["id"], {"delivery": {"case": 4, "freight": 150000}, "work": {"people": 2, "days": 3}})).json()
        f = [l for l in j["state"]["lines"] if l["id"].startswith("F")]
        w = [l for l in j["state"]["lines"] if l["id"].startswith("W")]
        rate = w[0]["unit_price"] if w else None
        want = 4710000 + 150000 + 6 * (rate or 0)
        return bool(f) and bool(w) and w[0]["qty"] == 6 and j["subtotal"] == want, \
            f"물류비 {[(l['id'], l['unit_price']) for l in f]} 인건비 {[(l['id'], l['qty'], l['unit_price']) for l in w]} subtotal={j['subtotal']}"
    await s.case("QT-012", "물류비·설치 인건비 줄", qt012)

    async def qt013():
        q = await x.new()
        await x.edit(q["id"], {"delivery": {"case": 4, "freight": 150000}, "work": {"people": 2, "days": 3}})
        j1 = (await x.edit(q["id"], {"delivery": {"case": 1}})).json()
        j3 = (await x.edit(q["id"], {"delivery": {"case": 3}})).json()
        f1 = [l["id"] for l in j1["state"]["lines"] if l["id"][0] in "FW"]
        w3 = [(l["qty"]) for l in j3["state"]["lines"] if l["id"].startswith("W")]
        f3 = [l["id"] for l in j3["state"]["lines"] if l["id"].startswith("F")]
        return not f1 and w3 == [6] and not f3, f"택배로 바꾼 뒤 {f1}, 설치 납품으로 되돌린 뒤 인건비 수량 {w3} 물류비 {f3}"
    await s.case("QT-013", "납품 방식 바꾸면 줄이 따라 바뀜", qt013)

    async def qt015():
        q = await x.new(lines=[{"name": "A", "qty": 3, "unit_price": 1000.5}])
        return ("CHECK", f"단가 1,000.5 × 3 → 화면 subtotal={q['subtotal']} (DB·엑셀은 3,001.5/3,002 가능) — 원 단위 소수 단가 허용 여부 확인")
    await s.case("QT-015", "소수 단가 반올림 일관성", qt015)

    async def qt016():
        q = await x.new(lines=[{"name": "A", "qty": 1, "unit_price": 1000005}])
        j = (await x.edit(q["id"], {"nego": {"mode": "percent", "value": 10}})).json()
        js_round = 1000005 - int(1000005 * 0.1 + 0.5)          # 화면(JS Math.round) 계산
        return j["subtotal"] == js_round, f"서버 {j['subtotal']} / 화면 계산 {js_round}"
    await s.case("QT-016", "NEGO % 반올림 화면=서버", qt016)

    async def qt017():
        q = await x.new(lines=[{"name": "A", "qty": 100000, "unit_price": 9999999999}])
        r = await x.edit(q["id"], {"lines": [{"name": f"A{i}", "qty": 1, "unit_price": 1} for i in range(61)]})
        return q["subtotal"] == 999999999900000 and r.status_code == 422, f"큰 금액 subtotal={q['subtotal']}, 61줄 {r.status_code}"
    await s.case("QT-017", "큰 금액·줄 수 상한", qt017)

    async def qt018():
        q = await x.new()
        rs = [await x.edit(q["id"], {"lines": [{"name": "A", "qty": -1, "unit_price": 1}]}),
              await x.edit(q["id"], {"lines": [{"name": "A", "qty": 1, "unit_price": -100}]}),
              await x.edit(q["id"], {"lines": [{"name": "A", "qty": 1, "unit_price": 1e10}]})]
        after = (await x.get(q["id"]))["subtotal"]
        return [r.status_code for r in rs] == [422] * 3 and after == 4710000, f"{[r.status_code for r in rs]} 합계 {after}"
    await s.case("QT-018", "음수·상한 초과 422", qt018)

    async def qt020():
        q = await x.new()
        j = (await x.edit(q["id"], {"lines": ids(q["state"]["lines"]) + [{"name": "X", "qty": 0, "unit_price": 10000}]})).json()
        newq = [l["qty"] for l in j["state"]["lines"] if l["name"] == "X"]
        lines = ids(j["state"]["lines"])
        lines[0]["qty"] = 0
        j2 = (await x.edit(q["id"], {"lines": lines})).json()
        return ("CHECK", f"새 줄 수량 0 → {newq}, 기존 L1 수량 0 → {j2['state']['lines'][0]['qty']} (안내 없이 바뀌거나 무시됨 — 거절·안내 필요 여부 확인)")
    await s.case("QT-020", "수량 0 입력 처리", qt020)

    # ── 권한 ──
    async def qt034():
        q = await x.new()
        qid = q["id"]
        rs = [await s.call("GET", f"{Q}/{qid}", "mason"), await x.edit(qid, {"form": {"note": "남의 견적"}}, "mason"),
              await x.issue(qid, "mason"), await s.call("GET", f"{Q}/{qid}/file", "mason"),
              await s.call("GET", f"{Q}/{qid}/preview", "mason"),
              await s.call("POST", f"{Q}/{qid}/lines/L1/photo", "mason", files={"file": ("a.png", png_bytes(), "image/png")})]
        note = (await x.get(qid))["state"]["form"]["note"]
        return all(r.status_code in (400, 404) for r in rs) and note == "", f"{[r.status_code for r in rs]} 비고={note!r}"
    await s.case("QT-034", "남의 견적 보기·고치기·발행 막힘", qt034)

    async def qt035():
        q = await x.new()
        r = await s.call("GET", f"{Q}/{q['id']}", "alex")
        return ("CHECK", f"영업 관리자 alex 가 lucas 견적 열기 → {r.status_code} (관리자 열람 필요 여부 확인)")
    await s.case("QT-035", "관리자의 멤버 견적 열람", qt035)

    async def qt036():
        cols = ("initials", "name_ko", "mobile", "email")
        async with SessionLocal() as db:            # 담당자 정보가 비어 막히는 것과 구분하려고 잠시 채운다
            prev = dict((await db.execute(text(f"SELECT {', '.join(cols)} FROM users WHERE username = 'finance_admin'"))).mappings().one())
            await db.execute(text("UPDATE users SET initials = 'FZ', name_ko = '재무시험', mobile = '010-0000-0000' "
                                  "WHERE username = 'finance_admin'"))
            await db.commit()
        try:
            q = await x.new(user="finance_admin")
            i = await x.issue(q["id"], "finance_admin")
            return i.status_code == 403, \
                f"재무 관리자: 견적 작성 OK, 발행 {i.status_code} {i.text[:100]} (200 이면 영업부 아닌 사람이 견적 발행·영업 건 생성)"
        finally:
            async with SessionLocal() as db:
                await db.execute(text(f"UPDATE users SET {', '.join(f'{c} = :{c}' for c in cols)} WHERE username = 'finance_admin'"), prev)
                await db.execute(text("DELETE FROM doc_numbers WHERE issuer = 'FZ'"))
                await db.commit()
    await s.case("QT-036", "영업부 아닌 사용자 견적 발행", qt036)

    # ── 발행 ──
    async def qt038():
        q = await x.new()
        r = await x.issue(q["id"])
        j = r.json()
        qt = j.get("quote", j)
        no = qt.get("quote_no") or ""
        return r.status_code == 200 and no.startswith(f"QT-LU{YMD}-") and qt.get("status") == "issued" and qt.get("deal_id"), \
            f"{r.status_code} 번호={no} 상태={qt.get('status')} 영업 건={qt.get('deal_id')} 파일={qt.get('file_name')}"
    await s.case("QT-038", "발행: 번호·상태·영업 건 생성", qt038)

    async def qt039():
        q = await x.new()
        a = (await x.issue(q["id"])).json()
        a = a.get("quote", a)
        lines = ids(a["state"]["lines"])
        lines[0]["qty"] = 4
        await x.edit(q["id"], {"lines": lines})
        b = (await x.issue(q["id"])).json()
        b = b.get("quote", b)
        old = await s.call("GET", f"{Q}/{q['id']}/file?revision=0", "lucas")
        sa, sb = int(a["quote_no"].rsplit("-", 1)[1]), int(b["quote_no"].rsplit("-", 1)[1])
        return sb == sa + 1 and b["revision"] == a["revision"] + 1 and len(b["history"]) == 2 and old.status_code == 200, \
            f"{a['quote_no']} r{a['revision']} → {b['quote_no']} r{b['revision']}, 이력 {len(b['history'])}, 이전 판 받기 {old.status_code}"
    await s.case("QT-039", "다시 발행: 새 번호·판+1·이전 판 보관", qt039)

    async def qt040():
        q = await x.new()
        rs = await asyncio.gather(x.issue(q["id"]), x.issue(q["id"]))
        got = await x.get(q["id"])
        revs = [h.get("revision") for h in got["history"]]
        nos = [h.get("quote_no") for h in got["history"]]
        return len(set(revs)) == len(revs) and len(set(nos)) == len(nos), \
            f"동시 발행 2번 → {[r.status_code for r in rs]}, 이력 판 {revs} 번호 {nos} (같은 판 두 개면 결함)"
    await s.case("QT-040", "동시 발행 판 번호 중복 없음", qt040)

    async def qt042():
        # 이니셜은 영문 2자만(사용자 결정 2026-10-16) — 내 정보도 3자를 받지 않아야 하고, 2자로는 발행된다
        prof = {"name_ko": "루카스", "mobile": "010-1234-5678", "email": "lucas@example.com",
                "name_en_first": "Lucas", "name_en_last": "Kim", "honorific": "Mr."}
        bad = await s.call("PUT", "/v1/me/profile", "lucas", json={"initials": "LUC", **prof})
        await s.call("PUT", "/v1/me/profile", "lucas", json={"initials": "LU", **prof})
        q = await x.new()
        i = await x.issue(q["id"])
        no = (i.json().get("quote") or i.json()).get("quote_no") if i.status_code == 200 else None
        return bad.status_code in (400, 422) and i.status_code == 200 and (no or "").startswith("QT-LU"),             f"내 정보 이니셜 LUC → {bad.status_code}, LU 로 발행 {i.status_code} {no or i.text[:120]}"
    await s.case("QT-042", "이니셜은 영문 2자만", qt042)

    async def qt043():
        async with SessionLocal() as db:
            prof = dict((await db.execute(text("SELECT initials, name_ko, mobile, email FROM users WHERE username='charles'"))).mappings().one())
        q = await x.new(user="charles")
        i = await x.issue(q["id"], "charles")
        empty = [k for k, v in prof.items() if not v]
        if not empty:
            return ("SKIP", "charles 내 정보가 채워져 있어 미입력 상황이 아님")
        return i.status_code == 400, f"내 정보 빈 칸 {empty} → 발행 {i.status_code} {i.text[:120]}"
    await s.case("QT-043", "담당자 정보 없으면 발행 막힘", qt043)

    async def qt044():
        r = await s.call("POST", f"{Q}/manual", "lucas", json={"form": {"contact_name": "홍길동"}})
        name = r.json()["state"]["form"]["contact_name"]
        return ("CHECK", f"브라우저 기억 값 '홍길동' vs 내 정보 → 담당자 칸 {name!r} (어느 쪽이 우선이어야 하는지 확인)")
    await s.case("QT-044", "담당자 칸 우선순위", qt044)

    # ── 해외(영문) ──
    async def qt045():
        q = await x.new()
        j = (await x.edit(q["id"], {"form": {"lang": "en", "fx_rate": 1380}})).json()
        f = j["state"]["form"]
        return f.get("contact_name_en") == "Lucas Kim" and f.get("contact_honorific") == "Mr." and "Ex-work" in (f.get("note") or "") \
            and abs((j.get("subtotal_usd") or 0) - 3413.06) < 0.05, \
            f"영문 이름 {f.get('contact_name_en')!r} {f.get('contact_honorific')!r}, 비고 {f.get('note')!r}, USD 합계 {j.get('subtotal_usd')}"
    await s.case("QT-045", "해외 전환: 영문 기본값·달러 환산", qt045)

    async def qt046():
        q = await x.new()
        await x.edit(q["id"], {"form": {"payment": "선금 30%, 잔금 70%"}, "nego": {"mode": "amount", "value": 800000}})
        j = (await x.edit(q["id"], {"form": {"lang": "en", "fx_rate": 1380}})).json()
        return ("CHECK", f"국내에서 고친 결제조건 → 해외 전환 후 {j['state']['form']['payment']!r}, NEGO 800,000 → problems {j['problems']} "
                         "(고친 값 덮어씀·NEGO 통화 처리 안내 필요 여부 확인)")
    await s.case("QT-046", "국내→해외 전환 시 고친 값·NEGO", qt046)

    async def qt048():
        q = await x.new(form={"lang": "en", "fx_rate": 1380, "note": "설치는 별도 협의"},
                        lines=[{"name": "MG10 그리퍼 2EA", "commodity": "Magbot Gripper", "qty": 2, "unit_price": 1250000}])
        p = await s.call("GET", f"{Q}/{q['id']}/preview", "lucas")
        g = await x.get(q["id"])
        cells = g["hangul"]
        spec = next((c for c in cells if "MG10" in c["text"]), {})
        ok = p.status_code == 200 and all(c.get("en") for c in cells if not c.get("keep")) and "MG10" in (spec.get("en") or "") \
            and g["state"]["form"]["note"] == "설치는 별도 협의"
        why = next((m["text"] for m in reversed(g["chat"]) if "영어로" in m["text"]), "")
        return ok, (f"미리보기 {p.status_code}, 번역 {[(c['label'], c.get('en')) for c in cells][:6]}, "
                    f"원문 유지={g['state']['form']['note']!r}, 안내={why[:160]!r}")
    await s.case("QT-048", "해외 미리보기: 한글 칸 자동 번역·원문 유지", qt048)

    async def qt051():
        q = await x.new(form={"lang": "en", "fx_rate": 1380, "note": "설치는 별도 협의"})
        await s.call("GET", f"{Q}/{q['id']}/preview", "lucas")
        p = f"{Q}/{q['id']}/en-cell"
        rs = [await s.call("POST", p, "lucas", json={"path": "form.note", "en": "설치 별도"}),
              await s.call("POST", p, "lucas", json={"path": "form.note", "en": "x" * 401}),
              await s.call("POST", p, "lucas", json={"path": "form.note", "en": "Installation to be discussed"})]
        cell = next((c for c in rs[2].json().get("hangul", []) if c["path"] == "form.note"), {})
        return [r.status_code for r in rs][:2] in ([400, 400], [400, 422]) and rs[2].status_code == 200 and cell.get("by") == "user", \
            f"{[r.status_code for r in rs]} 저장된 칸 {cell}"
    await s.case("QT-051", "영어 직접 고치기 검사", qt051)

    async def qt052():
        q = await x.new(form={"lang": "en", "fx_rate": 1380, "note": "설치는 별도 협의"})
        await s.call("GET", f"{Q}/{q['id']}/preview", "lucas")
        j = (await x.edit(q["id"], {"form": {"note": "설치 포함"}})).json()
        cell = next((c for c in j["hangul"] if c["path"] == "form.note"), {})
        return cell.get("en") is None, f"원문을 고친 뒤 영어 = {cell.get('en')!r} (None 이어야 다시 번역)"
    await s.case("QT-052", "원문 고치면 다시 번역 대상", qt052)

    async def qt053():
        q = await x.new(form={"lang": "en", "fx_rate": 1380, "foot_notes": "주석A 한글 설명\n주석B 한글 설명"})
        cells = q["hangul"]
        fn = [c for c in cells if c["path"].startswith("form.foot_notes")]
        if len(fn) < 2:
            return ("SKIP", f"표 아래 주석이 칸 2개로 나뉘지 않음: {[c['path'] for c in cells]}")
        await s.call("POST", f"{Q}/{q['id']}/en-cell", "lucas", json={"path": fn[0]["path"], "keep": True})
        j = (await x.edit(q["id"], {"form": {"foot_notes": "주석B 한글 설명"}})).json()
        left = [c for c in j["hangul"] if c["path"].startswith("form.foot_notes")]
        return not any(c.get("keep") for c in left), f"주석A 를 [한글 그대로] 후 지움 → 남은 주석B keep={[c.get('keep') for c in left]}"
    await s.case("QT-053", "[한글 그대로]가 지운 줄 다음 줄로 옮겨가지 않음", qt053)

    async def qt055():
        q = await x.new()
        return q["hangul"] == [], f"국내 견적 hangul={q['hangul']}"
    await s.case("QT-055", "국내 견적엔 번역 목록 없음", qt055)

    async def qt063():
        q = await x.new(lines=[{"name": "Gripper S20", "commodity": "Type T10", "qty": 1, "unit_price": 1000, "remark": "A01 볼트"}],
                        customer="A01 테크")
        ln = q["state"]["lines"][0]
        cust = q["state"]["form"]["customer"]
        lost = [v for v, got in (("Type T10", ln["commodity"]), ("A01 볼트", ln["remark"]), ("A01 테크", cust)) if v not in got]
        return not lost, f"응답에서 잘린 값: {lost} (품목={ln['commodity']!r} 비고={ln['remark']!r} 고객사={cust!r})"
    await s.case("QT-063", "모델명(T10·S20·A01)이 응답에서 지워지지 않음", qt063)

    # ── 업로드 ──
    async def upload(qid: int, name: str, data: bytes):
        return await s.call("POST", f"{Q}/{qid}/upload", "lucas", files={"file": (name, data, "application/octet-stream")})

    async def qt064():
        q = await x.new()
        r = await upload(q["id"], "quote.xlsx", xlsx_bytes([("Magbot", "MG10", 2, 1250000, 2500000), ("Magbot", "TCV1", 1, 480000, 480000)]))
        if r.status_code != 200:
            return False, f"업로드 {r.status_code} {r.text[:150]}"
        up = r.json()
        items = [{"name": it.get("name") or "A", "qty": it.get("qty") or 1, "unit_price": it.get("unit_price") or 1000}
                 for it in (up.get("read", {}).get("items") or up.get("items") or [])] or [{"name": "MG10", "qty": 2, "unit_price": 1250000}]
        c = await s.call("POST", f"{Q}/{q['id']}/upload/confirm", "lucas", json={"upload": up.get("upload") or up, "items": items, "initials": "LU"})
        no = c.json().get("quote_no") if c.status_code == 200 else c.text[:150]
        return c.status_code == 200 and str(no).startswith(f"QT-LU{YMD}-"), f"읽은 품목 {len(items)}줄, 확인 발행 {c.status_code} {no}"
    await s.case("QT-064", "밖에서 고친 견적서 올려 발행", qt064)

    async def qt065():
        q = await x.new()
        rs = [await upload(q["id"], "a.xls", b"abc"), await upload(q["id"], "a.docx", b"abc"), await upload(q["id"], "noext", b"abc"),
              await upload(q["id"], "empty.xlsx", b""), await upload(q["id"], "big.pdf", b"%PDF" + b"0" * (20 * 1024 * 1024 + 1)),
              await upload(q["id"], "broken.pdf", b"%PDF-1.4 garbage")]
        got = [r.status_code for r in rs]
        st = rs[5].json().get("read", {}).get("status") if rs[5].status_code == 200 else None
        return got[:3] == [415] * 3 and got[3] == 400 and got[4] == 413 and got[5] in (200, 400), f"{got} 깨진 pdf 판독={st}"
    await s.case("QT-065", "업로드 파일 형식·크기 검사", qt065)

    async def qt066():
        q = await x.new()
        r = await upload(q["id"], "quote.xlsx", xlsx_bytes([("Magbot", "MG10", 1, 1000, 1000)]))
        up = r.json()
        c = await s.call("POST", f"{Q}/{q['id']}/upload/confirm", "lucas",
                         json={"upload": up.get("upload") or up, "items": [{"name": "A", "qty": 1, "unit_price": 0}], "initials": "LU"})
        return c.status_code in (400, 422), f"단가 0원 품목으로 업로드 발행 → {c.status_code} (200 이면 0원 발행)"
    await s.case("QT-066", "업로드 발행 0원 품목 거절", qt066)

    async def qt069():
        q = await x.new()
        r = await upload(q["id"], "quote.xlsx", xlsx_bytes([("Magbot", "MG10", 1, 1000, 1000)]))
        up = r.json()
        low = f"qt-lu{YMD}-77"
        c = await s.call("POST", f"{Q}/{q['id']}/upload/confirm", "lucas",
                         json={"upload": up.get("upload") or up, "items": [{"name": "A", "qty": 1, "unit_price": 1000}],
                               "initials": "LU", "doc_no": low})
        async with SessionLocal() as db:
            reg = (await db.execute(text("SELECT count(*) FROM doc_numbers WHERE upper(doc_no) = upper(:n)"), {"n": low})).scalar()
        return c.status_code != 200 or reg == 1, f"소문자 번호 {low} 발행 {c.status_code}, 번호 기록 {reg}건 (0이면 같은 번호가 다시 나갈 수 있음)"
    await s.case("QT-069", "소문자 발행번호도 기록", qt069)

    # ── 사진 ──
    async def qt071():
        q = await x.new()
        p = f"{Q}/{q['id']}/lines/L1/photo"
        rs = [await s.call("POST", p, "lucas", files={"file": ("a.png", png_bytes(), "image/png")}),
              await s.call("POST", p, "lucas", files={"file": ("a.gif", b"GIF89a", "image/gif")}),
              await s.call("POST", p, "lucas", files={"file": ("a.png", b"not image", "image/png")}),
              await s.call("POST", p, "lucas", files={"file": ("a.png", b"\x89PNG" + b"0" * (10 * 1024 * 1024 + 1), "image/png")}),
              await s.call("POST", p, "lucas", files={"file": ("bomb.png", png_bytes(12000, 12000), "image/png")})]
        got = [r.status_code for r in rs]
        return got[0] == 200 and got[1] == 415 and got[2] == 400 and got[3] == 413 and got[4] != 500, \
            f"정상/GIF/가짜 PNG/10MB 초과/12000×12000 = {got}"
    await s.case("QT-071", "품목 사진 형식·크기·거대 이미지", qt071)

    async def qt072():
        q = await x.new()
        await x.edit(q["id"], {"delivery": {"case": 2, "freight": 100000}})
        rs = [await s.call("POST", f"{Q}/{q['id']}/lines/F1/photo", "lucas", files={"file": ("a.png", png_bytes(), "image/png")}),
              await s.call("POST", f"{Q}/{q['id']}/lines/L99/photo", "lucas", files={"file": ("a.png", png_bytes(), "image/png")})]
        return [r.status_code for r in rs] == [400, 400], f"{[r.status_code for r in rs]}"
    await s.case("QT-072", "물류비 줄·없는 줄 사진 막힘", qt072)

    async def qt073():
        q = await x.new()
        rs = [await s.call("GET", f"{Q}/{q['id']}/photo/{'a' * 32}.jpg", "mason"),
              await s.call("GET", f"{Q}/{q['id']}/photo/..%2F..%2Fsecret.jpg", "lucas")]
        return all(r.status_code == 404 for r in rs), f"{[r.status_code for r in rs]}"
    await s.case("QT-073", "사진 경로 조작·남의 사진 404", qt073)

    async def qt075():
        q = await x.new()
        rs = [await s.call("GET", f"{Q}/{q['id']}/file", "lucas"), await s.call("GET", f"{Q}/{q['id']}/file?revision=9", "lucas")]
        return all(r.status_code == 404 for r in rs), f"{[r.status_code for r in rs]}"
    await s.case("QT-075", "발행 전 파일 받기 404", qt075)

    async def qt076():
        a = (await s.call("POST", f"{Q}/manual", "mason", json={"form": {}})).json()["id"]
        b = (await s.call("POST", f"{Q}/manual", "mason", json={"form": {}})).json()["id"]
        try:
            return a == b, f"빈 초안 두 번 → id {a}, {b}"
        finally:
            async with SessionLocal() as db:
                await db.execute(text("DELETE FROM product_quotes WHERE id = ANY(:i) AND status = 'draft' "
                                      "AND jsonb_array_length(state->'lines') = 0"), {"i": [a, b]})
                await db.commit()
    await s.case("QT-076", "빈 수기 초안 재사용", qt076)

    async def qt078():
        q = await x.new()
        j = (await x.edit(q["id"], {"form": {"lang": "en", "fx_rate": None}, "delivery": {"case": 2, "freight": 100000}})).json()
        return any("환율" in p for p in j["problems"]), f"problems={j['problems']}"
    await s.case("QT-078", "해외 견적 환율 없으면 발행 막힘", qt078)

    for cid in ("QT-027", "QT-028", "QT-029", "QT-033", "QT-050"):
        s.skip(cid, "AI 답 고정 필요", "AI 응답을 바꿔 넣는 장치(stub)가 아직 없음 — 다음 단계")


if __name__ == "__main__":
    tc.main("quotes", body)
