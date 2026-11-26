# -*- coding: utf-8 -*-
"""견적서 [변경사항 저장](화면 직접 수정) — 추가한 품목이 빠지지 않는지(사용자 2026-11-26).

    MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_quote_edit_save.py

예전: 변경이 20개를 넘으면 뒤(새 품목)가 말없이 빠지고, 단가를 비운 품목도 말없이 빠졌다(머리 칸·멘트만 저장).
지금: 화면 저장은 제한 없이(최대 품목 줄 LINES_MAX) 모두 반영, 단가가 빈 줄은 '단가 없음'으로 남아 발행 전에 막힌다.
AI 대화는 예전 그대로 — 한 번에 20개까지, 단가를 말한 품목만.
"""
from __future__ import annotations

import copy
import sys

from app.company_knowledge import quote_session as qs

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


FORM = {"customer": "[테스트] 저장 확인 고객", "to": "김철수 님", "contact_name": "홍길동", "contact_title": "이사", "initials": "TS",
        "contact_mobile": "010-0000-0000", "contact_email": "name@example.com"}
S = qs.draft_state({"applicable": True, "items": []}, model="", payload_kg=None, form=FORM, manual=True)
S.update(asked=["extra"], delivery={"case": 1})
S["lines"] = [{"id": "L1", "kind": "extra", "key": "extra", "name": "MG10 그리퍼", "commodity": "Magbot Gripper", "qty": 1,
               "unit_price": 1_000_000, "remark": "", "price_status": "set"}]
S["next_id"] = 2
qs._sync_work(S)
BASE = [{"id": "L1", "name": "MG10 그리퍼", "commodity": "Magbot Gripper", "qty": 1, "unit_price": 1_000_000}]


def add(n: int, price: float | None = 10_000) -> list[dict]:
    return [{"id": None, "name": f"테스트 품목 {i}", "commodity": "Etc", "qty": 1, "unit_price": price} for i in range(n)]


def save(payload: dict) -> tuple[dict, list[str]]:
    return qs.apply_ops(copy.deepcopy(S), qs.edit_ops(S, payload), None)


def names(st: dict) -> list[str]:
    return [ln["name"] for ln in st["lines"] if ln["kind"] == "extra"]


# 1. 머리 칸 여러 개 + 새 품목 — 머리 칸·품목 모두 저장
form = {k: f"[고침] {k}" for k in ("customer", "to", "contact_name", "contact_title", "contact_mobile", "contact_email")}
form.update({k: ["[고침] 멘트 1", "[고침] 멘트 2"] for k in qs.FORM_FIELDS if qs.FORM_FIELDS[k][1] == "list"})
form.update({k: f"[고침] {k}" for k in qs.FORM_FIELDS if qs.FORM_FIELDS[k][1] == "text" and k not in form})
new, _ = save({"form": form, "lines": BASE + add(5)})
check(sum(n.startswith("테스트 품목") for n in names(new)) == 5, "머리 칸을 많이 고쳐도 새 품목 5개 모두 저장", str(names(new)))
check(new["form"]["customer"] == "[고침] customer", "머리 칸도 저장")

# 2. 품목 25개 한 번에 — 20개 제한 없음
new, _ = save({"lines": BASE + add(25)})
check(sum(n.startswith("테스트 품목") for n in names(new)) == 25, "품목 25개 한 번에 저장", str(len(names(new))))

# 3. 최대 줄 수(LINES_MAX)까지
new, _ = save({"lines": BASE + add(qs.LINES_MAX - 1)})
check(len(names(new)) == qs.LINES_MAX, f"최대 {qs.LINES_MAX}줄까지 저장", str(len(names(new))))

# 4. 단가를 비운 품목 — 저장하고 '단가 없음'으로 발행 전에 막힘
new, done = save({"lines": BASE + add(2, None)})
blank = [ln for ln in new["lines"] if ln["name"].startswith("테스트 품목")]
check(len(blank) == 2 and all(ln["unit_price"] is None and ln["price_status"] == "none" for ln in blank), "단가 빈 품목도 저장(단가 미정)",
      str(blank))
check(any("단가 미정" in d for d in done), "반영 기록에 '단가 미정'", str(done))
probs = qs.problems(new)
check(sum("단가 없음" in p for p in probs) == 2, "단가 빈 품목은 발행 전에 '단가 없음'으로 막힘", str(probs))
new2, _ = qs.apply_ops(copy.deepcopy(new), qs.edit_ops(new, {"lines": [
    {"id": ln["id"], "name": ln["name"], "commodity": ln.get("commodity"), "qty": ln["qty"], "unit_price": 20_000}
    for ln in new["lines"] if ln["kind"] == "extra"]}), None)
check(not any("단가 없음" in p for p in qs.problems(new2)), "단가를 채우면 막힘 풀림", str(qs.problems(new2)))

# 5. AI 대화는 예전 그대로 — 단가 없는 품목은 넣지 않고, 한 번에 20개까지
ops = [{"op": "add", "name": "대화 품목", "qty": 1, "unit_price": None}]
new, _ = qs.apply_ops(copy.deepcopy(S), ops, "대화 품목 하나 넣어 줘")
check("대화 품목" not in names(new), "대화: 단가를 말하지 않은 품목은 넣지 않음")
ops = [{"op": "add", "name": f"대화 품목 {i}", "qty": 1, "unit_price": 10_000} for i in range(25)]
new, _ = qs.apply_ops(copy.deepcopy(S), ops, "품목 25개 각 10000원")
check(sum(n.startswith("대화 품목") for n in names(new)) == qs.CHAT_OPS_MAX, f"대화: 한 번에 {qs.CHAT_OPS_MAX}개까지", str(len(names(new))))

# 6. 규격을 비우고 품목만 적은 줄 — 저장되고 견적서에도 찍힘(예전엔 말없이 빠짐)
new, _ = save({"lines": BASE + [{"id": None, "name": "", "commodity": "설치 및 시운전", "qty": 1, "unit_price": 300_000}]})
row = next((ln for ln in new["lines"] if ln.get("commodity") == "설치 및 시운전"), None)
check(bool(row) and row["name"] == "" and row["unit_price"] == 300_000, "규격 비우고 품목만 적은 줄도 저장", str(row))
rendered = qs.render_lines(new, "ko", None)
check(any("설치 및 시운전" in str(x.get("commodity") or x.get("desc") or "") for x in rendered), "그 줄이 견적서 줄로 찍힘")
new, _ = save({"lines": BASE + [{"id": None, "name": "", "commodity": "단가 미정 품목", "qty": 1, "unit_price": None}]})
check(any(p.startswith("단가 미정 품목 — 단가 없음") for p in qs.problems(new)), "규격 없는 줄의 발행 막힘 안내는 품목 이름으로", str(qs.problems(new)))
new, _ = save({"lines": BASE + [{"id": None, "name": "", "commodity": "", "qty": 1, "unit_price": 1000}]})
check(len(names(new)) == 1, "규격·품목 둘 다 빈 줄은 넣지 않음")
new, _ = qs.apply_ops(copy.deepcopy(S), [{"op": "add", "name": "", "commodity": "대화 품목만", "qty": 1, "unit_price": 10_000}], "대화 품목만 10000원")
check(not any(ln.get("commodity") == "대화 품목만" for ln in new["lines"]), "대화: 규격 없는 줄은 예전처럼 넣지 않음")

for label in PASS:
    print("PASS", label)
for label, detail in FAIL:
    print("FAIL", label, detail[:300])
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
sys.exit(1 if FAIL else 0)
