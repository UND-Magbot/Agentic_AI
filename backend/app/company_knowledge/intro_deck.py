"""회사 소개서(pptx) → 쪽별 원문(글상자·표 + 이미지 판독) → RAG 적재.

소개서는 105쪽, 1.3GB 이지만 대부분 동영상(별첨)이다 — 사용자 지시(2026-09-30)로 동영상과 별첨 쪽은 뺀다.
사양표·고객사 로고·4족 로봇 사양처럼 그림으로만 들어 있는 내용이 많아, 쪽 이미지를 사내 gemma 비전으로 읽는다.
  - 쪽 이미지는 PowerPoint 로 미리 뽑아 둔다(Windows 호스트, 컨테이너에는 렌더러가 없음).
  - 한 장을 통째로 읽히면 작은 로고·약어를 지어내 풀어 썼다(실측: "KIMS 한국원자력연구원"). 4조각으로 나눠
    2배 확대해 읽으면 맞게 읽었다(KITECH 한국생산기술연구원, KATECH 한국자동차연구원) — 그래서 조각 판독.
  - 판독 결과는 docs/company_knowledge/intro_ocr.json 에 캐시한다(재적재 때 다시 읽지 않음). 외부 전송 없음.
원문 글상자가 1차 근거이고, 이미지 판독은 보조 근거(카드 검증에서 "이미지 판독"으로 표시)다.

실행:
    # 1) 호스트: 쪽 이미지 → work/company_intro/slides/s001.png … (PowerPoint COM)
    # 2) 컨테이너: 판독 + 적재
    docker compose run --rm --no-deps -v ./docs:/docs -v ./work/company_intro:/intro backend \\
        python -m app.company_knowledge.intro_deck ocr      # 이미지 판독(캐시에 없는 쪽만)
    docker compose run --rm --no-deps -v ./docs:/docs backend python -m app.company_knowledge.intro_deck ingest
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger("company_knowledge.intro_deck")

DOCS = Path("/docs") if Path("/docs").is_dir() else Path(__file__).resolve().parents[3] / "docs"
SLIDE_DIR = Path("/intro/slides") if Path("/intro/slides").is_dir() else \
    Path(__file__).resolve().parents[3] / "work" / "company_intro" / "slides"
DECK_NAME = "[국문] (주)유엔디 맥봇플랫폼 및 SI 로봇 자동화, 4족로봇_이동형로봇 소개_3Q.pptx"
SOURCE_PATH = "docs/company_intro_3Q.pptx"       # documents.source_path (파일명이 길고 특수문자가 많아 짧은 키)
OCR_CACHE = DOCS / "company_knowledge" / "intro_ocr.json"
DOMAIN = "sales"
APPENDIX_SLIDES = range(79, 94)                  # 별첨1~11 — 동영상 쪽(사용자 지시로 제외)

_OCR_PROMPT = """이 이미지(슬라이드의 일부)에 보이는 글자만 빠짐없이 그대로 옮겨 적어라(OCR). 인사말·설명·마크다운 없이 글자만.
- 표는 '항목: 값1 | 값2' 형식으로 행마다.
- 로고는 로고에 실제로 적힌 글자만 적는다. 약어를 풀어 쓰거나 한글 이름을 덧붙이지 않는다.
- 이미지에 없는 내용을 추측해 넣지 않는다. 잘린 글자나 읽을 수 없는 글자는 (판독불가)."""


@dataclass
class Slide:
    no: int
    text: str            # 글상자·표 원문(1차 근거)
    ocr: str = ""        # 쪽 이미지 판독(보조 근거)
    pics: int = 0        # 그림 수

    def needs_ocr(self) -> bool:
        """그림은 있는데 글상자가 빈약한 쪽(사양·로고가 그림 속에 있음), 또는 사양 제목만 있고 수치가 없는 쪽.
        글상자에 설명이 충분한 사례 쪽까지 다 읽으면 쪽당 80초라 2시간이 걸린다(실측)."""
        digits = len(re.findall(r"\d", self.text))
        return self.pics > 0 and (len(self.text) < 450 or ("SPECIFICATION" in self.text.upper() and digits < 10))

    @property
    def title(self) -> str:
        first = self.text.split(" // ")[0] if self.text else ""
        return first[:60]

    def full(self) -> str:
        return self.text + (f"\n[이미지 판독]\n{self.ocr}" if self.ocr else "")


def _shape_texts(shapes) -> list[str]:
    out = []
    for sh in shapes:
        if sh.shape_type == 6:                                   # 그룹 안까지
            out += _shape_texts(sh.shapes)
            continue
        if sh.has_text_frame and sh.text_frame.text.strip():
            out.append(re.sub(r"\s*\n\s*", " / ", sh.text_frame.text.strip()))
        if getattr(sh, "has_table", False) and sh.has_table:
            rows = [" | ".join(c.text.strip().replace("\n", " ") for c in r.cells) for r in sh.table.rows]
            out.append("표: " + " ; ".join(rows))
    return out


def read_slides(path: Path | None = None) -> list[Slide]:
    """글상자·표 원문. 동영상은 zip 안에 있어도 열지 않는다(python-pptx 는 미디어를 읽지 않음)."""
    from pptx import Presentation

    prs = Presentation(str(path or DOCS / "sources" / DECK_NAME))
    out = []
    for n, s in enumerate(prs.slides, 1):
        if n in APPENDIX_SLIDES:
            continue
        pics = sum(1 for sh in s.shapes if sh.shape_type == 13)
        out.append(Slide(n, " // ".join(t for t in _shape_texts(s.shapes) if "유튜브 영상재생" not in t
                                         and "영상 플레이 버튼" not in t), pics=pics))
    return out


def _tiles(png: bytes) -> list[str]:
    """4조각(경계 겹침) × 2배 확대 → base64."""
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    out = []
    for x0, y0 in ((0, 0), (w // 2, 0), (0, h // 2), (w // 2, h // 2)):
        box = (max(0, x0 - 60), max(0, y0 - 40), min(w, x0 + w // 2 + 60), min(h, y0 + h // 2 + 40))
        c = im.crop(box).resize(((box[2] - box[0]) * 2, (box[3] - box[1]) * 2), Image.LANCZOS)
        b = io.BytesIO()
        c.save(b, "PNG")
        out.append(base64.b64encode(b.getvalue()).decode())
    return out


def _dedupe_lines(parts: list[str]) -> str:
    """조각이 겹쳐 같은 줄이 두 번 나오면 한 번만."""
    seen, out = set(), []
    for p in parts:
        for line in p.splitlines():
            key = re.sub(r"\s+", "", line)
            if key and key not in seen:
                seen.add(key)
                out.append(line.strip())
    return "\n".join(out)


async def ocr_slide(png: bytes, *, chat=None) -> str:
    from .. import proposal_llm

    chat = chat or proposal_llm.chat
    parts = []
    for img in _tiles(png):
        parts.append(await chat([{"role": "user", "content": _OCR_PROMPT, "images": [img]}],
                                fmt=None, num_predict=2000, temperature=0.0))
    return _dedupe_lines(parts)


def load_ocr() -> dict[str, str]:
    return json.loads(OCR_CACHE.read_text(encoding="utf-8")) if OCR_CACHE.exists() else {}


async def run_ocr(only: list[int] | None = None) -> None:
    cache = load_ocr()
    OCR_CACHE.parent.mkdir(parents=True, exist_ok=True)
    for s in read_slides():
        if (only and s.no not in only) or (not only and (str(s.no) in cache or not s.needs_ocr())):
            continue
        png = SLIDE_DIR / f"s{s.no:03d}.png"
        if not png.exists():
            print(f"  {s.no:3d} 쪽 이미지 없음: {png}", flush=True)
            continue
        try:
            cache[str(s.no)] = await ocr_slide(png.read_bytes())
        except Exception as e:  # noqa: BLE001 — 한 쪽 실패가 전체를 막지 않게
            print(f"  {s.no:3d} 판독 실패: {e}", flush=True)
            continue
        OCR_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  {s.no:3d} 판독 {len(cache[str(s.no)])}자", flush=True)


def slides_with_ocr() -> list[Slide]:
    ocr = load_ocr()
    return [Slide(s.no, s.text, ocr.get(str(s.no), ""), s.pics) for s in read_slides()]


def to_chunks(slides: list[Slide]) -> list[dict]:
    """쪽 단위 chunk — 소개서 쪽은 한 주제(제품 하나·사례 하나)라 쪽이 자연스러운 단위다."""
    out = []
    for s in slides:
        body = s.full().strip()
        if len(body) < 20:
            continue
        out.append({"source_label": f"회사 소개서 3Q · {s.no}쪽 {s.title}",
                    "content": f"[유엔디 회사 소개서 {s.no}쪽]\n{body}",
                    "metadata": {"doc": "회사 소개서 3Q", "slide": s.no, "kind": "company_intro",
                                 "has_ocr": bool(s.ocr)}})
    return out


async def ingest() -> int:
    """같은 source_path 의 기존 chunk 를 지우고 다시 적재."""
    from sqlalchemy import text

    from ..database import SessionLocal
    from ..rag import embed_batch, upsert_document

    chunks = to_chunks(slides_with_ocr())
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM documents WHERE source_path = :p"), {"p": SOURCE_PATH})
        for i in range(0, len(chunks), 8):
            batch = chunks[i:i + 8]
            vecs = await embed_batch([c["content"] for c in batch])
            for c, v in zip(batch, vecs):
                await upsert_document(db, source_path=SOURCE_PATH, source_label=c["source_label"],
                                      content=c["content"], metadata=c["metadata"], domain=DOMAIN, embedding=v)
        await db.commit()
    print(f"적재 {len(chunks)}건 → {SOURCE_PATH}", flush=True)
    return len(chunks)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ocr":
        asyncio.run(run_ocr([int(x) for x in sys.argv[2:]] or None))
    elif cmd == "ingest":
        asyncio.run(ingest())
    else:
        print(__doc__)
