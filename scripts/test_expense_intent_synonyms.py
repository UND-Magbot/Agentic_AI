"""Expense 의도 감지 — 다양한 동의 표현이 모두 트리거되는지, 정의/조회 질문은 트리거 안 되는지 검증.

main.py 의 _detect_force_expense_tool 은 fastapi 의존성 때문에 직접 import 가 어려워,
같은 정규식을 격리 복사해 단위 테스트한다. main.py 의 패턴을 ast 로 읽어 정합성도 확인.
"""
from __future__ import annotations

import ast
import io
import re
import sys
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = ROOT / "backend" / "app" / "main.py"

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def expect(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL {name} — {detail}")


# ─── 정규식을 main.py 에서 그대로 읽어와 동일성 보장 ─────────────────
def load_regexes_from_main() -> tuple[re.Pattern, re.Pattern, re.Pattern, re.Pattern]:
    src = MAIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and t.id in (
                "_COMPOSE_INTENT_RE", "_EXPENSE_CONTEXT_RE",
                "_INFO_REQUEST_RE", "_SEND_INTENT_RE",
            ):
                if (
                    isinstance(node.value, ast.Call)
                    and node.value.args
                    and isinstance(node.value.args[0], ast.Constant)
                ):
                    found[t.id] = node.value.args[0].value
    required = (
        "_COMPOSE_INTENT_RE", "_EXPENSE_CONTEXT_RE",
        "_INFO_REQUEST_RE", "_SEND_INTENT_RE",
    )
    for k in required:
        if k not in found:
            raise RuntimeError(f"{k} not found in main.py")
    return (
        re.compile(found["_COMPOSE_INTENT_RE"], re.IGNORECASE),
        re.compile(found["_EXPENSE_CONTEXT_RE"], re.IGNORECASE),
        re.compile(found["_INFO_REQUEST_RE"], re.IGNORECASE),
        re.compile(found["_SEND_INTENT_RE"], re.IGNORECASE),
    )


COMPOSE_RE, CTX_RE, INFO_RE, SEND_RE = load_regexes_from_main()


def force(text: str) -> bool:
    """main.py _detect_force_expense_tool 와 동일 로직 (메시지 단건 단위)."""
    if not (COMPOSE_RE.search(text) and CTX_RE.search(text)):
        return False
    if INFO_RE.search(text):
        return False
    if SEND_RE.search(text):
        return False
    return True


# ─── A. 긍정 케이스 — 트리거되어야 ───────────────────────────────────
def test_positive_cases() -> None:
    print("\n[test_positive_cases] 다양한 동의 표현이 모두 트리거")
    triggers = [
        # === 익스펜스 + 명령형 동사 변형 ===
        "익스펜스 만들어줘",
        "익스펜스 작성해줘",
        "익스펜스 써줘",
        "익스펜스 해줘",
        "익스펜스 부탁해",
        "익스펜스 부탁드려요",
        "익스펜스 부탁드립니다",
        "익스펜스 부탁할게요",
        "익스펜스 처리해줘",
        "익스펜스 정리해줘",
        "익스펜스 출력해줘",
        "익스펜스 생성해줘",
        "익스펜스 양식 만들어",
        "익스펜스 양식 부탁",
        "익스펜스 마무리해줘",
        "익스펜스 완성해줘",
        "익스펜스 좀 적어줘",
        "익스펜스 뽑아줘",
        "익스펜스 보고서 작성",
        "익스펜스 필요해",
        "익스펜스 필요합니다",

        # === expense 영문 + 동사 ===
        "expense 만들어",
        "expense 양식 만들어줘",
        "expense 작성해줘",
        "expense 보고서 작성",
        "5월 expense 양식 만들어",
        "이번 달 expense 작성 부탁",
        "Expense 보고서 출력해줘",
        "expense 처리해줘",

        # === 법인카드/개인카드 + 동사 ===
        "법인카드 지출 정리해줘",
        "개인카드 지출 정리해줘",
        "법인카드/개인카드 지출 양식 만들어줘",
        "법인 카드 사용 내역 정리해줘",
        "개인 카드 영수증 정리해서 양식 만들어",

        # === 지출 / 경비 / 비용 + 동사 ===
        "이번 달 지출 내역 정리해줘",
        "월 지출 보고 작성해줘",
        "지출 양식 만들어줘",
        "지출 보고서 부탁드립니다",
        "경비 처리 부탁",
        "경비 보고 양식 만들어",
        "경비 정산 처리해줘",
        "경비 신청 양식",
        "비용 보고서 작성 부탁",
        "비용 처리 필요해",
        "비용 정산 만들어줘",

        # === 정산 / 출장 ===
        "월말 정산 부탁드려요",
        "월간 정산서 만들어줘",
        "출장 경비 보고 작성",
        "출장 보고서 만들어",
        "카드 사용 내역 정리해서 양식",

        # === 실제 v6/v7 시나리오 ===
        "법인카드 지출 X\n개인카드 지출\n해당없음, 2/7 토, 안전화 구매, 72000\n2026년 2월 배재병 익스펜스 보고 작성해줘",
        "법인카드 지출 X\n개인카드 지출\n...\n2026년 2월 배재병 익스펜스 보고 문서 작성해줘",
        "법인카드 지출 X\n개인카드 지출\n...\n익스펜스 양식 만들어주세요",
        "법인카드 지출 X\n개인카드 지출\n...\n익스펜스 부탁드려요",
    ]
    for t in triggers:
        expect(f"trigger:{t[:40]!r}", force(t),
               f"compose={bool(COMPOSE_RE.search(t))} ctx={bool(CTX_RE.search(t))}")


# ─── B. 부정 케이스 — 트리거되면 안 됨 ──────────────────────────────
def test_negative_cases() -> None:
    print("\n[test_negative_cases] 정의/조회/일반 질문은 트리거 안 됨")
    non_triggers = [
        # 정의/개념 질문
        "expense 가 뭐야?",
        "익스펜스가 뭐야",
        "익스펜스 어떻게 처리되나요?",   # 의문 + 수동
        "익스펜스 만드는 법 알려줘",     # 정보 요청 — 알려
        "expense 양식이 어떻게 생겼어요?", # 의문
        "법인카드 분실 시 어떻게 해야 하나요",
        "영수증 분실하면 어떻게 해?",
        "expense 보고는 언제 내야 해?",
        "경비 보고 마감일이 언제야?",
        "expense 작성 방법 설명해줘",    # 정보 요청 — 설명
        "expense 만들 때 주의사항 알려",  # 알려

        # 발송 의도 (expense 도구는 발송 안 함)
        "expense 발송해줘",
        "expense 메일 보내줘",
        "익스펜스 작성해서 메일로 보내",  # 발송 의도 우선

        # 일반 인사/대화
        "안녕하세요",
        "오늘 날씨 어때?",
        "고마워요",

        # expense 컨텍스트 전혀 없음
        "내일 회의 자료 만들어줘",
        "보고서 작성 부탁해",
        "이메일 양식 좀",
    ]
    for t in non_triggers:
        expect(f"no_trigger:{t[:40]!r}", not force(t),
               f"compose={bool(COMPOSE_RE.search(t))} ctx={bool(CTX_RE.search(t))} — should NOT trigger")


# ─── C. 경계 케이스 — 의도적 모호 ─────────────────────────────────
def test_borderline_cases() -> None:
    """모호한 케이스는 트리거하는 쪽이 안전 (false negative 비용 > false positive 비용).
    서버측 검증에서 lines 비면 retryable 에러로 폐기 가능.
    """
    print("\n[test_borderline_cases] 모호하지만 트리거 OK (서버에서 lines 검증)")
    borderline_should_trigger = [
        "법인카드 지출 정리",        # 동사 없지만 '정리' 자체가 명사+동사 어간 — 트리거 OK
        "영수증 정리 부탁",            # 영수증+부탁
        "expense 정리",                # 짧지만 의도 분명
    ]
    for t in borderline_should_trigger:
        expect(f"borderline:{t!r}", force(t),
               f"compose={bool(COMPOSE_RE.search(t))} ctx={bool(CTX_RE.search(t))}")


# ─── D. anti-trigger 분기 단독 검증 ─────────────────────────────────
def test_anti_trigger_branches() -> None:
    """compose+ctx 매치되지만 anti-trigger(info request / send intent) 가 우선."""
    print("\n[test_anti_trigger_branches]")
    info_only = "익스펜스 양식 어떻게 만들어요?"  # compose=만들 ctx=익스펜스 info=어떻게
    expect("info.compose_matches", bool(COMPOSE_RE.search(info_only)))
    expect("info.ctx_matches", bool(CTX_RE.search(info_only)))
    expect("info.info_matches", bool(INFO_RE.search(info_only)))
    expect("info.NOT_forced", not force(info_only))

    send_only = "expense 작성해서 발송해줘"  # compose=작성 ctx=expense send=발송
    expect("send.compose_matches", bool(COMPOSE_RE.search(send_only)))
    expect("send.ctx_matches", bool(CTX_RE.search(send_only)))
    expect("send.send_matches", bool(SEND_RE.search(send_only)))
    expect("send.NOT_forced", not force(send_only))


if __name__ == "__main__":
    cases = [
        test_positive_cases,
        test_negative_cases,
        test_borderline_cases,
        test_anti_trigger_branches,
    ]
    for fn in cases:
        try:
            fn()
        except Exception as e:
            FAIL.append((fn.__name__, f"{e}"))
            print(f"  FAIL {fn.__name__} — {e}")
            traceback.print_exc()

    print(f"\n==== {len(PASS)} pass / {len(FAIL)} fail ====")
    if FAIL:
        for name, detail in FAIL:
            print(f"  [FAIL] {name}: {detail}")
        sys.exit(1)
    print("ALL OK")
