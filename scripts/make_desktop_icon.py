"""바탕화면 바로가기 아이콘(사용자 2026-10-29) — 회사 로고 UND-logo2.png(frontend/public/und-logo2.png: UD 마크 + UND ROBOTICS
+ (주)유엔디로보틱스)를 흰 둥근 정사각형 위에.

    python scripts/make_desktop_icon.py <출력 폴더>   → und-cortex.ico(16~256) · und_cortex_1024.png · icon_preview.png
    만든 und-cortex.ico 를 frontend/public/ 에 둔다(설치 파일이 이 주소에서 받아 PC 에 저장한다).
로고 둘레의 흰 여백을 잘라 가운데 놓는다(옅은 회색·압축 잡티는 흰색으로). 아이콘은 최대 256px 이라 원본(411×215)을 줄이기만 한다.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

OUT = Path(sys.argv[1])
SRC = Path(__file__).resolve().parents[1] / "frontend" / "public" / "und-logo2.png"
N = 1024
PAD = 0.08                     # 정사각형 가장자리 여백(폭 기준)

a = np.array(Image.open(SRC).convert("RGB")).astype(int)
ink = a.sum(2) < 660                                       # 흰 바탕·옅은 회색이 아닌 곳 = 로고
ys, xs = np.where(ink)
a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
pale = (a.min(2) > 200) & (a.max(2) - a.min(2) < 25)       # 옅은 회색·흰색(압축 잡티 포함) → 흰색
a[pale] = 255
logo = Image.fromarray(a.astype(np.uint8))
# 흰색을 투명으로(가장자리는 밝기만큼 반투명) — 흰 판 위에 올릴 때 경계가 깨끗하게
alpha = np.clip((765 - a.sum(2)) * 255 / 120, 0, 255).astype(np.uint8)
logo.putalpha(Image.fromarray(alpha))

im = Image.new("RGBA", (N, N), (0, 0, 0, 0))
ImageDraw.Draw(im).rounded_rectangle((0, 0, N - 1, N - 1), radius=int(N * 0.2), fill=(255, 255, 255, 255))
w = int(N * (1 - 2 * PAD))
h = round(logo.height * w / logo.width)
im.alpha_composite(logo.resize((w, h), Image.LANCZOS), ((N - w) // 2, (N - h) // 2))

OUT.mkdir(parents=True, exist_ok=True)
im.save(OUT / "und_cortex_1024.png")
im.save(OUT / "und-cortex.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
prev = Image.new("RGB", (900, 300), (40, 44, 52))
x = 20
for sz in (256, 128, 64, 48, 32, 16):
    t = im.resize((sz, sz), Image.LANCZOS)
    prev.paste(t, (x, 150 - sz // 2), t)
    x += sz + 30
prev.save(OUT / "icon_preview.png")
