"""설비 사진을 RAG 에 색인한다.

사용법:
  # 폴더 통째로 색인 (manifest.json 이 있으면 라이선스 정보를 함께 싣는다)
  python scripts/index_equipment_photos.py ./photos

  # 설비 종류를 강제 지정 (VLM 판정을 덮어씀)
  python scripts/index_equipment_photos.py ./photos --kind robot

  # 색인된 사진 목록 확인 / 전체 삭제
  python scripts/index_equipment_photos.py --list
  python scripts/index_equipment_photos.py --purge

색인 전에 gemma3 비전이 사진을 판정한다. 설비 사진이 아니거나 개념도에 쓸 수 없는
것은 자동으로 걸러진다.

로컬(도커 밖)에서 실행할 때는 embed/MinIO 주소를 컨테이너 내부 이름 대신
localhost 매핑 포트로 바꿔 준다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

# 도커 내부 호스트명(embed, minio)은 로컬에서 풀리지 않는다. 매핑 포트로 대체.
os.environ.setdefault("EMBED_BASE_URL", "http://localhost:8090")
if os.environ.get("MINIO_ENDPOINT", "minio:9000").startswith("minio:"):
    os.environ["MINIO_ENDPOINT"] = "localhost:9000"

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


async def cmd_index(folder: Path, force_kind: str, cutout_mode: str) -> int:
    from app.database import SessionLocal
    from app.diagram import photo_rag

    manifest: dict[str, dict] = {}
    mf = folder / "manifest.json"
    if mf.is_file():
        for rec in json.loads(mf.read_text(encoding="utf-8")):
            manifest[rec.get("file", "")] = rec

    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMG_EXT)
    if not files:
        print(f"이미지가 없습니다: {folder}")
        return 1

    print(f"{len(files)}장 판정 시작 (gemma3 비전)\n")
    ok = skip = 0
    async with SessionLocal() as db:
        for i, p in enumerate(files, 1):
            rec = manifest.get(p.name, {})
            lic = {
                "license": rec.get("license", ""),
                "author": rec.get("author", ""),
                "source_url": rec.get("source_url", ""),
                "title": rec.get("title", ""),
            }
            try:
                res = await photo_rag.index_photo(
                    db,
                    image=p.read_bytes(),
                    filename=p.name,
                    license_info=lic,
                    force_kind=force_kind,
                    cutout_mode=cutout_mode,
                )
            except Exception as e:
                print(f"[{i:2}/{len(files)}] {p.name:22} 오류: {str(e)[:70]}")
                skip += 1
                continue

            if res["indexed"]:
                ok += 1
                print(f"[{i:2}/{len(files)}] {p.name:22} OK  {res['kind']:9} "
                      f"누끼={res.get('cutout') or '-':18} {res['caption'][:32]}")
            else:
                skip += 1
                print(f"[{i:2}/{len(files)}] {p.name:22} --  {res['reason']:12} "
                      f"{(res.get('caption') or '')[:34]}")
        await db.commit()

    print(f"\n색인 {ok}건 / 제외 {skip}건")
    return 0


async def cmd_list() -> int:
    from sqlalchemy import text

    from app.database import SessionLocal
    from app.diagram import photo_rag

    async with SessionLocal() as db:
        rows = await db.execute(text(
            "SELECT id, source_label, metadata->>'station_kind' AS k, "
            "metadata->>'license' AS lic "
            "FROM documents WHERE metadata->>'kind' = :k ORDER BY id"
        ), {"k": photo_rag.PHOTO_KIND})
        items = rows.fetchall()

    if not items:
        print("색인된 설비 사진이 없습니다.")
        return 0
    print(f"색인된 설비 사진 {len(items)}건\n")
    by_kind: dict[str, int] = {}
    for r in items:
        by_kind[r[2] or "?"] = by_kind.get(r[2] or "?", 0) + 1
        print(f"  #{r[0]:<5} {r[2] or '?':10} {(r[1] or '')[:50]:52} {r[3] or ''}")
    print("\n설비별:", ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
    return 0


async def cmd_purge() -> int:
    from sqlalchemy import text

    from app.database import SessionLocal
    from app.diagram import photo_rag

    async with SessionLocal() as db:
        res = await db.execute(text(
            "DELETE FROM documents WHERE metadata->>'kind' = :k"
        ), {"k": photo_rag.PHOTO_KIND})
        await db.commit()
    print(f"삭제 {res.rowcount}건 (로컬 캐시 파일은 보존)")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description="설비 사진 RAG 색인")
    ap.add_argument("folder", nargs="?", help="사진 폴더")
    ap.add_argument("--kind", default="", help="설비 종류 강제 지정")
    ap.add_argument("--cutout", default="auto",
                    choices=["auto", "uniform", "ai", "none"],
                    help="배경 제거 방식 (기본 auto)")
    ap.add_argument("--list", action="store_true", help="색인 목록 조회")
    ap.add_argument("--purge", action="store_true", help="색인 전체 삭제")
    args = ap.parse_args()

    if args.list:
        return await cmd_list()
    if args.purge:
        return await cmd_purge()
    if not args.folder:
        ap.error("폴더를 지정하거나 --list / --purge 를 쓰세요.")
    return await cmd_index(Path(args.folder), args.kind, args.cutout)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
