"""회사 제품 추천 + 정정 학습 API (company_knowledge/product_recommend.py).

- POST /v1/product-recommend            {request, mode: recommend|chat|correct, recommendation_id?, history?}
    mode=recommend: Q&A(조건) → 추천(제품 사진 id 포함, kind=recommendation).
    mode=chat     : 추천 뒤 대화(kind=answer, 조건을 바꿔 달라면 새 추천 포함). 학습을 요청하면 kind=learn_offer
                    — 화면이 '학습시킬까요?'(예/아니요)를 묻고, 예면 초안 → 사용자가 학습 문구를 확인·수정 → 저장.
- POST /v1/product-recommend/corrections/draft {note, recommendation_id?, history?} — 학습 초안(저장 안 함)
- POST /v1/product-recommend/corrections {draft, recommendation_id?} — 저장(영업 관리자=바로 반영, 그 외=승인 대기)
- GET  /v1/product-recommend/corrections — AI 학습 내용 목록
- POST /v1/product-recommend/corrections/{id}/review {action: approve|reject|off|on} — 영업 관리자 승인·거절
- POST /v1/product-recommend/atc/answer {intake, question, text} — 대화로 받은 답을 질문 칸에 옮김
- POST /v1/product-recommend/quotes/draft {recommendation_id} — 견적서 작성 시작(초안), GET /quotes · /quotes/{id}
- POST /v1/product-recommend/quotes/{id}/chat · /edit · /issue — 대화로 고치기·직접 수정·발행, GET /quotes/{id}/xlsx 내려받기
- POST /v1/product-recommend/recommendations/{id}/finalize {history, memo} — 최종 제안 확정(미팅·결과·근거·대화 저장)
- GET  /v1/product-recommend/proposals/{id} — 영업 건의 [미팅 정보](최종 제안 확정 때 남긴 미팅·결과·근거·대화)
- GET  /v1/product-recommend/quotes/{id}/file?fmt=xlsx|pdf · /quotes/{id}/preview — 견적서 내려받기(엑셀·PDF) · PDF 미리보기
- POST /v1/product-recommend/atc/agent {…chat, versions, current} — GPT 식 대화(생각·단계·답 스트리밍, NDJSON)
- POST /v1/product-recommend/atc/chat {intake, message, history, recommendation_id?} — 대화로 칸 채우기 + 의도(다시 추천·학습·대화)
- POST /v1/product-recommend/atc/spec-search {product} — 실제 제품 툴의 공개 사양 외부 검색(Codex, 제품명만 전송)
"""
from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from .api_auth import get_current_user
from .company_knowledge import product_recommend as pr
from .company_knowledge.plain_text import scrub
from .models import User
from .proposal_project.experience import is_knowledge_approver


class _PlainTextRoute(APIRoute):
    """JSON 응답의 사람이 읽는 문장에서 내부 규칙 번호(R02·C11·P04 …)를 뺀다(사용자 2026-10-06: 화면에 개발자용 표기 금지).
    rule·ref·id 같은 값 칸과 intake(다시 보내는 입력)는 그대로 — plain_text.SKIP_KEYS."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def plain(request: Request) -> Response:
            resp = await handler(request)
            if getattr(resp, "body", None) and (isinstance(resp, JSONResponse) or resp.media_type == "application/json"):
                headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
                return JSONResponse(scrub(json.loads(resp.body)), status_code=resp.status_code, headers=headers)
            return resp
        return plain


router = APIRouter(prefix="/v1/product-recommend", tags=["ProductRecommend"], route_class=_PlainTextRoute)


class AskIn(BaseModel):
    request: str = Field(max_length=pr.REQUEST_MAX)
    mode: str = Field(default="recommend", pattern="^(recommend|chat|correct)$")
    recommendation_id: int | None = None
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)


class SaveIn(BaseModel):
    draft: dict[str, Any]
    recommendation_id: int | None = None


class DraftIn(BaseModel):
    note: str = Field(max_length=pr.REQUEST_MAX)
    recommendation_id: int | None = None
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)


class ReviewIn(BaseModel):
    action: str = Field(pattern="^(approve|reject|keep|off|on)$")


def _bad(e: pr.RecommendError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("")
async def ask(body: AskIn, current_user: User = Depends(get_current_user)):
    prev = await pr.get_recommendation(body.recommendation_id, current_user.id) if body.recommendation_id else None
    if body.mode == "chat" and prev is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="먼저 추천을 받아 주세요.")
    try:
        if body.mode == "chat":
            res = await pr.converse(prev, body.history, body.request, user_id=current_user.id)
            if res.get("learn"):
                # 바로 저장하지 않는다 — 화면이 '학습시킬까요?'를 묻고, 예면 초안·문구 확인을 거친다
                return {"kind": "learn_offer", "answer": res["answer"], "note": body.request}
            return {"kind": "answer", **res}
        draft = await pr.draft_correction(body.request, recommendation=prev) if body.mode in ("chat", "correct") else None
        if draft is not None:
            if draft["missing"]:
                return {"kind": "incomplete", "draft": draft}
            if body.mode == "correct":
                saved = await pr.save_correction(draft, user_id=current_user.id,
                                                 recommendation_id=prev["id"] if prev else None,
                                                 approver=is_knowledge_approver(current_user))
                return {"kind": "saved", "correction": saved}
            return {"kind": "confirm", "draft": draft}
        return {"kind": "recommendation", "recommendation": await pr.recommend(body.request, user_id=current_user.id)}
    except pr.RecommendError as e:
        raise _bad(e) from e


# ── 툴체인저(ATC) 단품 선정 — 주니어 질문지(J01~J10 + 후속) · 선정 규칙(atc_selection) ──────────

class AtcIn(BaseModel):
    intake: dict[str, Any]


@router.get("/atc/spec")
async def atc_spec(current_user: User = Depends(get_current_user)):
    """화면 질문의 원본(질문지 JSON) — 질문 문구·도움말·선택지·후속 질문을 화면에 하드코딩하지 않는다."""
    from .company_knowledge import atc_selection as atc

    return {"revision": atc.RULES_REVISION, "question_bank": atc.SPEC["question_bank"],
            "followup_question_bank": atc.SPEC["followup_question_bank"], "ui_policy": atc.SPEC["ui_policy"],
            "facts": atc.FACTS, "unresolved": atc.SPEC["unresolved_configuration"],
            "engineering_confirmed": atc.ENGINEERING_CONFIRMED}


def _test_samples() -> list[dict[str, Any]]:
    """테스트용 정답지 입력 — scripts/make_atc_test_samples.py 가 샘플 질문지(docs/atc_meeting)로 만든 것."""
    import json
    from pathlib import Path

    p = Path(__file__).with_name("company_knowledge") / "data" / "atc_test_samples.json"
    return json.loads(p.read_text(encoding="utf-8"))["samples"] if p.is_file() else []


@router.get("/atc/test-samples")
async def atc_test_samples(current_user: User = Depends(get_current_user)):
    """[제품 추천 질문 모두 채우기 (테스트용)] 목록 — 샘플 이름·정답 모델·견적 가능 여부(입력값은 빼고)."""
    return {"items": [{k: s[k] for k in ("id", "label", "file", "expected_model", "quotable")} for s in _test_samples()]}


@router.get("/atc/test-samples/{sid}")
async def atc_test_sample(sid: str, current_user: User = Depends(get_current_user)):
    """샘플 한 건의 질문지 입력(intake) — 화면이 Q&A 없이 바로 추천을 찾는다."""
    s = next((x for x in _test_samples() if x["id"] == sid), None)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="샘플을 찾을 수 없습니다.")
    return s


@router.post("/atc/evaluate")
async def atc_evaluate(body: AtcIn, current_user: User = Depends(get_current_user)):
    """저장 없이 판정 — 답에 해당하는 후속 질문을 띄우는 데 쓴다."""
    return await pr.atc_evaluate(body.intake)


class AtcAnswerIn(AtcIn):
    question: dict[str, Any]
    text: str = Field(max_length=1000)


@router.post("/atc/answer")
async def atc_answer(body: AtcAnswerIn, current_user: User = Depends(get_current_user)):
    """AI 가 대화에서 물은 질문에 사용자가 문장으로 답함 → 그 질문 칸에 옮긴 새 intake(저장 안 함)."""
    from .company_knowledge import atc_answer as aa

    try:
        parsed = await aa.read_answer(body.question, body.text, chat=pr._default_chat())
        if not parsed["understood"]:
            return {"understood": False, "intake": body.intake}
        return {"understood": True, "intake": aa.apply(body.intake, body.question, parsed),
                "values": parsed["values"], "unknown": parsed["unknown"]}
    except aa.AnswerError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


class AtcChatIn(AtcIn):
    message: str = Field(min_length=1, max_length=1000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    recommendation_id: int | None = None
    suggested: str | None = Field(default=None, max_length=60)    # 직전에 AI 가 제안한 모델('이걸로 할게'의 대상)
    locked: bool = False                                           # 최종 제안 확정 뒤 — 모델 바꾸기 잠금


@router.post("/atc/chat")
async def atc_chat(body: AtcChatIn, current_user: User = Depends(get_current_user)):
    """AI 와 같이 찾기 대화 — 말에서 칸 값을 채우고(검증) 의도(recommend·learn·suggest·switch·chat)를 읽는다. 저장 안 함.
    suggest·switch 의 모델은 제품 DB 에서 이 로봇·선정 하중에 쓸 수 있는 것만(model_options)."""
    from .company_knowledge import atc_answer as aa
    from .company_knowledge import atc_chat as ac

    from .company_knowledge import atc_selection as atc
    from .database import SessionLocal

    rec = await pr.get_recommendation(body.recommendation_id, current_user.id) if body.recommendation_id else None
    async with SessionLocal() as db:
        options = atc.model_options(await pr.atc_catalog(db), body.intake)
    try:
        return await ac.chat_turn(body.intake, body.message, history=body.history, rec=rec, chat=pr._default_chat(),
                                  options=options, suggested=body.suggested, locked=body.locked)
    except aa.AnswerError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


class AtcVersionIn(BaseModel):
    n: int = Field(ge=1, le=200)
    model: str | None = Field(default=None, max_length=60)
    label: str | None = Field(default=None, max_length=120)


class AtcAgentIn(AtcChatIn):
    versions: list[AtcVersionIn] = Field(default_factory=list, max_length=200)   # 추천 결과가 나온 뒤의 버전(되돌리기 대상)
    current: int | None = None


@router.post("/atc/agent")
async def atc_agent(body: AtcAgentIn, current_user: User = Depends(get_current_user)):
    """GPT 식 추천 대화 — 생각 과정·실행 단계·답을 한 줄에 JSON 하나씩 흘려 보낸다(atc_agent). 저장 안 함.
    칸 값·모델·되돌릴 버전은 코드가 검증한다."""
    import logging

    from fastapi.responses import StreamingResponse

    from .company_knowledge import atc_agent as ag
    from .company_knowledge import atc_answer as aa
    from .company_knowledge import atc_selection as atc
    from .database import SessionLocal
    from .proposal_llm import ProposalLLMError

    rec = await pr.get_recommendation(body.recommendation_id, current_user.id) if body.recommendation_id else None
    async with SessionLocal() as db:
        options = atc.model_options(await pr.atc_catalog(db), body.intake)
    if not body.message.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="내용을 적어 주세요.")

    async def gen():
        try:
            async for ev in ag.agent_turn(body.intake, body.message, history=body.history, rec=rec, stream=pr._default_stream(),
                                          options=options, suggested=body.suggested, locked=body.locked,
                                          versions=[v.model_dump() for v in body.versions], current=body.current):
                yield ag.ndjson(ev)
        except (aa.AnswerError, ProposalLLMError) as e:
            yield ag.ndjson({"t": "error", "text": str(e)})
        except Exception:                                    # 흘려 보내는 중이라 HTTP 오류로 바꿀 수 없다 — 화면에 알리고 로그에 남긴다
            logging.getLogger("atc_agent").exception("atc agent 실패")
            yield ag.ndjson({"t": "error", "text": "답을 만들지 못했습니다. 잠시 뒤 다시 말씀해 주세요."})

    return StreamingResponse(gen(), media_type="application/x-ndjson; charset=utf-8",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class QuoteDraftIn(BaseModel):
    """[견적서 작성] — 이 추천의 견적을 열거나 초안을 만든다. form: 브라우저에 기억한 담당자 정보 등 미리 채울 값."""
    recommendation_id: int
    form: dict[str, str] = Field(default_factory=dict, max_length=20)


class QuoteChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class QuoteLineIn(BaseModel):
    id: str | None = Field(default=None, max_length=10)          # 없으면 새 항목
    name: str = Field(default="", max_length=60)
    qty: int | None = Field(default=None, ge=0, le=100000)
    unit_price: float | None = Field(default=None, ge=0, lt=1e10)
    remark: str = Field(default="", max_length=100)
    confirmed: bool | None = None                                # 조건부 수량(액세서리 v1.2)을 담당자가 확인


class QuoteWorkIn(BaseModel):
    needed: bool | None = None
    people: int | None = Field(default=None, ge=0, le=100)
    days: int | None = Field(default=None, ge=0, le=365)
    rate: float | None = Field(default=None, ge=0, lt=1e9)


class QuoteDeliveryIn(BaseModel):
    """납품 방식 1 택배 · 2 화물 직납(물류비) · 3 UND 인원 설치 납품(인건비) · 4 화물 + 설치(물류비+인건비)."""
    case: int | None = Field(default=None, ge=1, le=5)
    freight: float | None = Field(default=None, ge=0, lt=1e9)


class QuoteEditIn(BaseModel):
    """왼쪽 화면 직접 수정 — 머리(form)·품목(lines, 물류비·인건비 줄 제외)·납품 방식(delivery)·설치 인원·일수·단가(work)·넣을 옵션."""
    form: dict[str, Any] = Field(default_factory=dict, max_length=30)
    lines: list[QuoteLineIn] | None = Field(default=None, max_length=60)
    delivery: QuoteDeliveryIn | None = None
    work: QuoteWorkIn | None = None
    include_options: list[str] = Field(default_factory=list, max_length=10)


@router.post("/quotes/draft", status_code=status.HTTP_201_CREATED)
async def quote_draft(body: QuoteDraftIn, current_user: User = Depends(get_current_user)):
    """견적서 작성 시작 — 추천 결과(선정 모델·구성품·단가표)로 초안, 이미 있으면 그 견적."""
    try:
        return await pr.quote_draft(body.recommendation_id, user_id=current_user.id, form=body.form)
    except pr.RecommendError as e:
        raise _bad(e) from e


class QuoteManualIn(BaseModel):
    """[견적서 수기 작성] — AI 추천 없이 빈 견적서. form: 브라우저에 기억한 담당자 정보 등."""
    form: dict[str, str] = Field(default_factory=dict, max_length=20)


@router.post("/quotes/manual", status_code=status.HTTP_201_CREATED)
async def quote_manual(body: QuoteManualIn, current_user: User = Depends(get_current_user)):
    return await pr.quote_manual(user_id=current_user.id, form=body.form)


@router.get("/quotes/price-list")
async def quote_price_list(current_user: User = Depends(get_current_user)):
    """수기 견적의 [단가표에서 추가] — 회사 단가표에서 가격이 정해진 품목."""
    return {"items": await pr.quote_price_list()}


@router.get("/quotes")
async def quotes(current_user: User = Depends(get_current_user)):
    """내 견적서 목록(초안·발행)."""
    return {"items": await pr.quote_list(user_id=current_user.id)}


@router.get("/quotes/{qid}")
async def quote_get(qid: int, current_user: User = Depends(get_current_user)):
    try:
        return await pr.quote_get(qid, user_id=current_user.id)
    except pr.RecommendError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/quotes/{qid}/chat")
async def quote_chat(qid: int, body: QuoteChatIn, current_user: User = Depends(get_current_user)):
    """견적 대화 — 말로 품목 추가·수정·삭제, AI 가 빠진 것(고객·작업 인원·일수·단가·담당자)을 묻는다."""
    try:
        return await pr.quote_chat(qid, user_id=current_user.id, message=body.message)
    except pr.RecommendError as e:
        raise _bad(e) from e


@router.post("/quotes/{qid}/edit")
async def quote_edit(qid: int, body: QuoteEditIn, current_user: User = Depends(get_current_user)):
    """직접 수정 저장(업데이트)."""
    payload = body.model_dump(exclude_none=True)
    if body.lines is not None:
        payload["lines"] = [x.model_dump() for x in body.lines]
    try:
        return await pr.quote_edit(qid, user_id=current_user.id, payload=payload)
    except pr.RecommendError as e:
        raise _bad(e) from e


class QuoteIssueIn(BaseModel):
    overwrite: bool = False          # True = 판 번호를 올리지 않고 지금 판을 덮어쓰기(발행 뒤 놓친 것 고치기)


@router.post("/quotes/{qid}/issue")
async def quote_issue(qid: int, body: QuoteIssueIn | None = None, current_user: User = Depends(get_current_user)):
    """[견적서 발행] — 첫 발행 = 영업 건 번호(S26-…)가 견적번호, 다시 발행 = 같은 번호 Rev+1(덮어쓰기면 지금 판).
    미정 값이 있으면 400(이유)."""
    try:
        return await pr.quote_issue(qid, user_id=current_user.id, overwrite=bool(body and body.overwrite))
    except pr.RecommendError as e:
        raise _bad(e) from e


_MEDIA = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "pdf": "application/pdf"}


@router.get("/quotes/{qid}/file")
async def quote_download(qid: int, fmt: Literal["xlsx", "pdf"] = "xlsx", revision: int | None = None,
                         current_user: User = Depends(get_current_user)):
    """발행한 견적서 내려받기 — 엑셀 또는 같은 모양의 PDF(기본 최신 판, ?revision=n 이면 그 판)."""
    try:
        name, data = await pr.quote_file(qid, user_id=current_user.id, revision=revision, fmt=fmt)
    except pr.QuotePdfError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except pr.RecommendError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return Response(content=data, media_type=_MEDIA[fmt],
                    headers={"Content-Disposition": f"attachment; filename=\"quote.{fmt}\"; filename*=UTF-8''{quote(name)}"})


@router.get("/quotes/{qid}/preview")
async def quote_preview(qid: int, current_user: User = Depends(get_current_user)):
    """지금 상태 그대로의 견적서 PDF 미리보기(저장 안 함, 화면에 바로 띄움). 채울 것이 남았으면 400."""
    try:
        data = await pr.quote_preview(qid, user_id=current_user.id)
    except pr.QuotePdfError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except pr.RecommendError as e:
        raise _bad(e) from e
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=\"preview.pdf\"", "Cache-Control": "no-store"})


class FinalizeIn(BaseModel):
    history: list[dict[str, str]] = Field(default_factory=list, max_length=200)
    memo: str = Field(default="", max_length=1000)


@router.post("/recommendations/{rid}/finalize", status_code=status.HTTP_201_CREATED)
async def finalize(rid: int, body: FinalizeIn, current_user: User = Depends(get_current_user)):
    """[최종 제안 확정] — 미팅·입력·선정 결과·근거·구성품·대화 기록을 확정 제안 한 건으로 저장."""
    from .sales_deals.service import DealError

    try:
        return await pr.finalize_recommendation(rid, user_id=current_user.id, history=body.history, memo=body.memo)
    except pr.RecommendError as e:
        raise _bad(e) from e
    except DealError as e:                     # 영업 건 번호 이니셜을 아직 안 정한 담당자 등 — 화면이 이니셜을 물어 다시 시도
        raise HTTPException(e.status, detail=str(e)) from e


@router.get("/proposals")
async def proposals(current_user: User = Depends(get_current_user)):
    """확정 제안 내역(목록)."""
    return {"items": await pr.list_proposals()}


@router.get("/proposals/{pid}")
async def proposal(pid: int, current_user: User = Depends(get_current_user)):
    try:
        return await pr.get_proposal(pid)
    except pr.RecommendError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e


class SpecSearchIn(BaseModel):
    product: str = Field(min_length=1, max_length=80)


@router.post("/atc/spec-search")
async def atc_spec_search(body: SpecSearchIn, current_user: User = Depends(get_current_user)):
    """실제 제품인 툴의 공개 사양 — 외부 AI(Codex 브리지) 웹 검색 + 사내 AI 가 값 정리. 칸에 넣는 건 화면에서 사용자가 확인한 뒤.
    외부로 나가는 것은 제품명뿐(external_calls 에 기록)."""
    from .company_knowledge import tool_spec_search as ts

    try:
        return await ts.search_spec(body.product, user_id=current_user.id)
    except ts.SpecSearchError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


class AtcRecommendIn(AtcIn):
    meeting: dict[str, Any] | None = None


MEETING_FILE_MAX = 10 * 1024 * 1024


@router.post("/atc/extract")
async def atc_extract(file: UploadFile | None = File(default=None), text: str | None = Form(default=None),
                      current_user: User = Depends(get_current_user)):
    """채운 첫 고객 미팅 질문지(docx·pdf·txt 또는 붙여 넣은 글) → 미팅 요약 + 선정 입력. 저장하지 않는다."""
    from .company_knowledge import atc_meeting as am

    try:
        form = None
        if file is not None:
            data = await file.read(MEETING_FILE_MAX + 1)
            if len(data) > MEETING_FILE_MAX:
                raise am.MeetingError("파일이 너무 큽니다(10MB 이하).")
            source = am.read_upload(data, file.filename or "", file.content_type or "")
            if (file.filename or "").lower().endswith(".docx"):
                form = am.parse_form(data)
        elif text and text.strip():
            source = text
        else:
            raise am.MeetingError("질문지 파일을 첨부하거나 내용을 붙여 넣어 주세요.")
        return await am.extract(source, chat=pr._default_chat(), form=form)
    except am.MeetingError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post("/atc/recommend")
async def atc_recommend(body: AtcRecommendIn, current_user: User = Depends(get_current_user)):
    """질문지 답 → 규칙 판정 + 기록. 결과는 일반 추천과 같은 모양(kind=recommendation)에 atc 판정을 더한다."""
    return {"kind": "recommendation",
            "recommendation": await pr.atc_recommend(body.intake, user_id=current_user.id, meeting=body.meeting)}


@router.post("/corrections/draft")
async def draft(body: DraftIn, current_user: User = Depends(get_current_user)):
    """'학습시킬까요?'에 예 → 학습 초안(종류·학습 문구 등). 사용자가 문구를 확인·수정한 뒤 POST /corrections."""
    prev = await pr.get_recommendation(body.recommendation_id, current_user.id) if body.recommendation_id else None
    try:
        return await pr.draft_correction(body.note, recommendation=prev, history=body.history)
    except pr.RecommendError as e:
        raise _bad(e) from e


@router.post("/corrections", status_code=status.HTTP_201_CREATED)
async def save(body: SaveIn, current_user: User = Depends(get_current_user)):
    """권한: 영업 관리자가 저장하면 바로 반영, 그 외 사용자는 승인 대기(관리자 승인 후 반영)."""
    try:
        return await pr.save_correction(body.draft, user_id=current_user.id, recommendation_id=body.recommendation_id,
                                        approver=is_knowledge_approver(current_user))
    except pr.RecommendError as e:
        raise _bad(e) from e


@router.get("/corrections")
async def corrections(current_user: User = Depends(get_current_user)):
    return {"items": await pr.list_corrections(), "approver": is_knowledge_approver(current_user)}


@router.post("/corrections/{cid}/review")
async def review(cid: int, body: ReviewIn, current_user: User = Depends(get_current_user)):
    if not is_knowledge_approver(current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="영업 관리자만 검토할 수 있습니다.")
    try:
        return await pr.review_correction(cid, body.action, reviewer_id=current_user.id)
    except pr.RecommendError as e:
        raise _bad(e) from e
