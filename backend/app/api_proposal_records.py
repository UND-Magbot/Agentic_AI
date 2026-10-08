"""제안서·개념도 확정 라우터 — 채팅 결과 카드의 '확정' 버튼이 부른다.

- GET  /v1/proposal-records/{id}          : 상태 조회 (본인/superadmin)
- POST /v1/proposal-records/{id}/confirm  : 확정. body.revised_attachment_id 가 있으면 수정본과 함께.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from . import confirmed
from .api_auth import get_current_user
from .database import get_db
from .models import User

router = APIRouter(prefix="/v1/proposal-records", tags=["ProposalRecords"])


class ConfirmRequest(BaseModel):
    revised_attachment_id: int | None = None


class RecordResponse(BaseModel):
    id: int
    kind: str
    title: str
    status: str
    revised: bool


def _response(rec: confirmed.Record) -> RecordResponse:
    return RecordResponse(id=rec.id, kind=rec.kind, title=rec.title, status=rec.status,
                          revised=bool(rec.revised_text))


@router.get("/{record_id}", response_model=RecordResponse)
async def get_record(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rec = await confirmed.get_record(db, record_id)
    if rec is None or (rec.user_id != current_user.id and current_user.role.value != "superadmin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="결과를 찾을 수 없습니다.")
    return _response(rec)


@router.post("/{record_id}/confirm", response_model=RecordResponse)
async def confirm_record(
    record_id: int,
    body: ConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        rec = await confirmed.confirm(
            db, record_id=record_id, user_id=current_user.id,
            is_superadmin=current_user.role.value == "superadmin",
            revised_attachment_id=body.revised_attachment_id,
        )
    except confirmed.RecordError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return _response(rec)
