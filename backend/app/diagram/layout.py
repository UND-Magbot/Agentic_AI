"""결정론적 레이아웃 엔진.

LLM 은 "무엇을 어디 근처에" 까지만 말한다(station id + 0~1 offset).
실제 픽셀 좌표와 겹침 해소는 전부 여기서 계산한다 — 같은 스펙이면 항상 같은 그림.

핵심 책임:
  1) station flex 가중치 → 실제 픽셀 폭/중심 좌표
  2) 카메라 라벨 겹침 → 세로 레인 자동 분리 (원본 개념도의 계단식 라벨 배치)
  3) 흐름 화살표를 설비 사이 빈 공간에 배치
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .spec import ConceptMapSpec, LineLayout

# 템플릿 CSS 와 맞물린 상수 — 바꾸면 concept_map.html.j2 도 같이 바꿔야 한다.
SHEET_BORDER = 1
BAND_PAD = 18
WRAP_BORDER = 1
WRAP_PAD = 14
STAGE_GAP = 6
LANE_H = 26          # 카메라 라벨 한 줄 높이
ZONE_H = 40          # 존 라벨 슬롯 높이 (라벨 유무와 무관하게 고정)
LANE_TOP = 6         # 첫 레인 상단 여백
CAM_ICON_W = 30      # 카메라 아이콘 + 간격
CHAR_W = 5.6         # 한글 기준 대략 글자폭 (px, 10px 폰트)
LABEL_PAD = 14


@dataclass
class StationBox:
    id: str
    left: float
    width: float

    @property
    def center(self) -> float:
        return self.left + self.width / 2

    @property
    def right(self) -> float:
        return self.left + self.width


@dataclass
class CamPos:
    label: str
    caption: str
    x: float
    cam_top: float
    cone: bool
    station_id: str
    offset: float


@dataclass
class CalloutPos:
    lines: list[str]
    x: float
    y: float


@dataclass
class FlowPos:
    x: float
    y: float
    label_y: float
    label: str
    color: str
    dashed: bool
    reverse: bool = False      # 되돌아오는 흐름 — 화살표를 좌향으로 뒤집는다


@dataclass
class LayoutResult:
    boxes: dict[str, StationBox] = field(default_factory=dict)
    cams: list[CamPos] = field(default_factory=list)
    callouts: list[CalloutPos] = field(default_factory=list)
    flows: list[FlowPos] = field(default_factory=list)
    line_pad_top: int = 40
    art_h: int = 150
    stage_h: int = 200
    zone_h: int = ZONE_H
    cone_w: int = 46
    outfeed_x: float = 0
    outfeed_y: float = 0


def _text_width(s: str, font_px: float) -> float:
    """문자열의 렌더 폭 추정.

    한글은 글자폭이 폰트 크기와 거의 같고 영숫자는 그 절반쯤이다. 한글을 영문처럼
    좁게 잡으면 겹침 판정이 헐거워져 라벨이 서로 붙는다.
    """
    w = 0.0
    for ch in s:
        if "가" <= ch <= "힣" or "ㄱ" <= ch <= "ㆎ":
            w += font_px                 # 한글 음절
        elif ch.isascii():
            w += font_px * 0.55
        else:
            w += font_px * 0.9           # 기호·전각
    return w


def _label_width(label: str, caption: str) -> float:
    """카메라 라벨 박스의 예상 픽셀 폭 — 겹침 판정용."""
    body = max(_text_width(label, 10.0), _text_width(caption, 8.5))
    return CAM_ICON_W + body + LABEL_PAD


def stage_inner_width(sheet_width: int) -> float:
    """stage(설비가 늘어서는 영역) 의 실제 픽셀 폭."""
    return (
        sheet_width
        - 2 * SHEET_BORDER
        - 2 * BAND_PAD
        - 2 * WRAP_BORDER
        - 2 * WRAP_PAD
    )


def _place_stations(line: LineLayout, sheet_width: int) -> dict[str, StationBox]:
    n = len(line.stations)
    if n == 0:
        return {}
    inner = stage_inner_width(sheet_width)
    avail = inner - STAGE_GAP * (n - 1)
    total = sum(max(s.width, 0.1) for s in line.stations)

    boxes: dict[str, StationBox] = {}
    cursor = float(WRAP_PAD)          # line-wrap padding box 기준 원점
    for s in line.stations:
        w = avail * max(s.width, 0.1) / total
        boxes[s.id] = StationBox(id=s.id, left=cursor, width=w)
        cursor += w + STAGE_GAP
    return boxes


def _assign_lanes(cams: list[CamPos], widths: list[float]) -> int:
    """카메라 라벨이 서로 겹치지 않도록 세로 레인을 배정한다.

    x 오름차순으로 훑으면서, 같은 레인의 직전 라벨 오른쪽 끝을 침범하면
    다음 레인으로 내린다. 원본 개념도의 계단식 라벨 배치와 같은 결과.
    """
    order = sorted(range(len(cams)), key=lambda i: cams[i].x)
    lane_right: list[float] = []
    for i in order:
        w = widths[i]
        left = cams[i].x - 4          # 아이콘이 x 기준 살짝 왼쪽에서 시작
        placed = False
        for lane, right in enumerate(lane_right):
            if left > right + 8:
                lane_right[lane] = left + w
                cams[i].cam_top = LANE_TOP + lane * LANE_H
                placed = True
                break
        if not placed:
            lane_right.append(left + w)
            cams[i].cam_top = LANE_TOP + (len(lane_right) - 1) * LANE_H
    return max(len(lane_right), 1)


def compute(spec: ConceptMapSpec, sheet_width: int) -> LayoutResult:
    """스펙 → 픽셀 좌표. line_layout 이 없으면 빈 결과."""
    res = LayoutResult()
    line = spec.line_layout
    if line is None or not line.stations:
        return res

    res.boxes = _place_stations(line, sheet_width)

    # 설비 그림 높이 — 폭에 비례하되 상한을 둬 세로로 뜨지 않게.
    inner = stage_inner_width(sheet_width)
    res.art_h = int(min(190, max(110, inner * 0.115)))
    res.cone_w = int(max(34, res.art_h * 0.30))

    # --- 카메라 배치 ---
    widths: list[float] = []
    for c in line.cameras:
        box = res.boxes.get(c.at)
        if box is None:
            continue
        x = box.left + box.width * min(max(c.offset, 0.0), 1.0)
        res.cams.append(
            CamPos(
                label=c.label,
                caption=c.caption,
                x=x,
                cam_top=LANE_TOP,
                cone=c.cone,
                station_id=c.at,
                offset=min(max(c.offset, 0.0), 1.0),
            )
        )
        widths.append(_label_width(c.label, c.caption))

    lanes = _assign_lanes(res.cams, widths) if res.cams else 1
    res.line_pad_top = LANE_TOP + lanes * LANE_H + 6

    # --- 콜아웃: 카메라 레인 아래, 설비 위쪽 ---
    callout_y = res.line_pad_top + 8
    for co in line.callouts:
        box = res.boxes.get(co.at)
        if box is None:
            continue
        res.callouts.append(
            CalloutPos(
                lines=co.lines,
                x=box.left + box.width * min(max(co.offset, 0.0), 1.0),
                y=callout_y,
            )
        )

    # --- 흐름 화살표: 두 설비 사이 간격 중앙 ---
    flow_y = res.line_pad_top + res.art_h * 0.66
    for f in line.flows:
        a = res.boxes.get(f.src)
        b = res.boxes.get(f.dst)
        if a is None or b is None:
            continue
        reverse = b.center < a.center
        x = (a.right + b.left) / 2 if b.left >= a.right else (a.center + b.center) / 2
        # 되돌아오는 흐름은 설비 하단으로 내린다. 위쪽은 카메라 라벨과 콜아웃이
        # 이미 쓰고 있어, 올리면 반드시 둘 중 하나와 부딪힌다.
        y = res.line_pad_top + res.art_h - 14 if reverse else flow_y
        res.flows.append(
            FlowPos(
                x=x,
                y=y,
                label_y=y - 15,
                label=f.label,
                color="#8a9bb0" if f.style == "dashed" else "#1f6feb",
                dashed=(f.style == "dashed"),
                reverse=reverse,
            )
        )

    # --- 완제품 배출 라벨: 마지막 설비 위 ---
    if line.outfeed_label and line.stations:
        last = res.boxes[line.stations[-1].id]
        res.outfeed_x = last.center
        res.outfeed_y = flow_y - 18

    res.stage_h = res.art_h + 4 + ZONE_H
    return res
