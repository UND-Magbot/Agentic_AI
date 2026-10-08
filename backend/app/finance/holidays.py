# -*- coding: utf-8 -*-
"""대한민국 공휴일 조회 — 정부 공공데이터(한국천문연구원 특일정보) API + 로컬 캐시 + 오프라인 폴백.

자금계획 자동생성의 영업일 보정(주말·공휴일 회피)에 쓰는 공휴일 집합을 공급한다.

우선순위:
  1) 로컬 캐시 JSON (`data/holidays_cache.json`) — 이미 받아둔 연도는 오프라인으로 즉시 사용.
  2) 정부 API — `.env` 의 `HOLIDAY_API_KEY` 가 있으면 호출하고 결과를 캐시에 적재.
  3) 정적 폴백 `STATIC_HOLIDAYS` — 키·네트워크가 없을 때(테스트/오프라인). 담당자·실데이터로
     검증된 2026년 세트가 들어 있어 기존 자동생성 동작을 그대로 보존한다.

핵심: **대체공휴일을 우리가 계산하지 않는다.** API(`getRestDeInfo`)가 대체공휴일을
`isHoliday=Y` 항목으로 함께 내려주므로(추석·개천절이 토요일과 겹치는 연쇄 케이스 포함)
그대로 신뢰한다.

API: 한국천문연구원_특일 정보 (data.go.kr)
  GET .../B090041/openapi/service/SpcdeInfoService/getRestDeInfo
  params: serviceKey, solYear(YYYY), solMonth(MM), numOfRows, _type=json
발급: data.go.kr 회원가입 → 위 서비스 "활용신청" → 일반 인증키(Decoding) 를 .env 에 저장.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

# 검증된 정적 폴백 — 연도별 공휴일(대체공휴일 포함). 오프라인/무키 환경 기본값.
# 2026: 자금계획 원본 실데이터·담당자 확정으로 검증됨(5/24 부처님오신날→5/25 대체 등).
STATIC_HOLIDAYS: dict[int, set[datetime.date]] = {
    2026: {
        datetime.date(2026, 1, 1),                                    # 신정
        datetime.date(2026, 2, 16), datetime.date(2026, 2, 17), datetime.date(2026, 2, 18),  # 설날
        datetime.date(2026, 3, 1), datetime.date(2026, 3, 2),         # 삼일절(일)+대체
        datetime.date(2026, 5, 5),                                    # 어린이날
        datetime.date(2026, 5, 24), datetime.date(2026, 5, 25),       # 부처님오신날(일)+대체
        datetime.date(2026, 6, 6),                                    # 현충일
        datetime.date(2026, 8, 15), datetime.date(2026, 8, 17),       # 광복절(토)+대체
        datetime.date(2026, 9, 24), datetime.date(2026, 9, 25), datetime.date(2026, 9, 26),  # 추석
        datetime.date(2026, 10, 3), datetime.date(2026, 10, 5),       # 개천절(토)+대체
        datetime.date(2026, 10, 9),                                   # 한글날
        datetime.date(2026, 12, 25),                                  # 성탄절
    },
}

_CACHE_PATH = Path(__file__).parent / "data" / "holidays_cache.json"
_API_URL = (
    "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
)
_ENV_KEY = "HOLIDAY_API_KEY"


def _load_cache() -> dict[int, set[datetime.date]]:
    if not _CACHE_PATH.exists():
        return {}
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[int, set[datetime.date]] = {}
    for y, days in raw.items():
        out[int(y)] = {datetime.date.fromisoformat(d) for d in days}
    return out


def _save_cache(cache: dict[int, set[datetime.date]]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    serial = {
        str(y): sorted(d.isoformat() for d in days) for y, days in cache.items()
    }
    _CACHE_PATH.write_text(
        json.dumps(serial, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _parse_items(payload: dict) -> set[datetime.date]:
    """getRestDeInfo JSON 응답 → 공휴일(date) 집합. 항목 0/1개/다수 형태를 모두 처리."""
    body = (payload or {}).get("response", {}).get("body", {})
    items = (body or {}).get("items", "")
    if not items:  # 빈 문자열('') = 해당 월 공휴일 없음
        return set()
    item = items.get("item", [])
    if isinstance(item, dict):  # 단일 항목은 dict 로 옴
        item = [item]
    days: set[datetime.date] = set()
    for it in item:
        if str(it.get("isHoliday", "Y")).strip().upper() != "Y":
            continue
        loc = str(it.get("locdate", "")).strip()  # 'YYYYMMDD'
        if len(loc) == 8 and loc.isdigit():
            days.add(datetime.date(int(loc[:4]), int(loc[4:6]), int(loc[6:8])))
    return days


def fetch_from_api(year: int, service_key: str, *, timeout: float = 10.0) -> set[datetime.date]:
    """정부 API 에서 해당 연도 공휴일(대체공휴일 포함)을 가져온다. 실패 시 예외 전파.

    data.go.kr 은 Encoding/Decoding 두 형태의 인증키를 제공한다. httpx 는 params 값을
    자동으로 URL 인코딩하므로, 이미 인코딩된 Encoding 키(예: '%2B')를 그대로 넘기면
    '%'가 재인코딩('%25')되어 이중 인코딩 → 401 이 난다. 어느 형태를 붙여넣어도 동작하도록
    '%'가 있으면 원본(Decoding)으로 되돌린 뒤 클라이언트가 한 번만 인코딩하게 한다.
    """
    import httpx
    from urllib.parse import unquote

    key = unquote(service_key) if "%" in service_key else service_key
    days: set[datetime.date] = set()
    with httpx.Client(timeout=timeout) as client:
        for month in range(1, 13):
            resp = client.get(
                _API_URL,
                params={
                    "serviceKey": key,
                    "solYear": str(year),
                    "solMonth": f"{month:02d}",
                    "numOfRows": "50",
                    "_type": "json",
                },
            )
            resp.raise_for_status()
            days |= _parse_items(resp.json())
    return days


def get_holidays(*years: int, use_api: bool = True) -> set[datetime.date]:
    """요청 연도들의 공휴일 합집합을 반환.

    캐시 → API(키 있을 때) → 정적 폴백 순으로 해석한다. 한 연도라도 API 로 새로 받으면
    캐시에 적재해 다음부터 오프라인으로 쓴다. 예외는 삼켜 폴백으로 이어간다(자동생성 중단 방지).
    """
    if not years:
        return set()
    cache = _load_cache()
    key = os.getenv(_ENV_KEY, "").strip()
    result: set[datetime.date] = set()
    dirty = False
    for y in sorted(set(years)):
        if y in cache:
            result |= cache[y]
            continue
        if use_api and key:
            try:
                fetched = fetch_from_api(y, key)
                cache[y] = fetched
                result |= fetched
                dirty = True
                continue
            except Exception:  # noqa: BLE001 — 네트워크/파싱 실패 시 폴백
                pass
        result |= STATIC_HOLIDAYS.get(y, set())
    if dirty:
        try:
            _save_cache(cache)
        except OSError:
            pass
    return result


__all__ = ["get_holidays", "fetch_from_api", "STATIC_HOLIDAYS"]
