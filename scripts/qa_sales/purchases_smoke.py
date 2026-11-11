"""제품 구매 관리 끝-끝 점검(로그인 상태, 프론트 프록시 경유) — 실제 파일 첨부·AI 견적서 판독·발주서 PDF·지출내역 대조.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts \\
        -v "./docs/examples/quotes:/samples:ro" backend python /scripts/qa_sales/purchases_smoke.py

테스트 건은 끝에 지운다(발주 번호는 doc_numbers 에서도 지운다 — 테스트 이니셜 ZZ).
"""
import asyncio
import io
import sys
import time
import urllib.parse

import httpx
from pathlib import Path
import openpyxl
from sqlalchemy import text

from app.database import SessionLocal
from app.security import create_access_token

F = "http://frontend:3000/api/purchases"
SAMPLE = "/samples/(쥬)유엔디로보틱스_발주서_(주)나우티앤에스_Lu261013.pdf"
SUP = "__QA_구매__나우티앤에스"
SUP_EN = "__QA_구매__Autoxing"
# 1×1 PNG(입고 사진 점검용)
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6300010000050001"
                    "0d0a2db40000000049454e44ae426082")
fails: list[str] = []


def report(ok: bool, *msg) -> None:
    print("OK  " if ok else "FAIL", *msg)
    if not ok:
        fails.append(str(msg[0]))


def expense_xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    for r in [["일자", "거래처", "적요", "출금액"], *rows]:
        wb.active.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


async def main() -> None:
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username='alex'"))).scalar()
        await db.execute(text("DELETE FROM purchases WHERE supplier = ANY(:s)"), {"s": [SUP, SUP_EN]})
        await db.commit()
    tok, _ = create_access_token(str(uid))
    ck = {"und_cortex_token": tok}
    pdf = open(SAMPLE, "rb").read()
    async with httpx.AsyncClient(timeout=300, cookies=ck) as c:
        async def post(path, js=None, files=None, data=None):
            r = await c.post(F + path, json=js if files is None else None, files=files, data=data)
            return r, (r.json() if "json" in r.headers.get("content-type", "") else None)

        r, p = await post("", {"supplier": SUP, "title": "TC700 LTE Router 8대"})
        report(r.status_code == 201 and p["stage"] == "견적 대기", "새 건", r.status_code)
        pid = p["id"]
        r, p = await post(f"/{pid}/files/quote", files={"file": ("견적서_나우티앤에스.pdf", pdf, "application/pdf")})
        report(r.status_code == 200 and p["stage"] == "재가 대기", "견적서 첨부(PDF)", r.status_code, p and p.get("stage"))
        r, p = await post(f"/{pid}/files/quote", files={"file": ("가짜.pdf", b"MZ not a pdf", "application/pdf")})
        report(r.status_code == 415, "내용이 PDF 가 아닌 파일 거절", r.status_code)
        g = await c.get(F + f"/{pid}/files/quote/0")
        report(g.status_code == 200 and g.content[:4] == b"%PDF", "첨부 다시 보기", g.status_code)
        await post(f"/{pid}/files/approval", files={"file": ("재가.pdf", pdf, "application/pdf")})
        r, p = await post(f"/{pid}/approve", {"date": "2026-11-02"})
        report(r.status_code == 200 and p["stage"] == "발주 대기", "재가 완료", r.status_code)

        d = (await c.get(F + f"/{pid}/po/draft")).json()
        report(d["source"] == "new" and d["header"]["supplier"]["name"] == SUP, "발주서 기본값", d["header"]["issuer"])
        t = time.time()
        r, rq = await post(f"/{pid}/po/read-quote", {})
        items = (rq or {}).get("items") or []
        report(r.status_code == 200 and rq["status"] == "ok" and any(it["qty"] == 8 and it["price"] == 95000 and it.get("commodity") == "TC700" and "text" not in rq and it["moq"] is None for it in items),
               "AI 견적서 판독(TC700 8 × 95,000)", f"{round(time.time() - t, 1)}s", rq and rq.get("summary"), items)
        # 테스트 계정(alex)은 내 정보에 한글 이름이 없다 — 발주 담당자를 채운다(비우면 발행 거절이 맞는 동작)
        r = await c.post(F + f"/{pid}/po/preview", json={"initials": "ZZ", "header": d["header"], "items": items})
        report(r.status_code == 400 and "발주 담당자 이름" in r.json()["detail"], "담당자 이름 없으면 미리보기 거절", r.status_code)
        issuer = {**d["header"]["issuer"], "name": "이승학", "title": "주임", "tel": "82-10-5896-0996", "attn": "Lucas (lucas@unde.co.kr)"}
        body = {"initials": "ZZ", "expected_date": "2026-11-20", "header": {**d["header"], "issuer": issuer, "supplier": {"name": SUP, "contact": "권창수 부장님", "email": "row@nwtns.com"}},
                "items": items or [{"description": "TC700\n- LTE Router", "qty": 8, "price": 95000}]}
        r = await c.post(F + f"/{pid}/po/preview", json=body)
        report(r.status_code == 200 and r.content[:4] == b"%PDF", "미리보기 PDF", r.status_code, len(r.content))
        r, p0 = await post(f"/{pid}/po/issue", {**body, "expected_date": ""})
        report(r.status_code == 400 and "입고 예정일" in p0["detail"], "입고 예정일 없이 발행 거절", p0)
        r, p = await post(f"/{pid}/po/issue", body)
        report(r.status_code == 200 and p["po"]["no"].startswith("UND-ZZ") and p["stage"] == "송금 대기", "발주서 발행", p and p["po"])
        total = p["po"]["total"]
        for fmt in ("pdf", "xlsx"):
            g = await c.get(F + f"/{pid}/po/file?fmt={fmt}")
            report(g.status_code == 200 and g.content[:2] in (b"%P", b"PK") and "attachment" in g.headers.get("content-disposition", ""),
                   f"발행본 {fmt}", g.status_code)
            if fmt == "xlsx":
                issued_xlsx = g.content

        # 발주서 AI 대화(사용자 2026-11-11, 실제 사내 AI) — 말한 숫자만, 머리 칸·날짜, 대화 기록 저장
        g = await c.get(F + f"/{pid}/po/draft")
        dr = g.json()
        t = time.time()
        r, ch = await post(f"/{pid}/po/chat", {"text": "1번 품목 수량 10개로 바꾸고, 공급사 Tel 010-1234-5678 넣어 줘. 입고 예정일은 2026-11-27",
                                               "draft": {k: dr[k] for k in ("initials", "expected_date", "header", "items")}})
        report(r.status_code == 200 and (ch or {}).get("items") and ch["items"][0]["qty"] == 10 and ch["items"][0]["price"] == dr["items"][0]["price"]
               and ch["header"]["supplier"].get("tel") == "010-1234-5678" and ch["expected_date"] == "2026-11-27"
               and len(ch.get("chat") or []) >= 2 and ch.get("reply"),
               "AI 대화 — 수량·Tel·입고 예정일 반영(단가 그대로)", r.status_code, f"{round(time.time() - t, 1)}s", ch,
               ch and ch.get("items"), ch and ch["header"]["supplier"])
        r, ch2 = await post(f"/{pid}/po/chat", {"text": "2번째 품목으로 AFT300 케이블 추가해 줘",
                                                "draft": {k: ch[k] for k in ("initials", "expected_date", "header", "items")}})
        added = (ch2 or {}).get("items") or []
        report(r.status_code == 200 and len(added) == 2 and added[1]["price"] is None and len(ch2["chat"]) >= 4,
               "AI 대화 — 말 안 한 단가는 비움, 대화 이어짐", ch2 and ch2.get("reply"), added)

        # 고친 발주서 파일로 발행(사용자 2026-11-11) — 받은 엑셀을 그대로 올리면 Order No.·품목을 읽고, 같은 번호면 지금 판을 바꾼다
        u = await c.post(F + f"/{pid}/po/upload", files={"file": ("발주서_고침.xlsx", issued_xlsx,
                                                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        ur = u.json()
        report(u.status_code == 200 and ur.get("doc_no") == p["po"]["no"] and ur.get("source") == "template" and len(ur.get("items") or []) == len(p["po"]["items"]),
               "고친 발주서 올리기 — 번호·품목 읽기", u.status_code, ur.get("doc_no"), ur.get("summary"))
        r, bad = await post(f"/{pid}/po/upload/confirm", {**{k: ur.get(k) for k in ("doc_no", "items")}, "upload": {**ur["upload"], "key": "purchases/999999/po/x.xlsx"},
                                                          "expected_date": "2026-11-20"})
        report(r.status_code == 400, "다른 건 저장 위치 파일은 거절", r.status_code)
        r, p = await post(f"/{pid}/po/upload/confirm", {"upload": ur["upload"], "doc_no": ur["doc_no"], "items": ur["items"],
                                                        "date": ur.get("date"), "expected_date": "2026-11-20"})
        report(r.status_code == 200 and p["po"]["no"] == ur["doc_no"] and p["po"]["upload"]["filename"] == "발주서_고침.xlsx"
               and p["po"]["total"] == total, "같은 번호 — 지금 판을 고친 파일로", r.status_code, p and p.get("po", {}).get("upload"))
        g = await c.get(F + f"/{pid}/po/file?fmt=xlsx")
        report(g.status_code == 200 and g.content == issued_xlsx, "내려받기는 올린 파일 그대로", g.status_code)
        g = await c.get(F + f"/{pid}/po/file?fmt=pdf")
        report(g.status_code == 200 and g.content[:4] == b"%PDF", "올린 엑셀도 PDF 로", g.status_code)

        r, p = await post(f"/{pid}/terms", {"terms": [{"label": "선금", "pct": 50, "when": "발주 시"}, {"label": "잔금", "pct": 50}]})
        half = total / 2
        report(r.status_code == 200 and p["terms"][0]["amount"] == half, "결제 조건", p and [t["amount"] for t in p["terms"]])
        good = expense_xlsx([["2026-11-02", SUP, "TC700 선금", round(half * 1.1)]])
        r, p = await post(f"/{pid}/terms/0/check", files={"file": ("지출내역.xlsx", good, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        report(r.status_code == 200 and p["terms"][0]["check"]["status"] == "ok", "지출내역 일치", p and p["terms"][0]["check"]["summary"])
        r, p = await post(f"/{pid}/terms/0/confirm", {"date": "2026-11-02"})
        report(r.status_code == 200 and p["terms"][0]["confirmed"], "송금 확인(일치)")
        bad = expense_xlsx([["2026-11-03", SUP, "TC700 잔금", 123_000]])
        r, p = await post(f"/{pid}/terms/1/check", files={"file": ("지출내역2.xlsx", bad, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        report(r.status_code == 200 and p["terms"][1]["check"]["status"] == "mismatch", "지출내역 불일치 보고", p and p["terms"][1]["check"]["summary"])
        # 회차별 [송금 확인]은 지출내역 없이도 된다(사용자 2026-11-11) — 예전 대조 기록이 불일치면 사유와 같이 남긴다
        r, p = await post(f"/{pid}/terms/1/confirm", {"date": "2026-11-03", "mismatch_note": "QA — 금액 차이 확인 중"})
        report(r.status_code == 200 and p["terms"][1]["mismatch"] and p["steps"][3]["done"], "잔금 송금 확인(예전 불일치 기록 포함) → 송금 끝")
        g = await c.get(F + f"/{pid}/files/expense/1")
        report(g.status_code == 200 and g.content[:2] == b"PK", "지출내역 파일 보기", g.status_code)

        r, p = await post(f"/{pid}/tax", {"mode": "prepaid", "date": "2026-11-03", "note": "10월분"})
        report(r.status_code == 200 and p["docs"]["tax"]["label"] == "선결제" and p["stage"] == "배송 대기", "세금계산서 선결제 사유 기록(첨부 없이)",
               r.status_code, p and p.get("stage"))
        r, p = await post(f"/{pid}/ship", {"date": p["po"]["date"], "note": "공급사 전화"})
        report(r.status_code == 200 and p["stage"] == "배송 중" and p["in_transit"], "배송 시작 → 배송 중", p and p.get("stage"))
        photo = await c.post(F + f"/{pid}/receipt-photos", files={"file": ("box.png", PNG, "image/png")})
        report(photo.status_code == 200, "입고 사진 올리기", photo.status_code)
        ph = photo.json().get("id", "")
        r, p = await post(f"/{pid}/receipt", {"date": "2026-11-04", "carrier": "경동택배", "tracking": "1234", "receiver": "이승학",
                                              "location": "2층 창고", "lines": [{"line_no": 0, "qty": 8, "photos": [ph], "serials": ["QA-SN-1"]}]})
        report(r.status_code == 200 and p["receipt"]["done"] and p["receipt"]["records"][0]["lines"][0]["photos"] == [ph]
               and p["receipt"]["records"][0]["lines"][0]["serials"] == ["QA-SN-1"] and p["stage"] == "완료",
               "입고 기록(사진·S/N) → 완료(명세서 미등록이어도)", r.status_code, p and p.get("stage"))
        r, p = await post(f"/{pid}/receipt/0/serials", {"lines": [{"line_no": 0, "serials": ["QA-SN-1", "QA-SN-2"]}]})
        report(r.status_code == 200 and p["receipt"]["records"][0]["lines"][0]["serials"] == ["QA-SN-1", "QA-SN-2"], "입고 뒤 S/N 따로 등록", r.status_code)
        g = await c.get(F + f"/{pid}/receipt-photos/{ph}")
        report(g.status_code == 200 and g.content[:4] == b"\x89PNG", "입고 사진 보기", g.status_code)
        r, p = await post(f"/{pid}/files/statement", files={"file": ("명세서.pdf", pdf, "application/pdf")})
        report(r.status_code == 200 and p["docs"]["statement"]["state"] == "file" and p["stage"] == "완료", "거래명세서 첨부만으로 '첨부'", p and p.get("docs"))
        lst = (await c.get(F)).json()
        row = next(x for x in lst["rows"] if x["id"] == pid)
        report(row["prepaid"]["tax"] and row["stage"] == "완료" and "배송 중" in lst["stages"] and "purchases/" not in str(row),
               "리스트 — 선결제 표시, 배송 중 상태 칩, 저장소 키 숨김")
        h = (await c.get(F + f"/{pid}")).json()["history"]
        report(len(h) >= 12, "변경 이력", len(h))

        # ── 해외: 발주(달러) → 송금 완료 → 통관(원산지 증명서) → 입고 ──
        r, o = await post("", {"supplier": SUP_EN, "title": "AMR S300", "region": "overseas"})
        oid = o["id"]
        # 첨부 없이 버튼으로 넘어가기(사용자 2026-11-11)
        r, o = await post(f"/{oid}/quote/skip", {"value": True})
        report(r.status_code == 200 and o["stage"] == "재가 대기", "견적서 없이 넘어가기", o and o.get("stage"))
        r, o = await post(f"/{oid}/approve", {"date": "2026-11-02"})
        report(r.status_code == 200 and o["stage"] == "발주 대기" and o["approval"]["no_file"], "재가 서류 없이 재가 완료", o and o.get("stage"))
        od = (await c.get(F + f"/{oid}/po/draft")).json()
        ob = {"initials": "ZZ", "expected_date": "2026-12-15", "header": {**od["header"], "swift": "BKCHCNBJ",
              "supplier": {"name": SUP_EN, "contact": "", "email": "echo@autoxing.com", "tel": "(+86)136"},
              "issuer": {**od["header"]["issuer"], "name": "Alex Jeon", "attn": "Mr. Alex Jeon (alex@unde.co.kr)"}},
              "items": [{"description": "AX8114\n(S300)", "qty": 2, "price": 4600, "remark": ""}, {"description": "SeaShippingcost", "qty": 1, "price": 2289}]}
        r = await c.post(F + f"/{oid}/po/preview", json=ob)
        report(r.status_code == 200 and r.content[:4] == b"%PDF", "해외 발주서 미리보기 PDF", r.status_code)
        r, o = await post(f"/{oid}/po/issue", ob)
        report(r.status_code == 200 and o["currency"] == "USD" and o["po"]["total"] == 11489 and o["expected_date"] == "2026-12-15",
               "해외 발주서 발행(달러·입고 예정일)", o and (o.get("po"), o.get("detail")))
        g = await c.get(F + f"/{oid}/po/file?fmt=pdf")
        report(g.status_code == 200 and g.content[:4] == b"%PDF" and "Purchasing" in urllib.parse.unquote(g.headers.get("content-disposition", "")),
               "해외 발행본 PDF·파일명", g.status_code)
        # 발주서 고치기 → 다시 발행: 예전 판도 보기·다시 다운로드(사용자 2026-11-11, 견적서와 같게)
        r, o = await post(f"/{oid}/po/issue", {**ob, "items": [*ob["items"], {"commodity": "CAB", "description": "Cable", "qty": 1, "price": 11}]})
        h0 = (o or {}).get("po", {}).get("history") or [{}]
        report(r.status_code == 200 and o["po"]["revision"] == 2 and h0[0].get("revision") == 1 and h0[0].get("file_name"),
               "발주서 다시 발행 — 예전 판 기록(판·파일명)", r.status_code, h0)
        g1 = await c.get(F + f"/{oid}/po/file?fmt=pdf&revision=1")
        g2 = await c.get(F + f"/{oid}/po/file?fmt=xlsx&revision=1")
        report(g1.status_code == 200 and g1.content[:4] == b"%PDF" and g2.status_code == 200 and g2.content[:2] == b"PK",
               "예전 판(v1) 보기 PDF·다시 다운로드 엑셀", g1.status_code, g2.status_code)
        r, o = await post(f"/{oid}/files/remit", files={"file": ("송금확인증.pdf", pdf, "application/pdf")})
        report(r.status_code == 200 and o["customs"]["remit_files"][0]["filename"] == "송금확인증.pdf", "제조사 송금 확인증 첨부", r.status_code)
        r, o = await post(f"/{oid}/paid", {"date": "2026-11-05", "note": "T/T"})
        report(r.status_code == 200 and o["stage"] == "배송 대기", "제조사 송금 확인 → 배송 대기", o and o.get("stage"))
        r, o = await post(f"/{oid}/ship", {"date": "2026-11-20"})
        report(r.status_code == 200 and o["stage"] == "배송 중" and "통관 서류" in o["stage_note"], "해외 배송 중(통관 서류 남음 표시)", o and o.get("stage_note"))
        r, o = await post(f"/{oid}/customs/no-origin", {"value": True})
        r, o2 = await post(f"/{oid}/files/customs_etc", files={"file": ("ci.pdf", pdf, "application/pdf")})
        report(r.status_code == 400 and "서류 이름" in o2["detail"], "기타 서류는 이름 없이 거절", o2)
        r, o = await post(f"/{oid}/files/customs_etc", data={"label": "CI"}, files={"file": ("ci.pdf", pdf, "application/pdf")})
        report(r.status_code == 200 and o["customs"]["no_origin"] and o["customs"]["etc_files"][0]["label"] == "CI", "증빙 자료 없음 → CI 첨부", r.status_code)
        g = await c.get(F + f"/{oid}/files/customs_etc/0")
        report(g.status_code == 200 and g.content[:4] == b"%PDF", "기타 서류 보기", g.status_code)
        r, o = await post(f"/{oid}/customs", {"date": "2026-12-10"})
        report(r.status_code == 200 and o["stage"] == "배송 중" and o["customs"]["done_at"], "통관 서류 제출 완료(원산지 증명서 없이)", o and o.get("stage"))
        r, o = await post(f"/{oid}/broker", {"date": "2026-12-11", "note": "관세"})
        report(r.status_code == 200 and o["customs"]["broker"]["note"] == "관세" and o["stage_note"] == "", "관세사 송금 확인", o and o.get("stage_note"))
        r, o = await post(f"/{oid}/receipt", {"date": "2026-12-15", "lines": [{"line_no": 0, "qty": 2}, {"line_no": 1, "qty": 1}, {"line_no": 2, "qty": 1}]})
        report(r.status_code == 200 and o["stage"] == "완료", "해외 입고 → 완료", o and o.get("stage"))
        lst = (await c.get(F)).json()
        orow = next(x for x in lst["rows"] if x["id"] == oid)
        report(orow["expected_date"] == "2026-12-15" and orow["region"] == "overseas", "리스트 — 해외·입고 예정일")
        await post("/delete", {"ids": [oid]})

        # 예전 양식(LHTC, 'Commodity & Description' 한 칸) 파일로 발행 → 발주서 고치기에 머리·품목(사용자 2026-11-11)
        r, o = await post("", {"supplier": "__QA_구매__LHTC", "region": "overseas"})
        lid = o["id"]
        await post(f"/{lid}/quote/skip", {"value": True})
        await post(f"/{lid}/approve", {"date": "2026-11-02"})
        old = Path("/samples/유엔디로보틱스_LHTC_Purchasing_Lu20261008.xlsx").read_bytes()
        r, ur = await post(f"/{lid}/po/upload", files={"file": ("LHTC_old.xlsx", old, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        report(r.status_code == 200 and ur["items"][0]["commodity"] == "LMO-E04-S11-M" and ur["items"][1]["qty"] == 3
               and (ur.get("header") or {}).get("swift") == "BKCHCNBJ530", "예전 양식 올리기 — 한 칸 품목·수량 '3ea'·머리 읽기", r.status_code, ur and ur.get("summary"))
        its = [{**x, "qty": x["qty"] or 1} for x in ur["items"]]
        r, o = await post(f"/{lid}/po/upload/confirm", {"upload": ur["upload"], "doc_no": "", "initials": "ZZ", "items": its, "header": ur["header"]})
        dr2 = (await c.get(F + f"/{lid}/po/draft")).json()
        report(r.status_code == 200 and dr2["header"]["supplier"]["name"].startswith("Zhengzhou") and dr2["header"]["account_no"] == "250790357823"
               and len(dr2["items"]) == 4, "발주서 고치기 — 예전 양식의 머리·품목이 채워짐", r.status_code, dr2.get("header", {}).get("supplier"))
        r, o = await post(f"/{lid}/po/issue", {"initials": "ZZ", "expected_date": "",
                                              "header": {**dr2["header"], "issuer": {**dr2["header"]["issuer"], "name": "Alex Jeon"}},
                                              "items": [{**dr2["items"][0], "qty": 2}, *dr2["items"][1:]]})
        report(r.status_code == 200 and o["po"]["revision"] == 2 and o["po"]["items"][0]["qty"] == 2 and o["po"]["history"][0]["upload"],
               "고쳐서 다시 발행 — v2, 예전 판은 올린 파일로 남음", r.status_code, o and o.get("detail"))
        # 예전 발주서 PDF 머리 칸(글자 위치) + AI 판독 실패 시 [버그 신고 후 넘어가기](사용자 2026-11-11)
        oldpdf = Path("/samples/유엔디로보틱스_발주서_에이딘로보틱스_PO_VT261102_1.pdf").read_bytes()
        r, ur = await post(f"/{lid}/po/upload", files={"file": ("aidin_old.pdf", oldpdf, "application/pdf")})
        hd = (ur or {}).get("header") or {}
        report(r.status_code == 200 and hd.get("supplier", {}).get("name") == "㈜에이딘로보틱스" and str(hd.get("comments", "")).startswith("6축"),
               "예전 발주서 PDF — 머리 칸(공급사·Comments) 읽기", r.status_code, hd)
        r, o = await post(f"/{lid}/po/upload/confirm", {"upload": ur["upload"], "doc_no": "", "initials": "ZZ",
                                                        "items": [{"commodity": "", "description": "", "qty": None, "price": None}],
                                                        "header": hd, "forced": True, "bug_report": "UND-QA"})
        report(r.status_code == 200 and o["po"]["forced"]["bug_report"] == "UND-QA" and o["po"]["upload"]["filename"] == "aidin_old.pdf",
               "판독 실패 — 버그 신고 후 넘어가기로 파일 그대로 발행", r.status_code, o and o.get("detail"))
        await post("/delete", {"ids": [lid]})

        r, p = await post("/delete", {"ids": [pid]})
        report(r.status_code in (200, 403), "삭제(관리자만)", r.status_code, p)
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM purchases WHERE supplier = ANY(:s)"), {"s": [SUP, SUP_EN]})
        await db.execute(text("DELETE FROM doc_numbers WHERE doc_type = 'PO' AND issuer = 'ZZ'"))
        await db.commit()
    print(f"\n{'ALL OK' if not fails else 'FAILS: ' + ', '.join(fails)}")
    sys.exit(1 if fails else 0)


asyncio.run(main())
