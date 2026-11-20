"""제품 재고 관리 API (purchases/stock.py, 사용자 2026-11-19) — 구매 입고 기록에서 제품 한 대 = 한 줄.

- GET  /v1/stock?purpose=sale|dev   → 목록 + 목록별 개수 + 구분·상태 선택지
- POST /v1/stock/{id}               → 구분·상태(+메모)·자체 개발용 영업팀 담당자·표시 이름 고치기
                                        {category?, status?, status_note?, owner?, display_name?, maker?}
- POST /v1/stock/bulk               → 여러 줄 한 번에 {ids, category?, status?, status_note?} — 판매용 묶음 줄('N개를 사용 중으로')
- GET  /v1/stock/{id}/history       → 변경 이력
- POST /v1/stock/{id}/photos        → 사진 넣고 빼기 {add?, remove?, scope: line|unit, kind?: item|sn} — 입고 기록에 저장
- POST /v1/stock/direct/draft       → [제품 등록] 시작 — 작성 중인 빈 직접 등록 건 id(사진을 먼저 올리려고)
- GET  /v1/stock/direct/{pid}       → 직접 등록 건(고치기 화면 값 — 구매 건 보기와 같은 꼴)
- POST /v1/stock/direct/{pid}       → 직접 등록 저장·고치기 {purpose, date, supplier, region, location, note, lines}
- POST /v1/stock/direct/{pid}/delete → 직접 등록 지우기(관리자, 등록 전 빈 건은 누구나)
- GET  /v1/stock/export             → 재고 엑셀(판매용·보유 후 자체 개발용 두 시트, 재고 번호 열, 상태·상태 메모 칸) — 사용자 2026-11-20
- POST /v1/stock/import/preview     → 고친 재고 엑셀 올리기 → 바뀌는 줄 미리보기(저장 안 함, 번호 → S/N → AI 추정 순으로 짝)
- POST /v1/stock/import/apply       → 미리보기에서 고른 줄만 반영 {items: [{id, status, status_note}]}
권한은 구매 건 관리와 같다(영업 또는 전체 부서 누구나).
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .api_purchases import ReceiptLineIn, require_category
from .api_sales_deals import is_admin, require_sales
from .database import get_db
from .models import User
from .purchases import service as psvc
from .purchases import stock
from .purchases import stock_excel

router = APIRouter(prefix="/v1/stock", tags=["Stock"])


class StockPatchIn(BaseModel):
    category: str | None = Field(default=None, max_length=20)
    status: str | None = Field(default=None, max_length=20)
    status_note: str | None = Field(default=None, max_length=500)
    owner: str | None = Field(default=None, max_length=100)
    display_name: str | None = Field(default=None, max_length=200)   # 재고 표시 이름(비우면 발주서 품목명)
    maker: str | None = Field(default=None, max_length=100)          # 제조사(비우면 구매 공급사)
    customer: str | None = Field(default=None, max_length=200)       # 판매 고객사(판매용) — 입고 기록을 다시 저장하면 그 값으로
    sw_owner: str | None = Field(default=None, max_length=100)       # S/W 담당자(자체 개발용) — 위와 같이 입고 기록 값으로 돌아감
    received_on: str | None = Field(default=None, max_length=10)     # 입고일 YYYY-MM-DD


class StockBulkIn(StockPatchIn):
    ids: list[int] = Field(min_length=1, max_length=stock.BULK_MAX)


def _fail(e: stock.StockError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXCEL_MAX_BYTES = 10 * 1024 * 1024


@router.get("/export")
async def export_stock(_: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> Response:
    data = await stock_excel.export(db)
    name = f"공장제품재고_{psvc.kst_today().strftime('%Y%m%d')}.xlsx"
    from urllib.parse import quote
    return Response(data, media_type=XLSX_MIME, headers={
        "Content-Disposition": f"attachment; filename=\"stock.xlsx\"; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/import/preview")
async def import_preview(file: UploadFile = File(...), _: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="엑셀 파일(.xlsx)만 올릴 수 있습니다.")
    data = await file.read(EXCEL_MAX_BYTES + 1)
    if len(data) > EXCEL_MAX_BYTES:
        raise HTTPException(status_code=413, detail="엑셀 파일이 너무 큽니다(10MB 까지).")
    try:
        return await stock_excel.preview(db, data)
    except stock_excel.ExcelError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e


class ImportPickIn(BaseModel):
    id: int = Field(gt=0)
    status: str = Field(max_length=20)
    status_note: str = Field(default="", max_length=500)


class ImportApplyIn(BaseModel):
    items: list[ImportPickIn] = Field(min_length=1, max_length=stock.BULK_MAX)


@router.post("/import/apply")
async def import_apply(body: ImportApplyIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return await stock_excel.apply(db, user.id, [x.model_dump() for x in body.items])


@router.get("")
async def list_stock(purpose: Literal["sale", "dev"] = "sale", user: User = Depends(require_sales),
                     db: AsyncSession = Depends(get_db)) -> dict:
    return {**await stock.list_items(db, purpose), "me": {"admin": is_admin(user)}}


class DirectIn(BaseModel):
    """재고 직접 등록 — 입고 기록과 같은 꼴(품목은 직접 적은 줄만) + 제조사(구매처)·국내/해외"""
    purpose: Literal["sale", "dev"]
    date: dt.date
    supplier: str = Field(default="", max_length=200)
    region: Literal["domestic", "overseas"] = "domestic"
    carrier: str = Field(default="", max_length=50)
    tracking: str = Field(default="", max_length=300)
    receiver: str = Field(default="", max_length=100)
    location: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=500)
    lines: list[ReceiptLineIn] = Field(default_factory=list, max_length=100)


def _pfail(e: psvc.PurchaseError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))


@router.post("/direct/draft")
async def direct_draft(user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return {"id": await psvc.direct_draft(db, user.id, user.alias or user.username)}


@router.get("/direct/{pid}")
async def direct_get(pid: int, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        d = await psvc.raw(db, pid)
        if not d.get("direct"):
            raise psvc.PurchaseError("재고 직접 등록 건이 아닙니다.", 404)
        return await psvc.get(db, pid, psvc.kst_today())
    except psvc.PurchaseError as e:
        raise _pfail(e) from e


@router.post("/direct/{pid}")
async def direct_save(pid: int, body: DirectIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    require_category(body.lines)
    try:
        await psvc.save_direct(db, pid, user.id, user.alias or user.username, body.model_dump())
    except psvc.PurchaseError as e:
        await db.rollback()
        raise _pfail(e) from e
    return {"ok": True, "id": pid}


@router.post("/direct/{pid}/delete")
async def direct_delete(pid: int, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        d = await psvc.raw(db, pid)
        if not d.get("direct"):
            raise psvc.PurchaseError("재고 직접 등록 건이 아닙니다.", 404)
        if (d.get("receipt") or {}).get("records") and not is_admin(user):
            raise psvc.PurchaseError("등록한 제품을 통째로 지우는 것은 관리자만 할 수 있습니다. 품목을 고치려면 [고치기]를 쓰세요.", 403)
        await psvc.delete_direct(db, pid)
    except psvc.PurchaseError as e:
        await db.rollback()
        raise _pfail(e) from e
    return {"ok": True}


@router.post("/bulk")
async def update_stock_bulk(body: StockBulkIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        n = await stock.update_items(db, body.ids, user.id, body.model_dump(exclude={"ids"}))
    except stock.StockError as e:
        await db.rollback()
        raise _fail(e) from e
    return {"ok": True, "count": n}


@router.post("/{item_id}")
async def update_stock(item_id: int, body: StockPatchIn, user: User = Depends(require_sales),
                       db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await stock.update_item(db, item_id, user.id, body.model_dump())
    except stock.StockError as e:
        await db.rollback()
        raise _fail(e) from e
    return {"ok": True}


class StockPhotosIn(BaseModel):
    add: list[str] = Field(default_factory=list, max_length=50)
    remove: list[str] = Field(default_factory=list, max_length=50)
    scope: Literal["line", "unit"] = "line"
    kind: Literal["item", "sn"] = "item"


@router.post("/{item_id}/photos")
async def stock_photos(item_id: int, body: StockPhotosIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await stock.edit_photos(db, item_id, user.id, body.model_dump())
    except stock.StockError as e:
        await db.rollback()
        raise _fail(e) from e
    return {"ok": True}


@router.get("/{item_id}/history")
async def stock_history(item_id: int, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return {"history": await stock.history(db, item_id)}
