"""영수증 이미지 → OCR → 필드 추출 → expense 개인 지출 카테고리 매핑.

목적:
    C:\\영수증 내역 의 영수증/카드 매출전표 이미지에서 글자를 추출하고,
    가맹점·일시·금액을 파싱해 expense 양식의 8개 개인 지출 카테고리로 분류한다.

카테고리(= expense_builder.ExpenseCategory):
    접대비 / 복리후생비 / 여비교통비 / 소모품비 / 차량유지비 / 지급수수료 / 도서인쇄비 / 해당없음

엔진: EasyOCR(ko+en). Windows 한글 경로는 PIL 로 로드해 numpy 로 전달(cv2.imread 한글경로 버그 회피).
저신뢰(종이 영수증) 이미지는 업스케일+그레이+오토컨트라스트 전처리로 1회 재시도.

실행:
    python scripts/receipt_ocr_extract.py [--dir "C:\\영수증 내역"] [--raw]
    --raw : 파일별 OCR 원문 라인도 출력(디버깅용).
출력:
    콘솔 검토 테이블 + _capture/receipt_extract.json + _capture/receipt_extract.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

# 콘솔 한글 안전 출력(Windows cp949 회피).
sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DEFAULT_DIR = r"C:\영수증 내역"
OUT_DIR = Path(__file__).resolve().parents[1] / "_capture"

# ── 8개 정규 카테고리 (expense_builder 와 동일) ──────────────────────────────
CATEGORIES = (
    "접대비", "복리후생비", "여비교통비", "소모품비",
    "차량유지비", "지급수수료", "도서인쇄비", "해당없음",
)

# 카테고리 매핑 키워드 — 영수증 도메인 확장(가맹점 업종 + 이용구분 + 상품명).
# 우선순위: 위에서부터 검사하여 첫 매치 채택(구체적인 것 먼저). 공백 제거 후 부분일치.
_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("여비교통비", (
        "지하철", "버스", "택시", "철도", "ktx", "srt", "고속버스", "톨게이트", "통행료",
        "티머니", "교통", "카카오t", "카카오택시", "항공", "공항", "기차", "코레일",
        "하이패스", "광역버스", "마을버스", "gtx", "수도권", "전철",
    )),
    ("차량유지비", (
        "주유", "주유소", "gs칼텍스", "칼텍스", "sk에너지", "s-oil", "에쓰오일", "현대오일",
        "오일뱅크", "주차", "세차", "정비", "카센터", "타이어", "엔진오일", "전기차충전",
    )),
    ("도서인쇄비", (
        # '복사'/'출력' 단독은 '예약번호 복사' 버튼 등 오탐 위험 → 제외. 서점/도서/인쇄소만.
        "교보문고", "영풍문고", "알라딘", "yes24", "예스24", "반디앤루니스", "서점", "북스",
        "인쇄소", "도서", "제본", "복사집", "인쇄물",
    )),
    ("소모품비", (
        "다이소", "오피스디포", "알파문구", "문구", "사무용품", "철물", "비품", "모닝글로리",
        "핫트랙스", "officedepot", "office",
    )),
    ("지급수수료", (
        "수수료", "송금수수료", "이체수수료",
    )),
    ("접대비", (
        "접대", "거래처",
    )),
    ("복리후생비", (
        # 음식점/배달/카페/마트/편의점/숙박 외 복지성 지출. 배달앱(우아한형제들=배민) 포함.
        "식당", "김밥", "분식", "백반", "한식", "중식", "일식", "양식", "식사", "점심", "저녁",
        "회식", "간식", "다과", "카페", "커피", "스타벅스", "투썸", "이디야", "빽다방", "메가커피",
        "베이커리", "빵", "파리바게뜨", "뚜레쥬르", "마트", "편의점", "gs25", "cu", "세븐일레븐",
        "이마트", "롯데마트", "홈플러스", "피자", "치킨", "버거", "맥도날드", "롯데리아", "탐앤탐스",
        "gs25", "지에스25", "지에스", "씨유", "세븐일레븐", "이마트24", "미니스톱",
        "우아한형제들", "배달의민족", "배민", "요기요", "쿠팡이츠",
        "안마", "마사지", "헬스", "휘트니스", "워크빌",
        # 출장 숙박(8개 카테고리엔 별도 숙박 항목이 없어 복리후생비로 귀속 — 운영 정책에 따라 조정).
        # '객실'은 OCR이 '객심'으로 자주 깨져 '숙소'도 함께 둔다.
        "숙박", "숙소", "호텔", "모텔", "펜션", "리조트", "객실", "게스트하우스", "야놀자", "여기어때",
    )),
]

# ── 필드 추출 정규식 ────────────────────────────────────────────────────────
# 금액: 반드시 '천단위 콤마 형식'(4,200 / 150,900) 또는 '숫자+원'. 승인번호(30055507,
# 콤마·원 없음)·카드번호(9490-94)·시각(11:51:00)을 금액으로 오인하던 v1 버그를 차단.
_AMOUNT_RE = re.compile(r"(\d{1,3}(?:,\d{3})+)\s*원?|(\d{2,7})\s*원")
# 날짜: 26.02.03 / 2026.02.07 / 26-02-03 / 2026.02,28(OCR이 .을 ,로 오인) 등.
# 구분자에 콤마 포함 — OCR이 마침표를 콤마로 자주 오인('2026.02,28'). 월>12/일>31은 _fmt 가 배제.
_DATE_RE = re.compile(r"(20\d{2}|\d{2})[.\-/,](\d{1,2})[.\-/,](\d{1,2})")
# 금액 라벨 — 우선순위 티어(앞일수록 우선). '실 결제 금액' > '결제 금액' > 합계/승인 > 카드.
_AMOUNT_LABEL_TIERS = (
    ("실결제금액", "실결제", "실지불"),
    ("결제금액", "승인금액", "거래금액"),
    ("합계", "총액", "합계금액"),
    ("카드금액", "금액", "매출"),
)
# 가맹점/상호 라벨(OCR 오타 흡수 위해 짧은 핵심부 포함: 가맹/가행).
_STORE_LABELS = ("가맹점명", "가맹점", "가행점", "상호", "사업장", "매장명", "상호명")
# 거래일 라벨(OCR 오타 흡수: 이용일시/이용인시/이용일자/승인일자/거래일시).
_TXN_DATE_LABELS = ("이용일시", "이용인시", "이용일자", "승인일자", "승인일시", "거래일시", "거래일자", "결제일시")
_PAY_DATE_LABELS = ("결제일", "결제일자", "권제임시", "결제임시")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _amounts_in_line(line: str) -> list[int]:
    """한 줄에서 유효 금액(콤마형식 또는 숫자+원) 정수 리스트."""
    out: list[int] = []
    for m in _AMOUNT_RE.finditer(line):
        tok = m.group(1) or m.group(2)
        if tok is None:
            continue
        v = int(tok.replace(",", ""))
        if 0 < v < 100_000_000:  # 1억 미만(영수증 현실 범위)
            out.append(v)
    return out


def map_category(text_joined: str, hint_lines: list[str]) -> tuple[str, str]:
    """OCR 전체 텍스트 + 힌트 라인에서 카테고리 결정. (category, matched_keyword) 반환."""
    hay = _norm(text_joined)
    for cat, kws in _CATEGORY_KEYWORDS:
        for kw in kws:
            if _norm(kw) in hay:
                return cat, kw
    return "해당없음", ""


def extract_amount(lines: list[str]) -> int | None:
    """금액 추출.

    우선순위:
      1) 금액 라벨 티어(실결제 > 결제/승인 > 합계 > 카드/금액) — 라벨 같은 줄 또는 다음 줄의 금액.
      2) 라벨이 없으면 카드 매출전표 상단(가맹점/매출전표 직후)의 **첫** 금액(= 거래금액).
      3) 그래도 없으면 전체 최댓값.
    """
    # 1) 티어별 라벨 인접 금액(같은 줄 + 다음 줄).
    for tier in _AMOUNT_LABEL_TIERS:
        for i, ln in enumerate(lines):
            n = _norm(ln)
            if any(lbl in n for lbl in tier):
                here = _amounts_in_line(ln)
                if here:
                    return max(here)
                if i + 1 < len(lines):
                    nxt = _amounts_in_line(lines[i + 1])
                    if nxt:
                        return max(nxt)
    # 2) 라벨 없음 — 첫 금액(카드전표 상단 거래금액). 시각/번호는 _amounts_in_line 이 이미 배제.
    all_amts: list[int] = []
    first: int | None = None
    for ln in lines:
        amts = _amounts_in_line(ln)
        if amts and first is None:
            first = amts[0]
        all_amts.extend(amts)
    if first is not None:
        return first
    return max(all_amts) if all_amts else None


def extract_date(lines: list[str]) -> str | None:
    """거래일 우선(이용일시/승인일자) → 없으면 결제일 → 없으면 첫 날짜. YYYY-MM-DD."""
    def _fmt(m: re.Match) -> str | None:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        year = int(y) if len(y) == 4 else 2000 + int(y)
        # 연도 sanity — 영수증은 최근 연도. 잡음 OCR이 만든 2002 등 비현실 연도 배제.
        if not (2020 <= year <= 2030):
            return None
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return None
        return f"{year:04d}-{mo:02d}-{d:02d}"

    def _date_on(idx: int) -> str | None:
        """idx 줄 또는 다음 줄에서 날짜 추출(라벨과 값이 분리된 카드전표 대응)."""
        for j in (idx, idx + 1):
            if 0 <= j < len(lines):
                m = _DATE_RE.search(lines[j])
                if m:
                    f = _fmt(m)
                    if f:
                        return f
        return None

    txn: str | None = None
    pay: str | None = None
    any_date: str | None = None
    for i, ln in enumerate(lines):
        n = _norm(ln)
        if txn is None and any(lbl in n for lbl in _TXN_DATE_LABELS):
            txn = _date_on(i)
        if pay is None and any(lbl in n for lbl in _PAY_DATE_LABELS):
            pay = _date_on(i)
        if any_date is None:
            m = _DATE_RE.search(ln)
            if m:
                any_date = _fmt(m)
    return txn or pay or any_date


def extract_store(lines: list[str]) -> str | None:
    """가맹점명 추출.

    1) 가맹점/상호 라벨 인접(같은 줄 뒤 또는 다음 줄).
    2) 카드 매출전표는 '매출전표' 바로 다음 줄에 가맹점명이 온다(라벨 없음).
       금액/날짜/잡음 줄은 건너뛰고 첫 의미 있는 한글 포함 줄을 가맹점으로.
    """
    # 1) 라벨 기반.
    for i, ln in enumerate(lines):
        for lbl in _STORE_LABELS:
            if lbl in ln:
                tail = ln.split(lbl, 1)[1].strip(" :=-\t")
                if len(tail) >= 2:
                    return tail[:40]
                if i + 1 < len(lines) and lines[i + 1].strip():
                    return lines[i + 1].strip()[:40]
    # 2) '매출전표' 다음 의미 있는 줄.
    for i, ln in enumerate(lines):
        if "매출전표" in _norm(ln):
            for j in range(i + 1, min(i + 4, len(lines))):
                cand = lines[j].strip()
                cn = _norm(cand)
                if (len(cand) >= 2 and not _amounts_in_line(cand)
                        and not _DATE_RE.search(cand)
                        and cn not in ("상세금액", "결제정보", "이용정보", "건제정보")
                        and re.search(r"[가-힣]", cand)):
                    return cand[:40]
    return None


def _preprocess(img: Image.Image, scale: float = 2.0) -> np.ndarray:
    """저신뢰 이미지 보정 — 업스케일 + 그레이 + 오토컨트라스트."""
    g = ImageOps.autocontrast(ImageOps.grayscale(img))
    w, h = g.size
    g = g.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return np.array(g)


def process_image(reader, path: Path, *, raw: bool) -> dict:
    img = Image.open(path).convert("RGB")
    # 1차 OCR(원본).
    res = reader.readtext(np.array(img), detail=1, paragraph=False)
    lines = [t for (_box, t, _c) in res]
    confs = [c for (_box, _t, c) in res]
    avg_conf = round(sum(confs) / len(confs), 3) if confs else 0.0

    # 저신뢰(종이 영수증 등) → 전처리 후 재시도, 더 좋은 쪽 채택.
    if avg_conf < 0.5 or len(lines) < 6:
        res2 = reader.readtext(_preprocess(img), detail=1, paragraph=False)
        lines2 = [t for (_box, t, _c) in res2]
        confs2 = [c for (_box, _t, c) in res2]
        avg2 = round(sum(confs2) / len(confs2), 3) if confs2 else 0.0
        if (avg2 * len(lines2)) > (avg_conf * len(lines)):
            lines, confs, avg_conf = lines2, confs2, avg2

    joined = "\n".join(lines)
    amount = extract_amount(lines)
    date = extract_date(lines)
    store = extract_store(lines)
    category, matched_kw = map_category(joined, lines)

    # 신뢰도 종합 — OCR 평균 + 핵심 필드 충족도.
    found = sum(x is not None for x in (amount, date)) + (1 if category != "해당없음" else 0)
    quality = "good" if (avg_conf >= 0.55 and found >= 2) else (
        "low" if avg_conf < 0.4 or found <= 1 else "fair")

    out = {
        "file": path.name,
        "store": store,
        "date": date,
        "amount": amount,
        "category": category,
        "matched_keyword": matched_kw,
        "ocr_avg_conf": avg_conf,
        "n_lines": len(lines),
        "quality": quality,
    }
    if raw:
        out["raw_lines"] = lines
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--raw", action="store_true")
    args = ap.parse_args()

    folder = Path(args.dir)
    images = sorted(
        [p for p in folder.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")],
        key=lambda p: (len(p.stem), p.stem),
    )
    if not images:
        print(f"이미지 없음: {folder}")
        return 1

    import easyocr
    print(f"[init] EasyOCR(ko,en) 로딩…")
    t0 = time.time()
    reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
    print(f"[init] {time.time()-t0:.1f}s · 대상 {len(images)}개\n")

    results = []
    for p in images:
        t = time.time()
        r = process_image(reader, p, raw=args.raw)
        results.append(r)
        print(
            f"{r['file']:<18} {r['quality']:<4} conf={r['ocr_avg_conf']:.2f} | "
            f"{(r['category'] or '-'):<7} | amt={r['amount']} date={r['date']} "
            f"store={r['store']!r} kw={r['matched_keyword']!r} ({time.time()-t:.1f}s)"
        )
        if args.raw:
            for ln in r.get("raw_lines", []):
                print("      ·", ln)

    # 요약.
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "receipt_extract.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    by_q: dict[str, int] = {}
    by_c: dict[str, int] = {}
    for r in results:
        by_q[r["quality"]] = by_q.get(r["quality"], 0) + 1
        by_c[r["category"]] = by_c.get(r["category"], 0) + 1
    print("\n── 요약 ──")
    print("품질:", dict(sorted(by_q.items())))
    print("카테고리:", dict(sorted(by_c.items(), key=lambda x: -x[1])))

    # 검토용 markdown 표.
    md = ["# 영수증 추출 결과", "", "| 파일 | 품질 | conf | 카테고리 | 금액 | 날짜 | 가맹점 | kw |",
          "|---|---|---|---|---|---|---|---|"]
    for r in results:
        md.append(
            f"| {r['file']} | {r['quality']} | {r['ocr_avg_conf']:.2f} | {r['category']} | "
            f"{r['amount']} | {r['date']} | {r['store'] or ''} | {r['matched_keyword']} |"
        )
    (OUT_DIR / "receipt_extract.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n저장: {OUT_DIR/'receipt_extract.json'} , {OUT_DIR/'receipt_extract.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
