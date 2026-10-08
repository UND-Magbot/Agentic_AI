"""참고 사례 관련성 판정 — 엉뚱한 사례가 제안 방향을 끌고 가는 것을 막는다.

검색 점수(cosine·제목 일치)만으로는 공정이 다른 사례가 섞인다(실측: 약액 캡핑 요청에 임플란트 드릴 사례).
그런 사례가 프롬프트에 들어가면 모델이 그 사례의 공정·설비 구성을 따라 쓴다. 그래서 후보마다
요청 원문과 사례 개요를 나란히 보여 주고 판정을 받는다.

  same_process   : 같은 종류의 공정·작업(용접↔용접, 포장↔포장) — 문제 정의·구성·검토 관점 참고 가능
  shared_problem : 공정은 다르지만 같은 기술 문제(고온·유분 제품 파지, 액체 충진·캡핑 …) — 그 관점만 참고
  unrelated      : 버린다

모델 판정만 믿지 않는다 — 2026-09-28 실측: 약액 병 캡핑 요청에 "14kg 중량물·피로도"를 근거로
피스톤링 연마 사례를 남김(14kg 는 로봇 가반하중, 피로도는 어느 사례에나 있는 일반 문제). 그래서
채택 사례마다 요청 원문 인용(request_quote)과 사례 원문 인용(case_quote)을 받아 코드가 원문에 있는지
확인하고, 인용이 없거나 틀리면 버린다. 일반 문제(피로도·인력난·생산성 …)만 겹치는 판정도 버린다.

판정 호출이 실패하면 사례를 **쓰지 않는다**(엉뚱한 사례를 넣느니 사례 없이 쓰는 편이 안전).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .. import proposal_llm
from ..casebook import CaseHit
from ..diagram import rulecheck
from ..diagram.llm_spec import _extract_json

_log = logging.getLogger("proposal.case_guard")

KEEP = ("same_process", "shared_problem")
_CASE_CHARS = 700
_QUOTE_MIN = 4   # 인용 조각이 이 글자 수(정규화 후) 이상 원문에 그대로 있어야 근거로 본다("용접" 같은 단어 X)
# 모델은 인용을 "A ... B", "A: B, C / D" 처럼 재배열한다 → 이 구분자로 조각낸다.
_ELLIPSIS_RE = re.compile(r"\.{2,}|…|·{3,}|[,/:;\n]")
# 공정이 달라도 거의 모든 사례가 공유하는 일반 문제 — 이것만으로는 shared_problem 이 아니다.
GENERIC_PROBLEMS = ("피로", "근골격", "인력난", "인력 수급", "인건비", "생산성", "반복 작업", "반복작업",
                    "숙련도", "안전사고", "작업 환경", "작업환경", "품질 향상", "원가 절감")

_PROMPT = """너는 로봇 자동화 제안서를 수백 건 써 본 수석 엔지니어다.
[고객 요청]에 대한 제안서를 쓸 때 아래 [후보 사례]가 참고 자료로 적절한지 사례마다 판정하라.

판정(verdict):
- same_process   : 사례의 대상 공정·작업이 요청과 같은 종류다 (예: 요청도 용접, 사례도 용접)
- shared_problem : 공정은 다르지만 요청의 핵심 **기술** 문제와 같은 문제를 풀었다
                   (예: 고온·유분 제품 파지, 액체 충진·캡핑, 비전으로 센서 대체).
                   다음만 겹치면 shared_problem 이 아니다 → unrelated:
                   작업자 피로·근골격계, 인력난, 인건비, 생산성, 반복 작업, 숙련도, 안전, 품질 향상 같은 일반 문제.
                   요청의 로봇 가반하중·사양을 사례의 제품 무게와 같다고 보지 않는다.
- unrelated      : 위 둘이 아니다. 애매하면 unrelated.

same_process/shared_problem 이면 반드시:
  aspect        : 이 사례에서 **무엇을** 참고할지 한 줄로 구체적으로 (예: "대형 하우징 4면 용접의 포지셔너 활용")
  request_quote : 그 관점이 해당하는 [고객 요청] 원문 조각을 글자 그대로
  case_quote    : 근거가 되는 [후보 사례] 원문 조각을 글자 그대로
unrelated 면 세 칸 모두 빈 문자열.

JSON: {"cases": [{"ref": "2024-03", "verdict": "...", "aspect": "...", "request_quote": "...",
                  "case_quote": "...", "reason": "짧게"}]}"""


@dataclass
class CaseVerdict:
    hit: CaseHit
    verdict: str
    aspect: str
    reason: str


def _case_body(h: CaseHit) -> str:
    return "\n".join(h.sections.get(s, "") for s in ("개요", "기존 공정의 문제점", "컨설팅 결과"))


def _case_digest(h: CaseHit) -> str:
    return f"### {h.ref} {h.title} (업종: {h.industry or '-'})\n{_case_body(h)[:_CASE_CHARS]}"


def _quoted(quote: str, source: str) -> bool:
    """인용이 원문에 근거하는가. 모델은 "A ... B" 처럼 조각을 이어 붙이고 조사를 조금씩 바꿔 적으므로
    (실측: 원문 "하고 있고" → 인용 "하고 있으며") 통째 일치 대신 조각 하나라도 원문에 그대로 있으면 인정한다.
    원문 어디에도 없는 인용(지어낸 근거)은 여전히 탈락한다."""
    src = rulecheck.normalize(source)
    for frag in _ELLIPSIS_RE.split(quote):
        f = rulecheck.normalize(frag)
        if len(f) >= _QUOTE_MIN and f in src:
            return True
        # 긴 조각은 앞·뒤 절반 중 하나라도 그대로 있으면 인정(끝 어미만 바뀐 경우)
        half = len(f) // 2
        if half >= _QUOTE_MIN and (f[:half] in src or f[half:] in src):
            return True
    return False


def grounded(v: "CaseVerdict", request: str, request_quote: str, case_quote: str) -> str:
    """채택 근거 검증. 통과면 "", 아니면 제외 사유."""
    if v.verdict not in KEEP:
        return v.verdict
    if not v.aspect:
        return "참고 관점 없음"
    if not _quoted(request_quote, request):
        return "요청 원문 인용 없음·불일치"
    case_src = _case_digest(v.hit) + "\n" + _case_body(v.hit)   # 번호·제목 머리글도 인용 대상
    if not _quoted(case_quote, case_src):
        return "사례 원문 인용 없음·불일치"
    if v.verdict == "shared_problem" and _generic_only(v.aspect):
        return "일반 문제만 공유"
    return ""


def _generic_only(aspect: str) -> bool:
    """관점에서 일반 문제 단어를 걷어내면 구체적 기술 내용이 남지 않는가."""
    rest = aspect
    for g in GENERIC_PROBLEMS:
        rest = rest.replace(g, "")
    return len(rulecheck.normalize(rest)) < 8 and any(g in aspect for g in GENERIC_PROBLEMS)


async def judge(request: str, hits: list[CaseHit]) -> tuple[list[CaseVerdict], list[CaseVerdict], bool]:
    """반환: (채택, 제외, 판정 성공 여부). 실패 시 채택 없음."""
    if not hits:
        return [], [], True
    user = f"[고객 요청]\n{request}\n\n[후보 사례]\n" + "\n\n".join(_case_digest(h) for h in hits)
    try:
        raw = await proposal_llm.chat(
            [{"role": "system", "content": _PROMPT}, {"role": "user", "content": user}],
            fmt="json", temperature=0.0,
        )
        data = _extract_json(raw)
    except Exception as e:  # 형식 오류·모델 오류 — 사례 없이 진행
        _log.warning("[proposal] 사례 관련성 판정 실패 → 사례 미사용: %r", e)
        return [], [], False
    by_ref = {h.ref: h for h in hits}
    kept: list[CaseVerdict] = []
    dropped: list[CaseVerdict] = []
    judged: set[str] = set()
    for d in data.get("cases") or []:
        if not isinstance(d, dict):
            continue
        ref = str(d.get("ref") or "").strip().lstrip("#")
        h = by_ref.get(ref)
        if h is None or ref in judged:
            continue
        judged.add(ref)
        v = CaseVerdict(h, str(d.get("verdict") or "unrelated"), str(d.get("aspect") or "").strip(),
                        str(d.get("reason") or "").strip())
        # 근거(참고 관점 + 양쪽 원문 인용)를 못 댄 사례는 쓰지 않는다 — 엉뚱한 사례가 방향을 흐린다.
        why = grounded(v, request, str(d.get("request_quote") or ""), str(d.get("case_quote") or ""))
        if why:
            v.reason = f"{why} — {v.reason}".strip(" —")
            dropped.append(v)
        else:
            kept.append(v)
    # 판정 누락 사례도 버린다.
    dropped += [CaseVerdict(h, "unjudged", "", "") for r, h in by_ref.items() if r not in judged]
    order = {h.ref: i for i, h in enumerate(hits)}
    kept.sort(key=lambda v: (v.verdict != "same_process", order[v.hit.ref]))
    return kept, dropped, True
