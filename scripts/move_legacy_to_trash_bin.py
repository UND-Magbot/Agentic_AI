"""예전 번호(S25-…·S26-…) 영업 건을 옛 데이터 보관함(trash_bin)으로 옮긴다 — 사용자 2026-10-08.

건 한 행 전체(to_jsonb)와 변경 이력을 trash_bin 에 넣고 sales_deals 에서 지운다(이력은 ON DELETE CASCADE).
한 트랜잭션 — 넣은 수와 지울 수가 다르면 아무것도 바꾸지 않는다. 기본은 미리보기, --apply 로 실제 실행.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/move_legacy_to_trash_bin.py [--apply]
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.database import SessionLocal

LEGACY = "deal_no ~ '^S[0-9]{2}-'"
REASON = "예전 번호 체계(S25-·S26-) 옛 데이터 — 영업 건 리스트에서 뺌(사용자 2026-10-08)"


async def main(apply: bool) -> int:
    async with SessionLocal() as db:
        rows = (await db.execute(text(f"SELECT id, deal_no, imported FROM sales_deals WHERE {LEGACY} ORDER BY id"))).all()
        print(f"대상 {len(rows)}건 (엑셀 이관 {sum(1 for r in rows if r.imported)} · 그 밖 {sum(1 for r in rows if not r.imported)})")
        for r in rows:
            if not r.imported:
                print(f"  - 이관 아닌 건: {r.deal_no} (id {r.id})")
        if not apply:
            print("미리보기만 했습니다. 실제로 옮기려면 --apply")
            return 0
        moved = (await db.execute(text(
            "INSERT INTO trash_bin (source_table, source_id, label, data, events, reason) "
            "SELECT 'sales_deals', d.id, d.deal_no, to_jsonb(d), "
            "       COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id) FROM sales_deal_events e WHERE e.deal_id = d.id), '[]'::jsonb), :r "
            f"FROM sales_deals d WHERE {LEGACY} ON CONFLICT (source_table, source_id) DO NOTHING"), {"r": REASON})).rowcount
        gone = (await db.execute(text(f"DELETE FROM sales_deals WHERE {LEGACY}"))).rowcount
        if moved != len(rows) or gone != len(rows):
            await db.rollback()
            print(f"수가 맞지 않아 되돌렸습니다: 대상 {len(rows)} · 보관 {moved} · 삭제 {gone}")
            return 1
        await db.commit()
        left = (await db.execute(text(f"SELECT COUNT(*) FROM sales_deals WHERE {LEGACY}"))).scalar()
        kept = (await db.execute(text("SELECT COUNT(*) FROM trash_bin WHERE source_table = 'sales_deals'"))).scalar()
        print(f"옮김 {moved}건 · 영업 건에 남은 옛 번호 {left}건 · 보관함 합계 {kept}건")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--apply" in sys.argv)))
