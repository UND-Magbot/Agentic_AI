"""meeting_transcriber._strip_repetition_hallucination 단위 검증.

STT 후처리 환각 클리너의 정상 케이스 / 회귀 보호.
faster-whisper 로딩이 불필요해 인프라 없이 빠르게 반복 검증할 수 있다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.meeting_transcriber import _strip_repetition_hallucination  # noqa: E402


def main() -> int:
    fails: list[str] = []

    def check(cond: bool, label: str) -> None:
        if not cond:
            fails.append(label)

    # ── (1) 문장 단위 반복 ──────────────────────────────────────────────
    s = (
        "오늘의 주인공은 누구일까요? "
        "오늘의 주인공은 누구일까요? "
        "오늘의 주인공은 누구일까요?"
    )
    c = _strip_repetition_hallucination(s)
    check(c.count("오늘의 주인공은") == 0,
          f"[1-1] 알려진 환각 phrase 통째로 제거: 실제={c!r}")

    # ── (2) 짧은 어구 반복(공백 분리) ──────────────────────────────────
    s = "예를 들어서 " * 15
    c = _strip_repetition_hallucination(s)
    check(c.count("예를 들어서") == 1,
          f"[2-1] '예를 들어서' 15회 → 1회: 실제={c.count('예를 들어서')}")

    # ── (3) 짧은 토큰 + 마침표 반복(새 패턴) ────────────────────────────
    s = "있습니다. " + "충분히. " * 13
    c = _strip_repetition_hallucination(s)
    check(c.count("충분히") == 1,
          f"[3-1] '충분히.' 13회 → 1회: 실제={c.count('충분히')}, c={c!r}")
    check("있습니다" in c, "[3-2] 앞 문장 보존")

    # ── (4) 짧은 토큰 + 쉼표 반복(새 패턴) ──────────────────────────────
    s = "참석자 명단: " + "위원님, " * 10
    c = _strip_repetition_hallucination(s)
    check(c.count("위원님") == 1, f"[4-1] '위원님,' 10회 → 1회: 실제={c!r}")

    # ── (5) 단일 글자 과도 반복(새 패턴) ────────────────────────────────
    s = "네네네네네네네네네네네네"
    c = _strip_repetition_hallucination(s)
    check(c.count("네") == 1, f"[5-1] '네' 12회 → 1회: 실제={c!r}")

    # ── (6) 숫자 나열 환각(새 패턴) ─────────────────────────────────────
    s = "회의가 시작되었습니다. 1, 2, 3, 4, 5, 6, 7, 8. 본격적으로 시작합니다."
    c = _strip_repetition_hallucination(s)
    check("1, 2, 3, 4" not in c and "5, 6, 7, 8" not in c,
          f"[6-1] 숫자 나열 환각 제거: 실제={c!r}")
    check("본격적으로 시작합니다" in c, "[6-2] 본문 보존")

    # ── (7) 무대지시 브래킷 제거(새 패턴) ───────────────────────────────
    s = "[(라멘을 풀고 있음)] 안녕하세요 [(엄청크)] 회의를 시작하겠습니다"
    c = _strip_repetition_hallucination(s)
    check("[(" not in c and ")]" not in c,
          f"[7-1] 무대지시 브래킷 제거: 실제={c!r}")
    check("안녕하세요" in c and "회의를 시작하겠습니다" in c,
          "[7-2] 본문 보존")

    # ── (8) 정상 한국어 false positive 회귀 검증 ───────────────────────
    safe_inputs = [
        "오늘 회의에 참석해 주셔서 감사합니다. 그럼 시작하겠습니다.",
        "맥봇 EOAT, 맥봇 이펙터, 맥봇 그리퍼 세 가지 라인업이 있습니다.",
        "1번 안건은 매출, 2번은 원가, 3번은 일정입니다.",
        "안녕하세요. 좋아요. 그래요.",
        "하노버 출장에서 시멘스, 보쉬, 후지키 3개 회사를 방문했습니다.",
        "DEEP ROBOTICS M20 / LITE3 / X30 데모를 진행했습니다.",
        "결재, 견적, 발주 프로세스를 점검합니다.",
    ]
    for s in safe_inputs:
        c = _strip_repetition_hallucination(s)
        # 공백 정규화 차이는 허용 — 의미가 보존되는지만 확인.
        norm_in = " ".join(s.split())
        norm_out = " ".join(c.split())
        if norm_in != norm_out:
            fails.append(
                f"[8] 정상 입력 변경됨(false positive): {s!r} → {c!r}"
            )

    # ── (9) 실제 STT 파일에 대한 통합 검증 ─────────────────────────────
    real = ROOT / "docs" / "archive" / "_transcript.txt"
    if real.exists():
        text = real.read_text(encoding="utf-8")
        before = len(text)
        cleaned = _strip_repetition_hallucination(text)
        removed = before - len(cleaned)
        # 알려진 환각 패턴이 모두 제거되었는지.
        check(cleaned.count("[(") == 0, "[9-1] 실제 파일: 무대지시 제거")
        # 짧은 토큰 + 마침표 반복 잔재가 없는지(같은 토큰 3회+ 연속).
        import re
        residue = re.findall(
            r"(?:[가-힣A-Za-z0-9]{1,7}[.,!?]\s*){3,}", cleaned
        )
        big_residue = [
            r for r in residue
            if len(set(re.findall(r"[가-힣A-Za-z0-9]+", r))) <= 2 and len(r) > 12
        ]
        check(not big_residue,
              f"[9-2] 짧은 토큰 반복 잔재 없음: {big_residue[:2]}")
        print(f"  실제 파일: {before} → {len(cleaned)} ({removed} chars 제거, "
              f"{removed/before*100:.1f}%)")

    if fails:
        print(f"\nFAIL ({len(fails)}건):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS - STT 환각 클리너 전 항목 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
