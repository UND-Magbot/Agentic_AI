"""맥봇 공식 사양 적재(company_knowledge/magbot_catalog.py) 점검.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./scripts:/scripts -v ./docs:/docs backend \\
        python /scripts/test_magbot_catalog.py [--db]
"""
from __future__ import annotations

import asyncio
import sys

from app.company_knowledge import magbot_catalog as mc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


items = mc.parse()
names = [i["card"]["name"] for i in items]
fams = {i["card"]["family"] for i in items}
check(len(items) == 52, "모델 52개(요약표 27 + 액세서리 8 + M-LTC 17)", str(len(items)))
check(all(i["image"] for i in items), "모델마다 사진 한 장(ATC 는 Master+Tool Plate 한 세트 그대로)")
check({"TCC1", "TCV4", "TCW1", "MG16", "MG250", "mDPG-C5", "mSMG-1S25D", "3 Finger Gripper", "M-LTC-0120D", "M-LTC-3000A"}
      <= set(names), "주요 모델 이름(괄호 설명은 떼고, 원본 오타 'LTC-0120D' 는 M-LTC 로)", str(names[:12]))
check(not any(n.startswith("LTC-") for n in names) and len(set(names)) == len(names), "이름 중복·오타 없음")
by = {i["card"]["name"]: i["card"] for i in items}
check(by["MG16"]["applies_when"].startswith("강자성체 전용") and "마그네틱" in by["MG16"]["how_it_works"],
      "MG: 적용 조건(강자성체 전용)·동작 방식", by["MG16"]["applies_when"])
check(any(s["item"] == "가반하중" and s["value"].startswith("16 kgf") for s in by["MG16"]["specs"]), "MG16 가반하중 16kgf")
check(any("확인 필요" in x for x in by["TCW1"]["limits"]) and "(무선)" in by["TCW1"]["summary"],
      "'확인 필요'는 원본 그대로 한계에, 괄호 설명은 요약으로", str(by["TCW1"]["limits"]))
check(not any(s["value"].startswith("자료 없음") for s in by["MG50"]["specs"])
      and any("자료 없음" in x for x in by["MG50"]["limits"]), "'자료 없음' 값은 사양에서 빼고 한계에 표시")
ltc = by["M-LTC-0020D"]
check(ltc["family"] == mc.LTC_FAMILY and any(s["item"] == "가반하중" and "20Kgf" in s["value"] for s in ltc["specs"])
      and "볼 록킹" in ltc["how_it_works"], "M-LTC: 공압 볼 록킹·가반하중", str(ltc["specs"][:2]))
check(not any("모듈" in n for n in names), "구성 모듈(25~39쪽)은 넣지 않음")
check("Magbot ATC · 액세서리" in fams and by["Pogo Pin Male (PPM)"]["applies_when"].startswith("적용 제품"), "액세서리 8종")
check(mc.card_key("mDPG-C5") == "magbot:mdpgc5", "카드 키")
check(by["mSMG-1S25D"].get("aliases") == ["Shift Lock Gripper", "시프트락 그리퍼", "SMG1S25D"]
      and "Shift Lock Gripper" in by["mSMG-1S25D"]["summary"] and not any("Shift" in n or "시프트" in n for n in names),
      "Shift Lock Gripper 는 mSMG-1S25D 의 다른 이름(별도 카드 없음)")
check([by[n].get("series") for n in ("mDPG1S1", "mDPG4S3", "mDPG8S7", "mDPG-C5", "mDPG-C25")]
      == ["S-series"] * 3 + ["C-series"] * 2 and "mDPG 시리즈" in by["mDPG1S1"]["family"],
      "mDPG 시리즈: 1S1·4S3·8S7 = S-series, C5·C25 = C-series(개념은 제품군으로만)")
check("Super-Simple" in (by["TCV1"].get("aliases") or []) and "Safety Lock 기능 포함" in by["TCV4"]["strengths"]
      and any(x["item"] == "반복 정밀도" for x in by["MG30"]["specs"])
      and not any(x["item"] == "반복 정밀도" for x in by["MG50"]["specs"])
      and "intro:reference:도어힌지이송공정" in (by["MG16"].get("related") or [])
      and any("200ms" in x["value"] for x in by["TCV1"]["specs"] if x["item"] == "전기 사양"),
      "예전 소개서 카드에만 있던 별칭·정밀도·센서·사례 연결은 모델 카드로(값이 다르면 공식 사양 우선)")

panel = mc.panel_models()
pby = {i["card"]["name"]: i for i in panel}
check(list(pby) == ["TCT1", "MG3060", "MG7179", "MGM3D", "MGM5T", "MGM10Q", "MGM3H"] and all(i["image"] for i in panel),
      "MGP·MGM 7모델(원본 'MG3O6O'·'MGM1Oq' 는 숫자로) + 모델마다 사진", str(list(pby)))
check({pby[n]["card"]["family"] for n in ("TCT1", "MG3060", "MG7179")} == {mc.MGP_FAMILY}
      and {pby[n]["card"]["family"] for n in ("MGM3D", "MGM5T", "MGM10Q", "MGM3H")} == {mc.MGM_FAMILY},
      "모델 분류: TCT1·MG3060·MG7179 = MGP, MGM 3D·5T·10Q·3H = MGM")
check("MGM16Q" in pby["MGM10Q"]["card"]["aliases"] and "MG3O6O" in pby["MG3060"]["card"]["aliases"]
      and any(s["value"].startswith("24V 3A X 20EA") for s in pby["MG7179"]["card"]["specs"])
      and any("1,057g" in x for x in pby["MG7179"]["card"]["limits"])
      and "MGM5T / MGM3T" in pby["MGM5T"]["card"]["summary"] and not any("5T" in x for x in pby["MGM5T"]["card"]["limits"]),
      "MGM 변형·원본 표기는 다른 이름으로, MGP 사양·원본 표 오류는 한계에")

hm = mc.h_models()
check([i["card"]["name"] for i in hm] == ["TCHK100", "TCHK150", "TCHK220"] and all(len(i["images"]) == 2 for i in hm)
      and all(i["card"]["family"] == mc.H_FAMILY != mc.ATC_AUTO and i["card"]["series"] == "H시리즈"
              and i["card"]["applies_when"].startswith("산업용 로봇") for i in hm)
      and all(any(x["item"].startswith("반복 정밀도") and x["value"] == "±0.05mm" for x in i["card"]["specs"]) for i in hm)
      and any(x["value"] == "220kgf" for x in hm[2]["card"]["specs"]),
      "H시리즈: TCHK100·150·220 모델 카드, 공통 행은 세 모델 모두, 사진은 H시리즈 그림 2장 공통")

et = mc.endtool_models()
check([i["card"]["name"] for i in et] == ["Magnet Gripper 1", "Magnet Gripper 2"]
      and all(i["card"]["family"] == mc.SMG_FAMILY for i in et) and len({i["image"] for i in et}) == 2,
      "소개서 20쪽 End Tool: Magnet Gripper 1·2(Air Gripper 는 없앰), 형상기억 그리퍼 제품군, 사진 1:1(박스 그림 3장을 한 장으로)")


async def db_flow() -> None:
    from sqlalchemy import text

    from app.database import SessionLocal

    async with SessionLocal() as db:
        n = (await db.execute(text("SELECT count(*) FROM knowledge_cards WHERE card_key LIKE 'magbot:%' AND kind = 'product' AND active"))).scalar()
        off = (await db.execute(text("SELECT count(*) FROM knowledge_cards WHERE card_key = ANY(:k) AND active"),
                                {"k": list(mc.SUPERSEDED_KEYS)})).scalar()
        photos = (await db.execute(text("SELECT count(DISTINCT k.id) FROM knowledge_cards k JOIN product_images i ON "
                                        "i.card_id = k.id WHERE k.card_key LIKE 'magbot:%' AND i.active"))).scalar()
    check(n == 64 and off == 0 and photos == 64, "DB: 64장(공식 사양 52 + MGP·MGM 7 + TCHK 3 + Magnet Gripper 2) 켜짐 · 대체된 소개서 카드 0장 켜짐 · 모두 승인 사진",
          f"{n} / {off} / {photos}")
    async with SessionLocal() as db:
        fam = (await db.execute(text("SELECT card->>'family' FROM knowledge_cards WHERE card_key = :k"),
                                {"k": "intro:product:cylindricalgripper"})).scalar()
    async with SessionLocal() as db:
        atc = dict((await db.execute(text("SELECT card->>'name', card->>'family' FROM knowledge_cards WHERE kind = 'product' "
                                          "AND active AND card->>'family' LIKE 'Magbot ATC%'"))).all())
    check(atc.get("MTC (Manual TC)") == "Magbot ATC · 수동 툴체인저" and atc.get("DTC (Dual TC)") == "Magbot ATC · 듀얼 툴체인저"
          and atc.get("TCV1") == atc.get("TCV4-Vision") == mc.ATC_AUTO and atc.get("TCHK220") == mc.H_FAMILY and "맥봇 자동툴체인져 / H시리즈" not in atc
          and atc.get("PneuMatic Male (PMM)") == "Magbot ATC · 액세서리",
          "맥봇 ATC 한 묶음: 자동·수동(MTC)·듀얼(DTC)·공압(M-LTC)·액세서리", str(sorted(set(atc.values()))))
    async with SessionLocal() as db:
        quad = dict((await db.execute(text("SELECT card_key, card->>'name' || ' | ' || (card->>'family') FROM knowledge_cards "
                                           "WHERE card_key = ANY(:k)"), {"k": list(mc.NAME_OVERRIDES)})).all())
        fam4 = (await db.execute(text("SELECT count(*) FROM knowledge_cards WHERE kind='product' AND active "
                                      "AND card->>'family' = '4족 로봇'"))).scalar()
    check(quad.get("intro:product:outdoorselfdrivingrobotmpayload") == "Outdoor Self-Driving Robot(X30 Customizing) | 4족 보행 로봇"
          and quad.get("intro:product:outdoorselfdrivingrobotcustomizing", "").startswith("Outdoor Self-Driving Robot(M20 Customizing)")
          and fam4 == 0, "4족: M20·X30 Customizing 이름, X30 Customizing 은 4족 보행 로봇군, '4족 로봇' 제품군 없음", str(quad))
    check(fam == "Magbot EOAT · 전동 핑거 그리퍼", "CYLINDRICAL_GRIPPER 는 전동 핑거 그리퍼 제품군(사용자 지정, 재적재해도 유지)", str(fam))
    async with SessionLocal() as db:
        plat = (await db.execute(text("SELECT kind, active FROM knowledge_cards WHERE card_key = :k"),
                                 {"k": mc.PLATFORM_KEY})).first()
        left = (await db.execute(text("SELECT count(*) FROM knowledge_cards WHERE kind = 'product' AND active "
                                      "AND card->>'family' = 'Magbot Platform'"))).scalar()
    check(tuple(plat or ()) == ("capability", True) and left == 0,
          "맥봇 플랫폼(ATC·EOAT·FIX·CORE)은 제품이 아니라 회사 역량 카드 1장", f"{plat} / {left}")
    async with SessionLocal() as db:
        sw = (await db.execute(text("SELECT kind, active FROM knowledge_cards WHERE card_key = :k"),
                               {"k": mc.SWITCHING_KEY})).first()
        sw_left = (await db.execute(text("SELECT count(*) FROM knowledge_cards WHERE kind = 'product' AND active "
                                         "AND card->>'name' ~ '스위칭 마그네틱'"))).scalar()
    check(tuple(sw or ()) == ("capability", True) and sw_left == 0,
          "스위칭 마그네틱은 ATC 의 툴 교체 기술 — 회사 역량 카드 1장(제품 카드 없음)", f"{sw} / {sw_left}")
    from app.proposal_project import plan
    g = await plan.gripper_names()
    c = plan.gripper_candidates("MG16", g)
    c2 = plan.gripper_candidates("시프트락 그리퍼", g)
    check(c2[0] == "mSMG-1S25D", "AI 가 '시프트락 그리퍼'로 적어도 mSMG-1S25D 카드로", str(c2[:3]))
    from app.company_knowledge import product_recommend as pr
    async with SessionLocal() as db:
        cat = await pr.product_catalog(db)
    check((pr.match_product("Shift Lock Gripper", cat) or {}).get("name") == "mSMG-1S25D", "추천: 다른 이름으로도 같은 카드")
    check(c[0] == "MG16" and "MG25" in c and any(x.startswith("mDPG") for x in c) and any("Finger" in x for x in c),
          "그리퍼 후보: AI 추천 + 같은 제품군 + 다른 방식 대표", str(c))


if "--db" in sys.argv:
    asyncio.run(db_flow())

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
