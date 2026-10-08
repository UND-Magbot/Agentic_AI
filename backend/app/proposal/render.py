"""제안서 본문 → 챗 마크다운. 파일 산출물(pptx)은 pptx_render.py."""
from __future__ import annotations

from .body import ProposalBody

DRAFT_MARK = "초안(Draft)"


def cover_title(body: ProposalBody) -> str:
    return body.title or "제안서 본문"


def to_markdown(body: ProposalBody) -> str:
    lines = [f"## {cover_title(body)} — {DRAFT_MARK}"]
    if body.subtitle:
        lines.append(f"_{body.subtitle}_")
    for n, s in enumerate(body.sections, 1):
        lines.append(f"\n### {n}. {s.title}")
        if s.headline:
            lines.append(f"> {s.headline}")
        if s.table:
            lines.append("")
            lines += [_md_row(s.table[0]), _md_row(["---"] * len(s.table[0]))]
            lines += [_md_row(r) for r in s.table[1:]]
        for c in s.cards:
            if c.heading:
                lines.append(f"\n**{c.heading}**")
            lines += [f"- {b}" for b in c.bullets]
        if s.banner:
            lines.append(f"\n**{s.banner_label or '요지'} |** {s.banner}")
    return "\n".join(lines)


def _md_row(cells: list[str]) -> str:
    return "| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |"
