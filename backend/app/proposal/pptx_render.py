"""제안서 본문 → pptx (16:9). 사내 제안서 공통 스타일을 코드로 고정한 템플릿.

스타일 근거 — docs 의 기존 제안서 4건(넥시스·KEC·GS칼텍스 pptx, 한화오션 PDF) 공통점:
  16:9(13.33×7.5in), 밝은 회청색 바탕, 네이비 굵은 제목 + 회색 한 줄 요지 + 오렌지 구분선,
  흰 둥근 카드 + 오렌지·네이비 번호 칩, 네이비 머리글 표(줄무늬 행), 바닥글 "고객사 | 제안 공급사" + 쪽번호.
AI 로 만든 기존 덱의 결함(글자 넘침, 카드 아래 빈 공간, 줄 어긋남)을 피하려고 모든 글상자는
글자 길이로 글자 크기를 계산하고(fit_font), 넘치는 표는 다음 장으로 나눈다.

슬라이드 유형: 표지 / 카드 2~4열(+하단 강조) / 단계 흐름 / 표 / 참고 사례 카드 / 확인 체크리스트.
섹션 → 유형은 SECTION_LAYOUT 이 정한다(모르는 섹션은 카드).
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from ..company_context import COMPANY_NAME
from .body import Card, ProposalBody, Section

# ── 스타일 상수 ───────────────────────────────────────────────────────────────
SLIDE_W, SLIDE_H = 13.333, 7.5
MARGIN = 0.6
CONTENT_W = SLIDE_W - 2 * MARGIN
FONT = "맑은 고딕"

NAVY = RGBColor(0x11, 0x27, 0x47)
ORANGE = RGBColor(0xF1, 0x82, 0x20)
TEXT = RGBColor(0x28, 0x30, 0x3F)
GREY = RGBColor(0x5B, 0x67, 0x7C)
LIGHT_GREY = RGBColor(0x8A, 0x94, 0xA6)
BG = RGBColor(0xF7, 0xF8, 0xFA)
CARD = RGBColor(0xFF, 0xFF, 0xFF)
BORDER = RGBColor(0xE1, 0xE6, 0xEE)
ZEBRA = RGBColor(0xF2, 0xF5, 0xF9)
RED = RGBColor(0xC0, 0x39, 0x2B)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

TITLE_Y, TITLE_H = 0.45, 0.7
LEAD_Y, LEAD_H = 1.15, 0.45
RULE_Y = 1.7
BODY_Y = 1.95
BANNER_H = 0.62
FOOTER_Y = 7.02
BODY_BOTTOM = 6.85          # 바닥글 위
DRAFT_MARK = "초안(Draft)"
LOGO = Path(__file__).parent / "assets" / "und_logo.jpg"

# 섹션 id → 슬라이드 유형
SECTION_LAYOUT = {
    "solution": "steps",
    "next_steps": "steps",
    "requirements": "table",
    "references": "cases",
    "open_items": "checklist",
}


# ── 글자 크기 맞춤 ────────────────────────────────────────────────────────────

def _text_width_pt(text: str, size: float) -> float:
    """한글·전각은 글자 크기만큼, 영문·숫자·공백은 절반 남짓 폭으로 어림한다(맑은 고딕 실측 근사)."""
    w = 0.0
    for ch in text:
        if ord(ch) > 0x2E80:
            w += size * 0.98
        elif ch == " ":
            w += size * 0.3
        else:
            w += size * 0.56
    return w


def _lines_needed(paragraphs: list[str], width_in: float, size: float) -> int:
    # 좌우 안쪽 여백 + 단어 단위 줄바꿈(eaLnBrk=0)으로 줄 끝에 남는 빈칸 몫 8%
    usable = (width_in * 72 - 10) * 0.92
    return sum(max(1, math.ceil(_text_width_pt(p, size) / usable)) for p in paragraphs)


def text_height_in(paragraphs: list[str], width_in: float, size: float, *, spacing: float = 1.25,
                   para_gap_pt: float = 3) -> float:
    """주어진 글자 크기로 글상자에 필요한 높이(인치)."""
    h = _lines_needed(paragraphs, width_in, size) * size * spacing + para_gap_pt * max(0, len(paragraphs) - 1)
    return (h + 8) / 72


def fit_font(paragraphs: list[str], width_in: float, height_in: float,
             max_pt: float, min_pt: float, *, spacing: float = 1.25, para_gap_pt: float = 3) -> tuple[float, bool]:
    """글상자에 들어가는 가장 큰 글자 크기. 반환: (크기, 최소 크기로도 넘치는지)."""
    usable_h = height_in * 72 - 8
    size = max_pt
    while size >= min_pt:
        h = _lines_needed(paragraphs, width_in, size) * size * spacing + para_gap_pt * max(0, len(paragraphs) - 1)
        if h <= usable_h:
            return size, False
        size -= 0.5
    return min_pt, True


# ── 도형 헬퍼 ────────────────────────────────────────────────────────────────

@dataclass
class RenderReport:
    overflow: list[str] = field(default_factory=list)   # 최소 글자 크기로도 넘친 글상자(사람 확인 필요)


def _set_font(run, size: float, color: RGBColor, bold: bool = False, italic: bool = False) -> None:
    f = run.font
    f.name = FONT
    f.size = Pt(size)
    f.bold = bold
    f.italic = italic
    f.color.rgb = color
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = rpr.makeelement(qn(tag), {})
            rpr.append(el)
        el.set("typeface", FONT)
    # 한글 단어 중간 줄바꿈 방지("대/응", "테/스트"). 실측(PowerPoint 렌더): 언어를 ko-KR 로 지정하면
    # 단어 단위로 줄을 바꾼다. eaLnBrk="0" 만 주면 오히려 글자 단위로 끊긴다.
    rpr.set("lang", "ko-KR")
    rpr.set("altLang", "en-US")


def _rect(slide, x, y, w, h, fill: RGBColor | None, *, line: RGBColor | None = None,
          shape=MSO_SHAPE.RECTANGLE, radius: float | None = None):
    shp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(0.75)
    shp.shadow.inherit = False
    if radius is not None and shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        shp.adjustments[0] = radius
    return shp


def _text(slide, x, y, w, h, paragraphs: list[tuple[str, dict]], *, anchor=MSO_ANCHOR.TOP,
          align=PP_ALIGN.LEFT, margin: float = 0.05):
    """paragraphs: [(글자, {size, color, bold, italic, bullet})]."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for side in ("margin_left", "margin_right"):
        setattr(tf, side, Inches(margin))
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    for i, (txt, st) in enumerate(paragraphs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(st.get("gap", 3))
        if st.get("bullet"):
            b = p.add_run()
            b.text = "▪ "
            _set_font(b, st["size"], ORANGE, bold=True)
        r = p.add_run()
        r.text = txt
        _set_font(r, st["size"], st.get("color", TEXT), st.get("bold", False), st.get("italic", False))
    return tb


def _fitted_text(slide, report: RenderReport, where: str, x, y, w, h, lines: list[str], *,
                 max_pt: float, min_pt: float, color=TEXT, bold=False, bullet=False, italic=False,
                 anchor=MSO_ANCHOR.TOP, align=PP_ALIGN.LEFT, size: float | None = None) -> float:
    """size 를 주면 그 크기로(여러 카드의 글자 크기를 맞출 때), 아니면 상자에 맞는 최대 크기로."""
    shown = [("▪ " + t) if bullet else t for t in lines]
    if size is None:
        size, over = fit_font(shown, w - 0.1, h, max_pt, min_pt)
    else:
        over = text_height_in(shown, w - 0.1, size) > h + 0.02
    if over:
        report.overflow.append(where)
    _text(slide, x, y, w, h, [(t, {"size": size, "color": color, "bold": bold, "bullet": bullet,
                                   "italic": italic}) for t in lines], anchor=anchor, align=align)
    return size


# ── 공통 틀 ─────────────────────────────────────────────────────────────────

@dataclass
class _Deck:
    prs: Presentation
    report: RenderReport
    footer: str
    page: int = 0


def _new_slide(deck: _Deck):
    slide = deck.prs.slides.add_slide(deck.prs.slide_layouts[6])   # Blank
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = BG
    deck.page += 1
    _text(slide, MARGIN, FOOTER_Y, 9, 0.3, [(deck.footer, {"size": 9, "color": LIGHT_GREY})])
    _text(slide, SLIDE_W - MARGIN - 2.5, FOOTER_Y, 2.5, 0.3,
          [(f"{DRAFT_MARK}   {deck.page:02d}", {"size": 9, "color": LIGHT_GREY})], align=PP_ALIGN.RIGHT)
    return slide


def _header(deck: _Deck, slide, title: str, lead: str, tag: str = "") -> None:
    if tag:
        chip_w = max(1.0, _text_width_pt(tag, 11) / 72 + 0.4)
        chip = _rect(slide, MARGIN, 0.22, chip_w, 0.3, ORANGE, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.3)
        tf = chip.text_frame
        tf.margin_top = tf.margin_bottom = 0
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = tag
        _set_font(r, 11, WHITE, bold=True)
    _fitted_text(slide, deck.report, f"{title} 제목", MARGIN, TITLE_Y + 0.1, CONTENT_W, TITLE_H, [title],
                 max_pt=28, min_pt=20, color=NAVY, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    if lead:
        _fitted_text(slide, deck.report, f"{title} 요지", MARGIN, LEAD_Y + 0.12, CONTENT_W, LEAD_H, [lead],
                     max_pt=14, min_pt=11, color=GREY)
    _rect(slide, MARGIN, RULE_Y + 0.1, CONTENT_W, 0.035, ORANGE)


def _banner(deck: _Deck, slide, label: str, text: str) -> None:
    y = BODY_BOTTOM - BANNER_H
    _rect(slide, MARGIN, y, CONTENT_W, BANNER_H, NAVY, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.12)
    lab_w = max(0.9, _text_width_pt(label, 12) / 72 + 0.35)
    _text(slide, MARGIN + 0.2, y, lab_w, BANNER_H, [(label, {"size": 12, "color": ORANGE, "bold": True})],
          anchor=MSO_ANCHOR.MIDDLE)
    _fitted_text(slide, deck.report, f"{label} 강조", MARGIN + 0.2 + lab_w, y, CONTENT_W - lab_w - 0.4, BANNER_H,
                 [text], max_pt=13, min_pt=10, color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)


def _column_font(columns: list[list[str]], width_in: float, max_h: float, max_pt: float, min_pt: float,
                 bullet: bool = True) -> tuple[float, float]:
    """여러 칸(카드·단계)의 본문을 같은 글자 크기로: 모든 칸이 max_h 에 들어가는 가장 큰 크기와
    그때 가장 긴 칸의 높이. 칸마다 크기가 다르면 들쭉날쭉해 보인다."""
    shown = [[("▪ " + t) if bullet else t for t in col] or [" "] for col in columns]
    size = min(fit_font(col, width_in - 0.1, max_h, max_pt, min_pt)[0] for col in shown)
    need = max(text_height_in(col, width_in - 0.1, size) for col in shown)
    return size, need


def _body_bottom(section: Section) -> float:
    return BODY_BOTTOM - (BANNER_H + 0.18 if section.banner else 0)


def _tag(section_no: int) -> str:
    return f"{section_no:02d}"


# ── 슬라이드 유형 ────────────────────────────────────────────────────────────

def _cover(deck: _Deck, body: ProposalBody, now: datetime) -> None:
    slide = _new_slide(deck)
    _rect(slide, 0, 0, 4.2, SLIDE_H, NAVY)
    _rect(slide, 4.2, 0, 0.08, SLIDE_H, ORANGE)
    if LOGO.exists():
        _rect(slide, 0.55, 0.55, 2.1, 1.15, WHITE, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.12)
        slide.shapes.add_picture(str(LOGO), Inches(0.72), Inches(0.66), height=Inches(0.93))
    _text(slide, 0.55, 1.9, 3.3, 0.4, [(COMPANY_NAME, {"size": 14, "color": WHITE, "bold": True})])
    _text(slide, 0.55, 6.3, 3.3, 0.6, [("로봇 자동화 제안", {"size": 12, "color": RGBColor(0xC8, 0xD1, 0xDE)})])
    x = 4.9
    w = SLIDE_W - x - MARGIN
    chip = _rect(slide, x, 1.25, 1.35, 0.34, RED, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.3)
    p = chip.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = DRAFT_MARK
    _set_font(r, 11, WHITE, bold=True)
    _fitted_text(slide, deck.report, "표지 제목", x, 1.8, w, 2.1, [body.title or "제안서"],
                 max_pt=36, min_pt=24, color=NAVY, bold=True, anchor=MSO_ANCHOR.BOTTOM)
    if body.subtitle:
        _fitted_text(slide, deck.report, "표지 부제", x, 4.0, w, 0.8, [body.subtitle],
                     max_pt=17, min_pt=12, color=GREY)
    _rect(slide, x, 4.95, 2.2, 0.05, ORANGE)
    meta = []
    if body.customer:
        meta.append(f"고객사  {body.customer}")
    meta += [f"제안사  {COMPANY_NAME}", f"작성일  {now:%Y. %m. %d}"]
    _text(slide, x, 5.2, w, 1.2, [(m, {"size": 13, "color": TEXT}) for m in meta])


_CARD_HEAD = 0.95    # 카드 윗부분(번호 칩 + 제목)
_MIN_CARD_H = 2.3


def _card(deck: _Deck, slide, x, y, w, h, card: Card, no: int, accent: RGBColor, where: str,
          size: float | None = None) -> None:
    _rect(slide, x, y, w, h, CARD, line=BORDER, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.06)
    _rect(slide, x, y, w, 0.08, accent)
    badge = _rect(slide, x + 0.2, y + 0.28, 0.42, 0.42, accent)
    p = badge.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = str(no)
    _set_font(r, 14, WHITE, bold=True)
    _fitted_text(slide, deck.report, f"{where} 카드 제목", x + 0.72, y + 0.2, w - 0.9, 0.62, [card.heading or "-"],
                 max_pt=15, min_pt=11, color=NAVY, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    _fitted_text(slide, deck.report, f"{where} 카드 본문", x + 0.2, y + _CARD_HEAD, w - 0.35, h - _CARD_HEAD - 0.15,
                 card.bullets, max_pt=16, min_pt=9.5, bullet=True, size=size)


def _cards(deck: _Deck, section: Section, no: int) -> None:
    cards = section.cards or [Card("-", [])]
    per = 4 if len(cards) > 3 else len(cards)
    for start in range(0, len(cards), per):
        chunk = cards[start:start + per]
        slide = _new_slide(deck)
        title = section.title + (" (계속)" if start else "")
        _header(deck, slide, title, section.headline if not start else "", _tag(no))
        top, bottom = BODY_Y + 0.15, _body_bottom(section) - 0.1
        gap = 0.25
        w = (CONTENT_W - gap * (len(chunk) - 1)) / len(chunk)
        size, need = _column_font([c.bullets for c in chunk], w - 0.35, bottom - top - _CARD_HEAD - 0.15, 16, 9.5)
        h = min(bottom - top, max(_MIN_CARD_H, _CARD_HEAD + need + 0.25))
        y = top + (bottom - top - h) / 2
        for i, c in enumerate(chunk):
            accent = ORANGE if (start + i) % 2 == 0 else NAVY
            _card(deck, slide, MARGIN + i * (w + gap), y, w, h, c, start + i + 1, accent, section.title, size)
        if section.banner:
            _banner(deck, slide, section.banner_label or "요지", section.banner)


def _steps(deck: _Deck, section: Section, no: int) -> None:
    cards = section.cards or [Card("-", [])]
    slide = _new_slide(deck)
    _header(deck, slide, section.title, section.headline, _tag(no))
    top, bottom = BODY_Y + 0.15, _body_bottom(section) - 0.1
    arrow = 0.35
    n = len(cards)
    w = (CONTENT_W - arrow * (n - 1)) / n
    size, need = _column_font([c.bullets for c in cards], w - 0.3, bottom - top - 0.75, 16, 9.5)
    h = min(bottom - top, max(_MIN_CARD_H, 0.55 + need + 0.3))
    y = top + (bottom - top - h) / 2
    for i, c in enumerate(cards):
        x = MARGIN + i * (w + arrow)
        _rect(slide, x, y, w, 0.55, NAVY if i < n - 1 else ORANGE)
        _fitted_text(slide, deck.report, f"{section.title} 단계 제목", x + 0.1, y, w - 0.2, 0.55,
                     [c.heading or f"{i + 1}단계"], max_pt=14, min_pt=10, color=WHITE, bold=True,
                     anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER)
        _rect(slide, x, y + 0.55, w, h - 0.55, CARD, line=BORDER)
        _fitted_text(slide, deck.report, f"{section.title} 단계 본문", x + 0.15, y + 0.7, w - 0.3, h - 0.8,
                     c.bullets, max_pt=16, min_pt=9.5, bullet=True, size=size)
        if i < n - 1:
            _rect(slide, x + w + 0.07, y + 0.12, arrow - 0.14, 0.32, ORANGE, shape=MSO_SHAPE.CHEVRON)
    if section.banner:
        _banner(deck, slide, section.banner_label or "요지", section.banner)


_TABLE_WIDTHS = {4: (3.2, 3.2, 4.43, 1.3)}


def _row_height(cells: list[str], widths: list[float], size: float) -> float:
    lines = max(_lines_needed([c], w - 0.1, size) for c, w in zip(cells, widths))
    return lines * size * 1.2 / 72 + 0.14


def _table(deck: _Deck, section: Section, no: int) -> None:
    head, rows = section.table[0], section.table[1:]
    ncol = len(head)
    widths = list(_TABLE_WIDTHS.get(ncol, [CONTENT_W / ncol] * ncol))
    size = 10.5
    avail = BODY_BOTTOM - BODY_Y - 0.25
    pages: list[list[list[str]]] = [[]]
    used = _row_height(head, widths, size)
    for r in rows:
        rh = _row_height(r, widths, size)
        if pages[-1] and used + rh > avail:
            pages.append([])
            used = _row_height(head, widths, size)
        pages[-1].append(r)
        used += rh
    for pi, page_rows in enumerate(pages):
        slide = _new_slide(deck)
        title = section.title + (f" ({pi + 1}/{len(pages)})" if len(pages) > 1 else "")
        _header(deck, slide, title, section.headline, _tag(no))
        all_rows = [head] + page_rows
        shp = slide.shapes.add_table(len(all_rows), ncol, Inches(MARGIN), Inches(BODY_Y + 0.15),
                                     Inches(sum(widths)), Inches(0.4 * len(all_rows)))
        tbl = shp.table
        tbl.first_row = True
        for j, wv in enumerate(widths):
            tbl.columns[j].width = Inches(wv)
        for i, row in enumerate(all_rows):
            tbl.rows[i].height = Emu(Inches(_row_height(row, widths, size)))
            for j, val in enumerate(row):
                cell = tbl.cell(i, j)
                cell.fill.solid()
                cell.fill.fore_color.rgb = NAVY if i == 0 else (ZEBRA if i % 2 == 0 else CARD)
                cell.margin_left = cell.margin_right = Inches(0.06)
                cell.margin_top = cell.margin_bottom = Inches(0.04)
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                tf = cell.text_frame
                tf.word_wrap = True
                p = tf.paragraphs[0]
                r = p.add_run()
                r.text = val
                status = j == ncol - 1 and i > 0
                color = WHITE if i == 0 else (RED if status and not val.startswith("반영") else
                                              (NAVY if status else TEXT))
                _set_font(r, size, color, bold=i == 0 or status)


def _cases(deck: _Deck, section: Section, no: int) -> None:
    slide = _new_slide(deck)
    _header(deck, slide, section.title, section.headline, _tag(no))
    cards = section.cards
    top, bottom = BODY_Y + 0.15, _body_bottom(section) - 0.1
    gap = 0.25
    w = (CONTENT_W - gap * (len(cards) - 1)) / max(1, len(cards))
    bodies = [[b for b in c.bullets if not b.startswith("출처:")] for c in cards]
    size, need = _column_font(bodies, w - 0.4, bottom - top - 1.65, 14, 9)
    h = min(bottom - top, max(_MIN_CARD_H, 0.95 + need + 0.75))
    y = top + (bottom - top - h) / 2
    for i, c in enumerate(cards):
        x = MARGIN + i * (w + gap)
        _rect(slide, x, y, w, h, CARD, line=BORDER, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.05)
        _rect(slide, x, y, 0.08, h, ORANGE)
        _fitted_text(slide, deck.report, "사례 제목", x + 0.25, y + 0.15, w - 0.4, 0.75, [c.heading],
                     max_pt=14, min_pt=10.5, color=NAVY, bold=True)
        src = [b for b in c.bullets if b.startswith("출처:")]
        rest = [b for b in c.bullets if not b.startswith("출처:")]
        _fitted_text(slide, deck.report, "사례 본문", x + 0.25, y + 0.95, w - 0.4, h - 1.7, rest,
                     max_pt=14, min_pt=9, bullet=True, size=size)
        if src:
            _fitted_text(slide, deck.report, "사례 출처", x + 0.25, y + h - 0.7, w - 0.4, 0.6, src,
                         max_pt=9.5, min_pt=8, color=LIGHT_GREY, italic=True)
    if section.banner:
        _banner(deck, slide, section.banner_label or "유의", section.banner)


def _checklist(deck: _Deck, section: Section, no: int) -> None:
    items = [b for c in section.cards for b in c.bullets]
    slide = _new_slide(deck)
    _header(deck, slide, section.title, section.headline, _tag(no))
    cols = 2 if len(items) > 6 else 1
    per = math.ceil(len(items) / cols) if items else 0
    gap = 0.3
    w = (CONTENT_W - gap * (cols - 1)) / cols
    top, bottom = BODY_Y + 0.15, _body_bottom(section) - 0.1
    columns = [[f"☐  {t}" for t in items[ci * per:(ci + 1) * per]] for ci in range(cols)]
    size, need = _column_font(columns, w - 0.45, bottom - top - 0.4, 15, 10, bullet=False)
    h = min(bottom - top, max(1.6, need + 0.45))
    y = top + (bottom - top - h) / 2
    for ci, col in enumerate(columns):
        x = MARGIN + ci * (w + gap)
        _rect(slide, x, y, w, h, CARD, line=BORDER, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.04)
        _fitted_text(slide, deck.report, "확인 항목", x + 0.25, y + 0.2, w - 0.45, h - 0.35, col,
                     max_pt=15, min_pt=10, size=size)


_RENDERERS = {"cards": _cards, "steps": _steps, "table": _table, "cases": _cases, "checklist": _checklist}


def to_pptx(body: ProposalBody, *, now: datetime | None = None) -> tuple[bytes, RenderReport]:
    """제안서 본문 → pptx 바이트 + 렌더 보고(넘침 경고)."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(SLIDE_W), Inches(SLIDE_H)
    footer = f"고객사 {body.customer}  |  제안 공급사 {COMPANY_NAME}" if body.customer \
        else f"제안 공급사 {COMPANY_NAME}"
    deck = _Deck(prs, RenderReport(), footer)
    _cover(deck, body, now or datetime.now())
    for no, section in enumerate(body.sections, 1):
        kind = SECTION_LAYOUT.get(section.id, "cards")
        if kind == "table" and not section.table:
            kind = "cards"
        _RENDERERS[kind](deck, section, no)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue(), deck.report
