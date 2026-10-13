"""발행번호(사용자 2026-10-12, 영업팀 요청 5번).

견적서 QT-LU20261012-01 · 거래명세서 TS-LU20261012-01 = 문서 종류 + 발행자 이니셜 + 발행일 + 일련번호.
- 일련번호는 발행자의 그날 발행 순서(문서 종류별로 따로, 고객사·건과 무관). 다른 발행자 번호와는 상관없다.
- 발행·재발행마다 새 번호. 다른 사람이 고쳐 다시 내면 그 사람의 그날 순서(첫 발행이면 01).
- 한 번 나간 번호는 다시 쓰지 않는다(되돌리기 없음, 건을 지워도 doc_numbers 에 남음).
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

QUOTE = "QT"
STATEMENT = "TS"
INITIALS_RE = re.compile(r"[A-Z]{2,4}")
DOC_NO_RE = re.compile(r"^(QT|TS)-([A-Z]{2,4})(\d{8})-(\d{2,})$")


def clean_initials(v: str | None) -> str | None:
    """'lu ' → 'LU'. 영문 2~4자가 아니면 None."""
    ini = str(v or "").strip().upper()
    return ini if INITIALS_RE.fullmatch(ini) else None


def format_no(doc_type: str, issuer: str, day: dt.date, seq: int) -> str:
    return f"{doc_type}-{issuer}{day:%Y%m%d}-{seq:02d}"


async def allocate(db: AsyncSession, doc_type: str, issuer: str, day: dt.date, *, deal_id: int | None = None,
                   quote_id: int | None = None, user_id: int | None = None) -> str:
    """다음 발행번호를 잡아 기록한다(커밋은 부르는 쪽 — 발행 저장과 한 트랜잭션, 실패해 되돌리면 번호도 안 나간 것).
    같은 발행자·날짜·종류는 잠가 두 사람이 동시에 내도 번호가 겹치지 않는다."""
    ini = clean_initials(issuer)
    if not ini or doc_type not in (QUOTE, STATEMENT):
        raise ValueError("발행자 이니셜은 영문 2~4자로 적어 주세요(예: LU).")
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"doc_no:{doc_type}:{ini}:{day:%Y%m%d}"})
    seq = (await db.execute(text("SELECT COALESCE(MAX(seq), 0) + 1 FROM doc_numbers "
                                 "WHERE doc_type = :t AND issuer = :i AND issue_day = :d"),
                            {"t": doc_type, "i": ini, "d": day})).scalar_one()
    no = format_no(doc_type, ini, day, seq)
    await db.execute(text("INSERT INTO doc_numbers (doc_type, issuer, issue_day, seq, doc_no, deal_id, quote_id, user_id) "
                          "VALUES (:t, :i, :d, :s, :n, :deal, :q, :u)"),
                     {"t": doc_type, "i": ini, "d": day, "s": seq, "n": no, "deal": deal_id, "q": quote_id, "u": user_id})
    return no


async def link_deal(db: AsyncSession, doc_no: str, deal_id: int) -> None:
    """번호를 먼저 잡고 건을 나중에 만든 경우(첫 견적서 발행) 건 번호를 이어 둔다."""
    await db.execute(text("UPDATE doc_numbers SET deal_id = :d WHERE doc_no = :n"), {"d": deal_id, "n": doc_no})


DOC_NO_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,39}$")


def clean_doc_no(v: str | None) -> str | None:
    """담당자가 견적서에 적은 발행번호(밖에서 고친 견적서 첨부) — 영문·숫자·-_.() 40자까지. 아니면 None."""
    t = re.sub(r"\s+", " ", str(v or "")).strip()
    return t if DOC_NO_TEXT_RE.fullmatch(t) else None


async def issued_where(db: AsyncSession, doc_no: str) -> dict | None:
    """이 번호로 이미 발행한 적이 있는가(사용자 2026-10-13: 같은 건이든 다른 건이든 같은 번호로는 다시 발행 안 됨).
    번호 기록(doc_numbers)·견적서 발행 이력(product_quotes)·영업 건 변경 이력(sales_deal_events)을 대소문자 무시하고 본다 —
    예전 형식 번호(doc_numbers 에 없는 번호)도 잡힌다. 있으면 {deal_no, customer}(건이 없으면 None 값), 없으면 None.
    같은 번호를 동시에 올려도 하나만 나가도록 거래(transaction) 끝까지 그 번호를 잠근다."""
    n = doc_no.strip().upper()
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"doc_no_text:{n}"})
    row = (await db.execute(text(
        """
        WITH hits AS (
            SELECT deal_id FROM doc_numbers WHERE upper(doc_no) = :n
            UNION ALL
            SELECT deal_id FROM product_quotes
             WHERE upper(quote_no) = :n
                OR (jsonb_typeof(history) = 'array'
                    AND EXISTS (SELECT 1 FROM jsonb_array_elements(history) h WHERE upper(h->>'quote_no') = :n))
            UNION ALL
            SELECT deal_id FROM sales_deal_events WHERE upper(detail->>'no') = :n
        )
        SELECT h.deal_id, d.deal_no, d.customer FROM hits h LEFT JOIN sales_deals d ON d.id = h.deal_id
         ORDER BY (d.id IS NULL) LIMIT 1
        """), {"n": n})).mappings().first()
    return {"deal_no": row["deal_no"], "customer": row["customer"]} if row else None


async def register(db: AsyncSession, doc_no: str, *, deal_id: int | None = None, quote_id: int | None = None,
                   user_id: int | None = None) -> None:
    """담당자가 견적서에 직접 적은 번호가 우리 형식(QT-LU20261012-03)이면 기록해 둔다 — 그 발행자의 그날 다음 번호가
    이 번호와 겹치지 않게. 이미 있으면(같은 판을 고친 것) 그대로 둔다. 다른 형식(예전 번호)은 기록하지 않는다."""
    m = DOC_NO_RE.match(doc_no)
    if not m:
        return
    t, ini, ymd, seq = m.group(1), m.group(2), m.group(3), int(m.group(4))
    try:
        day = dt.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
    except ValueError:
        return
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"doc_no:{t}:{ini}:{day:%Y%m%d}"})
    await db.execute(text("INSERT INTO doc_numbers (doc_type, issuer, issue_day, seq, doc_no, deal_id, quote_id, user_id) "
                          "VALUES (:t, :i, :d, :s, :n, :deal, :q, :u) ON CONFLICT DO NOTHING"),
                     {"t": t, "i": ini, "d": day, "s": seq, "n": doc_no, "deal": deal_id, "q": quote_id, "u": user_id})
