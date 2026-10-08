"""영업 문서(거래명세서·견적서) 실물 점검용 — 앱이 만드는 엑셀과 그 PDF 를 /out 에 쓴다(DB 는 읽기만).

    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./scripts/qa_sales/out:/out \\
        backend python /scripts/doc_render.py   (scripts 를 /scripts 로 마운트)

원본 양식(docs/ 의 PDF·엑셀)과 나란히 놓고 눈으로·칸 단위로 비교하는 데 쓴다.
"""
import asyncio
import json
import sys

from sqlalchemy import text

from app.company_knowledge import quote_xlsx as qx
from app.database import SessionLocal
from app.sales_deals import statement_xlsx as sx

OUT = sys.argv[1] if len(sys.argv) > 1 else "/out"


def load(v):
    return v if isinstance(v, (dict, list)) or v is None else json.loads(v)


async def main():
    async with SessionLocal() as db:
        # 거래명세서 — 발급된 것(지운 건은 보관함에서)
        rows = (await db.execute(text("SELECT deal_no, statement FROM sales_deals WHERE statement IS NOT NULL "
                                      "UNION ALL SELECT deal_no, deal->'statement' FROM sales_deal_trash "
                                      "WHERE deal->'statement' IS NOT NULL AND deal->>'statement' <> 'null'"))).all()
        quotes = (await db.execute(text("SELECT quote_no, revision, header, lines FROM product_quotes "
                                        "WHERE status = 'issued' AND quote_no IS NOT NULL ORDER BY updated_at DESC LIMIT 3"))).all()
    for no, st in rows:
        st = load(st)
        data = sx.build_xlsx(st)
        name = f"statement_{no}"
        open(f"{OUT}/{name}.xlsx", "wb").write(data)
        open(f"{OUT}/{name}.pdf", "wb").write(await qx.to_pdf(data))
        print("거래명세서", no, sx.file_name(st))
    for no, rev, header, lines in quotes:
        header, lines = load(header), load(lines)
        if not header or not lines:
            continue
        data = qx.build_xlsx(header, lines)
        name = f"quote_{no}_r{rev}"
        open(f"{OUT}/{name}.xlsx", "wb").write(data)
        open(f"{OUT}/{name}.pdf", "wb").write(await qx.to_pdf(data))
        print("견적서", no, rev, header.get("quote_no"))


asyncio.run(main())
