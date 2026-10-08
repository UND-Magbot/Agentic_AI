# -*- coding: utf-8 -*-
"""수금 거래처 레지스트리 — 정규화 · 치환 조회 · 시드 데이터.

- `normalize_vendor` : 계획표/마스터 표기를 공통 키로 정규화(㈜·주식회사·괄호 제거).
- `resolve_vendor`   : 원표기 → DB 거래처(canonical + 집행일 방향). name_norm → alias_norm 순.
- `SEED_VENDORS`     : 재무팀 수금확인거래처 30곳 + 실데이터 분석 결과(방향/유형/별칭) 시드.

집행일 방향 근거: 자금계획_FY26 2026-01~06 실기입 전수 분석
(docs/design/자금계획_거래처방향_분석표.md). 혼합은 최신 발생 기준으로 판정.
데이터 없는 거래처는 기본값 '전진'(수입은 늦게 잡는 보수적 가정) — 담당자 확정/데이터 누적 시 갱신.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

# 정규화 시 제거할 회사형태 토큰
_FORMS = ["㈜", "(주)", "（주）", "주식회사", "유한회사", "　", " "]


def normalize_vendor(name: str | None) -> str:
    """거래처 표기 → 매칭 키. 개명 괄호·지점·연구소·회사형태·공백 제거 후 소문자."""
    s = str(name or "")
    s = re.sub(r"\([^)]*구[^)]*\)", "", s)   # (구 OO) 등 개명 표기
    s = re.sub(r"\(지점\)", "", s)
    s = s.replace("_매출처", "").replace("_매입처", "")
    s = re.sub(r"수원연구소|연구소", "", s)
    for t in _FORMS:
        s = s.replace(t, "")
    s = re.sub(r"\(.*?\)", "", s)             # 남은 괄호 내용 제거
    return s.strip().lower()


# (canonical_name, currency, collection_type, collection_day, direction, source, note, [aliases])
# 실제 거래처·결제 조건은 공개 저장소에 두지 않는다 — data/vendor_seed.json(.gitignore)에서 읽고,
# 없으면 예시 거래처의 vendor_seed.example.json 으로 동작한다.
_DATA_DIR = Path(__file__).with_name("data")


def _load_seed_vendors() -> list[dict]:
    path = _DATA_DIR / "vendor_seed.json"
    if not path.exists():
        logging.getLogger("finance.vendor_registry").warning(
            "vendor_seed.json 이 없어 예시 거래처로 동작합니다: %s", path)
        path = _DATA_DIR / "vendor_seed.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


SEED_VENDORS: list[dict] = _load_seed_vendors()


def vendor_map_from_seed() -> dict[str, dict]:
    """시드(SEED_VENDORS) 기반 인메모리 거래처 맵 — {name_norm: rec}. 별칭도 같은 rec.

    rec = {canonical, direction, collection_type}. 동기 생성 경로(plan_generator)에서 DB 없이
    치환·방향 보정에 쓴다. 런타임은 DB에서 로드한 동등 맵을 대신 넘길 수 있다.
    """
    m: dict[str, dict] = {}
    for v in SEED_VENDORS:
        rec = {"canonical": v["name"], "direction": v.get("dir", "전진"),
               "collection_type": v.get("type", "미정")}
        m[normalize_vendor(v["name"])] = rec
        for a in v.get("aliases", []):
            an = normalize_vendor(a)
            if an:
                m[an] = rec
    return m


def lookup_rule(vendor_map: dict[str, dict] | None, raw_name: str | None) -> dict | None:
    """거래처 원표기 → 규칙 rec(canonical/direction/type) 또는 None."""
    if not vendor_map:
        return None
    return vendor_map.get(normalize_vendor(raw_name))


async def resolve_vendor(db, raw_name: str | None):
    """원표기 → vendors row(dict) 또는 None. name_norm → alias_norm 순 조회.

    반환 dict: {id, canonical_name, currency, collection_type, collection_day,
                direction, direction_source, note}. 못 찾으면 None(→ 호출측 기본값 처리).
    """
    from sqlalchemy import text as _t

    key = normalize_vendor(raw_name)
    if not key:
        return None
    row = (await db.execute(_t(
        "SELECT id, canonical_name, currency, collection_type, collection_day, "
        "direction, direction_source, note FROM vendors WHERE name_norm = :k"
    ), {"k": key})).first()
    if row is None:
        row = (await db.execute(_t(
            "SELECT v.id, v.canonical_name, v.currency, v.collection_type, v.collection_day, "
            "v.direction, v.direction_source, v.note "
            "FROM vendor_aliases a JOIN vendors v ON v.id = a.vendor_id "
            "WHERE a.alias_norm = :k"
        ), {"k": key})).first()
    if row is None:
        return None
    return {
        "id": row[0], "canonical_name": row[1], "currency": row[2],
        "collection_type": row[3], "collection_day": row[4],
        "direction": row[5], "direction_source": row[6], "note": row[7],
    }
