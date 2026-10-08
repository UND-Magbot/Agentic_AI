"""서버측 expense 파서 (LLM 우회) 종합 검증.

핵심 시나리오:
- v10 실제 입력 → 정확히 3건 라인 파싱 + 메타 추출
- 변형 케이스(요일 포함/제외, M/D vs YYYY-MM-DD, 통화기호, alias 등)
- 거부 케이스(파싱 실패 → None → LLM fallback)
- 시스템 프롬프트 메타 추출
"""
from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.expense_parser import (
    parse_expense_message,
    extract_today_from_system,
    extract_user_name_from_system,
    extract_attachment_ids_from_system,
)

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def expect(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL {name} — {detail}")


# ─── 1. v10 실제 사용자 입력 ───────────────────────────────────
def test_v10_real_user_input() -> None:
    print("\n[test_v10_real_user_input]")
    text = (
        "법인카드 지출 X\n"
        "\n"
        "개인카드 지출\n"
        "해당없음, 2/7 토, 현대 글로비스 공장 출입 위한 안전화 구매, 72000, 워크업 대구 반야월점\n"
        "여비교통비, 2/9 월, 평택지제역 → 동대구역 SRT 승차권 구매, 29500, 주식회사 에스알\n"
        "여비교통비, 2/10 화, 숙소 근처 → 평택지제역 콜택시 이용, 30000, 미래 대리우전\n"
        "\n"
        "2026년 2월 배재병 익스펜스 보고 문서 작성해줘"
    )
    args = parse_expense_message(
        text, default_year=2026, default_month=2, default_author="",
        attachment_ids=[17, 18, 19],
    )
    expect("v10.not_none", args is not None)
    if args is None:
        return
    expect("v10.author", args["author"] == "배재병", f"got {args['author']!r}")
    expect("v10.year", args["year"] == 2026)
    expect("v10.month", args["month"] == 2)
    expect("v10.lines_3", len(args["lines"]) == 3, f"got {len(args['lines'])}")
    expect("v10.receipts_3", args["receipt_attachment_ids"] == [17, 18, 19])

    # 라인 검증
    ln0 = args["lines"][0]
    expect("v10.line0_source", ln0["source"] == "개인카드")
    expect("v10.line0_category", ln0["category"] == "해당없음")
    expect("v10.line0_date", ln0["date"] == "2026-02-07", f"got {ln0['date']}")
    expect("v10.line0_amount", ln0["amount"] == 72000.0)
    expect("v10.line0_vendor", "워크업" in ln0["vendor"])
    expect("v10.line0_purpose_full",
           "안전화" in ln0["purpose"] and "글로비스" in ln0["purpose"],
           f"got {ln0['purpose']!r}")

    ln1 = args["lines"][1]
    expect("v10.line1_date", ln1["date"] == "2026-02-09")
    expect("v10.line1_amount", ln1["amount"] == 29500.0)
    expect("v10.line1_purpose_has_arrow",
           "→" in ln1["purpose"] or "->" in ln1["purpose"])

    ln2 = args["lines"][2]
    expect("v10.line2_date", ln2["date"] == "2026-02-10")
    expect("v10.line2_amount", ln2["amount"] == 30000.0)


# ─── 2. 법인카드 + 개인카드 혼합 ───────────────────────────────
def test_mixed_corp_personal() -> None:
    print("\n[test_mixed_corp_personal]")
    text = (
        "법인카드 지출\n"
        "접대비, 5/3, 거래처 점심, 80000, 한식당\n"
        "\n"
        "개인카드 지출\n"
        "여비교통비, 5/4, 출장 택시, 15000, 카카오모빌리티\n"
        "\n"
        "5월 익스펜스 김재무 작성"
    )
    args = parse_expense_message(
        text, default_year=2026, default_month=5, default_author="김재무",
    )
    expect("mixed.not_none", args is not None)
    if args is None:
        return
    expect("mixed.author", args["author"] == "김재무")
    expect("mixed.lines_2", len(args["lines"]) == 2)
    expect("mixed.corp_first", args["lines"][0]["source"] == "법인카드")
    expect("mixed.corp_user_filled", args["lines"][0]["user"] == "김재무")
    expect("mixed.personal_second", args["lines"][1]["source"] == "개인카드")
    expect("mixed.personal_user_empty", args["lines"][1]["user"] == "")


# ─── 3. amount 변형 (콤마, 통화기호, 원) ─────────────────────
def test_amount_variants() -> None:
    print("\n[test_amount_variants]")
    cases = [
        ("여비교통비, 2/9, 택시, 29500, 가맹점", 29500.0),
        ("여비교통비, 2/9, 택시, 29,500, 가맹점", 29500.0),
        ("여비교통비, 2/9, 택시, 29500원, 가맹점", 29500.0),
        ("여비교통비, 2/9, 택시, 29,500원, 가맹점", 29500.0),
        ("여비교통비, 2/9, 택시, ₩29500, 가맹점", 29500.0),
    ]
    for raw, want_amount in cases:
        text = f"개인카드 지출\n{raw}\n2026년 2월 배재병 익스펜스 작성"
        args = parse_expense_message(text, default_year=2026, default_month=2,
                                     default_author="배재병")
        if args and len(args["lines"]) == 1:
            got = args["lines"][0]["amount"]
            expect(f"amt:{raw[:40]}", got == want_amount, f"got {got}")
        else:
            expect(f"amt:{raw[:40]}", False, f"args={args}")


# ─── 4. category alias 매핑 ──────────────────────────────────
def test_category_aliases() -> None:
    print("\n[test_category_aliases]")
    aliases = [
        ("교통비", "여비교통비"),
        ("택시", "여비교통비"),
        ("SRT", "여비교통비"),
        ("회식", "복리후생비"),
        ("점심", "복리후생비"),
        ("접대", "접대비"),
        ("사무용품", "소모품비"),
        ("주유", "차량유지비"),
        ("도서", "도서인쇄비"),
        ("수수료", "지급수수료"),
        ("잡비", "해당없음"),
        ("기타", "해당없음"),
    ]
    for raw_cat, want in aliases:
        text = f"개인카드 지출\n{raw_cat}, 2/9, 사유, 10000, 가맹점\n배재병 익스펜스"
        args = parse_expense_message(text, default_year=2026, default_month=2,
                                     default_author="배재병")
        if args and len(args["lines"]) == 1:
            got = args["lines"][0]["category"]
            expect(f"alias:{raw_cat}->{want}", got == want, f"got {got}")
        else:
            expect(f"alias:{raw_cat}", False, "no lines")


# ─── 5. 날짜 변형 ──────────────────────────────────────────────
def test_date_variants() -> None:
    print("\n[test_date_variants]")
    cases = [
        ("2/9", "2026-02-09"),
        ("2/9 월", "2026-02-09"),
        ("2/9 토", "2026-02-09"),
        ("02/09", "2026-02-09"),
        ("2026-02-09", "2026-02-09"),
        ("2026-2-9", "2026-02-09"),
        ("2026/02/09", "2026-02-09"),
        ("2026.02.09", "2026-02-09"),
    ]
    for raw_date, want in cases:
        text = f"개인카드 지출\n여비교통비, {raw_date}, 택시, 10000, 가맹점\n배재병 익스펜스"
        args = parse_expense_message(text, default_year=2026, default_month=2,
                                     default_author="배재병")
        if args and len(args["lines"]) == 1:
            got = args["lines"][0]["date"]
            expect(f"date:{raw_date!r}->{want}", got == want, f"got {got}")


# ─── 6. 빈 섹션 마커 (X / 없음 / -) ─────────────────────────
def test_empty_section_markers() -> None:
    print("\n[test_empty_section_markers]")
    markers = ["X", "x", "없음", "-", "없습니다"]
    for marker in markers:
        text = (
            f"법인카드 지출 {marker}\n"
            "개인카드 지출\n"
            "여비교통비, 2/9, 택시, 10000, 가맹점\n"
            "배재병 익스펜스"
        )
        args = parse_expense_message(text, default_year=2026, default_month=2,
                                     default_author="배재병")
        if args:
            corp_count = sum(1 for l in args["lines"] if l["source"] == "법인카드")
            expect(f"empty:{marker!r}", corp_count == 0,
                   f"corp_count={corp_count}, lines={args['lines']}")
        else:
            expect(f"empty:{marker!r}", False, "args=None")


# ─── 7. purpose 안에 콤마 — 5필드 초과 케이스 ─────────────
def test_purpose_with_commas() -> None:
    print("\n[test_purpose_with_commas]")
    text = (
        "개인카드 지출\n"
        "여비교통비, 2/9, 평택→동대구, SRT, 일반석, 29500, 주식회사 에스알\n"
        "배재병 익스펜스"
    )
    args = parse_expense_message(text, default_year=2026, default_month=2,
                                 default_author="배재병")
    expect("purpose_commas.parsed", args is not None and len(args["lines"]) == 1)
    if args and args["lines"]:
        ln = args["lines"][0]
        expect("purpose_commas.amount", ln["amount"] == 29500.0)
        expect("purpose_commas.vendor", "에스알" in ln["vendor"])
        # 중간 부분이 purpose 에 합쳐져야
        expect("purpose_commas.purpose_merged",
               "평택" in ln["purpose"] and "SRT" in ln["purpose"]
               and "일반석" in ln["purpose"],
               f"got {ln['purpose']!r}")


# ─── 8. 거부 케이스 — None 반환 ────────────────────────────
def test_rejection_cases() -> None:
    print("\n[test_rejection_cases]")
    # 라인 없음 — 자연어만
    text1 = "안녕하세요. expense 가 뭐죠?"
    args1 = parse_expense_message(text1, default_year=2026, default_month=2,
                                  default_author="배재병")
    expect("reject.no_lines", args1 is None, f"got {args1}")

    # 잘못된 카테고리
    text2 = "개인카드 지출\nXYZUNKNOWNCAT, 2/9, 사유, 10000, 가맹점\n배재병 익스펜스"
    args2 = parse_expense_message(text2, default_year=2026, default_month=2,
                                  default_author="배재병")
    expect("reject.unknown_category",
           args2 is None or len(args2["lines"]) == 0,
           f"got {args2}")

    # 필드 부족 (4개만)
    text3 = "개인카드 지출\n여비교통비, 2/9, 택시, 10000\n배재병 익스펜스"
    args3 = parse_expense_message(text3, default_year=2026, default_month=2,
                                  default_author="배재병")
    expect("reject.too_few_fields",
           args3 is None or len(args3["lines"]) == 0,
           f"got {args3}")

    # 작성자 없음
    text4 = "개인카드 지출\n여비교통비, 2/9, 택시, 10000, 가맹점"
    args4 = parse_expense_message(text4, default_year=2026, default_month=2,
                                  default_author="")
    expect("reject.no_author", args4 is None, f"got {args4}")


# ─── 9. 시스템 프롬프트 메타 추출 ─────────────────────────
def test_system_prompt_extractors() -> None:
    print("\n[test_system_prompt_extractors]")
    sys_prompt = """
## 현재 요청 컨텍스트
- 현재 날짜: 2026-05-15 (YYYY-MM-DD).
  사용자가 '2/7', '5/8' 같은 월/일만 적으면 위 연도(2026)를 그대로 채워 정규화한다.
- 현재 사용자(작성자 후보): 배재병.
  도구 호출 시 author/작성자 인자에 사용. 다른 이름을 추론하지 말 것.
- 첨부된 파일(업로드 순, 총 3개):
  1) id=17 (expense_a1.jpg.png, image/png)
  2) id=18 (expense_a2.jpg.png, image/png)
  3) id=19 (expense_a3.jpg.png, image/png)
- 도구가 attachment_ids 또는 receipt_attachment_ids 인자를 받으면 위 3개 ID 전부를 빠짐없이 위 순서 그대로 전달: [17, 18, 19].
"""
    today = extract_today_from_system(sys_prompt)
    expect("sys.today", today == (2026, 5, 15), f"got {today}")

    user = extract_user_name_from_system(sys_prompt)
    expect("sys.user", user == "배재병", f"got {user!r}")

    ids = extract_attachment_ids_from_system(sys_prompt)
    expect("sys.ids", ids == [17, 18, 19], f"got {ids}")

    # 빈 프롬프트
    expect("sys.empty_today", extract_today_from_system("") is None)
    expect("sys.empty_user", extract_user_name_from_system("") is None)
    expect("sys.empty_ids", extract_attachment_ids_from_system("") == [])


# ─── 10. End-to-end: 전체 흐름 (v10 입력 + 시스템 프롬프트 → args) ──
def test_end_to_end_v10() -> None:
    print("\n[test_end_to_end_v10]")
    sys_prompt = (
        "## 현재 요청 컨텍스트\n"
        "- 현재 날짜: 2026-05-15 (YYYY-MM-DD).\n"
        "- 현재 사용자(작성자 후보): 배재병.\n"
        "  도구 호출 시 author/작성자 인자에 사용.\n"
        "- 도구가 receipt_attachment_ids 인자를 받으면 위 3개 ID 전부를 빠짐없이 위 순서 그대로 전달: [17, 18, 19]."
    )
    user_text = (
        "법인카드 지출 X\n\n"
        "개인카드 지출\n"
        "해당없음, 2/7 토, 안전화 구매, 72000, 워크업 대구 반야월점\n"
        "여비교통비, 2/9 월, 평택지제역 → 동대구역 SRT, 29500, 주식회사 에스알\n"
        "여비교통비, 2/10 화, 숙소 → 평택지제역 콜택시, 30000, 미래 대리우전\n\n"
        "2026년 2월 배재병 익스펜스 보고 문서 작성해줘"
    )

    today = extract_today_from_system(sys_prompt)
    user = extract_user_name_from_system(sys_prompt)
    ids = extract_attachment_ids_from_system(sys_prompt)
    args = parse_expense_message(
        user_text,
        default_year=today[0] if today else 2026,
        default_month=today[1] if today else 2,
        default_author=user or "",
        attachment_ids=ids,
    )
    expect("e2e.parsed", args is not None)
    if args:
        expect("e2e.author", args["author"] == "배재병")
        expect("e2e.year_from_msg", args["year"] == 2026)  # 메시지의 "2026년 2월" 우선
        expect("e2e.month_from_msg", args["month"] == 2)
        expect("e2e.lines", len(args["lines"]) == 3)
        expect("e2e.receipts", args["receipt_attachment_ids"] == [17, 18, 19])
        # 모든 source = 개인카드 (법인카드 X 처리됨)
        expect("e2e.all_personal",
               all(l["source"] == "개인카드" for l in args["lines"]))


# ─── 11. v6 같은 단순 입력 + 다양한 동의어 트리거 ───────────────
def test_user_phrasing_variants() -> None:
    """사용자가 '작성해줘', '만들어줘', '써줘' 등 다양한 표현 — 모두 동일 파싱."""
    print("\n[test_user_phrasing_variants]")
    phrasings = [
        "배재병 익스펜스 작성해줘",
        "배재병 익스펜스 만들어줘",
        "배재병 익스펜스 써줘",
        "배재병 expense report 작성",
        "배재병 지출 보고서 작성해줘",
    ]
    base_lines = (
        "개인카드 지출\n"
        "여비교통비, 2/9, 택시, 10000, 가맹점\n"
    )
    for phrase in phrasings:
        text = base_lines + "\n" + phrase
        args = parse_expense_message(text, default_year=2026, default_month=2,
                                     default_author="배재병")
        expect(f"phrasing:{phrase}", args is not None and len(args["lines"]) == 1,
               f"got {args}")


# ─── 12. 슬래시 구분자 5필드 (부서 컬럼 없음) ────────────────────
def test_slash_separator_5field() -> None:
    """' / ' 구분자, 부서 컬럼 없는 5필드."""
    print("\n[test_slash_separator_5field]")
    text = (
        "개인카드 지출\n"
        "여비교통비 / 2/9 / 평택 출장 / 29500 / 주식회사 에스알\n"
        "배재병 익스펜스 작성"
    )
    args = parse_expense_message(
        text, default_year=2026, default_month=2, default_author="배재병",
    )
    expect("slash5.parsed", args is not None and len(args["lines"]) == 1, f"got {args}")
    if args and args["lines"]:
        ln = args["lines"][0]
        expect("slash5.category", ln["category"] == "여비교통비")
        expect("slash5.date", ln["date"] == "2026-02-09", f"got {ln['date']}")
        expect("slash5.amount", ln["amount"] == 29500.0)
        expect("slash5.vendor", ln["vendor"] == "주식회사 에스알", f"got {ln['vendor']!r}")
        expect("slash5.purpose", ln["purpose"] == "평택 출장", f"got {ln['purpose']!r}")


# ─── 13. 사용자 원본: 슬래시 + 6필드(부서) + 대괄호헤더 ─────────────
def test_slash_with_department_and_bracket_header() -> None:
    """사용자가 실제로 보낸 원본 형식 그대로 — fast-path 가 한 번에 처리해야 함.

    회귀 방지 대상:
    - LLM fallback 으로 떨어져 line[2,4,5,6] 에서 date/vendor 누락 (expense_error_v11)
    """
    print("\n[test_slash_with_department_and_bracket_header]")
    text = (
        "[ 법인카드 지출 내역 - Total 333,050원 ]\n"
        "복리후생비 / 2/11 수 / 간식비 / 19,800 / 탐앤탐스 / 선행개발팀\n"
        "복리후생비 / 2/11 수 / 점심식대(출장) / 45,000 / 왕두꺼비부대찌개 / 선행개발팀\n"
        "소모품비 / 2/11 수 / 삼성웰스토리 RM 설치 용품 구매 / 18,800 / GS / 선행개발팀\n"
        "여비교통비 / 2/11 수 / 주차비 / 15,500 / 분당M타워 / 선행개발팀\n"
        "복리후생비 / 2/11 수 / 저녁식대(출장) / 144,000 / 무지막회 / 선행개발팀\n"
        "복리후생비 / 2/11 수 / 삼성웰스토리 RM 설치 용품 및 간식 구매 / 44,750 / 7ELEVEN / 선행개발팀\n"
        "복리후생비 / 2/12 목 / 간식비 / 31,700 / 투썸 / 선행개발팀\n"
        "여비교통비 / 2/12 목 / 주차비 / 9,500 / 분당M타워 / 선행개발팀\n"
        "복리후생비 / 2/12 목 / 점심식대(출장) / 114,000 / 도성루 / 선행개발팀\n"
        "\n"
        "[ 개인카드 지출 내역 - Total 691,100원 ]\n"
        "\n"
        "여비교통비 / 2/3 화 / 출장(유엔디, 김천구미역) / 5,300 / 가스충전소\n"
        "여비교통비 / 2/3 화 / 출장교통비 / 4,200 / 서울교통\n"
        "여비교통비 / 2/4 수 / 출장교통비 / 4,200 / 서울교통\n"
        "여비교통비 / 2/4 수 / 출장교통비(주차비) / 25,000 / 김천구미역 주차장\n"
        "여비교통비 / 2/4 수 / 출장(김천구미역, 유엔디) / 5,300 / 가스충전소\n"
        "복리후생비 / 2/6 금 / 연구소 야근 식대 / 151,000 / 배달의민족\n"
        "복리후생비 / 2/24 화 / 연구소 야근 식대 / 91,600 / 배달의민족\n"
        "복리후생비 / 2/25 수 / 연구소 야근 식대 / 150,900 / 배달의민족\n"
        "복리후생비 / 2/26 목 / 야근 식대 / 9,000 / GS\n"
        "복리후생비 / 2/27 금 / 출장 점심 식대 / 16,400 / 버거킹\n"
        "여비교통비 / 2/27 금 / 출장(봉곡도서관, 선산도서관, 복귀) / 9,300 / 버거킹\n"
        "복리후생비 / 2/27 금 / 야근식대 / 9,800 / GS\n"
        "여비교통비 / 2/28 토 / 출장교통비(SRT) / 35,100 / SRT\n"
        "여비교통비 / 2/28 토 / 숙박비(서울) / 85,000 / 여기어때\n"
        "여비교통비 / 2/28 토 / 숙박비(성남) / 89,000 / 여기어때\n"
        "\n"
        "2026년 2월 백동주 expense 보고서 작성해줘"
    )
    args = parse_expense_message(
        text, default_year=2026, default_month=2, default_author="백동주",
    )
    expect("slash6.parsed", args is not None, f"got {args}")
    if args is None:
        return

    expect("slash6.author", args["author"] == "백동주", f"got {args['author']!r}")
    expect("slash6.year", args["year"] == 2026)
    expect("slash6.month", args["month"] == 2)

    corp = [l for l in args["lines"] if l["source"] == "법인카드"]
    pers = [l for l in args["lines"] if l["source"] == "개인카드"]
    expect("slash6.corp_count_9", len(corp) == 9, f"got {len(corp)}")
    expect("slash6.personal_count_15", len(pers) == 15, f"got {len(pers)}")

    # 법인카드 첫 라인 — 부서 컬럼 폐기, user(법인) 자동 채움 검증.
    if corp:
        ln0 = corp[0]
        expect("slash6.corp0_category", ln0["category"] == "복리후생비")
        expect("slash6.corp0_date", ln0["date"] == "2026-02-11", f"got {ln0['date']}")
        expect("slash6.corp0_amount", ln0["amount"] == 19800.0)
        expect("slash6.corp0_vendor", ln0["vendor"] == "탐앤탐스", f"got {ln0['vendor']!r}")
        expect("slash6.corp0_purpose", ln0["purpose"] == "간식비", f"got {ln0['purpose']!r}")
        expect("slash6.corp0_user_author", ln0["user"] == "백동주")

    # purpose 가 긴 6번째 라인 (7ELEVEN) — 이전 LLM fallback 에서 자주 누락된 케이스.
    if len(corp) >= 6:
        ln5 = corp[5]
        expect("slash6.corp5_vendor", ln5["vendor"] == "7ELEVEN", f"got {ln5['vendor']!r}")
        expect("slash6.corp5_amount", ln5["amount"] == 44750.0)
        expect("slash6.corp5_purpose_has_both",
               "삼성웰스토리" in ln5["purpose"] and "간식 구매" in ln5["purpose"],
               f"got {ln5['purpose']!r}")

    # 개인카드 — purpose 안에 콤마가 있는 케이스(콤마 split 영향).
    if pers:
        ln_p0 = pers[0]
        expect("slash6.pers0_vendor", ln_p0["vendor"] == "가스충전소",
               f"got {ln_p0['vendor']!r}")
        expect("slash6.pers0_amount", ln_p0["amount"] == 5300.0)
        expect("slash6.pers0_purpose_has_both",
               "유엔디" in ln_p0["purpose"] and "김천구미역" in ln_p0["purpose"],
               f"got {ln_p0['purpose']!r}")

    # 합계 — 사용자 메모의 'Total 333,050원' 은 오기이고 실제 443,050원이 맞는지 확인.
    corp_total = sum(l["amount"] for l in corp)
    expect("slash6.corp_total_443050", corp_total == 443050.0, f"got {corp_total}")
    pers_total = sum(l["amount"] for l in pers)
    expect("slash6.pers_total_691100", pers_total == 691100.0, f"got {pers_total}")


# ─── 14. 작성자 이름이 trigger 뒤에 오는 케이스 ───────────────────
def test_author_name_after_trigger() -> None:
    """사용자가 'expense ... 이름 백동주' 처럼 trigger 뒤에 이름을 적는 케이스."""
    print("\n[test_author_name_after_trigger]")
    cases = [
        # 사용자 v11 원본 phrasing
        ("expense 보고서 좀 작성해줘 2026년 2월 자 이름 백동주", "백동주"),
        ("expense 작성해줘. 이름: 박지훈", "박지훈"),
        ("이번 달 expense, 작성자 김재무", "김재무"),
        ("expense 작성 by 이채진", "이채진"),
        ("지출 보고서. 담당자 - 홍길동", "홍길동"),
        # 기존 phrasing 도 회귀 확인
        ("2026년 2월 백동주 expense 보고서 작성해줘", "백동주"),
        ("배재병 익스펜스 작성해줘", "배재병"),
    ]
    for text, want in cases:
        body = "개인카드 지출\n여비교통비, 2/9, 택시, 10000, 가맹점\n" + text
        args = parse_expense_message(
            body, default_year=2026, default_month=2, default_author="",
        )
        if args is None:
            expect(f"author_after:{want}", False, f"args is None for text={text!r}")
            continue
        expect(f"author_after:{want}", args["author"] == want,
               f"got author={args['author']!r} from text={text!r}")


# ─── 15. default_author 폴백 (메시지에 이름 없을 때) ─────────────
def test_default_author_fallback() -> None:
    print("\n[test_default_author_fallback]")
    text = (
        "개인카드 지출\n"
        "여비교통비, 2/9, 택시, 10000, 가맹점\n"
        "expense 작성해줘"  # 이름 없음
    )
    args = parse_expense_message(
        text, default_year=2026, default_month=2, default_author="시스템사용자",
    )
    expect("default_author.used",
           args is not None and args["author"] == "시스템사용자",
           f"got {args}")


if __name__ == "__main__":
    cases = [
        test_v10_real_user_input,
        test_mixed_corp_personal,
        test_amount_variants,
        test_category_aliases,
        test_date_variants,
        test_empty_section_markers,
        test_purpose_with_commas,
        test_rejection_cases,
        test_system_prompt_extractors,
        test_end_to_end_v10,
        test_user_phrasing_variants,
        test_slash_separator_5field,
        test_slash_with_department_and_bracket_header,
        test_author_name_after_trigger,
        test_default_author_fallback,
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
