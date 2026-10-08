"""영업 건 관리 API (sales_deals/). 한 줄이 한 건 — 견적 → 수주 → 청구(세금계산서·입금) → 출하.

- GET  /v1/sales-deals                          → 리스트(최신 건이 위) + 단계별 개수
- GET  /v1/sales-deals/{id}                     → 한 건 + 변경 이력
- POST /v1/sales-deals                          → 새 견적 건 {customer, owner, quote_date, items[], vat_included, pay_terms_text, note}
- POST /v1/sales-deals/{id}/won                 → 수주 상태 {won: true(수주)|null(미확인), date?} — 못 간 건은 /drop
- POST /v1/sales-deals/{id}/invoices            → 세금계산서 발행 기록 {date, supply, vat?, issuer_note?}
- POST /v1/sales-deals/{id}/payments            → 입금 확인 {date, amount, full, note?}
- POST /v1/sales-deals/{id}/paid-full           → 완납 표시 바꾸기 {full}
- POST /v1/sales-deals/{id}/shipments           → 출하 기록 {date, carrier, tracking, items, receiver, address, purpose} — 입금 확인 없이도
- POST /v1/sales-deals/delete                   → 리스트에서 체크한 건들 삭제 {ids} — 지운 건은 sales_deal_trash 에 보관
- POST /v1/sales-deals/{id}/remove              → 잘못 넣은 계산서·입금·출하·발주서 한 줄 삭제 {kind, index}
- POST /v1/sales-deals/{id}/po                  → 발주서 파일 첨부(multipart file) — 수주 미확인이면 수주로
- GET  /v1/sales-deals/{id}/po/{index}          → 발주서 파일 보기·내려받기
- POST /v1/sales-deals/{id}/review              → 이관 확인 완료/되돌리기 {done}
- GET  /v1/sales-deals/photos/{name}            → 이관한 출하 사진(줄인 사본)
수주 뒤 진행(사용자 2026-10-07 — sales_deals/flow.py): 거래명세서 발급 → 결제 방식 → 입금 확인(은행 거래내역) → 출하(제품별 사진)
- POST /v1/sales-deals/{id}/history/rollback     → 히스토리 되돌리기(관리자) {keep_id} — 그 줄 때 내용·번호로
- GET  /v1/sales-deals/{id}/statement/draft      → 거래명세서 발급 창에 채울 값(견적 품목·기억한 공급받는자)
- POST /v1/sales-deals/{id}/statement            → 거래명세서 발급(다시 하면 덮어씀) {no, date, buyer, items}
- GET  /v1/sales-deals/{id}/statement/file?fmt=pdf|xlsx → 거래명세서 내려받기
- POST /v1/sales-deals/{id}/ship-photos          → 출하 사진 한 장 올리기(multipart) → {id}
- GET  /v1/sales-deals/{id}/ship-photos/{pid}    → 출하 사진 보기
출하 받는 사람 연락처·주소가 들어 있어 영업(또는 전체) 부서 사용자만 쓸 수 있다.
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse
import uuid
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .api_auth import get_current_user
from .database import get_db
from .models import User, UserDomain, UserRole
from .sales_deals import service as svc
from .storage import get_object_stream, put_object, remove_object

router = APIRouter(prefix="/v1/sales-deals", tags=["SalesDeals"])

PHOTO_DIR = Path(__file__).resolve().parent / "sales_deals" / "data" / "photos"
PHOTO_NAME = re.compile(r"^ship_r\d+_\d+\.jpg$")
MONEY_MAX = 100_000_000_000  # 1,000억 — 오타(0 몇 개 더) 방지
PO_MAX_BYTES = 20 * 1024 * 1024
# 발주서로 오는 형식만 — MIME 은 브라우저가 보낸 값 대신 확장자로 정한다
PO_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xls": "application/vnd.ms-excel", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".doc": "application/msword", ".hwp": "application/x-hwp", ".hwpx": "application/haansofthwpx"}
PO_INLINE = {"application/pdf", "image/png", "image/jpeg", "image/webp"}   # 브라우저에서 바로 볼 수 있는 형식


def require_sales(user: User = Depends(get_current_user)) -> User:
    if user.role == UserRole.superadmin or user.domain in (UserDomain.sales, UserDomain.all):
        return user
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="영업부만 볼 수 있습니다.")


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    unit_price: float | None = Field(default=None, ge=0, le=MONEY_MAX)
    qty: float | None = Field(default=None, gt=0, le=1_000_000)


class DealIn(BaseModel):
    customer: str = Field(min_length=1, max_length=200)
    contact: str | None = Field(default=None, max_length=200)
    owner: str | None = Field(default=None, max_length=50)
    title: str | None = Field(default=None, max_length=200)
    quote_date: dt.date
    items: list[ItemIn] = Field(min_length=1, max_length=50)
    vat_included: bool = False
    currency: Literal["KRW", "USD"] = "KRW"
    pay_terms_text: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=2000)


class WonIn(BaseModel):
    won: bool | None
    date: dt.date | None = None


class InvoiceIn(BaseModel):
    date: dt.date
    supply: float = Field(gt=0, le=MONEY_MAX)
    vat: float | None = Field(default=None, ge=0, le=MONEY_MAX)
    issuer_note: str | None = Field(default=None, max_length=200)


class PaymentIn(BaseModel):
    date: dt.date
    amount: float = Field(gt=0, le=MONEY_MAX)
    full: bool = False
    note: str | None = Field(default=None, max_length=300)


class PaidFullIn(BaseModel):
    full: bool


class ShipLineIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    qty: float | None = Field(default=None, ge=0, le=1_000_000)
    qty_text: str | None = Field(default=None, max_length=30)      # 세트로 묶어 보낼 때 '1 SET'(예전 출하 시트 표기)
    photos: list[str] = Field(default_factory=list, max_length=20)


class ShipmentIn(BaseModel):
    date: dt.date
    carrier: str | None = Field(default=None, max_length=50)
    tracking: str | None = Field(default=None, max_length=300)
    items: str | None = Field(default=None, max_length=1000)
    qty_text: str | None = Field(default=None, max_length=50)
    receiver: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    purpose: Literal["판매", "개발", "샘플", "기타"] = "판매"
    force: bool = False
    lines: list[ShipLineIn] = Field(default_factory=list, max_length=50)    # 제품별 출하 줄(사진 포함)


class OrderItemIn(BaseModel):
    name: str = Field(default="", max_length=80)
    spec: str = Field(default="", max_length=80)
    qty: float | None = Field(default=None, ge=0, le=1_000_000)
    unit_price: float | None = Field(default=None, ge=0, le=MONEY_MAX)
    note: str = Field(default="", max_length=60)
    unit: str = Field(default="", max_length=10)


class StatementItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    spec: str = Field(default="", max_length=80)
    qty: float = Field(gt=0, le=1_000_000)
    unit_price: float = Field(ge=0, le=MONEY_MAX)
    note: str = Field(default="", max_length=60)
    unit: str = Field(default="", max_length=10)            # 'SET' — 세트로 묶어서 발급할 때(수량 칸에 '1 SET')


class StatementBuyerIn(BaseModel):
    reg_no: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    ceo: str = Field(default="", max_length=50)
    address: str = Field(min_length=1, max_length=200)


class StatementIn(BaseModel):
    no: str = Field(default="", max_length=40)
    date: dt.date
    buyer: StatementBuyerIn
    items: list[StatementItemIn] = Field(min_length=1, max_length=30)
    parts: list[OrderItemIn] = Field(default_factory=list, max_length=30)   # 세트로 묶었을 때 원래 품목(참고·되돌리기용)


class RemoveIn(BaseModel):
    kind: Literal["invoices", "payments", "shipments", "po_files"]
    index: int = Field(ge=0)


class ReviewIn(BaseModel):
    done: bool = True


class DeleteIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=svc.DELETE_MAX)


def _fail(e: svc.DealError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))


def is_admin(user: User) -> bool:
    """영업 관리자(도메인 관리자)·최고 관리자 — 삭제·되돌리기·취소처럼 기록을 지우는 일은 이들만(사용자 2026-10-08)."""
    return user.role in (UserRole.superadmin, UserRole.domain_admin)


def require_admin(user: User = Depends(require_sales)) -> User:
    if is_admin(user):
        return user
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="관리자만 할 수 있습니다.")


@router.get("")
async def list_deals(user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    # me.admin — 화면이 관리자 전용 버튼(삭제·되돌리기·취소)을 숨기는 데 쓴다(실제 막는 것은 각 API)
    return {**await svc.list_deals(db, dt.date.today()), "me": {"admin": is_admin(user), "name": user.alias or user.username}}


@router.get("/export")
async def export_deals(_: User = Depends(require_sales), db: AsyncSession = Depends(get_db)):
    """제품 영업 건 관리 리스트 전체 → 엑셀(사용자 2026-10-08). 금액은 공급가액."""
    from fastapi.responses import Response

    from .sales_deals import export

    data = export.build_xlsx(await svc.list_deals(db, dt.date.today()))
    name = f"제품영업건_{dt.date.today():%Y%m%d}.xlsx"
    return Response(content=data, media_type=PO_TYPES[".xlsx"], headers={
        "Content-Disposition": f"attachment; filename=\"deals.xlsx\"; filename*=UTF-8''{urllib.parse.quote(name, safe='')}",
        "Cache-Control": "private, no-store"})


class InitialsIn(BaseModel):
    name: str | None = Field(default=None, max_length=50)          # 없으면 로그인한 사람(제품 추천 확정의 담당자와 같게)
    initials: str = Field(min_length=2, max_length=4)


@router.get("/initials")
async def list_initials(_: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    from sqlalchemy import text

    rs = (await db.execute(text("SELECT name, initials FROM sales_initials ORDER BY name"))).mappings().all()
    return {"items": [dict(r) for r in rs]}


@router.post("/initials")
async def set_initials(body: InitialsIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """담당자 건 번호 이니셜 — 처음 정하는 것은 누구나, 이미 있는 이니셜을 바꾸는 것은 관리자만."""
    try:
        await svc.set_initials(db, (body.name or "").strip() or user.alias or user.username, body.initials, overwrite=is_admin(user))
    except svc.DealError as e:
        raise _fail(e) from e
    return await list_initials(user, db)


@router.get("/photos/{name}")
async def photo(name: str, _: User = Depends(require_sales)) -> FileResponse:
    path = PHOTO_DIR / name
    if not PHOTO_NAME.match(name) or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="사진을 찾을 수 없습니다.")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/delete")
async def delete_deals(body: DeleteIn, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    """리스트에서 체크한 건들 삭제 — 지운 건은 sales_deal_trash 에 보관(되살릴 수 있게)."""
    try:
        nos = await svc.delete_deals(db, body.ids, user.id)
    except svc.DealError as e:
        await db.rollback()
        raise _fail(e) from e
    return {"deleted": len(nos), "deal_nos": nos}


@router.get("/{deal_id}")
async def get_deal(deal_id: int, _: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await svc.get_deal(db, deal_id, dt.date.today())
    except svc.DealError as e:
        raise _fail(e) from e


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_deal(body: DealIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        new_id = await svc.create_deal(db, user.id, body.model_dump())
    except svc.DealError as e:
        await db.rollback()
        raise _fail(e) from e
    return await svc.get_deal(db, new_id, dt.date.today())


async def _act(db: AsyncSession, deal_id: int, coro) -> dict:
    try:
        await coro
    except svc.DealError as e:
        await db.rollback()
        raise _fail(e) from e
    return await svc.get_deal(db, deal_id, dt.date.today())


@router.post("/{deal_id}/won")
async def set_won(deal_id: int, body: WonIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    if body.won is False:                                        # 실주는 없앴다 — 수주까지 못 간 건은 드랍(사용자 2026-10-08)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="수주까지 못 간 건은 [드랍]으로 처리해 주세요.")
    if body.won is None and not is_admin(user):                  # 수주를 미확인으로 되돌리기는 관리자만
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="미확인으로 되돌리기는 관리자만 할 수 있습니다.")
    return await _act(db, deal_id, svc.set_won(db, deal_id, user.id, body.won, body.date))


class TermIn(BaseModel):
    label: str = Field(min_length=1, max_length=10)
    pct: float = Field(gt=0, le=100)
    when: str | None = Field(default=None, max_length=30)   # 시점 — 자유 글(예: 발주 시), 비워도 됨(사용자 2026-10-08)
    expected_date: dt.date | None = None


class TermsIn(BaseModel):
    terms: list[TermIn] = Field(min_length=1, max_length=6)


class TermConfirmIn(BaseModel):
    index: int = Field(ge=0, le=10)
    date: dt.date | None = None


class MeetingIn(BaseModel):
    customer: str | None = Field(default=None, max_length=200)
    contact: str | None = Field(default=None, max_length=200)
    meeting_date: dt.date | None = None
    writer: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=50)
    robot: str | None = Field(default=None, max_length=300)
    requirements: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)


class DropIn(BaseModel):
    dropped: bool
    reason: str | None = Field(default=None, max_length=200)


@router.post("/{deal_id}/terms")
async def set_terms(deal_id: int, body: TermsIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """결제 조건 — 선금·중도금·잔금 %(합 100)·시점·예정 입금일(사용자 2026-10-08)."""
    raw = [{**t.model_dump(), "expected_date": t.expected_date.isoformat() if t.expected_date else None} for t in body.terms]
    return await _act(db, deal_id, svc.set_terms(db, deal_id, user.id, raw))


@router.post("/{deal_id}/confirm-term")
async def confirm_term(deal_id: int, body: TermConfirmIn, user: User = Depends(require_sales),
                       db: AsyncSession = Depends(get_db)) -> dict:
    """회차 입금 확인 — 회사가 따로 확인한 뒤 버튼으로."""
    return await _act(db, deal_id, svc.confirm_term(db, deal_id, user.id, body.index, body.date or dt.date.today()))


@router.post("/{deal_id}/unconfirm-term")
async def unconfirm_term(deal_id: int, body: TermConfirmIn, user: User = Depends(require_admin),
                         db: AsyncSession = Depends(get_db)) -> dict:
    """회차 입금 확인 취소 — 관리자만."""
    return await _act(db, deal_id, svc.unconfirm_term(db, deal_id, user.id, body.index))


class RollbackIn(BaseModel):
    keep_id: int = Field(gt=0)          # 이 줄 때로 돌아간다(이보다 위 줄은 모두 지움)


@router.post("/{deal_id}/history/rollback")
async def rollback_history(deal_id: int, body: RollbackIn, user: User = Depends(require_admin),
                           db: AsyncSession = Depends(get_db)) -> dict:
    """히스토리 되돌리기 — 관리자만. 최신 줄부터 고른 줄까지 지우고 그 아래 줄 때 내용·번호로(사용자 2026-10-08)."""
    return await _act(db, deal_id, svc.rollback_history(db, deal_id, body.keep_id, user.id))


@router.post("/{deal_id}/meeting")
async def set_meeting(deal_id: int, body: MeetingIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """미팅 정보 직접 입력 — AI 제품 추천 기록이 없는 건(수기 견적으로 시작)에 담당자가 적는다."""
    data = {**body.model_dump(), "meeting_date": body.meeting_date.isoformat() if body.meeting_date else None}
    return await _act(db, deal_id, svc.set_meeting(db, deal_id, user.id, data))


@router.post("/{deal_id}/drop")
async def set_drop(deal_id: int, body: DropIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """드랍(진행을 접음)은 누구나, 되살리기는 관리자만."""
    if not body.dropped and not is_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="드랍한 건을 되살리기는 관리자만 할 수 있습니다.")
    return await _act(db, deal_id, svc.set_dropped(db, deal_id, user.id, body.dropped, body.reason))


@router.post("/{deal_id}/invoices")
async def add_invoice(deal_id: int, body: InvoiceIn, user: User = Depends(require_sales),
                      db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, deal_id, svc.add_invoice(db, deal_id, user.id, body.model_dump()))


@router.post("/{deal_id}/payments")
async def add_payment(deal_id: int, body: PaymentIn, user: User = Depends(require_sales),
                      db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, deal_id, svc.add_payment(db, deal_id, user.id, body.model_dump()))


@router.post("/{deal_id}/paid-full")
async def set_paid_full(deal_id: int, body: PaidFullIn, user: User = Depends(require_sales),
                        db: AsyncSession = Depends(get_db)) -> dict:
    if not body.full and not is_admin(user):                     # 완납 해제는 관리자만
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="완납 해제는 관리자만 할 수 있습니다.")
    return await _act(db, deal_id, svc.set_paid_full(db, deal_id, user.id, body.full))


@router.post("/{deal_id}/shipments")
async def add_shipment(deal_id: int, body: ShipmentIn, user: User = Depends(require_sales),
                       db: AsyncSession = Depends(get_db)) -> dict:
    if any(not SHIP_PHOTO_ID.match(p) for ln in body.lines for p in ln.photos):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="출하 사진을 다시 올려 주세요.")
    return await _act(db, deal_id, svc.add_shipment(db, deal_id, user.id, body.model_dump()))


class ShipmentEditIn(ShipmentIn):
    index: int = Field(ge=0, le=500)


@router.post("/{deal_id}/shipment-edit")
async def edit_shipment(deal_id: int, body: ShipmentEditIn, user: User = Depends(require_sales),
                        db: AsyncSession = Depends(get_db)) -> dict:
    """이미 남긴 출하 기록 고치기 {index, …출하 입력}."""
    if any(not SHIP_PHOTO_ID.match(p) for ln in body.lines for p in ln.photos):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="출하 사진을 다시 올려 주세요.")
    p = body.model_dump()
    return await _act(db, deal_id, svc.update_shipment(db, deal_id, user.id, p.pop("index"), p))


@router.post("/{deal_id}/reset-to-quote")
async def reset_to_quote(deal_id: int, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    """(테스트용) 수주 확인 이후 기록을 모두 지우고 견적 상태로. 올린 출하 사진·발주서 파일도 저장소에서 지운다."""
    from .config import settings
    from .storage import get_client

    try:
        keys = await svc.reset_to_quote(db, deal_id, user.id)
    except svc.DealError as e:
        await db.rollback()
        raise _fail(e) from e
    try:   # 출하 사진은 기록에 안 붙은 것(작성하다 만 것)까지 이 건 폴더째
        keys += [o.object_name for o in get_client().list_objects(settings.minio_bucket, prefix=f"sales-deals/{deal_id}/ship/", recursive=True)]
        for k in keys:
            remove_object(k)
    except Exception:  # 기록은 이미 되돌렸다 — 저장소 정리 실패는 화면 동작을 막지 않는다
        pass
    return await svc.get_deal(db, deal_id, dt.date.today())


@router.post("/{deal_id}/remove")
async def remove_entry(deal_id: int, body: RemoveIn, user: User = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await svc.remove_entry(db, deal_id, user.id, body.kind, body.index)
    except svc.DealError as e:
        await db.rollback()
        raise _fail(e) from e
    # 발주서 파일은 지우지 않는다 — 히스토리 되돌리기로 그 줄이 다시 살아날 수 있어서(사용자 2026-10-08)
    return await svc.get_deal(db, deal_id, dt.date.today())


@router.post("/{deal_id}/po")
async def upload_po(deal_id: int, file: UploadFile = File(...), user: User = Depends(require_sales),
                    db: AsyncSession = Depends(get_db)) -> dict:
    """발주서(고객사 주문서) 파일 첨부. 저장소에 먼저 올리고, 기록에 실패하면 올린 파일을 지운다."""
    name = (file.filename or "발주서").strip()[:200]
    ext = PurePosixPath(name).suffix.lower()
    if ext not in PO_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail="발주서는 PDF·사진(JPG·PNG)·엑셀·워드·한글 파일만 올릴 수 있습니다.")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="빈 파일입니다.")
    if size > PO_MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="발주서 파일은 20MB 까지 올릴 수 있습니다.")
    key = f"sales-deals/{deal_id}/po/{uuid.uuid4().hex}{ext}"
    try:
        put_object(key=key, data=file.file, length=size, mime=PO_TYPES[ext])
    except Exception as e:  # pragma: no cover — 저장소 장애
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="파일 저장에 실패했습니다. 잠시 뒤 다시 시도해 주세요.") from e
    entry = {"key": key, "filename": name, "mime": PO_TYPES[ext], "size": size,
             "uploaded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "uploaded_by": user.alias or user.username}
    try:
        await svc.add_po_file(db, deal_id, user.id, entry)
    except Exception as e:
        await db.rollback()
        try:
            remove_object(key)
        except Exception:
            pass
        if isinstance(e, svc.DealError):
            raise _fail(e) from e
        raise
    return await svc.get_deal(db, deal_id, dt.date.today())


@router.post("/{deal_id}/po/{index}/compare")
async def compare_po(deal_id: int, index: int, user: User = Depends(require_sales),
                     db: AsyncSession = Depends(get_db)) -> dict:
    """발주서 ↔ 견적 품목 대조(사용자 2026-10-08) — 사내 AI 가 발주서 품목을 읽고 코드가 수량·단가를 비교해 저장한다."""
    from .sales_deals import po_compare

    try:
        d = await svc.get_deal(db, deal_id, dt.date.today())
        f = await svc.get_po_file(db, deal_id, index)
    except svc.DealError as e:
        raise _fail(e) from e
    obj = get_object_stream(f["key"])
    try:
        data = obj.read()
    finally:
        obj.close()
        obj.release_conn()
    await db.rollback()                                   # AI 판독 동안 트랜잭션을 잡아 두지 않는다
    result = await po_compare.run(data, f.get("filename") or "", f.get("mime") or "", (d.get("quote") or {}).get("items") or [])
    try:
        await svc.set_po_compare(db, deal_id, user.id, index, f["key"], result)
    except svc.DealError as e:
        await db.rollback()
        raise _fail(e) from e
    return await svc.get_deal(db, deal_id, dt.date.today())


@router.get("/{deal_id}/po/{index}")
async def download_po(deal_id: int, index: int, _: User = Depends(require_sales),
                      db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    try:
        f = await svc.get_po_file(db, deal_id, index)
    except svc.DealError as e:
        raise _fail(e) from e
    obj = get_object_stream(f["key"])
    original = f.get("filename") or "발주서"
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 and ch != '"' else "_" for ch in original) or "po"
    mode = "inline" if f.get("mime") in PO_INLINE else "attachment"
    headers = {"Content-Disposition": f"{mode}; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(original, safe='')}",
               "Cache-Control": "private, max-age=0, no-store", "X-Content-Type-Options": "nosniff"}

    def stream():
        try:
            yield from obj.stream(amt=64 * 1024)
        finally:
            obj.close()
            obj.release_conn()
    return StreamingResponse(stream(), media_type=f.get("mime") or "application/octet-stream", headers=headers)


@router.post("/{deal_id}/review")
async def set_review(deal_id: int, body: ReviewIn, user: User = Depends(require_sales),
                     db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, deal_id, svc.set_review_done(db, deal_id, user.id, body.done))


# ── 수주 뒤 진행: 거래명세서 · 결제 방식 · 입금 확인 · 출하 사진 (사용자 2026-10-07) ──────────────

SHIP_PHOTO_MAX = 15 * 1024 * 1024
SHIP_PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
SHIP_PHOTO_ID = re.compile(r"^[0-9a-f]{32}\.(jpg|jpeg|png|webp)$")


@router.get("/{deal_id}/statement/draft")
async def statement_draft(deal_id: int, _: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await svc.statement_draft(db, deal_id)
    except svc.DealError as e:
        raise _fail(e) from e


@router.post("/{deal_id}/statement")
async def set_statement(deal_id: int, body: StatementIn, user: User = Depends(require_sales),
                        db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, deal_id, svc.set_statement(db, deal_id, user.id, body.model_dump()))


@router.get("/{deal_id}/statement/file")
async def statement_file(deal_id: int, fmt: Literal["pdf", "xlsx"] = "pdf", _: User = Depends(require_sales),
                         db: AsyncSession = Depends(get_db)):
    from fastapi.responses import Response

    from .company_knowledge import quote_xlsx as qx

    try:
        name, data = await svc.statement_file(db, deal_id)
    except svc.DealError as e:
        raise _fail(e) from e
    if fmt == "pdf":
        try:
            data = await qx.to_pdf(data)
        except qx.QuoteError as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    mime = "application/pdf" if fmt == "pdf" else PO_TYPES[".xlsx"]
    full = f"{name}.{fmt}"
    return Response(content=data, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename=\"statement.{fmt}\"; filename*=UTF-8''{urllib.parse.quote(full, safe='')}",
        "Cache-Control": "private, no-store"})


@router.post("/{deal_id}/ship-photos")
async def upload_ship_photo(deal_id: int, file: UploadFile = File(...), _: User = Depends(require_sales),
                            db: AsyncSession = Depends(get_db)) -> dict:
    """출하 사진 한 장 — 저장소에 올리고 id 를 돌려준다(출하 기록을 저장할 때 제품 줄에 붙인다)."""
    try:
        await svc.get_deal(db, deal_id, dt.date.today())
    except svc.DealError as e:
        raise _fail(e) from e
    ext = PurePosixPath(file.filename or "").suffix.lower()
    if ext not in SHIP_PHOTO_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="사진은 JPG·PNG·WEBP 만 올릴 수 있습니다.")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if not 0 < size <= SHIP_PHOTO_MAX:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="사진은 15MB 까지 올릴 수 있습니다.")
    pid = f"{uuid.uuid4().hex}{ext}"
    try:
        put_object(key=f"sales-deals/{deal_id}/ship/{pid}", data=file.file, length=size, mime=SHIP_PHOTO_TYPES[ext])
    except Exception as e:  # pragma: no cover — 저장소 장애
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="사진 저장에 실패했습니다. 잠시 뒤 다시 시도해 주세요.") from e
    return {"id": pid}


@router.get("/{deal_id}/ship-photos/{pid}")
async def ship_photo(deal_id: int, pid: str, _: User = Depends(require_sales)) -> StreamingResponse:
    if not SHIP_PHOTO_ID.match(pid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="사진을 찾을 수 없습니다.")
    try:
        obj = get_object_stream(f"sales-deals/{deal_id}/ship/{pid}")
    except Exception as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="사진을 찾을 수 없습니다.") from e

    def stream():
        try:
            yield from obj.stream(amt=64 * 1024)
        finally:
            obj.close()
            obj.release_conn()
    return StreamingResponse(stream(), media_type=SHIP_PHOTO_TYPES["." + pid.rsplit(".", 1)[1]],
                             headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


# ── 수주 진행 탭 — AI 와 대화하며 수주 뒤 절차(사용자 2026-10-07, sales_deals/order_agent.py) ────────────

class OrderDraftIn(BaseModel):
    """왼쪽에서 직접 고친 명세서 초안(발급 전이라 빈 칸이 있어도 된다)."""
    no: str = Field(default="", max_length=40)
    date: dt.date
    buyer: dict[Literal["reg_no", "name", "ceo", "address"], str] = Field(default_factory=dict)
    items: list[OrderItemIn] = Field(default_factory=list, max_length=30)
    parts: list[OrderItemIn] = Field(default_factory=list, max_length=30)


class OrderAgentIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class OrderShipIn(BaseModel):
    """수주 진행 왼쪽 출하 칸에서 작성 중인 내용(대화 전에 저장 — AI 가 덮어쓰지 않게)."""
    ship: dict = Field(default_factory=dict)


class OrderNoteIn(BaseModel):
    event: Literal["issue", "payment", "ship", "case"]


_NOTE = {"issue": "거래명세서를 발급했습니다.", "payment": "입금을 확인했습니다.", "ship": "출하를 기록했습니다.",
         "case": "결제 조건을 정했습니다."}


@router.get("/{deal_id}/quote-file")
async def deal_quote_file(deal_id: int, quote: int, revision: int | None = None, fmt: Literal["pdf", "xlsx"] = "pdf",
                          _: User = Depends(require_sales), db: AsyncSession = Depends(get_db)):
    """이 건의 견적서 발행본 내려받기(영업부 누구나 — 작성자만 열 수 있는 견적서 탭과 달리). 이 건의 견적서가 아니면 404."""
    from fastapi.responses import Response

    from .company_knowledge import product_recommend as pr

    try:
        d = await svc.get_deal(db, deal_id, dt.date.today())
    except svc.DealError as e:
        raise _fail(e) from e
    if not any(q["id"] == quote for q in d.get("quotes") or []):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="이 건의 견적서가 아닙니다.")
    try:
        name, data = await pr.quote_file(quote, user_id=None, revision=revision, fmt=fmt)
    except pr.QuotePdfError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except pr.RecommendError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    mime = "application/pdf" if fmt == "pdf" else PO_TYPES[".xlsx"]
    return Response(content=data, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename=\"quote.{fmt}\"; filename*=UTF-8''{urllib.parse.quote(name, safe='')}",
        "Cache-Control": "private, no-store"})


@router.get("/{deal_id}/order")
async def order_open(deal_id: int, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    try:   # admin — 화면이 관리자 전용 버튼(입금 확인 취소)을 보일지 정한다(실제 막는 것은 각 API)
        return {**await svc.order_open(db, deal_id), "admin": is_admin(user)}
    except svc.DealError as e:
        raise _fail(e) from e


@router.post("/{deal_id}/order/draft")
async def order_draft(deal_id: int, body: OrderDraftIn, _: User = Depends(require_sales),
                      db: AsyncSession = Depends(get_db)) -> dict:
    try:
        res = await svc.order_open(db, deal_id)
        draft = body.model_dump()
        draft["date"] = body.date.isoformat()
        draft["buyer"] = {k: str(draft["buyer"].get(k) or "").strip()[:200] for k in ("reg_no", "name", "ceo", "address")}
        res["state"]["draft"] = draft
        await svc.save_order_state(db, deal_id, res["state"])
        return res
    except svc.DealError as e:
        raise _fail(e) from e


_SHIP_KEYS = ("mode", "date", "carrier", "tracking", "receiver", "address", "set_name", "set_qty", "set_photos", "lines")


@router.post("/{deal_id}/order/ship")
async def order_ship_draft(deal_id: int, body: OrderShipIn, _: User = Depends(require_sales),
                           db: AsyncSession = Depends(get_db)) -> dict:
    """작성 중인 출하 내용 저장(기록은 아직 아님 — [출하 기록 저장]에서). 알려진 칸만, 사진은 올린 사진 id 만."""
    import json as _json

    ship = {k: body.ship[k] for k in _SHIP_KEYS if k in body.ship}
    if len(_json.dumps(ship, ensure_ascii=False)) > 20_000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="출하 작성 내용이 너무 깁니다.")
    photos = [*(ship.get("set_photos") or []), *(p for ln in ship.get("lines") or [] if isinstance(ln, dict) for p in ln.get("photos") or [])]
    if any(not isinstance(p, str) or not SHIP_PHOTO_ID.match(p) for p in photos):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="출하 사진을 다시 올려 주세요.")
    try:
        res = await svc.order_open(db, deal_id)
        res["state"]["ship"] = ship
        await svc.save_order_state(db, deal_id, res["state"])
        return res
    except svc.DealError as e:
        raise _fail(e) from e


@router.post("/{deal_id}/order/note")
async def order_note(deal_id: int, body: OrderNoteIn, _: User = Depends(require_sales),
                     db: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await svc.order_note(db, deal_id, _NOTE[body.event])
    except svc.DealError as e:
        raise _fail(e) from e


@router.post("/{deal_id}/order/agent")
async def order_agent_turn(deal_id: int, body: OrderAgentIn, user: User = Depends(require_sales),
                           db: AsyncSession = Depends(get_db)):
    """수주 진행 대화 한 번 — 생각·단계·답을 한 줄에 JSON 하나씩 흘려 보낸다. 끝나면 대화 상태를 저장한다."""
    import logging

    from .company_knowledge import product_recommend as pr
    from .company_knowledge.atc_agent import ndjson
    from .proposal_llm import ProposalLLMError
    from .sales_deals import order_agent

    from .database import SessionLocal

    try:
        res = await svc.order_open(db, deal_id)
    except svc.DealError as e:
        raise _fail(e) from e

    # 흘려 보내는 동안에는 요청의 DB 세션이 이미 닫혀 있을 수 있어 따로 연다
    async def gen():
        async with SessionLocal() as sdb:
            async def set_case(terms: list) -> None:     # 대화에서 받은 결제 조건 저장(사용자 2026-10-08)
                try:
                    await svc.set_terms(sdb, deal_id, user.id, terms)
                except svc.DealError:
                    await sdb.rollback()
                    raise
            try:
                async for ev in order_agent.agent_turn(res["deal"], res["state"], body.message.strip(),
                                                       stream=pr._default_stream(), set_case=set_case):
                    if ev["t"] == "done":
                        await svc.save_order_state(sdb, deal_id, ev["state"])
                        ev = {**ev, **(await svc.order_open(sdb, deal_id))}
                    yield ndjson(ev)
            except ProposalLLMError as e:
                yield ndjson({"t": "error", "text": str(e)})
            except Exception:                               # 흘려 보내는 중이라 HTTP 오류로 못 바꾼다 — 화면에 알리고 로그
                logging.getLogger("order_agent").exception("order agent 실패")
                yield ndjson({"t": "error", "text": "답을 만들지 못했습니다. 잠시 뒤 다시 말씀해 주세요."})

    return StreamingResponse(gen(), media_type="application/x-ndjson; charset=utf-8",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/{deal_id}/order/preview")
async def order_preview(deal_id: int, _: User = Depends(require_sales), db: AsyncSession = Depends(get_db)):
    """발급 전 거래명세서 미리보기(PDF, 파일로 남기지 않음) — 저장한 초안 그대로, 빈 칸은 빈 채로."""
    from fastapi.responses import Response

    from .company_knowledge import quote_xlsx as qx
    from .sales_deals import statement_xlsx as sx

    try:
        draft = (await svc.order_open(db, deal_id))["state"].get("draft") or {}
    except svc.DealError as e:
        raise _fail(e) from e
    if not draft.get("date"):
        draft = {**draft, "date": dt.date.today().isoformat()}
    try:
        data = await qx.to_pdf(sx.build_xlsx(draft, strict=False))
    except qx.QuoteError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="preview.pdf"', "Cache-Control": "no-store"})
