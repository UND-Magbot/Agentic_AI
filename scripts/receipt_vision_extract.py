"""영수증 이미지 → gemma3:12b 비전 OCR(전체 텍스트) → 검증된 규칙 파서 → expense 라인.

전략(2단계):
  1) gemma3 비전에 영수증/카드전표의 **모든 텍스트를 줄 그대로** 추출시킨다(전체 OCR).
     - 특정 필드만 물으면(예: "가맹점만") gemma 가 이미지를 대충 보고 환각하므로(검증됨),
       반드시 "전체 텍스트를 그대로" 라고 지시해 비전을 꼼꼼히 작동시킨다.
  2) receipt_ocr_extract 와 동일한 규칙 파서로 가맹점·거래일·금액·카테고리 파싱.
     - 정답지(백동주 26.02) 정책 반영: 숙박비 → 여비교통비.

실행: docker exec und_cortex_backend python /app/receipt_vision_extract.py /app/_receipts [--raw]
"""
from __future__ import annotations

import base64
import glob
import json
import os
import re
import sys
import time

import httpx

OLLAMA = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:12b")
# 영수증 귀속 연도(예: "2026"). 익스펜스는 특정 월 단위로 제출되므로 사용자가 월을 지정하면
# 연도를 안다 — 비전이 연도를 26→24 처럼 흔히 오독하므로 이 값으로 보정한다(월/일은 비전 신뢰).
EXPENSE_YEAR = os.environ.get("EXPENSE_YEAR", "").strip()

CATEGORIES = (
    "접대비", "복리후생비", "여비교통비", "소모품비",
    "차량유지비", "지급수수료", "도서인쇄비", "해당없음",
)

# 카테고리 키워드. 정답지 정책: 숙박비 → 여비교통비(출장 부대비용).
_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("여비교통비", (
        "지하철", "버스", "택시", "철도", "ktx", "srt", "고속버스", "톨게이트", "통행료",
        "티머니", "교통", "카카오t", "카카오택시", "항공", "공항", "기차", "코레일",
        "하이패스", "광역버스", "마을버스", "gtx", "수도권", "전철", "주차", "주차장",
        "숙박", "숙소", "호텔", "모텔", "펜션", "리조트", "객실", "게스트하우스",
        "야놀자", "여기어때",
    )),
    ("차량유지비", (
        "주유", "주유소", "gs칼텍스", "칼텍스", "sk에너지", "s-oil", "에쓰오일", "현대오일",
        "오일뱅크", "세차", "정비", "카센터", "타이어", "엔진오일", "전기차충전", "가스충전",
        "충전소", "유류",
    )),
    ("도서인쇄비", (
        "교보문고", "영풍문고", "알라딘", "yes24", "예스24", "서점", "북스",
        "인쇄소", "도서", "제본", "복사집", "인쇄물",
    )),
    ("소모품비", (
        "다이소", "오피스디포", "알파문구", "문구", "사무용품", "철물", "비품", "모닝글로리",
        "핫트랙스", "office",
    )),
    ("지급수수료", ("수수료", "송금수수료", "이체수수료")),
    ("접대비", ("접대", "거래처")),
    ("복리후생비", (
        "식당", "김밥", "분식", "백반", "한식", "중식", "일식", "양식", "식사", "점심", "저녁",
        "회식", "간식", "다과", "카페", "커피", "스타벅스", "투썸", "이디야", "빽다방", "메가커피",
        "베이커리", "빵", "파리바게뜨", "뚜레쥬르", "마트", "편의점", "gs25", "cu",
        "세븐일레븐", "이마트", "롯데마트", "홈플러스", "피자", "치킨", "버거", "맥도날드",
        "롯데리아", "탐앤탐스", "지에스25", "지에스", "씨유", "이마트24", "미니스톱",
        "우아한형제들", "배달의민족", "배민", "요기요", "쿠팡이츠", "도성루", "부대찌개",
        "무지막회", "웰스토리", "야근",
    )),
]

_AMOUNT_RE = re.compile(r"(\d{1,3}(?:,\d{3})+)\s*원?|(\d{2,7})\s*원")
_DATE_RE = re.compile(r"(20\d{2}|\d{2})[.\-/,](\d{1,2})[.\-/,](\d{1,2})")
_AMOUNT_LABEL_TIERS = (
    ("실결제금액", "실결제", "실지불"),
    ("결제금액", "승인금액", "거래금액"),
    ("합계", "총액", "합계금액"),
    ("카드금액", "금액", "매출"),
)
_STORE_LABELS = ("가맹점명", "가맹점", "가행점", "상호", "사업장", "매장명", "상호명")
_TXN_DATE_LABELS = ("이용일시", "이용인시", "이용일자", "승인일자", "승인일시", "거래일시", "거래일자", "결제일시")
_PAY_DATE_LABELS = ("결제일", "결제일자")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _amounts_in_line(line: str) -> list[int]:
    out = []
    for m in _AMOUNT_RE.finditer(line):
        tok = m.group(1) or m.group(2)
        if tok is None:
            continue
        v = int(tok.replace(",", ""))
        if 0 < v < 100_000_000:
            out.append(v)
    return out


def map_category(text_joined: str) -> tuple[str, str]:
    hay = _norm(text_joined)
    for cat, kws in _CATEGORY_KEYWORDS:
        for kw in kws:
            if _norm(kw) in hay:
                return cat, kw
    return "해당없음", ""


def extract_amount(lines: list[str]) -> int | None:
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
    first = None
    all_amts = []
    for ln in lines:
        amts = _amounts_in_line(ln)
        if amts and first is None:
            first = amts[0]
        all_amts.extend(amts)
    if first is not None:
        return first
    return max(all_amts) if all_amts else None


def extract_date(lines: list[str]) -> str | None:
    def _fmt(m: re.Match) -> str | None:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        year = int(y) if len(y) == 4 else 2000 + int(y)
        if not (2020 <= year <= 2030):
            return None
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return None
        return f"{year:04d}-{mo:02d}-{d:02d}"

    def _date_on(idx: int) -> str | None:
        for j in (idx, idx + 1):
            if 0 <= j < len(lines):
                m = _DATE_RE.search(lines[j])
                if m:
                    f = _fmt(m)
                    if f:
                        return f
        return None

    txn = pay = any_date = None
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
    for i, ln in enumerate(lines):
        for lbl in _STORE_LABELS:
            if lbl in ln:
                tail = ln.split(lbl, 1)[1].strip(" :=-\t")
                if len(tail) >= 2 and not _amounts_in_line(tail):
                    return tail[:40]
                if i + 1 < len(lines) and lines[i + 1].strip():
                    nxt = lines[i + 1].strip()
                    if not _amounts_in_line(nxt) and not _DATE_RE.search(nxt):
                        return nxt[:40]
    for i, ln in enumerate(lines):
        if "매출전표" in _norm(ln):
            for j in range(i + 1, min(i + 4, len(lines))):
                cand = lines[j].strip()
                if (len(cand) >= 2 and not _amounts_in_line(cand)
                        and not _DATE_RE.search(cand) and re.search(r"[가-힣]", cand)):
                    return cand[:40]
    return None


_VISION_PROMPT = (
    "이 영수증/카드 매출전표 이미지에 적힌 모든 글자를 위에서 아래로 보이는 줄 그대로 옮겨 적어줘. "
    "라벨(가맹점명·이용일시·결제일·이용구분·금액 등)과 그 값을 한 줄씩 모두 포함하고, "
    "해석·요약·설명은 하지 말고 원문 텍스트만 출력해. 금액의 콤마와 '원'도 그대로. "
    "같은 줄을 반복하지 마."
)


def vision_ocr(path: str) -> list[str]:
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": _VISION_PROMPT, "images": [b64]}],
        "stream": False,
        "options": {"temperature": 0, "num_predict": 400, "repeat_penalty": 1.3},
    }
    r = httpx.post(f"{OLLAMA}/api/chat", json=payload, timeout=180)
    r.raise_for_status()
    text = r.json()["message"]["content"]
    lines = []
    for ln in text.splitlines():
        s = ln.strip().lstrip("*-•").strip()
        s = re.sub(r"^\d+[.)]\s*", "", s)
        # 안내 문구(프롬프트 메아리) 제거.
        if s and "옮겨 적" not in s and "원문 텍스트" not in s:
            lines.append(s)
    return lines


def main() -> int:
    folder = sys.argv[1] if len(sys.argv) > 1 else "/app/_receipts"
    raw = "--raw" in sys.argv
    files = sorted(
        [p for p in glob.glob(os.path.join(folder, "*")) if p.lower().endswith((".png", ".jpg", ".jpeg"))],
        key=lambda p: (len(os.path.basename(p)), os.path.basename(p)),
    )
    if not files:
        print(f"이미지 없음: {folder}")
        return 1
    print(f"[gemma3 비전 OCR(전체) + 규칙 파서] {len(files)}개 · {OLLAMA} · {MODEL}\n")
    results = []
    for p in files:
        name = os.path.basename(p)
        t = time.time()
        try:
            lines = vision_ocr(p)
            joined = "\n".join(lines)
            amount = extract_amount(lines)
            date = extract_date(lines)
            store = extract_store(lines)
            category, kw = map_category(joined)
            # 연도 보정 — 비전의 연도 오독(26→24 등)을 귀속연도로 교정. 월/일은 유지.
            if EXPENSE_YEAR and date and len(date) >= 10:
                date = EXPENSE_YEAR + date[4:]
            d = {"file": name, "store": store, "date": date, "amount": amount,
                 "category": category, "matched_keyword": kw, "n_lines": len(lines)}
            if raw:
                d["raw_lines"] = lines
            results.append(d)
            print(f"{name:<18} {str(store)[:16]!r:<18} date={date} amount={amount} "
                  f"cat={category} kw={kw!r} ({time.time()-t:.1f}s)")
            if raw:
                for ln in lines:
                    print("      ·", ln)
        except Exception as e:
            results.append({"file": name, "error": str(e)})
            print(f"{name:<18} ERROR {type(e).__name__}: {str(e)[:120]}")

    out_dir = "/app/_capture"
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "receipt_vision.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n저장: {out_dir}/receipt_vision.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
