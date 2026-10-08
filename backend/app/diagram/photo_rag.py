"""설비 사진 RAG 색인 · 검색.

개념도 품질을 좌우하는 것은 결국 실제 설비 사진이다. 이 모듈이 그 조달을 맡는다.

색인 경로:
    사진 → 제안서 모델 비전(판정·한국어 캡션) → 부적합이면 버림
         → MinIO 원본 보관 + 로컬 캐시
         → documents 테이블 색인 (domain='design', 기존 검색 파이프라인 재사용)

검색 경로:
    station(kind/label) → 한국어 질의문 구성 → 임베딩 검색 → 설비종류 가산점 → ImageRef

사내 규정 문서(domain='all')와 섞이지 않도록 domain 을 'design' 으로 격리하고,
metadata.kind 로 한 번 더 거른다.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .. import proposal_llm, rag
from .spec import ConceptMapSpec, ImageRef, Station

PHOTO_DOMAIN = "design"
PHOTO_KIND = "equipment_photo"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "equipment_photos"
MAX_EDGE = 1400          # 저장·VLM 입력 공통 상한

# station kind → 검색 질의에 쓸 한국어 표현. 사진 캡션도 한국어라 매칭이 붙는다.
KIND_KO: dict[str, str] = {
    "conveyor": "컨베이어 롤러 반송 라인",
    "capper": "캐핑기 뚜껑 체결 스크류 캡 장비",
    "robot": "협동로봇 로봇팔 이송 매니퓰레이터",
    "filler": "액체 충전기 주입 노즐 필링 장비",
    "tank": "스테인리스 탱크 저장조 약액 공급",
    "reject": "불량 배출 리젝 적치대",
    "outfeed": "완제품 배출 컨베이어 포장 라인",
    "table": "작업대 테이블 설비",
    "camera": "머신비전 카메라 산업용 카메라",
}

_VISION_PROMPT = """이미지를 보고 JSON 하나만 출력하세요. 설명·코드펜스 금지.

{
  "observed": "이미지에 실제로 보이는 것만 한국어로 서술. 없는 것을 추측해 넣지 말 것",
  "medium": "photo|drawing|painting|diagram|3d_render|screenshot",
  "is_monochrome": true 또는 false,
  "is_equipment": true 또는 false,
  "station_kind": "conveyor|capper|robot|filler|tank|reject|outfeed|table|camera|none",
  "confidence": 0.0 ~ 1.0,
  "caption": "한국어 한 문장 설명",
  "tags": ["한국어", "키워드", "3~6개"]
}

반드시 observed 를 먼저 채운 뒤, 거기 적은 내용만 근거로 나머지를 판단하세요.

판정 기준 — 엄격하게 적용할 것
- medium: 카메라로 찍은 사진만 "photo". 판화·삽화·회화는 "painting" 또는 "drawing",
  화학 분자구조나 CG 는 "3d_render", 도면·차트는 "diagram".
- is_equipment: 현대 산업 현장의 기계·설비·장치가 주 피사체일 때만 true.
  건물 외관·터널·구조물·풍경·인물·포장된 제품·실험기구는 false.
- station_kind: 무엇인지 확실할 때만 고른다. 애매하면 "none".
- confidence: 설비 종류를 얼마나 확신하는지. 확신이 없으면 0.5 미만으로 낮춘다.
- caption 과 tags 는 한국어로 쓴다.

추측하지 마세요. 보이지 않는 것을 지어내면 안 됩니다."""

# 이 값들을 통과해야 색인한다. gemma3 는 "산업 설비인가?" 를 물으면 거의 모두 예라고
# 답하는 긍정 편향이 있어, 매체 종류와 확신도로 한 번 더 조인다.
_MIN_CONFIDENCE = 0.40


@dataclass
class PhotoVerdict:
    is_equipment: bool
    station_kind: str
    caption: str
    tags: list[str]
    usable: bool
    raw: dict[str, Any]
    observed: str = ""
    medium: str = ""
    monochrome: bool = False
    confidence: float = 0.0

    def reject_reason(self) -> str:
        """색인에서 제외할 이유. 통과하면 빈 문자열.

        gemma3 비전의 한계를 감안한 기준이다. 제품 렌더링 이미지(3d_render)는 배경이
        깨끗해 오히려 개념도에 쓰기 좋으므로 통과시키고, 손으로 그린 그림·도표만 배제한다.
        채도는 판정에 쓰지 않는다 — 스테인리스 설비와 흰 배경 제품샷은 원래 채도가
        낮아서, 흑백 기록사진과 구분되지 않는다(monochrome 은 메타데이터로만 남긴다).
        """
        if self.medium in ("drawing", "painting", "diagram"):
            return f"사진 아님({self.medium})"
        if not self.is_equipment:
            return "설비 사진 아님"
        if self.station_kind == "none":
            return "설비 종류 불명"
        if self.confidence < _MIN_CONFIDENCE:
            return f"확신 부족({self.confidence:.2f})"
        return ""


def _normalize(data: bytes) -> tuple[bytes, int, int]:
    """긴 변을 MAX_EDGE 로 줄이고 PNG 로 통일. (bytes, w, h)"""
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    if max(im.size) > MAX_EDGE:
        im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), im.width, im.height


def measure_saturation(data: bytes) -> float:
    """평균 채도(0~255). 흑백·세피아 판별용 — VLM 보다 이쪽이 정확하다."""
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((140, 140))
        px = list(im.getdata())
        return sum(max(r, g, b) - min(r, g, b) for r, g, b in px) / max(len(px), 1)
    except Exception:
        return 99.0


def _extract_json(raw: str) -> dict[str, Any]:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        a, b = s.find("{"), s.rfind("}")
        if a >= 0 and b > a:
            return json.loads(s[a : b + 1])
    raise ValueError("비전 응답에서 JSON 을 찾지 못했습니다.")


async def describe_photo(image: bytes) -> PhotoVerdict:
    """제안서 모델(비전)로 사진을 판정하고 한국어 캡션을 만든다.

    외부에서 긁어온 사진에는 분자구조 렌더·책 삽화 같은 것이 섞인다. 파일명이나
    검색어를 믿지 않고 사진 자체를 보고 거른다.
    """
    b64 = base64.b64encode(image).decode()
    # 단순 분류라 사고 모드는 끈다. 모델·재시도는 제안서 전용 경로를 쓴다.
    content = await proposal_llm.chat(
        [{"role": "user", "content": _VISION_PROMPT, "images": [b64]}],
        fmt="json", think=False, temperature=0.1, num_ctx=8192, num_predict=512,
    )

    d = _extract_json(content)
    kind = str(d.get("station_kind") or "none").strip().lower()
    # 모델 답변 대신 실측값을 쓴다.
    measured_mono = measure_saturation(image) < 14.0
    if kind not in KIND_KO:
        kind = "none"
    tags = [str(t).strip() for t in (d.get("tags") or []) if str(t).strip()][:8]
    try:
        conf = float(d.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return PhotoVerdict(
        is_equipment=bool(d.get("is_equipment")),
        station_kind=kind,
        caption=str(d.get("caption") or "").strip(),
        tags=tags,
        usable=bool(d.get("usable", True)),
        raw=d,
        observed=str(d.get("observed") or "").strip(),
        medium=str(d.get("medium") or "").strip().lower(),
        monochrome=measured_mono,
        confidence=max(0.0, min(1.0, conf)),
    )


def _search_text(verdict: PhotoVerdict, extra: str = "") -> str:
    """임베딩 대상 텍스트. 질의문(설비 한국어 표현)과 어휘를 맞춰 둔다."""
    parts = [
        verdict.caption,
        " ".join(verdict.tags),
        KIND_KO.get(verdict.station_kind, ""),
        extra,
    ]
    return " / ".join(p for p in parts if p)


async def index_photo(
    db: AsyncSession,
    *,
    image: bytes,
    filename: str,
    license_info: dict[str, str] | None = None,
    force_kind: str = "",
    cutout_mode: str = "auto",
) -> dict[str, Any]:
    """사진 1장을 판정·저장·색인한다.

    Returns:
        {"indexed": bool, "reason": str, "kind": str, "caption": str, ...}
    """
    data, w, h = _normalize(image)
    verdict = await describe_photo(data)

    # 설비 종류는 파일명 > VLM 순으로 믿는다. 실측 결과 gemma3 는 캐핑기를 tank 로,
    # 로봇을 conveyor 로 보는 등 종류 판정이 자주 틀린다. 캡션 생성에만 의존한다.
    guessed = kind_from_filename(filename)
    if force_kind and force_kind in KIND_KO:
        verdict.station_kind = force_kind
    elif guessed:
        verdict.station_kind = guessed
        verdict.confidence = max(verdict.confidence, _MIN_CONFIDENCE)
    reason = verdict.reject_reason()
    if reason:
        return {
            "indexed": False,
            "reason": reason,
            "kind": verdict.station_kind,
            "caption": verdict.caption,
            "observed": verdict.observed,
            "file": filename,
        }

    digest = hashlib.sha1(data).hexdigest()[:16]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{digest}.png"
    cache_path.write_bytes(data)

    # 배경 제거 — 원본은 항상 남기고, 결과가 멀쩡할 때만 누끼본을 쓴다.
    cutout_name = ""
    cutout_method = ""
    if cutout_mode != "none":
        from . import cutout as cutout_mod

        cut, how = cutout_mod.remove_background(data, method=cutout_mode)
        ratio = cutout_mod.transparent_ratio(cut)
        # 거의 다 지웠거나(설비까지 날림) 거의 안 지웠으면(실패) 원본을 쓴다.
        if 0.05 <= ratio <= 0.85 and how not in ("failed", "none"):
            cut_path = CACHE_DIR / f"{digest}_cut.png"
            cut_path.write_bytes(cut)
            cutout_name = cut_path.name
            cutout_method = f"{how}({ratio:.2f})"
        else:
            cutout_method = f"skip({how},{ratio:.2f})"

    lic = license_info or {}
    object_key = f"equipment_photos/{digest}.png"
    stored = False
    try:
        from .. import storage

        storage.put_object(
            key=object_key, data=io.BytesIO(data), length=len(data), mime="image/png"
        )
        stored = True
    except Exception:
        # MinIO 가 없어도 로컬 캐시만으로 개념도는 그려진다. 색인은 계속한다.
        object_key = ""

    content = _search_text(verdict, extra=lic.get("title", ""))
    metadata = {
        "kind": PHOTO_KIND,
        "station_kind": verdict.station_kind,
        "caption": verdict.caption,
        "tags": verdict.tags,
        "cache_file": cache_path.name,
        "cutout_file": cutout_name,
        "cutout_method": cutout_method,
        "object_key": object_key,
        "width": w,
        "height": h,
        "license": lic.get("license", ""),
        "author": lic.get("author", ""),
        "source_url": lic.get("source_url", ""),
        "source_title": lic.get("title", ""),
    }

    embedding = await rag.embed_text(content)
    doc_id = await rag.upsert_document(
        db,
        source_path=f"equipment_photo/{digest}",
        source_label=(verdict.caption[:80] or filename),
        content=content,
        metadata=metadata,
        domain=PHOTO_DOMAIN,
        embedding=embedding,
    )
    return {
        "indexed": True,
        "reason": "",
        "doc_id": doc_id,
        "kind": verdict.station_kind,
        "caption": verdict.caption,
        "tags": verdict.tags,
        "file": filename,
        "cache": str(cache_path),
        "cutout": cutout_method,
        "minio": stored,
    }


# 현장에서 흔히 쓰는 파일명 약어 → station kind
_NAME_ALIASES: dict[str, str] = {
    "infeed": "conveyor", "in": "conveyor", "투입": "conveyor",
    "rbt": "robot", "arm": "robot", "cobot": "robot", "로봇": "robot",
    "cap": "capper", "capping": "capper", "캐핑": "capper",
    "fill": "filler", "filling": "filler", "주입": "filler",
    "tank": "tank", "탱크": "tank",
    "rej": "reject", "reject": "reject", "불량": "reject",
    "out": "outfeed", "outfeed": "outfeed", "배출": "outfeed",
    "cam": "camera", "vision": "camera", "비전": "camera",
}


def kind_from_filename(name: str) -> str:
    """파일명에서 설비 종류를 추론한다.

    VLM 은 '약액 주입 존' 같은 설비를 '금속 프레임 구조물' 로만 보고 종류를 못 맞춘다.
    현장 사진은 대개 설비명으로 정리돼 있으므로 파일명이 더 믿을 만한 단서다.
    """
    stem = Path(name).stem.lower()
    for k in KIND_KO:
        if stem == k or stem.startswith((f"{k}_", f"{k}-")):
            return k
    head = re.split(r"[_\-\s.]", stem)[0]
    if head in _NAME_ALIASES:
        return _NAME_ALIASES[head]
    for alias, k in _NAME_ALIASES.items():
        if alias in stem:
            return k
    return ""


def _station_query(st: Station) -> str:
    base = KIND_KO.get(st.kind, st.kind)
    extra = " ".join(x for x in (st.label, st.sublabel) if x)
    return f"{base} {extra}".strip()


async def find_photo(
    db: AsyncSession,
    query: str,
    *,
    station_kind: str = "",
    min_score: float = 0.30,
) -> tuple[ImageRef, dict[str, Any]] | None:
    """질의문에 가장 맞는 설비 사진 1장.

    설비 종류가 일치하는 후보에 가산점을 준다. 임베딩만으로는 '컨베이어' 와
    '배출 컨베이어' 처럼 표현이 겹치는 설비가 뒤섞이기 때문이다.
    """
    chunks = await rag.search(db, query, top_k=24, domain=PHOTO_DOMAIN)

    best = None
    best_score = -1.0
    for c in chunks:
        meta = c.metadata or {}
        if meta.get("kind") != PHOTO_KIND:
            continue
        score = float(c.score or 0.0)
        if station_kind and meta.get("station_kind") == station_kind:
            score += 0.35
        if score > best_score:
            best_score, best = score, (c, meta)

    if best is None or best_score < min_score:
        return None

    _, meta = best
    path = CACHE_DIR / str(meta.get("cutout_file") or "")
    if not path.is_file():
        path = CACHE_DIR / str(meta.get("cache_file") or "")
    if not path.is_file():
        return None
    return (
        ImageRef(src=str(path), alt=str(meta.get("caption") or "")),
        {**meta, "score": round(best_score, 3)},
    )


async def attach_photos(
    db: AsyncSession,
    spec: ConceptMapSpec,
    *,
    min_score: float = 0.30,
) -> dict[str, Any]:
    """스펙의 설비·검사항목·공정카드에 RAG 사진을 물린다.

    사진이 없는 자리는 손대지 않는다 — 렌더러가 SVG 심볼로 채우므로
    개념도는 어떤 경우에도 완성된다.
    """
    hits: list[dict[str, Any]] = []
    misses: list[str] = []
    by_kind: dict[str, ImageRef] = {}

    if spec.line_layout:
        for st in spec.line_layout.stations:
            found = await find_photo(
                db, _station_query(st), station_kind=st.kind, min_score=min_score
            )
            if found is None:
                misses.append(f"{st.id}({st.kind})")
                continue
            ref, meta = found
            st.image = ref
            by_kind.setdefault(st.kind, ref)
            hits.append({
                "target": st.id, "kind": st.kind,
                "caption": meta.get("caption"), "score": meta.get("score"),
                "license": meta.get("license"), "source_url": meta.get("source_url"),
            })

    # 공정 카드·검사 항목은 이미 찾아 둔 설비 사진을 재사용한다 (추가 검색 없이).
    for step in spec.process_steps:
        key = step.image.symbol if step.image else ""
        if key in by_kind:
            step.image = by_kind[key]
    for it in spec.inspections:
        key = it.image.symbol if it.image else ""
        if key in by_kind:
            it.image = by_kind[key]

    return {"attached": len(hits), "hits": hits, "missing": misses}


def credit_line(hits: list[dict[str, Any]]) -> str:
    """CC BY-SA 등 출처 표기가 필요한 사진의 크레딧 한 줄."""
    seen: set[str] = set()
    parts: list[str] = []
    for h in hits:
        lic = (h.get("license") or "").strip()
        if not lic or lic.lower().startswith("public domain"):
            continue
        url = (h.get("source_url") or "").strip()
        key = f"{lic}|{url}"
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{url} ({lic})" if url else lic)
    return "사진 출처: " + "; ".join(parts) if parts else ""
