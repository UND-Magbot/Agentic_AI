"""개념도 스펙(JSON) → draw.io(.drawio) 파일.

render.py 와 같은 스펙을 받아 "사람이 고칠 수 있는" 원본 파일을 만든다.
LLM 은 스펙만 정하고, 좌표·간격·라우팅은 전부 여기서 결정론적으로 계산한다.
결과물은 draw.io(무료)에서 열어 박스를 옮기거나 글자를 고친 뒤 PNG 로 다시 내보내면 된다.

PNG 내보내기는 draw.io 데스크톱 CLI 를 쓴다 (export_png).

사용:
    python -m app.diagram.drawio spec.json out.drawio [--png out.png] [--exe draw.io.exe]
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import shutil
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from . import symbols
from .spec import ConceptMapSpec, LineLayout, SpecTable

# --- 캔버스 / 팔레트 --------------------------------------------------------

SHEET_W = 1600
MARGIN = 40
INNER_W = SHEET_W - 2 * MARGIN
# 윈도우는 맑은 고딕, 리눅스(Docker)는 나눔고딕으로 대체된다.
FONT = "Malgun Gothic,NanumGothic,sans-serif"

NAVY = symbols.NAVY
INK = "#1f2937"
MUTED = "#5b6573"
LINE = "#c9d2dc"
PANEL = "#f4f7fa"
GREEN = "#2e9d57"
BADGE = {  # tone → (배경, 글자)
    "amber": ("#fff1d6", "#9a5b00"),
    "green": ("#dcf5e5", "#1d7a44"),
    "blue": ("#dde9fb", "#1f4f96"),
    "": ("#e8edf2", MUTED),
}

# 존 라벨이 비었을 때 쓰는 설비 종류 이름 (spec.StationKind 와 1:1).
KIND_LABEL = {
    "conveyor": "컨베이어", "capper": "캐핑", "robot": "로봇", "filler": "주입 장치",
    "tank": "탱크", "reject": "불량 배출", "outfeed": "완제품 배출", "table": "작업대",
}

# 라인 배치 세로 좌표 (섹션 상단 기준 오프셋)
CAM_LANE_H = 36          # 카메라 라벨 줄 간격
CAM_H = 30
STATION_IMG_H = 100
ZONE_H = 42
FLOW_LANE_H = 20         # 비인접 흐름 우회선 간격


def _style(**kw: object) -> str:
    base = {"html": 1, "whiteSpace": "wrap", "fontFamily": FONT}
    base.update(kw)
    return ";".join(f"{k}={v}" if v != "" else k for k, v in base.items()) + ";"


def _svg_uri(svg: str) -> str:
    # draw.io 스타일은 ';' 로 구분되므로 data URI 에 ';base64' 를 쓰지 않는 형식을 쓴다.
    return "data:image/svg+xml," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


class _Doc:
    """mxGraphModel 셀 누적기."""

    def __init__(self) -> None:
        self.root = ET.Element("root")
        ET.SubElement(self.root, "mxCell", id="0")
        ET.SubElement(self.root, "mxCell", id="1", parent="0")
        self._n = 0

    def _id(self, hint: str) -> str:
        self._n += 1
        return f"{hint}-{self._n}"

    def box(self, x: float, y: float, w: float, h: float, label: str = "", style: str = "",
            parent: str = "1", hint: str = "c") -> str:
        cid = self._id(hint)
        cell = ET.SubElement(self.root, "mxCell", id=cid, value=label, style=style,
                             vertex="1", parent=parent)
        ET.SubElement(cell, "mxGeometry", x=f"{x:.1f}", y=f"{y:.1f}", width=f"{w:.1f}",
                      height=f"{h:.1f}", **{"as": "geometry"})
        return cid

    def text(self, x: float, y: float, w: float, h: float, label: str, *, size: int = 12,
             color: str = INK, bold: bool = False, align: str = "left", parent: str = "1") -> str:
        st = _style(text="", strokeColor="none", fillColor="none", align=align,
                    verticalAlign="middle", fontSize=size, fontColor=color,
                    fontStyle=1 if bold else 0)
        return self.box(x, y, w, h, label, st, parent, "t")

    def image(self, x: float, y: float, w: float, h: float, svg: str, parent: str = "1") -> str:
        st = _style(shape="image", imageAspect=1, aspect="fixed", verticalLabelPosition="bottom",
                    image=_svg_uri(svg))
        return self.box(x, y, w, h, "", st, parent, "img")

    def edge(self, src: str, dst: str, label: str = "", *, dashed: bool = False,
             points: list[tuple[float, float]] | None = None, color: str = NAVY,
             extra: dict | None = None) -> str:
        kw: dict = dict(edgeStyle="orthogonalEdgeStyle", rounded=1, endArrow="block",
                        endFill=1, strokeColor=color, strokeWidth=2, fontSize=11,
                        fontColor=MUTED, labelBackgroundColor="#ffffff", whiteSpace="nowrap")
        if dashed:
            kw.update(dashed=1, dashPattern="6 4")
        kw.update(extra or {})
        cid = self._id("e")
        cell = ET.SubElement(self.root, "mxCell", id=cid, value=_esc(label), style=_style(**kw),
                             edge="1", parent="1", source=src, target=dst)
        geo = ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        if points:
            arr = ET.SubElement(geo, "Array", **{"as": "points"})
            for px, py in points:
                ET.SubElement(arr, "mxPoint", x=f"{px:.1f}", y=f"{py:.1f}")
        return cid

    def xml(self, name: str, height: float) -> str:
        mxfile = ET.Element("mxfile", host="und_cortex")
        diagram = ET.SubElement(mxfile, "diagram", name=name, id="concept")
        model = ET.SubElement(diagram, "mxGraphModel", dx="0", dy="0", grid="1", gridSize="10",
                              guides="1", page="1", pageWidth=str(SHEET_W),
                              pageHeight=str(int(height)), background="#ffffff")
        model.append(self.root)
        return ET.tostring(mxfile, encoding="unicode")


# --- 블록별 배치 ------------------------------------------------------------

def _panel(doc: _Doc, x: float, y: float, w: float, h: float, title: str = "") -> None:
    doc.box(x, y, w, h, "", _style(rounded=1, arcSize=3, fillColor=PANEL, strokeColor=LINE),
            hint="panel")
    if title:
        doc.text(x + 14, y + 6, w - 28, 26, _esc(title), size=14, color=NAVY, bold=True)


def _process_steps(doc: _Doc, spec: ConceptMapSpec, y: float) -> float:
    steps = spec.process_steps
    if not steps:
        return y
    gap, h = 14, 150
    w = (INNER_W - gap * (len(steps) - 1)) / len(steps)
    prev = None
    for i, st in enumerate(steps):
        x = MARGIN + i * (w + gap)
        card = doc.box(x, y, w, h, "", _style(rounded=1, arcSize=6, fillColor="#ffffff",
                                              strokeColor=LINE), hint="step")
        doc.box(8, 8, 24, 24, str(i + 1),
                _style(ellipse="", fillColor=NAVY, strokeColor="none", fontColor="#ffffff",
                       fontStyle=1, fontSize=12), parent=card)
        doc.text(38, 6, w - 44, 28, _esc(st.title), size=14, bold=True, parent=card)
        bullets = "<br>".join(f"• {_esc(b)}" for b in st.bullets)
        doc.box(10, 38, w - 76, 76, bullets,
                _style(text="", strokeColor="none", fillColor="none", align="left",
                       verticalAlign="top", fontSize=12, fontColor=MUTED), parent=card)
        kind = st.image.symbol or "table"
        doc.image(w - 68, 44, 60, 60, symbols.station_svg(kind), parent=card)
        if st.badge:
            bg, fg = BADGE.get(st.badge_tone, BADGE[""])
            doc.box(10, h - 32, min(w - 20, 16 + 13 * len(st.badge)), 22, _esc(st.badge),
                    _style(rounded=1, arcSize=50, fillColor=bg, strokeColor="none",
                           fontColor=fg, fontSize=11, fontStyle=1, whiteSpace="nowrap"),
                    parent=card)
        if prev:
            doc.edge(prev, card, color=LINE, extra=dict(strokeWidth=2, exitX=1, exitY=0.5,
                                                         entryX=0, entryY=0.5))
        prev = card
    return y + h


def _cam_lanes(cams_x: list[float], label_w: float,
               widths: list[float] | None = None) -> list[int]:
    """라벨이 가로로 겹치지 않도록 줄(lane)을 배정한다 (그리디, x 오름차순 입력).

    widths 를 주면 항목마다 폭이 다르다(카메라 라벨 + 콜아웃 혼합).
    """
    lane_right: list[float] = []
    lanes = []
    for k, cx in enumerate(cams_x):
        half = (widths[k] if widths else label_w) / 2
        for i, right in enumerate(lane_right):
            if cx - half >= right + 8:
                lane_right[i] = cx + half
                lanes.append(i)
                break
        else:
            lane_right.append(cx + half)
            lanes.append(len(lane_right) - 1)
    return lanes


def _line_layout(doc: _Doc, line: LineLayout, y: float) -> float:
    pad = 20
    usable = INNER_W - 2 * pad
    total_w = sum(s.width for s in line.stations) or 1
    gap = 18
    scale = (usable - gap * (len(line.stations) - 1)) / total_w
    xs: dict[str, tuple[float, float]] = {}
    x = MARGIN + pad
    for s in line.stations:
        w = s.width * scale
        xs[s.id] = (x, w)
        x += w + gap

    cam_label_w, callout_w = 170, 150
    cams = [c for c in line.cameras if c.at in xs]
    cams.sort(key=lambda c: xs[c.at][0] + c.offset * xs[c.at][1])
    cam_x = [xs[c.at][0] + c.offset * xs[c.at][1] for c in cams]
    callouts = [co for co in line.callouts if co.at in xs]

    def _callout_offset(co) -> float:
        # 같은 설비의 카메라와 x 가 가까우면 연결선이 카메라를 관통하므로 반대편으로 비켜 둔다.
        near = [c.offset for c in cams if c.at == co.at and abs(c.offset - co.offset) < 0.3]
        if not near:
            return co.offset
        return 0.9 if sum(near) / len(near) <= 0.5 else 0.1

    co_x = [xs[co.at][0] + _callout_offset(co) * xs[co.at][1] for co in callouts]
    co_h = [18 * len(co.lines) + 14 for co in callouts]

    # 카메라 라벨과 콜아웃을 한 줄 배정에 넣는다 — 콜아웃이 카메라 아이콘을 가리지 않게.
    entries = sorted([(x, cam_label_w, "cam", i) for i, x in enumerate(cam_x)]
                     + [(x, callout_w, "co", i) for i, x in enumerate(co_x)])
    entry_lanes = _cam_lanes([e[0] for e in entries], 0, [e[1] for e in entries])
    lane_of = {(e[2], e[3]): ln for e, ln in zip(entries, entry_lanes)}
    lanes = [lane_of[("cam", i)] for i in range(len(cams))]
    n_lanes = max(entry_lanes, default=-1) + 1
    lane_h = max([CAM_LANE_H] + [h + 6 for h in co_h])

    top = y + 40                                  # 섹션 제목 아래
    cam_y = top + n_lanes * lane_h + 4
    st_y = cam_y + CAM_H + 44                     # 콘 높이 확보
    zone_y = st_y + STATION_IMG_H + 6

    order = {s.id: i for i, s in enumerate(line.stations)}
    # 라벨 붙은 흐름도 우회선으로 보낸다 — 설비 사이 간격이 좁아 라벨이 설비를 덮는다.
    far = [f for f in line.flows
           if f.src in order and f.dst in order
           and (order[f.dst] - order[f.src] != 1 or f.label)]
    lanes_y0 = zone_y + ZONE_H + 18
    bottom = lanes_y0 + max(len(far) - 1, 0) * FLOW_LANE_H + 22
    _panel(doc, MARGIN, y, INNER_W, bottom - y, "라인 배치 개념도")

    img_id: dict[str, str] = {}
    zone_id: dict[str, str] = {}
    for s in line.stations:
        sx, sw = xs[s.id]
        img_id[s.id] = doc.image(sx, st_y, sw, STATION_IMG_H, symbols.station_svg(s.kind))
        name = s.label or (line.outfeed_label if s.kind == "outfeed" else "") or KIND_LABEL[s.kind]
        label = f"<b>{_esc(name)}</b>" + (f"<br><font style='font-size:11px' color='{MUTED}'>"
                                             f"{_esc(s.sublabel)}</font>" if s.sublabel else "")
        zone_style = (_style(rounded=1, arcSize=12, fillColor="#ffffff", strokeColor=NAVY,
                             fontSize=13, fontColor=NAVY)
                      if s.show_zone_label else
                      _style(rounded=1, arcSize=12, fillColor="#ffffff", strokeColor=LINE,
                             dashed=1, fontSize=13, fontColor=INK))
        zone_id[s.id] = doc.box(sx, zone_y, sw, ZONE_H, label, zone_style, hint="zone")

    # 비전 카메라 + 콘 + 라벨
    for c, cx, lane in zip(cams, cam_x, lanes):
        doc.box(cx - 36, cam_y + CAM_H - 2, 72, st_y - cam_y - CAM_H + 8, "",
                _style(shape="triangle", direction="north", fillColor=GREEN, opacity=22,
                       strokeColor="none"), hint="cone")
        doc.image(cx - 20, cam_y, 40, CAM_H, symbols.camera_svg())
        label = f"<b>{_esc(c.label)}</b>" + (f" <font color='{MUTED}'>{_esc(c.caption)}</font>"
                                             if c.caption else "")
        ly = top + lane * lane_h
        doc.box(cx - cam_label_w / 2, ly, cam_label_w, 30, label,
                _style(rounded=1, arcSize=20, fillColor="#ecf8f0", strokeColor=GREEN,
                       fontSize=11, fontColor=INK), hint="cam")

    # 콜아웃: 배정된 줄에 두고 설비까지 선으로 잇는다.
    for i, co in enumerate(callouts):
        cx = co_x[i]
        body = "<br>".join(_esc(t) for t in co.lines)
        cid = doc.box(cx - callout_w / 2, top + lane_of[("co", i)] * lane_h, callout_w,
                      co_h[i], body,
                      _style(rounded=1, arcSize=10, fillColor="#ffffff", strokeColor=NAVY,
                             fontSize=12, fontColor=NAVY), hint="callout")
        sx, sw = xs[co.at]
        doc.edge(cid, img_id[co.at], color=NAVY,
                 extra=dict(endArrow="oval", endSize=6, strokeWidth=1, exitX=0.5, exitY=1,
                            entryX=round((cx - sx) / sw, 3), entryY=0.15))

    # 흐름: 인접 전진은 설비 사이 직선, 나머지는 존 라벨 아래 우회선
    for f in line.flows:
        if f.src not in order or f.dst not in order:
            continue
        if f not in far:
            doc.edge(img_id[f.src], img_id[f.dst], f.label, dashed=f.style == "dashed",
                     extra=dict(exitX=1, exitY=0.62, entryX=0, entryY=0.62))
    for k, f in enumerate(far):
        ly = lanes_y0 + k * FLOW_LANE_H
        (sx, sw), (dx, dw) = xs[f.src], xs[f.dst]
        forward = order[f.dst] > order[f.src]
        ex = 0.7 if forward else 0.3
        en = 0.3 if forward else 0.7
        doc.edge(zone_id[f.src], zone_id[f.dst], f.label, dashed=f.style == "dashed",
                 points=[(sx + ex * sw, ly), (dx + en * dw, ly)],
                 extra=dict(exitX=ex, exitY=1, entryX=en, entryY=1))
    return bottom


def _table_html(t: SpecTable, width: float) -> str:
    th = "".join(f"<th style='background:{NAVY if t.accent else '#dfe6ee'};"
                 f"color:{'#fff' if t.accent else NAVY};padding:4px 8px;text-align:left'>"
                 f"{_esc(h)}</th>" for h in t.headers)
    rows = []
    for r in t.rows:
        tds = []
        for v in r:
            color = "#b45309" if v.strip() in ("확인 필요", "검토 중") else INK
            tds.append(f"<td style='border-top:1px solid {LINE};padding:4px 8px;color:{color}'>"
                       f"{_esc(v)}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return (f"<div style='font-weight:bold;color:{NAVY};margin-bottom:4px'>{_esc(t.title)}</div>"
            f"<table style='border-collapse:collapse;width:{width:.0f}px;font-size:12px'>"
            f"<tr>{th}</tr>{''.join(rows)}</table>")


def _bottom_row(doc: _Doc, spec: ConceptMapSpec, y: float) -> float:
    gap = 16
    col_w = (INNER_W - 2 * gap) / 3
    heights = [0.0]

    # 1) 검사 항목
    if spec.inspections:
        n = len(spec.inspections)
        x0 = MARGIN
        card_w = (col_w - 28 - 10 * (n - 1)) / n
        h = 250
        _panel(doc, x0, y, col_w, h, spec.inspections_title)
        for i, it in enumerate(spec.inspections):
            cx = x0 + 14 + i * (card_w + 10)
            card = doc.box(cx, y + 40, card_w, h - 54, "",
                           _style(rounded=1, arcSize=6, fillColor="#ffffff", strokeColor=LINE),
                           hint="insp")
            doc.image((card_w - 80) / 2, 8, 80, 80, symbols.station_svg(it.image.symbol or "table"),
                      parent=card)
            doc.box(6, 94, card_w - 12, h - 54 - 100,
                    f"<b>{_esc(it.title)}</b><br><font color='{MUTED}'>{_esc(it.detail)}</font>",
                    _style(text="", strokeColor="none", fillColor="none", align="center",
                           verticalAlign="top", fontSize=12, fontColor=INK), parent=card)
        heights.append(h)

    # 2) 시스템 구성도
    sd = spec.system_diagram
    if sd and sd.nodes:
        x0 = MARGIN + col_w + gap
        cols = max(n.col for n in sd.nodes) + 1
        rows = max(n.row for n in sd.nodes) + 1
        nw, nh = 110, 58
        cgap = (col_w - 28 - cols * nw) / max(cols - 1, 1)
        h = max(250, 60 + rows * (nh + 30))
        _panel(doc, x0, y, col_w, h, spec.system_title)
        ids = {}
        for n in sd.nodes:
            nx = x0 + 14 + n.col * (nw + cgap)
            ny = y + 90 + n.row * (nh + 30)
            ids[n.id] = doc.box(nx, ny, nw, nh,
                                f"<b>{_esc(n.label)}</b>"
                                + (f"<br><font color='{MUTED}'>{_esc(n.sublabel)}</font>"
                                   if n.sublabel else ""),
                                _style(rounded=1, arcSize=10, fillColor="#ffffff",
                                       strokeColor=NAVY, strokeWidth=2, fontSize=12,
                                       fontColor=NAVY), hint="node")
        for e in sd.edges:
            if e.src in ids and e.dst in ids:
                doc.edge(ids[e.src], ids[e.dst], e.label,
                         extra=dict(verticalLabelPosition="top", labelPosition="center"))
        heights.append(h)

    # 3) 표 — 행 수로 높이를 잡고 세로로 쌓는다
    if spec.tables:
        x0 = MARGIN + 2 * (col_w + gap)
        ty = y
        for t in spec.tables:
            th = 30 + 26 * (len(t.rows) + 1)
            doc.box(x0, ty, col_w, th, _table_html(t, col_w - 20),
                    _style(rounded=1, arcSize=4, fillColor="#ffffff", strokeColor=LINE,
                           align="left", verticalAlign="top", spacing=10, fontSize=12,
                           fontColor=INK), hint="table")
            ty += th + 10
        heights.append(ty - 10 - y)
    return y + max(heights)


def build_drawio(spec: ConceptMapSpec) -> str:
    """스펙 → .drawio XML 문자열."""
    doc = _Doc()
    doc.text(MARGIN, 18, INNER_W, 36, _esc(spec.title), size=26, color=NAVY, bold=True)
    y = 56
    if spec.subtitle:
        doc.text(MARGIN, y, INNER_W, 24, _esc(spec.subtitle), size=15, color=MUTED)
        y += 26
    y = _process_steps(doc, spec, y + 14) + 22
    if spec.line_layout and spec.line_layout.stations:
        y = _line_layout(doc, spec.line_layout, y) + 22
    y = _bottom_row(doc, spec, y)
    if spec.footnote:
        doc.text(MARGIN, y + 12, INNER_W, 24, _esc(spec.footnote), size=12, color=MUTED)
        y += 36
    return doc.xml(spec.title, y + 20)


_WIN_PATHS = (
    r"C:\Program Files\draw.io\draw.io.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\draw.io\draw.io.exe"),
)


def find_exe(configured: str = "") -> str | None:
    """draw.io 데스크톱 실행 파일 경로. 설정값 → PATH → 윈도우 기본 설치 경로 순."""
    if configured:
        return configured if (Path(configured).is_file() or shutil.which(configured)) else None
    for name in ("drawio", "draw.io"):
        found = shutil.which(name)
        if found:
            return found
    return next((p for p in _WIN_PATHS if Path(p).is_file()), None)


# 정상이면 3초 안팎이다. 렌더러가 가끔 죽어 멈추므로(2026-09-28, 같은 도면이 10/10 성공하다
# 한 번 멈춤) 짧게 끊고 새 프로세스로 한 번 더 시도한다.
EXPORT_TIMEOUT_S = 45
EXPORT_ATTEMPTS = 2


def _run_group(cmd: list[str], timeout: float) -> int:
    """자식 전체를 한 프로세스 그룹으로 실행하고, 시간 초과면 그룹째 죽인다.

    subprocess.run(timeout=) 은 직계 자식(xvfb-run)만 죽여 draw.io·Xvfb 가 고아로 남았다
    (2026-09-28 실측: 11분째 1.1GB 점유). 윈도우에는 프로세스 그룹 개념이 달라 그냥 실행한다.
    """
    if sys.platform == "win32":
        return subprocess.run(cmd, timeout=timeout, capture_output=True).returncode
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        raise


def export_png(drawio_path: Path, png_path: Path, exe: str, scale: int = 2) -> None:
    """draw.io 데스크톱 CLI 로 PNG 를 내보낸다.

    리눅스에서는 Electron 이 화면을 요구하므로 xvfb-run 으로 가상 디스플레이를 붙인다.
    """
    cmd = [exe, "--export", "--format", "png", "--scale", str(scale), "--border", "20"]
    if sys.platform != "win32":
        # 컨테이너엔 GPU·넉넉한 /dev/shm 이 없어 렌더러가 죽는다(2026-09-28 실측).
        cmd += ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
    cmd += ["--output", str(png_path), str(drawio_path)]
    if sys.platform != "win32" and shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a", *cmd]

    last: Exception | None = None
    for _ in range(EXPORT_ATTEMPTS):
        png_path.unlink(missing_ok=True)
        try:
            _run_group(cmd, EXPORT_TIMEOUT_S)
        except subprocess.TimeoutExpired as e:
            last = e
            continue
        if png_path.is_file() and png_path.stat().st_size > 0:
            return
        last = RuntimeError("draw.io 가 PNG 를 만들지 못했습니다.")
    raise last or RuntimeError("draw.io 가 PNG 를 만들지 못했습니다.")


def main() -> None:
    ap = argparse.ArgumentParser(description="개념도 스펙 → draw.io 파일")
    ap.add_argument("spec", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--png", type=Path)
    ap.add_argument("--exe", default="draw.io", help="draw.io 데스크톱 실행 파일 경로")
    a = ap.parse_args()
    spec = ConceptMapSpec.model_validate(json.loads(a.spec.read_text(encoding="utf-8")))
    a.out.write_text(build_drawio(spec), encoding="utf-8")
    print(f"drawio -> {a.out}")
    if a.png:
        export_png(a.out, a.png, a.exe)
        print(f"png -> {a.png}")


if __name__ == "__main__":
    sys.exit(main())
