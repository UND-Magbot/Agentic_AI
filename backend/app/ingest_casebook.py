"""KITECH 「로봇 엔지니어링 컨설팅 사례집」 PDF → pgvector 인제스트.

제안서 작성 시 유사 공정의 과거 컨설팅 사례(문제점·해결 구성·KPI)를 근거로 인용하기 위한
참고자료 RAG. 검색은 casebook.search_cases 가 담당한다.

실행(연도는 파일명에서, 없으면 --year):
    docker cp "docs/sources/참고. 2024년도 컨설팅 사례집.pdf" und_cortex_backend:/tmp/casebook_2024.pdf
    docker exec und_cortex_backend python -m app.ingest_casebook /tmp/casebook_2024.pdf
    docker exec und_cortex_backend python -m app.ingest_casebook /tmp/casebook_2024.pdf --dump  # 파싱 결과만

PDF 구조(2023·2024 사례집 공통): 한 PDF 페이지 = 좌우 2쪽 스프레드. 사례 1건 = PDF 2장.
  1장 좌: 산업·제목·기업 소개·기존 공정 정보   1장 우: 기존 공정의 문제점
  2장 좌: 컨설팅 결과 + KPI 타일              2장 우: 기업인-전문가 인터뷰
텍스트 레이어는 정상(ToUnicode 있음)이라 OCR 없이 추출하고, 다단 레이아웃은 XY-cut 으로
읽기 순서를 복원한다. KPI 타일은 수치·단위·라벨이 흩어진 span 이라 좌표로 짝지어 별도 파싱한다.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .database import SessionLocal
from .rag import embed_batch, upsert_document

DOMAIN = "sales"

_CASE_START_MARK = "기존 공정 정보"
_MIN_GAP = 8.0          # XY-cut 에서 단/문단 경계로 인정할 최소 공백(pt)
_SPAN_GAP = 15.0        # 한 줄 안에서 이보다 넓은 span 간격 = 다른 단
_LINE_GAP = 6.0         # 이보다 좁은 줄 간격 = 같은 문단
_PAGE_NO_Y = 690.0      # 이 아래의 숫자 줄 = 인쇄 쪽번호
_INFO_BOX_X0 = 80.0     # 개요 쪽 기업 정보 박스(기업명·항목명)의 왼쪽 정렬선 이내
_INFO_BOX_X1 = 262.0    # 정보 박스 우측 한계(오른쪽 소개글 단과 구분)
_INFO_ROW_GAP = 25.0    # 정보 박스 행 간격 상한 — 이보다 벌어지면 박스 끝(아래 공정 흐름도와 구분)
_INFO_KEYS = ("설립일자", "대표", "소재지", "전화", "홈페이지")
# 2023 판은 항목명이 "대 표", "소 재 지" 처럼 글자 사이가 벌어져 있다.
_SPACED_KEY_RE = {k: re.compile(r"(?<!\S)" + r"\s*".join(k) + r"(?!\S)") for k in _INFO_KEYS}
_STAT_RE = re.compile(r"\d+(?:\.\d+)?%")
_INTERVIEW_RE = re.compile(r"기업인\s*[-.·]\s*전문가\s*인터뷰")
_KPI_TEXT_MAX = 12      # KPI 수치·단위 조각의 최대 길이 — 그 위 본문 줄을 수치로 오인하지 않게
_TITLE_MIN_SIZE = 13.0  # 사례 제목 폰트 크기 하한
_KPI_LABEL_RE = re.compile(r"\((?:명|%|원|연간|KPI|생산\s*C/T)\)")
_PAGE_NO_RE = re.compile(r"^\d{1,3}(\s+\d{1,3})?$")

SECTIONS = ("개요", "기존 공정의 문제점", "컨설팅 결과", "기업인-전문가 인터뷰")


@dataclass(frozen=True)
class Casebook:
    """사례집 한 권. 연도별로 안내 쪽 위치만 다르고 사례 레이아웃은 같은 계열이다."""
    year: int
    source_path: str
    title: str
    program_pages: tuple[tuple[int, str], ...]   # (PDF 쪽, 쓸 반쪽) — 표지·목차·신청서 양식은 제외
    # 원본 인쇄 오류로 업종 태그가 다른 사례 것인 사례 번호. 지어내지 않고 업종을 비운다.
    wrong_industry_cases: tuple[int, ...] = ()


CASEBOOKS: dict[int, Casebook] = {
    2023: Casebook(2023, "casebook/kitech_robot_consulting_2023", "2023 로봇 엔지니어링 컨설팅 사례집(KITECH)",
                   ((4, "LR"), (7, "LR"), (8, "LR"), (9, "LR")),
                   # #20 ㈜한국소방기구제작소(분말 소화기)에 #10 의 '수산물가공품 제조_붉은대게' 태그가 인쇄됨
                   wrong_industry_cases=(20,)),
    2024: Casebook(2024, "casebook/kitech_robot_consulting_2024", "2024 로봇 엔지니어링 컨설팅 사례집(KITECH)",
                   ((3, "LR"), (6, "LR"), (7, "LR"), (8, "L"))),
}
SOURCE_PATHS = tuple(cb.source_path for cb in CASEBOOKS.values())


@dataclass
class _Block:
    x0: float
    y0: float
    x1: float
    y1: float
    lines: list[str]
    max_size: float

    @property
    def text(self) -> str:
        return " ".join(self.lines).strip()


@dataclass
class Case:
    case_no: int
    pdf_pages: tuple[int, int]          # 1-based PDF 페이지(스프레드)
    printed_pages: str                  # 사례집 인쇄 쪽수 "20-23"
    industry: str = ""
    title: str = ""
    subtitle: str = ""
    company: str = ""
    company_info: dict[str, str] = field(default_factory=dict)
    kpis: list[dict[str, str]] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)


def _half_blocks(page: pymupdf.Page) -> dict[str, list[_Block]]:
    """스프레드를 좌/우 쪽으로 나눈 줄 조각. PDF 가 같은 기준선의 두 단을 한 줄로 묶는 경우가
    있어(인터뷰 2단 질문) span 사이 공백이 넓으면 별개 조각으로 끊는다."""
    mid = page.rect.width / 2
    out: dict[str, list[_Block]] = {"L": [], "R": []}
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            if abs(ln["dir"][1]) > 0.1:
                continue  # 세로 쓰기(여백의 책 제목 머리글)
            groups: list[list[dict]] = []
            for s in ln["spans"]:
                if groups and s["bbox"][0] - groups[-1][-1]["bbox"][2] <= _SPAN_GAP:
                    groups[-1].append(s)
                else:
                    groups.append([s])
            for g in groups:
                t = "".join(s["text"] for s in g).strip()
                if not t:
                    continue
                x0, y0 = g[0]["bbox"][0], min(s["bbox"][1] for s in g)
                x1, y1 = g[-1]["bbox"][2], max(s["bbox"][3] for s in g)
                side = "L" if (x0 + x1) / 2 < mid else "R"
                out[side].append(_Block(x0, y0, x1, y1, [t], max(s["size"] for s in g)))
    return out


def _merge_paragraphs(units: list[_Block]) -> list[_Block]:
    """XY-cut 순서의 줄 조각 중 바로 아래 줄(같은 단, 같은 글자 크기)을 한 문단으로 잇는다."""
    out: list[_Block] = []
    for u in units:
        p = out[-1] if out else None
        if (
            p is not None
            and 0 <= u.y0 - p.y1 <= _LINE_GAP
            and min(p.x1, u.x1) > max(p.x0, u.x0)
            and abs(p.max_size - u.max_size) < 0.6
        ):
            out[-1] = _Block(min(p.x0, u.x0), p.y0, max(p.x1, u.x1), u.y1, p.lines + u.lines, p.max_size)
        else:
            out.append(u)
    return out


def _xycut(blocks: list[_Block]) -> list[_Block]:
    """재귀 XY-cut. 가장 넓은 공백 띠에서 자른다. 세로(단) 경계를 가로 경계보다 우선한다
    — 인터뷰처럼 2단 질문이 같은 높이에 나란히 놓인 레이아웃에서 두 단이 섞이지 않게."""
    if len(blocks) <= 1:
        return blocks
    for axis in ("x", "y"):
        spans = sorted((b.x0, b.x1) if axis == "x" else (b.y0, b.y1) for b in blocks)
        best: tuple[float, float] | None = None
        end = spans[0][1]
        for s, e in spans[1:]:
            if s - end >= _MIN_GAP and (best is None or s - end > best[1] - best[0]):
                best = (end, s)
            end = max(end, e)
        if best is not None:
            cut = best[1]
            key = (lambda b: b.x0) if axis == "x" else (lambda b: b.y0)
            first = [b for b in blocks if key(b) < cut]
            second = [b for b in blocks if key(b) >= cut]
            return _xycut(first) + _xycut(second)
    return sorted(blocks, key=lambda b: (b.y0, b.x0))


def _is_kpi_piece(t: str) -> bool:
    """KPI 타일 조각 = 수치("6.67%", "N/A") 또는 짧은 단위·방향("명", "증가").
    타일 바로 위 본문 줄의 짧은 문장("구성도를 제안하였다.")을 수치로 오인하지 않게."""
    return len(t) <= _KPI_TEXT_MAX and (bool(re.search(r"\d|N/A", t)) or len(t) <= 4)


def _parse_kpis(page: pymupdf.Page) -> tuple[list[dict[str, str]], float | None]:
    """컨설팅 결과 쪽(좌) 하단 KPI 타일 → [{label, value, unit, direction}].
    라벨 행의 y 와, 그 위 수치 띠의 상단 y(본문에서 제외할 경계)를 반환한다."""
    mid = page.rect.width / 2
    spans = [
        s
        for b in page.get_text("dict")["blocks"]
        for ln in b.get("lines", [])
        for s in ln["spans"]
        if s["text"].strip() and s["bbox"][0] < mid
    ]
    label_ys = [s["bbox"][1] for s in spans if _KPI_LABEL_RE.search(s["text"]) and s["size"] <= 9]
    if not label_ys:
        return [], None
    row_y = max(set(label_ys), key=label_ys.count)
    labels = [s for s in spans if abs(s["bbox"][1] - row_y) < 3 and not _PAGE_NO_RE.match(s["text"].strip())]
    values = [
        s for s in spans
        if row_y - 70 < s["bbox"][1] < row_y - 5 and _is_kpi_piece(s["text"].strip())
    ]
    if not values:
        return [], None

    def cx(s: dict) -> float:
        return (s["bbox"][0] + s["bbox"][2]) / 2

    tiles: dict[int, list[dict]] = {i: [] for i in range(len(labels))}
    for v in values:
        i = min(range(len(labels)), key=lambda k: abs(cx(labels[k]) - cx(v)))
        tiles[i].append(v)
    kpis: list[dict[str, str]] = []
    for i, lab in sorted(enumerate(labels), key=lambda t: t[1]["bbox"][0]):
        parts = sorted(tiles[i], key=lambda s: s["bbox"][0])
        if not parts:
            continue
        # 타일 안에서 가장 큰 글씨 = 수치(판마다 12pt/9pt 로 다름), 나머지 = 단위·방향.
        top = max(p["size"] for p in parts)
        big = " ".join(p["text"].strip() for p in parts if p["size"] >= top - 0.5)
        small = [p["text"].strip() for p in parts if p["size"] < top - 0.5]
        # 2023 판은 "6.67%"·"7.5명" 처럼 수치와 단위가 한 조각이다.
        m = re.match(r"^(N/A|[\d.,]+)\s*(.*)$", big)
        value, unit_in_value = (m.group(1), m.group(2)) if m else (big, "")
        # 작은 글씨 = 단위(%·명·년) + 방향(증가·감소·절감). 방향은 마지막 한글 2자 단어.
        direction = small[-1] if small and re.fullmatch(r"[가-힣]{2}", small[-1]) else ""
        unit = " ".join([unit_in_value] + (small[:-1] if direction else small)).strip()
        kpis.append({"label": lab["text"].strip(), "value": value, "unit": unit, "direction": direction})
    return kpis, row_y - 70


def format_kpis(kpis: list[dict[str, str]]) -> str:
    out = []
    for k in kpis:
        if not k["value"]:
            continue
        val = f"{k['value']}{k['unit']}".strip() if k["value"] != "N/A" else "N/A"
        out.append(f"- {k['label']}: {val} {k['direction']}".rstrip())
    return "\n".join(out)


def _is_page_no(b: _Block) -> bool:
    return b.y0 > _PAGE_NO_Y and bool(_PAGE_NO_RE.match(b.text))


def _order(blocks: list[_Block]) -> list[_Block]:
    return _merge_paragraphs(_xycut([b for b in blocks if not _is_page_no(b)]))


def _join(blocks: list[_Block]) -> str:
    return "\n".join(b.text for b in blocks if b.text)


def _parse_info_box(blocks: list[_Block], case: Case) -> list[_Block]:
    """개요 쪽 기업 정보 박스 → case.company / company_info. 박스에 속한 블록을 반환(본문에서 제외용).

    '설립일자' 조각을 기준점으로 삼는다(박스 위치는 소개글 길이에 따라 달라짐). 바로 위 = 기업명.
    같은 높이의 조각을 한 행으로 묶는다 — 2024 판은 "설립일자  1974년 1월     대표  제영섭" 이 한 조각,
    2023 판은 항목명 열("대 표")과 값 열("조우현")이 따로 있다. 항목명 없는 행은 직전 항목의 연장.
    """
    cand = sorted((b for b in blocks if b.x1 <= _INFO_BOX_X1 and not _is_page_no(b)), key=lambda b: (b.y0, b.x0))
    anchor = next((b for b in cand if b.x0 < _INFO_BOX_X0 and b.text.startswith("설립일자")), None)
    if anchor is None:
        return []
    rows: list[list[_Block]] = []
    for b in (b for b in cand if b.y0 >= anchor.y0 - 1):
        if rows and abs(b.y0 - rows[-1][0].y0) < 4:
            rows[-1].append(b)
        elif not rows or b.y0 - rows[-1][0].y0 <= _INFO_ROW_GAP:
            rows.append([b])
        else:
            break
    above = [b for b in cand if b.y0 < anchor.y0 - 1 and b.x0 < _INFO_BOX_X0 and anchor.y0 - b.y0 < 40]
    info = [b for row in rows for b in row]
    if above:
        case.company = above[-1].text
        info.insert(0, above[-1])
    key = ""
    for row in rows:
        text = "  ".join(b.text for b in sorted(row, key=lambda b: b.x0))
        for k, rx in _SPACED_KEY_RE.items():
            text = rx.sub(k, text)
        for tok in re.split(r"\s{2,}", text):
            if tok in _INFO_KEYS:
                key = tok
                case.company_info[key] = ""
            elif key:
                case.company_info[key] = f"{case.company_info[key]} {tok}".strip()
    return info


def _parse_case(doc: pymupdf.Document, idx: int, case_no: int) -> Case:
    p1, p2 = doc[idx], doc[idx + 1]
    h1, h2 = _half_blocks(p1), _half_blocks(p2)

    nums = sorted(int(b.text) for h in (h1, h2) for side in h.values() for b in side if _is_page_no(b))
    printed = f"{nums[0]}-{nums[-1]}" if nums else ""
    case = Case(case_no=case_no, pdf_pages=(idx + 1, idx + 2), printed_pages=printed)

    # ① 개요 쪽: 상단 산업 분류(작은 글씨) → 제목(큰 글씨) → 소개 → 기업 정보 박스 → 기존 공정 정보
    info = _parse_info_box(h1["L"], case)
    left1 = _order([b for b in h1["L"] if b not in info])
    titles = [b for b in left1 if b.y0 < 125 and b.max_size >= _TITLE_MIN_SIZE]
    tops = [b for b in left1 if b.y0 < 60 and b.max_size < _TITLE_MIN_SIZE]
    if tops:
        case.industry = tops[0].text
    # 부제는 공통 문구("로봇자동화 시스템 구축")라 그것을 뺀 나머지를 제목으로.
    generic = [b for b in titles if "시스템 구축" in b.text and len(titles) > 1]
    case.title = " ".join(b.text for b in titles if b not in generic)
    case.subtitle = " ".join(b.text for b in generic)
    case.sections["개요"] = _join([b for b in left1 if b not in titles and b not in tops[:1]])

    # ② 문제점, ③ 결과(+KPI), ④ 인터뷰. 결과가 인터뷰 쪽으로 이어지는 사례가 있어
    # 2장 우측은 '기업인-전문가 인터뷰' 머리글 앞/뒤로 나눈다.
    case.sections["기존 공정의 문제점"] = _join(_order(h1["R"]))
    kpis, kpi_top = _parse_kpis(p2)
    case.kpis = kpis
    result = _join(_order([b for b in h2["L"] if kpi_top is None or b.y0 < kpi_top]))
    interview_blocks = _order(h2["R"])
    extra, interview = [], []
    target = interview
    has_header = any(_INTERVIEW_RE.search(b.text) for b in interview_blocks)
    if has_header and any(b.text == "컨설팅 결과" for b in interview_blocks):
        target = extra
    for b in interview_blocks:
        if _INTERVIEW_RE.search(b.text):
            target = interview
        target.append(b.text)
    if extra:
        result = result + "\n" + "\n".join(extra)
    if kpis:
        result = result + "\n[도입 효과 KPI]\n" + format_kpis(kpis)
    case.sections["컨설팅 결과"] = result
    case.sections["기업인-전문가 인터뷰"] = "\n".join(interview)
    return case


def _pair_stat_rows(blocks: list[_Block]) -> list[_Block]:
    """인포그래픽의 '수치 행'(61.1% …)과 그 아래 같은 열의 '항목 행'(생산성 향상 …)을 짝지어
    "생산성 향상: 61.1%" 한 줄로 합친다. XY-cut 은 행 단위로 읽어 수치와 항목이 따로 흩어지기 때문."""
    stats = [b for b in blocks if _STAT_RE.fullmatch(b.text)]
    rows: dict[int, list[_Block]] = {}
    for b in stats:
        rows.setdefault(round(b.y0), []).append(b)
    used: set[int] = set()
    out = list(blocks)
    for y, row in rows.items():
        if len(row) < 3:
            continue
        below = [b for b in blocks if 0 < b.y0 - y < 120 and b not in stats]
        for s in row:
            cx = (s.x0 + s.x1) / 2
            lab = min(below, key=lambda b: abs((b.x0 + b.x1) / 2 - cx), default=None)
            if lab is None or abs((lab.x0 + lab.x1) / 2 - cx) > 20 or id(lab) in used:
                continue
            used.add(id(lab))
            out[out.index(s)] = _Block(s.x0, s.y0, s.x1, s.y1, [f"{lab.text}: {s.text}"], s.max_size)
            out.remove(lab)
    return out


def parse_program_pages(path: Path, cb: Casebook) -> list[dict]:
    """사례 앞의 지원사업·컨설팅 안내 쪽. 제안서의 '정부지원사업 활용' 근거용."""
    doc = pymupdf.open(str(path))
    out: list[dict] = []
    for pno, sides in cb.program_pages:
        h = _half_blocks(doc[pno - 1])
        blocks = [b for s in sides for b in _order(_pair_stat_rows(h[s]))]
        text = _join(blocks)
        if len(text) < 50:
            continue
        out.append({"pdf_page": pno, "title": blocks[0].text, "text": text})
    return out


def parse_pdf(path: Path) -> list[Case]:
    doc = pymupdf.open(str(path))
    starts = [
        i
        for i in range(doc.page_count - 1)
        if any(_CASE_START_MARK in b.text for b in _half_blocks(doc[i])["L"])
        and any(b.max_size >= _TITLE_MIN_SIZE and b.y0 < 125 for b in _half_blocks(doc[i])["L"])
    ]
    return [_parse_case(doc, i, n + 1) for n, i in enumerate(starts)]


def to_chunks(cb: Casebook, cases: list[Case], program: list[dict] | None = None) -> list[dict]:
    """사례 × 섹션 단위 chunk. 본문 앞에 사례 머리말을 붙여 섹션 단독으로 검색돼도 문맥이 남게 한다."""
    chunks: list[dict] = []
    for p in program or []:
        chunks.append(
            {
                "source_label": f"컨설팅 사례집 {cb.year} · 지원사업 안내 p{p['pdf_page']} {p['title']}",
                "content": f"[로봇 지원사업 안내 {cb.year}] {p['title']}\n{p['text']}",
                "metadata": {"doc": cb.title, "year": cb.year, "section": "지원사업 안내",
                             "pdf_pages": [p["pdf_page"]]},
            }
        )
    for c in cases:
        if c.case_no in cb.wrong_industry_cases:
            c.industry = ""
        head = f"[컨설팅 사례 {cb.year}-{c.case_no:02d}] {c.title}" + (f" — {c.company}" if c.company else "")
        if c.industry:
            head += f" ({c.industry})"
        for sec in SECTIONS:
            body = c.sections.get(sec, "").strip()
            if len(body) < 20:
                continue
            chunks.append(
                {
                    "source_label": f"컨설팅 사례집 {cb.year} · #{c.case_no:02d} {c.title} · {sec}",
                    "content": f"{head}\n[{sec}]\n{body}",
                    "metadata": {
                        "doc": cb.title,
                        "year": cb.year,
                        "case_no": c.case_no,
                        "title": c.title,
                        "company": c.company,
                        "industry": c.industry,
                        "section": sec,
                        "pdf_pages": list(c.pdf_pages),
                        "printed_pages": c.printed_pages,
                        "kpis": c.kpis if sec == "컨설팅 결과" else [],
                    },
                }
            )
    return chunks


async def ingest(path: Path, cb: Casebook, *, batch_size: int = 8) -> int:
    """같은 source_path 의 기존 row 를 지우고 재적재(파서 변경 시 잔존 chunk 정리)."""
    from sqlalchemy import text as _text

    chunks = to_chunks(cb, parse_pdf(path), parse_program_pages(path, cb))
    if not chunks:
        print("(no chunks parsed — nothing to ingest)")
        return 0
    print(f"parsed {len(chunks)} chunks from {path.name} ({cb.title})")
    n = 0
    async with SessionLocal() as db:
        await db.execute(_text("DELETE FROM documents WHERE source_path = :p"), {"p": cb.source_path})
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            vecs = await embed_batch([c["content"] for c in batch])
            for c, v in zip(batch, vecs):
                await upsert_document(
                    db,
                    source_path=cb.source_path,
                    source_label=c["source_label"],
                    content=c["content"],
                    metadata=c["metadata"],
                    domain=DOMAIN,
                    embedding=v,
                )
                n += 1
            print(f"  upserted {n}/{len(chunks)}")
        await db.commit()
    return n


def _dump(cases: list[Case], program: list[dict]) -> None:
    for p in program:
        print("=" * 100)
        print(f"[지원사업 안내] p{p['pdf_page']} {p['title']}")
        print(p["text"])
    for c in cases:
        print("=" * 100)
        print(f"#{c.case_no} p{c.pdf_pages} 쪽{c.printed_pages} | {c.industry} | {c.title} | {c.subtitle} | {c.company}")
        print("   info:", c.company_info)
        print("   kpi :", c.kpis)
        for sec in SECTIONS:
            print(f"--- [{sec}] ({len(c.sections.get(sec, ''))}자)")
            print(c.sections.get(sec, ""))


def casebook_for(path: Path, year: int | None = None) -> Casebook:
    """--year 가 없으면 파일명의 연도(2023, 2024 …)로 사례집 설정을 고른다."""
    if year is None:
        m = re.search(r"20\d{2}", path.name)
        year = int(m.group(0)) if m else None
    if year not in CASEBOOKS:
        raise SystemExit(f"사례집 연도를 알 수 없습니다: {path.name} (--year {'/'.join(map(str, CASEBOOKS))})")
    return CASEBOOKS[year]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--year", type=int, help="사례집 연도(생략 시 파일명에서)")
    ap.add_argument("--dump", action="store_true", help="DB 적재 없이 파싱 결과만 출력")
    args = ap.parse_args()
    if not args.pdf.exists():
        sys.exit(f"파일 없음: {args.pdf}")
    cb = casebook_for(args.pdf, args.year)
    if args.dump:
        sys.stdout.reconfigure(encoding="utf-8")
        _dump(parse_pdf(args.pdf), parse_program_pages(args.pdf, cb))
        return
    print(f"ingested {asyncio.run(ingest(args.pdf, cb))} chunks")


if __name__ == "__main__":
    main()
