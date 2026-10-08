"""제안서 본문 → pptx 디자인 버전 (대외 제출용). 심플 버전(pptx_render.py)과 같은 본문·같은 검증을 쓰고
시각 완성도만 높인다 — 계약 중인 고객사에 내는 문서라 "신경 쓴 티"가 나야 한다(사용자 요청 2026-09-28).

구성: 표지 → 목차 → [장 구분 → 섹션 슬라이드]×4장 → 마무리.
  Ⅰ 제안 개요(핵심 메시지·현황) / Ⅱ 제안 내용(리스크·단계·구성·성공 기준) /
  Ⅲ 근거와 검증(요구사항 대응표·참고 사례) / Ⅳ 향후 진행(확인 사항·다음 단계)
디자인 요소: 네이비 그라데이션 표지·장 구분, 장 위치 표시(breadcrumb), 오렌지 세로 막대 제목,
"핵심 메시지" 강조 상자, 그림자 카드 + 원형 번호 + 옅은 큰 숫자, 오각형 화살표 단계, 반영 현황 숫자 타일,
사례 KPI 큰 숫자, 상태 색 점. 브랜드 색(네이비·오렌지)은 심플 버전과 같다.
글자 크기 맞춤·한글 줄바꿈(lang=ko-KR)·표 나눔은 pptx_render 의 측정 함수를 그대로 쓴다.
"""
from __future__ import annotations

import io
import math
import re
from datetime import datetime

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from ..company_context import COMPANY_NAME
from .body import Card, ProposalBody, Section
from .pptx_render import (
    DRAFT_MARK, LOGO, SLIDE_H, SLIDE_W, RenderReport, _column_font, _fitted_text, _lines_needed, _set_font,
    _text, _text_width_pt,
)

# ── 팔레트 ───────────────────────────────────────────────────────────────────
NAVY_DEEP = RGBColor(0x0A, 0x1A, 0x33)
NAVY = RGBColor(0x13, 0x29, 0x4B)
NAVY_MID = RGBColor(0x24, 0x44, 0x73)
ORANGE = RGBColor(0xF1, 0x82, 0x20)
ORANGE_LIGHT = RGBColor(0xF7, 0xA8, 0x5C)
INK = RGBColor(0x1F, 0x29, 0x37)
GREY = RGBColor(0x5B, 0x67, 0x7C)
MUTED = RGBColor(0x94, 0xA0, 0xB4)
HAIR = RGBColor(0xE3, 0xE8, 0xF0)
TINT = RGBColor(0xF3, 0xF6, 0xFB)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
OK_GREEN = RGBColor(0x1E, 0x8E, 0x5A)
AMBER = RGBColor(0xE0, 0x8E, 0x0B)
RED = RGBColor(0xC6, 0x3B, 0x2F)

MARGIN = 0.7
CONTENT_W = SLIDE_W - 2 * MARGIN
TOP = 2.3                  # 핵심 메시지 상자 아래 본문 시작
BOTTOM = 6.72              # 바닥글 위
TAKEAWAY_H = 0.6

CHAPTERS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("Ⅰ", "제안 개요", "Overview", ("key_message", "situation")),
    ("Ⅱ", "제안 내용", "Proposal", ("risk", "solution", "system", "effects")),
    ("Ⅲ", "근거와 검증", "Evidence", ("requirements", "references")),
    ("Ⅳ", "향후 진행", "Next Steps", ("open_items", "next_steps")),
)
_LAYOUT = {"solution": "steps", "next_steps": "steps", "requirements": "table", "references": "cases",
           "open_items": "checklist"}


# ── 도형 헬퍼 ────────────────────────────────────────────────────────────────

def _shape(slide, kind, x, y, w, h, fill=None, *, line=None, line_w=0.75, radius=None, alpha=None, shadow=False):
    # 음수·0 크기 도형이 하나라도 있으면 PowerPoint 가 파일을 못 연다(실측) → 하한.
    w, h = max(w, 0.01), max(h, 0.01)
    shp = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
        if alpha is not None:
            _alpha(shp, alpha)
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(line_w)
    if radius is not None:
        shp.adjustments[0] = radius
    if shadow:
        _shadow(shp)
    else:
        shp.shadow.inherit = False
    return shp


def _alpha(shp, opacity: float) -> None:
    """채우기 불투명도(0~1)."""
    clr = shp.fill._xPr.find(qn("a:solidFill")).find(qn("a:srgbClr"))
    a = etree.SubElement(clr, qn("a:alpha"))
    a.set("val", str(int(opacity * 100000)))


def _shadow(shp) -> None:
    """부드러운 아래쪽 그림자."""
    sppr = shp._element.spPr
    eff = etree.SubElement(sppr, qn("a:effectLst"))
    sh = etree.SubElement(eff, qn("a:outerShdw"), blurRad="190500", dist="38100", dir="5400000",
                          algn="t", rotWithShape="0")
    clr = etree.SubElement(sh, qn("a:srgbClr"), val="0A1A33")
    etree.SubElement(clr, qn("a:alpha"), val="14000")


def _gradient(fill, c1: RGBColor, c2: RGBColor, angle: float = 90) -> None:
    fill.gradient()
    fill.gradient_angle = angle
    stops = fill.gradient_stops
    stops[0].color.rgb, stops[0].position = c1, 0.0
    stops[1].color.rgb, stops[1].position = c2, 1.0


def _label(slide, x, y, w, h, text, size, color, *, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
           spacing: int | None = None, italic=False):
    tb = _text(slide, x, y, w, h, [(text, {"size": size, "color": color, "bold": bold, "italic": italic})],
               anchor=anchor, align=align, margin=0.02)
    if spacing is not None:
        for r in tb.text_frame.paragraphs[0].runs:
            r._r.get_or_add_rPr().set("spc", str(spacing))
    return tb


def _circle_no(slide, x, y, d, text, fill=ORANGE, size=13):
    c = _shape(slide, MSO_SHAPE.OVAL, x, y, d, d, fill)
    tf = c.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = text
    _set_font(r, size, WHITE, bold=True)
    return c


def _logo(slide, x, y, h, *, card=True) -> None:
    if not LOGO.exists():
        return
    w = h * 341 / 177
    if card:
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x - 0.08, y - 0.06, w + 0.16, h + 0.12, WHITE, radius=0.18)
    slide.shapes.add_picture(str(LOGO), Inches(x), Inches(y), height=Inches(h))


# ── 틀 ──────────────────────────────────────────────────────────────────────

class _Deck:
    def __init__(self, prs, body: ProposalBody):
        self.prs = prs
        self.body = body
        self.report = RenderReport()
        self.page = 0
        who = f"{body.customer} × {COMPANY_NAME}" if body.customer else COMPANY_NAME
        self.footer = f"{who}   |   {body.title or '제안서'}"

    def slide(self, bg: RGBColor | None = WHITE):
        s = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        if bg is not None:
            s.background.fill.solid()
            s.background.fill.fore_color.rgb = bg
        self.page += 1
        return s


def _dark_bg(slide) -> None:
    _gradient(slide.background.fill, NAVY_DEEP, NAVY_MID, angle=135)
    # 기하 장식 — 우상단 큰 원 두 개(옅게), 좌하단 오렌지 사선 막대
    _shape(slide, MSO_SHAPE.OVAL, SLIDE_W - 4.2, -2.2, 6.0, 6.0, WHITE, alpha=0.04)
    _shape(slide, MSO_SHAPE.OVAL, SLIDE_W - 2.6, -0.9, 3.4, 3.4, WHITE, alpha=0.05)
    _shape(slide, MSO_SHAPE.OVAL, -1.4, SLIDE_H - 1.6, 3.2, 3.2, ORANGE, alpha=0.08)


def _footer(deck: _Deck, slide, x0: float = MARGIN) -> None:
    """x0: 바닥글 시작 위치(목차처럼 왼쪽에 어두운 판넬이 있으면 그 오른쪽부터)."""
    _shape(slide, MSO_SHAPE.RECTANGLE, x0, 6.95, SLIDE_W - MARGIN - x0, 0.012, HAIR)
    _label(slide, x0, 7.02, 9, 0.3, deck.footer, 8.5, MUTED)
    _label(slide, SLIDE_W - MARGIN - 3.0, 7.02, 3.0, 0.3, f"{DRAFT_MARK}    {deck.page:02d}", 8.5, MUTED,
           align=PP_ALIGN.RIGHT)


def _content_slide(deck: _Deck, chapter: tuple, section: Section, title_suffix: str = ""):
    s = deck.slide(WHITE)
    no, name, eng, _ = chapter
    _label(s, MARGIN, 0.32, 6, 0.3, f"{no}.  {name}   ·   {eng.upper()}", 9.5, ORANGE, bold=True, spacing=100)
    _shape(s, MSO_SHAPE.RECTANGLE, MARGIN, 0.72, 0.07, 0.52, ORANGE)
    _fitted_text(s, deck.report, f"{section.title} 제목", MARGIN + 0.2, 0.62, CONTENT_W - 2.0, 0.72,
                 [section.title + title_suffix], max_pt=28, min_pt=20, color=NAVY, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    _logo(s, SLIDE_W - MARGIN - 1.05, 0.34, 0.5, card=False)
    if section.headline:
        _shape(s, MSO_SHAPE.RECTANGLE, MARGIN, 1.5, CONTENT_W, 0.58, TINT)
        _shape(s, MSO_SHAPE.RECTANGLE, MARGIN, 1.5, 0.05, 0.58, NAVY)
        _label(s, MARGIN + 0.22, 1.5, 1.4, 0.58, "KEY MESSAGE", 8.5, ORANGE, bold=True,
               anchor=MSO_ANCHOR.MIDDLE, spacing=150)
        _fitted_text(s, deck.report, f"{section.title} 핵심 메시지", MARGIN + 1.55, 1.5, CONTENT_W - 1.75, 0.58,
                     [section.headline], max_pt=14.5, min_pt=11, color=NAVY, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    _footer(deck, s)
    return s


def _bottom(section: Section) -> float:
    return BOTTOM - (TAKEAWAY_H + 0.2 if section.banner else 0)


def _takeaway(deck: _Deck, slide, section: Section) -> None:
    if not section.banner:
        return
    y = BOTTOM - TAKEAWAY_H
    bar = _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, MARGIN, y, CONTENT_W, TAKEAWAY_H, NAVY, radius=0.5)
    _gradient(bar.fill, NAVY, NAVY_MID, angle=0)
    label = section.banner_label or "요지"
    pill_w = max(1.0, _text_width_pt(label, 10.5) / 72 + 0.45)
    pill = _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, MARGIN + 0.14, y + 0.12, pill_w, TAKEAWAY_H - 0.24, ORANGE,
                  radius=0.5)
    p = pill.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    pill.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    r = p.add_run()
    r.text = label
    _set_font(r, 10.5, WHITE, bold=True)
    _fitted_text(slide, deck.report, f"{section.title} 강조", MARGIN + pill_w + 0.35, y, CONTENT_W - pill_w - 0.6,
                 TAKEAWAY_H, [section.banner], max_pt=13.5, min_pt=10, color=WHITE, bold=True,
                 anchor=MSO_ANCHOR.MIDDLE)


# ── 표지·목차·장 구분·마무리 ──────────────────────────────────────────────────

def _cover(deck: _Deck, now: datetime) -> None:
    s = deck.slide(None)
    _dark_bg(s)
    body = deck.body
    _logo(s, MARGIN + 0.08, 0.62, 0.62)
    chip = _shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, SLIDE_W - MARGIN - 1.45, 0.72, 1.45, 0.36, None,
                  line=ORANGE_LIGHT, line_w=1, radius=0.5)
    p = chip.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = DRAFT_MARK
    _set_font(r, 10, ORANGE_LIGHT, bold=True)
    _label(s, MARGIN, 2.05, 8, 0.35, "ROBOT AUTOMATION PROPOSAL", 11, ORANGE_LIGHT, bold=True, spacing=300)
    _fitted_text(s, deck.report, "표지 제목", MARGIN, 2.45, 9.6, 1.9, [body.title or "제안서"],
                 max_pt=38, min_pt=26, color=WHITE, bold=True, anchor=MSO_ANCHOR.TOP)
    _shape(s, MSO_SHAPE.RECTANGLE, MARGIN, 4.5, 1.2, 0.06, ORANGE)
    if body.subtitle:
        _fitted_text(s, deck.report, "표지 부제", MARGIN, 4.7, 9.6, 0.8, [body.subtitle],
                     max_pt=17, min_pt=12, color=RGBColor(0xC9, 0xD3, 0xE3))
    who = ([("PREPARED FOR", body.customer)] if body.customer else []) + [
        ("PREPARED BY", COMPANY_NAME), ("DATE", f"{now:%Y. %m. %d}")]
    for i, (k, v) in enumerate(who):
        x = MARGIN + i * 3.1
        _label(s, x, 5.95, 2.9, 0.25, k, 8.5, MUTED, bold=True, spacing=150)
        _label(s, x, 6.2, 2.9, 0.4, v, 13, WHITE, bold=True)


def _agenda(deck: _Deck, chapters: list[tuple]) -> None:
    s = deck.slide(WHITE)
    panel = _shape(s, MSO_SHAPE.RECTANGLE, 0, 0, 4.1, SLIDE_H, NAVY)
    _gradient(panel.fill, NAVY_DEEP, NAVY_MID, angle=90)
    _shape(s, MSO_SHAPE.OVAL, -1.2, 4.6, 3.6, 3.6, ORANGE, alpha=0.1)
    _label(s, 0.7, 1.0, 3.2, 0.4, "CONTENTS", 12, ORANGE_LIGHT, bold=True, spacing=400)
    _label(s, 0.7, 1.45, 3.2, 0.8, "목차", 34, WHITE, bold=True)
    _shape(s, MSO_SHAPE.RECTANGLE, 0.72, 2.45, 0.9, 0.05, ORANGE)
    n = len(chapters)
    row_h = min(1.35, 5.6 / max(1, n))
    y0 = (SLIDE_H - row_h * n) / 2
    for i, (no, name, eng, secs) in enumerate(chapters):
        y = y0 + i * row_h
        _label(s, 4.9, y, 1.2, row_h, no, 34, ORANGE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
        _label(s, 6.0, y + 0.12, 6.5, 0.5, name, 20, NAVY, bold=True)
        _label(s, 6.0, y + 0.58, 6.5, 0.6, "  ·  ".join(secs), 11, GREY)
        if i < n - 1:
            _shape(s, MSO_SHAPE.RECTANGLE, 4.9, y + row_h - 0.01, 7.7, 0.01, HAIR)
    _footer(deck, s, x0=4.9)


def _divider(deck: _Deck, chapter: tuple, titles: list[str]) -> None:
    s = deck.slide(None)
    _dark_bg(s)
    no, name, eng, _ = chapter
    _label(s, MARGIN, 1.2, 5, 2.4, no, 120, WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    _shape(s, MSO_SHAPE.RECTANGLE, MARGIN + 0.05, 3.75, 1.0, 0.06, ORANGE)
    _label(s, MARGIN, 3.95, 8, 0.4, eng.upper(), 12, ORANGE_LIGHT, bold=True, spacing=300)
    _label(s, MARGIN, 4.35, 10, 0.9, name, 36, WHITE, bold=True)
    _label(s, MARGIN, 5.35, 11, 0.9, "   /   ".join(titles), 13, RGBColor(0xC9, 0xD3, 0xE3))


def _closing(deck: _Deck) -> None:
    s = deck.slide(None)
    _dark_bg(s)
    _logo(s, (SLIDE_W - 1.5) / 2, 1.7, 0.78)
    _label(s, 0, 3.0, SLIDE_W, 0.9, "감사합니다", 40, WHITE, bold=True, align=PP_ALIGN.CENTER)
    _shape(s, MSO_SHAPE.RECTANGLE, (SLIDE_W - 1.0) / 2, 4.05, 1.0, 0.05, ORANGE)
    _label(s, 0, 4.3, SLIDE_W, 0.5, "본 제안은 현장 확인 후 구체화됩니다. 확인 필요 사항에 대한 회신을 부탁드립니다.",
           13, RGBColor(0xC9, 0xD3, 0xE3), align=PP_ALIGN.CENTER)
    _label(s, 0, 4.85, SLIDE_W, 0.4, COMPANY_NAME, 12, ORANGE_LIGHT, bold=True, align=PP_ALIGN.CENTER, spacing=100)


# ── 본문 유형 ────────────────────────────────────────────────────────────────

_HEAD = 1.05     # 카드 윗부분(원형 번호 + 제목 + 구분선)


def _cards(deck: _Deck, chapter, section: Section) -> None:
    cards = section.cards or [Card("-", [])]
    per = 4 if len(cards) > 3 else len(cards)
    for start in range(0, len(cards), per):
        chunk = cards[start:start + per]
        s = _content_slide(deck, chapter, section, " (계속)" if start else "")
        top, bottom = TOP, _bottom(section) - 0.05
        gap = 0.3
        w = (CONTENT_W - gap * (len(chunk) - 1)) / len(chunk)
        size, need = _column_font([c.bullets for c in chunk], w - 0.5, bottom - top - _HEAD - 0.25, 15, 9.5)
        h = min(bottom - top, max(2.4, _HEAD + need + 0.35))
        y = top + (bottom - top - h) / 2
        for i, c in enumerate(chunk):
            x = MARGIN + i * (w + gap)
            _shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, WHITE, line=HAIR, radius=0.05, shadow=True)
            _label(s, x + w - 1.25, y + 0.02, 1.15, 0.9, f"{start + i + 1:02d}", 40, TINT, bold=True,
                   align=PP_ALIGN.RIGHT)
            _circle_no(s, x + 0.25, y + 0.28, 0.46, str(start + i + 1), fill=ORANGE if i % 2 == 0 else NAVY)
            _fitted_text(s, deck.report, f"{section.title} 카드 제목", x + 0.85, y + 0.2, w - 1.6, 0.64,
                         [c.heading or "-"], max_pt=15, min_pt=11, color=NAVY, bold=True, anchor=MSO_ANCHOR.MIDDLE)
            _shape(s, MSO_SHAPE.RECTANGLE, x + 0.25, y + 0.93, w - 0.5, 0.012, HAIR)
            _fitted_text(s, deck.report, f"{section.title} 카드 본문", x + 0.25, y + _HEAD + 0.08, w - 0.45,
                         h - _HEAD - 0.2, c.bullets, max_pt=15, min_pt=9.5, bullet=True, size=size)
        _takeaway(deck, s, section)


def _steps(deck: _Deck, chapter, section: Section) -> None:
    cards = section.cards or [Card("-", [])]
    s = _content_slide(deck, chapter, section)
    top, bottom = TOP, _bottom(section) - 0.05
    n = len(cards)
    gap = 0.12
    w = (CONTENT_W - gap * (n - 1)) / n
    head_h = 0.78
    size, need = _column_font([c.bullets for c in cards], w - 0.45, bottom - top - head_h - 0.45, 15, 9.5)
    h = min(bottom - top, max(2.4, head_h + need + 0.5))
    y = top + (bottom - top - h) / 2
    for i, c in enumerate(cards):
        x = MARGIN + i * (w + gap)
        kind = MSO_SHAPE.PENTAGON if i < n - 1 else MSO_SHAPE.RECTANGLE
        arrow = _shape(s, kind, x, y, w + (0.22 if i < n - 1 else 0), head_h, NAVY)
        _gradient(arrow.fill, NAVY if i < n - 1 else ORANGE, NAVY_MID if i < n - 1 else ORANGE_LIGHT, angle=0)
        _label(s, x + 0.25, y + 0.08, 1.4, 0.28, f"STEP {i + 1:02d}", 9, ORANGE_LIGHT if i < n - 1 else WHITE,
               bold=True, spacing=150)
        _fitted_text(s, deck.report, f"{section.title} 단계 제목", x + 0.25, y + 0.32, w - 0.45, 0.42,
                     [re.sub(r"^\s*\d+\s*단계\s*[:：]\s*", "", c.heading or f"{i + 1}단계")],
                     max_pt=14, min_pt=10, color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
        card = _shape(s, MSO_SHAPE.RECTANGLE, x, y + head_h + 0.12, w, h - head_h - 0.12, TINT)
        card.line.fill.background()
        _fitted_text(s, deck.report, f"{section.title} 단계 본문", x + 0.2, y + head_h + 0.28, w - 0.35,
                     h - head_h - 0.4, c.bullets, max_pt=15, min_pt=9.5, bullet=True, size=size)
    _takeaway(deck, s, section)


_STATUS_COLOR = (("반영", OK_GREEN), ("확인 필요", AMBER), ("미반영", RED))


def _row_h(cells, widths, size) -> float:
    return max(_lines_needed([c], w - 0.12, size) for c, w in zip(cells, widths)) * size * 1.2 / 72 + 0.16


def _table(deck: _Deck, chapter, section: Section) -> None:
    head, rows = section.table[0], section.table[1:]
    widths = [3.25, 3.0, 4.35, 1.33] if len(head) == 4 else [CONTENT_W / len(head)] * len(head)
    size = 10.5
    stats = [("대응표 항목", len(rows)), ("반영", sum(r[-1].startswith("반영") for r in rows)),
             ("확인 필요", sum(r[-1].startswith("확인") for r in rows)),
             ("미반영", sum(r[-1].startswith("미반영") for r in rows))]
    tiles_h = 0.85
    avail = BOTTOM - TOP - tiles_h - 0.25
    pages: list[list[list[str]]] = [[]]
    used = _row_h(head, widths, size)
    for r in rows:
        rh = _row_h(r, widths, size)
        if pages[-1] and used + rh > avail:
            pages.append([])
            used = _row_h(head, widths, size)
        pages[-1].append(r)
        used += rh
    for pi, page_rows in enumerate(pages):
        s = _content_slide(deck, chapter, section, f" ({pi + 1}/{len(pages)})" if len(pages) > 1 else "")
        tw = (CONTENT_W - 0.3 * 3) / 4
        for i, (k, v) in enumerate(stats):
            x = MARGIN + i * (tw + 0.3)
            color = [NAVY, OK_GREEN, AMBER, RED][i]
            _shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, TOP, tw, tiles_h, WHITE, line=HAIR, radius=0.12, shadow=True)
            _shape(s, MSO_SHAPE.RECTANGLE, x, TOP + 0.14, 0.06, tiles_h - 0.28, color)
            _label(s, x + 0.25, TOP + 0.08, 2.0, 0.3, k, 10, GREY, bold=True)
            _label(s, x + 0.25, TOP + 0.3, tw - 0.4, 0.55, f"{v}건", 22, color, bold=True, anchor=MSO_ANCHOR.MIDDLE)
        all_rows = [head] + page_rows
        ty = TOP + tiles_h + 0.25
        shp = s.shapes.add_table(len(all_rows), len(head), Inches(MARGIN), Inches(ty), Inches(sum(widths)),
                                 Inches(0.4 * len(all_rows)))
        tbl = shp.table
        for j, wv in enumerate(widths):
            tbl.columns[j].width = Inches(wv)
        for i, row in enumerate(all_rows):
            tbl.rows[i].height = Emu(Inches(_row_h(row, widths, size)))
            for j, val in enumerate(row):
                cell = tbl.cell(i, j)
                cell.fill.solid()
                cell.fill.fore_color.rgb = NAVY if i == 0 else (TINT if i % 2 == 0 else WHITE)
                cell.margin_left = cell.margin_right = Inches(0.08)
                cell.margin_top = cell.margin_bottom = Inches(0.05)
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                p = cell.text_frame.paragraphs[0]
                cell.text_frame.word_wrap = True
                if i > 0 and j == len(row) - 1:
                    color = next((c for k, c in _STATUS_COLOR if val.startswith(k)), INK)
                    dot = p.add_run()
                    dot.text = "● "
                    _set_font(dot, size, color, bold=True)
                    r = p.add_run()
                    r.text = val
                    _set_font(r, size, color, bold=True)
                    continue
                r = p.add_run()
                r.text = val
                _set_font(r, size, WHITE if i == 0 else (NAVY if j == 0 else INK), bold=(i == 0 or j == 0))


# KPI 한 조각: "생산성 12.5% 증가"(압축형) 또는 "생산성 (생산C/T): 12.5% 증가"(구형). 라벨의 단위 괄호는 뺀다.
_KPI_RE = re.compile(r"^(?P<label>[^:]+?)\s*(?:\([^)]*\))?\s*:?\s+(?P<value>N/A|\d[\d.,]*\s*[%명년원개대]?)\s*"
                     r"(?P<dir>증가|감소|절감|충원|향상|단축)?$")
_KPI_SPLIT_RE = re.compile(r"\s·\s|,\s(?=[가-힣])")


def _text_h(paragraphs: list[str], width_in: float, size: float) -> float:
    return (_lines_needed(paragraphs, width_in, size) * size * 1.25 + 8) / 72


def _parse_kpis(line: str) -> list[tuple[str, str, str]]:
    out = []
    for part in _KPI_SPLIT_RE.split(line):
        m = _KPI_RE.match(part.strip())
        if m and m.group("value") != "N/A":      # N/A 는 큰 숫자로 강조할 값이 아니다
            out.append((m.group("label").strip(), m.group("value").replace(" ", ""), m.group("dir") or ""))
    return out


def _cases(deck: _Deck, chapter, section: Section) -> None:
    s = _content_slide(deck, chapter, section)
    cards = section.cards
    # 사례 섹션의 "유의" 문구는 하단 띠 대신 한 줄 각주로 — 띠가 카드 높이를 잡아먹어 KPI 숫자가 늘 빠졌다(실측).
    top, bottom = TOP, BOTTOM - (0.35 if section.banner else 0.05)
    gap = 0.3
    n = max(1, len(cards))
    w = (CONTENT_W - gap * (n - 1)) / n
    h = bottom - top
    for i, c in enumerate(cards):
        x = MARGIN + i * (w + gap)
        kpi_line = next((b.split(": ", 1)[1] for b in c.bullets if b.startswith("해당 기업 도입 효과:")), "")
        src = next((b.split(": ", 1)[1] for b in c.bullets if b.startswith("출처:")), "")
        aspect = next((b for b in c.bullets if b.startswith("참고 관점")), "")
        rest = [b for b in c.bullets if not b.startswith(("해당 기업 도입 효과:", "출처:", "참고 관점", "업종:"))]
        _shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, top, w, h, WHITE, line=HAIR, radius=0.04, shadow=True)
        band = _shape(s, MSO_SHAPE.RECTANGLE, x, top, w, 0.95, NAVY)
        _gradient(band.fill, NAVY, NAVY_MID, angle=0)
        ref, _, title = c.heading.partition(" ")[2].partition(" ") if c.heading.startswith("사례 ") else ("", "", c.heading)
        _label(s, x + 0.22, top + 0.1, w - 0.4, 0.28, f"CASE {ref}", 9, ORANGE_LIGHT, bold=True, spacing=150)
        _fitted_text(s, deck.report, "사례 제목", x + 0.22, top + 0.35, w - 0.4, 0.58, [title or c.heading],
                     max_pt=13, min_pt=9.5, color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
        y = top + 1.1
        foot = 0.5 if src else 0
        if aspect:
            txt = aspect.replace("참고 관점", "참고 관점 ", 1)
            box_h = min(1.0, max(0.55, _text_h([txt], w - 0.6, 10) + 0.1))
            _shape(s, MSO_SHAPE.RECTANGLE, x + 0.2, y, w - 0.4, box_h, TINT)
            _shape(s, MSO_SHAPE.RECTANGLE, x + 0.2, y, 0.05, box_h, ORANGE)
            _fitted_text(s, deck.report, "사례 참고 관점", x + 0.35, y + 0.04, w - 0.6, box_h - 0.08, [txt],
                         max_pt=10, min_pt=8, color=NAVY)
            y += box_h + 0.12
        kpis = _parse_kpis(kpi_line)[:3]
        room = top + h - y - foot - 0.1
        if kpis and room >= 1.3:          # KPI 숫자 줄 자리가 없으면(하단 강조 띠가 있는 좁은 슬라이드) 생략
            kw = (w - 0.4) / len(kpis)
            for j, (lab, val, d) in enumerate(kpis):
                kx = x + 0.2 + j * kw
                _fitted_text(s, deck.report, "사례 KPI", kx, y, kw - 0.05, 0.42, [val.replace(" ", "")],
                             max_pt=19, min_pt=11, color=ORANGE, bold=True)
                _fitted_text(s, deck.report, "사례 KPI 라벨", kx, y + 0.42, kw - 0.05, 0.36, [lab if d and d in lab else f"{lab} {d}".strip()],
                             max_pt=8.5, min_pt=7, color=GREY)
            y += 0.85
        room = top + h - y - foot - 0.1
        if rest and room > 0.25:
            _fitted_text(s, deck.report, "사례 본문", x + 0.22, y, w - 0.4, room, rest,
                         max_pt=10.5, min_pt=8, bullet=True)
        if src:
            _shape(s, MSO_SHAPE.RECTANGLE, x + 0.2, top + h - foot - 0.02, w - 0.4, 0.01, HAIR)
            _fitted_text(s, deck.report, "사례 출처", x + 0.22, top + h - foot, w - 0.4, foot - 0.05, [src],
                         max_pt=8.5, min_pt=7, color=MUTED, italic=True)
    if section.banner:
        _label(s, MARGIN, BOTTOM - 0.26, CONTENT_W, 0.28, f"※ {section.banner}", 9.5, GREY, italic=True)


def _checklist(deck: _Deck, chapter, section: Section) -> None:
    items = [b for c in section.cards for b in c.bullets]
    s = _content_slide(deck, chapter, section)
    top, bottom = TOP, _bottom(section) - 0.05
    cols = 2 if len(items) > 5 else 1
    per = math.ceil(len(items) / cols) if items else 0
    gap = 0.4
    w = (CONTENT_W - gap * (cols - 1)) / cols
    rows = max(1, per)
    row_h = min(0.85, (bottom - top) / rows)
    size = 13.0
    for idx, item in enumerate(items):
        ci, ri = divmod(idx, per)
        x = MARGIN + ci * (w + gap)
        y = top + ri * row_h
        # 확인 상자·번호·질문을 모두 행의 세로 가운데에 맞춘다(실측: 번호는 위, 질문은 아래로 어긋남).
        box = 0.34
        _shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y + (row_h - box) / 2, box, box, None, line=ORANGE,
               line_w=1.5, radius=0.2)
        _label(s, x + 0.48, y, 0.5, row_h, f"{idx + 1:02d}", 12, ORANGE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
        _fitted_text(s, deck.report, "확인 항목", x + 1.0, y + 0.04, w - 1.05, row_h - 0.08, [item],
                     max_pt=size, min_pt=9.5, color=INK, anchor=MSO_ANCHOR.MIDDLE)
        line = _shape(s, MSO_SHAPE.RECTANGLE, x, y + row_h - 0.01, w, 0.01, HAIR)
        line.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    _takeaway(deck, s, section)


_RENDER = {"cards": _cards, "steps": _steps, "table": _table, "cases": _cases, "checklist": _checklist}


def _chapters(body: ProposalBody) -> list[tuple[tuple, list[Section]]]:
    """본문 섹션을 장에 배치. 모르는 섹션은 마지막 장으로."""
    placed: set[str] = set()
    out = []
    for ch in CHAPTERS:
        secs = [s for s in body.sections if s.id in ch[3]]
        placed |= {s.id for s in secs}
        out.append((ch, secs))
    extra = [s for s in body.sections if s.id not in placed]
    if extra:
        out[-1] = (out[-1][0], out[-1][1] + extra)
    return [(ch, secs) for ch, secs in out if secs]


CONCEPT_TITLE = "적용 컨셉 이미지"
CONCEPT_NOTE = "※ 제안 이해를 돕기 위한 컨셉 이미지입니다. 실제 설비 구성·외형·배치와 다를 수 있습니다."


def _concept(deck: _Deck, chapter, image: bytes, system: Section | None) -> None:
    """외부 생성 컨셉 이미지 + 오른쪽 구성 요약(시스템 구성 카드 제목·첫 항목)."""
    from PIL import Image

    sec = Section("concept", CONCEPT_TITLE, "제안 구성이 현장에 적용된 모습(컨셉)", [])
    s = _content_slide(deck, chapter, sec)
    top, bottom = TOP, BOTTOM - 0.35
    panel_w = 3.6
    box_w, box_h = CONTENT_W - panel_w - 0.3, bottom - top
    w_px, h_px = Image.open(io.BytesIO(image)).size
    scale = min(box_w / w_px, box_h / h_px)
    iw, ih = w_px * scale, h_px * scale
    ix, iy = MARGIN + (box_w - iw) / 2, top + (box_h - ih) / 2
    _shape(s, MSO_SHAPE.RECTANGLE, ix - 0.04, iy - 0.04, iw + 0.08, ih + 0.08, WHITE, line=HAIR, shadow=True)
    s.shapes.add_picture(io.BytesIO(image), Inches(ix), Inches(iy), Inches(iw), Inches(ih))
    px = MARGIN + box_w + 0.3
    _shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, px, top, panel_w, box_h, TINT, radius=0.04)
    _label(s, px + 0.25, top + 0.2, panel_w - 0.4, 0.3, "SYSTEM", 9, ORANGE, bold=True, spacing=150)
    _label(s, px + 0.25, top + 0.45, panel_w - 0.4, 0.45, "제안 구성 요약", 15, NAVY, bold=True)
    items = [(c.heading or "-") + (f" — {c.bullets[0]}" if c.bullets else "") for c in (system.cards if system else [])]
    if items:
        _fitted_text(s, deck.report, "컨셉 구성 요약", px + 0.25, top + 1.0, panel_w - 0.45, box_h - 1.2, items,
                     max_pt=12, min_pt=9, bullet=True)
    _label(s, MARGIN, BOTTOM - 0.26, CONTENT_W, 0.28, CONCEPT_NOTE, 9.5, GREY, italic=True)


def to_pptx_premium(body: ProposalBody, *, now: datetime | None = None,
                    concept_image: bytes | None = None) -> tuple[bytes, RenderReport]:
    """concept_image: 외부 생성 컨셉 이미지(PNG). 있으면 Ⅱ장 '시스템 구성' 뒤에 한 장을 넣는다."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(SLIDE_W), Inches(SLIDE_H)
    deck = _Deck(prs, body)
    groups = _chapters(body)
    _cover(deck, now or datetime.now())
    _agenda(deck, [(ch[0], ch[1], ch[2], tuple(s.title for s in secs)) for ch, secs in groups])
    for ch, secs in groups:
        _divider(deck, ch, [s.title for s in secs])
        for sec in secs:
            kind = _LAYOUT.get(sec.id, "cards")
            if kind == "table" and not sec.table:
                kind = "cards"
            _RENDER[kind](deck, ch, sec)
            if sec.id == "system" and concept_image:
                _concept(deck, ch, concept_image, sec)
    _closing(deck)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue(), deck.report
