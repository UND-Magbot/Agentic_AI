"""고객 요청 원문 → 제안서가 반드시 다뤄야 할 요구 항목.

두 갈래로 뽑아 합친다 — 추출과 판정을 같은 모델에 맡기면 누락이 안 잡히기 때문(rulecheck 실측).
  1) AI 추출: 목표·현황·제약·확인 요청·요청 산출물. 항목마다 원문 인용(evidence)을 받고,
     인용이 원문에 실제로 없으면 버린다(모델이 요청에 없는 요구를 지어내는 것 차단).
  2) 코드 추출(diagram.rulecheck.extract): 수치+단위, 영문 장비·브랜드명, 인증 등급, 기한,
     화살표로 이어진 공정 단계 — 글자로 대조 가능한 것.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .. import proposal_llm
from ..diagram import rulecheck
from ..diagram.llm_spec import _extract_json

_log = logging.getLogger("proposal.requirements")

KINDS = ("goal", "fact", "constraint", "question", "deliverable", "process")
KIND_LABEL = {
    "goal": "목표", "fact": "현황·사양", "constraint": "제약", "question": "확인 요청",
    "deliverable": "요청 산출물", "process": "공정",
}

_EXTRACT_PROMPT = """아래는 고객사가 보낸 로봇 자동화 제안 요청(또는 영업 담당자의 현장 메모)이다.
제안서가 반드시 다뤄야 할 요구 항목을 원문에서 **빠짐없이** 뽑아라. 원문에 없는 항목은 만들지 않는다.

항목 종류(kind):
- goal        : 고객이 이루려는 목표 (예: 용접 품질 편차 해소, 인력 대체)
- fact        : 현재 공정·제품·사양·수치·장비 (예: 제품 350kg, 작업자 4명, PLC 제어)
- constraint  : 지켜야 할 조건 (예: HACCP 위생, 세척 가능, 공간 제약, 예산·일정)
- question    : 검토·확인·제안을 요청한 사항 (예: 로봇 대수 검토, 비전 인식 가능 여부, 검사 방법 제안)
- deliverable : 제안서·산출물에 포함해 달라고 한 것 (예: PoC 범위 협의안, 레이아웃 표기)
- process     : 공정 단계·동작 순서 (예: 가접 후 4면 본용접)

evidence 에는 원문 문장 조각을 **글자 그대로** 옮긴다(바꿔 쓰지 않는다).
JSON: {"items": [{"kind": "...", "text": "짧은 항목명", "evidence": "원문 그대로"}]}"""


@dataclass
class Requirement:
    id: str
    kind: str
    text: str
    evidence: str
    source: str = "ai"              # ai | rule
    must: list[str] = field(default_factory=list)   # rule 항목: 본문에 있어야 하는 토큰
    min_hits: int = 0
    covered: bool = False
    where: str = ""                 # 반영된 섹션 제목
    response: str = ""              # 대응표에 적는 한 줄 대응


def _in_request(evidence: str, request: str) -> bool:
    """인용이 원문에 있는가 — 공백·구두점 차이는 무시한다."""
    ev = rulecheck.normalize(evidence)
    return len(ev) >= 2 and ev in rulecheck.normalize(request)


def rule_requirements(request: str) -> list[Requirement]:
    out = []
    for it in rulecheck.extract(request):
        if it.kind == "structure":      # 개념도 설비 배치 규칙 — 제안서 본문 대조 대상 아님
            continue
        out.append(Requirement(id=f"r{len(out) + 1}", kind=it.kind, text=it.text, evidence=it.evidence,
                               source="rule", must=list(it.must), min_hits=it.min_hits))
    return out


async def extract(request: str) -> tuple[list[Requirement], list[str]]:
    """반환: (요구 항목, 원문에 없어 버린 AI 항목명)."""
    raw = await proposal_llm.chat(
        [{"role": "system", "content": _EXTRACT_PROMPT}, {"role": "user", "content": request}],
        fmt="json", temperature=0.1,
    )
    items: list[Requirement] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for d in _extract_json(raw).get("items") or []:
        if not isinstance(d, dict):
            continue
        text = str(d.get("text") or "").strip()
        evidence = str(d.get("evidence") or "").strip()
        if not text:
            continue
        if not _in_request(evidence, request):
            dropped.append(text)
            continue
        key = rulecheck.normalize(text)
        if key in seen:
            continue
        seen.add(key)
        kind = d.get("kind") if d.get("kind") in KINDS else "fact"
        items.append(Requirement(id=f"a{len(items) + 1}", kind=kind, text=text, evidence=evidence))
    if dropped:
        _log.info("[proposal] 원문 근거 없는 추출 항목 버림: %s", dropped)
    return items + rule_requirements(request), dropped
