"""제안 두뇌 비교 — 같은 입력(삼성웰스토리 가이드 예시)으로 gemma 와 GPT 의 공정·대안 제안을 나란히.

자료 읽기(추출)는 gemma 로 한 번만 하고, 같은 문항 값으로 두 두뇌에 각각 N회 제안을 받는다.
결과는 docs/proposal_brain_compare/ 에 JSON + Markdown 으로 남긴다. 시험 프로젝트는 끝나면 지운다.
    docker compose run --rm --no-deps -v ./scripts:/scripts -v ./docs:/docs backend python /scripts/compare_proposal_brains.py
"""
import asyncio
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
for cand in (Path(__file__).resolve().parents[1] / "backend", Path("/app")):
    if (cand / "app").is_dir():
        sys.path.insert(0, str(cand))
        break

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.proposal_project import concept, dialog, service  # noqa: E402

RUNS = 2
OUT = Path("/docs/proposal_brain_compare") if Path("/docs").is_dir() else Path("docs/proposal_brain_compare")

TITLE = "삼성웰스토리 식기세척 후단 자동화 실증 제안"
REQUEST = """삼성웰스토리 InnovationLab입니다. 식기세척 후단 자동화 실증을 검토하고 있어 제안을 요청드립니다.
양산설비를 바로 확정하려는 것이 아니라, 세척 후 취출·이송·인식·분류·버퍼·적재의 자동화 가능성과 실증할 모듈, 예상 공간 및 비용 범위를 판단하려고 합니다.
기준은 300식/끼, 전체 식기 약 3,000개/끼입니다. 소형 식기 약 20%는 별도 처리하고, 나머지 약 2,400개를 로봇으로 2시간 이내 처리하는 것이 목표입니다.
식기는 약 8종이며 세척 직후 물기가 있는 상태입니다. 종류별로 카세트에 적재하고, 만재 카세트는 작업자가 외측으로 반출한 뒤 빈 카세트를 넣는 방식을 생각하고 있습니다.
사업장이 매우 협소해 최소형·컴팩트 구성이 중요합니다. 세 가지 컨셉을 이미지로 비교해 보고 싶습니다."""
INTAKE = {
    "project": "고객사: 삼성웰스토리 / 부서: InnovationLab / 프로젝트명: 식기세척 후단 자동화 실증\n의사결정 목적: 양산설비 확정이 아니라 자동화 가능성·실증 모듈·예상 공간과 비용 범위 판단\n우선순위: 공간(최소형·컴팩트). ROI가 기대되면 최소한의 공간 확장도 검토 가능",
    "target": "대상물: 세척 직후 물기가 있는 식기, 약 8종\n미확인: 정확한 품명·사진·치수·재질·무게·종류별 비율, 소형 식기가 8종에 포함되는지, 온도·겹침·잔수 상태",
    "process": "현재: 세척된 식기를 작업자가 종류별로 분류하고 보관·반출함 (정확한 인원·작업 시간·병목은 현장 영상으로 확인 필요)\n자동화 시작: 기존 컨베이어 방식 터널 식기세척기(핑거 타입 배출부)의 배출 이후\n자동화 종료: 종류별 카세트 적재 후 만재 카세트 외측 반출까지\n제외: 식기세척기 본체 교체는 공급 범위 아님. 소형 식기 약 20%는 별도 분류 또는 Sorter로 처리\n필요 시 핑거 타입을 플랫 타입으로 변환하고 버퍼·속도 조정 구간을 둠",
    "volume": "300식/끼, 식기 약 3,000개/끼\n소형 약 20% 제외한 약 2,400개를 로봇으로 2시간 이내 처리 (시간당 1,200개 이상)\n미확인: 혼합비율, 허용 실패·파손 수준",
    "robot": "대안 3가지: 휴머노이드형, AMR 양팔형, 상부 레일 델타형 (같은 공정 조건을 처리하는 대안으로 각 1장씩)\n휴머노이드: RM AIDAL 이미지의 몸체와 양팔 형태\nAMR 양팔: 공통 몸체 1개에 DOBOT 10kg급 팔 2대, 하부 AMR 1대, 머리 없음 (ABB 이미지는 형태만 참고)\n상부 레일 델타: 델타 로봇 1대의 본체 전체가 컨베이어 상부 레일·캐리지를 따라 이동\n유엔디 취급 제품을 우선 검토",
    "vision": "세척 전 비전: 식기 종류와 유입량을 미리 파악해 후단 분류·적재 준비에 활용\n세척 후 비전: 로봇이 집기 전 실제 취출 위치와 자세를 최종 확인 (물기·반사 대응 검토)\n카메라 외형: 첨부한 가로형 다중 렌즈 카메라로 전후단 통일, 모델·성능 미정\n그리퍼: 유엔디 MAGBOT 2F·3F·석션 우선 검토, 양팔은 한쪽 핑거·한쪽 석션처럼 다르게 구성 가능\n툴체인저: 선택 옵션\n미확인: 통과 중 식기 순서·간격 유지 여부, 접촉 허용 부위, 파손·흠집 허용 기준",
    "storage": "목적지: 약 8종을 A~H 8개 적재 위치로 구분 (양팔형은 좌우 각 4종, 델타형은 4개씩 두 줄)\n적재 방식: 승강 리프트 받침대를 적재량에 맞춰 내려 높이 관리 (식기별 최대 높이·수량 상한은 미정)\n만재 처리: 만재 카세트는 작업자가 외측으로 반출하고 빈 카세트를 외측에서 투입\n턴테이블: 필요 시 옵션 검토\n후면 진입·도킹 표현은 사용하지 않음",
    "exception": "로봇이 놓친 식기: 끝단 전에 센서로 감지하고 후단 컨베이어를 일시 정지 (세 타입 공통)\n정지 중: 후단 버퍼로 배출을 일시 수용, 버퍼 한계 시 사전에 정한 수동 전환 절차와 연계\n미확인: 재취출 우선인지 수동 제거인지, 재개 승인 방식, 미인식·겹침·파손·파지 실패·카세트 없음/만재 처리 순서",
    "site": "검토 사업장 유형: 600식 헬씨랩, 1,000식 일반, 1,500식 전자군, 3,000식 전자군 (실제 도면 치수는 미확인)\n소규모 사업장은 매우 협소함. 작업자 투입·카세트 교체·예외 대응이 가능한 구조 필요\n습기 대응: 로봇 보호 커버, 적합한 보호등급 검토, 카메라 방적·결로 대응, 내식 재질, 드립 트레이·배수, 청소 가능한 구조\n연동: 세척기·컨베이어·버퍼·비전·로봇·승강 적재부 통합 제어 (PLC·통신 방식은 미확인)\n미확정 IP등급, 파지 성능, 설치 치수는 만들어 넣지 말 것",
    "supply": "구성: 공통 이송·인식, 적재·배출, 로봇 대안 중 1종, 통합·실증으로 나눔\n옵션: 툴체인저, 턴테이블, 소형 Sorter\n현장 적용비는 별도. 현재 금액은 별도 협의 (가격 근거 없음)\n실증: 식기 인식·파지와 승강 적재·카세트 교체의 단위 실증부터 시작, 이후 8종 통합 운영 검토\n참고: 고객 예시 기준 1명 8시간 연간 약 4,500만원, ROI 판단은 고객이 직접 계산",
    "output": "10쪽 이내: 1 표지, 2 개요, 3 공정도, 4 공통 컨셉, 5 휴머노이드, 6 양팔, 7 상부 레일 델타, 8 주요 장비, 9 실증·현장 적용, 10 견적 구성\n컨셉도: 흰 배경 사선 시점, 세 타입 톤 통일, 그림을 크게, 주석은 짧게\n순서: 5·6·7쪽 컨셉도를 먼저 확정한 뒤 구성 확인, 그다음 제작\n최종 형식: PPT (공정도·텍스트·장비 표는 수정 가능하게)",
}


def md_run(tag: str, r: dict) -> list[str]:
    out = [f"### {tag} — {r['seconds']:.0f}초 · 대안 {len(r['alternatives'])}개 · 설계 문항 {len(r['fills'])}개"
           + (f" · 근거 없는 수치로 뺀 문장 {len(r['dropped'])}개" if r["dropped"] else "")
           + (f" · ⚠ 실제 응답: {r['used']} ({r['fallback_reason'][:80]})" if r["fallback_reason"] else ""), ""]
    for a in r["alternatives"]:
        out += [f"**대안 {a['id']} · {a['name']}** — {a['robot']}", f"- 요약: {a['summary']}", f"- 이유: {a['reason']}",
                "- 구성:"] + [f"  - {s}" for s in a["structure"]]
        if a["exclude"]:
            out += ["- 넣지 않을 것: " + " / ".join(a["exclude"])]
        out.append("")
    if r["fills"]:
        out += ["**설계 문항 제안**", ""] + [f"- {f['code']}: {f['value']}  _(이유: {f['reason']})_" for f in r["fills"]] + [""]
    if r["dropped"]:
        out += ["<details><summary>뺀 문장</summary>", ""] + [f"- {d}" for d in r["dropped"]] + ["", "</details>", ""]
    return out


async def main() -> None:
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username='alex'"))).scalar_one()
        pid = await service.create_project(db, user_id=uid, title=TITLE, request_text=REQUEST, intake=INTAKE, assets=[])
    try:
        t = time.time()
        await dialog.run_reading(pid)
        async with SessionLocal() as db:
            project = await service.get_project(db, pid)
        filled = {c: v for c, v in project["items"].items() if v["status"] != "empty"}
        print(f"자료 읽기(gemma) {time.time() - t:.0f}초, 문항 {len(filled)}/48 채움", flush=True)
        results: dict[str, list[dict]] = {"gemma": [], "gpt": []}
        for n in range(RUNS):
            for brain in ("gemma", "gpt"):
                t = time.time()
                r = await concept.propose_with_brain(project, brain=brain, user_id=uid)
                r["seconds"] = time.time() - t
                results[brain].append(r)
                print(f"{brain} #{n + 1}: {r['seconds']:.0f}초, 대안 {[a['name'] for a in r['alternatives']]}, "
                      f"채움 {len(r['fills'])}, 뺌 {len(r['dropped'])}, used={r['used']} {r['fallback_reason'][:80]}",
                      flush=True)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "result.json").write_text(json.dumps({"items": filled, "results": results}, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        md = [f"# 제안 두뇌 비교 — {TITLE}", "",
              f"입력: 가이드 삼성웰스토리 예시(요청 원문 + 공통 질문 11칸). 자료 읽기는 gemma 1회 → 문항 {len(filled)}/48.",
              "같은 문항 값으로 두 두뇌에 각 %d회 제안. 검증(근거 없는 수치 제거·설계 문항만 채움)은 동일 코드." % RUNS, ""]
        for brain, label in (("gemma", "사내 gemma4:12b"), ("gpt", "GPT (Codex 브리지)")):
            md += [f"## {label}", ""]
            for n, r in enumerate(results[brain], 1):
                md += md_run(f"{label} {n}회차", r)
        (OUT / "compare.md").write_text("\n".join(md), encoding="utf-8")
        print(f"저장: {OUT / 'compare.md'}", flush=True)
    finally:
        async with SessionLocal() as db:
            await db.execute(text("DELETE FROM proposal_projects WHERE id = :p"), {"p": pid})
            await db.commit()


asyncio.run(main())
