"""비전 추출 결과 vs 정답지(expense_26.02월_백동주) 개인카드 13건 대조 — 정답률 측정.

기준: 정답지 '영수증 첨부' 시트의 13개 캡션(날짜/지출사유/금액) + '내역' 시트의 계정과목.
금액은 멀티셋 매칭(±2% 또는 ±3원 — OCR 1자리 오독 허용), 날짜/카테고리는 매칭된 금액 기준 비교.
"""
import collections
import json
import sys

# 정답지 개인카드 13건 (영수증 = 캡션, 계정과목은 내역 시트 기준)
GOLD = [
    {"date": "2026-02-03", "amount": 4200, "cat": "여비교통비", "desc": "교통비"},
    {"date": "2026-02-04", "amount": 4200, "cat": "여비교통비", "desc": "교통비"},
    {"date": "2026-02-04", "amount": 25000, "cat": "여비교통비", "desc": "교통비(주차)"},
    {"date": "2026-02-06", "amount": 151000, "cat": "복리후생비", "desc": "야근식대"},
    {"date": "2026-02-15", "amount": 27750, "cat": "차량유지비", "desc": "유류비"},
    {"date": "2026-02-24", "amount": 91600, "cat": "복리후생비", "desc": "야근식대"},
    {"date": "2026-02-25", "amount": 150900, "cat": "복리후생비", "desc": "야근식대"},
    {"date": "2026-02-26", "amount": 9000, "cat": "복리후생비", "desc": "야근식대"},
    {"date": "2026-02-27", "amount": 16400, "cat": "복리후생비", "desc": "출장점심식대"},
    {"date": "2026-02-27", "amount": 9800, "cat": "복리후생비", "desc": "야근식대"},
    {"date": "2026-02-28", "amount": 35100, "cat": "여비교통비", "desc": "출장교통비"},
    {"date": "2026-02-28", "amount": 85000, "cat": "여비교통비", "desc": "숙박비(서울)"},
    {"date": "2026-02-28", "amount": 89000, "cat": "여비교통비", "desc": "숙박비(성남)"},
]


def _amt_match(a: int, g: int) -> bool:
    return a == g or abs(a - g) <= max(3, int(g * 0.02))


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/app/_capture/receipt_vision.json"
    ext = [e for e in json.load(open(path, encoding="utf-8")) if e.get("amount")]

    remaining = list(GOLD)
    amt_hit = date_hit = cat_hit = 0
    used = []
    for e in ext:
        a = e["amount"]
        gi = next((i for i, g in enumerate(remaining) if _amt_match(a, g["amount"])), None)
        if gi is None:
            used.append((e, None))
            continue
        g = remaining.pop(gi)
        amt_hit += 1
        if e.get("date") == g["date"]:
            date_hit += 1
        if e.get("category") == g["cat"]:
            cat_hit += 1
        used.append((e, g))

    n = len(GOLD)
    print(f"추출 {len(ext)}건 / 정답 {n}건")
    print(f"금액 매칭   : {amt_hit}/{n}  ({amt_hit/n*100:.1f}%)")
    print(f"날짜 일치   : {date_hit}/{n}  (금액 매칭분 중 {date_hit}/{amt_hit})")
    print(f"카테고리 일치: {cat_hit}/{n}  (금액 매칭분 중 {cat_hit}/{amt_hit})")
    print("\n=== 상세 ===")
    for e, g in used:
        if g:
            dm = "✓" if e.get("date") == g["date"] else f"✗({e.get('date')}≠{g['date']})"
            cm = "✓" if e.get("category") == g["cat"] else f"✗({e.get('category')}≠{g['cat']})"
            print(f"  {e['file']:<16} amt={e['amount']}✓ date={dm} cat={cm}  [{g['desc']}]")
        else:
            print(f"  {e['file']:<16} amt={e['amount']} → 정답 금액 미매칭 (store={e.get('store')!r})")
    if remaining:
        print("\n미검출 정답:", [(g["amount"], g["desc"]) for g in remaining])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
