"""컨설팅 사례집 RAG 검색 — 제안서 작성 시 유사 공정 사례를 근거로 인용하기 위한 헬퍼.

인제스트: app.ingest_casebook (사례 × 섹션 chunk, domain='sales').
검색은 섹션 chunk 단위로 하고 사례 단위로 묶어 반환한다. 제안서 LLM 에는 사례 전체
(개요·문제점·결과·KPI)를 넣어야 "문제 → 해결 구성 → 효과" 흐름을 참고할 수 있기 때문.

실행(검색 확인):
    docker exec und_cortex_backend python -m app.casebook "AMR 물류 이송 자동화"
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .ingest_casebook import DOMAIN, SOURCE_PATHS
from .rag import _extract_query_keywords, search

# 제안서 참고용 섹션 순서. 인터뷰는 소감 위주라 기본 제외(검색 매칭에는 쓰임).
_PROMPT_SECTIONS = ("개요", "기존 공정의 문제점", "컨설팅 결과")
# 제목 일치가 없을 때 채택할 cosine 하한. 2026-09-28 실측: 사례집 밖 질의(주유소 4족 순찰)
# 최고 0.512, 사례집 안 질의의 1순위 0.53~0.69 → 그 사이.
_STRONG_SCORE = 0.55
# 제목 일치로 구제할 때도 요구하는 cosine 하한 — 단어 하나 우연 일치로 무관한 사례가 올라오지 않게.
# 실측 정답 중 최저: "비전 검사 불량 선별" → 세라믹 검사공정 0.475.
_TITLE_MIN_SCORE = 0.45
# 거의 모든 사례 제목에 들어 있어 공정 구분력이 없는 단어.
_GENERIC_KW = {"로봇", "자동화", "공정", "시스템", "구축", "생산", "생산공정", "제조", "활용", "도입"}


@dataclass
class CaseHit:
    year: int
    case_no: int
    source_path: str
    doc_title: str
    title: str
    company: str
    industry: str
    printed_pages: str
    score: float                              # 이 사례에서 가장 높게 매칭된 섹션의 cosine
    matched_sections: list[str]
    kpis: list[dict[str, str]]
    sections: dict[str, str] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        """사례 식별자 "2024-02" — 사례집이 여러 권이라 번호만으로는 겹친다."""
        return f"{self.year}-{self.case_no:02d}"

    @property
    def citation(self) -> str:
        return f"{self.doc_title} 사례 #{self.case_no:02d} ({self.company}, {self.printed_pages}쪽)"


async def _load_sections(
    db: AsyncSession, source_path: str, case_no: int
) -> dict[str, tuple[str, dict[str, Any]]]:
    res = await db.execute(
        text(
            "SELECT content, metadata FROM documents "
            "WHERE source_path = :p AND (metadata->>'case_no')::int = :n"
        ),
        {"p": source_path, "n": case_no},
    )
    return {r["metadata"]["section"]: (r["content"], r["metadata"]) for r in res.mappings().all()}


def _strip_head(content: str) -> str:
    """chunk 앞의 '[컨설팅 사례 N] …\\n[섹션]\\n' 머리말 제거 — 프롬프트에선 사례 머리글을 따로 단다."""
    parts = content.split("\n", 2)
    return parts[2] if len(parts) == 3 and parts[1].startswith("[") else content


def _title_hits(query: str, title: str) -> int:
    """질의의 공정 키워드(용접·디팔렛타이징·머신텐딩 …)가 사례 제목에 그대로 나오는 개수.

    업종 설명은 보지 않는다 — 긴 나열이라 일반 명사가 우연히 걸린다(2026-09-28 실측: 약액 요청의
    "센터 잡은 후"가 임플란트 사례 업종 "임상교육센터"에, "PLC 제어"가 "로봇 제어기"에 걸려 1순위로 올라옴).
    """
    hay = title.lower()
    kws = {k for k in _extract_query_keywords(query) if len(k) >= 2 and k not in _GENERIC_KW}
    return sum(1 for k in kws if k in hay)


async def search_cases(
    db: AsyncSession,
    query: str,
    *,
    top_cases: int = 3,
) -> list[CaseHit]:
    """질의(고객 공정·요구사항 요약)와 유사한 사례 최대 top_cases 건. 없으면 빈 리스트.

    채택 조건: 공정 키워드가 사례 제목에 직접 나오고 cosine ≥ _TITLE_MIN_SCORE, 또는 cosine ≥ _STRONG_SCORE.
    cosine 만으로는 "숙련공 부족" 같은 일반 문제 서술이 공정 종류를 덮어 용접 질의에
    볼팅 사례가 앞서고, 사례집 밖 공정(주유소 순찰 등)에도 0.5 안팎으로 무관한 사례가 걸린다.
    """
    chunks = await search(db, query, top_k=40, domain=DOMAIN, min_score=0.0)
    grouped: dict[tuple[str, int], CaseHit] = {}
    for c in chunks:
        md = c.metadata
        if c.source_path not in SOURCE_PATHS or "case_no" not in md:
            continue  # 지원사업 안내 쪽·다른 sales 문서
        n = int(md["case_no"])
        hit = grouped.get((c.source_path, n))
        if hit is None:
            hit = grouped[(c.source_path, n)] = CaseHit(
                year=int(md.get("year", 0)),
                case_no=n,
                source_path=c.source_path,
                doc_title=md.get("doc", ""),
                title=md.get("title", ""),
                company=md.get("company", ""),
                industry=md.get("industry", ""),
                printed_pages=md.get("printed_pages", ""),
                score=c.score,
                matched_sections=[],
                kpis=[],
            )
        hit.matched_sections.append(md.get("section", ""))
        hit.score = max(hit.score, c.score)
    ranked = sorted(
        ((_title_hits(query, h.title) > 0 and h.score >= _TITLE_MIN_SCORE, h) for h in grouped.values()),
        key=lambda t: (t[0], t[1].score),
        reverse=True,
    )
    picked = [h for titled, h in ranked if titled or h.score >= _STRONG_SCORE][:top_cases]
    for hit in picked:
        for sec, (content, md) in (await _load_sections(db, hit.source_path, hit.case_no)).items():
            hit.sections[sec] = _strip_head(content)
            if md.get("kpis"):
                hit.kpis = md["kpis"]
    return picked


def build_reference_block(hits: list[CaseHit], *, max_chars_per_case: int = 2500) -> str:
    """제안서 LLM 프롬프트에 붙일 참고 사례 블록.

    사례의 수치(KPI·중량·C/T)는 그 기업의 결과이지 우리 제안의 약속이 아니므로,
    인용할 때 출처를 밝히고 고객 수치로 옮겨 쓰지 않도록 안내문을 함께 단다.
    """
    if not hits:
        return ""
    lines = [
        "## 참고: 유사 공정 로봇 컨설팅 사례 (외부 공개 사례집)",
        "- 아래는 다른 기업의 사례다. 문제 정의·해결 구성·검토 관점을 참고하되 문장을 복사하지 않는다.",
        "- 사례의 수치(KPI, 중량, 사이클타임, 투자회수 등)를 고객 제안의 기대효과로 옮겨 쓰지 않는다.",
        "  인용이 필요하면 '유사 사례(사례 2024-02)에서는 …' 처럼 사례 번호를 밝힌다.",
    ]
    for h in hits:
        lines.append(f"\n### 사례 {h.ref} {h.title} — {h.company} ({h.industry})")
        lines.append(f"출처: {h.citation}")
        body = []
        for sec in _PROMPT_SECTIONS:
            if h.sections.get(sec):
                body.append(f"[{sec}]\n{h.sections[sec]}")
        text_ = "\n".join(body)
        if len(text_) > max_chars_per_case:
            text_ = text_[:max_chars_per_case].rstrip() + " …(이하 생략)"
        lines.append(text_)
    return "\n".join(lines)


async def _main(query: str) -> None:
    from .database import SessionLocal

    async with SessionLocal() as db:
        hits = await search_cases(db, query)
    if not hits:
        print("(유사 사례 없음)")
        return
    for h in hits:
        print(f"{h.ref} {h.score:.3f} {h.title} — {h.company} | 매칭 {h.matched_sections}")
        print("    KPI:", json.dumps(h.kpis, ensure_ascii=False))
    print("\n" + build_reference_block(hits)[:1500])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(_main(" ".join(sys.argv[1:]) or "AMR 물류 이송 자동화"))
