"""화면 문구에서 내부 규칙 번호 빼기(company_knowledge/plain_text.py) 점검 — 사용자 2026-10-06 "P04·P07·P08·P09 같은 개발자용 표기 제거".

    PYTHONPATH=backend python scripts/test_plain_text.py [--db]     # --db: 제품 DB 툴체인저로 판정
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.company_knowledge import atc_selection as atc
from app.company_knowledge import plain_text as pt
from app.company_knowledge.product_recommend import atc_context

sys.stdout.reconfigure(encoding="utf-8")
PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
CODE = re.compile(pt._CODE)


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


# 1) 실제 화면 문구 → 번호만 빠지고 뜻은 남음
CASES = {
    "무선 TCW1 은 무선을 따로 고른 경우에만 검토(C09 유선 기본)": "무선 TCW1 은 무선을 따로 고른 경우에만 검토(유선 기본)",
    "속도 단위·기준(P01) 확정 전 — 상위 후보 비교 여부는 내부 검토": "속도 단위·기준 확정 전 — 상위 후보 비교 여부는 내부 검토",
    "상위 TCV1와 조건 비교 후 담당자 확정(C11, 자동 상향 아님)": "상위 TCV1와 조건 비교 후 담당자 확정(자동 상향 아님)",
    "이 계열 최대 30kg 로는 수용 못함(R09 — 내부 검토)": "이 계열 최대 30kg 로는 수용 못함(내부 검토)",
    "모델표(P04)·속도 기준(P01)·공압 2포트(P06)가 미확정": "모델표·속도 기준·공압 2포트가 미확정",
    "잠정 기준 적용(P01) — 영업부 잠정값으로 계산": "잠정 기준 적용 — 영업부 잠정값으로 계산",
    "포고핀 산정(E01·E02)": "포고핀 산정",
}
for src, want in CASES.items():
    got = pt.hide_codes(src)
    check(got == want, f"문구: {src[:30]}", got)
MODELS = "UR10e 와 M1013, MG16, S600, TCV1, RB10, HS220, M-LTC-0005A, TCHK150"
check(pt.hide_codes(MODELS) == MODELS, "제품·로봇 모델명은 그대로")
check(pt.hide_codes("P04") == "P04" and pt.hide_codes("P04·P07·P08·P09") == "P04·P07·P08·P09", "번호만 있는 값(id)은 그대로")
check(pt.hide_codes("다음은 R02 ", strip=False) == "다음은 ", "흘려 보내는 조각은 앞뒤 공백 유지")

# 2) scrub — 값 칸(id·rule·ref)과 입력(intake)은 그대로, 문장만
obj = {"id": "P04", "rule": "R02", "text": "모델표(P04) 확정 전", "intake": {"note": "R10 로봇(C11)"},
       "list": [{"ref": "C11", "text_ko": "정격 근접(C11)"}]}
s = pt.scrub(obj)
check(s["id"] == "P04" and s["rule"] == "R02" and s["list"][0]["ref"] == "C11", "값 칸은 그대로", str(s))
check(s["text"] == "모델표 확정 전" and s["list"][0]["text_ko"] == "정격 근접", "문장 칸은 번호 제거", str(s))
check(s["intake"] == obj["intake"], "입력(intake)은 그대로 — 다음 요청에 다시 쓰임")

# 3) 테스트 샘플 전부 — 판정 결과를 scrub 하면 문장 칸에 번호가 하나도 없음
SKIP = pt.SKIP_KEYS
if "--db" in sys.argv:
    from app.company_knowledge import product_recommend as pr
    from app.database import SessionLocal

    async def _cat():
        async with SessionLocal() as db:
            return await pr.atc_catalog(db)
    CAT = asyncio.run(_cat())
else:
    CAT = [{"card_id": i, "name": n, "series": se, "series_label": se, "payload_kg": p, "official": True, "wireless": n == "TCW1"}
           for i, (n, se, p) in enumerate([("TCC1", "auto", 5), ("TCV1", "auto", 10), ("TCW1", "auto", 10), ("TCV2", "auto", 16),
                                           ("TCV3", "auto", 25), ("TCV4", "auto", 30), ("TCHK100", "industrial", 100),
                                           ("TCHK150", "industrial", 150), ("TCHK220", "industrial", 220),
                                           ("M-LTC-0005A", "mltc", 5), ("M-LTC-0040A", "mltc", 40), ("M-LTC-0300G", "mltc", 300)])]


def leftovers(o, key=None, path="") -> list[str]:
    if key in SKIP:
        return []
    if isinstance(o, str):
        return [f"{path}: {o[:80]}"] if CODE.search(o) and not pt._FULL.fullmatch(o) else []
    if isinstance(o, dict):
        return [x for k, v in o.items() for x in leftovers(v, k, f"{path}.{k}")]
    if isinstance(o, list):
        return [x for i, v in enumerate(o) for x in leftovers(v, key, f"{path}[{i}]")]
    return []


samples = json.loads((Path(atc.__file__).with_name("data") / "atc_test_samples.json").read_text(encoding="utf-8"))["samples"]
raw_hits = clean_hits = 0
for smp in samples:
    out = atc.evaluate(smp["intake"], CAT)
    raw_hits += len(leftovers(out))
    left = leftovers(pt.scrub(out))
    clean_hits += len(left)
    check(not left, f"샘플 '{smp.get('name') or smp.get('id')}' 화면 문장에 번호 없음", "; ".join(left[:3]))
    ctx = atc_context({"atc": out})
    check(not CODE.search(ctx), f"샘플 '{smp.get('name') or smp.get('id')}' AI 근거에도 번호 없음", ctx[:200])
check(raw_hits > 0, f"(대조) 판정 원문에는 번호가 {raw_hits}곳 있었음 — 걸러야 할 것이 실제로 있었다")

# 4) 제품 추천 API 의 응답에 자동 적용(route_class)
from app import api_product_recommend as api  # noqa: E402

app = FastAPI()


@api.router.get("/__plain_probe")
async def _probe():
    return {"note": "검토(C09 유선 기본)", "rule": "C09", "intake": {"x": "R02"}}

app.include_router(api.router)
r = TestClient(app).get("/v1/product-recommend/__plain_probe")
check(r.json() == {"note": "검토(유선 기본)", "rule": "C09", "intake": {"x": "R02"}}, "API 응답에 자동 적용", r.text)

# 건 번호·견적번호(S26-0182)는 규칙 번호가 아니다 — 'S26' 이 빠져 '-0182'로 보이던 일(2026-10-07)
check(pt.hide_codes("견적서를 발행했습니다 — S26-0182 (Rev.1)") == "견적서를 발행했습니다 — S26-0182 (Rev.1)"
      and pt.scrub({"deal_no": "S26-0182", "title": "S26-0182 건 (R02)"}) == {"deal_no": "S26-0182", "title": "S26-0182 건"},
      "건 번호 S26-0182 는 그대로, 규칙 번호만 뺌")

check(pt.hide_codes("포고핀 6핀(PGR04) — 조건부(PGR17), 4개소 커스텀 필요(PGR13·E20) PGR18·PGR24 검토, 근거 PGR03~PGR08")
      == "포고핀 6핀 — 조건부, 4개소 커스텀 필요 검토, 근거", "v1.2 규칙 번호(PGR··)·예제 번호도 화면 문장에서 뺌",
      pt.hide_codes("포고핀 6핀(PGR04) — 조건부(PGR17), 4개소 커스텀 필요(PGR13·E20) PGR18·PGR24 검토, 근거 PGR03~PGR08"))
check(pt.hide_codes("PPM1, PPF 2, TCV1, 24V 1.5A") == "PPM1, PPF 2, TCV1, 24V 1.5A", "PPM1·TCV1 같은 품목·모델은 그대로")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
