"""현재 코드로 expense xlsx 를 만들어 결과를 검증.

사용자 시나리오: 개인카드 3건 + 영수증 3장(있다면).
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.expense_builder import (
    ExpenseLine,
    ExpenseReport,
    ReceiptImage,
    build_expense_xlsx,
)


def make_dummy_image(label: str = "TEST") -> bytes:
    """간단한 PNG 더미 이미지(슬롯 비율 어림 600x800)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 800), color=(245, 245, 220))
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 590, 790], outline=(50, 50, 50), width=4)
    draw.text((50, 50), label, fill=(20, 20, 20))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def main() -> None:
    lines = [
        ExpenseLine(
            source="개인카드",
            category="해당없음",
            date="2026-02-07",
            purpose="현대 글로비스 공장 출입 위한 안전화 구매",
            amount=72000,
            vendor="워크업 대구 반야월점",
        ),
        ExpenseLine(
            source="개인카드",
            category="여비교통비",
            date="2026-02-09",
            purpose="평택지제역 -> 동대구역 SRT 승차권 구매",
            amount=29500,
            vendor="주식회사 에스알",
        ),
        ExpenseLine(
            source="개인카드",
            category="여비교통비",
            date="2026-02-10",
            purpose="숙소 근처 -> 평택지제역 콜택시 이용",
            amount=30000,
            vendor="미래 대리우전",
        ),
    ]

    receipts = [
        ReceiptImage(filename="r1.png", data=make_dummy_image("R1-안전화")),
        ReceiptImage(filename="r2.png", data=make_dummy_image("R2-SRT")),
        ReceiptImage(filename="r3.png", data=make_dummy_image("R3-택시")),
    ]
    report = ExpenseReport(
        author="배재병",
        year=2026,
        month=2,
        lines=lines,
        receipts=receipts,
    )
    xlsx = build_expense_xlsx(report)
    out_path = ROOT / "_test_expense_fresh.xlsx"
    out_path.write_bytes(xlsx)
    print(f"✓ written {out_path} ({len(xlsx):,} bytes)")


if __name__ == "__main__":
    main()
