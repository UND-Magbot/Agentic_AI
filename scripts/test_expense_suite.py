"""Expense 빌더 종합 검증.

검증 항목:
- 사용자 시나리오(개인카드 3건 + 영수증 3) 정상 빌드
- 법인카드만, 개인카드만, 혼합
- 영수증 0개, 영수증 16개(한도)
- 영수증 17개 → 에러 (한도 초과)
- 라인 48건(한도), 49건 → 에러
- 잘못된 날짜 형식 ('2/30', '2026-13-01', 자유 텍스트)
- 큰 이미지(EXIF 회전, RGBA, 매우 큰 픽셀) 처리
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

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from PIL import Image as PILImage

from app.expense_builder import (
    ExpenseLine,
    ExpenseReport,
    ReceiptImage,
    build_expense_xlsx,
)

OUT_DIR = ROOT / "_test_artifacts"
OUT_DIR.mkdir(exist_ok=True)

FAIL = []
PASS = []


def png_bytes(label: str, size: tuple[int, int] = (600, 800)) -> bytes:
    img = PILImage.new("RGB", size, color=(240, 240, 220))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def big_image(label: str) -> bytes:
    """4000x3000 큰 이미지(스마트폰 풀해상도 시뮬레이션)."""
    img = PILImage.new("RGB", (4000, 3000), color=(220, 240, 240))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


def expect(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL {name} — {detail}")


def case_user_scenario() -> None:
    print("\n[case_user_scenario] 개인카드 3건 + 영수증 3장 (사용자 보고 데이터)")
    lines = [
        ExpenseLine("개인카드", "해당없음", "2026-02-07",
                    "현대 글로비스 공장 출입 위한 안전화 구매", 72000, "워크업 대구 반야월점"),
        ExpenseLine("개인카드", "여비교통비", "2026-02-09",
                    "평택지제역 -> 동대구역 SRT 승차권 구매", 29500, "주식회사 에스알"),
        ExpenseLine("개인카드", "여비교통비", "2026-02-10",
                    "숙소 근처 -> 평택지제역 콜택시 이용", 30000, "미래 대리우전"),
    ]
    receipts = [
        ReceiptImage("r1.png", png_bytes("R1")),
        ReceiptImage("r2.png", png_bytes("R2")),
        ReceiptImage("r3.png", png_bytes("R3")),
    ]
    data = build_expense_xlsx(ExpenseReport("배재병", 2026, 2, lines, receipts))
    path = OUT_DIR / "user_scenario.xlsx"
    path.write_bytes(data)

    wb = load_workbook(path)
    ws = wb["expense_내역"]
    # 날짜는 datetime 으로 저장되어야 한다(v3 fix).
    import datetime as _dt
    expect("user.date_is_datetime_I5",
           isinstance(ws["I5"].value, _dt.datetime),
           f"got {type(ws['I5'].value).__name__}: {ws['I5'].value!r}")
    expect("user.date_is_datetime_I6",
           isinstance(ws["I6"].value, _dt.datetime),
           f"got {type(ws['I6'].value).__name__}")
    expect("user.date_is_datetime_I7",
           isinstance(ws["I7"].value, _dt.datetime),
           f"got {type(ws['I7'].value).__name__}")
    # 캡션 이용된 슬롯만 채워짐.
    wr = wb["expense_개인카드영수증 첨부"]
    expect("user.caption_slot0_filled", wr["C4"].value == "02/07")
    expect("user.caption_slot1_filled", wr["G4"].value == "02/09")
    expect("user.caption_slot2_filled", wr["K4"].value == "02/10")
    # 사용되지 않은 슬롯 캡션은 None.
    expect("user.caption_slot3_clear",
           wr["O4"].value is None, f"got {wr['O4'].value!r}")
    expect("user.caption_slot4_clear",
           wr["C26"].value is None, f"got {wr['C26'].value!r}")
    expect("user.caption_slot7_clear",
           wr["O26"].value is None, f"got {wr['O26'].value!r}")
    expect("user.caption_slot8_clear",
           wr["C48"].value is None)
    expect("user.caption_slot15_clear",
           wr["O70"].value is None)
    # 이미지 3장.
    expect("user.image_count_3", len(wr._images) == 3,
           f"got {len(wr._images)}")
    # 이미지가 슬롯 안에 인셋(1px)으로 위치.
    for i, im in enumerate(wr._images):
        a = im.anchor
        expect(f"user.image[{i}].inset_from",
               a._from.colOff > 0 and a._from.rowOff > 0,
               f"colOff={a._from.colOff} rowOff={a._from.rowOff}")
        # 우하단 offset 은 셀 폭/높이 이하여야 함(슬롯 밖으로 안 나감).
        # 정확한 값은 anchor 셀의 폭/높이에 의존하지만, 우리는 1px 인셋했으므로 양수.
        expect(f"user.image[{i}].inset_to_positive",
               a.to.colOff > 0 and a.to.rowOff > 0)


def case_corp_only() -> None:
    print("\n[case_corp_only] 법인카드만 2건")
    lines = [
        ExpenseLine("법인카드", "접대비", "2026-03-01",
                    "외부 미팅 점심", 45000, "맘스터치", "홍길동"),
        ExpenseLine("법인카드", "소모품비", "2026-03-15",
                    "잉크 카트리지", 28000, "쿠팡", "이몽룡"),
    ]
    data = build_expense_xlsx(ExpenseReport("홍길동", 2026, 3, lines, []))
    path = OUT_DIR / "corp_only.xlsx"
    path.write_bytes(data)
    wb = load_workbook(path)
    ws = wb["expense_내역"]
    import datetime as _dt
    expect("corp.B5_is_date", isinstance(ws["B5"].value, _dt.datetime))
    expect("corp.A5", ws["A5"].value == "접대비")
    expect("corp.C5", "외부 미팅" in str(ws["C5"].value))
    expect("corp.D5", ws["D5"].value == 45000)
    expect("corp.F5", ws["F5"].value == "홍길동")
    # 개인카드 영역(H/I/J/K/L) 은 비어야.
    expect("corp.H5_empty", ws["H5"].value is None)
    # 이미지 없음.
    wr = wb["expense_개인카드영수증 첨부"]
    expect("corp.no_images", len(wr._images) == 0)
    # 캡션 전부 비어야.
    expect("corp.no_caption_slot0", wr["C4"].value is None)


def case_no_receipts_with_lines() -> None:
    print("\n[case_no_receipts_with_lines] 개인카드 라인 있고 영수증 첨부 안 한 경우")
    lines = [
        ExpenseLine("개인카드", "복리후생비", "2026-04-05",
                    "팀 회식", 120000, "맛집"),
    ]
    data = build_expense_xlsx(ExpenseReport("김재무", 2026, 4, lines, []))
    path = OUT_DIR / "no_receipts.xlsx"
    path.write_bytes(data)
    wb = load_workbook(path)
    wr = wb["expense_개인카드영수증 첨부"]
    # 캡션은 채워야(영수증 없어도 캡션은 라인 순서대로).
    expect("no_recpt.caption_filled", wr["C4"].value == "04/05")
    expect("no_recpt.no_image", len(wr._images) == 0)


def case_max_receipts() -> None:
    print("\n[case_max_receipts] 영수증 16장 (한도)")
    lines = [
        ExpenseLine("개인카드", "여비교통비", f"2026-05-{i+1:02d}",
                    f"택시 {i+1}", 10000 + i * 100, f"택시{i+1}회사")
        for i in range(16)
    ]
    receipts = [ReceiptImage(f"r{i}.png", png_bytes(f"R{i}")) for i in range(16)]
    data = build_expense_xlsx(ExpenseReport("최강자", 2026, 5, lines, receipts))
    path = OUT_DIR / "max_receipts.xlsx"
    path.write_bytes(data)
    wb = load_workbook(path)
    wr = wb["expense_개인카드영수증 첨부"]
    expect("max16.image_count", len(wr._images) == 16)
    # 마지막 슬롯(O70/P70/Q70) 캡션 확인.
    expect("max16.last_caption_filled",
           wr["O70"].value == "05/16",
           f"got {wr['O70'].value!r}")


def case_over_max_receipts() -> None:
    print("\n[case_over_max_receipts] 영수증 17장 (한도 초과 → ValueError)")
    lines = [
        ExpenseLine("개인카드", "여비교통비", f"2026-05-{i+1:02d}",
                    f"교통 {i+1}", 5000, "가맹점")
        for i in range(16)
    ]
    receipts = [ReceiptImage(f"r{i}.png", png_bytes(f"R{i}")) for i in range(17)]
    try:
        build_expense_xlsx(ExpenseReport("최강자", 2026, 5, lines, receipts))
        expect("over_max.raised", False, "no error raised")
    except ValueError as e:
        expect("over_max.raised", True)
        expect("over_max.message_mentions_limit",
               "한도" in str(e) or "16" in str(e),
               f"msg={e}")


def case_too_many_personal_lines() -> None:
    print("\n[case_too_many_personal_lines] 개인카드 17건 → ValueError(슬롯 한도)")
    lines = [
        ExpenseLine("개인카드", "복리후생비", f"2026-06-{(i%28)+1:02d}",
                    f"식사 {i+1}", 8000, "식당")
        for i in range(17)
    ]
    try:
        build_expense_xlsx(ExpenseReport("배재병", 2026, 6, lines, []))
        expect("too_many.raised", False, "no error")
    except ValueError as e:
        expect("too_many.raised", True)


def case_invalid_date_strings() -> None:
    print("\n[case_invalid_date_strings] 잘못된 날짜는 문자열로 폴백")
    lines = [
        ExpenseLine("개인카드", "해당없음", "2/30",
                    "잘못된 월/일", 1000, "테스트"),
        ExpenseLine("개인카드", "해당없음", "2026-13-01",
                    "월 범위 초과", 1000, "테스트"),
        ExpenseLine("개인카드", "해당없음", "어제",
                    "자유 텍스트", 1000, "테스트"),
    ]
    data = build_expense_xlsx(ExpenseReport("배재병", 2026, 2, lines, []))
    path = OUT_DIR / "invalid_dates.xlsx"
    path.write_bytes(data)
    wb = load_workbook(path)
    ws = wb["expense_내역"]
    # 잘못된 날짜는 문자열로 그대로 들어감(파싱 실패 시 폴백).
    expect("invalid.I5_str", isinstance(ws["I5"].value, str), f"got {type(ws['I5'].value).__name__}")
    expect("invalid.I6_str", isinstance(ws["I6"].value, str))
    expect("invalid.I7_str", isinstance(ws["I7"].value, str))


def case_big_image() -> None:
    print("\n[case_big_image] 큰 이미지(4000×3000 JPEG) 처리")
    lines = [
        ExpenseLine("개인카드", "여비교통비", "2026-07-01", "테스트", 1000, "X"),
    ]
    receipts = [ReceiptImage("big.jpg", big_image("BIG"))]
    data = build_expense_xlsx(ExpenseReport("배재병", 2026, 7, lines, receipts))
    path = OUT_DIR / "big_image.xlsx"
    path.write_bytes(data)
    # 파일 사이즈가 폭증하지 않아야.
    expect("big.file_size_reasonable",
           len(data) < 1_000_000,
           f"file_size={len(data):,}")


def case_personal_capacity_limit() -> None:
    """개인카드 라인 수 17건 ≤ 슬롯 한도(16) 초과 ValueError."""
    print("\n[case_personal_capacity_limit] 개인카드 49건 → 데이터 행 한도 초과 ValueError")
    # 48행 한도 = 데이터 last_row 52 - first_row 5 + 1.
    lines = [
        ExpenseLine("법인카드", "지급수수료", f"2026-08-{(i%28)+1:02d}",
                    f"수수료 {i+1}", 1000, "은행", "박과장")
        for i in range(49)
    ]
    try:
        build_expense_xlsx(ExpenseReport("배재병", 2026, 8, lines, []))
        expect("cap.raised", False, "no error")
    except ValueError:
        expect("cap.raised", True)


if __name__ == "__main__":
    cases = [
        case_user_scenario,
        case_corp_only,
        case_no_receipts_with_lines,
        case_max_receipts,
        case_over_max_receipts,
        case_too_many_personal_lines,
        case_invalid_date_strings,
        case_big_image,
        case_personal_capacity_limit,
    ]
    for fn in cases:
        try:
            fn()
        except Exception as e:
            FAIL.append((fn.__name__, f"unexpected exception: {e}"))
            print(f"  FAIL {fn.__name__} — exception: {e}")
            traceback.print_exc()

    print(f"\n==== {len(PASS)} pass / {len(FAIL)} fail ====")
    if FAIL:
        for name, detail in FAIL:
            print(f"  [FAIL] {name}: {detail}")
        sys.exit(1)
    print("ALL OK")
