# -*- coding: utf-8 -*-
"""기존 엑셀에서 변환한 영업 건(이관 미리보기 기준)을 DB(sales_deals)에 한 번 넣는다.

    python scripts/import_sales_deals.py --dry-run   # 넣을 건수만 확인
    python scripts/import_sales_deals.py             # 실제로 넣기(이미 이관했으면 거부)

입력: backend/app/sales_deals/data/legacy_snapshot.json (scripts/build_sales_deals_preview.py 산출물)
판단이 애매했던 점은 각 건의 '확인 필요'(review_alerts)로 함께 들어가고, 화면에서 확인 완료 처리한다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal  # noqa: E402
from app.migrations import _SALES_DEALS_STMTS  # noqa: E402
from app.sales_deals.legacy_import import build_preview  # noqa: E402
from app.sales_deals.service import DealError, import_preview_rows  # noqa: E402

SNAPSHOT = ROOT / "backend" / "app" / "sales_deals" / "data" / "legacy_snapshot.json"


async def main(dry_run: bool) -> int:
    preview = build_preview(json.loads(SNAPSHOT.read_text(encoding="utf-8")))
    rows = preview["rows"]
    print(f"넣을 건 {len(rows)}개 · 단계별 {preview['summary']}")
    if dry_run:
        return 0
    async with SessionLocal() as db:
        for stmt in _SALES_DEALS_STMTS:  # 백엔드 기동 전에도 돌 수 있게 이 기능의 표만 만든다(멱등)
            await db.execute(stmt)
        await db.commit()
        try:
            n = await import_preview_rows(db, rows, preview["sources"])
        except DealError as e:
            print("중단:", e)
            return 1
    print(f"이관 완료: {n}건")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args().dry_run)))
