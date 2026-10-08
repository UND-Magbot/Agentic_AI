"""meeting_summarizer 순수 함수 단위 검증 — vLLM/인프라 불필요.

split_transcript(청크 분할) 와 parse_sections(LLM 출력 파싱) 의 경계 동작을 검증한다.
실제 vLLM 요약은 E2E 테스트(test_meeting_e2e)에서 확인한다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.meeting_summarizer import (  # noqa: E402
    _has_placeholder_leak,
    _needs_retry,
    _normalize_markers,
    parse_sections,
    split_transcript,
)


def main() -> int:
    fails: list[str] = []

    def check(cond: bool, label: str) -> None:
        if not cond:
            fails.append(label)

    # ── split_transcript ──────────────────────────────────────────────
    check(split_transcript("") == [], "빈 문자열 → []")
    check(split_transcript("   ") == [], "공백만 → []")

    short = "짧은 회의 내용입니다."
    check(split_transcript(short) == [short], "짧은 텍스트 → 단일 청크")

    # 긴 텍스트 — 문장 경계로 분할, 각 청크 max_chars 이하.
    sentence = "이것은 회의에서 논의된 안건입니다. "  # 20자
    long_text = sentence * 600  # ~12000자
    chunks = split_transcript(long_text, max_chars=3000)
    check(len(chunks) >= 3, f"긴 텍스트 → 3+ 청크 (실제 {len(chunks)})")
    check(all(len(c) <= 3000 for c in chunks), "모든 청크 max_chars 이하")
    # 내용 보존 — 공백 제거 후 원문과 동일.
    rejoined = "".join(chunks).replace(" ", "")
    check(rejoined == long_text.replace(" ", ""), "분할 후 내용 보존")

    # 줄바꿈 경계 분할.
    nl_text = ("회의 발언 한 줄입니다.\n" * 300)
    nl_chunks = split_transcript(nl_text, max_chars=2000)
    check(all(len(c) <= 2000 for c in nl_chunks), "줄바꿈 텍스트도 max_chars 이하")

    # ── parse_sections ────────────────────────────────────────────────
    well_formed = (
        "[회의 주요 내용]\n"
        "1. 하노버 출장 결과\n- 신규 파트너 확보\n\n"
        "[지시사항]\n"
        "1. B2G 전략 추진\n- 협력 기관 발굴"
    )
    s = parse_sections(well_formed)
    check("하노버 출장 결과" in s.main_content, "정상 형식 — 주요 내용 추출")
    check("B2G 전략 추진" in s.directives, "정상 형식 — 지시사항 추출")
    check("[지시사항]" not in s.main_content, "주요 내용에 마커 잔재 없음")
    check("[회의 주요 내용]" not in s.directives, "지시사항에 마커 잔재 없음")

    # 마커 없음 → 전체가 main_content, directives 는 안내 문구.
    no_marker = "회의에서 여러 안건을 논의했습니다."
    s2 = parse_sections(no_marker)
    check(s2.main_content == no_marker, "마커 없음 — 전체가 주요 내용")
    check("확인되지 않" in s2.directives, "마커 없음 — 지시사항 안내 문구")

    # 지시사항 마커만 존재.
    only_d = "회의 내용 요약\n[지시사항]\n1. 후속 조치"
    s3 = parse_sections(only_d)
    check(s3.main_content == "회의 내용 요약", "지시사항 마커만 — 앞부분이 주요 내용")
    check("후속 조치" in s3.directives, "지시사항 마커만 — 뒷부분이 지시사항")

    # 빈 입력 → 두 필드 모두 안내 문구(예외 없이).
    s4 = parse_sections("")
    check(bool(s4.main_content) and bool(s4.directives), "빈 입력 — 안내 문구로 채움")

    # '지시사항 없음'에 번호/불릿이 붙은 경우 → 줄 제거 후 안내 문구로 대체.
    s5 = parse_sections(
        "[회의 주요 내용]\n1. 안건 논의\n[지시사항]\n1. (별도 지시사항 없음)"
    )
    check("확인되지 않" in s5.directives, f"지시사항 없음 정규화 (실제 {s5.directives!r})")

    # 실제 지시 항목 + 끝에 '별도 지시사항 없음'이 덧붙은 경우 → 그 줄만 제거.
    s6 = parse_sections(
        "[회의 주요 내용]\n1. 안건\n[지시사항]\n1. 보고서 제출\n2. 일정 확정\n\n(별도 지시사항 없음)"
    )
    check("보고서 제출" in s6.directives and "일정 확정" in s6.directives,
          "실제 지시 항목 보존")
    check("별도 지시사항" not in s6.directives,
          f"덧붙은 '없음' 줄 제거 (실제 {s6.directives!r})")

    # ── 마커 변형 정규화 (새 기능) ─────────────────────────────────────
    # markdown 으로 감싼 마커 / 콜론 추가 / 공백 변형 모두 표준 마커로 매핑.
    variant1 = (
        "**[회의 주요 내용]**\n1. 안건\n"
        "## [지시사항]\n1. 후속 조치"
    )
    sv1 = parse_sections(variant1)
    check("안건" in sv1.main_content, f"[변형1] **[회의 주요 내용]** 인식: {sv1.main_content!r}")
    check("후속 조치" in sv1.directives, f"[변형1] ## [지시사항] 인식: {sv1.directives!r}")

    variant2 = (
        "[회의 주요 내용]:\n1. A\n"
        "[지시 사항]：\n1. B"  # 한자 콜론 + 공백 변형
    )
    sv2 = parse_sections(variant2)
    check("A" in sv2.main_content, f"[변형2] 콜론 변형 인식 (main): {sv2.main_content!r}")
    check("B" in sv2.directives, f"[변형2] 한자 콜론/공백 변형 (directives): {sv2.directives!r}")

    # _normalize_markers 단독 검증.
    norm = _normalize_markers("**[회의 주요 내용]**\n내용\n[지시 사항]:")
    check("[회의 주요 내용]" in norm and "[지시사항]" in norm,
          f"_normalize_markers: {norm!r}")

    # ── markdown 추가 변형 처리 ────────────────────────────────────────
    md_more = (
        "[회의 주요 내용]\n1. __강조 제목__\n- `코드 같은 항목`\n\n\n\n[지시사항]\n1. *기울임*"
    )
    sm = parse_sections(md_more)
    check("__" not in sm.main_content and "`" not in sm.main_content,
          f"__bold__ / `code` 제거: {sm.main_content!r}")
    check("강조 제목" in sm.main_content, "내용은 보존")
    check("\n\n\n" not in sm.main_content, "다중 공백줄 정규화")
    check("기울임" in sm.directives and "*" not in sm.directives,
          f"*기울임* 제거: {sm.directives!r}")

    # ── 자리표시 leak 탐지기 ──────────────────────────────────────────
    leak_yes = "[회의 주요 내용]\n1. 주제\n- 세부 내용\n2. 주제\n[지시사항]\n1. 지시 제목"
    check(_has_placeholder_leak(leak_yes), "[leak] '주제' / '세부 내용' / '지시 제목' 탐지")
    leak_no = "[회의 주요 내용]\n1. 하노버 출장 결과\n- 시멘스 미팅 진행\n2. 신규 파트너 확보"
    check(not _has_placeholder_leak(leak_no), "[leak] 정상 본문 false-positive 없음")
    # '주제' 단어가 문장 중간에 등장하는 정상 케이스 — leak 아님.
    leak_no2 = "[회의 주요 내용]\n1. 본 회의 주제는 글로벌 확장 전략입니다.\n- 독일·미국 시장 우선"
    check(not _has_placeholder_leak(leak_no2), "[leak] 본문 중 '주제' false-positive 없음")

    # ── _needs_retry 판정 ─────────────────────────────────────────────
    check(_needs_retry("", 2000), "[retry] 빈 출력 → 재시도")
    check(_needs_retry("짧은 출력", 2000), "[retry] 짧은 출력 + 긴 전사문 → 재시도")
    check(not _needs_retry("짧은 출력", 100),
          "[retry] 짧은 출력이지만 전사문도 짧음 → 재시도 안 함")
    check(_needs_retry("a" * 500 + "1. 주제\n- 세부 내용", 5000),
          "[retry] leak 포함 → 재시도")
    # 정상 회의록은 명사형 종결(필요/예정/확보…) 위주 — 평서형 종결('~입니다')이 아니다.
    # (이전 픽스처는 '정상 내용입니다'×30 의 평서형이라 _declarative_ratio 가드에 정당하게
    #  걸렸음. 가드는 의도된 동작이므로 픽스처를 실제 회의록 형식으로 정정.)
    good_summary = (
        "[회의 주요 내용]\n"
        "1. 신제품 출시 준비\n- 3월 양산 목표 확정\n- 품질 검증 우선 진행 필요\n- 협력사 부품 단가 재협상\n\n"
        "2. 인력 운영 계획\n- 6월 2명 합류 예정\n- 추가 충원 검토 중\n- 신규 입사자 온보딩 자료 정비\n\n"
        "3. 조직 문화 개선\n- 생성형 AI 업무 활용 확대\n- 부서 간 협업 강화 필요\n- 주간 공유회 정례화\n"
        "[지시사항]\n1. 금주 예정 사항\n- 부서별 발전 계획 수립\n- 대표님 면담 일정 조율\n- 다음 회의 안건 사전 취합"
    )
    check(not _needs_retry(good_summary, 5000),
          "[retry] 정상 출력(명사형 종결) → 재시도 안 함")

    if fails:
        print(f"FAIL ({len(fails)}건):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS - split_transcript / parse_sections / 변형마커 / leak 탐지 / retry 판정 전 항목 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
