"""회사 경험(학습) 검증 — 카드 검증·전체 목록·회상 검증·[적용]/[빼기]·컨셉을 지식으로 저장.

- 기본: 순수 함수(카드·회상 결과 검증, 목록 정렬·숨김) — DB 불필요.
- --db : 실제 DB 로 카드 저장 → 회상(가짜 모델) → 적용/빼기 → 지식 저장 → 다시 떠오르는지.
    docker compose run --rm --no-deps -v ./scripts:/scripts backend python /scripts/test_experience.py --db
외부(GPT) 호출은 하지 않는다 — codex_client.ask 를 막아 gemma 대체 경로(가짜)로 검증한다.
"""
import asyncio
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
for cand in (Path(__file__).resolve().parents[1] / "backend", Path("/app")):
    if (cand / "app").is_dir():
        sys.path.insert(0, str(cand))
        break

from app.proposal_project import experience as ex  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


# ── 1) 카드 검증 ─────────────────────────────────────────────────────────────
src = ex.Source("casebook:t-01", "casebook", "시험 사례", "실적",
                "사출품 검사 자동화. 공정불량률 60% 감소, 작업인원 2명 감소, 투자회수 2.5년.")
card, dropped = ex.validate_card({
    "title": "사출품 검사·포장 자동화", "process": "사출품 검사", "key_ideas": ["인덱스 컨베이어", " "],
    "effect": "불량률 60% 감소, 생산성 300% 증가",
    "effect_numbers": [{"metric": "공정불량률", "value": "60% 감소"}, {"metric": "생산성", "value": "300% 증가"}]}, src)
check([e["metric"] for e in card["effect_numbers"]] == ["공정불량률"], "원문에 없는 효과 수치(300%)는 버림", str(card))
check("300" not in card["effect"] and "60" not in card["effect"] and dropped,
      "효과 문장에 지어낸 수치가 섞이면 숫자를 빼고 보관", card["effect"])
check(card["key_ideas"] == ["인덱스 컨베이어"], "빈 아이디어 제거")

# ── 2) 전체 목록(머릿속) — 적용된 지식이 앞, 여러 번 빠진 지식은 숨김 ─────────────
CARDS = [
    {"id": 1, "evidence": "제안", "source_title": "회사 제안서 A",
     "card": {"title": "카세트 이송", "process": "카세트 이송", "key_ideas": ["기존 설비 유지 + 인터페이스 박스"],
              "effect_numbers": []}},
    {"id": 2, "evidence": "실적", "source_title": "사례집 #16",
     "card": {"title": "사출품 검사", "process": "사출품 검사", "key_ideas": ["인덱스 컨베이어"],
              "effect_numbers": [{"metric": "공정불량률", "value": "60% 감소"}]}},
    {"id": 3, "evidence": "실적", "source_title": "사례집 #9",
     "card": {"title": "프레스 사상", "process": "프레스", "key_ideas": ["반전 지그"], "effect_numbers": []}},
]
idx = ex.memory_index(CARDS, {1: 2, 3: -2})
lines = idx.splitlines()
check(lines[0].startswith("E1 [제안] (사용자 적용 2회)") and lines[1].startswith("E2 [실적]") and len(lines) == 2,
      "적용받은 지식이 맨 앞, 빼기가 쌓인 지식(E3)은 떠올리지 않음", idx)
check("공정불량률 60% 감소" in lines[1], "목록에 효과 수치 포함")

# ── 3) 회상 결과 검증 ─────────────────────────────────────────────────────────
by_id = {c["id"]: c for c in CARDS}
hits = ex.validate_hits({"hits": [
    {"id": "E2", "suggestion": "예전에 사출품 검사에 인덱스 컨베이어를 써서 불량률을 60% 줄인 효과가 있었습니다. 이번에도 해 보시겠습니까?",
     "why": "외관 검사가 필요", "fit": "맞음"},
    {"id": "E1", "suggestion": "예전에 기존 설비를 유지하고 인터페이스 박스를 추가했더니 효과가 좋았습니다. 이번에도?",
     "why": "기존 설비 재사용", "fit": "주의", "caution": "신호 사양 확인"},
    {"id": "E9", "suggestion": "없는 카드"},
    {"id": "E2", "suggestion": "중복"},
    {"id": "E3", "suggestion": "불량률 99% 감소 효과가 있었습니다"},
]}, by_id, "시간당 17병")
check([h["card_id"] for h in hits] == [2, 1], "없는 카드·중복·근거 없는 수치(99%)는 버림", str([h["card_id"] for h in hits]))
check("효과가 있었습니다" in hits[0]["suggestion"], "[실적] 카드는 '효과가 있었다' 허용")
check("효과가 좋았" not in hits[1]["suggestion"] and "제안한 적이 있" in hits[1]["suggestion"],
      "[제안] 카드의 '효과가 좋았다' 는 '제안한 적이 있다' 로 낮춤", hits[1]["suggestion"])
check(hits[1]["fit"] == "주의" and hits[0]["status"] == "suggested" and hits[0]["evidence"] == "실적"
      and hits[0]["effect_numbers"][0]["value"] == "60% 감소", "판정·상태·근거 수준·효과 수치(카드에서) 보존")

# ── 4) DB 통합 ───────────────────────────────────────────────────────────────
async def db_flow() -> None:
    from sqlalchemy import text

    from app import codex_client, proposal_llm
    from app.database import SessionLocal
    from app.migrations import run_migrations
    from app.proposal_project import service, workflow

    async def no_external(*_a, **_k):
        raise codex_client.CodexError("시험: 외부 호출 없음")
    codex_client.ask = no_external

    async with SessionLocal() as db:
        await run_migrations(db)
        uid = (await db.execute(text("SELECT id FROM users WHERE username='alex'"))).scalar_one()
        before_fb = (await db.execute(text("SELECT COALESCE(MAX(id),0) FROM experience_feedback"))).scalar_one()
        before_ext = (await db.execute(text("SELECT COALESCE(MAX(id),0) FROM external_calls"))).scalar_one()
    s1 = ex.Source("test:exp-a", "casebook", "시험 사례 A", "실적", "보틀 비전 검사로 불량 30% 감소")
    await ex.save_card(s1, {"title": "보틀 비전 검사", "process": "보틀 검사", "key_ideas": ["목 기울어짐 비전 선별"],
                            "effect_numbers": [{"metric": "불량", "value": "30% 감소"}], "problem": "", "solution": "",
                            "effect": "", "cautions": [], "applies_when": "", "industry": ""})
    cards = {c["source_key"]: c for c in await ex.list_cards()}
    cid = cards["test:exp-a"]["id"]

    async def fake_recall_llm(messages, **_kw):
        # 5c 회상 엔진(상황 판단 → 되짚기)과 04 임시 경로 모두 받을 수 있는 가짜 응답.
        return json.dumps({"process": "보틀 검사", "cues": ["보틀 목 기울어짐 선별"], "thinking": "비전 선별이 핵심",
                           "hits": [{"id": f"E{cid}", "ref": f"E{cid}", "fit": "맞음", "facts": ["목 기울어짐 비전 선별"],
                                     "why": "예전에 보틀 목 기울어짐을 비전으로 선별해 불량을 30% 줄인 효과가 있었습니다. 이번에도 적용해 보시겠습니까?",
                                     "suggestion": "예전에 보틀 목 기울어짐을 비전으로 선별해 불량을 30% 줄인 효과가 있었습니다. 이번에도 적용해 보시겠습니까?"}]},
                          ensure_ascii=False)
    proposal_llm.chat = fake_recall_llm
    # 5c 엔진은 단서(knowledge_cues)로 연상된 후보만 되짚는다 — 실제 저장 흐름처럼 단서를 만들어 둔다.
    n_seed = await ex.refresh_cues(cid, cards["test:exp-a"]["card"])
    check(n_seed >= 1, "시험 카드 회상 단서 생성", str(n_seed))
    got = await ex.recall("보틀 목이 기울어진 경우 비전으로 걸러 내고 싶음", brain="gpt", user_id=uid)
    check(len(got) >= 1 and got[0]["card_id"] == cid and got[0]["evidence"] == "실적"
          and "30%" in got[0]["suggestion"] and got[0]["status"] == "suggested",
          "회상(5c 엔진 연결): 경험 기반 제안 형식으로 받음(GPT 실패 시 gemma 대체)", str(got)[:300])
    got = got[:1]

    async with SessionLocal() as db:
        pid = await service.create_project(db, user_id=uid, title="경험 시험", request_text="",
                                           intake={"P06": "유리 보틀 2종"}, assets=[])
        await db.execute(text("UPDATE proposal_projects SET stage='concept', alternatives=CAST(:a AS jsonb), "
                              "experience=CAST(:e AS jsonb) WHERE id=:p"),
                         {"a": json.dumps([{"id": "A", "name": "협동로봇 셀", "robot": "협동로봇 1대", "summary": "",
                                            "reason": "", "structure": ["협동로봇 1대가 컨베이어 옆에 선다"],
                                            "exclude": [], "item_codes": ["P14"]}], ensure_ascii=False),
                          "e": json.dumps([{**got[0], "alt_id": "A"}], ensure_ascii=False), "p": pid})
        await db.commit()
    made_cards = ["test:exp-a", f"project:{pid}:A"]
    try:
        # 컨셉 단계: [컨셉에 반영]/[넘기기] — 지식에는 기록하지 않는다.
        async with SessionLocal() as db:
            p = await service.get_project(db, pid)
            for act in ("apply", "skip"):
                try:
                    await workflow.judge_experience(db, p, cid, act, uid)
                    check(False, f"컨셉 단계에선 지식 판정({act}) 거부")
                except service.ProjectError:
                    await db.rollback()
                    check(True, f"컨셉 단계에선 지식 판정({act}) 거부")
            try:
                await workflow.save_concept_knowledge(db, p, "A", user_id=uid, approver=True)
                check(False, "컨셉 단계에선 지식 저장 거부")
            except service.ProjectError:
                await db.rollback()
                check(True, "컨셉 단계에선 지식 저장 거부")
            hit = await workflow.judge_experience(db, p, cid, "reflect", uid)
            check(hit["status"] == "reflected", "[컨셉에 반영] 표시")
            p = await service.get_project(db, pid)
            try:
                await workflow.judge_experience(db, p, cid, "dismiss", uid)
                check(False, "이미 고른 경험은 다시 못 고름")
            except service.ProjectError:
                await db.rollback()
                check(True, "이미 고른 경험은 다시 못 고름")
        check((await ex.card_scores()).get(cid) is None, "컨셉 단계 선택은 지식 가중치에 남지 않음")

        # 제안서 완성 후: [적용]/[빼기] — 이때만 지식에 기록.
        async with SessionLocal() as db:
            await db.execute(text("UPDATE proposal_projects SET stage='done' WHERE id=:p"), {"p": pid})
            await db.commit()
            p = await service.get_project(db, pid)
            hit = await workflow.judge_experience(db, p, cid, "apply", uid)
            check(hit["judged"] == "apply" and hit["status"] == "reflected", "완성 후 [적용] = 맞는 지식 판정")
            p = await service.get_project(db, pid)
            try:
                await workflow.judge_experience(db, p, cid, "skip", uid)
                check(False, "이미 판정한 경험은 다시 못 고름")
            except service.ProjectError:
                await db.rollback()
                check(True, "이미 판정한 경험은 다시 못 고름")
        scores = await ex.card_scores()
        check(scores.get(cid) == 1, "완성 후 [적용] 이 지식 가중치로 쌓임", str(scores.get(cid)))
        for _ in range(3):
            await ex.record_feedback(cid, "skip", project_id=pid, user_id=uid)
        idx2 = ex.memory_index(await ex.list_cards(), await ex.card_scores())
        check(f"E{cid} " not in idx2, "[빼기] 가 쌓이면 더 이상 떠올리지 않음")

        # 승인권자 판정 — 영업 도메인 관리자만(superadmin·다른 도메인 관리자·영업 구성원 제외).
        from types import SimpleNamespace as U
        check(ex.is_knowledge_approver(U(role="domain_admin", domain="sales"))
              and not ex.is_knowledge_approver(U(role="superadmin", domain="all"))
              and not ex.is_knowledge_approver(U(role="domain_admin", domain="finance"))
              and not ex.is_knowledge_approver(U(role="member", domain="sales")), "승인권자 = 영업 도메인 관리자만")
        async with SessionLocal() as db:
            admin = (await db.execute(text("SELECT role, domain FROM users WHERE username='alex'"))).one()
        check(ex.is_knowledge_approver(U(role=admin[0], domain=admin[1])), "sales_admin 계정은 승인권자", str(admin))

        # 승인권자가 아닌 사람이 저장 → 승인 대기(회상에 안 쓰임)
        async with SessionLocal() as db:
            p = await service.get_project(db, pid)
            kid = await workflow.save_concept_knowledge(db, p, "A", user_id=uid, approver=False)
        check(kid not in {c["id"] for c in await ex.list_cards()}, "승인 전 지식은 회상 목록에 없음")
        pend = {r["id"]: r for r in await ex.pending_reviews()}
        check(kid in pend and pend[kid]["project_id"] == pid and pend[kid]["submitted_by"] == "alex",
              "승인 대기 목록에 카드·제출자·원본 프로젝트", str(pend.get(kid))[:200])
        check((await ex.card_reviews([kid])) == {kid: "pending"}, "작업 화면용 상태 = pending")
        try:
            await ex.review_card(kid, "reject", reviewer_id=uid, note=" ")
            check(False, "반려는 사유 필수")
        except ex.ReviewError:
            check(True, "반려는 사유 필수")
        check(await ex.review_card(kid, "approve", reviewer_id=uid) == "approved", "승인")
        try:
            await ex.review_card(kid, "approve", reviewer_id=uid)
            check(False, "이미 판정한 지식은 다시 판정 못 함")
        except ex.ReviewError:
            check(True, "이미 판정한 지식은 다시 판정 못 함")
        async with SessionLocal() as db:
            p = await service.get_project(db, pid)
        saved = {c["id"]: c for c in await ex.list_cards()}[kid]
        check(p["alternatives"][0]["knowledge_card_id"] == kid and saved["source_type"] == "project"
              and saved["evidence"] == "제안" and any("비전으로 선별" in k for k in saved["card"]["key_ideas"])
              and "경험 시험" not in json.dumps(saved["card"], ensure_ascii=False),
              "좋은 컨셉 → 회사 지식(경험 카드)으로 저장, 적용한 경험 포함, 프로젝트명(고객 정보) 빼고",
              json.dumps(saved["card"], ensure_ascii=False)[:300])
        idx3 = ex.memory_index(await ex.list_cards(), await ex.card_scores())
        check(f"E{kid} [제안]" in idx3, "저장한 컨셉이 다음 회상 목록에 들어감")
        async with SessionLocal() as db:
            n_cues = (await db.execute(text("SELECT count(*) FROM knowledge_cues WHERE card_table='experience_cards' "
                                            "AND card_id=:c"), {"c": kid})).scalar_one()
        check(n_cues >= 1, "승인하면 회상 단서가 바로 생김(연상에 걸림)", str(n_cues))

        # 승인된 지식을 승인권자가 아닌 사람이 다시 저장(수정) → 다시 승인 대기
        async with SessionLocal() as db:
            p = await service.get_project(db, pid)
            again = await workflow.save_concept_knowledge(db, p, "A", user_id=uid, approver=False)
        check(again == kid and kid not in {c["id"] for c in await ex.list_cards()}
              and (await ex.card_reviews([kid])) == {kid: "pending"}, "승인된 지식을 고치면 다시 승인 대기")
        check(await ex.review_card(kid, "reject", reviewer_id=uid, note="공정 조건이 달라 일반화 어려움") == "rejected"
              and kid not in {c["id"] for c in await ex.list_cards()}, "반려하면 회상에 쓰지 않음")
        # 승인권자가 직접 저장 → 바로 승인
        async with SessionLocal() as db:
            p = await service.get_project(db, pid)
            await workflow.save_concept_knowledge(db, p, "A", user_id=uid, approver=True)
        check((await ex.card_reviews([kid])) == {kid: "approved"} and kid in {c["id"] for c in await ex.list_cards()},
              "승인권자가 저장하면 바로 회상에 쓰임")

        # '지식 저장' 한 번(2026-09-30): 컨셉마다 카드 + 반영한 경험만 맞는 지식(넘긴 경험은 기록 안 함)
        detailed = await ex.attach_details([{k: v for k, v in got[0].items() if k != "detail"}])
        check(bool(detailed[0].get("detail")), "예전 경험에도 상세(무슨 공정이었나)를 붙여 보여 줌", str(detailed[0].get("detail")))
        async with SessionLocal() as db:
            pid2 = await service.create_project(db, user_id=uid, title="경험 일괄 시험", request_text="",
                                                intake={"P06": "유리 보틀 2종"}, assets=[])
            alt = {"robot": "협동로봇 1대", "summary": "", "reason": "", "exclude": [], "item_codes": ["P14"]}
            await db.execute(text("UPDATE proposal_projects SET stage='done', alternatives=CAST(:a AS jsonb), "
                                  "experience=CAST(:e AS jsonb) WHERE id=:p"),
                             {"a": json.dumps([{**alt, "id": "A", "name": "셀 A", "structure": ["협동로봇 1대가 선다"]},
                                               {**alt, "id": "B", "name": "셀 B", "structure": ["AMR 1대가 선다"]}],
                                              ensure_ascii=False),
                              "e": json.dumps([{**got[0], "alt_id": "A", "status": "reflected"},
                                               {**got[0], "card_id": -1, "alt_id": "A", "status": "dismissed"}],
                                              ensure_ascii=False), "p": pid2})
            await db.commit()
        made_cards += [f"project:{pid2}:A", f"project:{pid2}:B"]
        try:
            fb_before = (await ex.card_scores()).get(cid, 0)
            async with SessionLocal() as db:
                p2 = await service.get_project(db, pid2)
                res = await workflow.save_all_knowledge(db, p2, user_id=uid, approver=False)
                p2 = await service.get_project(db, pid2)
            check(len(res["saved"]) == 2 and res["judged"] == 1
                  and all(a.get("knowledge_card_id") for a in p2["alternatives"]),
                  "지식 저장 한 번: 컨셉 2개 카드 + 반영 경험 1개", str(res))
            check((await ex.card_scores()).get(cid, 0) == fb_before + 1
                  and [h.get("judged") for h in p2["experience"]] == ["apply", None],
                  "반영한 경험만 맞는 지식, 넘긴 경험은 기록 안 함", str([h.get("judged") for h in p2["experience"]]))
            check(all(v == "pending" for v in (await ex.card_reviews(
                [a["knowledge_card_id"] for a in p2["alternatives"]])).values()), "승인권자가 아니면 승인 대기")
            async with SessionLocal() as db:
                try:
                    await workflow.save_all_knowledge(db, p2, user_id=uid, approver=False)
                    check(False, "다 저장한 뒤 다시 누르면 안내")
                except service.ProjectError as e:
                    await db.rollback()
                    check("이미 모두" in str(e), "다 저장한 뒤 다시 누르면 안내", str(e))
        finally:
            async with SessionLocal() as db:
                await db.execute(text("DELETE FROM proposal_projects WHERE id=:p"), {"p": pid2})
                await db.commit()
    finally:
        async with SessionLocal() as db:
            await db.execute(text("DELETE FROM proposal_projects WHERE id=:p"), {"p": pid})
            await db.execute(text("DELETE FROM knowledge_cues WHERE card_table='experience_cards' AND card_id IN "
                                  "(SELECT id FROM experience_cards WHERE source_key = ANY(:k))"), {"k": made_cards})
            await db.execute(text("DELETE FROM experience_cards WHERE source_key = ANY(:k)"), {"k": made_cards})
            await db.execute(text("DELETE FROM experience_feedback WHERE id > :b"), {"b": before_fb})
            await db.execute(text("DELETE FROM external_calls WHERE id > :b"), {"b": before_ext})
            await db.commit()


if "--db" in sys.argv:
    asyncio.run(db_flow())

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print("  FAIL", label, detail)
sys.exit(1 if FAIL else 0)
