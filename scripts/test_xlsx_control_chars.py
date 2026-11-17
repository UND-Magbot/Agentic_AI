# -*- coding: utf-8 -*-
"""엑셀 문서(견적서·발주서·거래명세서)에 보이지 않는 제어문자가 섞여도 파일이 만들어지는지(QA 2026-11-16).

PDF·웹에서 사양 문구를 복사해 붙이거나 AI 판독 결과에 \\x02 같은 제어문자가 섞이면 openpyxl 이 셀에 쓰기를 거부해
미리보기·다운로드가 500 이 났다(발행은 돼서 그 판을 영영 못 받음). 셀에 쓰기 직전에 빼고, 줄바꿈·탭은 남긴다.
DB 없이 빌더만 돌린다: python scripts/test_xlsx_control_chars.py (컨테이너 안 PYTHONPATH=/app)
"""
from __future__ import annotations

import copy
import io
from datetime import date

from openpyxl import load_workbook

from app.company_knowledge import quote_session as qs
from app.company_knowledge import quote_xlsx as qx
from app.purchases import po_xlsx as px
from app.sales_deals import statement_xlsx as sx

OK: list[bool] = []
BAD = "\x02\x07\x1b"


def check(cond: bool, label: str, extra: str = "") -> None:
    OK.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + label + ("" if cond else f" :: {extra}"))


def texts(data: bytes) -> str:
    ws = load_workbook(io.BytesIO(data)).active
    return "\n".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)


def build(label: str, fn) -> str:
    try:
        out = texts(fn())
        check(not any(ch in out for ch in BAD), f"{label} — 파일이 만들어지고 제어문자는 빠짐")
        return out
    except Exception as e:  # noqa: BLE001
        check(False, f"{label} — 파일이 만들어지고 제어문자는 빠짐", f"{type(e).__name__}: {e}")
        return ""


# 1) 발주서 — 품목 설명·Comments·우리 담당(서식 칸)
hdr = {"lang": "ko", "date": "2026-11-16", "no": "UND-ZZ20261116-01",
       "supplier": {"name": "공급사\x02", "contact": "", "email": "", "tel": ""},
       "issuer": {"name": "홍길동\x07", "title": "", "address": "대구", "tel": "", "fax": "", "attn": ""},
       "bank": "", "address": "", "swift": "", "account_no": "", "account_holder": "", "comments": "납품처\x1b 구미\n2줄"}
out = build("발주서", lambda: px.build_xlsx(hdr, [{"commodity": "A1", "description": "설명\x02끝\n둘째 줄", "qty": 2, "price": 1000}]))
check("설명끝\n둘째 줄" in out and "납품처 구미\n2줄" in out, "발주서 — 줄바꿈은 그대로 남음", out[:200])

# 2) 견적서
S = qs.draft_state({"applicable": True, "items": []}, model="", payload_kg=None, form={
    "customer": "고객\x02", "to": "김철수", "contact_name": "홍길동", "contact_title": "이사", "initials": "TS",
    "contact_mobile": "010-0000-0000", "contact_email": "name@example.com"})
S.update(manual=True, asked=["extra"], delivery={"case": 1, "freight": None}, next_id=2)
S["lines"] = [{"id": "L1", "kind": "extra", "key": "extra", "name": "TCV1", "commodity": "Magbot ATC", "qty": 1,
               "unit_price": 2_300_000, "remark": "", "price_status": "set"}]
new, _ = qs.apply_ops(copy.deepcopy(S), qs.edit_ops(S, {"lines": [{"id": "L1", "name": "TCV1\x02 master", "commodity": "Magbot ATC",
                                                                  "qty": 1, "unit_price": 2300000, "remark": "", "block": qs.MAIN_BLOCK}]}), None)
h = qx.quote_header(new["form"], lang="ko", quote_no="QT-TS20261116-01", day=date(2026, 11, 16), pogo=False)
out = build("견적서", lambda: qx.build_xlsx(h, qs.render_lines(new, "ko", None)))
check("TCV1 master" in out, "견적서 — 제어문자만 빠지고 품명은 그대로", out[:200])

# 3) 거래명세서
st = {"no": "TS-ZZ20261116-01", "date": "2026-11-16", "buyer": {"reg_no": "314-86-54321", "name": "상호\x02", "ceo": "", "address": "대구"},
      "items": [{"name": "MG10\x07", "spec": "규격\x1b", "qty": 1, "unit_price": 1_000_000, "note": ""}]}
out = build("거래명세서", lambda: sx.build_xlsx(st))
check("MG10" in out, "거래명세서 — 품명 그대로", out[:200])

print(f"\nPASS {sum(OK)} / FAIL {len(OK) - sum(OK)}")
