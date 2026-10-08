"""제안서 작업 프로젝트 라우터 — 원클릭 버튼 → 자료 입력 → 작업 화면(자료 읽기·핵심 질문·컨셉·구성·제작).

- GET  /v1/proposal-projects/catalog : 질문 목록 P01~P48(12 묶음) + 상태·첨부 용도 이름
- GET  /v1/proposal-projects         : 내 프로젝트 목록
- POST /v1/proposal-projects         : 자료 입력(메일·요청 원문 + 첨부) → 프로젝트 생성 + 자료 읽기 시작
- GET  /v1/proposal-projects/{id}    : 프로젝트 전체(문항·첨부·대화·승인) + 다음 질문 묶음
- POST /v1/proposal-projects/{id}/read    : 자료 다시 읽기(백그라운드)
- POST /v1/proposal-projects/{id}/answers : 질문 답 제출 → 다음 질문 묶음
- POST /v1/proposal-projects/{id}/finish-questions : 핵심 질문 마치기 → 공정 컨셉 계획(로봇·그리퍼 후보, 회사 경험)
- 3단계 계획: POST {id}/plan-select(로봇·그리퍼 선택) · plan-confirm(확정 → 컨셉 이미지). 계획 중 revise 는 글만 고침
- 3단계: POST {id}/propose · extra(비교안) · revise(수정 요청) · alternatives · image · image-approve · labels
         · finish-concept (만드는 판단은 GPT, 실패 시 gemma)
- 5단계: POST {id}/structure(저장) · approve-structure(승인 → 제작 시작)
- 6단계·공통: POST {id}/produce(다시 제작) · deck-edit(완성본 프롬프트로 수정) · save-knowledge-all(지식 저장 한 번에) · back(되돌리기) · quick(바로 만들기, 실행 시험용)
- 제품 이미지: GET product-images · POST product-images/{card_id}(올리기) · POST product-images/review/{image_id}
             · GET product-images/file/{image_id} — 컨셉 이미지의 외형 참고(승인분만 쓰임)
오래 걸리는 작업은 백그라운드(proposal_project.jobs)로 돌고, GET 의 job·job_alive 로 진행을 본다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .api_auth import get_current_user
from .company_knowledge import product_images
from .database import get_db
from .models import User
from .proposal_project import dialog, experience, jobs, plan, service, workflow
from .proposal_project.catalog import catalog_payload

router = APIRouter(prefix="/v1/proposal-projects", tags=["ProposalProjects"])


class AssetIn(BaseModel):
    attachment_id: int
    role: str
    note: str = ""
    external_ok: bool = False


class AnswerIn(BaseModel):
    code: str = Field(max_length=8)
    value: str = ""
    unknown: bool = False


class AnswersIn(BaseModel):
    answers: list[AnswerIn] = Field(max_length=dialog.BATCH_MAX)


class ProjectCreate(BaseModel):
    title: str = Field(max_length=service.TITLE_MAX)
    request_text: str = ""
    intake: dict[str, str] = Field(default_factory=dict)
    assets: list[AssetIn] = Field(default_factory=list)


@router.get("/catalog")
async def get_catalog(_user: User = Depends(get_current_user)):
    return catalog_payload()


@router.get("")
async def list_my_projects(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    return await service.list_projects(db, current_user.id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        pid = await service.create_project(
            db, user_id=current_user.id, title=body.title, request_text=body.request_text,
            intake=body.intake,
            assets=[service.AssetInput(**a.model_dump()) for a in body.assets],
        )
    except service.ProjectError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    dialog.start_reading(pid)      # 자료 입력 직후 자료 읽기(1단계)를 백그라운드로 시작
    return {"id": pid}


async def _owned(db: AsyncSession, project_id: int, user: User) -> dict:
    p = await service.get_project(db, project_id, user_id=user.id,
                                  is_superadmin=user.role.value == "superadmin")
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="프로젝트를 찾을 수 없습니다.")
    return p


async def _snapshot(db: AsyncSession, project_id: int, user: User) -> dict:
    """프로젝트 + 진행 상태. 실행 여부를 데이터보다 먼저 본다 — 반대로 하면 읽는 사이 작업이 끝났을 때
    '끝남' 과 끝나기 전 데이터가 한 응답에 섞인다(실측: 대안이 빈 채로 완료 판정)."""
    reading, alive = dialog.is_reading(project_id), jobs.is_running(project_id)
    p = await _owned(db, project_id, user)
    p["reading"] = reading
    p["job_alive"] = alive
    p["vision_pending"] = workflow.vision_pending(project_id)     # 이미지 검수 초안을 뒤에서 쓰는 중
    p["questions"] = dialog.next_questions(p) if p["stage"] == "questioning" else []
    p["required_open"] = dialog.required_open(p)
    p["experience"] = await experience.attach_details(p["experience"])
    p["plan_pending"] = plan.pending(p)
    reviews = await experience.card_reviews([a["knowledge_card_id"] for a in p["alternatives"]
                                             if a.get("knowledge_card_id")])
    for a in p["alternatives"]:
        if a.get("knowledge_card_id"):
            a["knowledge_status"] = reviews.get(a["knowledge_card_id"], "")
    p["knowledge_approver"] = experience.is_knowledge_approver(user)
    return p


class ReviewIn(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=300)


def _approver(user: User) -> None:
    if not experience.is_knowledge_approver(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="회사 지식 승인은 영업 관리자만 할 수 있습니다.")


@router.get("/knowledge-reviews")
async def list_knowledge_reviews(current_user: User = Depends(get_current_user)):
    """승인 대기 회사 지식(경험 카드) — 영업 관리자 전용."""
    _approver(current_user)
    return await experience.pending_reviews()


@router.post("/knowledge-reviews/{card_id}")
async def review_knowledge(card_id: int, body: ReviewIn, current_user: User = Depends(get_current_user)):
    """승인하면 회상에 쓰이고(단서 생성), 반려하면 사유를 남기고 쓰지 않는다."""
    _approver(current_user)
    try:
        review = await experience.review_card(card_id, body.action, reviewer_id=current_user.id, note=body.note)
    except experience.ReviewError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return {"id": card_id, "review_status": review, "pending": await experience.pending_reviews()}


# ── 제품 이미지 — 컨셉 이미지의 외형 참고. 누구나 올리고, 영업 관리자가 승인해야 쓰인다. ──
@router.get("/product-images")
async def list_product_images(current_user: User = Depends(get_current_user)):
    return {"products": await product_images.library(),
            "approver": experience.is_knowledge_approver(current_user)}


@router.post("/product-images/{card_id}", status_code=status.HTTP_201_CREATED)
async def upload_product_image(card_id: int, file: UploadFile = File(...),
                               current_user: User = Depends(get_current_user)):
    data = await file.read(product_images.UPLOAD_BYTES_MAX + 1)
    try:
        iid, review = await product_images.upload(
            card_id, data, user_id=current_user.id, approver=experience.is_knowledge_approver(current_user),
            note=f"직접 올림 {file.filename or ''}".strip())
    except product_images.ImageError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return {"id": iid, "review_status": review}


class ImageReviewIn(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")


@router.post("/product-images/review/{image_id}")
async def review_product_image(image_id: int, body: ImageReviewIn, current_user: User = Depends(get_current_user)):
    _approver(current_user)
    try:
        return {"id": image_id,
                "review_status": await product_images.review(image_id, body.action, reviewer_id=current_user.id)}
    except product_images.ImageError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/product-images/file/{image_id}")
async def product_image_file(image_id: int, _user: User = Depends(get_current_user)):
    try:
        data = await product_images.read_image(image_id)
    except product_images.ImageError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{project_id}")
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _snapshot(db, project_id, current_user)


@router.post("/{project_id}/read", status_code=status.HTTP_202_ACCEPTED)
async def reread(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p = await _owned(db, project_id, current_user)
    if p["stage"] not in ("intake", "reading", "questioning"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="질문 단계가 끝난 프로젝트는 다시 읽지 않습니다.")
    return {"started": dialog.start_reading(project_id)}


@router.post("/{project_id}/answers")
async def post_answers(
    project_id: int,
    body: AnswersIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p = await _owned(db, project_id, current_user)
    if p["stage"] != "questioning" or dialog.is_reading(project_id) or jobs.is_running(project_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="지금은 질문에 답할 단계가 아닙니다.")
    try:
        await dialog.answer(db, project_id, [a.model_dump() for a in body.answers])
    except service.ProjectError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return await _snapshot(db, project_id, current_user)


@router.post("/{project_id}/finish-questions")
async def finish_questions(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p = await _owned(db, project_id, current_user)
    if p["stage"] != "questioning":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="지금은 질문 단계가 아닙니다.")
    try:
        await dialog.finish_questions(db, p)
    except service.ProjectError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    # 질문을 마치면 이어서 공정 컨셉 계획(로봇·그리퍼 후보, 회사 경험)까지 — 이미지는 계획 확정 후.
    jobs.start(project_id, "concept", lambda step: workflow.run_concept_plan(project_id, step))
    return await _snapshot(db, project_id, current_user)


# ── 3~6단계 ──────────────────────────────────────────────────────────────────

class AlternativesIn(BaseModel):
    alternatives: list[dict] = Field(max_length=3)


class ImageIn(BaseModel):
    alt_id: str | None = Field(default=None, max_length=2)
    # 직접 고친 구성을 저장한 뒤 다시 그릴 때 — 이전 버전을 참고로 보내 바뀐 부분만 고친다.
    revision: str = Field(default="", max_length=300)


class ImageRef(BaseModel):
    image_id: int


class LabelsIn(BaseModel):
    image_id: int
    labels: list[dict] = Field(max_length=12)


class StructureIn(BaseModel):
    pages: list[dict] = Field(max_length=30)    # workflow.PAGES_HARD_MAX — 10쪽은 권장 상한(경고만)
    quote_lines: list[dict] = Field(max_length=30)


class BackIn(BaseModel):
    stage: str


class QuickIn(BaseModel):
    with_images: bool = True


def _start(project_id: int, kind: str, work) -> None:
    if dialog.is_reading(project_id) or not jobs.start(project_id, kind, work):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="다른 작업이 진행 중입니다. 끝난 뒤 다시 시도해 주세요.")


def _idle(project_id: int) -> None:
    if jobs.is_running(project_id) or dialog.is_reading(project_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="작업이 진행 중입니다. 끝난 뒤 다시 시도해 주세요.")


async def _act(db: AsyncSession, project_id: int, user: User, fn) -> dict:
    _idle(project_id)
    p = await _owned(db, project_id, user)
    try:
        await fn(p)
    except service.ProjectError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return await _snapshot(db, project_id, user)


@router.post("/{project_id}/propose", status_code=status.HTTP_202_ACCEPTED)
async def propose(project_id: int, current_user: User = Depends(get_current_user),
                  db: AsyncSession = Depends(get_db)):
    p = await _owned(db, project_id, current_user)
    if p["stage"] != "concept":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="공정·컨셉 단계에서만 제안받을 수 있습니다.")
    _start(project_id, "propose", lambda step: workflow.run_propose(project_id, step))
    return {"started": True}


@router.post("/{project_id}/alternatives")
async def save_alternatives(project_id: int, body: AlternativesIn, current_user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)):
    return await _act(db, project_id, current_user,
                      lambda p: workflow.save_alternatives(db, p, body.alternatives))


@router.post("/{project_id}/image", status_code=status.HTTP_202_ACCEPTED)
async def make_image(project_id: int, body: ImageIn, current_user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    p = await _owned(db, project_id, current_user)
    if p["stage"] != "concept":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="공정·컨셉 단계에서만 이미지를 만들 수 있습니다.")
    if body.alt_id and body.alt_id not in {a["id"] for a in p["alternatives"]}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="없는 대안입니다.")
    if any(a["id"] == body.alt_id and plan.alt_pending(a) for a in p["alternatives"]):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="먼저 공정 컨셉 계획을 확정해 주세요.")

    async def work(step):
        await workflow.run_image(project_id, body.alt_id, step, revision=body.revision.strip())
        return f"{'대안 ' + body.alt_id if body.alt_id else '공통'} 이미지를 만들었습니다."
    _start(project_id, "image", work)
    return {"started": True}


@router.post("/{project_id}/image-approve")
async def approve_image(project_id: int, body: ImageRef, current_user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    return await _act(db, project_id, current_user,
                      lambda p: workflow.approve_image(db, p, body.image_id, current_user.id))


@router.post("/{project_id}/labels")
async def save_labels(project_id: int, body: LabelsIn, current_user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    return await _act(db, project_id, current_user,
                      lambda p: workflow.save_labels(db, p, body.image_id, body.labels))


@router.post("/{project_id}/finish-concept")
async def finish_concept(project_id: int, current_user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    out = await _act(db, project_id, current_user,
                     lambda p: workflow.finish_concept(db, p, current_user.id))
    _start(project_id, "structure", lambda step: workflow.run_structure(project_id, step))
    out["job_alive"] = True
    return out


@router.post("/{project_id}/structure")
async def save_structure(project_id: int, body: StructureIn, current_user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    return await _act(db, project_id, current_user,
                      lambda p: workflow.save_structure(db, p, body.pages, body.quote_lines))


@router.post("/{project_id}/approve-structure")
async def approve_structure(project_id: int, current_user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)):
    out = await _act(db, project_id, current_user,
                     lambda p: workflow.approve_structure(db, p, current_user.id))
    _start(project_id, "produce", lambda step: workflow.run_produce(project_id, step))
    out["job_alive"] = True
    return out


@router.post("/{project_id}/produce", status_code=status.HTTP_202_ACCEPTED)
async def produce_again(project_id: int, current_user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """제작이 실패·중단됐을 때 다시 제작(producing 단계에서만)."""
    p = await _owned(db, project_id, current_user)
    if p["stage"] != "producing":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="제작 단계가 아닙니다.")
    _start(project_id, "produce", lambda step: workflow.run_produce(project_id, step))
    return {"started": True}


@router.post("/{project_id}/back")
async def back(project_id: int, body: BackIn, current_user: User = Depends(get_current_user),
               db: AsyncSession = Depends(get_db)):
    return await _act(db, project_id, current_user, lambda p: workflow.back_to(db, p, body.stage))


@router.post("/{project_id}/quick", status_code=status.HTTP_202_ACCEPTED)
async def quick(project_id: int, body: QuickIn, current_user: User = Depends(get_current_user),
                db: AsyncSession = Depends(get_db)):
    """바로 제안서 만들기(실행 시험용) — 남은 질문·승인을 건너뛰고 제작까지."""
    p = await _owned(db, project_id, current_user)
    if p["stage"] == "done":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 완성된 프로젝트입니다. 구성 단계로 되돌린 뒤 다시 만드세요.")
    _start(project_id, "quick", lambda step: workflow.run_quick(project_id, step, with_images=body.with_images))
    return {"started": True}



class ReviseIn(BaseModel):
    alt_id: str = Field(max_length=2)
    request: str = Field(max_length=1000)


@router.post("/{project_id}/revise", status_code=status.HTTP_202_ACCEPTED)
async def revise(project_id: int, body: ReviseIn, current_user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_db)):
    """컨셉 수정 요청(말로) → 구성 문장 수정 → 이전 버전을 참고로 새 버전 이미지. 여러 번 반복 가능."""
    p = await _owned(db, project_id, current_user)
    if p["stage"] != "concept":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="공정·컨셉 단계에서만 수정할 수 있습니다.")
    if body.alt_id not in {a["id"] for a in p["alternatives"]}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="없는 컨셉입니다.")
    if not body.request.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="수정할 내용을 적어 주세요.")
    _start(project_id, "revise", lambda step: workflow.run_revise(project_id, body.alt_id, body.request, step))
    return {"started": True}


@router.post("/{project_id}/extra", status_code=status.HTTP_202_ACCEPTED)
async def add_extra(project_id: int, current_user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    """비교안 1개 추가(사용자가 요청할 때만). 공정 컨셉은 기본 1개."""
    p = await _owned(db, project_id, current_user)
    if p["stage"] != "concept":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="공정·컨셉 단계에서만 비교안을 더할 수 있습니다.")
    if not p["alternatives"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="먼저 공정 컨셉을 제안받아 주세요.")
    if len(p["alternatives"]) >= 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="비교안은 3개까지입니다.")
    _start(project_id, "extra", lambda step: workflow.run_extra(project_id, step))
    return {"started": True}


class ExperienceIn(BaseModel):
    card_id: int
    action: str = Field(pattern="^(reflect|dismiss|apply|skip)$")


class KnowledgeIn(BaseModel):
    alt_id: str = Field(max_length=2)


@router.post("/{project_id}/experience")
async def judge_experience(project_id: int, body: ExperienceIn, current_user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    """경험 기반 제안 판단.
    - 컨셉 단계 reflect(컨셉에 반영)/dismiss(넘기기): 지식에는 기록하지 않는다. reflect 면 수정 요청 경로로 새 버전.
    - 제안서 완성 후 apply(맞는 지식)/skip(안 맞는 지식): 이때만 지식 가중치로 기록한다."""
    _idle(project_id)
    p = await _owned(db, project_id, current_user)
    try:
        hit = await workflow.judge_experience(db, p, body.card_id, body.action, current_user.id)
    except service.ProjectError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    if body.action == "reflect" and hit.get("alt_id"):
        request = workflow.experience_request(hit)
        _start(project_id, "revise", lambda step: workflow.run_revise(project_id, hit["alt_id"], request, step))
    return await _snapshot(db, project_id, current_user)


@router.post("/{project_id}/save-knowledge-all")
async def save_knowledge_all(project_id: int, current_user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    """제안서 완성 후 '지식 저장' 한 번 — 컨셉마다 경험 카드로 저장하고, 컨셉에 반영한 회사 경험은 맞는 지식으로 기록.
    (사용자 요청 2026-09-30: 낱개로 고르면 무엇을 저장할지 헷갈린다.)"""
    return await _act(db, project_id, current_user, lambda p: workflow.save_all_knowledge(
        db, p, user_id=current_user.id, approver=experience.is_knowledge_approver(current_user)))


class PlanSelectIn(BaseModel):
    alt_id: str = Field(max_length=2)
    robot_pick: str | None = Field(default=None, max_length=80)
    gripper_pick: str | None = Field(default=None, max_length=80)


class PlanConfirmIn(BaseModel):
    picks: list[PlanSelectIn] = Field(default_factory=list, max_length=3)


@router.post("/{project_id}/plan-select")
async def plan_select(project_id: int, body: PlanSelectIn, current_user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    """계획 단계 — 로봇 팔·그리퍼 선택 저장(후보 밖 직접 입력 허용)."""
    return await _act(db, project_id, current_user, lambda p: workflow.plan_select(
        db, p, body.alt_id, body.robot_pick, body.gripper_pick))


@router.post("/{project_id}/plan-confirm", status_code=status.HTTP_202_ACCEPTED)
async def plan_confirm(project_id: int, body: PlanConfirmIn, current_user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """계획 확정 → 고른 모델을 구성에 반영 → 컨셉 이미지를 그린다(이미지 없는 컨셉만)."""
    p = await _owned(db, project_id, current_user)
    if p["stage"] != "concept" or not plan.pending(p):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="확정할 공정 컨셉 계획이 없습니다.")
    picks = [x.model_dump() for x in body.picks]
    _start(project_id, "plan-confirm", lambda step: workflow.run_plan_confirm(project_id, picks, step))
    return {"started": True}


class DeckEditIn(BaseModel):
    request: str = Field(max_length=1000)


@router.post("/{project_id}/deck-edit", status_code=status.HTTP_202_ACCEPTED)
async def deck_edit(project_id: int, body: DeckEditIn, current_user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    """완성된 PPT 를 프롬프트로 수정 → 새 버전(최신 버전에 이어서 계속). 쪽 문구·제목·순서·삭제만(표·그림은 구성·컨셉 단계에서)."""
    p = await _owned(db, project_id, current_user)
    if p["stage"] != "done" or not p["outputs"]:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="제안서를 만든 뒤에 고칠 수 있습니다.")
    if not body.request.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="고칠 내용을 적어 주세요.")
    _start(project_id, "deck-edit", lambda step: workflow.run_deck_edit(project_id, body.request, step))
    return {"started": True}


@router.post("/{project_id}/save-knowledge")
async def save_knowledge(project_id: int, body: KnowledgeIn, current_user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    """잘 만든 공정 컨셉을 회사 지식(경험 카드)으로 저장 — 제안서 완성 후. 다음 프로젝트에서 떠오른다."""
    return await _act(db, project_id, current_user, lambda p: workflow.save_concept_knowledge(
        db, p, body.alt_id, user_id=current_user.id, approver=experience.is_knowledge_approver(current_user)))
