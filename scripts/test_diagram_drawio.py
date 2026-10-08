"""개념도 스펙 → draw.io 변환(diagram.drawio) 검증 — draw.io 설치 불필요."""
import html
import io
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.diagram import drawio  # noqa: E402
from app.diagram.spec import ConceptMapSpec  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


spec = ConceptMapSpec.model_validate(
    json.loads((ROOT / "docs/concept_drawio/chem_spec.json").read_text(encoding="utf-8")))
tree = ET.fromstring(drawio.build_drawio(spec))
cells = tree.findall(".//mxCell")
values = html.unescape(" ".join(c.get("value", "") for c in cells))
ids = {c.get("id") for c in cells}
edges = [c for c in cells if c.get("edge") == "1"]
line = spec.line_layout

check(len(ids) == len(cells), "셀 id 중복 없음")
check(all(e.get("source") in ids and e.get("target") in ids for e in edges), "모든 엣지 끝점 존재")
for s in line.stations:
    check(s.label in values, f"존 라벨 표시: {s.label}")
for c in line.cameras:
    check(c.label in values, f"카메라 라벨 표시: {c.label}")
for f in line.flows:
    if f.label:
        check(any(e.get("value") == f.label for e in edges), f"흐름 라벨 엣지: {f.label}")
for t in spec.tables:
    for r in t.rows:
        check(all(v in values for v in r), f"표 행: {r[0]}")
check("일정: 금주 금요일까지" in values, "각주 표시")
check(all("whiteSpace=nowrap" in e.get("style", "") for e in edges), "엣지 라벨 줄바꿈 금지")

# 같은 설비에 비전 2대 → 라벨이 서로 다른 줄
check(drawio._cam_lanes([100, 150], 170) == [0, 1], "겹치는 카메라 라벨은 줄 분리")
check(drawio._cam_lanes([100, 400], 170) == [0, 0], "안 겹치면 같은 줄")

# 12b 실측 회귀(2026-09-28): 카메라 달린 설비의 콜아웃이 카메라를 가림 / 빈 존 라벨 / 배지 줄바꿈
spec2 = spec.model_copy(deep=True)
spec2.line_layout.cameras.append(spec2.line_layout.cameras[0].model_copy(update={"id": "vr", "at": "rbt"}))
spec2.line_layout.stations[-1].label = ""
root2 = ET.fromstring(drawio.build_drawio(spec2))


def rect(c):
    g = c.find("mxGeometry")
    return tuple(float(g.get(k)) for k in ("x", "y", "width", "height"))


def overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


c2 = root2.findall(".//mxCell")
callouts = [rect(c) for c in c2 if c.get("id", "").startswith("callout")]
cam_imgs = [rect(c) for c in c2 if c.get("id", "").startswith("img") and c.get("parent") == "1"
            and float(c.find("mxGeometry").get("width")) == 40]
cam_labels = [rect(c) for c in c2 if c.get("id", "").startswith("cam")]
check(bool(callouts) and not any(overlap(a, b) for a in callouts for b in cam_imgs + cam_labels),
      "콜아웃이 카메라·카메라 라벨과 안 겹침")
co_cx = [r[0] + r[2] / 2 for r in callouts]
check(all(not (b[0] <= x <= b[0] + b[2]) for x in co_cx for b in cam_imgs + cam_labels
          if b[1] > callouts[0][1]), "콜아웃 연결선이 아래쪽 카메라·라벨을 안 지남")
zones = [html.unescape(c.get("value", "")) for c in c2 if c.get("id", "").startswith("zone")]
check("완제품 배출" in zones[-1], "빈 존 라벨은 설비 종류 이름으로", zones[-1])
badges = [c.get("style", "") for c in cells if "arcSize=50" in c.get("style", "")]
check(badges and all("whiteSpace=nowrap" in b for b in badges), "배지 줄바꿈 금지")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, d in FAIL:
    print("  FAIL", label, d)
sys.exit(1 if FAIL else 0)
