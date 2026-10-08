"""제안서 본문 생성(S5) — 요청 원문 + 요구 항목 + 관련성 판정을 통과한 사례 → 섹션별 본문(JSON).

원칙(docs/design/proposal_automation_design.md §1·§6): "숫자와 고유명사는 코드가, 문장은 모델이".
  - 모델은 서술 섹션만 쓴다. 참고 사례(사례명·기업·KPI·출처), 요구사항 대응표, 확인 필요 목록은 코드가 채운다.
  - 요청 원문에 없는 수치는 지우고 "(수치 확인 필요)" 로 바꾼다. 사례 수치는 사례 번호를 밝힌 문장에서만 허용.
  - 빠진 섹션은 1회 보완 요청, 그래도 없으면 "(수기 작성 필요)" 로 두고 나머지를 완성한다.
  - 요청 반영 검증·전문가 검토·표절 제거는 quality.py 가 draft 와 finalize 사이에서 한다.

섹션 흐름은 실물 제안서(한화오션 PoC, docs/Hanwha_…pdf)의 논지 흐름을 따른다:
핵심 메시지 → 현황·과제 → 리스크 → 단계적 제안 → 구성 → 성공 기준 → 요구사항 대응표 → 사례 → 확인 항목 → 다음 단계.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .. import proposal_llm
from ..company_context import COMPANY_NAME
from ..diagram import rulecheck
from ..diagram.llm_spec import _extract_json
from .case_guard import CaseVerdict
from .requirements import KIND_LABEL, Requirement

_log = logging.getLogger("proposal.body")

# (id, 제목, 쓰는 법). 모델이 쓰는 서술 섹션 — 순서대로.
LLM_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("key_message", "제안의 핵심 메시지",
     "카드 3개: ① 고객 목표 ② 유엔디 제안 ③ 고객 결정 포인트. banner_label '핵심'."),
    ("situation", "고객 현황과 과제",
     "현재 작업 방식과 문제점을 카드 2~3개로. 요청 원문에 적힌 사실만 쓴다."),
    ("risk", "자동화 전 검증해야 할 기술 리스크",
     "이 공정에서 실패할 수 있는 요인 3~4개. 리스크 하나당 카드 하나(heading=리스크 이름, "
     "bullets=왜 문제인지·어떻게 확인할지). 여러 리스크를 카드 하나에 몰아 쓰지 않는다. "
     "banner_label '접근 방향'."),
    ("solution", "제안 방향: 단계적 추진",
     "1단계~3(또는 4)단계 카드. 각 단계의 목적과 검증 내용. 먼저 실패하면 안 되는 기반 조건부터. "
     "요청이 검토·제안을 요구한 사항(대수 산정, 검사 방법 등)에 대한 접근 방법을 여기와 system 에서 답한다. "
     "banner_label '목표'."),
    ("system", "시스템 구성(안)",
     "로봇, 툴·그리퍼(EOAT), 비전·센서, 주변 설비, 제어·연동 블록을 카드로. "
     "원문에 없는 모델명·사양은 쓰지 말고 '선정 필요' 또는 '확인 필요'로 쓴다."),
    ("effects", "성공 기준과 기대 효과",
     "카드 2개: ① 성공 기준(측정 항목 이름만, 목표 수치는 쓰지 않음) ② 기대 효과(정성 서술). "
     "유사 사례 수치를 쓰려면 반드시 '유사 사례(사례 2024-02)에서는 …' 처럼 사례 번호를 붙여서."),
    ("next_steps", "다음 단계 제안",
     "현장 실사, 자료 요청, 검증 범위 합의 등 고객과 할 다음 액션 3개 카드. 기간은 원문에 없으면 쓰지 않는다."),
)
REQUIRED_IDS = tuple(sid for sid, _, _ in LLM_SECTIONS)
PLACEHOLDER = "(수기 작성 필요)"
NUMBER_MASK = "(수치 확인 필요)"

_SYSTEM = """너는 {company}(로봇 자동화 SI)의 수석 기술영업이다. 로봇 자동화 제안서를 수백 건 써 왔고,
고객 요청을 한 줄도 빠뜨리지 않으면서 현장 조건에 맞는 현실적인 제안을 쓰는 것으로 신뢰받는다.
[고객 요청 원문]을 바탕으로 제안서 본문 초안을 JSON 으로 쓴다.
자사 제품 스위칭 마그네틱 툴체인저 '맥봇(Magbot)'은 요청이 공구·그리퍼 교체나 다품종 툴 전환을 다룰 때만 언급한다.

작성 원칙
1. [요구 항목]은 전부 본문 어딘가에서 다룬다. 사실·수치는 원문 그대로 쓰고, 검토·제안 요청에는
   "어떻게 검토·결정할지"를 구체적으로 답한다. 판단할 근거가 없으면 "확인 필요"로 쓰고 open_questions 에 올린다.
2. 요청 원문에 없는 사실·수치·장비 모델명·일정·가격·고객사명은 쓰지 않는다.
3. 제안 방향은 오직 [고객 요청 원문]에서 정한다. [참고 사례]는 다른 기업의 사례이며, 사례마다 적힌
   "참고 관점"에 해당하는 부분만 참고한다. 공정이 다른 사례의 공정·설비 구성·단계는 가져오지 않는다.
   사례 문장을 옮겨 쓰지 않는다(표절 금지). 사례 수치는 "유사 사례(사례 2024-02)에서는 …" 처럼 번호를 붙여서만.
4. 어느 제안서에나 들어갈 일반론("생산성 향상", "경쟁력 강화"만 있는 문장)은 쓰지 않는다. 문장마다 이 고객의
   공정·제품·조건이 드러나야 한다.
5. 톤: 무리하게 약속하지 않고 "먼저 실패하면 안 되는 조건을 검증한 뒤 단계적으로 넓힌다"는 신뢰 중심.
   과장·홍보 문구("최고의", "혁신적인", "완벽한", "획기적인")는 쓰지 않는다.
6. 짧은 개조식(~함, ~필요, ~확인). bullet 하나 70자 이내, 카드당 bullet 2~4개. headline 은 한 문장.

sections — 아래 id 를 이 순서대로 모두 쓴다:
{outline}

JSON 형식:
{{"title": "제안서 제목", "subtitle": "한 줄 포지셔닝",
  "customer": "원문에 고객사명이 있으면 그대로, 없으면 빈 문자열",
  "sections": [{{"id": "key_message", "title": "섹션 제목", "headline": "한 줄 요지",
                "cards": [{{"heading": "카드 제목", "bullets": ["..."]}}],
                "banner_label": "핵심", "banner": "하단 강조 한 문장"}}],
  "case_insights": [{{"case_ref": "2024-02", "insight": "이번 제안에 참고한 점 1~2문장(참고 관점 안에서)"}}],
  "open_questions": ["고객에게 확인할 질문"]}}"""

_REPAIR = """다음 섹션이 빠졌거나 내용이 비어 있습니다: {ids}
같은 작성 원칙으로 이 섹션들만 다시 쓰세요. JSON: {{"sections": [ ... ]}}"""

_VERDICT_LABEL = {"same_process": "같은 공정", "shared_problem": "공정 다름·관점만 참고"}
_CASE_PROMPT_CHARS = 1500

# 단위가 붙은 수치 — 근거 확인 대상. '1단계'·'3차' 같은 순서 표기는 제외.
_NUM_UNIT_RE = re.compile(
    r"(?<![\d.,A-Za-z])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(%|퍼센트|kg|㎏|톤|mm|cm|m/s|m|초|분|시간|개월|주|일|년|대|명|만\s?원|억\s?원|억|원|개|EA|ea|°C|℃|kW|W|V|Hz|fps|ppm|배|회|종|pcs)"
    r"(?![A-Za-z])"
)
_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?")
_UNIT_ALIAS = {"㎏": "kg", "℃": "°C", "퍼센트": "%", "EA": "개", "ea": "개", "pcs": "개"}
_CITE_RE = re.compile(r"사례\s*#?\s*\d+")
COPY_WINDOW = 20        # 공백·문장부호 제거 후 이 길이 이상 참고 원문과 같으면 복사로 본다
_NORM_RE = re.compile(r"[\s\W_]+")


@dataclass
class Card:
    heading: str
    bullets: list[str]


@dataclass
class Section:
    id: str
    title: str
    headline: str
    cards: list[Card]
    banner_label: str = ""
    banner: str = ""
    placeholder: bool = False
    table: list[list[str]] | None = None     # 첫 행 = 머리글. 요구사항 대응표 등 코드가 만드는 표


@dataclass
class ProposalBody:
    title: str
    subtitle: str
    customer: str
    sections: list[Section]
    case_insights: dict[str, str] = field(default_factory=dict)   # 사례 ref("2024-02") → 시사점
    open_questions: list[str] = field(default_factory=list)


@dataclass
class BodyReport:
    masked_numbers: list[str] = field(default_factory=list)   # "섹션: 원래 수치"
    copied: list[str] = field(default_factory=list)           # 다시 써도 참고 원문과 겹쳐 뺀 문장
    rewritten: int = 0                                        # 표절 의심으로 다시 쓴 문장 수
    repaired: list[str] = field(default_factory=list)         # 보완 요청으로 채운 섹션
    placeholders: list[str] = field(default_factory=list)     # 끝내 비어 수기 작성으로 남긴 섹션
    review_rounds: int = 0                                    # 요청 반영·전문가 검토 보완 횟수
    review_issues: list[str] = field(default_factory=list)    # 검토에서 지적돼 고친 문제
    uncovered: list[str] = field(default_factory=list)        # 끝내 본문에 반영 안 된 요구 항목
    leaked_terms: list[str] = field(default_factory=list)     # 보완 후에도 남은 참고 사례 고유 용어
    dropped_requirements: list[str] = field(default_factory=list)  # 원문 근거 없어 버린 추출 항목
    dropped_cases: list[str] = field(default_factory=list)    # 관련성 판정으로 뺀 사례
    case_judge_failed: bool = False


# ── 프롬프트 ────────────────────────────────────────────────────────────────

def _reference_block(verdicts: list[CaseVerdict]) -> str:
    lines = ["(다른 기업의 공개 사례. 사례별 '참고 관점'만 참고하고 문장을 옮겨 쓰지 않는다.)"]
    for v in verdicts:
        h = v.hit
        body = "\n".join(f"[{s}]\n{h.sections[s]}" for s in ("개요", "기존 공정의 문제점", "컨설팅 결과")
                         if h.sections.get(s))
        if len(body) > _CASE_PROMPT_CHARS:
            body = body[:_CASE_PROMPT_CHARS].rstrip() + " …(이하 생략)"
        lines.append(f"\n### 사례 {h.ref} {h.title} — {h.company}\n"
                     f"판정: {_VERDICT_LABEL.get(v.verdict, v.verdict)} / 참고 관점: {v.aspect}\n{body}")
    return "\n".join(lines)


def build_messages(
    request: str, reqs: list[Requirement], verdicts: list[CaseVerdict], examples: str = ""
) -> list[dict[str, str]]:
    outline = "\n".join(f"- {sid} | {title} | {how}" for sid, title, how in LLM_SECTIONS)
    system = _SYSTEM.format(company=COMPANY_NAME, outline=outline)
    parts = [f"[고객 요청 원문]\n{request}"]
    ai_reqs = [r for r in reqs if r.source == "ai"]
    if ai_reqs:
        parts.append("[요구 항목] — 전부 다뤄야 함\n" + "\n".join(
            f"- ({KIND_LABEL.get(r.kind, r.kind)}) {r.text}  〔원문: {r.evidence}〕" for r in ai_reqs))
    if verdicts:
        parts.append("[참고 사례]\n" + _reference_block(verdicts))
    else:
        parts.append("[참고 사례]\n(관련 사례 없음 — case_insights 는 빈 배열로 둔다)")
    if examples:
        # 담당자가 확정한 과거 제안서 — 구성·표기·밀도의 기준(confirmed.get_confirmed_examples).
        parts.append(examples)
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]


# ── 파싱·직렬화 ─────────────────────────────────────────────────────────────

def _s(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def parse_section(d: dict[str, Any]) -> Section | None:
    sid = _s(d.get("id"))
    if sid not in REQUIRED_IDS:
        return None
    cards = []
    for c in d.get("cards") or []:
        if not isinstance(c, dict):
            continue
        bullets = [_s(b) for b in (c.get("bullets") or []) if _s(b)]
        if _s(c.get("heading")) or bullets:
            cards.append(Card(_s(c.get("heading")), bullets))
    default_title = next(t for i, t, _ in LLM_SECTIONS if i == sid)
    return Section(sid, _s(d.get("title")) or default_title, _s(d.get("headline")), cards,
                   _s(d.get("banner_label")), _s(d.get("banner")))


def is_complete(s: Section | None) -> bool:
    return s is not None and bool(s.headline) and any(c.bullets for c in s.cards)


def parse_body(data: dict[str, Any]) -> tuple[ProposalBody, list[str]]:
    """모델 JSON → ProposalBody. 반환 두 번째 = 빠졌거나 빈 섹션 id."""
    by_id: dict[str, Section] = {}
    for d in data.get("sections") or []:
        if isinstance(d, dict) and (s := parse_section(d)) and is_complete(s):
            by_id.setdefault(s.id, s)
    insights: dict[str, str] = {}
    for d in data.get("case_insights") or []:
        if isinstance(d, dict) and _s(d.get("case_ref")) and _s(d.get("insight")):
            insights[_s(d.get("case_ref")).lstrip("#")] = _s(d.get("insight"))
    body = ProposalBody(
        title=_s(data.get("title")), subtitle=_s(data.get("subtitle")), customer=_s(data.get("customer")),
        sections=[by_id[i] for i in REQUIRED_IDS if i in by_id],
        case_insights=insights,
        open_questions=[_s(q) for q in (data.get("open_questions") or []) if _s(q)],
    )
    return body, [i for i in REQUIRED_IDS if i not in by_id]


def body_from_dict(d: dict[str, Any]) -> ProposalBody:
    """asdict(ProposalBody) 역변환 — 확정 기록(proposal_records.content)·저장 본문을 다시 렌더할 때."""
    sections = [Section(id=s["id"], title=s["title"], headline=s["headline"],
                        cards=[Card(c["heading"], list(c["bullets"])) for c in s.get("cards", [])],
                        banner_label=s.get("banner_label", ""), banner=s.get("banner", ""),
                        placeholder=s.get("placeholder", False), table=s.get("table"))
                for s in d.get("sections", [])]
    return ProposalBody(title=d.get("title", ""), subtitle=d.get("subtitle", ""), customer=d.get("customer", ""),
                        sections=sections, case_insights=dict(d.get("case_insights", {})),
                        open_questions=list(d.get("open_questions", [])))


def replace_sections(body: ProposalBody, new: list[Section]) -> list[str]:
    """같은 id 의 완성된 섹션으로 교체. 반환 = 교체된 id."""
    by_id = {s.id: s for s in new if is_complete(s)}
    done = []
    for i, s in enumerate(body.sections):
        if s.id in by_id:
            body.sections[i] = by_id[s.id]
            done.append(s.id)
    return done


def sections_json(body: ProposalBody, ids: tuple[str, ...] | list[str] = REQUIRED_IDS) -> str:
    """모델에게 보여 줄 현재 서술 섹션(JSON)."""
    out = [{"id": s.id, "title": s.title, "headline": s.headline,
            "cards": [{"heading": c.heading, "bullets": c.bullets} for c in s.cards],
            "banner_label": s.banner_label, "banner": s.banner}
           for s in body.sections if s.id in ids and not s.placeholder]
    return json.dumps(out, ensure_ascii=False)


def body_text(body: ProposalBody) -> str:
    """요청 반영 판정용 본문 글자(서술 섹션만). 확인 질문(open_questions)은 넣지 않는다 —
    "로봇 2대 운용 동선?" 을 되묻는 것은 '확인 필요'이지 '로봇 대수 검토'에 답한 것이 아니다."""
    lines = [body.title, body.subtitle]
    for s in body.sections:
        lines += [f"[{s.title}]", s.headline, s.banner]
        for c in s.cards:
            lines.append(c.heading)
            lines += c.bullets
    return "\n".join(x for x in lines if x)


def requirement_label(r: Requirement) -> str:
    """사람이 읽는 항목명. 원문 표기 항목은 정규화된 토큰("1.200mm") 대신 원문 구절을 보인다."""
    return r.text if r.source == "ai" else f"원문 표기 '{r.evidence}'"


# ── 코드 점검 ───────────────────────────────────────────────────────────────

def _key(num: str, unit: str = "") -> str:
    unit = _UNIT_ALIAS.get(unit, unit)
    return f"{num.replace(',', '')}|{re.sub(r'\s+', '', unit).lower()}"


def _numbers_in(text: str) -> set[str]:
    """문장 속 수치 키 집합. 단위가 붙은 수치는 '숫자|단위', 단위 없는 숫자는 '숫자|'."""
    keys: set[str] = set()
    with_unit: set[int] = set()
    for m in _NUM_UNIT_RE.finditer(text):
        keys.add(_key(m.group(1), m.group(2)))
        with_unit.add(m.start(1))
    for m in _NUM_RE.finditer(text):
        if m.start() not in with_unit:
            keys.add(_key(m.group(0)))
    return keys


def mask_unsupported_numbers(text: str, request_nums: set[str], case_nums: set[str]) -> tuple[str, list[str]]:
    """근거 없는 '수치+단위'를 NUMBER_MASK 로 바꾼다. 반환: (고친 문장, 가린 수치들).

    근거 = 요청 원문에 같은 숫자·같은 단위가 있음(요청에 단위 없이 적힌 숫자는 단위 무관 인정),
    또는 사례 원문에 있고 같은 문장이 사례 번호를 밝힘. 요청의 "로봇 2대" 가 본문의 "2명 절감" 을
    정당화하지 않도록 단위까지 맞춘다.
    """
    masked: list[str] = []
    cited = bool(_CITE_RE.search(text))

    def repl(m: re.Match[str]) -> str:
        k, bare = _key(m.group(1), m.group(2)), _key(m.group(1))
        if k in request_nums or bare in request_nums or (cited and k in case_nums):
            return m.group(0)
        masked.append(m.group(0))
        return NUMBER_MASK

    return _NUM_UNIT_RE.sub(repl, text), masked


def norm_text(s: str) -> str:
    return _NORM_RE.sub("", s)


def shingles(text: str, n: int = COPY_WINDOW) -> set[str]:
    t = norm_text(text)
    return {t[i : i + n] for i in range(len(t) - n + 1)}


def copied(text: str, source_shingles: set[str], n: int = COPY_WINDOW) -> bool:
    t = norm_text(text)
    return any(t[i : i + n] in source_shingles for i in range(len(t) - n + 1))


def texts(body: ProposalBody):
    """본문의 모든 모델 작성 문장을 (섹션 제목, getter, setter) 로 순회. setter(None) = 삭제."""
    yield "제목", lambda: body.title, lambda v: setattr(body, "title", v or "")
    yield "제목", lambda: body.subtitle, lambda v: setattr(body, "subtitle", v or "")
    for s in body.sections:
        if s.table is not None:
            continue  # 코드가 만든 표
        yield s.title, (lambda s=s: s.headline), (lambda v, s=s: setattr(s, "headline", v or ""))
        yield s.title, (lambda s=s: s.banner), (lambda v, s=s: setattr(s, "banner", v or ""))
        for c in s.cards:
            yield s.title, (lambda c=c: c.heading), (lambda v, c=c: setattr(c, "heading", v or ""))
            for i in range(len(c.bullets)):
                yield s.title, (lambda c=c, i=i: c.bullets[i]), (lambda v, c=c, i=i: c.bullets.__setitem__(i, v))
    for n in list(body.case_insights):
        yield "참고 사례", (lambda n=n: body.case_insights[n]), (lambda v, n=n: body.case_insights.__setitem__(n, v))
    for i in range(len(body.open_questions)):
        yield "확인 필요 사항", (lambda i=i: body.open_questions[i]), (lambda v, i=i: body.open_questions.__setitem__(i, v))


def drop_deleted(body: ProposalBody) -> None:
    """setter(None) 으로 지운 문장을 실제로 걷어낸다."""
    for s in body.sections:
        for c in s.cards:
            c.bullets = [b for b in c.bullets if b]
    body.case_insights = {k: v for k, v in body.case_insights.items() if v}
    body.open_questions = [q for q in body.open_questions if q]


SPEC_MASK = "(사양 확인 필요)"
# 등급·규격 표기 — 숫자+단위가 아니라 수치 점검에 안 걸린다. 실측: 요청에 없는 "IP69K 등급 로봇"이 본문에 들어감.
_SPEC_RE = re.compile(r"\b(?:[Ii][Pp]\s?\d{2}[Kk]?|[Cc]lass\s?\d+|ISO\s?\d{3,5}(?:-\d+)?|SUS\s?\d{3}L?|"
                      r"Cat(?:egory)?\.?\s?[1-4]|SIL\s?[1-4]|PL\s?[a-e])\b")
# 안전 성능 등급 "PL d" 는 소문자만 — 대소문자를 무시하면 "PLC"(PL + C)가 걸린다(실측 오탐).


def mask_unsupported_specs(text: str, request: str) -> tuple[str, list[str]]:
    """요청 원문에 없는 등급·규격 표기(IP69K, Class 100, ISO 13849, SUS316L …)를 SPEC_MASK 로."""
    req = re.sub(r"\s+", "", request).lower()
    masked: list[str] = []

    def repl(m: re.Match[str]) -> str:
        if re.sub(r"\s+", "", m.group(0)).lower() in req:
            return m.group(0)
        masked.append(m.group(0))
        return SPEC_MASK

    return _SPEC_RE.sub(repl, text), masked


def mask_numbers(body: ProposalBody, request: str, verdicts: list[CaseVerdict], report: BodyReport) -> None:
    request_nums = _numbers_in(request)
    case_nums = _numbers_in("\n".join(t for v in verdicts for t in v.hit.sections.values()))
    for where, get, put in texts(body):
        text = get()
        if not text:
            continue
        fixed, masked = mask_unsupported_numbers(text, request_nums, case_nums)
        fixed, specs = mask_unsupported_specs(fixed, request)
        if masked or specs:
            put(fixed)
            report.masked_numbers += [f"{where}: {m}" for m in masked + specs]


# ── 코드가 채우는 섹션 ──────────────────────────────────────────────────────

def compact_kpis(kpis: list[dict[str, str]]) -> str:
    """사례 KPI 한 줄 — "생산성 12.5% 증가 · 작업인원 3명 감소". 라벨의 단위 괄호("(%)", "(명)")는 뺀다.
    방향은 사례집 원문 그대로 둔다(원문에 "불량률 38% 증가"처럼 적힌 곳이 있어 추정해 바꾸지 않는다)."""
    out = []
    for k in kpis:
        if not k.get("value"):
            continue
        label = re.sub(r"\s*\([^)]*\)\s*$", "", k["label"]).strip()
        val = "N/A" if k["value"] == "N/A" else f"{k['value']}{k.get('unit', '')}"
        out.append(f"{label} {val} {k.get('direction', '')}".strip())
    return " · ".join(out)


def reference_section(verdicts: list[CaseVerdict], insights: dict[str, str]) -> Section | None:
    if not verdicts:
        return None
    cards = []
    for v in verdicts:
        h = v.hit
        bullets = [f"업종: {h.industry}"] if h.industry else []
        bullets.append(f"참고 관점({_VERDICT_LABEL.get(v.verdict, v.verdict)}): {v.aspect}")
        kpi = compact_kpis(h.kpis)
        if kpi:
            bullets.append(f"해당 기업 도입 효과: {kpi}")
        if h.ref in insights:
            bullets.append(f"참고한 점: {insights[h.ref]}")
        bullets.append(f"출처: {h.citation}")
        cards.append(Card(f"사례 {h.ref} {h.title} — {h.company}", bullets))
    return Section(
        "references", "유사 공정 참고 사례",
        f"공개 컨설팅 사례집에서 관련성을 확인한 사례 {len(verdicts)}건 — 검토 관점 참고용",
        cards, "유의", "사례 수치는 해당 기업의 결과이며 본 제안의 약속이 아닙니다.",
    )


def requirements_section(reqs: list[Requirement]) -> Section | None:
    """고객 요구사항 대응표 — 요청의 모든 항목이 어디에 반영됐는지(또는 안 됐는지) 보이게 한다."""
    rows = [["요구사항", "원문", "제안 반영", "상태"]]
    for r in reqs:
        if r.source == "rule" and r.covered:
            continue  # 원문 표기(수치·장비명) 대조 항목은 빠졌을 때만 표에 올린다
        if r.covered:
            status, where = "반영", f"{r.where}: {r.response}".strip(": ")
        elif r.kind == "question":
            status, where = "확인 필요", "현장 확인 필요 사항에 등록"
        else:
            status, where = "미반영 — 담당자 보완 필요", ""
        label = r.text if r.source == "ai" else "원문 표기"
        rows.append([f"({KIND_LABEL.get(r.kind, r.kind)}) {label}", r.evidence, where, status])
    if len(rows) == 1:
        return None
    # 표에 싣는 행(주요 요구사항 + 빠진 원문 표기)과 머리말 숫자가 어긋나지 않게 둘을 나눠 쓴다
    # (디자인판 숫자 타일이 표 행 수를 세므로 "요청 32건"과 "17건"이 한 장에 같이 보이는 일이 있었다).
    ai = [r for r in reqs if r.source == "ai"]
    rule = [r for r in reqs if r.source == "rule"]
    ai_ok, rule_ok = sum(r.covered for r in ai), sum(r.covered for r in rule)
    main = (f"주요 요구사항 {len(ai)}건 모두 반영" if ai_ok == len(ai)
            else f"주요 요구사항 {len(ai)}건 중 {ai_ok}건 반영 — 나머지는 상태 칸 참조")
    headline = main + (f" · 원문 수치·장비 표기 {len(rule)}건 중 {rule_ok}건 확인" if rule else "")
    return Section("requirements", "고객 요구사항 대응표", headline, [], table=rows)


def open_items_section(reqs: list[Requirement], questions: list[str]) -> Section | None:
    """본문에서 답하지 못한 확인 요청(맨 앞) + 모델이 제기한 확인 질문. 본문에서 이미 답한 요청은
    되풀이하지 않는다(전체 목록은 요구사항 대응표에 있다)."""
    seen: set[str] = set()
    bullets: list[str] = []
    pending = [requirement_label(r) for r in reqs if r.kind == "question" and not r.covered]
    for q in pending + questions:
        key = norm_text(q)
        if key and key not in seen:
            seen.add(key)
            bullets.append(q)
    if not bullets:
        return None
    return Section("open_items", "현장 확인 필요 사항", "제안 확정 전에 고객과 확인할 항목입니다.",
                   [Card("확인 항목", bullets)])


def placeholder_section(sid: str) -> Section:
    title = next(t for i, t, _ in LLM_SECTIONS if i == sid)
    return Section(sid, title, PLACEHOLDER, [Card(PLACEHOLDER, [])], placeholder=True)


# ── 생성 ────────────────────────────────────────────────────────────────────

async def draft_body(
    request: str, reqs: list[Requirement], verdicts: list[CaseVerdict], report: BodyReport, *,
    examples: str = "",
) -> tuple[ProposalBody, list[dict[str, str]]]:
    """본문 초안 → 누락 섹션 보완(1회). 반환: (본문, 생성에 쓴 메시지 — 이후 보완 호출이 이어 씀)."""
    messages = build_messages(request, reqs, verdicts, examples)
    raw = await proposal_llm.chat(messages, fmt="json", temperature=0.3)
    body, missing = parse_body(_extract_json(raw))
    if missing:
        _log.info("[proposal] 누락 섹션 보완 요청: %s", missing)
        fix = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": _REPAIR.format(ids=", ".join(missing))},
        ]
        try:
            extra, still = parse_body(_extract_json(
                await proposal_llm.chat(fix, fmt="json", temperature=0.3)))
        except ValueError:
            extra, still = None, missing
        if extra is not None:
            have = {s.id for s in body.sections}
            body.sections += [s for s in extra.sections if s.id not in have]
            report.repaired = [s.title for s in extra.sections if s.id in missing]
        missing = [i for i in missing if i in still]
    for sid in missing:
        body.sections.append(placeholder_section(sid))
        report.placeholders.append(next(t for i, t, _ in LLM_SECTIONS if i == sid))
    body.sections.sort(key=lambda s: REQUIRED_IDS.index(s.id))
    return body, messages


def finalize_body(
    body: ProposalBody, request: str, reqs: list[Requirement], verdicts: list[CaseVerdict],
    report: BodyReport,
) -> None:
    """수치 근거 가림 + 코드 섹션 조립(대응표·참고 사례·확인 항목). next_steps 를 맨 끝에 둔다."""
    mask_numbers(body, request, verdicts, report)
    known = {v.hit.ref for v in verdicts}
    body.case_insights = {n: v for n, v in body.case_insights.items() if n in known}
    tail = body.sections.pop() if body.sections and body.sections[-1].id == "next_steps" else None
    for extra in (requirements_section(reqs), reference_section(verdicts, body.case_insights),
                  open_items_section(reqs, body.open_questions)):
        if extra:
            body.sections.append(extra)
    if tail:
        body.sections.append(tail)


def rule_covered(req: Requirement, normalized_body: str) -> bool:
    """글자 대조 — 원문 표기 토큰(수치·장비명·공정 단계 핵심어)이 본문에 있는가."""
    hits = sum(rulecheck._has(normalized_body, m) for m in req.must)
    return hits >= (req.min_hits or len(req.must))
