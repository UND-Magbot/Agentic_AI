"""개념도 스펙 스키마.

LLM 이 생성하는 것은 "이미지" 가 아니라 이 스펙(JSON) 이다.
스펙 → layout.py(좌표 확정) → render.py(HTML→PNG) 로 결정론적으로 조판된다.

원본 제안서 개념도를 분해한 결과 5개 블록으로 환원된다:
  1) 상단  process_steps  : 번호 배지 + 제목 + 불릿 + 사진 카드의 가로 띠
  2) 중앙  line_layout    : 설비 좌→우 배치 + 비전 콘 + 흐름 화살표 (핵심)
  3) 좌하  inspections    : 검사 항목 사진 + 캡션
  4) 중하  system_diagram : 노드/엣지 구성도
  5) 우하  tables         : 수치 표 N 개
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --- 공통 -------------------------------------------------------------------

class ImageRef(BaseModel):
    """이미지 참조. RAG 검색으로 채워지거나 사용자가 직접 지정한다.

    src 가 비면 렌더러가 kind 에 맞는 SVG 심볼로 대체한다 —
    사진이 없어도 개념도는 항상 완성된다.
    """
    src: str = ""                      # 로컬 경로 또는 data URI
    symbol: str = ""                   # 대체 심볼 키 (symbols.py)
    alt: str = ""


# --- 1) 상단 공정 카드 ------------------------------------------------------

class ProcessStep(BaseModel):
    title: str                                  # "투입 / 반제 적치"
    bullets: list[str] = Field(default_factory=list, max_length=3)
    image: ImageRef = Field(default_factory=ImageRef)
    badge: str = ""                             # "주입시간 210초"
    badge_tone: Literal["amber", "green", "blue", ""] = ""


# --- 2) 중앙 라인 레이아웃 (핵심) -------------------------------------------

StationKind = Literal[
    "conveyor", "capper", "robot", "filler", "tank", "reject", "outfeed", "table"
]


class Station(BaseModel):
    """라인 위 설비 1기. width 는 상대 가중치(합산해 정규화)."""
    id: str
    kind: StationKind
    label: str = ""                             # 존 라벨 박스 윗줄
    sublabel: str = ""                          # 아랫줄 "(주입시간: 210초)"
    width: float = 1.0
    image: ImageRef = Field(default_factory=ImageRef)
    show_zone_label: bool = True


class VisionCam(BaseModel):
    """비전 카메라 + 녹색 광선 콘. station 위에 얹힌다."""
    id: str
    at: str                                     # station id
    label: str                                  # "비전 1"
    caption: str = ""                           # "(정렬/표시자세)"
    offset: float = 0.5                         # station 내 가로 위치 0~1
    cone: bool = True


class Callout(BaseModel):
    """설비를 가리키는 설명 박스. (한화로봇 사양 등)"""
    at: str
    lines: list[str]
    offset: float = 0.5


class Flow(BaseModel):
    src: str
    dst: str
    style: Literal["solid", "dashed"] = "solid"
    label: str = ""


class LineLayout(BaseModel):
    stations: list[Station]
    cameras: list[VisionCam] = Field(default_factory=list)
    callouts: list[Callout] = Field(default_factory=list)
    flows: list[Flow] = Field(default_factory=list)
    outfeed_label: str = ""                     # 우측 끝 "완제품 배출"


# --- 3) 좌하 검사 항목 ------------------------------------------------------

class InspectionItem(BaseModel):
    title: str                                  # "비전 1 : 투입/자세 확인"
    detail: str = ""
    image: ImageRef = Field(default_factory=ImageRef)


# --- 4) 중하 시스템 구성도 --------------------------------------------------

class SysNode(BaseModel):
    id: str
    label: str
    sublabel: str = ""
    kind: Literal["camera", "ipc", "plc", "controller", "hmi"] = "ipc"
    col: int = 0                                # 좌→우 단계
    row: int = 0


class SysEdge(BaseModel):
    src: str
    dst: str
    label: str = ""


class SystemDiagram(BaseModel):
    nodes: list[SysNode] = Field(default_factory=list)
    edges: list[SysEdge] = Field(default_factory=list)


# --- 5) 우하 표 -------------------------------------------------------------

class SpecTable(BaseModel):
    title: str
    headers: list[str]
    rows: list[list[str]]
    accent: bool = False


# --- 루트 -------------------------------------------------------------------

class ConceptMapSpec(BaseModel):
    title: str
    subtitle: str = ""
    process_steps: list[ProcessStep] = Field(default_factory=list)
    line_layout: LineLayout | None = None
    inspections: list[InspectionItem] = Field(default_factory=list)
    inspections_title: str = "비전 검사 항목"
    system_diagram: SystemDiagram | None = None
    system_title: str = "시스템 구성도"
    tables: list[SpecTable] = Field(default_factory=list)
    footnote: str = ""
