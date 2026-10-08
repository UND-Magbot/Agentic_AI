"""본문 품질 보증 — 요청 반영 검증 + 전문가 검토 + 보완, 표절 제거.

제안서에서 가장 중요한 것은 "요청한 사항이 전부 제대로 들어갔는가"와 그 내용의 품질이다.
초안 한 번으로는 보장되지 않는다(개념도 실측: 12b 가 스스로 "전부 반영" 판정하고 항목을 빠뜨림). 그래서:

  1) 대조 — 원문 표기 항목(수치·장비명·공정 단계)은 코드가 글자로 대조하고, 의미 항목은 검토 모델이 판정한다.
     모델의 "반영" 판정도 항목 원문의 수치·영문 토큰이 본문에 없으면 뒤집는다.
  2) 전문가 검토 — 같은 호출에서 주제 이탈·요청과 모순·근거 없는 단정·일반론 문장을 찾는다.
  3) 보완 — 빠진 항목과 지적 사항을 주고 해당 섹션만 다시 쓰게 한다(최대 MAX_ROUNDS 회).
  4) 표절 — 참고 원문(사례집·확정본)과 COPY_WINDOW 자 이상 겹치는 문장은 다시 쓰게 하고,
     그래도 겹치면 문장을 뺀다. 표절이 남은 문서는 내보내지 않는다.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .. import proposal_llm
from ..diagram import rulecheck
from ..diagram.llm_spec import _extract_json
from .body import (
    REQUIRED_IDS, BodyReport, ProposalBody, Section, body_text, copied, drop_deleted, parse_section,
    replace_sections, requirement_label, rule_covered, sections_json, shingles, texts,
)
from .requirements import KIND_LABEL, Requirement

_log = logging.getLogger("proposal.quality")

MAX_ROUNDS = 2

_REVIEW_PROMPT = """너는 로봇 자동화 제안서를 수백 건 검토해 온 수석 기술영업이다. 발송 전 최종 검토를 한다.

1) [요구 항목] 각각이 [제안서 본문]에서 제대로 다뤄졌는지 판정하라.
   - covered: 본문이 그 항목을 구체적으로 다루면 true. 단어만 스치듯 나오거나 일반론이면 false.
     사실·수치 항목은 원문 값이 그대로 드러나야 true. 검토·제안 요청은 "어떻게 검토·결정할지"가 있어야 true.
   - where: 다룬 섹션 제목. response: 본문이 그 항목에 어떻게 답하는지 한 줄 요약(본문에 있는 내용만).
2) 본문의 문제 문장을 찾아라(없으면 빈 배열). 종류(problem):
   - 주제 이탈: 요청과 다른 공정·설비·목표를 다룸
   - 모순: 요청 원문과 어긋남
   - 근거 없는 단정: 원문에 없는 사양·성능·일정·효과를 사실처럼 씀
   - 일반론: 이 고객 공정이 드러나지 않아 어느 제안서에나 들어갈 문장
   section 은 섹션 id(아래 목록 중 하나), text 는 문제 문장 그대로, fix 는 고칠 방향.

섹션 id: {ids}
JSON: {{"items": [{{"id": "a1", "covered": true, "where": "...", "response": "..."}}],
        "issues": [{{"section": "risk", "problem": "일반론", "text": "...", "fix": "..."}}]}}"""

_REVISE_PROMPT = """아래 제안서 섹션을 고쳐라. 같은 작성 원칙을 지킨다:
요청 원문에 없는 사실·수치·모델명·일정은 쓰지 않고, 모르면 "확인 필요"로 쓴다. 참고 사례 문장을 옮기지 않는다.

[고객 요청 원문]
{request}

[본문에 빠졌거나 부족한 요구 항목] — 가장 알맞은 섹션에 구체적으로 넣는다
{missing}

[검토 지적 사항] — 해당 문장을 고친다
{issues}

[현재 섹션(JSON)]
{sections}

고친 섹션만 같은 형식으로 돌려준다(다른 섹션은 넣지 않는다).
JSON: {{"sections": [{{"id": "...", "title": "...", "headline": "...", "cards": [{{"heading": "...", "bullets": ["..."]}}], "banner_label": "...", "banner": "..."}}]}}"""

_REWRITE_PROMPT = """아래 문장들은 참고 자료(다른 기업 사례·과거 문서)의 표현과 겹친다. 뜻은 유지하되
참고 자료의 표현을 쓰지 말고 이 제안서의 고객 상황에 맞게 완전히 새로 써라. 수치·고유명사는 바꾸지 않는다.

[고객 요청 원문]
{request}

[문장]
{items}

JSON: {{"rewrites": [{{"i": 0, "text": "새 문장"}}]}}"""


def _req_line(r: Requirement) -> str:
    return f"- {r.id} ({KIND_LABEL.get(r.kind, r.kind)}) {r.text}  〔원문: {r.evidence}〕"


def _tokens_present(r: Requirement, normalized_body: str) -> bool:
    """모델 '반영' 판정 교차 확인 — 항목 원문의 수치·영문 토큰·인증 등급(Class 100)·기한이 본문에 있어야 한다.
    (실측: "Class 100 가능 여부"를 모델이 반영이라 했지만 본문엔 "Class 사양 확인"만 있고 100 이 빠짐.)"""
    must = [it.must[0] for it in rulecheck.extract(r.evidence)
            if it.kind in ("fact", "question", "deliverable") and it.must]
    return all(rulecheck._has(normalized_body, m) for m in must)


async def _review(request: str, reqs: list[Requirement], body: ProposalBody) -> tuple[dict[str, Any], list[dict]]:
    """반환: (id → 판정, 지적 사항)."""
    ai_reqs = [r for r in reqs if r.source == "ai"]
    user = (f"[고객 요청 원문]\n{request}\n\n[요구 항목]\n" + "\n".join(_req_line(r) for r in ai_reqs)
            + f"\n\n[제안서 본문]\n{body_text(body)}")
    raw = await proposal_llm.chat(
        [{"role": "system", "content": _REVIEW_PROMPT.format(ids=", ".join(REQUIRED_IDS))},
         {"role": "user", "content": user}],
        fmt="json", temperature=0.0,
    )
    data = _extract_json(raw)
    judged = {str(d.get("id")): d for d in data.get("items") or [] if isinstance(d, dict)}
    issues = [d for d in data.get("issues") or []
              if isinstance(d, dict) and d.get("section") in REQUIRED_IDS and str(d.get("text") or "").strip()]
    return judged, issues


# 섹션별 최소 카드 수 — 모델이 여러 항목을 카드 하나에 몰아 쓰면(실측: 리스크 4개를 카드 1장에) 보완시킨다.
MIN_CARDS = {"key_message": 3, "situation": 2, "risk": 3, "solution": 3, "system": 2, "effects": 2, "next_steps": 2}
# 사례 제목·업종에서 공정 구분력이 없는 단어 — 이 말이 본문에 나와도 사례 용어 유출이 아니다.
_COMMON_TERMS = {"공정", "생산", "생산공정", "제조", "자동화", "로봇", "시스템", "구축", "제품", "활용", "검사",
                 "이송", "적재", "조립", "가공", "라인", "설비", "부품", "제작", "공급", "처리", "관련", "기타",
                 # 업종 설명에 흔한 일반 명사(실측 오탐: "덴탈 케어용 소재"의 '소재'가 "비부식성 소재"에 걸림)
                 "소재", "환경", "품질", "제조업", "전문", "방식", "구조", "장비", "용기", "사업", "개발",
                 "판매", "기능성", "산업용", "자동차", "전자", "식품", "기계", "제조판매"}
_TERM_RE = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")


SHRINK_RATIO = 0.7   # 보완본의 항목(bullet) 수가 원래의 이 비율 미만이면 내용이 줄어든 것으로 본다


def shrinks(body: ProposalBody, new: Section) -> bool:
    """보완본이 원래 섹션보다 내용을 크게 줄이는가. 실측: 어묵 요청의 '시스템 구성'이 보완 중
    카드 4장(로봇·그리퍼·비전·주변 설비) → 1장(주변 설비)으로 줄어 요청 내용이 사라짐. 그런 보완은 받지 않는다."""
    old = next((s for s in body.sections if s.id == new.id), None)
    if old is None or old.placeholder:
        return False
    need_cards = min(MIN_CARDS.get(new.id, 1), len(old.cards))
    old_b = sum(len(c.bullets) for c in old.cards)
    new_b = sum(len(c.bullets) for c in new.cards)
    if len(new.cards) < need_cards or new_b < old_b * SHRINK_RATIO:
        _log.info("[proposal] 내용이 줄어든 보완본 거부: %s 카드 %d→%d, 항목 %d→%d",
                  new.id, len(old.cards), len(new.cards), old_b, new_b)
        return True
    return False


def structure_issues(body: ProposalBody) -> list[dict]:
    """코드로 잡는 구성 문제(카드 수 부족)."""
    out = []
    for s in body.sections:
        need = MIN_CARDS.get(s.id)
        if need and not s.placeholder and len(s.cards) < need:
            out.append({"section": s.id, "problem": "구성",
                        "text": f"카드 {len(s.cards)}개 — 항목을 카드 하나에 몰아 씀",
                        "fix": f"항목마다 카드를 나눠 {need}개 이상으로(카드 제목 = 항목 이름)"})
    return out


def case_term_issues(body: ProposalBody, verdicts, request: str) -> list[dict]:
    """참고 사례 고유 용어(제목·업종 단어 중 요청에 없는 말)가 본문에 들어간 곳 — 사례가 방향을 끄는 신호.
    (다른 세션 실측: 공정만 공유한 세라믹 검사 사례 때문에 개념도 제목이 "세라믹 카트리지/보틀 …"로 나옴.)"""
    req = rulecheck.normalize(request)
    terms: set[str] = set()
    for v in verdicts:
        for w in _TERM_RE.findall(f"{v.hit.title} {v.hit.industry}"):
            if w not in _COMMON_TERMS and rulecheck.normalize(w) not in req:
                terms.add(w)
    out = []
    for s in body.sections:
        if s.table is not None:
            continue
        lines = [s.headline, s.banner] + [t for c in s.cards for t in [c.heading, *c.bullets]]
        for t in lines:
            hit = [w for w in terms if w in t]
            if hit and s.id in REQUIRED_IDS:
                out.append({"section": s.id, "problem": "주제 이탈",
                            "text": t, "fix": f"참고 사례 용어({', '.join(sorted(hit))})를 빼고 이 고객 공정 기준으로"})
    return out


def apply_coverage(reqs: list[Requirement], body: ProposalBody, judged: dict[str, Any]) -> None:
    """판정 결과를 요구 항목에 반영(코드 대조 + 모델 판정의 교차 확인)."""
    nb = rulecheck.normalize(body_text(body))
    for r in reqs:
        if r.source == "rule":
            r.covered = rule_covered(r, nb)
            continue
        d = judged.get(r.id) or {}
        r.covered = bool(d.get("covered")) and _tokens_present(r, nb)
        r.where = str(d.get("where") or "").strip() if r.covered else ""
        r.response = str(d.get("response") or "").strip() if r.covered else ""


async def review_and_revise(
    request: str, reqs: list[Requirement], body: ProposalBody, report: BodyReport,
    *, verdicts=(), max_rounds: int = MAX_ROUNDS,
) -> None:
    """요청 반영 검증·전문가 검토(+ 코드 구성·사례 용어 검사) → 보완(최대 max_rounds).
    reqs 의 covered/where/response 를 채운다."""
    for rnd in range(max_rounds + 1):
        try:
            judged, issues = await _review(request, reqs, body)
        except Exception as e:  # 검토 실패 — 코드 대조만으로 상태를 남긴다
            _log.warning("[proposal] 검토 호출 실패: %r", e)
            judged, issues = {}, []
        issues = structure_issues(body) + case_term_issues(body, verdicts, request) + issues
        apply_coverage(reqs, body, judged)
        missing = [r for r in reqs if not r.covered]
        if (not missing and not issues) or rnd == max_rounds:
            break
        issue_lines = [f"- [{d['section']}] ({d.get('problem', '')}) \"{d['text']}\" → {d.get('fix', '')}"
                       for d in issues]
        prompt = _REVISE_PROMPT.format(
            request=request,
            missing="\n".join(_req_line(r) for r in missing) or "(없음)",
            issues="\n".join(issue_lines) or "(없음)",
            sections=sections_json(body),
        )
        try:
            raw = await proposal_llm.chat([{"role": "user", "content": prompt}],
                                          fmt="json", temperature=0.2)
            new = [s for d in _extract_json(raw).get("sections") or []
                   if isinstance(d, dict) and (s := parse_section(d))]
        except Exception as e:
            _log.warning("[proposal] 보완 호출 실패: %r", e)
            break
        changed = replace_sections(body, [n for n in new if not shrinks(body, n)])
        report.review_rounds += 1
        report.review_issues += [f"{d.get('problem', '')}: {d['text']}" for d in issues
                                 if d["section"] in changed]
        if not changed:
            break
    report.uncovered = [requirement_label(r) for r in reqs if not r.covered and r.kind != "question"]
    report.leaked_terms = [f"{d['section']}: {d['text']}" for d in case_term_issues(body, verdicts, request)]


async def remove_plagiarism(
    body: ProposalBody, sources: list[str], request: str, report: BodyReport,
) -> None:
    """참고 원문과 겹치는 문장 → 다시 쓰기 1회 → 그래도 겹치면 삭제. 요청 원문과 겹치는 건 표절이 아니다
    (고객이 쓴 요구를 그대로 옮기는 것)."""
    src = "\n".join(s for s in sources if s)
    if not src.strip():
        return
    req_sh = shingles(request)
    src_sh = shingles(src) - req_sh
    flagged = [(where, get, put) for where, get, put in texts(body) if get() and copied(get(), src_sh)]
    if not flagged:
        return
    items = "\n".join(f"{i}. {get()}" for i, (_, get, _) in enumerate(flagged))
    rewrites: dict[int, str] = {}
    try:
        raw = await proposal_llm.chat(
            [{"role": "user", "content": _REWRITE_PROMPT.format(request=request, items=items)}],
            fmt="json", temperature=0.4,
        )
        for d in _extract_json(raw).get("rewrites") or []:
            if isinstance(d, dict) and str(d.get("text") or "").strip():
                rewrites[int(d.get("i", -1))] = str(d["text"]).strip()
    except Exception as e:
        _log.warning("[proposal] 표절 문장 재작성 실패 → 해당 문장 삭제: %r", e)
    for i, (where, get, put) in enumerate(flagged):
        new = rewrites.get(i)
        if new and not copied(new, src_sh):
            put(new)
            report.rewritten += 1
        else:
            report.copied.append(f"{where}: {get()}")
            put(None)
    drop_deleted(body)


def plagiarism_sources(verdicts, examples: str) -> list[str]:
    """표절 대조 원문 — 판정을 통과한 사례 전문 + 확정본 예시."""
    return [t for v in verdicts for t in v.hit.sections.values()] + [examples]
