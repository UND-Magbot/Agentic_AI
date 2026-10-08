"""회사 지식(소개서 → 카드) + 회상 엔진 검증 — 모델·DB 불필요(가짜 주입).

실제 소개서 쪽 원문을 발췌해 쓴다(맥봇 툴체인저 사양표 9쪽, 휠 너트 이송 44쪽, 스팟 충전 도킹 47쪽 컨셉).
"""
import asyncio
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.company_knowledge import cards as kc, intro_deck as deck, recall as rc  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


S9 = deck.Slide(9, "Products // 표: Model |  | TCC1 | TCV1 | TCV2 ; Payload (Weight Capability) |  | 5kgf [49N / 11lbs] | "
                   "10kgf [98N / 22lbs] | 16kgf [157N / 35lbs] ; IP Code (Ingress Protection) |  | IP45 (Option IP56) ; "
                   "Temperature and Humidity |  | -10°C~60°C // 맥봇 자동툴체인져 / mTC시리즈")
S44 = deck.Slide(44, "Reference // Customized Multi-MG // 자동차 휠 너트 이송 시스템 // Nut Guide Panel 교체 방식 // 본 공정은 "
                     "자동차 휠 너트 이송 공정으로, 차량 타이어 조립 과정에서 휠 너트 5개를 동시에 전량 이송함으로써 사이클 타임을 "
                     "획기적으로 단축하였습니다.")
S47 = deck.Slide(47, "Reference // Spot Robot 충전 도킹 시스템 // Customized ATC // 본 컨셉은 4족 로봇이나 이동형 로봇의 충전 "
                     "과정에서 발생하는 반복 정밀도 저하 문제를 해결하기 위해, 지그(Jig)와 같은 안착 보조 장치로 활용되어 안정적인 "
                     "충전을 지원합니다.", ocr="UND magbot [MG16TS]\n75×75mm")

# ── 1) 쪽 판독 대상 ─────────────────────────────────────────────────────────
check(deck.Slide(70, "PRODUCTS // Quadruped Robot Line-up", pics=8).needs_ocr(), "그림 많고 글 빈약한 쪽은 이미지 판독")
check(not deck.Slide(9, S9.text * 3, pics=2).needs_ocr(), "사양표가 글상자에 있으면 판독 안 함")
check(deck.Slide(71, "SPECIFICATIONS // FEATURES // Stair Climbing Perception // Auto Charging " * 8 + "Deep Robotics / X30", pics=5).needs_ocr(),
      "사양 제목만 있고 수치가 없는 쪽은 판독(X30 사양이 그림 속)")
check(deck._dedupe_lines(["KITECH 한국생산기술연구원\nSBC", "SBC\n DGIST "]) == "KITECH 한국생산기술연구원\nSBC\nDGIST",
      "겹친 조각의 같은 줄은 한 번만")

# ── 2) 카드 검증 ────────────────────────────────────────────────────────────
raw9 = {"kind": "product", "name": "맥봇 자동 툴체인저 mTC 시리즈", "family": "맥봇 ATC",
        "specs": [{"model": "TCV2", "item": "가반하중", "value": "16kgf"},
                  {"model": "TCV4", "item": "가반하중", "value": "30kgf"},            # 이 쪽 발췌에 없음 → 버림
                  {"model": "", "item": "보호등급", "value": "IP45 (옵션 IP56)"}],
        "limits": ["기본 IP45"], "cues": ["로봇 툴을 자주 바꿔야 해서 라인이 멈춘다"], "customers": ["현대자동차"]}
c9 = kc.validate(raw9, S9)
specs = {(s["model"], s["value"]) for s in c9.card["specs"]}
check(("TCV2", "16kgf") in specs and ("TCV4", "30kgf") not in specs and any("30kgf" in d for d in c9.dropped),
      "원문에 없는 사양 수치는 버리고 보고", str(c9.dropped))
check(c9.card["customers"] == [], "원문에 없는 고객명은 버림")
check(c9.card["specs"][0]["source"] == "원문" and c9.card["slides"] == [9], "사양 출처·쪽 기록")
c47 = kc.validate({"kind": "reference", "name": "스팟 충전 도킹", "specs": [{"model": "MG16TS", "item": "안착 플레이트",
                                                                        "value": "75×75mm"}]}, S47)
check(c47.card["is_concept"] and kc.evidence_of(c47.card) == "컨셉(회사 제안)", "'본 컨셉은' 쪽은 컨셉 근거로")
check(c47.card["specs"][0]["source"] == "이미지 판독", "글상자에 없고 판독에만 있는 수치는 '이미지 판독' 표시")
c44 = kc.validate({"kind": "reference", "name": "자동차 휠 너트 이송", "effect": "사이클 타임 30% 단축",
                   "effect_numbers": [{"metric": "동시 이송", "value": "휠 너트 5개"}]}, S44)
check(c44.card["effect_numbers"] == [{"metric": "동시 이송", "value": "휠 너트 5개"}] and "30" not in c44.card["effect"]
      and kc.evidence_of(c44.card) == "실적(회사 수행)", "효과 수치는 원문 것만, 없는 %는 문장에서 뺌", c44.card["effect"])
check(kc.validate({"kind": "slogan", "name": "x"}, S9) is None and kc.validate({"kind": "product"}, S9) is None,
      "모르는 종류·이름 없는 카드는 버림")

S49 = deck.Slide(49, "Customizing 도어힌지 이송 그리퍼 // S POLE Ø22mm 구배 적용 사양 // H.P(kgf) : 3.5kg~4kg 성능 (GRIP성공) // "
                     "H.P(kgf) : 1.5kg~2kg 성능 (GRIP실패) // N POLE : Ø15mm // 본 공정은 차량용 도어힌지 이송 공정입니다.")
c49 = kc.validate({"kind": "reference", "name": "도어힌지 이송", "lessons": [
    "S극 Ø22mm 구배 사양은 3.5~4kg 흡착으로 파지 성공, 1.5~2kg 사양은 실패",
    "자석 극 크기를 30mm 로 키우면 안정적"]}, S49)
check(c49.card["lessons"] == ["S극 Ø22mm 구배 사양은 3.5~4kg 흡착으로 파지 성공, 1.5~2kg 사양은 실패"]
      and any("30mm" in d for d in c49.dropped), "교훈(해 보니)은 원문 수치만 남김", str(c49.dropped))
check("해 보니: S극 Ø22mm" in kc.card_markdown(c49.card, "실적(회사 수행)"), "교훈이 정리에 나옴")

# 카드 이름은 그 쪽에 나오는 말로(실측: 100쪽 H자동차 물류 카드에 앞 사례 이름 '자동차 도장 붓 교체 공정'을 붙임)
S100 = deck.Slide(100, "국내 레퍼런스1 // < H 자동차 메뉴얼 가이드 북 물류 자동화 프로젝트 >")
c100 = kc.validate({"kind": "reference", "name": "자동차 도장 붓 교체 공정", "process": "매뉴얼 가이드 북 물류 자동화"}, S100)
check(c100.card["name"] == "매뉴얼 가이드 북 물류 자동화" and any("이 쪽에 없는 말" in d for d in c100.dropped),
      "쪽에 없는 이름은 공정명으로 바꾸고 보고", c100.card["name"])
check(kc.validate({"kind": "capability", "name": "주요 고객사"}, deck.Slide(5, "주요 고객사 // 대기업 · 그룹사")).card["name"]
      == "주요 고객사", "쪽에 있는 이름은 그대로")
c65 = kc.validate({"kind": "reference", "name": "수직 원주 용접시스템", "source_issues": ["제목 '수삽 공정'과 설명(수직 원주 용접)이 다름"]},
                  deck.Slide(65, "수삽 공정 이송 및 작업자 연계 라인 // > 본 공정은 수직 원주 용접시스템으로"))
check(c65.card["source_issues"] == ["65쪽: 제목 '수삽 공정'과 설명(수직 원주 용접)이 다름"], "소개서 편집 오류 기록")

# 병합·연결
a = dict(c9.card, cues=["툴 교체가 잦다"], summary="짧음")
b = dict(c9.card, slides=[7], cues=["공압 없이 툴 교체"], summary="더 긴 요약 문장입니다")
m = kc.merge(a, b)
check(sorted(m["slides"]) == [7, 9] and set(m["cues"]) == {"툴 교체가 잦다", "공압 없이 툴 교체"} and m["summary"] == "더 긴 요약 문장입니다",
      "같은 이름 카드 병합: 쪽·단서 합집합, 글은 긴 쪽")
check(kc.merge(dict(c44.card), dict(c47.card, name=c44.card["name"]))["is_concept"] is False,
      "한 쪽이라도 실제 수행이면 컨셉 아님")
pool = {kc.card_key("product", "맥봇 MG 마그네틱 그리퍼"): {"kind": "product", "name": "맥봇 MG 마그네틱 그리퍼", "family": "맥봇 EOAT"},
        kc.card_key("reference", "휠 너트"): {"kind": "reference", "name": "휠 너트", "products_used": ["MG 마그네틱 그리퍼"]}}
kc._link(pool)
pk, rk = list(pool)
check(pool[rk]["related"] == [pk] and pool[pk]["related"] == [rk], "사례 ↔ 제품 연결(쓴 제품 표기로)")


async def fake_cards_chat(messages, **kw):
    user = messages[-1]["content"]
    assert "[이미 만든 카드 이름]" in user
    if "9쪽" in user:
        return json.dumps({"cards": [raw9]}, ensure_ascii=False)
    if "44쪽" in user:
        return json.dumps({"cards": [{"kind": "reference", "name": "자동차 휠 너트 이송", "products_used": ["맥봇 자동 툴체인저"],
                                      "cues": ["여러 부품을 한 번에 옮겨 사이클 타임을 줄이고 싶다"]}]}, ensure_ascii=False)
    return "형식 오류"


built, report = asyncio.run(kc.build_cards([S9, S44, S47], chat=fake_cards_chat))
check(len(built) == 2 and any("47쪽" in r for r in report), "쪽별 카드 생성 + 실패 쪽은 보고하고 계속", str(report))
ref = next(c for c in built.values() if c["kind"] == "reference")
check(ref.get("related") == [kc.card_key("product", "맥봇 자동 툴체인저 mTC 시리즈")], "생성 후 사례-제품 연결")
md = kc.card_markdown(c9.card, "제품 사양")
check("TCV2 가반하중 16kgf" in md and "떠올릴 상황" in md, "사람이 읽는 정리")

# 이름만 같은 다른 공정은 합치지 않는다(실측: 38쪽 다품종 이송 + 49쪽 도어힌지가 한 카드로 합쳐짐)
S38 = deck.Slide(38, "Reference // 다품종 이송 플랫폼 // eMG (electric Magnetic Gripper) // 본 공정은 다양한 형상의 제품을 하나의 "
                     "마그네틱 그리퍼로 공용 대응하는 자동화 공정으로, 로봇 이송과 반전기 고정용 지그(Fix) 기능을 동시에 수행합니다.")
S39 = deck.Slide(39, "Reference // MG (Magnetic Gripper) // 베어링 이송 마그네틱 그리퍼 패널 타입 // 본 공정은 다량의 베어링을 한 번에 "
                     "이송하기 위해 커스터마이징 제작된 MGP 시스템입니다.")
S40 = deck.Slide(40, "Reference // 베어링 이송 마그네틱 그리퍼 패널 타입 // 본 공정은 맥봇 마그네틱 그리퍼를 활용하여 가공물 이송 공정을 "
                     "수행합니다. // MG (Magnetic Gripper) // Magnetic Gripper / Panel Type")
check(not kc.same_case({"slides": [38]}, {}, S49, [S38, S49]), "다른 공정(38·49쪽)은 같은 사례 아님")
S69 = deck.Slide(69, S47.text)
check(kc.same_case({"slides": [47]}, {}, S69, [S47, S69]), "소개서에 그대로 반복된 쪽(47·69쪽 스팟 도킹)은 합침")
check(not kc.same_case({"slides": [39]}, {}, S40, [S39, S40]), "애매하면 합치지 않음(보수적: 섞이는 것보다 중복이 낫다)")


async def same_name_chat(messages, **kw):
    return json.dumps({"cards": [{"kind": "reference", "name": "마그네틱 이송"}]}, ensure_ascii=False)

two, _ = asyncio.run(kc.build_cards([S38, S49], chat=same_name_chat))
check(len(two) == 2, "모델이 같은 이름을 붙여도 다른 공정이면 카드 2장", str(list(two)))
seen_msgs = []


async def capture_chat(messages, **kw):
    seen_msgs.append(messages[-1])
    return json.dumps({"cards": []})

asyncio.run(kc.cards_from_slide(S49, [], chat=capture_chat, image="QUJD"))
check(seen_msgs[-1].get("images") == ["QUJD"] and "쪽 이미지 첨부" in seen_msgs[-1]["content"],
      "쪽 이미지를 함께 보내 사진·수치 짝을 판단하게 함")

# 소개서 편집 오류(제목·설명이 다른 공정) — 후보는 낱말로, 판정은 모델로(실측 52장 중 후보 14, 실제 4)
wrong = {"kind": "reference", "name": "수삽 공정 이송 및 작업자 연계 라인", "summary": "원통형 부품을 회전하며 원주 방향 용접을 수행"}
fine = {"kind": "reference", "name": "Taper Bearing 조립기", "summary": "베어링 구성품을 자동 공급·정렬해 체결"}
check(kc.title_mismatch(wrong) and kc.title_mismatch(fine), "낱말이 안 겹치면 후보(영문 이름도 후보가 됨)")
check(not kc.title_mismatch(dict(wrong, summary="")), "설명이 없으면 판정하지 않음")


def same_reply(same):
    async def _chat(messages, **kw):
        return json.dumps({"same": same, "reason": "r"}, ensure_ascii=False)
    return _chat

check(asyncio.run(kc.same_equipment(fine, same_reply(True)))[0], "같은 설비면 그대로")
check(not asyncio.run(kc.same_equipment(wrong, same_reply(False)))[0], "다른 설비 판정")


def mm_chat(same):
    async def _chat(messages, **kw):
        if "같은 공정·설비를 가리키는지" in messages[0]["content"]:
            return await same_reply(same)(messages)
        return json.dumps({"cards": [wrong]}, ensure_ascii=False)
    return _chat

S65 = deck.Slide(65, "수삽 공정 이송 및 작업자 연계 라인 // > 본 공정은 수직 원주 용접시스템으로 원통형 부품의 원주 방향 용접")
mm, mm_report = asyncio.run(kc.build_cards([S65], chat=mm_chat(True)))
only = next(iter(mm.values()))
check(not only.get("needs_review") and not only["source_issues"], "모델이 같다고 하면 카드 유지")
mm2, mm2_report = asyncio.run(kc.build_cards([S65], chat=mm_chat(False)))
bad = next(iter(mm2.values()))
check(bad.get("needs_review") and "확인 필요" in bad["source_issues"][0] and bad["name"] == wrong["name"]
      and any("확인 필요로 제외" in r for r in mm2_report),
      "제목·설명이 다른 설비면 이름을 추측해 고치지 않고 확인 필요로 제외(회상에서 빠짐)", str(bad["source_issues"]))
import tempfile  # noqa: E402
_real_docs, kc.DOCS = kc.DOCS, Path(tempfile.mkdtemp())      # 실제 docs/company_knowledge/cards.md 를 덮지 않게
md_path = kc.write_markdown({"a": only, "b": bad}, [])
kc.DOCS = _real_docs
md_text = md_path.read_text(encoding="utf-8")
check("확인 필요로 뺀 카드 (1장" in md_text and md_text.count("### ") == 1, "정리 문서: 확인 필요 카드는 본문에서 빼고 목록으로")

# ── 3) 회상: 연상 점수 ──────────────────────────────────────────────────────
ms = [("철 부품 이송", "knowledge_cards", 1, 0.80), ("다품종", "knowledge_cards", 1, 0.40),     # 낮은 유사도 무시
      ("철 부품 이송", "knowledge_cards", 2, 0.76), ("다품종", "knowledge_cards", 2, 0.70),
      ("툴 교체", "knowledge_cards", 2, 0.66), ("철 부품 이송", "knowledge_cards", 2, 0.60)]   # 같은 신호는 최고값만
agg = rc.aggregate(ms)
check(round(agg[("knowledge_cards", 1)][0], 3) == 0.8 and agg[("knowledge_cards", 2)][0] > agg[("knowledge_cards", 1)][0],
      "여러 신호에 두루 걸린 카드가 한 번 세게 걸린 카드보다 앞", str(agg))
check(agg[("knowledge_cards", 2)][1][0] == "철 부품 이송", "걸린 신호를 강한 순서로")
weak = rc.aggregate([("사람이 하고 있음", "experience_cards", 35, 0.52), ("사람이 하고 있음", "knowledge_cards", 164, 0.66)])
check(("experience_cards", 35) not in weak, "한 신호에서 최상위보다 한참 낮은 약한 연상은 치지 않음(상대 하한)")
terms = rc.key_terms({"objects": "식판·국그릇·밥그릇 등 식기를", "material": "", "process": "세척 후 분류·적재"})
check("식기" in terms and "공정" not in terms, "상황의 핵심 명사(조사 뗀 꼴 포함)", str(terms))
check("주물" in rc.key_terms({"objects": "주철 브라켓", "aliases": ["주물", "주조품"]}), "동의어도 핵심 명사로")
check(rc.term_bonus({"식기", "주철"}, rc._norm("다양한 식기를 비전으로 분류")) == rc.TERM_BONUS
      and rc.term_bonus({"a", "b", "c", "d"}, "abcd") == rc.TERM_CAP, "핵심 명사 가산(상한)")
check(rc.feedback_bonus(5, 0) == rc.FEEDBACK_CAP and rc.feedback_bonus(0, 2) == -0.08, "피드백 가감(상한)")


def card(t, i, ev, **c):
    return rc.Card(t, i, ev, {"kind": c.pop("kind", "reference"), **c})


pool_cards = {("knowledge_cards", 1): card("knowledge_cards", 1, "실적(회사 수행)", name="휠 너트", related=["P"], score=0),
              ("knowledge_cards", 2): card("knowledge_cards", 2, "제품 사양", kind="product", name="MG")}
pool_cards[("knowledge_cards", 1)].score = 1.0
rc.spread(pool_cards, {"P": ("knowledge_cards", 2)})
check(pool_cards[("knowledge_cards", 2)].score == 0.5, "사례에서 쓴 제품으로 활성이 번짐")

# ── 4) 근거 확인 ────────────────────────────────────────────────────────────
e7 = card("experience_cards", 7, "제안", title="GS칼텍스 4족 로봇 주유소 순찰", process="설비 순찰",
          solution="4족 로봇이 정해진 경로를 순찰하며 열화상으로 설비 온도를 확인한다")
k3 = card("knowledge_cards", 3, "실적(회사 수행)", name="자동차 휠 너트 이송",
          solution="휠 너트 5개를 동시에 전량 이송해 사이클 타임을 단축")
pool = {c.ref: c for c in (e7, k3)}
hits, notes = rc.ground({"hits": [
    {"ref": "K3", "fit": "맞음", "why": "예전에 휠 너트 5개를 동시에 옮겨 효과가 좋았습니다.",
     "facts": ["휠 너트 5개를 동시에 전량 이송해 사이클 타임을 단축", "생산성 40% 향상"]},
    {"ref": "E7", "fit": "맞음", "why": "순찰 제안을 한 적이 있습니다. 현장에서 효과가 좋았습니다.",
     "facts": ["4족 로봇이 정해진 경로를 순찰하며"]},
    {"ref": "K99", "fit": "맞음"}, {"ref": "K3", "fit": "최고"}]}, pool)
check([h["ref"] for h in hits] == ["K3", "E7", "K3"] and any("K99" in n for n in notes), "모르는 카드 제외")
check(hits[0]["facts"] == ["휠 너트 5개를 동시에 전량 이송해 사이클 타임을 단축"] and any("K3 카드에 없는 인용 1건" in n for n in notes),
      "카드에 없는 인용(40%) 제외")
check("효과가 좋았습니다" in hits[0]["why"], "실적 카드는 효과 표현 허용")
check("효과가 좋았" not in hits[1]["why"] and "순찰 제안을 한 적이 있습니다" in hits[1]["why"],
      "제안 카드의 효과 단정 문장 제거", hits[1]["why"])
check(hits[2]["fit"] == "주의", "모르는 fit 값은 '주의'")
fix_hits, fix_notes = rc.ground({"hits": [{"ref": "E3", "fit": "맞음", "why": "w", "facts": []}]}, pool)
check([h["ref"] for h in fix_hits] == ["K3"] and any("E3 → K3" in n for n in fix_notes), "접두어만 틀린 카드 번호는 바로잡음")
none_hits, _ = rc.ground({"hits": [{"ref": "K3", "fit": "맞음", "why": "w", "caution": "none"}]}, pool)
check(none_hits[0]["caution"] == "", "'none' 같은 빈 주의는 비움")
check(e7.line().startswith("E7 [제안] GS칼텍스 4족 로봇 주유소 순찰 — 설비 순찰"), "기억 목록 한 줄", e7.line())

big = [card("knowledge_cards", i, "제품 사양", kind="product", name=f"제품{i}", summary="요약 " * 40) for i in range(400)]
q, ctx = rc._reflect_inputs("상황 " * 2000, {"process": "용접"}, big[:6], big)
check(len(q) <= rc.QUESTION_MAX and len(ctx) <= rc.CONTEXT_MAX + 2, "브리지 글자 수 한도 안", f"{len(q)} {len(ctx)}")
check("[다른 후보]" in ctx and "[떠오른 카드]" in ctx and "[전체 기억 목록]" not in ctx, "상세 + 후보 목록만 전달(전체 목록 아님)")
k1 = rc.cache_key("주철 브라켓 이송", "gpt", 8, "knowledge_cards:110:t1")
check(k1 == rc.cache_key(" 주철 브라켓 이송 ", "gpt", 8, "knowledge_cards:110:t1"), "같은 상황·같은 기억이면 같은 결과 재사용")
check(k1 != rc.cache_key("주철 브라켓 이송", "gpt", 8, "knowledge_cards:111:t2") and k1 != rc.cache_key("주철 브라켓 이송", "local", 8, "knowledge_cards:110:t1"),
      "카드가 바뀌거나 두뇌가 다르면 다시 계산")


async def ask_fail(question, context, *, user_id=None):
    raise RuntimeError("bridge down")


async def local_reflect(messages, **kw):
    return json.dumps({"thinking": "철 부품", "hits": [{"ref": "K3", "fit": "맞음", "why": "w", "facts": []}]})

data, used = asyncio.run(rc._think("q", "c", "gpt", 3, chat=local_reflect, ask=ask_fail))
check(used == "local" and data["hits"][0]["ref"] == "K3", "GPT 되짚기 실패 시 사내 모델로 대체(표시)")


async def ask_ok(question, context, *, user_id=None):
    assert user_id == 3 and "JSON" in question
    return '설명 {"thinking": "t", "hits": []} 끝'

data, used = asyncio.run(rc._think("q", "c", "gpt", 3, chat=local_reflect, ask=ask_ok))
check(used == "gpt" and data["thinking"] == "t", "GPT 되짚기(요청자 전달, 앞뒤 설명 무시)")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
