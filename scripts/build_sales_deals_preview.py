# -*- coding: utf-8 -*-
"""영업 건 관리 이관 미리보기 — 기존 엑셀 두 파일 → 스냅샷 JSON + 출하 사진(줄인 사본).

    python scripts/build_sales_deals_preview.py
        [--management "docs/sources/견적,세금,수주 관리 시트.xlsx"]
        [--shipments "docs/sources/영업출하시트파일_ma261006.xlsx"]

산출물(backend/app/sales_deals/data/) — 백엔드 컨테이너가 이미지에 함께 담아 API 로 보여준다.
  legacy_snapshot.json  원자료(건·품목·출하·사진 파일명)
  photos/ship_r{행}_{n}.jpg  출하 사진(긴 변 1280px, 원본은 엑셀에 그대로)
원본 엑셀은 읽기만 한다.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from PIL import Image, ImageOps  # noqa: E402

from app.sales_deals.legacy_import import build_preview, build_snapshot  # noqa: E402

OUT = ROOT / "backend" / "app" / "sales_deals" / "data"
PHOTO_MAX = 1280


def save_photos(shipment_path: Path, snapshot: dict) -> int:
    photo_dir = OUT / "photos"
    photo_dir.mkdir(parents=True, exist_ok=True)
    for old in photo_dir.glob("ship_*.jpg"):
        old.unlink()
    n = 0
    with zipfile.ZipFile(shipment_path) as z:
        for s in snapshot["shipments"]:
            s["photos"] = []
            for i, ref in enumerate(s.pop("photo_refs"), 1):
                img = ImageOps.exif_transpose(Image.open(io.BytesIO(z.read(ref)))).convert("RGB")
                if min(img.size) < 200:  # 아이콘 크기 조각(엑셀에 붙은 작은 그림)은 건너뛴다
                    continue
                img.thumbnail((PHOTO_MAX, PHOTO_MAX))
                name = f"ship_r{s['row_start']}_{i}.jpg"
                img.save(photo_dir / name, "JPEG", quality=80)
                s["photos"].append(name)
                n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--management", default=str(ROOT / "docs" / "sources" / "견적,세금,수주 관리 시트.xlsx"))
    ap.add_argument("--shipments", default=str(ROOT / "docs" / "sources" / "영업출하시트파일_ma261006.xlsx"))
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    snapshot = build_snapshot(a.management, a.shipments)
    photos = save_photos(Path(a.shipments), snapshot)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "legacy_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")

    view = build_preview(snapshot)
    print(f"건 {view['counts']['deals']} · 출하 {view['counts']['shipments']}"
          f"(견적에 연결 {view['counts']['shipments_linked']}) · 사진 {photos}장")
    print("단계별:", view["summary"])


if __name__ == "__main__":
    main()
