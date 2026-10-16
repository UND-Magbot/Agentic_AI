"""제품 추천(툴체인저 ATC + AI 학습) API 검증 — cases/01_제품추천.md 의 API 케이스.

    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \
      -v ./backend/app:/app/app -v ./test_cases:/tc backend python /tc/runners/api/recommend.py

선정 규칙(evaluate)은 AI 없이 코드로 판정한다. 대화·학습 초안은 실제 사내 AI 를 부른다.
"""
from __future__ import annotations

import copy
import json
import re
import sys
import time

from sqlalchemy import text

sys.path.insert(0, "/tc/runners")
import tc  # noqa: E402

from app.database import SessionLocal  # noqa: E402

P = "/v1/product-recommend"
RULE_CODE = re.compile(r"\b(PGR|JF|[QRTCFUJAPOGSE])\d{2}\b")
BASE = {
    "project": {"process": {"category": "transfer"}, "tool_count": 1, "environment": {"has_special_conditions": False},
                "customer_name": f"{tc.MARK}고객"},
    "robots": [{"id": "R1", "model": "M1013", "type": "cobot", "quantity": 1, "speed": {"value": 50, "unit": "%"},
                "cables": {"master_4m_sufficient": True, "system_1m_sufficient": True}, "manufacturer": "두산로보틱스"}],
    "tools": [{"id": "T1", "name": f"{tc.MARK}그리퍼", "robot_ids": ["R1"], "installed_quantity": 1, "workpiece_count": 1,
               "workpiece_not_applicable": False,
               "intake": {"dimensions_mm": {"x": 200.0, "y": 100.0, "z": 100.0}, "tool_assembly_mass_kg": 3.0, "tilts_or_flips": False,
                          "needs_power": False, "needs_air": False, "has_sensors_or_signals": False},
               "mass_components": {"max_simultaneous_workpieces_kg": 2.0}}],
}


def intake(**edit) -> dict:
    d = copy.deepcopy(BASE)
    for path, v in edit.items():          # 'tools.0.intake.tool_assembly_mass_kg' 꼴
        cur = d
        keys = path.split(".")
        for k in keys[:-1]:
            cur = cur[int(k)] if k.isdigit() else cur.setdefault(k, {})
        cur[keys[-1]] = v
    return d


def first(ev: dict) -> dict:
    prim = next((c for c in ev["screening_candidates"] if c.get("primary")), ev["screening_candidates"][0])
    return prim["models"][0] if prim["models"] else {}


def visible_text(obj) -> str:
    """화면에 나갈 만한 글 — 규칙 번호를 담는 칸(rules·provisional·rule_trace·rule·id·ref)은 화면에 안 쓰므로 제외."""
    if isinstance(obj, dict):
        return " ".join(visible_text(v) for k, v in obj.items()
                        if k not in ("rules", "provisional", "rule_trace", "rule", "rule_id", "id", "ref"))
    if isinstance(obj, list):
        return " ".join(visible_text(v) for v in obj)
    return obj if isinstance(obj, str) else ""


async def body(s: tc.Suite) -> None:
    async def ev(it: dict, user: str = "lucas"):
        return await s.call("POST", f"{P}/atc/evaluate", user, json={"intake": it})

    # ── 선정 규칙 ──
    async def rec001():
        e = (await ev(intake())).json()
        m = first(e)
        return m.get("payload_kg", 0) >= 5 and e["screening_payload_kg"] == 5 and e["industrial_factor"] is None, \
            f"1순위 {m.get('name')} {m.get('payload_kg')}kg, 선정 하중 {e['screening_payload_kg']}, 산업용 배수 {e['industrial_factor']}"
    await s.case("REC-001", "협동로봇 기본 선정", rec001)

    async def rec002():
        e = (await ev(intake(**{"robots.0.type": "industrial"}))).json()
        return e["screening_payload_kg"] == 10 and e["industrial_factor"] == 2, \
            f"산업용 → 선정 하중 {e['screening_payload_kg']}, 배수 {e['industrial_factor']}, 1순위 {first(e).get('name')}"
    await s.case("REC-002", "산업용 로봇 ×2", rec002)

    async def rec004():
        out = {}
        for v in (70, 70.1, 71):
            e = (await ev(intake(**{"robots.0.speed": {"value": v, "unit": "%"}}))).json()
            out[v] = (e["speed_review"]["compare_upper"], first(e).get("name"))
        ok = out[70][0] is False and out[70.1][0] is True and out[71][0] is True and len({m for _, m in out.values()}) == 1
        return ok, f"속도별 (상위 비교, 1순위) {out}"
    await s.case("REC-004", "협동로봇 속도 70% 경계", rec004)

    async def rec006():
        e = (await ev(intake(**{"tools.0.intake.tool_assembly_mass_kg": 33.0}))).json()
        auto = next((c for c in e["screening_candidates"] if c["series"] == "auto"), {})
        return "nonstandard_or_out_of_range" in [x["status"] for x in e["statuses"]] or auto.get("out_of_range"), \
            f"툴측 35kg → 상태 {[x['status'] for x in e['statuses']]}, 자동 계열 out_of_range={auto.get('out_of_range')} 모델 {[m['name'] for m in auto.get('models', [])]}"
    await s.case("REC-006", "정격 범위 밖 → 검토", rec006)

    async def rec007():
        e = (await ev(intake())).json()
        names = [m["name"] for c in e["screening_candidates"] for m in c["models"]] + [a["name"] for a in e["alternatives"]]
        return not any("TCW" in n for n in names), f"후보·대안 {names}"
    await s.case("REC-007", "무선(TCW1)은 따로 고를 때만", rec007)

    async def rec008():
        res = {}
        for lab, pr in (("1~5", {"min": 1, "max": 5}), ("5~6", {"min": 5, "max": 6}), ("0.5~4", {"min": 0.5, "max": 4})):
            it = intake(**{"tools.0.intake.needs_air": True, "tools.0.pneumatic": {"medium": "압축공기", "pressure_bar": pr,
                                                                                   "observed_hose_count": 1, "required_independent_paths": 1}})
            e = (await ev(it)).json()
            res[lab] = any("압력" in r or "bar" in r for r in visible_text(e["engineering_review_reasons"]).split("|")) or \
                "bar" in visible_text(e["engineering_review_reasons"])
        return res["1~5"] is False and res["5~6"] and res["0.5~4"], f"압력 범위별 엔지니어 검토 여부 {res}"
    await s.case("REC-008", "공압 압력 표준 범위", rec008)

    async def rec009():
        it = intake(**{"tools.0.intake.needs_air": True, "tools.0.pneumatic": {"medium": "압축공기", "pressure_bar": {"min": 6, "max": 4},
                                                                               "observed_hose_count": 1, "required_independent_paths": 1}})
        e = (await ev(it)).json()
        flagged = "bar" in visible_text(e["engineering_review_reasons"])
        return flagged, f"뒤바뀐 범위 6~4bar → 검토 사유 {e['engineering_review_reasons']}"
    await s.case("REC-009", "뒤바뀐 압력 범위(6~4bar)", rec009)

    async def rec012():
        bads = {"무게 '3kg'": intake(**{"tools.0.intake.tool_assembly_mass_kg": "3kg"}),
                "수량 '2대'": intake(**{"robots.0.quantity": "2대"}),
                "system_request true": intake(**{"project.system_request": True})}
        got = {k: (await ev(v)).status_code for k, v in bads.items()}
        return all(c != 500 for c in got.values()), f"상태 {got} (500 이면 입력 검사 없이 계산하다 오류)"
    await s.case("REC-012", "잘못된 형식 입력 → 500 아님", rec012)

    async def rec013():
        it = intake(**{"tools.0.intake.needs_power": True, "tools.0.intake.has_sensors_or_signals": True,
                       "tools.0.electrical": {"power_circuits": ["24V 1회로 1A"], "extra_pins": 0, "dio": {"di": 200000, "do": 0}}})
        t = time.time()
        r = await ev(it)
        sec = round(time.time() - t, 1)
        mods = r.json().get("pogo_modules") if r.status_code == 200 else None
        return r.status_code == 400, f"DI 20만 개 → {r.status_code} {sec}s, 포고 모듈 {mods} (상한 없이 계산하면 더 큰 값에 서버 메모리 위험)"
    await s.case("REC-013", "비현실적으로 큰 신호 수 거절", rec013)

    async def rec014():
        got = {}
        for v in (-5.0, 0.0):
            r = await ev(intake(**{"tools.0.intake.tool_assembly_mass_kg": v, "mass_components": {}}))
            e = r.json() if r.status_code == 200 else {}
            got[v] = (r.status_code, e.get("required_payload_kg"), first(e).get("name") if e else None)
        neg = got[-5.0]
        return neg[0] == 400 or (neg[1] is not None and neg[1] > 0), f"(상태, 선정 하중, 1순위) {got}"
    await s.case("REC-014", "음수·0 무게", rec014)

    # ── 대화 답 읽기(코드 판독 경로) ──
    async def answer(field: dict, txt: str):
        return await s.call("POST", f"{P}/atc/answer", "lucas", json={"intake": BASE, "question": {"id": "TC", "fields": [field]}, "text": txt})

    async def rec015():
        cases = [({"path": "tools[].intake.tool_assembly_mass_kg", "type": "number", "label_ko": "툴 무게", "unit": "kg"}, "800g", 0.8),
                 ({"path": "tools[].electrical.current_A", "type": "number", "label_ko": "전류", "unit": "A"}, "500mA", 0.5),
                 ({"path": "tools[].intake.dimensions_mm.x", "type": "number", "label_ko": "가로", "unit": "mm"}, "30cm", 300)]
        got = []
        for f, txt, want in cases:
            j = (await answer(f, txt)).json()
            v = (j.get("values") or {}).get(f["path"])
            got.append((txt, v, want))
        bad = [g for g in got if g[1] is not None and g[1] != g[2]]
        return not bad, f"(입력, 저장된 값, 기대) {got} — 단위를 무시하고 숫자만 넣으면 결함"
    await s.case("REC-015", "단위 다른 숫자 답(800g·500mA·30cm)", rec015)

    async def rec016():
        f = {"path": "robots[].cables.master_4m_sufficient", "type": "boolean", "label_ko": "4m 케이블 충분"}
        got = []
        for txt, want in (("충분하지 않아요", False), ("네, 없습니다", False), ("아니요, 있어요", True)):
            j = (await answer(f, txt)).json()
            got.append((txt, (j.get("values") or {}).get(f["path"]), want))
        return all(g[1] == g[2] or g[1] is None for g in got), f"(답, 읽은 값, 기대) {got}"
    await s.case("REC-016", "있음/없음 문장 판독", rec016)

    async def rec017():
        f = {"path": "tools[].intake.tool_assembly_mass_kg", "type": "number", "label_ko": "툴 무게", "unit": "kg"}
        j = (await answer(f, "3,5kg")).json()
        v = (j.get("values") or {}).get(f["path"])
        return v != 35, f"'3,5kg' → {v} (35 로 저장되면 결함)"
    await s.case("REC-017", "소수 쉼표(3,5kg)", rec017)

    async def rec018():
        f = {"path": "robots[].cables.master_to_controller_required_m", "type": "number", "label_ko": "케이블 길이", "unit": "m"}
        j = (await answer(f, "총 6m 필요해요, 추가 비용은 문의")).json()
        v = (j.get("values") or {}).get(f["path"])
        return v in (6, 6.0, None), f"'총 6m … 추가 비용' → {v} (10 이면 '추가'를 연장으로 오인)"
    await s.case("REC-018", "케이블 '총 6m'를 연장으로 오인하지 않음", rec018)

    # ── 일반 추천·입력 ──
    async def rec021():
        r1 = await s.call("POST", P, "lucas", json={"request": "   ", "mode": "recommend"})
        return r1.status_code == 400, f"공백 요청 → {r1.status_code} {r1.text[:80]}"
    await s.case("REC-021", "빈 요청 거절", rec021)

    # ── 추천 기록·확정·권한 ──
    rec = await s.call("POST", f"{P}/atc/recommend", "lucas", json={"intake": BASE})
    rid = rec.json()["recommendation"]["id"] if rec.status_code == 200 else None

    async def rec027():
        if rid is None:
            return False, f"추천 실패 {rec.status_code} {rec.text[:120]}"
        body_ = rec.json()
        vt = visible_text(body_)
        codes = sorted(set(m.group(0) for m in RULE_CODE.finditer(vt)))
        leak = tc.leaked(vt)
        return not codes and not leak, f"화면 글에 규칙 번호 {codes[:8]} / 모델명 {leak}"
    await s.case("REC-027", "추천 결과 화면 글에 규칙 번호·모델명 없음", rec027)

    async def rec036():
        if rid is None:
            return ("SKIP", "추천 실패")
        r1 = await s.call("POST", P, "mason", json={"request": f"{tc.MARK} 왜?", "mode": "chat", "recommendation_id": rid})
        r2 = await s.call("POST", f"{P}/recommendations/{rid}/finalize", "mason", json={"history": [], "memo": ""})
        return r1.status_code == 400 and r2.status_code in (400, 404), f"남의 추천에 대화 {r1.status_code} / 확정 {r2.status_code}"
    await s.case("REC-036", "남의 추천 대화·확정 막힘", rec036)

    async def rec033():
        if rid is None:
            return ("SKIP", "추천 실패")
        a = await s.call("POST", f"{P}/recommendations/{rid}/finalize", "lucas", json={"history": [], "memo": tc.MARK})
        b = await s.call("POST", f"{P}/recommendations/{rid}/finalize", "lucas", json={"history": [], "memo": tc.MARK})
        async with SessionLocal() as db:
            n_prop = (await db.execute(text("SELECT count(*) FROM product_proposals WHERE recommendation_id = :r"), {"r": rid})).scalar()
            n_deal = (await db.execute(text("SELECT count(*) FROM sales_deals WHERE proposal_id IN "
                                            "(SELECT id FROM product_proposals WHERE recommendation_id = :r)"), {"r": rid})).scalar()
        return a.status_code in (200, 201) and n_prop == 1 and n_deal <= 1, \
            f"확정 두 번 {a.status_code}/{b.status_code} → 확정 제안 {n_prop}건, 영업 건 {n_deal}건"
    await s.case("REC-033", "최종 제안 확정 두 번 → 한 건", rec033)

    async def rec037():
        async with SessionLocal() as db:
            pid = (await db.execute(text("SELECT id FROM product_proposals WHERE recommendation_id = :r"), {"r": rid})).scalar() if rid else None
        if pid is None:
            return ("SKIP", "확정 제안 없음")
        got = {u: (await s.call("GET", f"{P}/proposals/{pid}", u)).status_code for u in ("mason", "finance_admin")}
        return all(c in (403, 404) for c in got.values()), f"lucas 의 확정 제안(고객사·미팅·대화) 열람 {got}"
    await s.case("REC-037", "남의 확정 제안 열람 막힘", rec037)

    async def rec038():
        got = {"추천": (await s.call("POST", f"{P}/atc/recommend", "finance_admin", json={"intake": BASE})).status_code,
               "학습 목록": (await s.call("GET", f"{P}/corrections", "finance_admin")).status_code}
        return ("CHECK", f"재무 관리자 접근 {got} (영업 전용이어야 하는지 정책 확인)")
    await s.case("REC-038", "재무 관리자의 제품 추천 사용", rec038)

    # ── 학습(정정) 권한 ──
    draft = {"kind": "correction", "situation": f"{tc.MARK} 철판 25kg 이상", "wrong_name": "MG10", "right_name": "MG30",
             "reason": f"{tc.MARK} 하중 여유", "rule": "철판 25kg 이상은 MG30 우선 검토", "note": tc.MARK}

    async def rec039():
        r = await s.call("POST", f"{P}/corrections", "mason", json={"draft": draft})
        j = r.json()
        return r.status_code == 201 and j.get("review_status") == "pending" and j.get("active") is False, \
            f"{r.status_code} 상태 {j.get('review_status')} 반영 {j.get('active')} {str(j)[:120] if r.status_code != 201 else ''}"
    await s.case("REC-039", "멤버 학습은 승인 대기", rec039)

    async def rec040():
        async with SessionLocal() as db:
            cid = (await db.execute(text("SELECT id FROM product_corrections WHERE note = :m ORDER BY id DESC LIMIT 1"), {"m": tc.MARK})).scalar()
        if cid is None:
            return ("SKIP", "대기 학습 없음")
        r1 = await s.call("POST", f"{P}/corrections/{cid}/review", "mason", json={"action": "approve"})
        r2 = await s.call("POST", f"{P}/corrections/{cid}/review", "alex", json={"action": "approve"})
        return r1.status_code == 403 and r2.status_code == 200, f"멤버 승인 {r1.status_code} / 관리자 승인 {r2.status_code}"
    await s.case("REC-040", "학습 승인은 영업 관리자만", rec040)

    async def rec041():
        async with SessionLocal() as db:
            cid = (await db.execute(text("SELECT id FROM product_corrections WHERE note = :m ORDER BY id DESC LIMIT 1"), {"m": tc.MARK})).scalar()
        r = await s.call("POST", f"{P}/corrections/{cid}/review", "superadmin", json={"action": "off"})
        return r.status_code == 200, f"최고 관리자 검토 {r.status_code}"
    await s.case("REC-041", "최고 관리자도 학습 검토 가능", rec041)

    async def rec042():
        rs = [await s.call("POST", f"{P}/corrections", "mason", json={"draft": {**draft, "right_card_id": "abc"}}),
              await s.call("POST", f"{P}/corrections", "mason", json={"draft": {**draft, "right_card_id": 999999}})]
        return all(r.status_code != 500 for r in rs), f"카드 id 'abc' / 없는 999999 → {[r.status_code for r in rs]}"
    await s.case("REC-042", "학습 저장 잘못된 카드 id", rec042)

    async def rec043():
        rs = {"review": await s.call("POST", f"{P}/corrections/999999/review", "alex", json={"action": "approve"}),
              "finalize": await s.call("POST", f"{P}/recommendations/999999/finalize", "lucas", json={"history": []}),
              "proposal": await s.call("GET", f"{P}/proposals/999999", "lucas"),
              "sample": await s.call("GET", f"{P}/atc/test-samples/zzz", "lucas")}
        got = {k: r.status_code for k, r in rs.items()}
        return all(c in (400, 404) for c in got.values()), f"{got}"
    await s.case("REC-043", "없는 id 요청", rec043)

    async def rec048():
        r = await s.call("GET", f"{P}/atc/test-samples", "lucas")
        return ("CHECK", f"일반 영업 사용자에게 정답지(테스트 샘플) {len(r.json().get('items', []))}건 노출 — 테스트 메뉴 노출 정책 확인")
    await s.case("REC-048", "테스트용 정답지 노출", rec048)

    for cid in ("REC-023", "REC-024", "REC-025", "REC-026"):
        s.skip(cid, "AI 답 고정 필요", "AI 응답을 바꿔 넣는 장치(stub)가 아직 없음 — 다음 단계")


if __name__ == "__main__":
    tc.main("recommend", body)
