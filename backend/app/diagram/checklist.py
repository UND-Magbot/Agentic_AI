"""요청 체크리스트 — 영업 요청에 적힌 것이 개념도에 빠짐없이 드러났는지 확인한다.

개념도 스펙 생성만으로는 "요청에 있는데 그림에 없는" 항목이 남는다(실측: Class 100 확인,
비전 장착 방식 구분이 반복 누락). 그래서 생성 전후로 두 단계를 더 둔다.

  1) extract : 요청 원문 → 반드시 드러나야 할 항목 목록 (원문 근거 포함)
  2) verify  : 생성된 스펙 ↔ 항목 목록 대조 → 누락 항목
  3) 누락이 있으면 직전 스펙과 누락 목록을 주고 고치게 한다 (llm_spec.generate_spec(revise=...))

LLM 이 뽑고 LLM 이 판정하면 모델이 약할 때 누락이 그대로 통과한다(12b 실측). 그래서
rulecheck 가 원문에서 글자로 확인 가능한 항목을 따로 뽑아 코드로 대조하고, LLM 의 '반영' 판정도
숫자·영문 토큰이 실제 스펙에 있는지 교차 확인한다. 보완 후에도 남은 누락은 스펙에 직접 넣는다.

확인·검토가 필요한 항목은 수치를 지어내지 않고 '검토 사항' 표에 "확인 필요" 로 남기게 한다.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .. import proposal_llm
from . import llm_spec, rulecheck
from .spec import ConceptMapSpec

_log = logging.getLogger("diagram.checklist")

NL = "\n"

KINDS = ("process", "fact", "question", "deliverable")

_EXTRACT_PROMPT = """아래는 영업 담당자가 보낸 공정 개념도 요청 원문이다.
개념도에 반드시 드러나야 할 항목을 원문에서 **빠짐없이** 뽑아라. 원문에 없는 항목은 만들지 않는다.

항목 종류(kind):
- process     : 공정 단계·동작 순서 (예: 비전으로 X·Y·Z 센터링 후 캐핑)
- fact        : 사양·수치·장비명 (예: 빈 병 1.5kg, 약액 주입 210초, PLC 제어)
- question    : 확인·검토를 요구한 사항 (예: Class 100 대응 여부 확인, 비전 대수 검토, CT 검토)
- deliverable : 그림에 표기해 달라고 한 것 (예: 비전 포지셔닝 표기)

JSON: {"items": [{"id": "c1", "kind": "...", "text": "짧은 항목명", "evidence": "원문에서 그대로 인용"}]}"""

_VERIFY_PROMPT = """아래 [개념도 스펙]이 [체크리스트]의 각 항목을 담고 있는지 판정하라.
개념도에 **글자로 드러나야** 반영된 것이다. 비슷한 말로 바뀌어 있어도 의미가 같으면 반영된 것으로 본다.
question 항목은 "확인 필요", "검토 중" 같은 표시와 함께 드러나야 반영이다.
판정만 한다. 스펙을 고치지 않는다.

JSON: {"results": [{"id": "c1", "covered": true|false, "where": "찾은 위치 또는 빈 문자열", "reason": "짧게"}]}"""

_FIX_RULES = """위 개념도 스펙에서 요청 원문의 다음 항목이 빠졌습니다. 모두 반영해 고치세요.

{missing}

고치는 규칙:
- 확인·검토가 필요한 항목(인증 등급, 장비 선정, 대수 산정, 사이클 타임 등)은 tables 에
  {{"title": "검토 사항", "headers": ["항목", "내용", "상태"], "rows": [...]}} 표를 두고 상태를 "확인 필요" 또는 "검토 중" 으로 적는다.
- 비전을 로봇에 다는지 고정형인지 물었다면 cameras[].caption 에 "(로봇 장착)" 또는 "(고정형)" 을 붙인다.
  근거가 없으면 "(장착 방식 검토)" 로 적는다.
- 사양·수치는 요청 원문에 있는 값만 쓴다. 모르는 수치는 지어내지 않고 "확인 필요" 로 둔다.
- 빠진 항목과 무관한 부분은 바꾸지 않는다."""


@dataclass
class CheckItem:
    id: str
    kind: str
    text: str
    evidence: str = ""
    covered: bool = False
    where: str = ""
    auto_filled: bool = False


@dataclass
class ChecklistReport:
    items: list[CheckItem]
    rounds: int = 0
    history: list[list[str]] = field(default_factory=list)   # 라운드별 누락 항목
    rule_items: list[rulecheck.RuleItem] = field(default_factory=list)
    overturned: list[str] = field(default_factory=list)       # LLM '반영' 판정을 뒤집은 항목

    @property
    def missing(self) -> list[CheckItem]:
        return [i for i in self.items if not i.covered]

    @property
    def rule_missing(self) -> list[rulecheck.RuleItem]:
        return [i for i in self.rule_items if not i.covered]

    @property
    def total(self) -> int:
        return len(self.items) + len(self.rule_items)

    @property
    def auto_filled(self) -> list[str]:
        return ([i.text for i in self.items if i.auto_filled]
                + [i.text for i in self.rule_items if i.auto_filled])


def _parse(raw: str) -> dict[str, Any]:
    return llm_spec._extract_json(raw)


async def extract(request: str) -> list[CheckItem]:
    raw = await proposal_llm.chat(
        [{"role": "system", "content": _EXTRACT_PROMPT}, {"role": "user", "content": request}],
        fmt="json", temperature=0.1,
    )
    items = []
    for i, d in enumerate(_parse(raw).get("items") or [], 1):
        text = str(d.get("text") or "").strip()
        if not text:
            continue
        kind = d.get("kind") if d.get("kind") in KINDS else "fact"
        items.append(CheckItem(id=str(d.get("id") or f"c{i}"), kind=kind, text=text,
                               evidence=str(d.get("evidence") or "")))
    return items


async def verify(spec: ConceptMapSpec, items: list[CheckItem]) -> None:
    """items 의 covered/where 를 채운다."""
    payload = json.dumps([{"id": i.id, "kind": i.kind, "text": i.text} for i in items], ensure_ascii=False)
    raw = await proposal_llm.chat(
        [{"role": "system", "content": _VERIFY_PROMPT},
         {"role": "user", "content": f"[체크리스트]\n{payload}\n\n[개념도 스펙]\n{spec.model_dump_json()}"}],
        fmt="json", temperature=0.0,
    )
    by_id = {str(r.get("id")): r for r in _parse(raw).get("results") or []}
    for it in items:
        r = by_id.get(it.id)
        # 판정이 빠진 항목은 누락으로 본다 — 보수적으로 한 번 더 고치게 한다.
        it.covered = bool(r and r.get("covered"))
        it.where = str((r or {}).get("where") or "")


def _cross_check(spec: ConceptMapSpec, items: list[CheckItem]) -> list[str]:
    """LLM 이 '반영' 이라 한 항목 중 숫자·영문 토큰이 스펙에 없는 것을 누락으로 뒤집는다."""
    flipped = []
    for it in items:
        if it.covered and not rulecheck.cross_check(spec, f"{it.text} {it.evidence}"):
            it.covered = False
            flipped.append(it.text)
    return flipped


def _backfill_llm(spec: ConceptMapSpec, items: list[CheckItem]) -> None:
    """LLM 항목 중 끝까지 빠진 것 — 검토 사항 표에 "확인 필요" 로 남긴다."""
    for it in items:
        if not it.covered:
            rulecheck.review_table(spec).rows.append(
                [it.text, (it.evidence or "요청 원문")[:40], "확인 필요"])
            it.covered = it.auto_filled = True


async def generate_checked(
    request: str,
    *,
    context: str = "",
    max_rounds: int = 2,
    on_stage: Callable[[str], None] | None = None,
) -> tuple[ConceptMapSpec, list[str], ChecklistReport]:
    """체크리스트 추출 → 스펙 생성 → 누락 확인 → (누락 시) 보완 생성.

    on_stage 는 단계 시작 시 "extract" / "design" / "verify" 로 호출된다(진행 표시용).
    """
    stage = on_stage or (lambda _s: None)
    stage("extract")
    items = await extract(request)
    rule_items = rulecheck.extract(request)
    stage("design")
    spec, notes = await llm_spec.generate_spec(request, context=context)
    report = ChecklistReport(items=items, rule_items=rule_items)
    if not items and not rule_items:
        return spec, notes, report

    stage("verify")
    # AI 판정은 첫 라운드만 전체, 이후엔 직전에 빠졌던 AI 항목만 다시 본다 — 보완 지시가
    # "빠진 항목과 무관한 부분은 바꾸지 않는다" 이고, 수치·설비·일정은 코드가 매 라운드 대조한다.
    to_verify = list(items)
    for rnd in range(max_rounds + 1):
        if to_verify:
            await verify(spec, to_verify)
            report.overturned += _cross_check(spec, to_verify)
        rulecheck.verify(spec, rule_items)
        missing, rule_missing = report.missing, report.rule_missing
        report.history.append([m.text for m in missing] + [m.text for m in rule_missing])
        _log.info("체크리스트 r%d: %d/%d 반영", rnd,
                  report.total - len(missing) - len(rule_missing), report.total)
        if not (missing or rule_missing) or rnd == max_rounds:
            break
        lines = NL.join(f"- [{m.kind}] {m.text} (원문: {m.evidence})"
                        for m in [*missing, *rule_missing])
        # 보완에는 참고 자료(사례집·확정본)를 다시 넣지 않는다 — 직전 스펙과 누락 목록이면 충분하고,
        # 입력이 길면 12b 가 컨텍스트 한도에 걸려 재시도한다(2026-09-28 실측).
        spec, more = await llm_spec.generate_spec(
            request, revise=(spec, _FIX_RULES.format(missing=lines)))
        notes += more
        report.rounds = rnd + 1
        to_verify = list(missing)

    # 보완 생성으로도 안 채워진 항목은 코드가 직접 넣는다 — 조용히 빠지는 항목이 없게.
    _backfill_llm(spec, items)
    rulecheck.backfill(spec, rule_items)
    if report.auto_filled:
        _log.info("체크리스트 자동 보완: %s", report.auto_filled)
    return spec, notes, report
