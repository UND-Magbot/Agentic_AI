"""UND 사칙 엑셀 → pgvector 인제스트.

실행:
    docker exec und_cortex_backend python -m app.ingest_und_rules /docs/sources/UND사칙_2026년_최종.xlsx

사이트(시트)별로 "카테고리 헤더 + 항목 번호" 단위로 chunk 분할해 임베딩 후 documents 테이블 upsert.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Iterable

import openpyxl

from .database import SessionLocal
from .rag import embed_batch, upsert_document


# 카테고리 헤더 패턴 — 대괄호로 시작하는 라인.
_HEADER_RE = re.compile(r"\[UND\s*사칙\]_?\s*([^\]]+)")
_RULE_NUM_RE = re.compile(r"^\s*(\d+)\s*$")


def _norm(s: str | None) -> str:
    if s is None:
        return ""
    return str(s).replace("\r\n", "\n").strip()


def parse_xlsx(path: Path) -> list[dict]:
    """엑셀을 chunk 리스트로 변환.

    각 chunk:
      {
        site, category, rule_no, content, source_label
      }
    site = 시트명에서 추출 (대구본사 / 구미공장 등).
    category = 가장 최근에 등장한 [UND 사칙]_xxx 헤더.
    rule_no = 해당 행 첫 컬럼의 숫자(있을 때만). 없으면 이전 항목의 연장으로 합쳐줌.
    """
    wb = openpyxl.load_workbook(str(path), data_only=True)
    chunks: list[dict] = []

    for sheet in wb.sheetnames:
        ws = wb[sheet]
        # 시트명에서 site 추출.
        site = sheet.replace("UND 사칙_", "").strip()

        category: str | None = None
        current: dict | None = None  # 현재 누적 중인 rule chunk
        # 시트 첫 헤더(예: "[UND 사칙]_ 대구본사", "[UND 사칙]_ 구미공장") 는 시트 제목용 라벨이라
        # 카테고리로 채택하지 않는다 — 안 그러면 source_label 이 "대구본사 · 대구본사" 처럼 site 가 두 번 노출됨.
        seen_first_header = False

        for row in ws.iter_rows(values_only=True):
            cells = [_norm(c) for c in row]
            joined = " | ".join([c for c in cells if c]).strip()
            if not joined:
                continue

            # 카테고리 헤더 검출.
            m = _HEADER_RE.search(joined)
            if m:
                # 진행 중이던 항목 flush.
                if current and current["content"].strip():
                    chunks.append(current)
                    current = None
                if not seen_first_header:
                    # 시트 제목 헤더 — site 는 시트명에서 이미 알고 있으니 카테고리로 채택하지 않음.
                    seen_first_header = True
                    continue
                category = m.group(1).strip().rstrip("/")
                continue

            # rule_no + 본문 행. 첫 빈셀 제외하고 처음으로 등장하는 셀이 숫자인 케이스가 일반.
            non_empty = [c for c in cells if c]
            if not non_empty:
                continue

            head = non_empty[0]
            rule_match = _RULE_NUM_RE.match(head)
            if rule_match:
                # 새 rule 시작.
                if current and current["content"].strip():
                    chunks.append(current)
                rno = int(rule_match.group(1))
                body = " ".join(non_empty[1:]).strip()
                current = {
                    "site": site,
                    "category": category or "(미분류)",
                    "rule_no": rno,
                    "content": body,
                }
            else:
                # 본문 연장(들여쓰기된 부속 설명) — 현재 항목에 append.
                if current is None:
                    # 카테고리 헤더 직후의 ★/Du Date 같은 상단 안내문 — 별도 chunk 로.
                    chunks.append(
                        {
                            "site": site,
                            "category": category or "(미분류)",
                            "rule_no": None,
                            "content": joined,
                        }
                    )
                else:
                    current["content"] = (current["content"] + "\n" + joined).strip()

        # 마지막 항목 flush.
        if current and current["content"].strip():
            chunks.append(current)

    # source_label 부여 + content 너무 짧은(노이즈) 항목 컷.
    # rule_no 가 없는 chunk(헤더 직후 안내문 등) 가 같은 (site, category) 내 여러 개 있으면
    # 라벨이 동일해져 documents 의 UNIQUE (source_path, source_label) 키에서 ON CONFLICT 로
    # 마지막 row 만 살아남는 버그가 있었다 (대구 r5 "법인카드 목적 기재 필수" 가 r7 익스펜스
    # 보고로 덮어쓰여 사라진 케이스). 카운터로 unique 보장.
    out: list[dict] = []
    note_counter: dict[tuple[str, str], int] = {}
    for c in chunks:
        if len(c["content"]) < 5:
            continue
        if c["rule_no"] is not None:
            label = f"UND 사칙 · {c['site']} · {c['category']} #{c['rule_no']}"
        else:
            key = (c["site"], c["category"])
            note_counter[key] = note_counter.get(key, 0) + 1
            label = f"UND 사칙 · {c['site']} · {c['category']} · 안내 #{note_counter[key]}"
        c["source_label"] = label
        out.append(c)
    return out


async def ingest(
    path: Path,
    *,
    source_path_label: str | None = None,
    batch_size: int = 16,
    replace: bool = True,
) -> int:
    """파싱·임베딩·업서트를 한 번에 수행. 반환=upsert 된 row 수.

    replace=True (기본): 같은 source_path 의 기존 row 를 모두 삭제 후 재삽입.
                        파서 변경/항목 삭제로 인한 잔존 chunk 정리.
    replace=False: 단순 upsert (UNIQUE 키 충돌 시 갱신, 새 항목은 추가, 사라진 항목은 잔존).
    """
    if not path.exists():
        raise FileNotFoundError(path)

    chunks = parse_xlsx(path)
    if not chunks:
        print("(no chunks parsed — nothing to ingest)")
        return 0

    print(f"parsed {len(chunks)} chunks from {path.name}")

    spath = source_path_label or str(path)
    inserted = 0

    async with SessionLocal() as db:
        try:
            if replace:
                # 같은 파일에서 온 모든 row 정리 — 파서 결과만 정답이 되도록.
                from sqlalchemy import text as _text
                res = await db.execute(
                    _text("DELETE FROM documents WHERE source_path = :p"),
                    {"p": spath},
                )
                # rowcount 가 -1 (드라이버에 따라) 일 수도 있어 안전하게.
                deleted = getattr(res, "rowcount", None)
                print(f"  cleaned {deleted if deleted is not None else '?'} stale rows for {spath}")
                await db.commit()

            # 배치 임베딩 → 하나씩 upsert.
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i : i + batch_size]
                texts = [c["content"] for c in batch]
                vecs = await embed_batch(texts)
                for c, v in zip(batch, vecs):
                    metadata = {
                        "site": c["site"],
                        "category": c["category"],
                        "rule_no": c["rule_no"],
                    }
                    await upsert_document(
                        db,
                        source_path=spath,
                        source_label=c["source_label"],
                        content=c["content"],
                        metadata=metadata,
                        domain="all",
                        embedding=v,
                    )
                    inserted += 1
                await db.commit()
                print(f"  upserted {inserted}/{len(chunks)}")
        except Exception:
            await db.rollback()
            raise
    return inserted


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        print("usage: python -m app.ingest_und_rules <path-to-xlsx>")
        sys.exit(2)
    path = Path(argv[1]).resolve()
    n = asyncio.run(ingest(path))
    print(f"done — {n} chunks ingested.")


if __name__ == "__main__":
    main(sys.argv)
