"""회사 제품 추천 + 정정 학습(company_knowledge/product_recommend.py) 점검.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./scripts:/scripts backend python /scripts/test_product_recommend.py [--db]

--db: 실제 DB·임베딩으로 추천 → 정정 저장 → 다음 추천에서 정정을 읽고 재고 → 관리자 끄기까지(모델 응답만 가짜).
만든 추천·정정 행은 끝에 지운다.
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.company_knowledge import product_recommend as pr

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


CAT = [{"card_id": 1, "name": "마그네틱 그리퍼 MG series", "family": "Magbot EOAT", "line": ""},
       {"card_id": 2, "name": "mDPG-C series", "family": "Magbot EOAT", "line": ""},
       {"card_id": 3, "name": "SLIM AMR", "family": "AMR", "line": ""}]

check(pr.looks_like_correction("MG 가 아니라 mDPG-C 를 쓸 거야, 왜냐하면 비철이라서")
      and pr.looks_like_correction("이건 틀렸어 정답은 SLIM AMR") and not pr.looks_like_correction("무거운 철판 집는 그리퍼 추천"),
      "정정처럼 보이는 글 알아보기")
check(pr.match_product("mdpg-c Series", CAT)["card_id"] == 2 and pr.match_product("MG series", CAT)["card_id"] == 1
      and pr.match_product("없는 제품", CAT) is None, "제품 이름 맞추기(대소문자·기호 무시, 없으면 None)")
corr = [{"id": 7, "situation": "알루미늄 판재", "wrong_name": "마그네틱 그리퍼 MG series", "wrong_card_id": 1,
         "right_name": "mDPG-C series", "right_card_id": 2, "reason": "비철은 자석에 안 붙음", "rule": ""}]
v = pr.validate_recommendation({"summary": "s", "items": [{"product": "마그네틱 그리퍼 MG series", "reason": "r"},
                                                          {"product": "지어낸 그리퍼"}, {"product": "mDPG-C series"}],
                                "reconsidered": [{"correction_id": 7, "note": "비철이라 MG 대신 mDPG-C"},
                                                 {"correction_id": 99, "note": "없는 정정"}]}, CAT, corr)
check([i["product"] for i in v["items"]] == ["마그네틱 그리퍼 MG series", "mDPG-C series"] and v["dropped"] == ["지어낸 그리퍼"],
      "목록에 없는 제품은 버림", str(v))
check("warning" in v["items"][0] and "#7" in v["items"][0]["warning"], "정정에서 틀렸다고 한 제품이 또 나오면 경고")
check([r["correction_id"] for r in v["reconsidered"]] == [7], "없는 정정 번호는 버림")
check(not pr.is_product("Magbot EOAT") and not pr.is_product("스위칭 마그네틱 기술") and pr.is_product("mDPG-C series"),
      "묶음·기술 카드는 추천 후보에서 뺌(QA 1회차)")
check("강자성체" in pr.principle_of("마그네틱 그리퍼 MG series", {"family": "Magbot EOAT"})
      and "진공" in pr.principle_of("mDPG-C series", {"family": "mDPG"})
      and pr.principle_of("MTC (Manual TC)", {"family": "Magbot ATC", "how_it_works": "Lock 구조 마그네틱"}) == ""
      and "핑거" in pr.principle_of("2FINGER_GRIPPER", {"family": "Magbot EOAT"}),
      "원리상 제약: 그리퍼에만(툴체인저·구조 오탐 없음)")
check(pr.payload_range("마그네틱 그리퍼 MG series", {"family": "Magbot EOAT", "specs": [
          {"item": "가반하중", "value": "5kgf [49N]"}, {"item": "가반하중", "value": "30kgf [294N]"}]}) == "가반하중: 5~30kgf(모델별)"
      and pr.payload_range("mDPG-C series", {"family": "mDPG", "specs": []}).startswith("가반하중: 자료 없음")
      and pr.payload_range("SLIM AMR", {"family": "AMR", "specs": []}) == "",
      "가반하중 범위(사양 표) / 그리퍼인데 자료 없으면 명시(QA 3회차)")
check("원리상 제약(추정)" in pr.product_line("마그네틱 그리퍼", {"family": "Magbot EOAT", "summary": "s", "how_it_works": "자력"}),
      "추천에 넘기는 제품 줄에 동작 방식·원리상 제약 포함")
va = pr.validate_recommendation({"items": [{"product": "mDPG-C series"}]}, CAT, corr)
check([r["correction_id"] for r in va["reconsidered"]] == [7] and va["reconsidered"][0].get("auto"),
      "정정대로 바뀌었는데 모델이 안 적으면 재고 기록을 코드가 채움(QA 5회차)", str(va["reconsidered"]))
vb = pr.validate_recommendation({"items": [{"product": "마그네틱 그리퍼 MG series"}, {"product": "mDPG-C series"}]}, CAT, corr)
check(not vb["reconsidered"], "틀린 제품이 그대로 있으면 자동 재고 기록 안 함(경고만)")
check(not pr.is_product("magbot DC Pnuematic Gripper (mDPG 시리즈)"), "개별 모델이 있는 제품군 카드 제외(QA 5회차)")
CAT2 = [dict(c, official=c["card_id"] != 3) for c in CAT] + [{"card_id": 9, "name": "H시리즈", "family": "ATC",
                                                                 "line": "", "official": False}]
vo = pr.validate_recommendation({"items": [{"product": "H시리즈"}, {"product": "mDPG-C series"}]}, CAT2, [])
check([i["product"] for i in vo["items"]] == ["mDPG-C series", "H시리즈"] and vo["items"][1].get("info_note")
      and not vo["items"][0].get("info_note"),
      "정보 미흡(소개서 요약) 제품은 1순위 금지 — 공식 사양 제품을 앞으로, 미흡 표시(2026-10-01)", str(vo["items"]))
va2 = pr.validate_recommendation({"items": [{"product": "SLIM AMR"}]}, CAT2, [])
check([i["product"] for i in va2["items"]] == ["SLIM AMR"] and va2["items"][0].get("info_note"),
      "공식 사양 제품이 없는 분야는 그대로 추천하되 미흡 표시")
d = pr.validate_correction({"wrong": "MG series", "right": "mDPG-C", "reason": "", "situation": "알루미늄"}, CAT, "원문")
check(d["wrong_card_id"] == 1 and d["right_card_id"] == 2 and d["missing"] == ["이유"], "정정 초안: 제품 맞춤 + 이유 없으면 빠짐 표시", str(d))
d2 = pr.validate_correction({"wrong": "MG", "right": "타사 진공 그리퍼", "reason": "특주"}, CAT, "원문")
check(d2["right_card_id"] is None and not d2["right_in_catalog"] and not d2["missing"], "정답이 회사 제품이 아니어도 저장 가능(표시)")


async def db_flow() -> None:
    from sqlalchemy import text

    from app.database import SessionLocal
    from app.migrations import run_migrations

    async with SessionLocal() as db:
        await run_migrations(db)
        catalog = await pr.product_catalog(db)
    eoat = [p for p in catalog if "EOAT" in p["family"] or "그리퍼" in p["name"]]
    check(len(eoat) >= 2, "회사 제품 DB 에 그리퍼 2종 이상", str(len(eoat)))
    wrong, right = eoat[0]["name"], eoat[1]["name"]
    prompts: list[str] = []

    async def fake(messages, **_kw):
        sys_, user = messages[0]["content"], messages[-1]["content"]
        prompts.append(user)
        if "이야기한다" in sys_:
            prompts.append(messages[1]["content"])      # 대화 맥락(조건·현재 추천·회사 제품)
            last = messages[-1]["content"]
            if "다시" in last:
                return json.dumps({"answer": "공압 없이 쓰는 조건으로 다시 골랐습니다.", "recommend_again": True,
                                   "condition": "공압 없음"}, ensure_ascii=False)
            if "말고" in last:
                return json.dumps({"answer": "정정으로 받아들이겠습니다.", "learn": True}, ensure_ascii=False)
            return json.dumps({"answer": f"1번은 {wrong} 입니다.", "recommend_again": False}, ensure_ascii=False)
        if "기록 담당" in sys_:
            if "써서" in user:
                return json.dumps({"kind": "experience", "wrong": wrong, "right": right, "reason": "파지 실패 0건, 교체 시간 절반",
                                   "situation": "알루미늄 케이스 이송 공정", "rule": f"알루미늄 이송이면 {right} 우선 검토",
                                   "missing": []}, ensure_ascii=False)
            return json.dumps({"wrong": wrong, "right": right, "reason": "알루미늄은 자석에 붙지 않는다",
                               "situation": "알루미늄 판재를 집는 공정", "rule": f"비철이면 {wrong} 대신 {right}",
                               "missing": []}, ensure_ascii=False)
        cid = next((int(line.split()[0][1:]) for line in user.split("\n") if line.startswith("#")), None)
        if cid and wrong in user.split("[AI 학습 내용]")[1]:
            return json.dumps({"summary": "정정 반영", "items": [{"product": right, "reason": "비철 대응"}],
                               "reconsidered": [{"correction_id": cid, "note": f"비철이라 {wrong} 대신 {right}"}]},
                              ensure_ascii=False)
        return json.dumps({"summary": "첫 추천", "items": [{"product": wrong, "reason": "판재 파지"}]}, ensure_ascii=False)

    made_rec: list[int] = []
    made_cor: list[int] = []
    made_prop: list[int] = []

    async def _empty_rec():
        return json.dumps({"summary": "맞는 제품 없음", "items": []}, ensure_ascii=False)
    try:
        r1 = await pr.recommend("알루미늄 판재 3kg 를 집는 그리퍼 추천", user_id=None, chat=fake)
        made_rec.append(r1["id"])
        check([i["product"] for i in r1["items"]] == [wrong] and not r1["reconsidered"], "첫 추천(정정 없음)", str(r1)[:200])
        check("image_id" in r1["items"][0], "추천 결과에 제품 사진 id(없으면 None)", str(r1["items"][0]))
        a1 = await pr.converse(r1, [], "1번은 뭐야?", user_id=None, chat=fake)
        check(wrong in a1["answer"] and "recommendation" not in a1 and "[현재 추천]" in prompts[-1],
              "대화: 추천에 대한 질문에 답(현재 추천을 맥락으로)", str(a1))
        a2 = await pr.converse(r1, [{"role": "user", "text": "1번은 뭐야?"}, {"role": "assistant", "text": a1["answer"]}],
                               "공압 없는 걸로 다시 골라 줘", user_id=None, chat=fake)
        made_rec.append(a2["recommendation"]["id"])
        check(a2.get("recommendation") and "[추가 조건] 공압 없음" in a2["recommendation"]["request"],
              "대화: 조건을 바꿔 다시 골라 달라면 새 추천", str(a2)[:200])
        a3 = await pr.converse(r1, [], f"{wrong} 말고 {right} 를 쓸 거야, 알루미늄이라서", user_id=None, chat=fake)
        check(a3.get("learn") and "recommendation" not in a3, "대화 중 바로잡으면 학습 제안(바로 저장 안 함)")
        a4 = await pr.converse(r1, [], "이거 다음부터는 이렇게 하도록 학습해 줘", user_id=None, chat=fake)
        check(a4.get("learn"), "'학습해 줘'라고 하면 AI 판단이 빠져도 학습 제안", str(a4))
        draft = await pr.draft_correction("이거 학습해 줘", recommendation=r1, chat=fake,
                                          history=[{"role": "user", "text": f"{wrong} 가 아니라 {right} 를 쓸 거야. 알루미늄이라 자석에 안 붙어"}])
        check(draft["wrong_name"] == wrong and draft["right_name"] == right and not draft["missing"]
              and "[직전 요청]" in prompts[-1] and "[대화]" in prompts[-1] and "자석에 안 붙어" in prompts[-1],
              "학습 초안: 직전 추천 + 앞 대화 내용까지 읽음('이거 학습해 줘')", str(draft)[:200])
        pend = await pr.save_correction(draft, user_id=None, recommendation_id=r1["id"])
        made_cor.append(pend["id"])
        async with SessionLocal() as db:
            rel0 = await pr.relevant_corrections(db, "알루미늄 판재를 옮기는 그리퍼 골라줘")
        check(not pend["active"] and pend["review_status"] == "pending" and pend["id"] not in [c["id"] for c in rel0],
              "일반 영업 사용자가 학습시키면 승인 대기 — 승인 전엔 추천에 안 씀", str(pend)[:200])
        await pr.review_correction(pend["id"], "reject", reviewer_id=1)
        check((await pr.get_correction(pend["id"]))["review_status"] == "rejected", "관리자 거절")
        edited = {**draft, "rule": "알루미늄 판재(비자성)는 마그네틱 대신 진공·핑거 계열로 — 자석에 안 붙음"}
        saved = await pr.save_correction(edited, user_id=None, recommendation_id=r1["id"], approver=True)
        made_cor.append(saved["id"])
        check(saved["active"] and saved["review_status"] == "kept" and saved["recommendation_id"] == r1["id"]
              and saved["rule"] == edited["rule"] and "학습 문구: 알루미늄 판재(비자성)" in pr.correction_text(saved),
              "영업 관리자가 학습시키면 바로 반영 + 사용자가 고친 학습 문구 그대로 저장", str(saved)[:200])
        r2 = await pr.recommend("알루미늄 판재를 옮기는 그리퍼 골라줘", user_id=None, chat=fake)
        made_rec.append(r2["id"])
        check(f"#{saved['id']}" in prompts[-1] and [i["product"] for i in r2["items"]] == [right]
              and [x["correction_id"] for x in r2["reconsidered"]] == [saved["id"]],
              "다음 추천: 비슷한 조건의 정정을 읽고 재고(오답 회피)", json.dumps(r2, ensure_ascii=False)[:300])
        check((await pr.get_correction(saved["id"]))["hits"] == 1, "정정이 쓰인 횟수 기록")
        await pr.review_correction(saved["id"], "off", reviewer_id=1)
        async with SessionLocal() as db:
            rel = await pr.relevant_corrections(db, "알루미늄 판재를 옮기는 그리퍼 골라줘")
        check(saved["id"] not in [c["id"] for c in rel], "관리자가 끄면 다음 추천에서 안 씀")
        sd = await pr.draft_correction(f"알루미늄 케이스 이송 공정에 {right} 를 써서 파지 실패 0건이었어", recommendation=r1, chat=fake)
        check(sd["kind"] == "experience" and sd["wrong_name"] == "" and sd["right_name"] == right and not sd["missing"],
              "경험 초안: 사용 제품·결과, 틀린 제품 없음", str(sd)[:200])
        ss = await pr.save_correction(sd, user_id=None, recommendation_id=r1["id"], approver=True)
        made_cor.append(ss["id"])
        check(ss["kind"] == "experience" and ss["active"] and ss["wrong_name"] == ""
              and pr.correction_text(ss).startswith(f"#{ss['id']} [경험]"),
              "경험 저장 + 추천 프롬프트에 '[경험]'으로 들어감", str(ss)[:200])
        try:
            await pr.save_correction({**draft, "reason": ""}, user_id=None, recommendation_id=None)
            check(False, "이유 없는 정정은 저장 거부")
        except pr.RecommendError:
            check(True, "이유 없는 정정은 저장 거부")
        # 최종 제안 확정(사용자 2026-10-02) — 추천 한 건 → 확정 제안(미팅·결과·근거·대화), 같은 추천은 덮어씀
        hist = [{"role": "user", "text": "이 후보로 진행해요"}, {"role": "assistant", "text": "추천을 확정했습니다."}]
        fp = await pr.finalize_recommendation(r2["id"], user_id=None, history=hist, memo="견적 요청 예정")
        fp2 = await pr.finalize_recommendation(r2["id"], user_id=None, history=hist + [{"role": "user", "text": "메모 고침"}])
        made_prop.append(fp["id"])
        lst = await pr.list_proposals()
        check(fp["id"] == fp2["id"] and fp["model"] == right and fp["snapshot"]["recommendation"]["id"] == r2["id"]
              and len(fp2["snapshot"]["history"]) == 3 and fp["memo"] == "견적 요청 예정"
              and any(x["id"] == fp["id"] and "snapshot" not in x for x in lst),
              "최종 제안 확정: 결과·근거·대화를 한 건으로 저장, 같은 추천은 덮어씀, 목록엔 요약만", str(fp)[:300])
        try:
            empty = await pr.recommend("아무 제품도 안 맞는 조건", user_id=None,
                                       chat=lambda *a, **k: _empty_rec())
            made_rec.append(empty["id"])
            await pr.finalize_recommendation(empty["id"], user_id=None, history=[])
            check(False, "후보 없는 결과는 확정 거부")
        except pr.RecommendError:
            check(True, "후보 없는 결과는 확정 거부")
    finally:
        async with SessionLocal() as db:
            await db.execute(text("DELETE FROM product_proposals WHERE id = ANY(:i)"), {"i": made_prop})
            await db.execute(text("DELETE FROM product_corrections WHERE id = ANY(:i)"), {"i": made_cor})
            await db.execute(text("DELETE FROM product_recommendations WHERE id = ANY(:i)"), {"i": made_rec})
            await db.commit()


# AI 학습 내용 = 경험 + 정정 + 기준(2026-10-02)
VC = [{"card_id": 1, "name": "MG30"}, {"card_id": 2, "name": "mDPG-C"}]
v_ok = pr.validate_correction({"kind": "experience", "wrong": "MG30", "right": "mDPG-C", "reason": "불량 0건"}, VC, "n")
v_old = pr.validate_correction({"kind": "success", "right": "mDPG-C", "reason": "r"}, VC, "n")
v_bad = pr.validate_correction({"kind": "experience", "right": "mDPG-C", "reason": ""}, VC, "n")
v_cor = pr.validate_correction({"wrong": "MG30", "right": "", "reason": "비철"}, VC, "n")
v_rule = pr.validate_correction({"kind": "rule", "rule": "협동로봇이면 속도부터 확인", "reason": ""}, VC, "n")
v_rule0 = pr.validate_correction({"kind": "rule", "rule": ""}, VC, "n")
check(v_ok["kind"] == "experience" and v_ok["wrong_name"] == "" and v_ok["right_card_id"] == 2 and not v_ok["missing"]
      and v_old["kind"] == "experience" and v_bad["missing"] == ["결과"]
      and v_cor["kind"] == "correction" and v_cor["missing"] == ["정답 제품"]
      and not v_rule["missing"] and v_rule0["missing"] == ["학습 문구"],
      "학습 종류: 경험=사용 제품·결과, 정정=정답 제품·이유, 기준=학습 문구가 필요(예전 success→경험)",
      str([v_ok, v_bad, v_cor, v_rule, v_rule0])[:400])
check(pr.correction_text({"id": 3, "kind": "experience", "situation": "s", "right_name": "B", "reason": "r"})
      == "#3 [경험] [조건] s → 'B' 사용. 결과: r"
      and pr.correction_text({"id": 4, "situation": "s", "wrong_name": "A", "right_name": "B", "reason": "r"}).startswith("#4 [정정]")
      and pr.correction_text({"id": 5, "kind": "rule", "situation": "s", "rule": "속도부터 확인", "reason": ""})
      == "#5 [기준] [조건] s → 속도부터 확인",
      "회상 한 줄: 경험/정정/기준 구분")


# 가반하중 여유(2026-10-01): 25kg 에 MG25(25kgf)가 1순위로 나오면 여유 있는 MG30 을 앞으로
MGCAT = [{"card_id": 25, "name": "MG25", "family": "MG", "official": True, "line": "",
          "card": {"specs": [{"item": "가반하중", "value": "25 kgf [245N / 55lbs]"}]}},
         {"card_id": 30, "name": "MG30", "family": "MG", "official": True, "line": "",
          "card": {"specs": [{"item": "가반하중", "value": "30 kgf [294N / 66lbs]"}]}},
         {"card_id": 99, "name": "MGM3D", "family": "MGM", "official": False, "line": "", "card": {"specs": []}}]
req25 = "Q. 대상물 재질은?\nA. 철·강(자석 붙음)\nQ. 무게는? (툴 교체면 툴 무게)\nA. 약 25kg"
check(pr.request_weight(req25) == 25 and pr.request_weight("철판 25kg 집는 그리퍼") is None
      and pr.request_weight("Q. 무게는?\nA. 툴 포함 1,200kg") == 1200
      and pr.request_weight(pr._clean(req25 + "\nQ. 크기·형상·표면은?\nA. 400×300mm 3kg 이하 부속", 2000)) == 25,
      "Q&A 무게 답 읽기(줄바꿈이 합쳐진 저장 형태도, 다음 문항 숫자는 안 섞임, 자유 문장은 판단 안 함)")
check(pr.payload_max(MGCAT[0]["card"]) == 25 and pr.payload_max({"specs": [{"item": "가반하중", "value": "패드 구성 의존"}]}) is None,
      "제품 가반하중 최댓값(수치 없으면 None)")
vm = pr.validate_recommendation({"items": [{"product": "MG25"}, {"product": "MG30"}]}, MGCAT, [], req25)
check([i["product"] for i in vm["items"]] == ["MG30", "MG25"], "여유 20% 미만 1순위는 여유 있는 후보와 자리 바꿈",
      str([i["product"] for i in vm["items"]]))
vm2 = pr.validate_recommendation({"items": [{"product": "MG25"}, {"product": "MGM3D"}]}, MGCAT, [], req25)
check([i["product"] for i in vm2["items"]][0] == "MG25", "정보 미흡·가반하중 모름 후보는 올리지 않음")
vm3 = pr.validate_recommendation({"items": [{"product": "MG25"}, {"product": "MG30"}]}, MGCAT, [], "철판 25kg")
check([i["product"] for i in vm3["items"]][0] == "MG25", "무게를 모르면 순서를 건드리지 않음")
check(vm["items"][0]["specs"] and vm["items"][0]["specs"][0]["value"].startswith("30 kgf"), "추천 결과의 주요 사양은 DB 값")

# AI 용어 오타(2026-10-01 실측 '스위칭 마어네틱') — 틀린 꼴만 고치고 맞는 말은 그대로
check(pr.fix_terms("스위칭 마어네틱 원리로 작동하며 마네틱 그리파") == "스위칭 마그네틱 원리로 작동하며 마그네틱 그리파"
      and pr.fix_terms("마그네틱 그리퍼와 툴체인저, 툴체인져, 스위칭 마그네틱") == "마그네틱 그리퍼와 툴체인저, 툴체인져, 스위칭 마그네틱"
      and pr.fix_terms("자석 그러퍼, 툴체인져") == "자석 그리퍼, 툴체인져",
      "AI 문장 용어 오타 바로잡기(마어네틱→마그네틱, 그러퍼→그리퍼), 맞는 말은 안 바꿈",
      pr.fix_terms("스위칭 마어네틱 원리로 작동하며 마네틱 그리파"))
vt = pr.validate_recommendation({"summary": "스위칭 마어네틱 방식", "items": [{"product": "MG30", "reason": "스위칭 마어네틱 원리로 작동하며",
                                 "checks": [{"condition": "재질", "basis": "마어네틱 흡착", "ok": True}]}]}, MGCAT, [], req25)
check("마어네틱" not in json.dumps(vt, ensure_ascii=False), "추천 결과(요약·이유·근거) 전체에 적용")


if "--db" in sys.argv:
    asyncio.run(db_flow())

# 확정 제안 머리 — 질문지 '고객 정보' 단계의 고객사도 쓴다(사용자 2026-10-02)
hd = pr._proposal_head({"kind": "atc", "intake": {"project": {"customer_name": "가나다산업"}},
                        "atc": {"screening_candidates": [{"models": [{"name": "TCC1"}]}], "junior_summary": {}}})
check(hd["customer"] == "가나다산업" and hd["title"] == "가나다산업 · TCC1", "확정 제안 고객사: 미팅 파일이 없으면 질문지 고객 정보", str(hd))

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
