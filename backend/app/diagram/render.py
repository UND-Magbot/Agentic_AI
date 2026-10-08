"""개념도 렌더러 — 스펙(JSON) → HTML → PNG.

이미지 "생성" 이 아니라 "조판" 이다. 한글 라벨·표·수치·실제 사진이 전부
원본 그대로 픽셀에 찍힌다 (diffusion 모델이 못 하는 지점).

렌더 경로: Jinja2 → HTML 문자열 → Playwright(Chromium) → PNG bytes
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import layout as layout_mod
from . import symbols
from .spec import ConceptMapSpec, ImageRef

_TPL_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(_TPL_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _data_uri(path: str) -> str | None:
    """로컬 이미지 → data URI. Playwright 가 파일 접근 없이 바로 그린다."""
    p = Path(path)
    if not p.is_file():
        return None
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def _art(image: ImageRef, kind: str = "") -> Markup:
    """ImageRef → 실제 사진 <img> 또는 대체 SVG 심볼.

    사진이 없어도 개념도가 완성되도록 항상 무언가를 반환한다.
    """
    if image and image.src:
        if image.src.startswith("data:"):
            return Markup(f'<img src="{image.src}" alt="{image.alt}"/>')
        uri = _data_uri(image.src)
        if uri:
            return Markup(f'<img src="{uri}" alt="{image.alt}"/>')
    key = (image.symbol if image else "") or kind
    if key:
        return Markup(symbols.station_svg(key))
    return Markup("")


def _sys_columns(diagram) -> list[list]:
    """SysNode 를 col 값으로 묶어 좌→우 단계 리스트로."""
    if diagram is None or not diagram.nodes:
        return []
    cols: dict[int, list] = {}
    for n in diagram.nodes:
        cols.setdefault(n.col, []).append(n)
    for c in cols.values():
        c.sort(key=lambda n: n.row)
    return [cols[k] for k in sorted(cols)]


def build_html(spec: ConceptMapSpec, width: int = 1600) -> str:
    """스펙 → 완성 HTML 문자열. (PNG 없이 미리보기·디버깅용으로도 쓴다)"""
    lay = layout_mod.compute(spec, width)

    cones_by: dict[str, list] = {}
    for c in lay.cams:
        if c.cone:
            cones_by.setdefault(c.station_id, []).append(c)

    n_panels = sum(
        [
            bool(spec.inspections),
            bool(spec.system_diagram and spec.system_diagram.nodes),
            bool(spec.tables),
        ]
    )
    bottom_cols = {3: "1.15fr 1fr 1.05fr", 2: "1fr 1fr", 1: "1fr"}.get(n_panels, "1fr")

    tpl = _env.get_template("concept_map.html.j2")
    return tpl.render(
        spec=spec,
        L=spec.line_layout,
        width=width,
        art=_art,
        camera_svg=Markup(symbols.camera_svg()),
        cams=lay.cams,
        cones_by=cones_by,
        callouts=lay.callouts,
        flows=lay.flows,
        line_pad_top=lay.line_pad_top,
        art_h=lay.art_h,
        stage_h=lay.stage_h,
        zone_h=layout_mod.ZONE_H,
        cone_w=lay.cone_w,
        outfeed_x=lay.outfeed_x,
        outfeed_y=lay.outfeed_y,
        sys_cols=_sys_columns(spec.system_diagram),
        insp_cols=min(len(spec.inspections), 4) or 1,
        bottom_cols=bottom_cols,
    )


async def render_png(
    spec: ConceptMapSpec,
    *,
    width: int = 1600,
    scale: int = 2,
) -> bytes:
    """스펙 → PNG bytes. scale=2 면 실제 3200px 폭의 인쇄 가능 해상도."""
    from playwright.async_api import async_playwright

    html = build_html(spec, width=width)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--force-color-profile=srgb"])
        try:
            page = await browser.new_page(
                viewport={"width": width, "height": 900},
                device_scale_factor=scale,
            )
            await page.set_content(html, wait_until="load")
            await page.wait_for_timeout(120)   # 웹폰트/SVG 레이아웃 안정화
            el = await page.query_selector(".sheet")
            return await (el.screenshot(type="png") if el
                          else page.screenshot(type="png", full_page=True))
        finally:
            await browser.close()


def render_png_sync(spec: ConceptMapSpec, *, width: int = 1600, scale: int = 2) -> bytes:
    """동기 컨텍스트(CLI·테스트)용. 이벤트 루프 안에서는 render_png 를 쓸 것."""
    from playwright.sync_api import sync_playwright

    html = build_html(spec, width=width)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb"])
        try:
            page = browser.new_page(
                viewport={"width": width, "height": 900},
                device_scale_factor=scale,
            )
            page.set_content(html, wait_until="load")
            page.wait_for_timeout(120)
            el = page.query_selector(".sheet")
            return el.screenshot(type="png") if el else page.screenshot(
                type="png", full_page=True
            )
        finally:
            browser.close()
