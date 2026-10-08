"""수금 거래처 시드 — 재무팀 수금확인거래처 30곳 + 집행일 방향 분석 결과를 UPSERT.

멱등: name_norm UNIQUE 위에 ON CONFLICT DO UPDATE. 별칭도 alias_norm UNIQUE 위 UPSERT.
집행일 방향(전진/후진)은 실데이터 분석에서 seed 하되, direction_source='실데이터' 인 행만
덮어쓴다 — 담당자가 수동 확정(direction_source='담당자확정')한 값은 시드가 보존한다.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from .database import SessionLocal
from .finance.vendor_registry import SEED_VENDORS, normalize_vendor

logger = logging.getLogger("und_cortex.seed.vendors")


_UPSERT_VENDOR = text(
    """
    INSERT INTO vendors (
        canonical_name, name_norm, currency, collection_type, collection_day,
        direction, direction_source, note
    ) VALUES (
        :canonical_name, :name_norm, :currency, :collection_type, :collection_day,
        :direction, :direction_source, :note
    )
    ON CONFLICT (name_norm) DO UPDATE SET
        canonical_name  = EXCLUDED.canonical_name,
        currency        = EXCLUDED.currency,
        -- 담당자 수동확정 값은 시드가 덮지 않는다.
        collection_type = CASE WHEN vendors.direction_source = '담당자확정'
                               THEN vendors.collection_type ELSE EXCLUDED.collection_type END,
        collection_day  = CASE WHEN vendors.direction_source = '담당자확정'
                               THEN vendors.collection_day ELSE EXCLUDED.collection_day END,
        direction       = CASE WHEN vendors.direction_source = '담당자확정'
                               THEN vendors.direction ELSE EXCLUDED.direction END,
        direction_source= CASE WHEN vendors.direction_source = '담당자확정'
                               THEN vendors.direction_source ELSE EXCLUDED.direction_source END,
        note            = EXCLUDED.note,
        updated_at      = NOW()
    RETURNING id, (xmax = 0) AS inserted;
    """
)

_UPSERT_ALIAS = text(
    """
    INSERT INTO vendor_aliases (vendor_id, alias, alias_norm)
    VALUES (:vendor_id, :alias, :alias_norm)
    ON CONFLICT (alias_norm) DO UPDATE SET
        vendor_id = EXCLUDED.vendor_id, alias = EXCLUDED.alias
    RETURNING (xmax = 0) AS inserted;
    """
)


async def seed_vendors_on_startup() -> dict[str, int]:
    """수금거래처 + 별칭 시드. 반환: {inserted, updated, aliases}."""
    ins = upd = al = 0
    async with SessionLocal() as db:
        try:
            for v in SEED_VENDORS:
                params = {
                    "canonical_name": v["name"],
                    "name_norm": normalize_vendor(v["name"]),
                    "currency": v.get("cur"),
                    "collection_type": v.get("type", "미정"),
                    "collection_day": v.get("day"),
                    "direction": v.get("dir", "전진"),
                    "direction_source": v.get("src"),
                    "note": v.get("note"),
                }
                res = (await db.execute(_UPSERT_VENDOR, params)).first()
                vid = res[0]
                if res[1]:
                    ins += 1
                else:
                    upd += 1
                for alias in v.get("aliases", []):
                    an = normalize_vendor(alias)
                    if not an or an == params["name_norm"]:
                        continue
                    await db.execute(_UPSERT_ALIAS, {
                        "vendor_id": vid, "alias": alias, "alias_norm": an,
                    })
                    al += 1
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    logger.info("[seed.vendors] inserted=%d updated=%d aliases=%d", ins, upd, al)
    return {"inserted": ins, "updated": upd, "aliases": al}
