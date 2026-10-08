"""RAG (Retrieval-Augmented Generation) 헬퍼.

흐름:
  1) 임베딩 서비스(TEI, /v1/embeddings) 로 query 임베딩 생성.
  2) pgvector 의 documents 테이블에서 cosine 거리 기반 top-k 검색.
  3) chunk + 메타데이터 반환 → 호출자가 system prompt 에 부착하거나 출처 표기에 사용.

임베딩 호출은 TEI 의 OpenAI-호환 엔드포인트(`/v1/embeddings`) 사용 — input/text 둘 다 호환.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


EMBED_BASE_URL = os.environ.get("EMBED_BASE_URL", "http://embed:80").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))


# Lexical re-rank — cosine score 가 1~3순위에서 0.01~0.02 차이로 수렴할 때
# query 의 핵심 키워드(익스펜스/연차/카톡/점심 등 카테고리 단어)가 본문에 직접 등장하는
# chunk 를 위로 올린다. cosine 만으로는 "메일 송부 일반 규정" vs "익스펜스 CC 규정" 같은
# 카테고리 차이를 분간 못 해 정답 chunk 가 2~3순위로 밀리는 현상 방지.
_KW_TOKEN_RE = re.compile(r"[가-힣]{2,}|[a-zA-Z]{3,}")
_KW_STOP = {
    # 의문사·일반 동사 어미·인사말 등 정보 없는 토큰.
    "누구", "어디", "언제", "어떻게", "어떤", "무엇", "이거", "그것", "저것",
    "있나요", "합니까", "해야", "할까요", "해야하나요", "보내", "보낼", "넣어야",
    "들어가", "있어요", "합니다", "관련", "사람", "사항", "내용", "정보",
    "그래", "그래서", "그리고", "또한", "정확히", "대해", "대해서",
}
# Site alias — 사용자가 "구미공장" 이라고 물어도 chunk source_label 의 "구미" 와 매칭되도록 정규화.
# chunk 본문/label 에는 짧은 이름("대구", "구미")만 들어있는데 사용자는 흔히 긴 이름을 쓴다.
_SITE_ALIASES = {
    "구미공장": "구미",
    "구미본사": "구미",
    "대구본사": "대구",
    "대구공장": "대구",
}
_SITE_KEYWORDS = {"구미", "대구"}  # site 키워드는 보너스가 일반 키워드의 4배.

# Typo alias — chunk 본문에 오타가 있는 영문 단어를 query keyword 와 매칭시켜 정답 chunk
# 가 검색되도록 한다. 사칙 원본 엑셀의 오타를 직접 수정하지 않고 검색 시점에서 흡수.
# 예: chunk #194 본문 "이채진 주임(Vvian)" — 사용자 "Vivian" query 와 매칭 위해.
_TYPO_ALIASES = {
    "vivian": ("vvian",),  # chunk #194 (구미 준수사항 #9) 본문 오타
    "vvian": ("vivian",),
}

# 한국어 조사 — keyword 끝에 붙으면 제거. "파일명을" → "파일명", "메일에는" → "메일".
# 길이 긴 조사 우선 검사. 어간 길이 2 이상 보장(너무 잘리지 않게).
_KOREAN_PARTICLES = (
    "에서는", "에게서", "으로서", "으로써", "에서", "에는", "에게", "한테",
    "으로", "이라고", "라고", "이며", "이며,", "이라", "라는", "이라는",
    "을", "를", "이", "가", "은", "는", "의", "에", "와", "과", "도",
    "만", "랑", "로", "야", "여",
)
_LEXICAL_BONUS = 0.05               # 약한 신호: 짧은 keyword exact 또는 prefix 매칭.
_LEXICAL_BONUS_LONG_EXACT = 0.20    # 강한 신호: 3자 이상 한국어 keyword 가 chunk 본문에 exact
                                    # substring 매칭. "파일명"/"익스펜스"/"개인정보" 같은
                                    # specific 명사가 정답 chunk 와 직접 일치 → site 와 동등 보너스.
_SITE_BONUS = 0.20                  # site 키워드 (구미/대구) — cosine 0.05+ 격차도 뒤집음.


def _strip_particle(tok: str) -> str:
    """한국어 조사 제거. "파일명을" → "파일명", "메일에는" → "메일".
    어간 길이가 2 미만이 되면 원본 유지.
    """
    for p in sorted(_KOREAN_PARTICLES, key=len, reverse=True):
        if tok.endswith(p) and len(tok) - len(p) >= 2:
            return tok[: -len(p)]
    return tok


def _extract_query_keywords(query: str) -> set[str]:
    """query → 매칭 키워드 set.
    - stop word 제거
    - site keyword(구미/대구) 가 토큰 안에 substring 으로 들어 있으면 site 도 추가
    - 한국어 조사 제거된 어간만 keyword 로 (원본 토큰 + 어간 둘 다 넣으면 보너스 중복)
    - typo alias 추가 — query 의 "vivian" 이 chunk 의 "vvian" 과 매칭되도록
    """
    out: set[str] = set()
    for t in _KW_TOKEN_RE.findall(query):
        tl = t.lower()
        if tl in _KW_STOP:
            continue
        for site in _SITE_KEYWORDS:
            if site in tl:
                out.add(site)
        stem = _strip_particle(tl)
        if stem in _KW_STOP:
            continue
        out.add(stem)
        # typo alias — 영문 오타 보정 (chunk 본문의 오타와 매칭)
        for alias in _TYPO_ALIASES.get(stem, ()):
            out.add(alias)
    return out


def _kw_hit_kind(kw: str, hl: str) -> str:
    """매칭 종류 반환: 'exact' | 'prefix' | 'none'.
    - exact substring (강한 신호)
    - 한글 3자 이상 keyword 는 prefix 2자 매칭도 인정 (어미 흡수, 약한 신호)
    - 영문/숫자는 exact 만
    """
    if kw in hl:
        return "exact"
    if len(kw) >= 3 and not kw.isascii() and kw[:2] in hl:
        return "prefix"
    return "none"


def _kw_hit(kw: str, hl: str) -> bool:
    return _kw_hit_kind(kw, hl) != "none"


def _kw_match_bonus(kws: set[str], haystack: str) -> float:
    """매칭 가중치 합(=lexical bonus). exact vs prefix 차등.
    - site keyword exact: _SITE_BONUS (0.20)
    - 긴 한글(4자+) exact: _LEXICAL_BONUS_LONG_EXACT (0.15) — 카테고리 구분 키워드
    - 중간 한글(3자) exact: _LEXICAL_BONUS_LONG_EXACT (0.15) — 어간이 chunk 와 정확히 일치
    - 짧은 한글(2자)/영문 exact: _LEXICAL_BONUS (0.05)
    - prefix(어미 흡수): _LEXICAL_BONUS (0.05) — 약한 신호
    """
    hl = haystack.lower()
    bonus = 0.0
    for kw in kws:
        kind = _kw_hit_kind(kw, hl)
        if kind == "none":
            continue
        if kw in _SITE_KEYWORDS and kind == "exact":
            bonus += _SITE_BONUS
        elif kind == "exact" and len(kw) >= 3 and not kw.isascii():
            bonus += _LEXICAL_BONUS_LONG_EXACT
        else:
            bonus += _LEXICAL_BONUS
    return bonus


def _kw_match_count(kws: set[str], haystack: str) -> tuple[int, int]:
    """Legacy: (normal_count, site_count). min_score 컷 구제 판정."""
    hl = haystack.lower()
    normal = 0
    site = 0
    for kw in kws:
        if not _kw_hit(kw, hl):
            continue
        if kw in _SITE_KEYWORDS:
            site += 1
        else:
            normal += 1
    return normal, site


@dataclass
class RetrievedChunk:
    """검색 결과 한 row."""

    id: int
    content: str
    source_path: str
    source_label: str
    metadata: dict[str, Any]
    score: float  # cosine similarity (1.0 - cosine_distance)


async def embed_text(text_input: str) -> list[float]:
    """단일 텍스트 임베딩. TEI 의 /embed 엔드포인트.

    TEI 는 list[str] 입력을 받아 list[list[float]] 반환.
    """
    if not text_input.strip():
        raise ValueError("empty text")
    payload = {"inputs": [text_input]}
    timeout = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{EMBED_BASE_URL}/embed", json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"embed {r.status_code}: {r.text[:300]}")
    data = r.json()
    # TEI 응답: [[...vector...]]
    if isinstance(data, list) and data and isinstance(data[0], list):
        vec = data[0]
    elif isinstance(data, dict) and "embeddings" in data:
        vec = data["embeddings"][0]
    else:
        raise RuntimeError(f"unexpected embed response: {str(data)[:200]}")
    if len(vec) != EMBED_DIM:
        raise RuntimeError(f"embedding dim mismatch: got {len(vec)} expected {EMBED_DIM}")
    return vec


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """배치 임베딩 — 인제스트 시 사용."""
    if not texts:
        return []
    payload = {"inputs": texts}
    timeout = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{EMBED_BASE_URL}/embed", json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"embed batch {r.status_code}: {r.text[:300]}")
    data = r.json()
    if isinstance(data, list):
        return data  # type: ignore[return-value]
    if isinstance(data, dict) and "embeddings" in data:
        return data["embeddings"]  # type: ignore[return-value]
    raise RuntimeError(f"unexpected embed batch response: {str(data)[:200]}")


def _vec_literal(vec: list[float]) -> str:
    """pgvector 입력 리터럴 — '[v1,v2,...]'."""
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


async def search(
    db: AsyncSession,
    query: str,
    *,
    top_k: int = 5,
    domain: str | None = None,
    min_score: float = 0.0,
) -> list[RetrievedChunk]:
    """top_k 유사 chunk 검색.

    pgvector 의 cosine distance(`<=>`) 로 SQL-측 정렬. IVFFlat 인덱스 활용.
    score = 1 - cosine_distance (즉 cosine similarity).
    """
    qvec = await embed_text(query)
    qvec_lit = _vec_literal(qvec)

    # SQL fetch 는 lexical re-rank 후보를 더 풍부히 — 최소 50개.
    # cosine 만으로는 정답 chunk 가 10~20위 밖이지만 lexical/site 보너스로 1순위가 되는
    # 경우(예: C19 "퇴근할 때 확인" — 대구 Daily #4 cos=0.538 이 SQL top-20 밖)를 구제.
    fetch_k = max(top_k * 10, 50)

    where_clauses = ["embedding IS NOT NULL"]
    params: dict[str, Any] = {"qvec": qvec_lit, "k": fetch_k}
    if domain:
        where_clauses.append("domain = :domain")
        params["domain"] = domain
    where_sql = " AND ".join(where_clauses)

    sql = text(
        f"""
        SELECT id, content, source_path, source_label, metadata,
               1 - (embedding <=> CAST(:qvec AS vector)) AS score
        FROM documents
        WHERE {where_sql}
        ORDER BY embedding <=> CAST(:qvec AS vector)
        LIMIT :k
        """
    )
    res = await db.execute(sql, params)
    rows: list[RetrievedChunk] = []
    all_rows: list[RetrievedChunk] = []
    for row in res.mappings().all():
        score = float(row["score"]) if row["score"] is not None else 0.0
        all_rows.append(
            RetrievedChunk(
                id=int(row["id"]),
                content=row["content"],
                source_path=row["source_path"],
                source_label=row["source_label"],
                metadata=row["metadata"] or {},
                score=score,
            )
        )
    # min_score 컷 적용. 단 lexical 매칭으로 보너스 받는 chunk 는 cosine 약해도 살림 (영문
    # 이름 단독 query 같이 bge-m3 임베딩이 약한 케이스 대응).
    pre_kws = _extract_query_keywords(query)
    rows = []
    for r in all_rows:
        if r.score >= min_score:
            rows.append(r)
            continue
        if pre_kws:
            normal, site = _kw_match_count(pre_kws, r.content + " " + r.source_label)
            if normal + site > 0:  # lexical 매칭 chunk 는 min_score 컷에서 구제
                rows.append(r)

    # Lexical re-rank — query 키워드 매칭 chunk 를 위로 올림. cosine score 는 그대로
    # 보존(UI 출처 칩 표시용), 정렬 순서만 변경.
    # haystack 에 source_label 까지 포함 — "대구본사"/"구미" 같은 site 단어는 chunk
    # content 가 아니라 label/metadata 에만 들어 있으므로, content 만 보면 site 인지가
    # 안 돼서 다른 사이트 chunk 가 1순위로 오르는 케이스가 생긴다 (eval: lunch_time_daegu).
    kws = _extract_query_keywords(query)
    if kws and rows:
        def _rerank_key(r: RetrievedChunk) -> float:
            return r.score + _kw_match_bonus(kws, r.content + " " + r.source_label)
        rows.sort(key=_rerank_key, reverse=True)

    # Content dedupe — 양 사이트(대구/구미) 가 완전히 동일한 본문을 가질 때 (예: 파일명
    # 규칙, 연차 보고 양식 등 전사 공통 정책) 두 chunk 가 같이 들어가면 자동 multi-site
    # 감지가 잘못 트리거되어 모델이 "구미는 X, 대구는 X" 같은 어색한 양쪽 답변을 만든다.
    # 정규화(공백 압축) 후 동일하면 우선순위 높은 chunk 만 남기고 다음을 드롭.
    if rows:
        seen: set[str] = set()
        deduped: list[RetrievedChunk] = []
        for r in rows:
            norm = re.sub(r"\s+", "", r.content)
            if norm in seen:
                continue
            seen.add(norm)
            deduped.append(r)
        rows = deduped

    # 최종 호출자 요청 top_k 로 자름 (SQL fetch_k 는 넓게 가져왔지만 표시는 사용자 의도대로).
    return rows[:top_k]


async def upsert_document(
    db: AsyncSession,
    *,
    source_path: str,
    source_label: str,
    content: str,
    metadata: dict[str, Any],
    domain: str = "all",
    embedding: list[float] | None = None,
) -> int:
    """단일 chunk 업서트. (source_path, source_label) UNIQUE 키 충돌 시 갱신.
    embedding 은 pgvector 의 vector(1024) 컬럼에 캐스팅 입력.
    """
    emb_lit = _vec_literal(embedding) if embedding is not None else None
    # CAST(... AS vector) 단일 표현 — NULL 도 vector 로 캐스팅되어 NULL 그대로 저장.
    # CASE WHEN 분기는 asyncpg 가 NULL 가지의 타입을 추론하지 못해 AmbiguousParameterError 발생.
    sql = text(
        """
        INSERT INTO documents (source_path, source_label, content, metadata, domain, embedding)
        VALUES (:source_path, :source_label, :content, CAST(:metadata AS jsonb),
                CAST(:domain AS user_domain), CAST(:embedding AS vector))
        ON CONFLICT (source_path, source_label) DO UPDATE SET
            content = EXCLUDED.content,
            metadata = EXCLUDED.metadata,
            domain = EXCLUDED.domain,
            embedding = EXCLUDED.embedding,
            updated_at = NOW()
        RETURNING id
        """
    )
    import json
    res = await db.execute(
        sql,
        {
            "source_path": source_path,
            "source_label": source_label,
            "content": content,
            "metadata": json.dumps(metadata, ensure_ascii=False),
            "domain": domain,
            "embedding": emb_lit,
        },
    )
    return int(res.scalar_one())


_PRIMARY_GAP = 0.04  # 1순위 score 와 차이가 이 값을 넘는 chunk 는 prompt 에서 제외.
_PRIMARY_MAX = 1     # PRIMARY 최대 N 개. eval set 검색 정확도 21/21 로 lexical re-rank
                     # 가 신뢰할 만하므로 1순위만 prompt 에 부착해 합성 환각을 원천 차단.
                     # (eval expense_mail_cc 케이스: PRIMARY=3 일 때 모델이 [chunk 2/3]
                     # 일반 메일 규정의 sales@unde.co.kr 를 답에 합침. MAX=1 로 차단.)
                     # 출처 칩(sources 응답)은 search() top_k 전체가 그대로 노출되므로 UI 변화 없음.


def build_context_block(chunks: list[RetrievedChunk], query: str = "") -> str:
    """검색된 chunk 중 **답변 근거가 되는 PRIMARY 만** system prompt 에 직렬화.

    히스토리:
      v8: REFERENCE chunk 를 prompt 에서 완전 제외. PRIMARY 만 부착. 합성 환각 차단.
      v9: PRIMARY_MAX 2→3 완화 + lexical re-rank 도입 → 정답 chunk 1순위 안착.
      v9b: lexical re-rank 정확도 100% 검증 후 PRIMARY_MAX 3→1 로 축소 — 1순위 1개만
           prompt 에 넣어 환각 원천 차단.
      v11: query 에 site keyword(구미/대구) 2개 이상 → **multi-site 비교 의도**로 보고
           PRIMARY_MAX 를 동적으로 2 로 확장. 사용자가 "구미/대구 다르게 알려줘" 같이
           양쪽을 동시에 묻는 경우 각 사이트 chunk 가 한 prompt 에 들어가야 모델이
           "구미는 X, 대구는 Y" 식으로 구분 답변 가능.

    PRIMARY 산출 규칙:
      - 1순위 score 기준 _PRIMARY_GAP 이내인 chunk 만
      - 동률이라도 최대 primary_max 개 (multi-site 의도면 2, 아니면 _PRIMARY_MAX)
    """
    if not chunks:
        return ""

    # 동적 PRIMARY_MAX — 두 경로로 multi-site 감지:
    #   (1) query 에 site keyword(구미, 대구) 2개 이상 직접 등장 — "구미와 대구 점심시간"
    #   (2) top-2 chunk 가 같은 카테고리의 서로 다른 site + score 차가 작음 — query 에
    #       site 키워드 없이 "점심시간 언제?" 처럼 모호하게 물어도 검색기는 양쪽 사이트의
    #       점심시간 chunk 를 비슷한 score 로 가져오므로 자동 감지 가능.
    primary_max = _PRIMARY_MAX
    is_multi_site = False
    if query:
        qkws = _extract_query_keywords(query)
        site_hits = sum(1 for k in qkws if k in _SITE_KEYWORDS)
        if site_hits >= 2:
            primary_max = max(primary_max, 2)
            is_multi_site = True
    if not is_multi_site and len(chunks) >= 2:
        c1, c2 = chunks[0], chunks[1]
        m1, m2 = (c1.metadata or {}), (c2.metadata or {})
        s1, s2 = m1.get("site"), m2.get("site")
        cat1, cat2 = m1.get("category"), m2.get("category")
        if s1 and s2 and s1 != s2 and cat1 == cat2 and abs(c1.score - c2.score) <= 0.05:
            primary_max = max(primary_max, 2)
            is_multi_site = True

    # search() 가 lexical re-rank 까지 적용해 보낸 순서를 그대로 사용한다.
    top_score = chunks[0].score
    if is_multi_site:
        # multi-site 의도: 각 사이트별 1순위 chunk 1개씩 prompt 부착.
        # top-2 가 같은 site 이면 다른 site chunk 가 prompt 에서 빠지는 문제 해결
        # (K50 "대구·구미 청소 비교" — top-1/2 가 모두 대구이면 구미 정보 누락).
        primary: list[RetrievedChunk] = []
        seen_sites: set[str] = set()
        for c in chunks:
            site = (c.metadata or {}).get("site")
            if site is None or site in seen_sites:
                continue
            primary.append(c)
            seen_sites.add(site)
            if len(primary) >= primary_max:
                break
        if not primary:
            primary = [chunks[0]]
    else:
        primary = [c for c in chunks if (top_score - c.score) <= _PRIMARY_GAP][:primary_max]

    if is_multi_site:
        lines = [
            "## 사내 자료 (검색된 사칙 — 사이트별 비교)",
            "",
            "사용자가 **구미와 대구 양쪽을 동시에** 묻고 있다. 아래 chunk 들은 각 사이트의",
            "관련 규정이다. 답변 시 다음을 **엄격히** 지킨다.",
            "",
            "1. **각 사이트별로 따로 답한다.** 예: \"구미는 11:30~12:30, 대구는 12:00~13:00\".",
            "2. 두 사이트의 정보를 **합치거나 동일하다고 단정하지 않는다.** chunk 본문에",
            "   적힌 그대로 인용한다.",
            "3. chunk 본문에 없는 정보를 추측·덧붙이지 않는다.",
            "4. 답변에 사용한 chunk 의 출처 라벨 `[...]` 를 사이트별로 붙인다.",
            "",
            "──── 검색된 chunk ────",
        ]
    else:
        lines = [
            "## 사내 자료 (검색된 사칙)",
            "",
            "아래 chunk 는 검색기 + lexical re-rank 로 선정된 1순위 chunk 다.",
            "답변 작성 시 다음을 지킨다.",
            "",
            "0. **사용자가 메일 작성·양식·발송을 요청하면 chunk 본문을 직접 풀어쓰지 말고",
            "   `compose_leave_email` (작성) 또는 `send_leave_email` (발송) 도구를 호출한다.**",
            "   이 자료(chunk)는 정책 참고용일 뿐, 메일 본문 생성 책임은 도구가 맡는다.",
            "1. **사용자 질문에 직접 답하는 부분만** chunk 에서 추출해 답한다.",
            "   질문이 좁게 묻는 정보(예: \"메일 제목 양식\")라면 그 부분만 1~3 문장으로 짧게.",
            "   chunk 본문을 그대로 복사하지 말 것 — 질문 범위를 벗어난 정보(부수 절차, 추가",
            "   조항 등)는 답에 넣지 않는다.",
            "2. 질문의 어휘와 chunk 의 어휘가 약간 달라도(예: \"공유\"↔\"발설\", \"외부 소프트웨어\"↔",
            "   \"기타 소프트웨어\") 의미상 같은 것을 다루면 답한다.",
            "3. chunk 본문에 없는 정보를 추측해서 덧붙이지 않는다.",
            "4. **이전 대화의 양식·예시·인물 정보(예: 연차 메일 제목 양식, CC 인원 목록 등)를",
            "   현재 질문에 응용하지 말 것.** 현재 질문의 답은 오직 위 chunk 본문에서만 나온다.",
            "   예: 이전에 \"연차 메일 양식\" 을 답했어도, 현재 질문이 \"출퇴근 지문\" 이면 연차 양식을",
            "   응용해 \"지문 미발생 보고\" 같은 가짜 양식을 만들지 않는다.",
            "5. chunk 가 질문 주제와 **완전히 무관**할 때만 다음과 같이 답한다:",
            "   \"사칙에 명시된 내용이 없습니다.\"",
            "6. 답변 끝에 사용한 chunk 의 출처 라벨 `[...]` 를 붙인다.",
            "",
            "──── 검색된 chunk ────",
        ]
    for i, c in enumerate(primary, 1):
        lines.append(f"\n[chunk {i}] [{c.source_label}]")
        lines.append(c.content.strip())
    lines.append("\n──── (끝) ────")
    return "\n".join(lines)
