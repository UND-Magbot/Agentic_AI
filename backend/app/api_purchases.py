"""제품 구매 관리 API (purchases/, 사용자 2026-11-02). 한 줄이 한 구매 건 — 견적 → 재가 → 발주 → 송금·입고·거래명세서·세금계산서.

- GET  /v1/purchases                              → 리스트(최근 바뀐 건이 위) + 상태별 개수 + me.admin
- POST /v1/purchases                              → 새 구매 건 {supplier, title, owner, note}
- POST /v1/purchases/delete                       → 관리자 — 체크한 건 지우기 {ids}
- GET  /v1/purchases/{id}                         → 한 건 + 변경 이력
- POST /v1/purchases/{id}/info                    → 기본 정보 고치기 {supplier, title, owner, note}
- POST /v1/purchases/{id}/drop                    → 구매 취소 {dropped, reason} (되살리기는 관리자)
- POST /v1/purchases/{id}/files/{kind}            → 파일 첨부(multipart) kind = quote(교체)·approval·statement·tax·customs(원산지)
                                                     ·remit(제조사 송금 확인증)·customs_etc(통관 기타 서류, form label=서류 이름)
- GET  /v1/purchases/{id}/files/{kind}/{n}        → 파일 보기·내려받기(kind 에 expense = 회차 n 의 지출내역)
- POST /v1/purchases/{id}/files/{kind}/{n}/remove → 첨부 한 개 지우기(끝난 단계·견적서는 관리자)
- POST /v1/purchases/{id}/quote/skip              → [견적서 없이 넘어가기] {value} (false = 되돌리기, 재가 전까지)
- POST /v1/purchases/{id}/approve                 → 재가 완료 {date} — 첨부 없이도 됨(no_file 표시) / unapprove(관리자)
- GET  /v1/purchases/{id}/po/draft                → 발주서 작성 창 값(초안 → 마지막 발행본 → 기본값)
- POST /v1/purchases/{id}/po/draft                → 작성 중 저장 {initials, header, items}
- POST /v1/purchases/{id}/po/read-quote           → 지금 견적서를 AI 가 읽어 품목·공급사 담당자 돌려줌(저장 안 함)
- POST /v1/purchases/{id}/po/preview              → 미리보기 PDF {header, items}(번호 없음)
- POST /v1/purchases/{id}/po/issue                → 발주서 발행 {initials, header, items} — 새 번호 PO-{이니셜}{발주일}-NN
- GET  /v1/purchases/{id}/po/file?fmt=pdf|xlsx&revision= → 발행한 발주서 내려받기(고친 파일로 발행했으면 그 파일)
- POST /v1/purchases/{id}/po/chat                 → 발주서 AI 대화 {text, draft} — 말로 고친 초안을 저장하고 돌려줌(사용자 2026-11-11)
- POST /v1/purchases/{id}/po/upload               → 고친 발주서 파일(xlsx·pdf) 올리기 — 저장하고 번호·날짜·품목을 읽어 돌려줌(발행 전)
- POST /v1/purchases/{id}/po/upload/confirm       → 확인한 값으로 발행 {upload, doc_no, initials, date, expected_date, items}
- POST /v1/purchases/{id}/terms                   → 결제 조건 {terms:[{label, pct, when, expected_date}]}
- POST /v1/purchases/{id}/terms/{n}/check         → 지출내역 문서(multipart)와 회차 금액 대조
- POST /v1/purchases/{id}/terms/{n}/confirm       → 송금 확인 {date, mismatch_note?} — 불일치면 사유를 적고 넘어감
- POST /v1/purchases/{id}/terms/{n}/unconfirm     → 송금 확인 취소(누구나 — 금액이 바뀔 수 있어서, 이력에 남음)
- POST /v1/purchases/{id}/expected-date           → 입고 예정일 바꾸기 {date, reason}(발행할 때 꼭 적음 — po/issue 의 expected_date)
- POST /v1/purchases/{id}/paid · /paid/undo        → 해외 [제조사 송금 확인] {date, note} · 취소(누구나)
- POST /v1/purchases/{id}/ship · /ship/undo(관리자) → [배송 시작] {date, note} → 상태 '배송 중'
- POST /v1/purchases/{id}/customs                 → 해외 [통관 서류 제출 완료] {date, note} — 첨부 없이도 됨
- POST /v1/purchases/{id}/customs/no-origin       → 원산지 증명서 [증빙 자료 없음] {value} — 기타 서류 칸이 열림
- POST /v1/purchases/{id}/broker · /broker/undo    → 해외 [관세사 송금 확인] {date, note} · 취소(누구나)
- POST /v1/purchases/{id}/receipt                 → 입고 기록 {date, carrier, tracking, receiver, location, note, lines[{line_no, qty, photos, serials}], final}
- POST /v1/purchases/{id}/receipt/{n}/serials     → 남긴 입고 기록에 S/N 만 등록·고치기 {lines:[{line_no, serials}]}
- POST /v1/purchases/{id}/receipt/{n}/edit · /remove(관리자) → 입고 기록 고치기·지우기
- POST /v1/purchases/{id}/receipt-photos · GET /receipt-photos/{name} → 입고 사진
- POST /v1/purchases/{id}/statement · /tax        → 거래명세서·세금계산서를 못 끊는 사유 기록 {mode: prepaid|pre_issued, date, note}
- POST /v1/purchases/{id}/undo/{step}             → 되돌리기 — 통관 서류 제출(관리자), 명세서·계산서 사유 기록(누구나)
권한은 영업 건 관리와 같다(영업 또는 전체 부서, 지우기·취소는 관리자).
"""
from __future__ import annotations

import datetime as dt
import urllib.parse
import uuid
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .api_sales_deals import PO_INLINE, PO_MAGIC, PO_MAX_BYTES, PO_TYPES, is_admin, require_admin, require_sales
from .database import get_db
from .models import User
from .purchases import service as svc
from .storage import get_object_stream, put_object, remove_object
from .user_profile import get_profile

router = APIRouter(prefix="/v1/purchases", tags=["Purchases"])

FILE_TYPES = {**PO_TYPES, ".csv": "text/csv"}           # 지출내역은 CSV 로도 온다
ALL_KINDS = (*svc.FILE_KINDS, "expense")


def _fail(e: svc.PurchaseError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))


def _name(user: User) -> str:
    return user.alias or user.username


async def _act(db: AsyncSession, pid: int, coro) -> dict:
    try:
        await coro
    except svc.PurchaseError as e:
        await db.rollback()
        raise _fail(e) from e
    except ValueError as e:                               # 결제 조건 검사(flow.clean_terms)
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return await svc.get(db, pid, svc.kst_today())


async def _store(pid: int, kind: str, file: UploadFile, user: User) -> dict:
    """올린 파일 검사(형식·크기·파일 첫머리) → 저장소 purchases/{id}/{kind}/… → 기록할 항목."""
    name = (file.filename or kind).strip()[:200]
    ext = PurePosixPath(name).suffix.lower()
    if ext not in FILE_TYPES or (ext == ".csv" and kind != "expense"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail="PDF·사진(JPG·PNG)·엑셀·워드·한글 파일만 올릴 수 있습니다" + ("(지출내역은 CSV 도)." if kind == "expense" else "."))
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="빈 파일입니다.")
    if size > PO_MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="파일은 20MB 까지 올릴 수 있습니다.")
    head = file.file.read(16)
    file.file.seek(0)
    if ext != ".csv" and (not head.startswith(PO_MAGIC[ext]) or (ext == ".webp" and head[8:12] != b"WEBP")):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=f"파일 내용이 {ext} 형식이 아닙니다. 원본 파일을 올려 주세요.")
    key = f"purchases/{pid}/{kind}/{uuid.uuid4().hex}{ext}"
    try:
        put_object(key=key, data=file.file, length=size, mime=FILE_TYPES[ext])
    except Exception as e:  # pragma: no cover — 저장소 장애
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="파일 저장에 실패했습니다. 잠시 뒤 다시 시도해 주세요.") from e
    return {"key": key, "filename": name, "mime": FILE_TYPES[ext], "size": size,
            "uploaded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "uploaded_by": _name(user)}


def _drop_object(key: str) -> None:
    try:
        remove_object(key)
    except Exception:
        pass


def _read_object(key: str) -> bytes:
    obj = get_object_stream(key)
    try:
        return obj.read()
    finally:
        obj.close()
        obj.release_conn()


def _disposition(mode: str, original: str) -> str:
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 and ch != '"' else "_" for ch in original) or "file"
    return f"{mode}; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(original, safe='')}"


# ── 입력 모델 ──────────────────────────────────────────────────────────────────

class PurchaseIn(BaseModel):
    supplier: str = Field(min_length=1, max_length=200)
    region: Literal["domestic", "overseas"] | None = None      # 국내·해외(사용자 2026-11-02) — 고치기에서 비우면 그대로
    title: str = Field(default="", max_length=300)
    owner: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=2000)


class DeleteIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class DropIn(BaseModel):
    dropped: bool
    reason: str = Field(default="", max_length=500)


class DayIn(BaseModel):
    date: dt.date


class PoIn(BaseModel):
    initials: str = Field(default="", max_length=4)
    expected_date: str | None = Field(default=None, max_length=10)     # 입고 예정일 — 발행할 때 꼭(사용자 2026-11-02)
    header: dict = Field(default_factory=dict)
    items: list[dict] = Field(default_factory=list, max_length=31)


class PoChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    draft: PoIn


class PoUploadIn(BaseModel):
    """고친 발주서 파일로 발행(사용자 2026-11-11) — upload 는 /po/upload 가 돌려준 저장 기록 그대로."""
    upload: dict
    doc_no: str = Field(default="", max_length=60)        # 파일의 Order No.(비우면 새 번호 — initials 필요)
    initials: str = Field(default="", max_length=4)
    date: str | None = Field(default=None, max_length=10)
    expected_date: str | None = Field(default=None, max_length=10)
    items: list[dict] = Field(default_factory=list, max_length=31)
    header: dict = Field(default_factory=dict)            # 파일에서 읽은 머리(공급사·계좌·Comments) — 빈 값은 지금 머리 유지
    # [버그 신고 후 넘어가기](사용자 2026-11-11 — AI 판독이 실패하거나 이상해도 [버그 신고 후 넘어가기]로 이 파일 그대로 발행하고 다음 단계로) — 품목 검사를 건너뛰고 발행, bug_report = 접수 번호(UND-00012)
    forced: bool = False
    bug_report: str = Field(default="", max_length=20)


class TermIn(BaseModel):
    label: str = Field(max_length=10)
    pct: float
    when: str = Field(default="", max_length=30)
    expected_date: str | None = Field(default=None, max_length=10)


class TermsIn(BaseModel):
    terms: list[TermIn] = Field(min_length=1, max_length=10)


class ConfirmIn(BaseModel):
    date: dt.date
    mismatch_note: str | None = Field(default=None, max_length=500)


class ReceiptLineIn(BaseModel):
    line_no: int = Field(ge=0, le=40)
    qty: float | None = Field(default=None, ge=0, le=1_000_000)
    photos: list[str] = Field(default_factory=list, max_length=10)
    serials: list[str] = Field(default_factory=list, max_length=500)     # S/N(사용자 2026-11-11 — 출하처럼 입고 때 등록)


class SerialLineIn(BaseModel):
    line_no: int = Field(ge=0, le=40)
    serials: list[str] = Field(default_factory=list, max_length=500)


class SerialsIn(BaseModel):
    lines: list[SerialLineIn] = Field(min_length=1, max_length=31)


class FlagIn(BaseModel):
    value: bool


class ReceiptIn(BaseModel):
    """입고 기록(출하 기록과 같은 꼴, 사용자 2026-11-02) — 품목 줄별 수량·사진, final = 이번 입고로 입고 완료."""
    date: dt.date
    carrier: str = Field(default="", max_length=50)
    tracking: str = Field(default="", max_length=300)
    receiver: str = Field(default="", max_length=100)
    location: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=500)
    lines: list[ReceiptLineIn] = Field(default_factory=list, max_length=31)
    final: bool = False


class DayNoteIn(BaseModel):
    date: dt.date
    note: str = Field(default="", max_length=300)


class ExpectedIn(BaseModel):
    date: dt.date
    reason: str = Field(default="", max_length=200)


class DocIn(BaseModel):
    mode: str = Field(max_length=20)
    date: dt.date
    note: str = Field(default="", max_length=300)


# ── 리스트·건 ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_purchases(user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return {**await svc.list_purchases(db, svc.kst_today()), "me": {"admin": is_admin(user), "name": _name(user)}}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: PurchaseIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        pid = await svc.create(db, user.id, {**body.model_dump(), "owner": body.owner.strip() or _name(user)})
    except svc.PurchaseError as e:
        await db.rollback()
        raise _fail(e) from e
    return await svc.get(db, pid, svc.kst_today())


@router.post("/delete")
async def delete(body: DeleteIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    return {"deleted": await svc.delete(db, body.ids)}


@router.get("/{pid}")
async def get(pid: int, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        return {**await svc.get(db, pid, svc.kst_today()), "me": {"admin": is_admin(user), "name": _name(user)}}
    except svc.PurchaseError as e:
        raise _fail(e) from e


@router.post("/{pid}/info")
async def set_info(pid: int, body: PurchaseIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.set_info(db, pid, user.id, body.model_dump()))


@router.post("/{pid}/drop")
async def drop(pid: int, body: DropIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    if not body.dropped and not is_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="취소한 구매 건을 되살리는 것은 관리자만 할 수 있습니다.")
    return await _act(db, pid, svc.set_dropped(db, pid, user.id, body.dropped, body.reason))


# ── 파일 ─────────────────────────────────────────────────────────────────────

@router.post("/{pid}/files/{kind}")
async def upload(pid: int, kind: Literal["quote", "approval", "statement", "tax", "customs", "remit", "customs_etc"], file: UploadFile = File(...),
                 label: str = Form(default="", max_length=40),
                 user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await svc.raw(db, pid)
    except svc.PurchaseError as e:
        raise _fail(e) from e
    entry = await _store(pid, kind, file, user)
    try:
        await svc.add_file(db, pid, user.id, kind, entry, label)
    except Exception as e:
        await db.rollback()
        _drop_object(entry["key"])
        if isinstance(e, svc.PurchaseError):
            raise _fail(e) from e
        raise
    return await svc.get(db, pid, svc.kst_today())


@router.get("/{pid}/files/{kind}/{index}")
async def download(pid: int, kind: Literal["quote", "approval", "statement", "tax", "customs", "remit", "customs_etc", "expense"],
                   index: int,
                   _: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    try:
        f = await svc.get_file(db, pid, kind, index)
    except svc.PurchaseError as e:
        raise _fail(e) from e
    obj = get_object_stream(f["key"])
    headers = {"Content-Disposition": _disposition("inline" if f.get("mime") in PO_INLINE else "attachment", f.get("filename") or "file"),
               "Cache-Control": "private, max-age=0, no-store", "X-Content-Type-Options": "nosniff"}

    def stream():
        try:
            yield from obj.stream(amt=64 * 1024)
        finally:
            obj.close()
            obj.release_conn()
    return StreamingResponse(stream(), media_type=f.get("mime") or "application/octet-stream", headers=headers)


@router.post("/{pid}/files/{kind}/{index}/remove")
async def remove_file(pid: int, kind: Literal["quote", "approval", "statement", "tax", "customs", "remit", "customs_etc"], index: int,
                      user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    # 파일 자체는 저장소에 남긴다(이력으로 무엇을 지웠는지 확인할 수 있게 — 영업 건 발주서와 같은 방식)
    return await _act(db, pid, svc.remove_file(db, pid, user.id, kind, index, admin=is_admin(user)))


# ── 재가 ─────────────────────────────────────────────────────────────────────

@router.post("/{pid}/approve")
async def approve(pid: int, body: DayIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.approve(db, pid, user.id, _name(user), body.date))


@router.post("/{pid}/quote/skip")
async def quote_skip(pid: int, body: FlagIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.skip_quote(db, pid, user.id, _name(user), body.value))


@router.post("/{pid}/unapprove")
async def unapprove(pid: int, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.unapprove(db, pid, user.id))


# ── 발주서 ───────────────────────────────────────────────────────────────────

@router.get("/{pid}/po/draft")
async def po_draft(pid: int, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await svc.po_draft(db, pid, await get_profile(db, user.id))
    except svc.PurchaseError as e:
        raise _fail(e) from e


@router.post("/{pid}/po/draft")
async def save_po_draft(pid: int, body: PoIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.save_po_draft(db, pid, user.id, body.model_dump()))


@router.post("/{pid}/po/read-quote")
async def read_quote(pid: int, _: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """지금 견적서(마지막으로 올린 파일)를 사내 AI 가 읽어 발주서 품목·공급사 담당자를 돌려준다(저장 안 함)."""
    from .purchases import quote_read

    try:
        files = svc.files_of(await svc.raw(db, pid), "quote")
    except svc.PurchaseError as e:
        raise _fail(e) from e
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="올린 견적서가 없습니다.")
    f = files[-1]
    data = _read_object(f["key"])
    await db.rollback()                                   # AI 판독 동안 트랜잭션을 잡아 두지 않는다
    res = await quote_read.read(data, f.get("filename") or "", f.get("mime") or "")
    res.pop("text", None)
    return {**res, "filename": f.get("filename")}


async def _pdf(xlsx: bytes) -> bytes:
    from .company_knowledge import quote_xlsx as qx

    try:
        return await qx.to_pdf(xlsx)
    except qx.QuoteError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e


@router.post("/{pid}/po/preview")
async def po_preview(pid: int, body: PoIn, _: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> Response:
    from .purchases import po_xlsx as px

    try:
        d = await svc.raw(db, pid)
        header, items = svc.clean_po(body.model_dump(), "en" if d["region"] == "overseas" else "ko")
        data = px.build_xlsx({**header, "no": "(발행 시 부여)"}, items)
    except svc.PurchaseError as e:
        raise _fail(e) from e
    except px.POError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    await db.rollback()
    return Response(content=await _pdf(data), media_type="application/pdf",
                    headers={"Content-Disposition": _disposition("inline", "발주서_미리보기.pdf"), "Cache-Control": "private, no-store"})


@router.post("/{pid}/po/issue")
async def po_issue(pid: int, body: PoIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.issue_po(db, pid, user.id, _name(user), body.model_dump()))


@router.post("/{pid}/po/chat")
async def po_chat(pid: int, body: PoChatIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """발주서 AI 대화 — 지금 화면의 초안(저장 안 한 것 포함)에 말을 반영해 저장하고, 새 초안·답을 돌려준다.
    '견적서에서 불러와 줘'는 올린 최종 견적서를 quote_read 로 읽어 품목을 채운다."""
    from .purchases import po_chat as pc
    from .purchases import quote_read

    uid = user.id                                         # rollback 뒤 user 속성을 다시 읽지 않게(MissingGreenlet)
    try:
        d = await svc.raw(db, pid)
        profile = await get_profile(db, uid)
        prev = (await svc.po_draft(db, pid, profile)).get("chat") or []
    except svc.PurchaseError as e:
        raise _fail(e) from e
    files = svc.files_of(d, "quote")
    await db.rollback()                                   # AI 판독 동안 트랜잭션을 잡아 두지 않는다
    draft = body.draft.model_dump()
    res = await pc.run(draft, body.text, svc.kst_today())
    reply = res["reply"]
    if res["read_quote"]:
        if not files:
            reply += " 올린 견적서가 없어 품목을 불러오지 못했습니다 — 견적 단계에서 견적서를 먼저 올려 주세요."
        else:
            f = files[-1]
            r = await quote_read.read(_read_object(f["key"]), f.get("filename") or "", f.get("mime") or "")
            if r.get("items"):
                res["draft"]["items"] = r["items"]
                sup = res["draft"]["header"].setdefault("supplier", {})
                for k, v in (r.get("supplier") or {}).items():
                    if v and not str(sup.get(k) or "").strip():
                        sup[k] = v
            reply = " ".join(x for x in (reply, r.get("summary")) if x)
    chat = [*prev, {"role": "user", "text": body.text.strip()}, {"role": "assistant", "text": reply.strip() or "반영했습니다."}]
    try:
        await svc.save_po_draft(db, pid, uid, res["draft"], chat=chat)
    except svc.PurchaseError as e:
        await db.rollback()
        raise _fail(e) from e
    return {**await svc.po_draft(db, pid, profile), "reply": reply, "question": res["question"]}


PO_UPLOAD_EXT = (".xlsx", ".pdf")


@router.post("/{pid}/po/upload")
async def po_upload(pid: int, file: UploadFile = File(...), user: User = Depends(require_sales),
                    db: AsyncSession = Depends(get_db)) -> dict:
    """고친 발주서 파일 올리기 — 저장하고 Order No.·발주일·품목을 읽어 돌려준다(아직 발행 전, 사용자 2026-11-11)."""
    from .purchases import po_read

    try:
        await svc.raw(db, pid)
    except svc.PurchaseError as e:
        raise _fail(e) from e
    if PurePosixPath((file.filename or "").lower()).suffix not in PO_UPLOAD_EXT:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="발주서는 엑셀(xlsx)·PDF 파일만 올릴 수 있습니다.")
    entry = await _store(pid, "po", file, user)
    await db.rollback()                                   # 판독 동안 트랜잭션을 잡아 두지 않는다
    data = _read_object(entry["key"])
    return {"upload": entry, **await po_read.read(data, entry["filename"], entry["mime"])}


@router.post("/{pid}/po/upload/confirm")
async def po_upload_confirm(pid: int, body: PoUploadIn, user: User = Depends(require_sales),
                            db: AsyncSession = Depends(get_db)) -> dict:
    """확인한 번호·품목으로 고친 발주서 파일 발행. 올린 파일은 이 건의 저장 위치(purchases/{id}/po/)에 있는 것만 받는다."""
    up = {k: body.upload.get(k) for k in svc.FILE_KEYS}
    if not str(up.get("key") or "").startswith(f"purchases/{pid}/po/") or ".." in str(up.get("key")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="올린 발주서 파일을 찾을 수 없습니다. 파일을 다시 올려 주세요.")
    try:
        base = await svc.po_draft(db, pid, await get_profile(db, user.id))
    except svc.PurchaseError as e:
        raise _fail(e) from e
    return await _act(db, pid, svc.upload_po(db, pid, user.id, _name(user), up, body.model_dump(exclude={"upload"}), base))


@router.get("/{pid}/po/file")
async def po_file(pid: int, fmt: Literal["pdf", "xlsx"] = "pdf", revision: int | None = None, _: User = Depends(require_sales),
                  db: AsyncSession = Depends(get_db)) -> Response:
    try:
        src = await svc.po_source(db, pid, revision)
        up = src.get("upload")
        name, data = (None, None) if up else await svc.po_file(db, pid, revision)
    except svc.PurchaseError as e:
        raise _fail(e) from e
    await db.rollback()
    if up:                                                # 고친 파일로 발행한 판 — 올린 파일 그대로(PDF 면 엑셀 요청도 PDF)
        name, data = src.get("file_name") or "발주서", _read_object(up["key"])
        if up.get("mime") == "application/pdf":
            return Response(content=data, media_type="application/pdf",
                            headers={"Content-Disposition": _disposition("attachment", f"{name}.pdf"), "Cache-Control": "private, no-store"})
    if fmt == "pdf":
        data = await _pdf(data)
    return Response(content=data, media_type="application/pdf" if fmt == "pdf" else PO_TYPES[".xlsx"],
                    headers={"Content-Disposition": _disposition("attachment", f"{name}.{fmt}"), "Cache-Control": "private, no-store"})


# ── 송금 ─────────────────────────────────────────────────────────────────────

@router.post("/{pid}/terms")
async def set_terms(pid: int, body: TermsIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.set_terms(db, pid, user.id, [t.model_dump() for t in body.terms], admin=is_admin(user)))


@router.post("/{pid}/terms/{index}/check")
async def check_term(pid: int, index: int, file: UploadFile = File(...), user: User = Depends(require_sales),
                     db: AsyncSession = Depends(get_db)) -> dict:
    """지출내역 문서 ↔ 회차 금액 대조 — 코드가 공급사 이름·금액(공급가액 또는 부가세 포함)으로 판정해 회차에 저장한다."""
    from .purchases import expense_check

    try:
        d = await svc.raw(db, pid)
        t = svc.term_at(d, index)
    except svc.PurchaseError as e:
        raise _fail(e) from e
    if t["confirmed"]:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 송금 확인한 회차입니다.")
    entry = await _store(pid, "expense", file, user)
    data = _read_object(entry["key"])
    uid = user.id                                         # rollback 뒤 user 를 읽으면 만료된 객체를 다시 불러오려다 실패한다
    await db.rollback()
    result = await expense_check.run(data, entry["filename"], entry["mime"], d["supplier"], t["amount"], t["amount_vat"])
    try:
        await svc.set_check(db, pid, uid, index, result, entry)
    except svc.PurchaseError as e:
        await db.rollback()
        _drop_object(entry["key"])
        raise _fail(e) from e
    return await svc.get(db, pid, svc.kst_today())


@router.post("/{pid}/terms/{index}/confirm")
async def confirm_term(pid: int, index: int, body: ConfirmIn, user: User = Depends(require_sales),
                       db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.confirm_term(db, pid, user.id, _name(user), index, body.date, body.mismatch_note))


@router.post("/{pid}/terms/{index}/unconfirm")
async def unconfirm_term(pid: int, index: int, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """회차 송금 확인 취소 — 누구나(사용자 2026-11-11 — 견적서·발주서가 바뀌며 금액이 바뀔 수 있어 담당자가 직접 취소)."""
    return await _act(db, pid, svc.unconfirm_term(db, pid, user.id, index))


# ── 입고 · 거래명세서 · 세금계산서 ────────────────────────────────────────────────

@router.post("/{pid}/expected-date")
async def expected_date(pid: int, body: ExpectedIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.set_expected_date(db, pid, user.id, _name(user), body.date, body.reason))


@router.post("/{pid}/paid")
async def paid(pid: int, body: DayNoteIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """해외 — [송금 완료](회차·지출내역 대조 없음)."""
    return await _act(db, pid, svc.confirm_paid(db, pid, user.id, _name(user), body.date, body.note))


@router.post("/{pid}/paid/undo")
async def unpaid(pid: int, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """송금 확인(해외 제조사 송금) 취소 — 누구나(사용자 2026-11-11 — 견적서·발주서가 바뀌며 금액이 바뀔 수 있어 담당자가 직접 취소)."""
    return await _act(db, pid, svc.unconfirm_paid(db, pid, user.id))


@router.post("/{pid}/customs")
async def customs(pid: int, body: DayNoteIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """해외 — [통관 서류 제출 완료](첨부 없이도 됨, 사용자 2026-11-11)."""
    return await _act(db, pid, svc.confirm_customs(db, pid, user.id, _name(user), body.date, body.note))


@router.post("/{pid}/customs/no-origin")
async def customs_no_origin(pid: int, body: FlagIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.set_no_origin(db, pid, user.id, body.value))


@router.post("/{pid}/broker")
async def broker(pid: int, body: DayNoteIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """해외 — [관세사 송금 확인](사용자 2026-11-11)."""
    return await _act(db, pid, svc.confirm_broker(db, pid, user.id, _name(user), body.date, body.note))


@router.post("/{pid}/broker/undo")
async def broker_undo(pid: int, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """관세사 송금 확인 취소 — 누구나(사용자 2026-11-11 — 견적서·발주서가 바뀌며 금액이 바뀔 수 있어 담당자가 직접 취소)."""
    return await _act(db, pid, svc.undo_broker(db, pid, user.id))


@router.post("/{pid}/ship")
async def ship(pid: int, body: DayNoteIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    """[배송 시작](사용자 2026-11-11) — 상태 '배송 중'."""
    return await _act(db, pid, svc.start_shipping(db, pid, user.id, _name(user), body.date, body.note))


@router.post("/{pid}/ship/undo")
async def ship_undo(pid: int, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.undo_shipping(db, pid, user.id))


@router.post("/{pid}/receipt")
async def receipt(pid: int, body: ReceiptIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.add_receipt(db, pid, user.id, _name(user), body.model_dump()))


@router.post("/{pid}/receipt/{index}/edit")
async def receipt_edit(pid: int, index: int, body: ReceiptIn, user: User = Depends(require_sales),
                       db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.update_receipt(db, pid, user.id, _name(user), index, body.model_dump()))


@router.post("/{pid}/receipt/{index}/serials")
async def receipt_serials(pid: int, index: int, body: SerialsIn, user: User = Depends(require_sales),
                          db: AsyncSession = Depends(get_db)) -> dict:
    """입고 기록을 남긴 뒤 S/N 만 등록·고치기(입고 완료 뒤에도, 사용자 2026-11-11)."""
    return await _act(db, pid, svc.set_receipt_serials(db, pid, user.id, index, body.model_dump()["lines"]))


@router.post("/{pid}/receipt/{index}/remove")
async def receipt_remove(pid: int, index: int, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.remove_receipt(db, pid, user.id, index))


RECEIPT_PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


@router.post("/{pid}/receipt-photos")
async def upload_receipt_photo(pid: int, file: UploadFile = File(...), _: User = Depends(require_sales),
                               db: AsyncSession = Depends(get_db)) -> dict:
    """입고 사진 한 장 — 저장소에 올리고 id 를 돌려준다(입고 기록을 저장할 때 품목 줄에 붙인다, 출하 사진과 같은 방식)."""
    try:
        await svc.raw(db, pid)
    except svc.PurchaseError as e:
        raise _fail(e) from e
    ext = PurePosixPath(file.filename or "").suffix.lower()
    if ext not in RECEIPT_PHOTO_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="사진은 JPG·PNG·WEBP 만 올릴 수 있습니다.")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if not 0 < size <= 15 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="사진은 15MB 까지 올릴 수 있습니다.")
    head = file.file.read(16)
    file.file.seek(0)
    if not head.startswith(PO_MAGIC[ext]):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="사진 파일이 아닙니다.")
    name = f"{uuid.uuid4().hex}{ext}"
    try:
        put_object(key=f"purchases/{pid}/receipt/{name}", data=file.file, length=size, mime=RECEIPT_PHOTO_TYPES[ext])
    except Exception as e:  # pragma: no cover — 저장소 장애
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="사진 저장에 실패했습니다. 잠시 뒤 다시 시도해 주세요.") from e
    return {"id": name}


@router.get("/{pid}/receipt-photos/{name}")
async def receipt_photo(pid: int, name: str, _: User = Depends(require_sales)) -> StreamingResponse:
    if not svc.RECEIPT_PHOTO.match(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="사진을 찾을 수 없습니다.")
    try:
        obj = get_object_stream(f"purchases/{pid}/receipt/{name}")
    except Exception as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="사진을 찾을 수 없습니다.") from e

    def stream():
        try:
            yield from obj.stream(amt=64 * 1024)
        finally:
            obj.close()
            obj.release_conn()
    return StreamingResponse(stream(), media_type=RECEIPT_PHOTO_TYPES["." + name.rsplit(".", 1)[1]],
                             headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.post("/{pid}/statement")
async def statement(pid: int, body: DocIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.confirm_doc(db, pid, user.id, _name(user), "statement", body.mode, body.date, body.note))


@router.post("/{pid}/tax")
async def tax(pid: int, body: DocIn, user: User = Depends(require_sales), db: AsyncSession = Depends(get_db)) -> dict:
    return await _act(db, pid, svc.confirm_doc(db, pid, user.id, _name(user), "tax", body.mode, body.date, body.note))


@router.post("/{pid}/undo/{step}")
async def undo(pid: int, step: Literal["customs", "statement", "tax"], user: User = Depends(require_sales),
               db: AsyncSession = Depends(get_db)) -> dict:
    # 명세서·계산서 사유 기록(선결제·선발행)은 선택 서류라 누구나 되돌린다 — 통관 서류 제출 완료 취소는 관리자
    if step == "customs" and not is_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="통관 서류 제출 완료 취소는 관리자만 할 수 있습니다.")
    return await _act(db, pid, svc.undo_step(db, pid, user.id, step))
