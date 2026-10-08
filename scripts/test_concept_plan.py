"""공정 컨셉 계획 확인 단계(proposal_project/plan.py) 점검 — 이미지 전에 로봇·그리퍼를 글로 정한다.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./scripts:/scripts backend python /scripts/test_concept_plan.py [--db]

--db 면 로봇 DB(robot_specs)에서 실제 후보를 고르는지까지 본다(로봇 비교표 적재 전제).
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.proposal_project import concept, plan

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


# ── 제안 응답의 로봇 요구 사양·그리퍼 추천 ────────────────────────────────────
h = concept.plan_hint({"robot_need": {"arm": True, "payload_kg": 8, "reach_mm": 1300, "ip_min": 54, "why": "식기 1.6kg+그리퍼"},
                       "gripper": "mDPG-C series", "gripper_why": "물기 있는 식기"})
check(h["robot_need"] == {"arm": True, "payload_kg": 8.0, "reach_mm": 1300.0, "ip_min": 54, "exclude_makers": [],
                          "why": "식기 1.6kg+그리퍼", "makers": [], "priority": "price"}
      and h["gripper_ai"] == "mDPG-C series", "요구 사양·그리퍼 추천 읽기(기본 우선순위 = 가격 중심)", str(h))
h2 = concept.plan_hint({"robot_need": {"payload_kg": 10, "makers": ["dobot"], "priority": "domestic", "mobile": True}})
check(h2["robot_need"]["makers"] == ["DOBOT"] and h2["robot_need"]["priority"] == "domestic" and h2["robot_need"]["mobile"],
      "지정 제조사 표기 맞춤·국내 우선·AMR 탑재", str(h2))
bad = concept.plan_hint({"robot_need": {"payload_kg": 500, "reach_mm": "모름", "ip_min": 99}})
check(bad["robot_need"]["payload_kg"] is None and bad["robot_need"]["reach_mm"] is None
      and bad["robot_need"]["ip_min"] is None, "말이 안 되는 값은 비움(추측 금지)", str(bad))
check(concept.plan_hint({})["robot_need"]["arm"] is True, "형식이 없으면 6축 팔 기본")

# ── 계획 상태·선택·확정 ────────────────────────────────────────────────────────
alt = {"id": "A", "name": "양팔 셀", "robot": "AMR 양팔형 1대", "structure": ["컨베이어 1대", "양팔 로봇이 선다"],
       "exclude": [], "plan": {"robot_need": {"arm": True}, "robot_candidates": [{"label": "DOBOT CR10A"}],
                               "robot_pick": "DOBOT CR10A", "gripper_ai": "석션", "gripper_candidates": ["석션"],
                               "gripper_pick": "석션", "confirmed": False}}
proj = {"stage": "concept", "alternatives": [alt, {"id": "B", "name": "옛 컨셉", "structure": ["x"]}]}
check(plan.pending(proj) and plan.alt_pending(alt) and not plan.alt_pending(proj["alternatives"][1]),
      "계획 없는 옛 컨셉은 확정된 것으로, 계획 있는 컨셉은 확인 중")
s1 = plan.select(alt, "레인보우 RB10-1300 특주", None)
check(s1["plan"]["robot_pick"] == "레인보우 RB10-1300 특주" and s1["plan"]["robot_custom"] and s1["plan"]["gripper_pick"] == "석션",
      "후보 밖 직접 입력 허용(표시), 안 준 칸은 그대로")
c1 = plan.confirm(alt)
c2 = plan.confirm(plan.select({**c1, "plan": {**c1["plan"], "confirmed": False}}, "DOBOT CR12A", None))
check(c1["plan"]["confirmed"] and c1["structure"][-1] == "선택 모델: 로봇 팔 DOBOT CR10A, 엔드툴 석션를 적용한다."
      and sum(s.startswith(plan.PICK_PREFIX) for s in c2["structure"]) == 1 and "CR12A" in c2["structure"][-1],
      "확정: 고른 모델을 구성 끝 한 줄로(다시 확정해도 한 줄만)", str(c2["structure"]))
check(not any(ch.isdigit() for ch in c1["structure"][-1].replace("CR10A", "")), "확정 문장에 사양 수치 없음(제안서 수치 검증과 충돌 방지)")
full = plan.confirm({**alt, "structure": [f"문장 {i}" for i in range(9)]})
check(len(full["structure"]) == concept.STRUCTURE_MAX and full["structure"][-1].startswith(plan.PICK_PREFIX),
      "구성이 꽉 차도 선택 모델 줄은 들어감")
val = concept.alternative_item_value(c1)
check("로봇 팔 DOBOT CR10A" in val and "그리퍼 석션" in val, "컨셉 문항 값에도 선택 모델", val)
rv, _note, _d = concept.validate_revision(
    {"structure": ["컨베이어 1대"], "robot_need": {"arm": True, "payload_kg": 16}, "gripper": "진공 석션", "note": "키움"},
    alt, {"items": {}, "request_text": ""}, "16kg급으로 키워 줘")
vq = concept.validate_quote({"lines": [{"group": "공통", "item": "외관 검사 비전 카메라"},
                                     {"group": "공통", "item": "설치 공간 기준 배치 검토"},
                                     {"group": "대안", "alt_id": "A", "item": "협동로봇 본체"}]}, {"A"})
check([x["group"] for x in vq] == ["공통", "통합·실증", "대안"],
      "견적 초안: 검토·검증 같은 활동은 장비 표가 아니라 통합·실증으로(M8)", str(vq))
check(plan.maker_intent("로봇 팔은 한화 제품 중에서 골라 줘") == (["한화로보틱스"], [])
      and plan.maker_intent("KUKA 는 빼고 DOBOT 으로") == (["DOBOT"], ["KUKA"]),
      "계획 수정 요청 속 제조사 지정·제외 알아보기(QA 3회차)", str(plan.maker_intent("KUKA 는 빼고 DOBOT 으로")))
mi = plan.apply_maker_intent({"plan": {"robot_need": {"arm": True, "exclude_makers": ["KUKA"]}}}, "한화로 골라 줘")
check(mi["plan"]["robot_need"]["makers"] == ["한화로보틱스"] and mi["plan"]["robot_need"]["exclude_makers"] == ["KUKA"],
      "지정 제조사 반영, 앞선 제외는 유지")
kept, _n, _d = concept.validate_revision({"structure": ["x"], "robot_need": {"payload_kg": 7}}, 
    {**alt, "plan": {**alt["plan"], "robot_need": {"arm": True, "makers": ["DOBOT"], "payload_kg": 5}}},
    {"items": {}, "request_text": ""}, "7kg 로")
check(kept["plan"]["robot_need"]["makers"] == ["DOBOT"] and kept["plan"]["robot_need"]["payload_kg"] == 7.0,
      "모델이 일부 칸만 돌려줘도 앞서 지정한 제조사 유지(칸별 합치기)", str(kept["plan"]["robot_need"]))
check(rv["plan"]["robot_need"]["payload_kg"] == 16.0 and rv["plan"]["gripper_ai"] == "진공 석션"
      and rv["plan"]["robot_pick"] == "DOBOT CR10A", "계획 수정 요청으로 요구 사양·그리퍼 추천 변경(선택은 유지)", str(rv["plan"]))


async def db_flow() -> None:
    a = await plan.attach({"id": "A", "structure": ["x"], "plan": {"robot_need": {"arm": True, "payload_kg": 10,
                                                                                  "reach_mm": 1300, "ip_min": 54}}}, [])
    c = a["plan"]["robot_candidates"]
    check(len(c) == plan.ROBOT_CANDIDATES and a["plan"]["robot_pick"] == c[0]["label"] and c[0]["label"].startswith("DOBOT")
          and all(x["payload_kg"] >= 10 and x["reach_mm"] >= 1300 * 0.9 and x["policy_note"] for x in c),
          "로봇 DB 후보 3개(K03: DOBOT 우선) + 첫 후보 기본 선택", json.dumps([x["line"] for x in c], ensure_ascii=False))
    m = await plan.attach({"id": "A", "robot": "AMR 양팔형 1대", "plan": {"robot_need": {"arm": True, "payload_kg": 5,
                                                                                         "reach_mm": 1000, "ip_min": 54}}}, [])
    check(m["plan"]["robot_need"]["mobile"] and all(x["maker"] != "KUKA" for x in m["plan"]["robot_candidates"])
          and m["plan"]["robot_candidates"][0]["label"] == "DOBOT CR5A",
          "AMR 양팔형(5kg·1,000mm·IP54): DOBOT CR5A 우선, KUKA 없음", str([x["label"] for x in m["plan"]["robot_candidates"]]))
    short = await plan.attach({"id": "A", "plan": {"robot_need": {"arm": True, "payload_kg": 25, "ip_min": 67}}}, [])
    check(short["plan"]["robot_candidates"] and all(x.get("ip_short") for x in short["plan"]["robot_candidates"]),
          "IP 까지 맞는 게 없으면 IP 조건을 풀고 표시", str([x["label"] for x in short["plan"]["robot_candidates"]]))
    delta = await plan.attach({"id": "A", "plan": {"robot_need": {"arm": False, "payload_kg": 3}}}, [])
    check(delta["plan"]["robot_candidates"] == [] and delta["plan"]["robot_pick"] == "", "6축 팔이 아니면 후보 없음")
    kept = await plan.attach(plan.select(a, "DOBOT 특주", None), [])
    check(kept["plan"]["robot_pick"] == "DOBOT 특주", "다시 후보를 골라도 사용자가 직접 입력한 선택은 유지")
    g = await plan.gripper_names()
    ga = await plan.attach({"id": "A", "plan": {"robot_need": {"arm": False}, "gripper_ai": "진공 석션"}}, g)
    check(g and ga["plan"]["gripper_candidates"][0] == "진공 석션" and ga["plan"]["gripper_pick"] == "진공 석션",
          "그리퍼 후보: AI 추천 + 회사 그리퍼 제품", str(ga["plan"]["gripper_candidates"][:4]))


if "--db" in sys.argv:
    asyncio.run(db_flow())

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
