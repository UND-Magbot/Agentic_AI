"""로봇 스펙 DB(company_knowledge/robot_specs.py) 점검.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./scripts:/scripts -v ./docs:/docs backend \\
        python /scripts/test_robot_specs.py [--db]

--db 면 실제 DB 에 적재·재적재(갱신)·후보 조회까지 본다(적재 결과는 남긴다 — 운영 데이터).
"""
from __future__ import annotations

import asyncio
import sys

from app.company_knowledge import robot_specs as rs

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


# ── 파서(표 한 장) ─────────────────────────────────────────────────────────────
H = rs.HEADER
rows = rs.parse_rows([H, ["한화로보틱스", "HCR-5W", "협동로봇", "4.5 kg", "625 mm", "±0.02 mm", "10.9 kg", "IP66"],
                      ["레인보우로보틱스", "RB20-1800", "협동로봇", "20 kg", "1,800 mm", "확인 필요", "70.5 kg", "확인 필요"],
                      ["KUKA", "해당 비교급 없음", "–", "–", "–", "–", "–", "–"]], payload_class="20kg이상급", slide=6)
check(len(rows) == 2, "'해당 비교급 없음' 행은 모델이 아니라 뺌", str(rows))
w = rows[0]
check((w["payload_kg"], w["reach_mm"], w["repeatability_mm"], w["weight_kg"], w["ip_rating"])
      == (4.5, 625.0, 0.02, 10.9, "IP66"), "수치 칸 읽기(소수·단위·±)", str(w))
u = rows[1]
check(u["reach_mm"] == 1800.0 and u["repeatability_mm"] is None and u["ip_rating"] is None
      and u["unverified"] == ["반복정밀도", "IP 등급"], "'확인 필요'는 비우고 목록에 남김(추측으로 안 채움)", str(u))
check(rs.parse_rows([["가반하중급", "DOBOT"], ["3kg급", "CR3A"]]) == [], "요약표(머리 다름)는 건너뜀")
check(rs.spec_line(w) == "한화로보틱스 HCR-5W — 가반 4.5kg · 도달 625mm · 반복 ±0.02mm · 중량 10.9kg · IP66",
      "한 줄 요약", rs.spec_line(w))
check("반복 확인 필요" in rs.spec_line(u) and "IP 확인 필요" in rs.spec_line(u), "확인 필요 표시", rs.spec_line(u))

# ── 원본 비교표 전체 ─────────────────────────────────────────────────────────
deck = rs.parse_deck()
makers = {r["maker"] for r in deck}
check(len(deck) == 40 and len(makers) == 5, "비교표 40개 모델 · 제조사 5곳", f"{len(deck)} / {makers}")
by = {r["model"]: r for r in deck}
check(by["RB5-850"]["reach_mm"] == 927.7 and by["UR10e"]["payload_kg"] == 12.5 and by["CR3A"]["payload_class"] == "3kg급"
      and by["UR30"]["payload_class"] == "20kg이상급" and by["HCR-14"]["payload_class"] == "14~18kg급",
      "값·가반하중급(쪽 제목) 매칭", str({k: by[k]["payload_class"] for k in ("CR3A", "HCR-14", "UR30")}))
check(sum(1 for r in deck if r["unverified"]) == 2, "확인 필요 모델 2개(RB20-1800, RB20-1900)",
      str([r["model"] for r in deck if r["unverified"]]))

# ── 후보 고르기 ────────────────────────────────────────────────────────────────
c = rs.rank(deck, 10, reach_mm=1300, ip_min=54)
check(c and all(r["payload_kg"] >= 10 and r["reach_mm"] >= 1300 * 0.9 and rs.ip_level(r["ip_rating"]) >= 54 for r in c)
      and all(r["reach_short"] == (r["reach_mm"] < 1300) for r in c),
      "가반·IP 는 만족, 도달거리는 10% 이내 부족까지(표시)", str([(r["model"], r["reach_short"]) for r in c]))
check(c[0]["model"] in ("CR10A", "RB10-1300", "LBR iisy 11 R1300", "UR10e", "UR12e"), "여유가 적은 모델부터(과한 사양 뒤로)",
      str([r["model"] for r in c[:4]]))
b = rs.rank(deck, 5, reach_mm=1000, ip_min=54, mobile=True, limit=3)
check(b[0]["weight_kg"] <= 25 and any(not r["reach_short"] for r in b),
      "AMR 탑재: 가벼운 5kg급 우선 + 조건을 다 만족하는 모델 하나는 포함", str([(r["model"], r["weight_kg"]) for r in b]))
check(all(r["maker"] != "KUKA" for r in rs.rank(deck, 5, reach_mm=1000, exclude_makers=["kuka"], limit=10)),
      "제조사 제외")
pp = rs.policy_pick(deck, 5, reach_mm=1000, ip_min=54, mobile=True, limit=3)
check(pp[0]["maker"] == "DOBOT" and pp[0]["model"] == "CR5A" and pp[1]["maker"] == "레인보우로보틱스"
      and all(r["maker"] not in ("KUKA", "Universal Robots") for r in pp) and "K03" in pp[0]["policy_note"],
      "회사 우선순위(K03) 가격 중심: DOBOT → 레인보우, KUKA·UR 은 보충일 때만", str([(r["model"], r["policy_note"]) for r in pp]))
pd_ = rs.policy_pick(deck, 5, reach_mm=1000, ip_min=54, mobile=True, priority="domestic", limit=3)
check(pd_[0]["maker"] == "레인보우로보틱스" and pd_[1]["maker"] == "한화로보틱스" and "미정" in pd_[1]["policy_note"],
      "국내 신뢰성 중시: 레인보우 → 한화(세부 기준 미정 표시)", str([r["model"] for r in pd_]))
pm = rs.policy_pick(deck, 10, makers=["DOBOT"], limit=3)
check(pm and all(r["maker"] == "DOBOT" and r["policy_note"] == "프로젝트 지정 제조사" for r in pm),
      "프로젝트가 제조사를 지정하면 회사 일반 순위보다 지정 우선", str([r["model"] for r in pm]))
pf = rs.policy_pick(deck, 25, limit=3)
check(pf and any(r["policy_note"] == rs.FILL_NOTE for r in pf), "우선 제조사에 맞는 모델이 모자라면 보충 후보(표시)",
      str([(r["model"], r["policy_note"]) for r in pf]))
check(all(r["model"] != "RB20-1800" for r in rs.rank(deck, 20, ip_min=54)),
      "IP 확인 필요 모델은 IP 조건 만족으로 치지 않음")
check([r["maker"] for r in rs.rank(deck, 5, makers=["DOBOT"])] == ["DOBOT"] * len(rs.rank(deck, 5, makers=["DOBOT"])),
      "제조사 제한")


async def db_flow() -> None:
    first = await rs.ingest()
    again = await rs.ingest()
    check(first["parsed"] == 40 and again["inserted"] == 0 and again["updated"] == 40,
          "적재 후 다시 돌리면 갱신만(중복 없음)", f"{first} / {again}")
    stored = await rs.list_specs()
    check(len([r for r in stored if r["source_doc"] == rs.DECK_NAME]) == 40, "DB 에 40개", str(len(stored)))
    got = await rs.candidates(14, reach_mm=900, ip_min=65)
    check(got and all(r["payload_kg"] >= 14 and r["reach_mm"] >= 900 for r in got), "DB 후보 조회",
          str([rs.spec_line(r) for r in got][:3]))
    md = (rs.DOCS / "company_knowledge" / "robot_specs.md")
    check(md.exists() and "RB20-1800: 반복정밀도, IP 등급" in md.read_text(encoding="utf-8"),
          "정리 문서(docs/company_knowledge/robot_specs.md) 생성")


if "--db" in sys.argv:
    asyncio.run(db_flow())

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
