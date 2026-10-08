"""설비 SVG 심볼 라이브러리.

RAG 에 실제 설비 사진이 없을 때 대체로 쓰인다. 사진과 섞여도 어색하지 않도록
동일한 스틸 그레이 + 딥네이비 팔레트로 통일했다.

각 심볼은 viewBox="0 0 100 100" 정규화 좌표계로 그려지고, 바닥선(y=88) 을
공유하므로 좌우로 나열하면 같은 바닥에 선다.
"""
from __future__ import annotations

# 팔레트 — 원본 개념도 톤.
STEEL_D = "#8a97a6"
STEEL = "#b9c4ce"
STEEL_L = "#dde4ea"
NAVY = "#1f3b63"
BASE = "#6f7d8c"
GLASS = "#cfe0ea"

_GRAD = """
<defs>
  <linearGradient id="stl" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#dde4ea"/><stop offset="1" stop-color="#8a97a6"/>
  </linearGradient>
  <linearGradient id="wht" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#d8dfe6"/>
  </linearGradient>
  <linearGradient id="gls" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#eaf3f8" stop-opacity=".95"/>
    <stop offset="1" stop-color="#cfe0ea" stop-opacity=".7"/>
  </linearGradient>
</defs>
"""


def _bottle(x: float, y: float, s: float = 1.0) -> str:
    """약액 병 1개. (x, y) = 바닥 중심."""
    w = 7 * s
    h = 16 * s
    nk = 2.4 * s
    d = (
        f"M{x - w / 2},{y} v{-h + 5 * s} q0,{-2 * s} {2 * s},{-2.5 * s} "
        f"l{w / 2 - nk / 2 - 2 * s},{-1 * s} v{-3 * s} h{nk} v{3 * s} "
        f"l{w / 2 - nk / 2 - 2 * s},{1 * s} q{2 * s},{.5 * s} {2 * s},{2.5 * s} "
        f"v{h - 5 * s} z"
    )
    return (
        f'<g><path d="{d}" fill="url(#wht)" stroke="{STEEL_D}" stroke-width=".5"/>'
        f'<rect x="{x - nk / 2}" y="{y - h - 1.2 * s}" width="{nk}" height="{2 * s}"'
        f' rx="{.4 * s}" fill="{NAVY}" opacity=".75"/></g>'
    )


def _frame(x: float, w: float, top: float, bot: float = 88) -> str:
    """설비 안전 프레임 — 상부 빔 + 좌우 기둥."""
    return (
        f'<rect x="{x}" y="{top}" width="{w}" height="3" fill="{STEEL_D}"/>'
        f'<rect x="{x + 1}" y="{top}" width="2.5" height="{bot - top}" fill="{STEEL}"/>'
        f'<rect x="{x + w - 3.5}" y="{top}" width="2.5" height="{bot - top}" fill="{STEEL}"/>'
    )


def _bench(x: float = 4, w: float = 92, top: float = 70) -> str:
    """작업대 받침 — 심볼 공통 바닥."""
    return (
        f'<rect x="{x}" y="{top}" width="{w}" height="6" rx="1" fill="url(#stl)"'
        f' stroke="{STEEL_D}" stroke-width=".6"/>'
        f'<rect x="{x + 3}" y="{top + 6}" width="3" height="{88 - top - 6}" fill="{BASE}"/>'
        f'<rect x="{x + w - 6}" y="{top + 6}" width="3" height="{88 - top - 6}" fill="{BASE}"/>'
        f'<rect x="{x + 3}" y="{top + 15}" width="{w - 6}" height="2" fill="{BASE}"'
        f' opacity=".6"/>'
    )


def conveyor() -> str:
    rollers = "".join(
        f'<circle cx="{x}" cy="68" r="2.6" fill="{STEEL_L}" stroke="{STEEL_D}"'
        f' stroke-width=".5"/>'
        for x in range(10, 95, 9)
    )
    return (
        f'<rect x="4" y="64" width="92" height="8" rx="1" fill="url(#stl)"'
        f' stroke="{STEEL_D}" stroke-width=".6"/>{rollers}'
        f'<rect x="7" y="72" width="3" height="16" fill="{BASE}"/>'
        f'<rect x="90" y="72" width="3" height="16" fill="{BASE}"/>'
        + _bottle(26, 64)
        + _bottle(50, 64)
        + _bottle(74, 64)
    )


def capper() -> str:
    return (
        _frame(28, 44, 8)
        + f'<rect x="41" y="14" width="18" height="30" rx="2" fill="url(#stl)"'
        f' stroke="{STEEL_D}" stroke-width=".6"/>'
        + f'<rect x="46" y="44" width="8" height="14" fill="{STEEL_D}"/>'
        + f'<circle cx="50" cy="59" r="4" fill="{NAVY}" opacity=".8"/>'
        + _bench(4, 92, 70)
        + _bottle(50, 70)
    )


def _link(x1: float, y1: float, x2: float, y2: float, w: float) -> str:
    """로봇 링크 1개 — 외곽선을 깐 뒤 그 위에 본체를 덮어 1px 테두리 효과.

    stroke 에 그라데이션을 쓰면 배경(흰색) 과 섞여 형태가 사라진다. 단색 2 겹으로 그린다.
    """
    return (
        f'<path d="M{x1},{y1} L{x2},{y2}" stroke="{STEEL_D}" stroke-width="{w}"'
        f' stroke-linecap="round" fill="none"/>'
        f'<path d="M{x1},{y1} L{x2},{y2}" stroke="#f4f7fa" stroke-width="{w - 1.6}"'
        f' stroke-linecap="round" fill="none"/>'
    )


def _joint(cx: float, cy: float, r: float) -> str:
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#f4f7fa" stroke="{STEEL_D}"'
        f' stroke-width=".8"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r * .38}" fill="{STEEL}" opacity=".7"/>'
    )


def robot() -> str:
    """협동로봇 팔 — 베이스 + 3 링크 + 그리퍼가 용기를 파지한 자세."""
    return (
        # 바닥 베이스 (다른 심볼과 같은 y=88 바닥선)
        f'<rect x="32" y="80" width="36" height="8" rx="2" fill="#2b2f36"/>'
        f'<rect x="38" y="72" width="24" height="9" rx="3" fill="#3a4049"/>'
        # 숄더 → 엘보 → 리스트
        + _link(50, 72, 50, 50, 13)
        + _joint(50, 50, 7)
        + _link(50, 50, 76, 38, 12)
        + _joint(76, 38, 6)
        + _link(76, 38, 84, 54, 9.5)
        + _joint(84, 54, 4.6)
        # 그리퍼 + 파지한 용기
        + f'<rect x="79" y="57" width="10" height="5" rx="1" fill="{STEEL}"'
          f' stroke="{STEEL_D}" stroke-width=".6"/>'
        + f'<rect x="79.5" y="62" width="2.4" height="5" fill="{STEEL_D}"/>'
        + f'<rect x="86.1" y="62" width="2.4" height="5" fill="{STEEL_D}"/>'
        + _bottle(84, 80, .78)
    )


def filler() -> str:
    """약액 주입 존 — 안전 프레임 + 노즐 + 병."""
    return (
        f'<rect x="16" y="6" width="68" height="66" fill="url(#gls)" opacity=".22"/>'
        + _frame(16, 68, 6)
        + f'<rect x="44" y="12" width="12" height="20" rx="1.5" fill="url(#stl)"'
        f' stroke="{STEEL_D}" stroke-width=".6"/>'
        + f'<rect x="48" y="32" width="4" height="16" fill="{STEEL_D}"/>'
        + f'<path d="M47,48 h6 l-1.5,6 h-3 z" fill="{NAVY}"/>'
        + _bench(10, 80, 70)
        + _bottle(50, 70, 1.1)
    )


def tank() -> str:
    return (
        f'<ellipse cx="50" cy="20" rx="17" ry="5" fill="{STEEL_L}"'
        f' stroke="{STEEL_D}" stroke-width=".6"/>'
        f'<rect x="33" y="20" width="34" height="42" fill="url(#stl)"'
        f' stroke="{STEEL_D}" stroke-width=".6"/>'
        f'<ellipse cx="50" cy="62" rx="17" ry="5" fill="{STEEL}"'
        f' stroke="{STEEL_D}" stroke-width=".6"/>'
        f'<rect x="47" y="10" width="6" height="10" fill="{STEEL_D}"/>'
        f'<rect x="48" y="66" width="4" height="10" fill="{STEEL_D}"/>'
        f'<rect x="36" y="30" width="5" height="24" rx="1" fill="{GLASS}" opacity=".8"/>'
        f'<rect x="20" y="76" width="60" height="4" rx="1" fill="{BASE}"/>'
        f'<rect x="24" y="80" width="4" height="8" fill="{BASE}"/>'
        f'<rect x="72" y="80" width="4" height="8" fill="{BASE}"/>'
    )


def reject() -> str:
    return (
        _bench(12, 76, 70)
        + f'<rect x="30" y="52" width="40" height="18" rx="2" fill="{STEEL_L}"'
        f' stroke="{STEEL_D}" stroke-width=".6" stroke-dasharray="3 2"/>'
        + _bottle(50, 52, .85)
    )


def outfeed() -> str:
    slats = "".join(
        f'<rect x="{x}" y="66" width="5" height="3" rx=".5" fill="{STEEL_L}"/>'
        for x in range(9, 92, 8)
    )
    return (
        f'<rect x="4" y="64" width="92" height="7" rx="1" fill="url(#stl)"'
        f' stroke="{STEEL_D}" stroke-width=".6"/>{slats}'
        f'<rect x="7" y="71" width="3" height="17" fill="{BASE}"/>'
        f'<rect x="90" y="71" width="3" height="17" fill="{BASE}"/>'
        + _bottle(34, 64, .9)
        + _bottle(62, 64, .9)
    )


def table() -> str:
    return _bench(8, 84, 66)


def camera() -> str:
    """비전 카메라 — viewBox 0 0 40 30 전용."""
    return (
        '<rect x="6" y="6" width="20" height="14" rx="2" fill="#2f3640"/>'
        '<rect x="26" y="9" width="8" height="8" rx="1.5" fill="#4a5560"/>'
        '<circle cx="32" cy="13" r="2.6" fill="#8fd6ff" opacity=".9"/>'
        '<rect x="10" y="2" width="9" height="4" rx="1" fill="#4a5560"/>'
    )


_REGISTRY = {
    "conveyor": conveyor,
    "capper": capper,
    "robot": robot,
    "filler": filler,
    "tank": tank,
    "reject": reject,
    "outfeed": outfeed,
    "table": table,
}


def station_svg(kind: str) -> str:
    """station kind 에 대응하는 완성 SVG 문자열."""
    fn = _REGISTRY.get(kind, table)
    return (
        '<svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMax meet"'
        f' xmlns="http://www.w3.org/2000/svg">{_GRAD}{fn()}</svg>'
    )


def camera_svg() -> str:
    return (
        '<svg viewBox="0 0 40 30" preserveAspectRatio="xMidYMid meet"'
        f' xmlns="http://www.w3.org/2000/svg">{camera()}</svg>'
    )


def available() -> list[str]:
    return sorted(_REGISTRY)
