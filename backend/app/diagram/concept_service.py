"""공정 개념도 원클릭 — 영업 요청 원문 → 스펙(AI) → draw.io 원본 + PNG → 첨부 저장.

챗 fast-path(main._stream_concept_map)가 호출한다. 요청 원문은 모달이 .txt 첨부로 올린다
(첨부 소유자 = 요청자라서 결과 파일도 요청자 본인만 내려받을 수 있다).

AI 는 스펙(JSON)만 정하고, 도면 좌표·그리기는 drawio.py 가 결정론적으로 한다.

참고 자료(스펙 생성 context): 유사 공정 컨설팅 사례(casebook) + 담당자가 확정한 과거 개념도(confirmed).
결과는 초안으로 남기고(confirmed.create_draft), 채팅의 확정 카드로 확정하면 다음 생성에 쓰인다.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import confirmed
from ..config import settings
from ..database import SessionLocal
from ..request_files import ResultFile, fetch_request_text, safe_name
from ..request_files import save_result as _save
from . import checklist, drawio, rulecheck
from .spec import ConceptMapSpec

logger = logging.getLogger("diagram.concept_service")

# 진행 단계 ID — main._stream_concept_map 이 ProgressCard 로 흘린다.
PROGRESS_STEPS: tuple[tuple[str, str], ...] = (
    ("fetch", "요청 내용 확인"),
    ("extract", "요청 항목 정리"),
    ("design", "개념도 설계 (AI)"),
    ("verify", "누락 항목 확인·보완"),
    ("draw", "draw.io 도면 작성"),
    ("upload", "결과 저장"),
)

_PNG_MIME = "image/png"
_DRAWIO_MIME = "application/vnd.jgraph.mxfile"
_PENDING = ("확인 필요", "검토 중")


@dataclass
class ConceptMapResult:
    title: str
    covered: int
    total: int
    missing: list[str]
    pending: list[str]
    files: list[ResultFile] = field(default_factory=list)
    auto_filled: list[str] = field(default_factory=list)   # AI 가 빠뜨려 코드가 넣은 항목
    overturned: list[str] = field(default_factory=list)    # AI '반영' 판정이 거짓이었던 항목
    png_available: bool = True
    png_error: bool = False                                # draw.io 는 있는데 변환 실패
    references: list[str] = field(default_factory=list)    # 참고한 컨설팅 사례
    used_confirmed: bool = False                           # 확정본 예시를 참고했는지
    record_id: int | None = None                           # 확정 카드용 초안 id
    masked_numbers: list[str] = field(default_factory=list)  # 요청에 없어 가린 수치
    removed_terms: list[str] = field(default_factory=list)   # 사례에서 옮겨 와 지운 표현

    @property
    def summary(self) -> str:
        lines = [f"**{self.title}** 개념도를 만들었습니다."]
        if self.total:
            by_ai = self.covered - len(self.auto_filled)
            lines.append(f"- 요청 항목 {self.total}개 중 {by_ai}개를 AI 가 반영")
        if self.auto_filled:
            lines.append("- AI 가 빠뜨려 자동으로 넣은 항목(표·각주·설비 확인 필요): "
                         + ", ".join(self.auto_filled))
        if self.overturned:
            lines.append("- AI 가 반영했다고 했지만 실제로 없어 다시 확인한 항목: "
                         + ", ".join(dict.fromkeys(self.overturned)))
        if self.missing:
            lines.append("- 반영하지 못한 항목: " + ", ".join(self.missing))
        if self.pending:
            lines.append("- 확인이 필요한 항목(표에 표시): " + ", ".join(self.pending))
        if self.masked_numbers:
            lines.append("- 요청에 없는 수치라 '확인 필요'로 바꾼 값: " + ", ".join(self.masked_numbers))
        if self.removed_terms:
            lines.append("- 참고 사례에서 옮겨 온 표현이라 지운 단어: " + ", ".join(self.removed_terms))
        if self.references:
            lines.append("- 참고한 유사 공정 컨설팅 사례: " + ", ".join(self.references))
        if self.used_confirmed:
            lines.append("- 담당자가 확정한 비슷한 과거 개념도의 형식을 참고했습니다")
        if self.png_error:
            lines.append("- PNG 변환에 실패했습니다. .drawio 파일을 draw.io 에서 열어 PNG 로 내보내 주세요.")
        elif not self.png_available:
            lines.append("- 서버에 draw.io 가 없어 PNG 는 만들지 못했습니다. "
                         ".drawio 파일을 draw.io 에서 열어 내보내 주세요.")
        lines.append("- 수정이 필요하면 .drawio 파일을 draw.io(무료, app.diagrams.net)에서 "
                     "열어 고친 뒤 PNG 로 내보내면 됩니다.")
        return "\n".join(lines)


# 개념도용 사례 참고 — 제안서 본문보다 짧게. 스펙 JSON 을 쓰는 데는 공정 흐름·설비 구성이면 충분하다.
_CASE_TOP = 2
_CASE_CANDIDATES = 3      # 관련성 판정(case_guard)에 올리는 후보 수 — 걸러진 뒤 최대 _CASE_TOP 건
_CASE_CHARS = 1200
_CASE_QUERY_MAX = 600
_CONCEPT_GUARD = ("위 참고 자료는 공정 단계·설비 구성·검사 위치를 잡는 데만 참고한다. "
                  "참고 자료의 수치·기업명·KPI 는 개념도에 쓰지 않는다. "
                  "개념도의 수치는 요청 원문에 있는 값만 쓰고, 없으면 \"확인 필요\" 로 둔다.")


async def _reference_context(request: str) -> tuple[str, list[str], bool, list[str]]:
    """(스펙 생성 context, 요약용 사례 표기, 확정본 사용 여부, 사례 원제목). 실패 시 참고 없이."""
    blocks: list[str] = []
    titles: list[str] = []
    case_titles: list[str] = []
    used_confirmed = False
    try:
        # 지연 import — 사례집 모듈 장애가 개념도 기능 전체를 막지 않게.
        from ..casebook import build_reference_block, search_cases
        from ..proposal import case_guard

        async with SessionLocal() as db:
            hits = await search_cases(db, request[:_CASE_QUERY_MAX], top_cases=_CASE_CANDIDATES)
            example = await confirmed.get_confirmed_examples(db, request, kind="concept_map")
        # 검색 점수만으로는 공정이 다른 사례가 섞인다(실측: 약액 캡핑 요청에 임플란트 드릴·피스톤링
        # 연마). 관련성 판정을 통과한 사례만 쓴다 — 판정 실패 시 사례 없이 진행.
        kept, _dropped, _ok = await case_guard.judge(request, hits)
        # 개념도는 설비 배치 그림이라 '같은 공정' 사례만 쓴다. 문제 관점만 공유하는 다른 업종 사례는
        # 업종 명사가 옮겨 온다(실측: 약액 병 요청 → "세라믹 카트리지/보틀" 제목). 그런 관점은 본문 몫.
        kept = [v for v in kept if v.verdict == "same_process"][:_CASE_TOP]
        case_titles = [v.hit.title for v in kept]
        if kept:
            blocks.append(build_reference_block([v.hit for v in kept],
                                                max_chars_per_case=_CASE_CHARS))
            titles = [f"사례 {v.hit.ref} {v.hit.title}" + (f" (참고: {v.aspect})" if v.aspect else "")
                      for v in kept]
        if example:
            blocks.append(example)
            used_confirmed = True
    except Exception as e:  # 참고 자료는 보조 — 검색 장애로 개념도 자체를 막지 않는다.
        logger.warning("[concept] 참고 자료 검색 실패, 참고 없이 진행: %r", e)
        return "", [], False, []
    if not blocks:
        return "", [], False, []
    return "\n\n".join(blocks) + "\n\n" + _CONCEPT_GUARD, titles, used_confirmed, case_titles


async def _fetch_request(attachment_ids: list[int]) -> tuple[str, int]:
    """첨부 중 첫 텍스트 파일 → (요청 원문, 소유자 id)."""
    return await fetch_request_text(attachment_ids, what="개념도 요청 내용")


def _safe_name(s: str) -> str:
    return safe_name(s, "개념도")


def _pending_items(spec: ConceptMapSpec) -> list[str]:
    """표에서 '확인 필요/검토 중' 으로 남은 행의 첫 칸."""
    out = []
    for t in spec.tables:
        for r in t.rows:
            if r and any(c.strip() in _PENDING for c in r[1:]):
                out.append(r[0])
    return out


def _export_png(xml: str, exe: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "concept.drawio", Path(tmp) / "concept.png"
        src.write_text(xml, encoding="utf-8")
        drawio.export_png(src, dst, exe)
        return dst.read_bytes()


async def build_concept_map(
    *,
    attachment_ids: list[int],
    on_stage: Callable[[str], None] | None = None,
) -> ConceptMapResult:
    """요청 첨부 → 개념도 스펙 → .drawio + PNG 저장."""
    stage = on_stage or (lambda _s: None)
    stage("fetch")
    request, owner_id = await _fetch_request(attachment_ids)

    context, references, used_confirmed, case_titles = await _reference_context(request)
    spec, notes, report = await checklist.generate_checked(
        request, context=context, on_stage=stage)
    # 참고 자료(사례집·확정본)의 수치가 옮겨 오지 않게 — 요청에 근거 없는 수치는 가린다.
    spec, masked_numbers = rulecheck.mask_unsupported_numbers(spec, request)
    spec, removed_terms = rulecheck.strip_case_terms(spec, request, case_titles)
    if removed_terms:
        logger.info("[concept] 사례 표현 제거: %s", removed_terms)
    if masked_numbers:
        logger.info("[concept] 근거 없는 수치 가림: %s", masked_numbers)
    if notes:
        logger.info("[concept] 스펙 교정: %s", notes)

    stage("draw")
    xml = drawio.build_drawio(spec)
    exe = drawio.find_exe(settings.drawio_exe)
    png: bytes | None = None
    png_error = False
    if exe:
        try:
            png = await asyncio.to_thread(_export_png, xml, exe)
        except Exception as e:  # PNG 한 장 때문에 수 분짜리 결과를 버리지 않는다 — .drawio 는 전달.
            png_error = True
            logger.warning("[concept] PNG 변환 실패: %r / spec=%s", e, spec.model_dump_json())
    else:
        logger.warning("[concept] draw.io 실행 파일을 찾지 못해 PNG 를 건너뜀")

    stage("upload")
    stem = f"개념도_{_safe_name(spec.title)}_{datetime.now():%y%m%d_%H%M}"
    files = []
    if png:
        files.append(await _save(owner_id, stem + ".png", png, _PNG_MIME))
    files.append(await _save(owner_id, stem + ".drawio", xml.encode("utf-8"), _DRAWIO_MIME))

    record_id = None
    try:
        async with SessionLocal() as db:
            record_id = await confirmed.create_draft(
                db, user_id=owner_id, kind="concept_map", title=spec.title,
                request_text=request, content=spec.model_dump(),
                result_attachment_ids=[f.attachment_id for f in files
                                       if f.attachment_id is not None])
    except Exception as e:  # 초안 기록 실패는 결과 전달을 막지 않는다(확정 카드만 빠짐).
        logger.warning("[concept] 초안 기록 실패: %r", e)

    missing = [m.text for m in report.missing] + [m.text for m in report.rule_missing]
    return ConceptMapResult(
        title=spec.title, covered=report.total - len(missing), total=report.total,
        missing=missing, pending=_pending_items(spec), files=files,
        png_available=png is not None, png_error=png_error, auto_filled=report.auto_filled,
        overturned=report.overturned, references=references,
        used_confirmed=used_confirmed, record_id=record_id, masked_numbers=masked_numbers,
        removed_terms=removed_terms,
    )
