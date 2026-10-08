"""제안서 본문 원클릭 — 요청 원문(.txt 첨부) → 요구 항목 → 사례 검색·관련성 판정 → 초안(AI)
→ 요청 반영 검증·전문가 검토·보완 → 표절 제거 → 수치 근거 가림 → 컨셉 이미지(외부, 가명 처리)
→ .pptx(디자인판·심플판) + 확정 카드.

챗 fast-path(main._stream_proposal_body)가 호출한다. 결과 파일은 요청자 소유 첨부로 저장된다.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .. import confirmed
from ..casebook import CaseHit, search_cases
from ..database import SessionLocal
from ..request_files import ResultFile, fetch_request_text, safe_name, save_result
from . import case_guard, concept_image, quality, requirements
from .body import BodyReport, ProposalBody, draft_body, finalize_body
from .case_guard import CaseVerdict
from .pptx_premium import to_pptx_premium
from .pptx_render import to_pptx
from .render import cover_title, to_markdown
from .requirements import Requirement

logger = logging.getLogger("proposal.service")

PROGRESS_STEPS: tuple[tuple[str, str], ...] = (
    ("fetch", "요청 내용 확인"),
    ("extract", "요구 항목 정리 (AI)"),
    ("cases", "유사 사례 검색·관련성 판정"),
    ("write", "본문 작성 (AI)"),
    ("review", "요청 반영·전문가 검토·보완"),
    ("save", "표절·수치 점검"),
    ("image", "컨셉 이미지 생성 (외부·가명 처리)"),
    ("files", "문서 저장"),
)

KIND = "proposal_body"   # confirmed.KINDS
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_QUERY_MAX = 600         # 사례 검색 질의 길이(요청 원문 앞부분).
_CANDIDATES = 5          # 관련성 판정에 올릴 후보 수(판정 후 최대 _MAX_CASES 건 사용)
_MAX_CASES = 3


@dataclass
class ProposalBodyResult:
    body: ProposalBody
    report: BodyReport
    reqs: list[Requirement]
    verdicts: list[CaseVerdict]
    files: list[ResultFile] = field(default_factory=list)
    record_id: int | None = None          # 확정 대기 기록(proposal_records) — 생성 실패 시 None
    used_confirmed_example: bool = False  # 비슷한 확정본을 형식 예시로 썼는지
    layout_overflow: list[str] = field(default_factory=list)   # 최소 글자 크기로도 넘친 슬라이드 글상자
    image_note: str = ""                  # 컨셉 이미지 결과 안내(생성·생략 사유)

    @property
    def summary(self) -> str:
        r = self.report
        n_ok = sum(1 for q in self.reqs if q.covered)
        lines = [f"**{cover_title(self.body)}** 본문 초안을 만들었습니다. 대외 발송 전 담당자 검토가 필요합니다.",
                 f"- 요청 항목 {len(self.reqs)}건 중 {n_ok}건 본문 반영(문서의 '고객 요구사항 대응표' 참조)"]
        if r.uncovered:
            lines.append("- ⚠ 보완 후에도 본문에 반영되지 않은 항목 — 직접 보완해 주세요: " + ", ".join(r.uncovered))
        if r.review_rounds:
            lines.append(f"- 요청 반영·전문가 검토로 {r.review_rounds}회 보완"
                         + (f" (고친 문제 {len(r.review_issues)}건)" if r.review_issues else ""))
        if self.used_confirmed_example:
            lines.append("- 비슷한 요청의 확정 제안서를 구성·표기 참고 예시로 반영했습니다.")
        if self.verdicts:
            lines.append("- 참고한 사례(관련성 확인): " + ", ".join(
                f"{v.hit.ref} {v.hit.title}({v.hit.company})" for v in self.verdicts))
        elif r.case_judge_failed:
            lines.append("- 사례 관련성 판정에 실패해 사례 없이 작성했습니다.")
        else:
            lines.append("- 관련 있는 사례를 찾지 못해 사례 없이 작성했습니다.")
        if r.leaked_terms:
            lines.append("- ⚠ 참고 사례의 용어가 본문에 남은 곳 — 이 고객 공정에 맞는지 확인해 주세요: "
                         + " / ".join(r.leaked_terms))
        if r.dropped_cases:
            lines.append("- 검색됐지만 관련이 약해 제외한 사례: " + ", ".join(r.dropped_cases))
        if r.masked_numbers:
            lines.append(f"- 요청에 근거가 없는 수치·사양 {len(r.masked_numbers)}건을 '(수치/사양 확인 필요)'로 바꿨습니다: "
                         + ", ".join(r.masked_numbers))
        if r.rewritten:
            lines.append(f"- 참고 자료와 표현이 겹친 문장 {r.rewritten}건을 새로 썼습니다.")
        if r.copied:
            lines.append(f"- 새로 써도 겹쳐 뺀 문장 {len(r.copied)}건: " + " / ".join(r.copied))
        if r.repaired:
            lines.append("- 처음에 빠져 다시 작성한 섹션: " + ", ".join(r.repaired))
        if r.placeholders:
            lines.append("- 작성하지 못해 '(수기 작성 필요)'로 둔 섹션: " + ", ".join(r.placeholders))
        if self.image_note:
            lines.append(f"- {self.image_note}")
        if self.layout_overflow:
            lines.append("- 글이 많아 슬라이드에서 넘칠 수 있는 곳(열어서 확인해 주세요): "
                         + ", ".join(dict.fromkeys(self.layout_overflow)))
        return "\n".join(lines)


def case_query(request: str) -> str:
    """사례 검색 질의 = 요청 원문 앞부분. AI 가 뽑은 항목은 의역돼 공정 이름("꼬치 어묵 포장")을
    잃는다 — 2026-09-28 실측: 항목 질의는 칫솔모·세라믹 사례로 빗나가고, 원문 질의는 어묵 포장 사례 0.723 1순위."""
    return request[:_QUERY_MAX]


async def _search(query: str) -> list[CaseHit]:
    async with SessionLocal() as db:
        return await search_cases(db, query, top_cases=_CANDIDATES)


async def _confirmed_examples(request: str) -> str:
    """비슷한 요청의 확정 제안서(형식 예시). 조회 실패는 예시 없이 진행한다."""
    try:
        async with SessionLocal() as db:
            return await confirmed.get_confirmed_examples(db, request, kind=KIND)
    except Exception as e:
        logger.warning("[proposal] 확정본 조회 실패, 예시 없이 진행: %r", e)
        return ""


async def _create_record(
    owner_id: int, request: str, body: ProposalBody, files: list[ResultFile]
) -> int | None:
    """확정 대기 기록. 실패해도 결과 파일은 이미 저장됐으므로 확정 카드만 빠진다."""
    try:
        async with SessionLocal() as db:
            return await confirmed.create_draft(
                db, user_id=owner_id, kind=KIND, title=cover_title(body), request_text=request,
                content=asdict(body),
                result_attachment_ids=[f.attachment_id for f in files if f.attachment_id is not None],
            )
    except Exception as e:
        logger.warning("[proposal] 확정 기록 생성 실패: %r", e)
        return None


async def select_cases(request: str, report: BodyReport) -> list[CaseVerdict]:
    """검색 후보 → 관련성 판정 → 최대 _MAX_CASES 건. 검색·판정 실패는 사례 없이."""
    try:
        hits = await _search(case_query(request))
    except Exception as e:  # 임베딩 서비스 장애
        logger.warning("[proposal] 사례 검색 실패, 사례 없이 진행: %r", e)
        return []
    kept, dropped, ok = await case_guard.judge(request, hits)
    report.case_judge_failed = not ok
    report.dropped_cases = [f"{v.hit.ref} {v.hit.title}" for v in dropped + kept[_MAX_CASES:]]
    return kept[:_MAX_CASES]


async def write_body(
    request: str, *, on_stage: Callable[[str], None] | None = None,
) -> tuple[ProposalBody, BodyReport, list[Requirement], list[CaseVerdict], str]:
    """요청 원문 → 완성 본문(저장 없음). 반환: (본문, 보고, 요구 항목, 채택 사례, 확정 예시)."""
    stage = on_stage or (lambda _s: None)
    report = BodyReport()

    stage("extract")
    reqs, report.dropped_requirements = await requirements.extract(request)

    stage("cases")
    verdicts = await select_cases(request, report)
    examples = await _confirmed_examples(request)

    stage("write")
    body, _ = await draft_body(request, reqs, verdicts, report, examples=examples)

    stage("review")
    await quality.review_and_revise(request, reqs, body, report, verdicts=verdicts)

    stage("save")
    await quality.remove_plagiarism(body, quality.plagiarism_sources(verdicts, examples), request, report)
    finalize_body(body, request, reqs, verdicts, report)
    return body, report, reqs, verdicts, examples


async def build_proposal_body(
    *,
    attachment_ids: list[int],
    on_stage: Callable[[str], None] | None = None,
) -> ProposalBodyResult:
    stage = on_stage or (lambda _s: None)
    stage("fetch")
    request, owner_id = await fetch_request_text(attachment_ids, what="제안서 요청 내용")
    body, report, reqs, verdicts, examples = await write_body(request, on_stage=stage)

    stage("image")
    image, image_note = await concept_image.generate(body, user_id=owner_id)

    stage("files")

    stem = f"제안서_초안_{safe_name(body.customer or cover_title(body), '제안서')}_{datetime.now():%y%m%d_%H%M}"
    # 같은 본문을 두 디자인으로 — 디자인판(대외 제출용)을 먼저, 심플판(내부 검토용)을 뒤에.
    files, overflow = [], []
    renders = (("디자인", lambda b: to_pptx_premium(b, concept_image=image)), ("심플", to_pptx))
    for suffix, render in renders:
        data, render_report = render(body)
        overflow += [f"{suffix}판 {w}" for w in render_report.overflow]
        files.append(await save_result(owner_id, f"{stem}_{suffix}.pptx", data, _PPTX_MIME))
    if overflow:
        logger.warning("[proposal] 최소 글자 크기로도 넘친 글상자: %s", overflow)
    record_id = await _create_record(owner_id, request, body, files)
    return ProposalBodyResult(body=body, report=report, reqs=reqs, verdicts=verdicts, files=files,
                              record_id=record_id, used_confirmed_example=bool(examples),
                              layout_overflow=overflow, image_note=image_note)


def result_message(result: ProposalBodyResult) -> str:
    links = "\n".join(f"[{f.filename}]({f.download_url})" for f in result.files)
    if result.record_id is not None:
        links += "\n" + confirmed.confirm_link(result.record_id)
    return f"{result.summary}\n\n{links}\n\n---\n\n{to_markdown(result.body)}"
