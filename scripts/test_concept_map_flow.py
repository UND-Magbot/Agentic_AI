"""공정 개념도 원클릭 흐름 검증 — 의도 감지 / 서비스 / 챗 스트림. 서버·LLM·MinIO 불필요.

DRAWIO_EXE 환경변수가 있으면 실제 draw.io 로 PNG 내보내기까지 확인한다.
"""
import asyncio
import io
import json
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import main  # noqa: E402
from app.diagram import checklist, concept_service as cs, drawio  # noqa: E402
from app.diagram.spec import ConceptMapSpec  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


SYS_WITH = "- 도구가 attachment_ids 인자를 받으면 **위 1개 ID 전부를 빠짐없이** 위 순서 그대로 전달: [42]."
user = lambda t: [{"role": "user", "content": t}]  # noqa: E731

# ── 1) 의도 감지 ────────────────────────────────────────────────────────────
check(main._detect_concept_map_intent(user("공정 개념도 작성 기능 수행"), SYS_WITH), "표준 프롬프트+첨부 → 감지")
check(not main._detect_concept_map_intent(user("공정 개념도 작성 기능 수행"), ""), "첨부 없으면 미감지")
check(not main._detect_concept_map_intent(user("개념도가 뭐야? 설명해줘"), SYS_WITH), "정보 질문은 미감지")
check(not main._detect_concept_map_intent(user("영수증 대조·검증 기능 수행"), SYS_WITH), "다른 기능 문구 미감지")
check(not main._detect_expense_reconcile_intent(user("공정 개념도 작성 기능 수행"), SYS_WITH),
      "영수증 대조 fast-path 가 가로채지 않음")
check(not main._detect_fund_plan_intent(user("공정 개념도 작성 기능 수행"), SYS_WITH),
      "자금계획 fast-path 가 가로채지 않음")

# ── 2) 서비스 (LLM·저장 대체) ───────────────────────────────────────────────
SPEC = ConceptMapSpec.model_validate(
    json.loads((ROOT / "docs/concept_drawio/chem_spec.json").read_text(encoding="utf-8")))
saved: list[tuple[str, int, str]] = []
stages: list[str] = []


async def fake_fetch(ids):
    return "한화로봇 14kg 약액 주입 210초 요청", 7


async def fake_checked(request, *, on_stage=None, **kw):
    for s in ("extract", "design", "verify"):
        on_stage and on_stage(s)
    items = [checklist.CheckItem(id="c1", kind="fact", text="약액 주입 210초", evidence=""),
             checklist.CheckItem(id="c2", kind="question", text="Class 100", evidence="")]
    items[0].covered = True
    return SPEC, [], checklist.ChecklistReport(items=items)


async def fake_save(owner_id, filename, data, mime):
    saved.append((filename, owner_id, mime))
    return cs.ResultFile(filename, f"/api/attachments/{100 + len(saved)}/download")


cs._fetch_request = fake_fetch
cs.checklist.generate_checked = fake_checked
cs._save = fake_save
exe = os.environ.get("DRAWIO_EXE", "")
real_find = drawio.find_exe
cs.drawio.find_exe = lambda configured="": real_find(exe) if exe else None

res = asyncio.run(cs.build_concept_map(attachment_ids=[42], on_stage=stages.append))
check(stages == ["fetch", "extract", "design", "verify", "draw", "upload"], "단계 순서", str(stages))
check(all(o == 7 for _, o, _ in saved), "결과 소유자 = 요청 첨부 소유자")
check(any(f.endswith(".drawio") for f, _, _ in saved), ".drawio 저장")
check((res.covered, res.total, res.missing) == (1, 2, ["Class 100"]), "반영/누락 집계",
      str((res.covered, res.total, res.missing)))
check("Class 100 대응" in res.pending and "로봇/캐핑" in res.pending, "확인 필요 항목 수집", str(res.pending))
if exe:
    check(any(f.endswith(".png") and m == "image/png" for f, _, m in saved), "실제 draw.io PNG 저장")
    check(res.png_available, "PNG 생성 표시")
else:
    check(not res.png_available and "PNG 는 만들지 못했습니다" in res.summary, "draw.io 없으면 안내")
check(all(ch not in f for f, _, _ in saved for ch in r'/\:*?"<>| '), "파일명 안전 문자")

# PNG 변환이 멈추거나 실패해도 .drawio 는 전달 (2026-09-28 draw.io 180초 멈춤 실측)
saved.clear()
real_export, real_find2 = cs._export_png, cs.drawio.find_exe


def hang_export(xml, exe):
    raise TimeoutError("draw.io export timeout")


cs._export_png, cs.drawio.find_exe = hang_export, (lambda configured="": "drawio")
res2 = asyncio.run(cs.build_concept_map(attachment_ids=[42]))
cs._export_png, cs.drawio.find_exe = real_export, real_find2
check(any(f.endswith(".drawio") for f, _, _ in saved) and not any(f.endswith(".png") for f, _, _ in saved),
      "PNG 실패해도 .drawio 저장")
check(res2.png_error and "PNG 변환에 실패했습니다" in res2.summary, "PNG 실패 안내")

# ── 3) 챗 스트림 ────────────────────────────────────────────────────────────


async def fake_build(*, attachment_ids, on_stage):
    for s in ("fetch", "extract", "design"):
        on_stage(s)
    return cs.ConceptMapResult(title="T", covered=1, total=1, missing=[], pending=[],
                               files=[cs.ResultFile("a.png", "/api/attachments/5/download"),
                                      cs.ResultFile("a.drawio", "/api/attachments/6/download")])


async def fake_fail(*, attachment_ids, on_stage):
    on_stage("fetch")
    raise ValueError("개념도 요청 내용이 비어 있습니다.")


async def collect(fn):
    cs.build_concept_map = fn
    return "".join([c async for c in main._stream_concept_map(attachment_ids=[42])])


out = asyncio.run(collect(fake_build))
check("[a.png](/api/attachments/5/download)" in out and "[a.drawio](/api/attachments/6/download)" in out,
      "결과 링크 2개")
last = out.rsplit(main.PROGRESS_SENTINEL, 2)[-2]
check(all(s["state"] == "done" for s in json.loads(last)["steps"]), "완료 시 전 단계 done")


async def fake_build_rec(*, attachment_ids, on_stage):
    r = await fake_build(attachment_ids=attachment_ids, on_stage=on_stage)
    r.record_id, r.references = 77, ["대선주조 — 소주 병 공정"]
    return r


out_rec = asyncio.run(collect(fake_build_rec))
check("[이 결과를 확정](/api/proposal-records/77/confirm)" in out_rec, "결과 끝에 확정 카드 마커")
check(out_rec.index("a.drawio") < out_rec.index("이 결과를 확정"), "확정 카드는 다운로드 뒤")
check("참고한 유사 공정 컨설팅 사례: 대선주조" in out_rec, "참고 사례 요약 표시")
check("proposal-records" not in out, "초안 기록이 없으면 확정 카드 없음")
out = asyncio.run(collect(fake_fail))
check("⚠ 개념도 작성 실패: 개념도 요청 내용이 비어 있습니다." in out, "ValueError 는 사용자 메시지로")
last = json.loads(out.rsplit(main.PROGRESS_SENTINEL, 2)[-2])
check(any(s["state"] == "error" for s in last["steps"]), "실패 단계 error 표시")

# ── 4) 참고 자료: 관련성 판정(case_guard)을 통과한 사례만 context 로 ─────────
from types import SimpleNamespace  # noqa: E402

from app import casebook, confirmed  # noqa: E402
from app.proposal import case_guard  # noqa: E402


class _FakeDB:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


hits = [SimpleNamespace(ref=r, title=t) for r, t in
        [("2024-19", "임플란트 드릴"), ("2023-01", "담금주 생산공정"), ("2024-11", "보호계전기 볼팅")]]
seen_blocks: list[list[str]] = []


async def fake_search(db, q, *, top_cases=3):
    return hits[:top_cases]


async def fake_judge_keep_one(request, hs):
    return [SimpleNamespace(hit=hs[1], verdict="same_process", aspect="병 충진·캡핑"),
            SimpleNamespace(hit=hs[2], verdict="shared_problem", aspect="체결 토크 관리")], hs[:1], True


async def fake_judge_fail(request, hs):
    return [], [], False


async def fake_examples(db, q, *, kind, top_k=1):
    return ""


real = (cs.SessionLocal, casebook.search_cases, casebook.build_reference_block, case_guard.judge,
        confirmed.get_confirmed_examples)
cs.SessionLocal = _FakeDB
casebook.search_cases = fake_search
casebook.build_reference_block = lambda hs, **kw: (seen_blocks.append([h.ref for h in hs]) or "BLOCK")
confirmed.get_confirmed_examples = fake_examples
try:
    case_guard.judge = fake_judge_keep_one
    ctx, titles, used, case_titles = asyncio.run(cs._reference_context("약액 병 캡핑 요청"))
    check(seen_blocks[-1] == ["2023-01"], "같은 공정(same_process) 사례만 참고 블록에", str(seen_blocks))
    check(case_titles == ["담금주 생산공정"], "표현 유출 검사용 원제목 전달", str(case_titles))
    check(titles == ["사례 2023-01 담금주 생산공정 (참고: 병 충진·캡핑)"], "요약에 참고 관점 표시", str(titles))
    check("BLOCK" in ctx and "요청 원문에 있는 값만" in ctx, "참고 블록 + 수치 금지 안내")
    case_guard.judge = fake_judge_fail
    ctx, titles, used, case_titles = asyncio.run(cs._reference_context("약액 병 캡핑 요청"))
    check((ctx, titles, case_titles) == ("", [], []), "판정 실패 시 사례 없이 진행")
finally:
    (cs.SessionLocal, casebook.search_cases, casebook.build_reference_block, case_guard.judge,
     confirmed.get_confirmed_examples) = real

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, d in FAIL:
    print("  FAIL", label, d)
sys.exit(1 if FAIL else 0)
