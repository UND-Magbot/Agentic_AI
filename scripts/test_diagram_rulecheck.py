"""원문 ↔ 스펙 결정론 대조(diagram.rulecheck) 검증 — LLM 불필요."""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.diagram import rulecheck as rc  # noqa: E402
from app.diagram.spec import ConceptMapSpec  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


# 9/23 약액 요청 원문 (26b 가 22/22 반영했던 요청)
REQ = ("한화로봇 14kg Class 100 되는지 확인 기존 전용장비에서 센서 쓰던것을 비전으로 대체 보틀 목이 기울어져 있는 "
       "경우를 검출해야함 로봇 투입 -> 비전으로 x,y,z 센터 잡은후 너트러너로 뚜껑 풀기 -> 로봇이 액주입장비로 이동, "
       "비전으로 xy 확인 후 병 놓기 -> 액주입장치 불량 시 병 치우기 -> 필링 후 뚜껑 잠그기 -> 비전 확인. "
       "빈 병 1.5Kg, 액 3.7Kg. 금주 금요일까지. 비전 장착 개수 및 고정 방식 검토, 컨트롤러 PLC(비전 IPC 포함), "
       "Keyence 또는 인텔 리얼센스. 약액 주입 210초, CT 검토.")


def good_spec() -> ConceptMapSpec:
    return ConceptMapSpec.model_validate(
        json.loads((ROOT / "docs/concept_drawio/chem_spec.json").read_text(encoding="utf-8")))


items = rc.extract(REQ)
texts = [i.text for i in items]
for t in ("14kg", "1.5Kg", "3.7Kg", "210초", "Class 100", "PLC", "IPC", "Keyence", "리얼센스",
          "일정: 금주 금요일까지", "불량 배출 설비", "로봇 설비", "캡 개봉·체결 설비", "주입 설비"):
    check(t in texts, f"원문 항목 추출: {t}", str(texts))
check(sum(i.kind == "process" for i in items) >= 4, "화살표 공정 단계 추출")
check(not any(t in texts for t in ("x", "xy", "Class")), "축·일반어는 항목 아님")

# 1) 오탐 없음 — 사람이 봐도 요청을 다 담은 26b 스펙에서 누락 0
spec = good_spec()
rc.verify(spec, items)
check(not [i for i in items if not i.covered], "정답 스펙 누락 0 (오탐 없음)",
      str([i.text for i in items if not i.covered]))

# 2) 12b 실측 누락 재현 → 전부 검출
spec = good_spec()
ll = spec.line_layout
ll.stations = [s for s in ll.stations if s.kind != "reject"]
ll.flows = [f for f in ll.flows if f.dst != "rej"]
spec.footnote = "※ 중량 정보: 빈 병 1.5kg"
spec.tables[1].rows = [r for r in spec.tables[1].rows if "Keyence" not in r[1]]
items = rc.extract(REQ)
rc.verify(spec, items)
miss = {i.text for i in items if not i.covered}
for t in ("3.7Kg", "일정: 금주 금요일까지", "불량 배출 설비", "Keyence", "리얼센스"):
    check(t in miss, f"누락 검출: {t}", str(miss))
check("1.5Kg" not in miss, "남아 있는 수치는 누락 아님 (대소문자·공백 무시)")

# 3) 강제 보완 → 재대조 누락 0, 설비·흐름·각주 확인
filled = rc.backfill(spec, items)
check(set(filled) == miss, "누락 전부 보완", str(filled))
again = rc.extract(REQ)
rc.verify(spec, again)
check(not [i for i in again if not i.covered], "보완 후 재대조 누락 0")
kinds = [s.kind for s in spec.line_layout.stations]
check(kinds.index("reject") == kinds.index("filler") + 1, "불량 배출은 주입 설비 바로 뒤")
check(any(f.dst == "reject" and f.style == "dashed" for f in spec.line_layout.flows), "불량 흐름 추가")
check("액 3.7Kg" in spec.footnote and "금요일" in spec.footnote, "수치·일정은 원문 구절로 각주", spec.footnote)
check(spec.footnote.count("Keyence") == 1, "같은 구절은 한 번만", spec.footnote)

# 4) LLM 판정 교차 확인
spec = good_spec()
check(rc.cross_check(spec, "약액 주입 210초"), "스펙에 있는 수치 → 판정 유지")
check(not rc.cross_check(spec, "약액 주입 250초"), "스펙에 없는 수치 → 판정 뒤집음")
check(rc.cross_check(spec, "Class 100 대응 확인"), "'100 대응' 은 수량이 아님")
check(rc.cross_check(spec, "비전 검사 항목"), "숫자·영문 없는 한글 항목은 판정 유지")

# 5) 단위 경계
toks = [t for _, t in rc._num_units("Class 100 대응, 비전 4대를, 100 대응, 2개씩, 3,7kg, 210초간")]
check(toks == ["4대", "2개", "3.7kg", "210초"], "수치+단위 경계", str(toks))

# 6) 구조 규칙은 키워드가 있을 때만 — '완제품 배출' 만으로 불량 배출 설비를 요구하지 않음
kinds_needed = {i.station_kind for i in rc.extract("컨베이어 투입 후 완제품 배출") if i.kind == "structure"}
check("reject" not in kinds_needed, "'배출' 만으로 불량 설비 요구 안 함", str(kinds_needed))

# 7) 요청에 없는 수치 가림 — 사례집 수치가 옮겨 오는 것 차단
spec = good_spec()
fixed, masked = rc.mask_unsupported_numbers(spec, REQ)
check(masked == [], "정답 스펙은 가리지 않음 (합계 5.2kg·카메라 4EA·1EA 허용)", str(masked))
spec.tables[0].rows.append(["로봇 이송", "45 초"])
spec.footnote += " | 사례: 생산성 30% 향상, 파레트 700kg"
fixed, masked = rc.mask_unsupported_numbers(spec, REQ)
check(masked == ["45초", "30%", "700kg"], "사례식 수치만 가림", str(masked))
check(fixed.tables[0].rows[-1] == ["로봇 이송", "(확인 필요)"], "가린 자리 표시")
check("210 초" in fixed.tables[0].rows[0][1], "요청 수치(210초)는 유지")
check(all(i.image.symbol == j.image.symbol for i, j in zip(spec.process_steps, fixed.process_steps)),
      "심볼 키 등 비표시 필드는 그대로")

# 8) 참고 사례 표현 유출 제거 — 12b 실측(2026-09-28): 약액 병 요청 + 2024-15 세라믹 검사 사례
#    → 제목 "세라믹 카트리지/보틀 자동화 검사 및 주입 공정"
leak = ConceptMapSpec.model_validate(
    json.loads((ROOT / "docs/concept_drawio/leak_spec_12b.json").read_text(encoding="utf-8")))
check("세라믹" in leak.title, "누출 재현 자료")
terms = rc.case_terms(["세라믹 제품 검사공정"], REQ)
check(terms == ["세라믹"], "사례 고유 단어만 (제품·검사공정 같은 일반어 제외)", str(terms))
fixed, removed = rc.strip_case_terms(leak, REQ, ["세라믹 제품 검사공정"])
check(removed == ["세라믹"] and "세라믹" not in rc.spec_text(fixed), "누출 단어 제거", str(removed))
check(fixed.title.startswith("카트리지/보틀"), "제목 자연스럽게 정리", fixed.title)
check(rc.strip_case_terms(leak, REQ, ["담금주 생산공정"])[1] == [], "안 쓰인 사례 단어는 그대로")
same = rc.strip_case_terms(good_spec(), REQ, ["한화 로봇 약액 주입 공정"])[1]
check(same == [], "요청에 있는 단어는 사례 제목에 있어도 유지", str(same))

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, d in FAIL:
    print("  FAIL", label, d)
sys.exit(1 if FAIL else 0)
