"""영수증 이미지 → gemma3:12b 비전 OCR → 규칙 파서 → expense 초안 라인.

반자동(semi-automatic) expense 작성의 비전 추출 계층.

배경 — 카드 매출전표 정답률 측정(개인카드 13건 대조):
  - 금액 84.6% (11/13): 라벨 기반 규칙 파서로 신뢰 가능 → **자동 채움**
  - 날짜 6/13: 비전이 연/일을 흔히 오독 → 자동 채우되 사용자 확인 권장
  - 카테고리 0/13: 카드전표엔 업종/맥락 정보가 없어 재현 구조적 불가 → **사용자 확정**

따라서 본 모듈은 금액·날짜를 자동 추출해 초안을 채우고, 카테고리·지출사유는
호출자가 placeholder 로 비워 사용자에게 확정/보완을 요청한다(`compose_expense_report`
가 받는 콤마 구분 형식으로 round-trip).

전략(2단계, scripts/receipt_vision_extract.py 검증 로직 이식):
  1) gemma3 비전에 영수증/카드전표의 **모든 텍스트를 줄 그대로** OCR (전체 추출).
     특정 필드만 물으면 환각하므로 전체 텍스트를 받아 규칙 파서로 거른다.
  2) 라벨(이용일시·결제금액 등) 우선순위 규칙 파서로 금액·날짜·가맹점·카테고리 추출.

엔드포인트: {settings.ollama_base_url}/api/chat  · 모델: settings.ollama_model
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from .config import settings
from .ollama_client import no_think
from .database import SessionLocal
from .models import Attachment
from .storage import get_object_stream

logger = logging.getLogger("expense_vision")

# 영수증으로 허용할 MIME — expense_service._RECEIPT_IMAGE_MIMES 와 동기.
_RECEIPT_IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp", "image/bmp", "image/gif",
}

# 동시 비전 호출 수 — Ollama 단일 GPU 직렬화를 고려해 보수적으로. 진행 표시는 완료 순.
_VISION_CONCURRENCY = 2

# 8개 정규 카테고리 — expense_builder.ExpenseCategory / expense_parser._EXPENSE_CATEGORIES 동기.
CATEGORIES = (
    "접대비", "복리후생비", "여비교통비", "소모품비",
    "차량유지비", "지급수수료", "도서인쇄비", "해당없음",
)

# 카테고리 키워드(가맹점 업종 + 이용구분 + 상품명). 위에서부터 첫 매치 채택.
# 정답지 정책: 숙박비 → 여비교통비(출장 부대비용).
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
_TXN_DATE_LABELS = (
    "이용일시", "이용인시", "이용일자", "승인일자", "승인일시",
    "거래일시", "거래일자", "결제일시",
)
_PAY_DATE_LABELS = ("결제일", "결제일자")

_VISION_PROMPT = (
    "이 영수증/카드 매출전표 이미지에 적힌 모든 글자를 위에서 아래로 보이는 줄 그대로 옮겨 적어줘. "
    "라벨(가맹점명·이용일시·결제일·이용구분·금액 등)과 그 값을 한 줄씩 모두 포함하고, "
    "해석·요약·설명은 하지 말고 원문 텍스트만 출력해. 금액의 콤마와 '원'도 그대로. "
    "같은 줄을 반복하지 마."
)


@dataclass
class DraftReceipt:
    """비전이 추출한 영수증 1건의 초안.

    amount/date 는 자동 추출(신뢰), category_guess 는 best-effort(사용자 확정 대상).
    ok=True 이면 amount·date 둘 다 추출 성공 — 초안 라인으로 바로 활용 가능.
    """

    attachment_id: int
    filename: str
    order_index: int
    amount: int | None
    date: str | None  # YYYY-MM-DD
    vendor: str | None
    category_guess: str
    raw_lines: list[str]

    @property
    def ok(self) -> bool:
        return self.amount is not None


# ── 규칙 파서(scripts/receipt_vision_extract.py 와 동일 로직) ─────────────────
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _amounts_in_line(line: str) -> list[int]:
    out: list[int] = []
    for m in _AMOUNT_RE.finditer(line):
        tok = m.group(1) or m.group(2)
        if tok is None:
            continue
        v = int(tok.replace(",", ""))
        if 0 < v < 100_000_000:
            out.append(v)
    return out


def _map_category(text_joined: str) -> str:
    hay = _norm(text_joined)
    for cat, kws in _CATEGORY_KEYWORDS:
        for kw in kws:
            if _norm(kw) in hay:
                return cat
    return "해당없음"


def _extract_amount(lines: list[str]) -> int | None:
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
    all_amts: list[int] = []
    for ln in lines:
        amts = _amounts_in_line(ln)
        if amts and first is None:
            first = amts[0]
        all_amts.extend(amts)
    if first is not None:
        return first
    return max(all_amts) if all_amts else None


def _extract_date(lines: list[str]) -> str | None:
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


def _extract_store(lines: list[str]) -> str | None:
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


def _clean_ocr_text(text: str) -> list[str]:
    lines: list[str] = []
    for ln in text.splitlines():
        s = ln.strip().lstrip("*-•").strip()
        s = re.sub(r"^\d+[.)]\s*", "", s)
        if s and "옮겨 적" not in s and "원문 텍스트" not in s:
            lines.append(s)
    return lines


def _parse_ocr_lines(lines: list[str], *, expense_year: int | None) -> dict:
    """OCR 라인 → {amount, date, vendor, category_guess}. 연도 보정 적용."""
    joined = "\n".join(lines)
    amount = _extract_amount(lines)
    date = _extract_date(lines)
    store = _extract_store(lines)
    category = _map_category(joined)
    # 비전의 연도 오독(26→24 등) 교정 — 익스펜스는 특정 월 제출이라 연도를 안다. 월/일은 유지.
    if expense_year and date and len(date) >= 10:
        date = f"{expense_year:04d}{date[4:]}"
    return {
        "amount": amount,
        "date": date,
        "vendor": store,
        "category_guess": category,
    }


# ── 비전 OCR 호출 ────────────────────────────────────────────────────────────
async def _vision_ocr(client: httpx.AsyncClient, image_bytes: bytes) -> list[str]:
    b64 = base64.b64encode(image_bytes).decode()
    payload = no_think({
        "model": settings.ollama_model,
        "messages": [{"role": "user", "content": _VISION_PROMPT, "images": [b64]}],
        "stream": False,
        "options": {"temperature": 0, "num_predict": 400, "repeat_penalty": 1.3,
                    "num_ctx": settings.ollama_num_ctx},  # 채팅과 같아야 모델 재적재 없음
    })
    resp = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
    resp.raise_for_status()
    text = resp.json()["message"]["content"]
    return _clean_ocr_text(text)


# ── 영수증 fetch(expense_service._fetch_receipts 와 동일 정책) ────────────────
@dataclass
class _FetchedReceipt:
    attachment_id: int
    filename: str
    order_index: int
    data: bytes


async def _fetch_receipt_bytes(
    db, attachment_ids: list[int]
) -> list[_FetchedReceipt]:
    """첨부 ID → 이미지 바이트. 입력 순서 보존, 비이미지/누락은 skip+경고."""
    if not attachment_ids:
        return []
    res = await db.execute(select(Attachment).where(Attachment.id.in_(attachment_ids)))
    by_id = {a.id: a for a in res.scalars().all()}

    out: list[_FetchedReceipt] = []
    for idx, att_id in enumerate(attachment_ids):
        att = by_id.get(att_id)
        if att is None:
            logger.warning("[vision] 첨부 id=%s 존재하지 않음 — skip", att_id)
            continue
        if att.mime not in _RECEIPT_IMAGE_MIMES:
            logger.warning("[vision] 첨부 id=%s mime=%s 이미지 아님 — skip", att_id, att.mime)
            continue
        try:
            response = get_object_stream(att.object_key)
            try:
                chunks = [c for c in response.stream(amt=64 * 1024)]
                data = b"".join(chunks)
            finally:
                response.close()
                response.release_conn()
        except Exception as e:
            logger.warning("[vision] MinIO fetch 실패 id=%s: %s", att_id, e)
            continue
        out.append(_FetchedReceipt(att.id, att.original_filename, idx, data))
    return out


# ── 핵심 entry ──────────────────────────────────────────────────────────────
async def extract_receipts(
    attachment_ids: list[int],
    *,
    expense_year: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[DraftReceipt]:
    """첨부 영수증 이미지들을 비전 OCR → 규칙 파서로 초안 라인 추출.

    Args:
        attachment_ids: 영수증 첨부 ID(입력/표시 순서 보존).
        expense_year: 연도 보정값(비전 연도 오독 교정). None 이면 보정 안 함.
        on_progress: 콜백(done_count, total) — 진행 표시용(선택). 완료 순으로 호출.

    Returns:
        DraftReceipt 리스트 — attachment_ids 순서대로 정렬. amount 추출 실패 건도 포함
        (ok=False) 하여 호출자가 '수동 입력 필요'로 노출 가능(무음 누락 방지).
    """
    async with SessionLocal() as db:
        fetched = await _fetch_receipt_bytes(db, attachment_ids)

    if not fetched:
        return []

    total = len(fetched)
    done = 0
    sem = asyncio.Semaphore(_VISION_CONCURRENCY)
    # connect 만 짧게, OCR 생성은 수십초 가능 → read timeout 없음.
    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    results: list[DraftReceipt | None] = [None] * total

    async with httpx.AsyncClient(timeout=timeout) as client:

        async def _one(slot: int, fr: _FetchedReceipt) -> None:
            nonlocal done
            async with sem:
                try:
                    lines = await _vision_ocr(client, fr.data)
                    parsed = _parse_ocr_lines(lines, expense_year=expense_year)
                except Exception as e:
                    logger.warning("[vision] OCR 실패 id=%s: %s", fr.attachment_id, e)
                    lines, parsed = [], {
                        "amount": None, "date": None,
                        "vendor": None, "category_guess": "해당없음",
                    }
            results[slot] = DraftReceipt(
                attachment_id=fr.attachment_id,
                filename=fr.filename,
                order_index=fr.order_index,
                amount=parsed["amount"],
                date=parsed["date"],
                vendor=parsed["vendor"],
                category_guess=parsed["category_guess"],
                raw_lines=lines,
            )
            done += 1
            if on_progress is not None:
                try:
                    on_progress(done, total)
                except Exception:  # 진행 콜백 실패가 추출을 막지 않도록.
                    pass

        await asyncio.gather(*(_one(i, fr) for i, fr in enumerate(fetched)))

    # 입력 순서 보존.
    return [r for r in results if r is not None]


# ── 원시 이미지 바이트 OCR(첨부/MinIO 무관) ──────────────────────────────────
async def ocr_image_batch(
    images: list[bytes],
    *,
    expense_year: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """원시 이미지 바이트 리스트를 비전 OCR → 규칙 파서로 파싱.

    `extract_receipts` 의 '바이트 직접' 버전 — 첨부 ID/MinIO 를 거치지 않고 이미
    메모리에 있는 이미지(예: 엑셀 내장 이미지)를 대조 검증에 쓰기 위함.
    동시성·타임아웃·연도 보정 정책은 `extract_receipts` 와 동일.

    Args:
        images: 이미지 바이트들(입력 순서 보존).
        expense_year: 연도 보정값(비전 연도 오독 교정). None 이면 보정 안 함.
        on_progress: 콜백(done, total) — 진행 표시용(선택). 완료 순 호출.

    Returns:
        입력 순서대로 정렬된 파싱 dict 리스트
        (각 원소: {amount, date, vendor, category_guess, raw_lines}).
        OCR 실패 건도 amount=None 으로 포함(무음 누락 방지).
    """
    total = len(images)
    if total == 0:
        return []

    done = 0
    sem = asyncio.Semaphore(_VISION_CONCURRENCY)
    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    results: list[dict | None] = [None] * total

    async with httpx.AsyncClient(timeout=timeout) as client:

        async def _one(slot: int, data: bytes) -> None:
            nonlocal done
            async with sem:
                try:
                    lines = await _vision_ocr(client, data)
                    parsed = _parse_ocr_lines(lines, expense_year=expense_year)
                except Exception as e:
                    logger.warning("[vision] batch OCR 실패 slot=%s: %s", slot, e)
                    lines, parsed = [], {
                        "amount": None, "date": None,
                        "vendor": None, "category_guess": "해당없음",
                    }
            parsed["raw_lines"] = lines
            results[slot] = parsed
            done += 1
            if on_progress is not None:
                try:
                    on_progress(done, total)
                except Exception:
                    pass

        await asyncio.gather(*(_one(i, d) for i, d in enumerate(images)))

    return [r if r is not None else {
        "amount": None, "date": None, "vendor": None,
        "category_guess": "해당없음", "raw_lines": [],
    } for r in results]


__all__ = ["DraftReceipt", "extract_receipts", "ocr_image_batch", "CATEGORIES"]
