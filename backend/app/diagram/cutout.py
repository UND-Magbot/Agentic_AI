"""설비 사진 배경 제거(누끼).

원본 제안서 개념도의 설비들은 배경이 없는 상태로 라인 위에 떠 있다. 사진을 그대로
넣으면 사각 프레임과 주변 배경이 같이 들어와 도면이 지저분해진다.

두 가지 경로를 쓴다:
  · uniform  — 흰/단색 배경(제품 카탈로그 샷)은 모서리에서 flood fill. 빠르고 깨끗하다.
  · ai       — 현장 사진처럼 배경이 복잡하면 rembg(U2-Net). 느리지만 견고하다.
auto 는 테두리 균일도를 재서 둘 중 하나를 고른다.

누끼 뒤에는 반드시 여백을 잘라낸다(trim). 그래야 설비가 station 폭을 꽉 채워
원본 개념도처럼 같은 바닥선에 선다.
"""
from __future__ import annotations

import io
from typing import Literal

Method = Literal["auto", "uniform", "ai", "none"]

_AI_SESSION = None


def _load(data: bytes):
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    return im.convert("RGBA")


def _dump(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def border_uniformity(data: bytes) -> tuple[float, tuple[int, int, int]]:
    """테두리 픽셀의 (표준편차, 대표색). 표준편차가 작을수록 단색 배경."""
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(np.int16)
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    std = float(border.std(axis=0).mean())
    med = tuple(int(x) for x in np.median(border, axis=0))
    return std, med  # type: ignore[return-value]


def cutout_uniform(data: bytes, tolerance: int = 34) -> bytes:
    """모서리에서 번져 나가며 단색 배경만 투명하게 만든다.

    설비 내부의 밝은 면(스테인리스 등)은 배경과 연결돼 있지 않으므로 살아남는다.
    단순 색상 임계값으로 지우면 설비 몸통까지 뚫리는데, flood fill 은 그 사고를 막는다.
    """
    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(data)).convert("RGBA")
    a = np.asarray(im).astype(np.int16)
    h, w = a.shape[:2]
    rgb = a[:, :, :3]

    corners = [rgb[0, 0], rgb[0, w - 1], rgb[h - 1, 0], rgb[h - 1, w - 1]]
    seed = np.median(np.stack(corners), axis=0)

    # 배경 후보: 시드색과 충분히 가까운 픽셀
    dist = np.abs(rgb - seed).max(axis=2)
    similar = dist <= tolerance

    # 가장자리에서 시작해 연결된 영역만 배경으로 인정 (BFS by scipy 없이 구현)
    try:
        from scipy import ndimage  # type: ignore

        lbl, n = ndimage.label(similar)
        edge_labels = set(lbl[0].tolist()) | set(lbl[-1].tolist())
        edge_labels |= set(lbl[:, 0].tolist()) | set(lbl[:, -1].tolist())
        edge_labels.discard(0)
        bg = np.isin(lbl, list(edge_labels))
    except Exception:
        import cv2  # opencv 로 대체 (프로젝트에 이미 있다)

        mask = (similar.astype(np.uint8) * 255)
        num, lbl = cv2.connectedComponents(mask, connectivity=4)
        edge_labels = set(lbl[0].tolist()) | set(lbl[-1].tolist())
        edge_labels |= set(lbl[:, 0].tolist()) | set(lbl[:, -1].tolist())
        edge_labels.discard(0)
        bg = np.isin(lbl, list(edge_labels))

    alpha = np.where(bg, 0, 255).astype(np.uint8)

    # 경계 1px 를 부드럽게 — 톱니 방지
    try:
        import cv2

        alpha = cv2.GaussianBlur(alpha, (3, 3), 0)
    except Exception:
        pass

    out = a.copy()
    out[:, :, 3] = alpha
    return _dump(Image.fromarray(out.astype(np.uint8), "RGBA"))


def cutout_ai(data: bytes, model: str = "u2net") -> bytes:
    """rembg(U2-Net) 로 배경 제거. 첫 호출 때 모델을 내려받는다(~170MB)."""
    global _AI_SESSION
    from rembg import new_session, remove

    if _AI_SESSION is None:
        _AI_SESSION = new_session(model)
    return remove(data, session=_AI_SESSION)


def trim(data: bytes, pad: int = 2, alpha_threshold: int = 12) -> bytes:
    """투명 여백 제거. 설비가 프레임을 꽉 채우도록."""
    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(data)).convert("RGBA")
    a = np.asarray(im)
    mask = a[:, :, 3] > alpha_threshold
    if not mask.any():
        return data
    ys, xs = np.where(mask)
    top, bot = max(int(ys.min()) - pad, 0), min(int(ys.max()) + pad + 1, a.shape[0])
    left, right = max(int(xs.min()) - pad, 0), min(int(xs.max()) + pad + 1, a.shape[1])
    return _dump(im.crop((left, top, right, bot)))


def add_ground_shadow(data: bytes, opacity: int = 58) -> bytes:
    """바닥에 타원 그림자를 깐다. 설비가 공중에 뜬 느낌을 없앤다."""
    from PIL import Image, ImageDraw, ImageFilter

    im = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = im.size
    pad_h = max(int(h * 0.07), 6)

    canvas = Image.new("RGBA", (w, h + pad_h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h + pad_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    ew, eh = int(w * 0.62), max(int(pad_h * 1.5), 8)
    d.ellipse(
        [(w - ew) // 2, h + pad_h - eh - 1, (w + ew) // 2, h + pad_h - 1],
        fill=(40, 55, 75, opacity),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(pad_h * 0.45, 3)))

    canvas.alpha_composite(shadow)
    canvas.alpha_composite(im, (0, 0))
    return _dump(canvas)


def remove_background(
    data: bytes,
    *,
    method: Method = "auto",
    uniform_std_max: float = 26.0,
    do_trim: bool = True,
    shadow: bool = True,
) -> tuple[bytes, str]:
    """배경 제거 파이프라인. (결과 PNG, 사용한 방법) 을 돌려준다.

    실패하면 원본을 그대로 돌려준다 — 누끼가 안 되더라도 개념도는 나와야 한다.
    """
    if method == "none":
        return data, "none"

    chosen = method
    if method == "auto":
        try:
            std, _ = border_uniformity(data)
            chosen = "uniform" if std <= uniform_std_max else "ai"
        except Exception:
            chosen = "ai"

    try:
        cut = cutout_uniform(data) if chosen == "uniform" else cutout_ai(data)
    except Exception:
        # ai 실패 시 단색 경로로 한 번 더 시도
        try:
            cut, chosen = cutout_uniform(data), "uniform(fallback)"
        except Exception:
            return data, "failed"

    try:
        if do_trim:
            cut = trim(cut)
        if shadow:
            cut = add_ground_shadow(cut)
    except Exception:
        pass
    return cut, chosen


def transparent_ratio(data: bytes) -> float:
    """투명 픽셀 비율. 0에 가까우면 누끼가 안 된 것, 1에 가까우면 다 지워진 것."""
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
    return float((a[:, :, 3] < 12).mean())
