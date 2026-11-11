# -*- coding: utf-8 -*-
"""국내·해외 발주서 렌더 비교(사용자 2026-11-11 — 해외 발주서 품질 점검). 같은 품목으로 두 양식을 만들고, 해외 원본 양식(LHTC, 사용자 2026-11-11)과 함께
PDF → PNG 로 /out 에 둔다. 컨테이너 안(LibreOffice)에서:

    docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts \\
        -v "./docs/examples/quotes:/samples:ro" -v "./_capture/po_compare:/out" backend python /scripts/qa_sales/render_po_compare.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.company_knowledge import quote_xlsx as qx
from app.purchases import po_xlsx as px

OUT = Path("/out")
ITEMS = [{"commodity": "AX8114", "description": "AMR S300\n- Lidar, 300kg payload", "moq": None, "qty": 2, "price": 4600, "remark": "Sea"},
         {"commodity": "SHIP", "description": "Sea Shipping cost", "moq": None, "qty": 1, "price": 2289, "remark": ""}]
ISSUER_KO = {"name": "이승학", "title": "주임", "address": "대구광역시 수성구 알파시티1로 31길 31, 5층", "tel": "82-10-5896-0996",
             "fax": "82-54-462-5468", "attn": "Lucas (lucas@unde.co.kr)"}
ISSUER_EN = {"name": "Alex Jeon", "title": "Mr.", "address": "5F, 31, Alphacity 1-ro 31-gil, Suseong-gu, Daegu, Republic of Korea",
             "tel": "82-10-9515-5466", "fax": "", "attn": "Mr. Alex Jeon (alex@unde.co.kr)"}


def head(lang: str) -> dict:
    return {"lang": lang, "no": "UND-LU20261111-01", "date": "2026-11-11",
            "supplier": {"name": "Guangzhou Autoxing Technology", "contact": "Echo", "email": "echo@autoxing.com", "tel": "(+86)136 0000 0000"},
            "bank": "Bank of China", "address": "Guangzhou, China", "swift": "BKCHCNBJ", "account_no": "1234-5678-9012", "account_holder": "Autoxing",
            "issuer": ISSUER_EN if lang == "en" else ISSUER_KO, "comments": "Delivery by sea"}


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = {"ko": px.build_xlsx(head("ko"), ITEMS), "en": px.build_xlsx(head("en"), ITEMS),
            "en_template": Path("/samples/유엔디로보틱스_LHTC_Purchasing_Lu20261008.xlsx").read_bytes()}
    for name, x in jobs.items():
        (OUT / f"{name}.xlsx").write_bytes(x)
        pdf = await qx.to_pdf(x)
        (OUT / f"{name}.pdf").write_bytes(pdf)
        import pymupdf
        for i, page in enumerate(pymupdf.open(OUT / f"{name}.pdf")):
            page.get_pixmap(dpi=80).save(OUT / f"{name}-{i + 1}.png")
    print("ok", sorted(p.name for p in OUT.iterdir()))


asyncio.run(main())
