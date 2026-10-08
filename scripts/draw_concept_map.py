"""공정 개념도 생성 CLI.

사용법:
  # 자연어 요청 → gemma3 가 스펙 설계 → PNG
  python scripts/draw_concept_map.py --request "약액 주입 공정 개념도. 협동로봇 14kg, 비전 4대"

  # 이미 있는 스펙 JSON 으로 다시 그리기 (수정 후 재렌더)
  python scripts/draw_concept_map.py --spec out/spec.json --out out/map.png

  # 설비 사진 폴더 연결 — 파일명이 station id 와 같으면 자동으로 물린다
  python scripts/draw_concept_map.py --request "..." --photos ./photos

  # 내장 샘플로 렌더러만 확인
  python scripts/draw_concept_map.py --sample
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Windows 기본 콘솔(cp949) 에서는 한글·기호 출력이 깨지거나 예외가 난다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.diagram import render, samples  # noqa: E402
from app.diagram.spec import ConceptMapSpec, ImageRef  # noqa: E402


def attach_photos(spec: ConceptMapSpec, photo_dir: Path) -> int:
    """photo_dir 안에서 station id 와 같은 이름의 이미지를 찾아 물린다.

    RAG 검색이 붙기 전까지 쓰는 수동 경로이자, RAG 가 넘겨줄 결과의 형태이기도 하다.
    """
    if spec.line_layout is None:
        return 0
    exts = (".png", ".jpg", ".jpeg", ".webp")
    hit = 0
    for st in spec.line_layout.stations:
        for ext in exts:
            p = photo_dir / f"{st.id}{ext}"
            if p.is_file():
                st.image = ImageRef(src=str(p), alt=st.label or st.kind)
                hit += 1
                break
    return hit


async def build_spec(args: argparse.Namespace) -> ConceptMapSpec:
    if args.sample:
        return samples.chemical_filling_line()
    if args.spec:
        data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        return ConceptMapSpec.model_validate(data)

    from app.config import settings
    from app.diagram import llm_spec

    print(f"[1/3] {settings.proposal_model} 로 개념도 스펙 설계 중 … ({args.request[:40]}…)")
    spec, notes = await llm_spec.generate_spec(args.request, context=args.context)
    for n in notes:
        print(f"      · 자동 교정: {n}")
    return spec


async def main() -> int:
    ap = argparse.ArgumentParser(description="공정 개념도 생성")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--request", help="자연어 요청")
    src.add_argument("--spec", help="스펙 JSON 파일 경로")
    src.add_argument("--sample", action="store_true", help="내장 샘플 사용")
    ap.add_argument("--context", default="", help="참고 자료 텍스트 (RAG 결과 등)")
    ap.add_argument("--photos", help="설비 사진 폴더 (파일명 = station id)")
    ap.add_argument("--from-rag", action="store_true",
                    help="RAG 에 색인된 설비 사진을 자동 연결")
    ap.add_argument("--out", default="concept_map.png", help="출력 PNG 경로")
    ap.add_argument("--save-spec", help="생성된 스펙 JSON 저장 경로")
    ap.add_argument("--width", type=int, default=1600, help="조판 폭(px)")
    ap.add_argument("--scale", type=int, default=2, help="해상도 배율")
    args = ap.parse_args()

    spec = await build_spec(args)
    print(f"[2/3] 스펙 확정 — 공정 {len(spec.process_steps)}단계, "
          f"설비 {len(spec.line_layout.stations) if spec.line_layout else 0}기, "
          f"검사 {len(spec.inspections)}종")

    if args.from_rag:
        import os

        # 도커 밖에서 돌릴 때를 위한 주소 보정 (색인 CLI 와 동일).
        os.environ.setdefault("EMBED_BASE_URL", "http://localhost:8090")
        if os.environ.get("MINIO_ENDPOINT", "minio:9000").startswith("minio:"):
            os.environ["MINIO_ENDPOINT"] = "localhost:9000"

        from app.database import SessionLocal
        from app.diagram import photo_rag

        async with SessionLocal() as db:
            rep = await photo_rag.attach_photos(db, spec)
        print(f"      · RAG 설비 사진 {rep['attached']}건 연결"
              + (f" / 미연결 {', '.join(rep['missing'])}" if rep["missing"] else ""))
        for h in rep["hits"]:
            print(f"        - {h['target']:8} {h['caption'][:40]} (score {h['score']})")

        credit = photo_rag.credit_line(rep["hits"])
        if credit and credit not in (spec.footnote or ""):
            spec.footnote = f"{spec.footnote}  {credit}".strip()

    if args.photos:
        n = attach_photos(spec, Path(args.photos))
        print(f"      · 설비 사진 {n}장 연결")

    if args.save_spec:
        Path(args.save_spec).write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        print(f"      · 스펙 저장: {args.save_spec}")

    # main() 이 asyncio 루프 안이므로 반드시 async 경로를 쓴다 (sync API 는 루프 안에서 실패).
    png = await render.render_png(spec, width=args.width, scale=args.scale)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    print(f"[3/3] 완료 → {out}  ({len(png):,} bytes, {args.width * args.scale}px 폭)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
