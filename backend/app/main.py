import asyncio
import datetime as _dt
import json as _json_mod
import logging
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import codex_delegate
from .api_attachments import router as attachments_router
from .api_auth import router as auth_router
from .api_bug_reports import router as bug_reports_router
from .api_chat import router as chat_router
from .api_projects import router as projects_router
from .api_proposal_projects import router as proposal_projects_router
from .api_product_recommend import router as product_recommend_router
from .api_proposal_records import router as proposal_records_router
from .api_rag import router as rag_router
from .api_sales_deals import router as sales_deals_router
from .config import settings, validate_security_settings
from .database import dispose, ping
from .migrations import run_migrations_on_startup
from .schemas import GenerateRequest, TitleRequest
from .seed_conversations import run_seed_on_startup
from .seed_mail_accounts import seed_mail_accounts_on_startup
from .seed_vendors import seed_vendors_on_startup
from .tools import TOOLS, dispatch
from .llm import stream_chat, stream_chat_with_tools

logger = logging.getLogger("und_cortex.startup")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # startup #-1 — 보안 필수 설정 검증. 약한/기본 JWT 시크릿이면 즉시 부팅 거부.
    validate_security_settings()

    # startup #0 — 멱등 스키마 마이그레이션. 시드/조회보다 먼저 컬럼 모양을 맞춰둔다.
    try:
        await run_migrations_on_startup()
    except Exception as exc:
        logger.warning("[migrations] failed: %s", exc)

    # startup — 사이드바 RECENT 시연용 가상 대화 시드 보장(멱등).
    # 03_conversations.sql 은 Postgres 볼륨이 비어있을 때만 실행되므로,
    # 기존 볼륨을 재사용하는 환경에서는 backend 부팅마다 여기서 채워준다.
    try:
        stats = await run_seed_on_startup()
        if stats.get("conv_inserted") or stats.get("msg_inserted"):
            logger.info(
                "[seed] conversations=%d messages=%d skipped=%d",
                stats.get("conv_inserted", 0),
                stats.get("msg_inserted", 0),
                stats.get("skipped", 0),
            )
    except Exception as exc:
        # 시드 실패는 앱 기동을 막지 않는다(/health 로 진단). RECENT 가 일시적으로 비어있어도 흐름은 유지.
        logger.warning("[seed] failed to ensure conversation seeds: %s", exc)

    # startup — mail_accounts 멱등 UPSERT. .env 의 HIWORKS_* 를 원격 DB 로 동기화.
    try:
        mail_seed = await seed_mail_accounts_on_startup()
        logger.info("[seed.mail_accounts] result=%s", mail_seed)
    except Exception as exc:
        logger.warning("[seed.mail_accounts] failed: %s", exc)

    # startup — 수금 거래처(vendors) + 별칭 멱등 UPSERT. 집행일 방향 규칙 영구 동기화.
    try:
        vendor_seed = await seed_vendors_on_startup()
        logger.info("[seed.vendors] result=%s", vendor_seed)
    except Exception as exc:
        logger.warning("[seed.vendors] failed: %s", exc)

    yield
    await dispose()


app = FastAPI(title="UND AI Agent Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(projects_router)
app.include_router(attachments_router)
app.include_router(rag_router)
app.include_router(proposal_records_router)
app.include_router(proposal_projects_router)
app.include_router(product_recommend_router)
app.include_router(sales_deals_router)
app.include_router(bug_reports_router)


async def require_internal_token(
    x_internal_token: str | None = Header(default=None),
) -> None:
    """BFF → Agent 내부 호출 보호. INTERNAL_SERVICE_TOKEN 설정 시 헤더 일치를 강제.

    설정이 비어 있으면(개발) 통과 — 운영에서는 .env 에 토큰을 지정해 외부 직접 접근을 차단한다.
    인증 우회 차단이 목적이므로 사용자 식별(JWT)과 별개로, 신뢰된 BFF 만 호출하도록 한다.
    """
    expected = settings.internal_service_token
    if not expected:
        return
    if x_internal_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효한 내부 서비스 토큰이 필요합니다.",
        )


@app.get("/health")
async def health():
    db_ok = await ping()
    return {
        "ok": True,
        "db": db_ok,
        "provider": "ollama",
        "active_model": settings.ollama_model,
        "ollama": settings.ollama_base_url,
        "tavily": bool(settings.tavily_api_key),
    }


_SEND_INTENT_RE = re.compile(r"(발송|보내|전송|쏴|쏘아|보내줘|보내 줘|send)", re.IGNORECASE)
# 작성/생성/요청 의도 — 명령형 또는 요청형. 정의/조회 질문(뭐야/언제/어떻게)은 제외.
# 동의 표현 망라: 작성/만들/써/해줘/부탁/처리/생성/출력/요청/필요 등.
# 단순 "써" 단어 매치는 "쓰다" 어간 — '뭐 써?' 같은 의문문도 매치되지만, 같이 와야 하는
# expense 컨텍스트 매치가 의문문 패턴과 겹치는 경우는 드물어 실용적으로 OK.
_COMPOSE_INTENT_RE = re.compile(
    r"("
    r"작성|만들|초안|양식|"               # 기본 동사 어간
    r"써|적어|뽑아|구성|정리|"            # 변형 동사
    r"해\s*줘|해\s*주세요|해\s*주실|"     # 해줘 / 해 주세요 / 해 주실 수
    r"부탁(?:해|드려|드립|합|할)|"        # 부탁해/드려/드립니다/합니다/할게요
    r"처리|생성|출력|마무리|완성|"        # 추가 동사
    r"준비|발급|제출|신청|"               # 사무 동사
    r"필요해|필요합니다|필요한데|"        # 필요 표현
    r"요청"                                # 요청드립니다
    r")",
    re.IGNORECASE,
)
_LEAVE_CONTEXT_RE = re.compile(r"(연차|휴가|Dear All|사용 보고|사용예정|사용일)")
# 정보·설명 요청 마커 — '어떻게 X', '뭐야', '알려줘', '설명해' 등이 등장하면
# 사용자는 도구 호출이 아니라 답변(정보)을 원함. 이 패턴이 있으면 force 트리거 회피.
_INFO_REQUEST_RE = re.compile(
    r"("
    r"어떻게|언제|어디|왜|누가|무엇|뭐야|뭔가요|뭐죠|뭐예요|뭐에요|"
    r"알려|설명|보여\s*줘|보여\s*주세요|가르쳐|"
    r"되나요|되니|되니까|되니|되는지|"   # '처리되나요' 같은 수동형 의문
    r"\?$"                                  # 끝에 물음표
    r")",
    re.IGNORECASE,
)
# Expense 의도 — 익스펜스/expense 또는 expense 관련 명사·구.
# 단순 'expense 가 뭐야' 같은 정의 질문은 _COMPOSE_INTENT_RE 가 비어 자동 제외.
# 키워드 망라: 영문/한글 익스펜스 + 카드/영수증 + 지출/경비/비용 명사구 + 정산 + 출장.
_EXPENSE_CONTEXT_RE = re.compile(
    r"("
    r"expense|익스펜스|"
    r"법인\s*카드|개인\s*카드|영수증|"
    r"월\s*지출|지출\s*내역|지출\s*정리|지출\s*보고|지출\s*양식|지출\s*보고서|"
    r"경비\s*보고|경비\s*양식|경비\s*정산|경비\s*처리|경비\s*신청|경비\s*보고서|"
    r"비용\s*보고|비용\s*정리|비용\s*신청|비용\s*처리|비용\s*정산|"
    r"카드\s*사용\s*내역|카드\s*내역|"
    r"월말\s*정산|월간\s*정산|"
    r"정산서|"
    r"출장\s*경비|출장\s*보고"
    r")",
    re.IGNORECASE,
)
# 주간 회의록 보고 컨텍스트 — 회의 녹음 → 요약 → xlsx 양식 생성.
# 단순 '회의 언제야' 같은 정의/일정 질문은 _COMPOSE_INTENT_RE 미매치로 자동 제외.
_MEETING_CONTEXT_RE = re.compile(
    r"("
    r"주간\s*회의|"
    r"회의록|"
    r"회의\s*보고|회의\s*요약|회의\s*정리|"
    r"회의\s*녹음|미팅\s*보고|미팅\s*회의록"
    r")",
    re.IGNORECASE,
)
# 일일자금수지 비교·검증 컨텍스트 — 거래내역 + 일일자금수지(자금실적) xlsx → 당일잔액 기입+검증.
# 자금/자금실적/자금계획/일일자금수지/자금수지/자금일계표 등 명사 + 비교/검증/대조 동작.
_FUND_CONTEXT_RE = re.compile(
    r"("
    r"일일\s*자금\s*수지|자금\s*수지|"
    r"자금\s*실적|자금\s*계획|자금\s*일계표|일계표|"
    r"자금\s*현황|자금\s*마감|자금\s*집계|"
    r"당일\s*잔액|전일\s*잔액|증감액"
    r")",
    re.IGNORECASE,
)
# 비교·검증·대조 동작(또는 작성/기입 의도)이 함께 와야 트리거. '자금실적이 뭐야' 같은 질문 제외.
_FUND_ACTION_RE = re.compile(
    r"("
    r"비교|검증|대조|reconcile|"
    r"맞춰|맞추|채워|채우|메꿔|메워|입력|기입|"
    r"계산|산출|집계|정리|마감|완성|"
    r"작성|만들|수행|처리"
    r")",
    re.IGNORECASE,
)
# 첨부(xlsx 2개)가 있을 때만 쓰는 느슨한 자금 힌트 — 첨부가 강한 신호라 단어 폭을 넓힌다.
# '자금/잔액/수지/일계표/자금수지' 단독도 허용(첨부 게이팅으로 오탐 억제).
_FUND_LOOSE_RE = re.compile(
    r"(자금|잔액|수지|일계표|입출금|입·?출금)",
    re.IGNORECASE,
)
# 자금계획 '자동작성' fast-path — 월마감 자료로 다음 2개월 계획표를 새로 작성.
# reconcile(비교·검증)과 구분되는 결정적 신호는 '마감'(월마감 자료 참조)이다.
_FUND_PLAN_CONTEXT_RE = re.compile(r"(자금\s*계획|계획표)", re.IGNORECASE)
_FUND_PLAN_CLOSING_RE = re.compile(r"(월\s*마감|마감\s*자료|마감자료|마감)", re.IGNORECASE)
_FUND_PLAN_ACTION_RE = re.compile(
    r"(자동\s*작성|작성|생성|만들|전개|채워|채우|반영)", re.IGNORECASE
)
# 개인카드 영수증 대조·검증 fast-path — expense xlsx(내역 시트 + 영수증 첨부 시트)에서
# 개인카드 지출 날짜별 합계 ↔ 영수증 날짜별 합계를 대조. compose(양식 작성)와 구분되는
# 결정적 신호는 '대조/검증/매칭'(비교 동작) + '영수증/개인카드' 컨텍스트다.
_EXPENSE_RECON_CONTEXT_RE = re.compile(
    r"(개인\s*카드|영수증|expense|익스펜스)", re.IGNORECASE
)
_EXPENSE_RECON_ACTION_RE = re.compile(
    r"(대조|비교|검증|매칭|맞춰|맞추|점검|확인|reconcile)", re.IGNORECASE
)
# 공정 개념도 원클릭 — (개념도/컨셉도) AND (작성 동작) AND (요청 .txt 첨부).
_CONCEPT_CONTEXT_RE = re.compile(r"(개념도|컨셉도|concept\s*map)", re.IGNORECASE)
_CONCEPT_ACTION_RE = re.compile(r"(작성|만들|그려|그리|생성)")
# 제안서 본문 원클릭 — (제안서) AND (작성 동작) AND (요청 .txt 첨부). 개념도 감지 다음 순서.
_PROPOSAL_CONTEXT_RE = re.compile(r"제안서")
_intent_log = logging.getLogger("intent.leave_email")


def _detect_force_leave_tool(messages: list[dict]) -> dict | None:
    """user 의도에 따라 compose_leave_email 또는 send_leave_email tool_choice 를 강제.

    LLM 이 도구를 호출하지 않고 자연어로 양식/발송을 흉내내는 사고를 차단.
    - 발송 의도(발송/보내/전송) > 작성 의도(작성/만들/초안/양식). 둘 다 매치되면 send 우선.
    - 연차 컨텍스트(연차/휴가/사용 보고 등)가 history 어딘가에 있어야 활성화 — 일반 메시지와 혼동 방지.
    """
    if not messages:
        return None
    last = messages[-1]
    if last.get("role") != "user":
        return None
    last_text = last.get("content") or ""
    has_send = bool(_SEND_INTENT_RE.search(last_text))
    has_compose = bool(_COMPOSE_INTENT_RE.search(last_text))
    if not (has_send or has_compose):
        return None
    leave_context = any(_LEAVE_CONTEXT_RE.search(m.get("content") or "") for m in messages)
    if not leave_context:
        return None
    name = "send_leave_email" if has_send else "compose_leave_email"
    _intent_log.warning(
        "[intent] forcing tool_choice=%s (last_user=%r)",
        name, last_text[:80],
    )
    return {"type": "function", "function": {"name": name}}


def _detect_force_expense_tool(messages: list[dict]) -> dict | None:
    """expense 양식 작성 의도 감지 시 compose_expense_report 강제.

    v6 관측: LLM 이 tool_call 프로토콜을 안 따르고 JSON 인자를 본문(content) 으로 흘려보내
    화면이 깨지는 사고. tool_choice 를 named function 으로 강제하면 vLLM 이 무조건
    hermes tool_call 포맷으로 응답하도록 보장 → 본문 누출 자체를 차단.

    트리거 조건: (작성 의도) AND (expense 컨텍스트) AND (정보요청·발송 의도 아님).
    - 작성 의도: 작성/만들/써/해줘/부탁/처리/정리/생성/출력 등 _COMPOSE_INTENT_RE
    - expense 컨텍스트: expense/익스펜스/법인카드/개인카드/영수증/지출/경비/비용/정산 등
    - **anti-trigger 1**: '어떻게/뭐야/알려/?' 등 정보 요청 → 답변 모드 우선
    - **anti-trigger 2**: '발송/보내/전송' 등 send 의도 → expense 도구는 발송 안 함
    """
    if not messages:
        return None
    last = messages[-1]
    if last.get("role") != "user":
        return None
    last_text = last.get("content") or ""
    has_compose = bool(_COMPOSE_INTENT_RE.search(last_text))
    has_expense_ctx = bool(_EXPENSE_CONTEXT_RE.search(last_text))
    if not (has_compose and has_expense_ctx):
        return None
    # 정보 요청 — 'expense 어떻게 처리되나요?' 같은 의문문은 답변 모드.
    if _INFO_REQUEST_RE.search(last_text):
        return None
    # 발송 의도가 있으면 expense 도구는 적절치 않음(발송 기능 없음).
    # LLM 이 자연스럽게 '발송 기능은 1차 범위 외' 안내하도록 자연 모드로 패스.
    if _SEND_INTENT_RE.search(last_text):
        return None
    _intent_log.warning(
        "[intent] forcing tool_choice=compose_expense_report (last_user=%r)",
        last_text[:120],
    )
    return {"type": "function", "function": {"name": "compose_expense_report"}}


def _try_fast_expense_dispatch(
    system_prompt: str, msgs: list[dict]
) -> dict | None:
    """서버측 expense 파서로 사용자 메시지를 직접 인자로 변환.

    LLM 을 거치지 않고 compose_expense_report 를 호출하기 위함.
    파싱 실패 시 None → 기존 LLM 흐름으로 fallback.

    이점:
    - 입력 토큰 한도 무관(LLM 호출 0회) — expense_error_v10 (input 7836/8192) 근본 해결
    - 결정론적 — LLM 의 라인 누락/날짜 오매핑(v5)·prose leak(v6~v9) 모두 우회
    - 빠름 — LLM 호출 1~3초 → 서버 파서 ms 단위
    """
    from . import expense_parser as _ep

    if not msgs or msgs[-1].get("role") != "user":
        return None
    user_text = msgs[-1].get("content") or ""
    if not user_text.strip():
        return None

    today = _ep.extract_today_from_system(system_prompt or "")
    if today:
        default_year, default_month = today[0], today[1]
    else:
        now = _dt.date.today()
        default_year, default_month = now.year, now.month

    default_author = _ep.extract_user_name_from_system(system_prompt or "") or ""
    attachment_ids = _ep.extract_attachment_ids_from_system(system_prompt or "")

    args = _ep.parse_expense_message(
        user_text,
        default_year=default_year,
        default_month=default_month,
        default_author=default_author,
        attachment_ids=attachment_ids,
    )
    return args


# ── 진행 단계 마커 ──────────────────────────────────────────────────────────
# frontend(message.tsx) 와 같은 separator. 본문에 자연 등장하지 않는 제어문자.
PROGRESS_SENTINEL = "␜"  # FILE SEPARATOR — NOTICE_SENTINEL(␟) 과 구분.


def _progress_marker(steps: list[dict]) -> str:
    """현재 step 리스트 스냅샷을 한 줄 JSON 마커로 직렬화.

    형식: `␟{"type":"progress","steps":[{id,label,state},...]}␟\n`
    frontend 는 sentinel 쌍을 한 마커로 추출하고 본문에서 제거한다.
    """
    payload = _json_mod.dumps(
        {"type": "progress", "steps": steps}, ensure_ascii=False
    )
    return f"{PROGRESS_SENTINEL}{payload}{PROGRESS_SENTINEL}\n"


def _init_steps(spec: tuple[tuple[str, str], ...]) -> list[dict]:
    """spec((id,label), ...) → 초기 pending 상태 리스트."""
    return [
        {"id": sid, "label": label, "state": "pending"}
        for sid, label in spec
    ]


def _apply_step(steps: list[dict], step_id: str, state: str) -> None:
    """steps 리스트에서 step_id 의 state 를 in-place 갱신."""
    for s in steps:
        if s["id"] == step_id:
            s["state"] = state
            return


def _mark_active_as_error(steps: list[dict]) -> None:
    """예외 발생 시 마지막으로 active 였던 단계를 error 로 마킹."""
    for s in steps:
        if s["state"] == "active":
            s["state"] = "error"


async def _stream_fast_expense(args: dict):
    """파싱 성공 시 dispatch 만 호출하고 short_message 를 사용자에게 전달.

    LLM 호출 없음 — 토큰 한도/잘림 사고 원천 차단.
    진행 단계는 단순 2단계(parse+build / save) 흐름을 마커로 노출한다.
    """
    # 파서가 셋팅한 누락 라인 수를 dispatch 인자에서 분리(스키마 오염 방지) — 경고용으로만 사용.
    dropped_lines = args.pop("_dropped_lines", 0)

    steps = _init_steps((
        ("parse_compose", "양식 데이터 구성"),
        ("save_xlsx", "엑셀 양식 생성/저장"),
    ))
    _apply_step(steps, "parse_compose", "done")  # 이미 fast-path 직전에 parse 완료.
    _apply_step(steps, "save_xlsx", "active")
    yield _progress_marker(steps)

    import json as _json
    try:
        result = await dispatch("compose_expense_report", args)
    except Exception as e:
        logger.warning("[fast_expense] dispatch failed: %s", e)
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield f"⚠ 양식 생성 실패: {e}"
        return
    try:
        data = _json.loads(result)
        out = data.get("short_message") or data.get("error") or "(결과 없음)"
    except _json.JSONDecodeError:
        out = result

    _apply_step(steps, "save_xlsx", "done")
    yield _progress_marker(steps)
    if dropped_lines:
        # 무음 누락 방지 — 합계 정확성이 핵심인 경비 보고에서 일부 라인이 해석 실패했음을 명시.
        out = (
            f"{out}\n\n⚠ 입력한 지출 라인 중 {dropped_lines}건을 해석하지 못해 양식에서 빠졌습니다. "
            "날짜(YYYY-MM-DD 또는 M/D)·금액(숫자)·구분자(콤마 또는 ' / ')를 확인해 다시 보내주세요."
        )
    yield out


# ── 반자동 expense — 영수증 비전 초안 ────────────────────────────────────────
# 사용자가 지출 라인을 타이핑하지 않고 영수증 이미지만 첨부한 채 expense 작성을 요청하면,
# 비전(gemma3)이 금액·날짜를 자동 추출해 초안 표를 만들고 카테고리·지출사유는 사용자가
# 확정/보완하도록 되돌려준다(검증: 금액 84.6%, 날짜·카테고리는 사용자 확인 대상).
_DRAFT_PLACEHOLDER_PURPOSE = "(지출사유 입력)"
_DRAFT_PLACEHOLDER_DATE = "(날짜확인)"
_DRAFT_PLACEHOLDER_AMOUNT = "(금액확인)"
_DRAFT_PLACEHOLDER_VENDOR = "가맹점"


def _format_expense_draft(receipts: list, author: str, year: int, month: int) -> str:
    """비전 추출 결과를 사용자가 편집·재전송할 콤마 구분 초안으로 직렬화.

    출력은 parse_expense_message 가 그대로 다시 인식하는 형식이라, 사용자가 계정과목·
    지출사유만 보완해 "작성해줘" 와 함께 재전송하면 결정론 파이프라인이 xlsx 를 만든다.
    금액·날짜는 비전이 채우고, 누락 필드는 placeholder 로 남겨 무음 누락을 방지한다.
    """
    ok = [r for r in receipts if r.ok]
    failed = [r for r in receipts if not r.ok]

    lines: list[str] = []
    lines.append(f"{year}년 {month}월 {author} 익스펜스 초안입니다.")
    lines.append(
        f"영수증 {len(receipts)}장 중 {len(ok)}장에서 금액을 자동 인식했습니다. "
        "**금액은 자동, 날짜는 확인 권장, 계정과목·지출사유는 직접 확정**해 주세요."
    )
    lines.append("")
    lines.append("```")
    lines.append("개인카드 지출")
    for r in ok:
        date = r.date or _DRAFT_PLACEHOLDER_DATE
        amount = f"{int(r.amount)}" if r.amount is not None else _DRAFT_PLACEHOLDER_AMOUNT
        vendor = r.vendor or _DRAFT_PLACEHOLDER_VENDOR
        lines.append(
            f"{r.category_guess}, {date}, {_DRAFT_PLACEHOLDER_PURPOSE}, {amount}, {vendor}"
        )
    lines.append("```")
    lines.append("")
    lines.append(
        "위 표에서 **계정과목**(접대비·복리후생비·여비교통비·소모품비·차량유지비·"
        f"지급수수료·도서인쇄비·해당없음)을 확정하고, `{_DRAFT_PLACEHOLDER_PURPOSE}` 자리에 "
        "지출 사유를 적어주세요. 날짜가 어색하면 `M/D` 또는 `YYYY-MM-DD` 로 고쳐주세요."
    )
    if failed:
        names = ", ".join(r.filename for r in failed)
        lines.append("")
        lines.append(
            f"⚠ 금액을 읽지 못한 영수증 {len(failed)}장({names})은 초안에서 빠졌습니다. "
            "해당 건은 `계정과목, 날짜, 지출사유, 금액, 가맹점` 형식으로 직접 한 줄 추가해 주세요."
        )
    lines.append("")
    lines.append(
        "확정 후 **영수증을 그대로 첨부한 채** 위 내용을 \"이대로 작성해줘\" 와 함께 "
        "다시 보내주시면 엑셀 양식을 생성하고 영수증을 함께 임베드합니다."
    )
    return "\n".join(lines)


async def _stream_expense_draft(
    *, attachment_ids: list[int], author: str, year: int, month: int
):
    """영수증 비전 추출 → 편집 가능한 초안 반환. xlsx 는 사용자 확정 후 생성.

    OCR 은 영수증당 수 초 걸린다(최대 16장). 진행 마커 + keepalive 로 연결을 유지하는
    구조는 _stream_meeting 과 동일하다.
    """
    from . import expense_vision as _ev

    steps = _init_steps((
        ("ocr", "영수증 비전 인식"),
        ("draft", "초안 구성"),
    ))
    _apply_step(steps, "ocr", "active")
    yield _progress_marker(steps)

    loop = asyncio.get_running_loop()
    progress_queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue()

    def _on_progress(done: int, total: int) -> None:
        loop.call_soon_threadsafe(progress_queue.put_nowait, (done, total))

    task = asyncio.create_task(
        _ev.extract_receipts(
            attachment_ids, expense_year=year, on_progress=_on_progress
        )
    )
    while not task.done():
        getter = asyncio.create_task(progress_queue.get())
        done_set, _pending = await asyncio.wait(
            {getter, task}, timeout=25, return_when=asyncio.FIRST_COMPLETED
        )
        if getter in done_set:
            done, total = getter.result()
            # 진행 라벨에 카운트를 실어 보낸다(같은 step id 의 label 갱신).
            for s in steps:
                if s["id"] == "ocr":
                    s["label"] = f"영수증 비전 인식 ({done}/{total})"
            while not progress_queue.empty():
                done, total = progress_queue.get_nowait()
                for s in steps:
                    if s["id"] == "ocr":
                        s["label"] = f"영수증 비전 인식 ({done}/{total})"
            yield _progress_marker(steps)
        else:
            getter.cancel()
            if not task.done():
                yield "​"  # keepalive

    try:
        receipts = task.result()
    except Exception as e:
        logger.warning("[expense_draft] vision 실패: %s", e)
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield "⚠ 영수증 인식 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        return

    _apply_step(steps, "ocr", "done")
    _apply_step(steps, "draft", "active")
    yield _progress_marker(steps)

    if not receipts:
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield (
            "첨부된 영수증 이미지를 찾지 못했습니다. 영수증 사진(png/jpg)을 입력창에 "
            "첨부한 뒤 다시 요청해 주세요."
        )
        return

    _apply_step(steps, "draft", "done")
    yield _progress_marker(steps)
    yield _format_expense_draft(receipts, author, year, month)


def _try_fast_leave_dispatch(system_prompt: str, msgs: list[dict]) -> dict | None:
    """서버측 연차 파서로 사용자 메시지를 compose/send_leave_email 인자로 변환.

    Ollama(Gemma)는 tool_choice 강제가 없어 연차도 expense 처럼 결정론 처리한다.
    파싱 실패(날짜 없음 등) 시 None → 기존 LLM 흐름으로 fallback.
    """
    from . import leave_parser as _lp

    if not msgs or msgs[-1].get("role") != "user":
        return None
    user_text = msgs[-1].get("content") or ""
    if not user_text.strip():
        return None
    return _lp.parse_leave_message(user_text, system_prompt=system_prompt or "")


async def _stream_fast_leave(tool_name: str, args: dict):
    """연차 메일 compose/send fast-path. LLM 호출 없음.

    compose_leave_email → 초안 반환, send_leave_email → 실제 발송(서버가 본문 재생성).
    진행 단계는 PROGRESS_SENTINEL 마커로 노출.
    """
    is_send = tool_name == "send_leave_email"
    steps = _init_steps((
        ("compose", "메일 양식 구성"),
        ("send", "메일 발송") if is_send else ("done", "초안 완성"),
    ))
    _apply_step(steps, "compose", "done")
    _apply_step(steps, "send" if is_send else "done", "active")
    yield _progress_marker(steps)

    import json as _json
    try:
        result = await dispatch(tool_name, args)
    except Exception as e:
        logger.warning("[fast_leave] dispatch failed: %s", e)
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield f"⚠ 연차 메일 처리 실패: {e}"
        return

    try:
        data = _json.loads(result)
        out = (
            data.get("short_message")
            or data.get("composed")
            or data.get("detail")
            or data.get("error")
            or "(결과 없음)"
        )
    except _json.JSONDecodeError:
        out = result

    _apply_step(steps, "send" if is_send else "done", "done")
    yield _progress_marker(steps)
    yield out


def _detect_fund_plan_intent(messages: list[dict], system_prompt: str = "") -> bool:
    """자금계획 자동작성 의도 감지(월마감 자료 → 다음 2개월 계획표). LLM 우회 fast-path.

    reconcile(비교·검증)와 분리하기 위해 **'마감' 컨텍스트 + 자금계획/계획표 + 작성/생성**
    세 신호를 모두 요구한다. '비교/검증/대조'만 있는 요청은 여기 걸리지 않는다(reconcile 행).
    엄격(첨부 무관) 또는 xlsx 2개 첨부 게이팅 둘 중 하나로 잡는다.
    """
    if not messages:
        return False
    last = messages[-1]
    if last.get("role") != "user":
        return False
    text = last.get("content") or ""
    if not text:
        return False

    has_plan = bool(_FUND_PLAN_CONTEXT_RE.search(text))
    has_closing = bool(_FUND_PLAN_CLOSING_RE.search(text))
    has_action = bool(_FUND_PLAN_ACTION_RE.search(text))
    is_info = bool(_INFO_REQUEST_RE.search(text))
    if not (has_plan and has_closing and has_action) or is_info:
        return False
    _intent_log.warning("[intent] fund PLAN fast-path (last_user=%r)", text[:120])
    return True


async def _stream_fund_plan(*, attachment_ids: list[int]):
    """자금계획 + 월마감 첨부 → 다음 2개월 자금계획 자동작성. LLM 우회.

    진행 단계를 PROGRESS_SENTINEL 마커로 흘리고, 완료 시 작성 요약 + 다운로드 링크를 보낸다.
    """
    from .finance import plan_service as _ps

    if not attachment_ids or len(attachment_ids) < 2:
        yield (
            "엑셀 파일 2개를 입력창에 함께 첨부한 뒤 다시 요청해 주세요 — "
            "① 자금계획 엑셀(아직 다음 달 표가 비어 있는 파일), ② 월마감 자료."
        )
        return

    steps = _init_steps(_ps.PROGRESS_STEPS)
    yield _progress_marker(steps)
    _apply_step(steps, "fetch", "active")
    yield _progress_marker(steps)

    task = asyncio.create_task(
        _ps.generate_fund_plan(attachment_ids=attachment_ids)
    )
    advanced = False
    while not task.done():
        await asyncio.wait({task}, timeout=4, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            break
        if not advanced:
            for sid in ("fetch", "classify", "learn"):
                _apply_step(steps, sid, "done")
            _apply_step(steps, "generate", "active")
            yield _progress_marker(steps)
            advanced = True
        else:
            yield "​"  # zero-width keepalive

    try:
        result = task.result()
    except (ValueError, RuntimeError) as e:
        logger.warning("[fund-plan] generate failed: %s", e)
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield f"⚠ 자금계획 자동작성 실패: {e}"
        return
    except Exception as e:
        logger.warning("[fund-plan] unexpected error: %s", e)
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield "⚠ 자금계획 자동작성 중 오류가 발생했습니다. 파일 형식을 확인 후 다시 시도해 주세요."
        return

    for s in steps:
        if s["state"] != "error":
            s["state"] = "done"
    yield _progress_marker(steps)

    out = (
        f"{result.summary}\n\n"
        f"[{result.filename}]({result.download_url})"
    )
    yield out


def _detect_fund_reconcile_intent(messages: list[dict], system_prompt: str = "") -> bool:
    """일일자금수지 비교·검증 의도 감지(Ollama: tools 미지원 → 결정론 fast-path).

    두 갈래로 잡는다(LLM 은 첨부 xlsx 를 못 읽으므로 서버 처리로 직행):
    1) 엄격: (자금 컨텍스트) AND (동작) AND (정보 질문 아님) — 첨부 없어도 동작.
    2) 관대: 첨부 xlsx 2개+ AND (자금 컨텍스트 OR 느슨한 자금 힌트) AND (동작).
       첨부 2개는 이 기능의 강한 신호라, '두 엑셀로 잔액 채워줘' 같은 느슨한 표현도 잡는다.
       (첨부 게이팅으로 일반 '두 파일 비교해줘' 등 오탐은 억제 — 자금 단어가 있어야 함)
    """
    if not messages:
        return False
    last = messages[-1]
    if last.get("role") != "user":
        return False
    text = last.get("content") or ""
    if not text:
        return False

    has_ctx = bool(_FUND_CONTEXT_RE.search(text))
    has_action = bool(_FUND_ACTION_RE.search(text))
    is_info = bool(_INFO_REQUEST_RE.search(text))

    # 1) 엄격 — 기존 동작(첨부 무관).
    if has_ctx and has_action and not is_info:
        _intent_log.warning("[intent] fund fast-path (strict, last_user=%r)", text[:120])
        return True

    # 2) 관대 — xlsx 2개+ 첨부 게이팅. 정보 질문이어도 명시 동작 동사가 있으면 요청으로 본다.
    try:
        from . import expense_parser as _ep
        n_attach = len(_ep.extract_attachment_ids_from_system(system_prompt or ""))
    except Exception:
        n_attach = 0
    if n_attach >= 2 and has_action and (has_ctx or _FUND_LOOSE_RE.search(text)):
        _intent_log.warning(
            "[intent] fund fast-path (lenient, attachments=%d, last_user=%r)",
            n_attach, text[:120],
        )
        return True
    return False


def _extract_fund_date(text: str) -> str | None:
    """사용자 메시지에서 대상 일자(YYYY-MM-DD)를 뽑는다(없으면 None → 자동 탐지)."""
    m = re.search(r"(20\d{2})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    # '5월 28일' 처럼 연도 생략 — 연도는 서비스가 거래내역에서 보정하므로 여기선 None.
    return None


async def _stream_fund_reconcile(
    *, attachment_ids: list[int], requested_date: str | None,
):
    """거래내역 + 일일자금수지 첨부 → 당일잔액 기입 + 계획↔실적 검증. LLM 우회.

    진행 단계를 PROGRESS_SENTINEL 마커로 흘리고, 완료 시 검증 요약 + 다운로드 링크를 보낸다.
    """
    from .finance import reconcile_service as _rs

    if not attachment_ids or len(attachment_ids) < 2:
        yield (
            "엑셀 파일 2개를 입력창에 함께 첨부한 뒤 다시 요청해 주세요 — "
            "① 은행 거래내역, ② 작성할 일일자금수지(당일잔액·증감액이 비어 있는 파일)."
        )
        return

    steps = _init_steps(_rs.PROGRESS_STEPS)
    yield _progress_marker(steps)

    # 단계 진행은 서비스 내부 단계와 1:1 콜백이 없으므로, 굵직한 전이를 여기서 마킹한다.
    _apply_step(steps, "fetch", "active")
    yield _progress_marker(steps)

    task = asyncio.create_task(
        _rs.reconcile_fund_daily(
            attachment_ids=attachment_ids, requested_date=requested_date
        )
    )
    # 대조·기입은 수 초~수십 초. keepalive 로 연결 유지하며 단계 마커를 점진 노출.
    advanced = False
    while not task.done():
        await asyncio.wait({task}, timeout=4, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            break
        if not advanced:
            for sid in ("fetch", "classify", "reconcile"):
                _apply_step(steps, sid, "done")
            _apply_step(steps, "fill", "active")
            yield _progress_marker(steps)
            advanced = True
        else:
            yield "​"  # zero-width keepalive

    try:
        result = task.result()
    except (ValueError, RuntimeError) as e:
        logger.warning("[fund] reconcile failed: %s", e)
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield f"⚠ 일일자금수지 비교·검증 실패: {e}"
        return
    except Exception as e:  # 예기치 못한 오류 — stack trace 노출 금지.
        logger.warning("[fund] unexpected error: %s", e)
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield "⚠ 비교·검증 중 오류가 발생했습니다. 파일 형식을 확인 후 다시 시도해 주세요."
        return

    for s in steps:
        if s["state"] != "error":
            s["state"] = "done"
    yield _progress_marker(steps)

    # 검증 요약 + 채워진 파일 + 검증 워크북 다운로드 링크(프론트가 markdown link 를 카드로 렌더).
    out = (
        f"{result.summary}\n\n"
        f"[{result.filename}]({result.download_url})\n\n"
        f"[검증결과_{result.verify_filename}]({result.verify_download_url})"
    )
    yield out


def _detect_expense_reconcile_intent(
    messages: list[dict], system_prompt: str = ""
) -> bool:
    """개인카드 영수증 대조·검증 의도 감지(Ollama tools 미지원 → 결정론 fast-path).

    트리거: (영수증/개인카드 컨텍스트) AND (대조/검증/비교 동작) AND (xlsx 첨부 ≥1)
            AND (정보 질문 아님). 첨부 게이팅으로 '영수증 대조가 뭐야' 등 오탐 억제.
    compose(양식 작성) 보다 먼저 분기해야 하므로 dispatch 상단에서 호출한다.
    """
    if not messages:
        return False
    last = messages[-1]
    if last.get("role") != "user":
        return False
    text = last.get("content") or ""
    if not text:
        return False

    has_ctx = bool(_EXPENSE_RECON_CONTEXT_RE.search(text))
    has_action = bool(_EXPENSE_RECON_ACTION_RE.search(text))
    is_info = bool(_INFO_REQUEST_RE.search(text))
    if not (has_ctx and has_action) or is_info:
        return False

    try:
        from . import expense_parser as _ep
        n_attach = len(_ep.extract_attachment_ids_from_system(system_prompt or ""))
    except Exception:
        n_attach = 0
    if n_attach < 1:
        return False

    _intent_log.warning(
        "[intent] expense-reconcile fast-path (attachments=%d, last_user=%r)",
        n_attach, text[:120],
    )
    return True


async def _stream_expense_reconcile(*, attachment_ids: list[int]):
    """expense xlsx 첨부 → 개인카드 내역 ↔ 영수증 날짜별 대조 + OCR 교차검증. LLM 우회.

    진행 단계를 PROGRESS_SENTINEL 마커로 흘리고, 완료 시 대조 요약 + 다운로드 링크를 보낸다.
    """
    from . import expense_reconcile as _er

    if not attachment_ids:
        yield (
            "개인카드 내역과 영수증이 함께 담긴 expense 엑셀(.xlsx) 파일을 "
            "입력창에 첨부한 뒤 다시 요청해 주세요."
        )
        return

    steps = _init_steps(_er.PROGRESS_STEPS)
    yield _progress_marker(steps)
    _apply_step(steps, "fetch", "active")
    yield _progress_marker(steps)

    task = asyncio.create_task(
        _er.reconcile_expense_receipts(attachment_ids=attachment_ids)
    )
    # 파싱+OCR 은 수 초~수십 초(영수증 장수 비례). keepalive 로 연결 유지하며 단계 노출.
    advanced = False
    while not task.done():
        await asyncio.wait({task}, timeout=4, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            break
        if not advanced:
            for sid in ("fetch", "parse"):
                _apply_step(steps, sid, "done")
            _apply_step(steps, "ocr", "active")
            yield _progress_marker(steps)
            advanced = True
        else:
            yield "​"  # zero-width keepalive

    try:
        result = task.result()
    except (ValueError, RuntimeError) as e:
        logger.warning("[expense-recon] failed: %s", e)
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield f"⚠ 개인카드 영수증 대조·검증 실패: {e}"
        return
    except Exception as e:  # 예기치 못한 오류 — stack trace 노출 금지.
        logger.warning("[expense-recon] unexpected error: %s", e)
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield "⚠ 대조·검증 중 오류가 발생했습니다. 파일 양식을 확인 후 다시 시도해 주세요."
        return

    for s in steps:
        if s["state"] != "error":
            s["state"] = "done"
    yield _progress_marker(steps)

    out = (
        f"{result.summary}\n\n"
        f"[{result.filename}]({result.download_url})"
    )
    yield out


def _detect_concept_map_intent(
    messages: list[dict], system_prompt: str = ""
) -> bool:
    """공정 개념도 작성 의도 감지 — 요청 원문 .txt 첨부 게이팅으로 오탐 억제."""
    if not messages or messages[-1].get("role") != "user":
        return False
    text = messages[-1].get("content") or ""
    if not (_CONCEPT_CONTEXT_RE.search(text) and _CONCEPT_ACTION_RE.search(text)):
        return False
    if _INFO_REQUEST_RE.search(text):
        return False
    try:
        from . import expense_parser as _ep
        n_attach = len(_ep.extract_attachment_ids_from_system(system_prompt or ""))
    except Exception:
        n_attach = 0
    if n_attach < 1:
        return False
    _intent_log.warning(
        "[intent] concept-map fast-path (attachments=%d, last_user=%r)", n_attach, text[:120]
    )
    return True


async def _stream_concept_map(*, attachment_ids: list[int]):
    """요청 .txt 첨부 → AI 스펙 → draw.io 원본 + PNG. 단계는 PROGRESS_SENTINEL 로 흘린다."""
    from .diagram import concept_service as _cs

    def render(result) -> str:
        links = "\n".join(f"[{f.filename}]({f.download_url})" for f in result.files)
        if result.record_id is not None:
            from .confirmed import confirm_link
            links += "\n" + confirm_link(result.record_id)
        return f"{result.summary}\n\n{links}"

    async for chunk in _stream_staged_job(
        _cs.PROGRESS_STEPS,
        lambda on_stage: _cs.build_concept_map(attachment_ids=attachment_ids, on_stage=on_stage),
        fail_label="개념도 작성",
        log_tag="concept",
        render=render,
    ):
        yield chunk


def _detect_proposal_body_intent(
    messages: list[dict], system_prompt: str = ""
) -> bool:
    """제안서 본문 작성 의도 감지 — 요청 원문 .txt 첨부 게이팅으로 오탐 억제.
    개념도 감지보다 뒤에 둔다("제안서용 개념도 만들어줘" 는 개념도)."""
    if not messages or messages[-1].get("role") != "user":
        return False
    text = messages[-1].get("content") or ""
    if not (_PROPOSAL_CONTEXT_RE.search(text) and _CONCEPT_ACTION_RE.search(text)):
        return False
    if _INFO_REQUEST_RE.search(text):
        return False
    try:
        from . import expense_parser as _ep
        n_attach = len(_ep.extract_attachment_ids_from_system(system_prompt or ""))
    except Exception:
        n_attach = 0
    if n_attach < 1:
        return False
    _intent_log.warning(
        "[intent] proposal-body fast-path (attachments=%d, last_user=%r)", n_attach, text[:120]
    )
    return True


async def _stream_proposal_body(*, attachment_ids: list[int]):
    """요청 .txt 첨부 → 요구 항목 → 유사 사례 → 본문(AI) → 점검 → .docx."""
    from .proposal import service as _ps

    async for chunk in _stream_staged_job(
        _ps.PROGRESS_STEPS,
        lambda on_stage: _ps.build_proposal_body(attachment_ids=attachment_ids, on_stage=on_stage),
        fail_label="제안서 본문 작성",
        log_tag="proposal",
        render=_ps.result_message,
    ):
        yield chunk


async def _stream_staged_job(progress_steps, start, *, fail_label: str, log_tag: str, render):
    """AI 원클릭 작업 공용 스트리밍 — 단계 마커 + keepalive + 오류 문구.

    start(on_stage) 는 결과를 돌려주는 코루틴. 제안서 모델(사고 모드)은 수 분 걸리므로
    4초마다 단계 변화 또는 zero-width keepalive 를 흘려 연결을 유지한다.
    """
    steps = _init_steps(progress_steps)
    order = [sid for sid, _ in progress_steps]
    changed = {"v": True}

    def on_stage(sid: str) -> None:
        if sid not in order:
            return
        idx = order.index(sid)
        for s in steps[:idx]:
            s["state"] = "done"
        steps[idx]["state"] = "active"
        changed["v"] = True

    yield _progress_marker(steps)
    task = asyncio.create_task(start(on_stage))
    while not task.done():
        await asyncio.wait({task}, timeout=4, return_when=asyncio.FIRST_COMPLETED)
        if changed["v"]:
            changed["v"] = False
            yield _progress_marker(steps)
        elif not task.done():
            yield "​"  # zero-width keepalive

    try:
        result = task.result()
    except ValueError as e:
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield f"⚠ {fail_label} 실패: {e}"
        return
    except Exception as e:  # 예기치 못한 오류 — stack trace 노출 금지.
        logger.warning("[%s] unexpected error: %r", log_tag, e)
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield f"⚠ {fail_label} 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        return

    for s in steps:
        s["state"] = "done"
    yield _progress_marker(steps)
    yield render(result)


async def _stream_codex(intent, req: GenerateRequest, msgs: list[dict]):
    """Codex 위임(웹 검색·이미지·심층 분석) — 20초~수 분 걸려 단계 마커 + keepalive 로 흘린다."""
    if intent.kind == "search":
        def start(on_stage):
            return codex_delegate.run_search(intent.text, on_stage, user_id=req.user_id)
    elif intent.kind == "image":
        def start(on_stage):
            return codex_delegate.run_image(intent.text, req.user_id, on_stage)
    else:
        # Codex 실패 시 gemma 가 같은 질문(명령 접두 제거)에 답한다.
        fallback_msgs = [*msgs[:-1], {**msgs[-1], "content": intent.text}]

        async def fallback() -> str:
            parts = [
                d async for d in stream_chat(
                    system_prompt=req.systemPrompt, messages=fallback_msgs
                )
            ]
            return "".join(parts)

        def start(on_stage):
            return codex_delegate.run_ask(intent.text, on_stage, fallback, user_id=req.user_id)

    async for chunk in _stream_staged_job(
        codex_delegate.PROGRESS_STEPS[intent.kind],
        start,
        fail_label=codex_delegate.FAIL_LABELS[intent.kind],
        log_tag="codex",
        render=lambda text: text,
    ):
        yield chunk


def _detect_meeting_intent(messages: list[dict]) -> bool:
    """주간 회의록 보고 작성 의도 감지.

    트리거 조건: (작성 의도) AND (회의 컨텍스트) AND (정보 요청 아님).
    감지되면 LLM 을 거치지 않고 서버에서 STT→요약→xlsx fast-path 를 탄다.
    LLM 은 오디오를 들을 수 없어 도구 인자 추출이 원천 불가능하므로, expense 처럼
    tool_choice 강제가 아니라 결정론적 서버 처리로 직행한다.
    """
    if not messages:
        return False
    last = messages[-1]
    if last.get("role") != "user":
        return False
    text = last.get("content") or ""
    if not (_MEETING_CONTEXT_RE.search(text) and _COMPOSE_INTENT_RE.search(text)):
        return False
    # '주간 회의 언제야?' 같은 정보 요청은 답변 모드.
    if _INFO_REQUEST_RE.search(text):
        return False
    _intent_log.warning("[intent] meeting report fast-path (last_user=%r)", text[:120])
    return True


async def _stream_meeting(
    *,
    attachment_ids: list[int],
    today: tuple[int, int, int] | None,
    author: str,
):
    """회의 녹음 첨부 → 회의록 xlsx 생성. LLM 호출 없음(STT/요약은 service 내부 처리).

    진행 상태는 PROGRESS_SENTINEL 마커로 frontend ProgressCard 에 흘려보낸다.
    각 단계 전이는 service 의 on_progress 콜백으로 받아 asyncio.Queue 에 넣고,
    이 generator 가 queue 와 task 를 동시에 대기하며 마커를 yield 한다.
    """
    # 지연 import — faster-whisper 등 무거운 의존성을 모듈 로드 시점에 끌어오지 않는다.
    from . import meeting_service

    if not attachment_ids:
        yield (
            "회의 녹음 파일을 입력창에 첨부한 뒤 다시 요청해 주세요. "
            "(m4a, mp3, wav 등 오디오 파일 지원)"
        )
        return

    if today:
        meeting_date = f"{today[0]:04d}-{today[1]:02d}-{today[2]:02d}"
    else:
        meeting_date = _dt.date.today().isoformat()

    steps = _init_steps(meeting_service.PROGRESS_STEPS)
    # 초기 pending 상태를 먼저 흘려 UI 가 카드 골격을 즉시 그릴 수 있게 한다.
    yield _progress_marker(steps)

    # service 콜백 → queue. service 가 다른 스레드(executor)에서 호출할 수 있어
    # thread-safe 한 call_soon_threadsafe + put_nowait 경로로 push.
    loop = asyncio.get_running_loop()
    progress_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    def _on_progress(step_id: str, state: str) -> None:
        loop.call_soon_threadsafe(progress_queue.put_nowait, (step_id, state))

    # STT+요약은 수 분이 걸린다. 그 동안 스트림이 침묵하면 프론트↔백엔드 fetch 연결이
    # idle timeout(undici bodyTimeout 기본 5분)으로 끊겨 'terminated' 오류가 난다.
    # 작업을 백그라운드 태스크로 돌리고 진행 마커 + 25s keepalive 로 연결을 유지한다.
    task = asyncio.create_task(
        meeting_service.compose_meeting_report(
            author=author,
            meeting_date=meeting_date,
            attachment_ids=attachment_ids,
            on_progress=_on_progress,
        )
    )
    # 단일 wait 로 (queue 항목 도착 OR task 종료) 둘 다 깨우도록 한다.
    while not task.done():
        getter = asyncio.create_task(progress_queue.get())
        done, _pending = await asyncio.wait(
            {getter, task},
            timeout=25,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if getter in done:
            step_id, state = getter.result()
            _apply_step(steps, step_id, state)
            # 같은 tick 에 도착한 추가 이벤트는 한 마커로 묶어 보낸다(트래픽/깜빡임 절감).
            while not progress_queue.empty():
                step_id, state = progress_queue.get_nowait()
                _apply_step(steps, step_id, state)
            yield _progress_marker(steps)
        else:
            getter.cancel()
            if not task.done():
                yield "\u200b"  # zero-width space — UI 에 보이지 않는 keepalive

    # 남은 progress 이벤트 모두 반영(task 가 완료된 직후 도착한 done 등).
    while not progress_queue.empty():
        step_id, state = progress_queue.get_nowait()
        _apply_step(steps, step_id, state)

    try:
        result = task.result()
    except (ValueError, RuntimeError) as e:
        logger.warning("[meeting] build failed: %s", e)
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield f"⚠ 회의 보고 생성 실패: {e}"
        return
    except Exception as e:  # 예기치 못한 오류 — stack trace 노출 금지.
        logger.warning("[meeting] unexpected error: %s", e)
        _mark_active_as_error(steps)
        yield _progress_marker(steps)
        yield "⚠ 회의 보고 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        return

    # 모든 단계 완료 상태로 한 번 더 보낸다(콜백 마지막 done 이 큐와 task 종료 사이
    # 끼었을 경우 안전 보강 — idempotent).
    for s in steps:
        if s["state"] != "error":
            s["state"] = "done"
    yield _progress_marker(steps)

    minutes = result.audio_duration_sec / 60
    yield (
        f"✓ 주간 회의록 보고가 생성되었습니다. "
        f"(회의 날짜 {result.meeting_date}, 녹음 {minutes:.0f}분 분량)\n\n"
        f"[{result.filename}]({result.download_url})"
    )


@app.post("/v1/generate")
async def generate(req: GenerateRequest, _auth: None = Depends(require_internal_token)):
    msgs = [m.model_dump() for m in req.messages]

    # ── Codex 위임 fast-path — 웹 검색·이미지 생성·심층 분석(gemma 한계 보완).
    #    '@이미지/@검색/@codex' 명령은 다른 흐름보다 우선. 브리지 미설정이면 비활성.
    from . import expense_parser as _ep_codex

    codex_intent = codex_delegate.detect_intent(
        msgs,
        has_attachments=bool(
            _ep_codex.extract_attachment_ids_from_system(req.systemPrompt or "")
        ),
    )
    if codex_intent is not None:
        logger.warning(
            "[codex] TAKING FAST-PATH: kind=%s user_id=%s", codex_intent.kind, req.user_id
        )
        return StreamingResponse(
            _stream_codex(codex_intent, req, msgs),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    # ── 자금계획 자동작성 fast-path — 자금계획 + 월마감 xlsx 첨부 기반. LLM 우회.
    #    '마감' 컨텍스트로 reconcile 보다 먼저 분기(작성 vs 비교·검증 구분).
    if _detect_fund_plan_intent(msgs, req.systemPrompt or ""):
        from . import expense_parser as _ep

        attachment_ids = _ep.extract_attachment_ids_from_system(req.systemPrompt or "")
        logger.warning("[fund-plan] TAKING FAST-PATH: attachments=%s", attachment_ids)
        return StreamingResponse(
            _stream_fund_plan(attachment_ids=attachment_ids),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    # ── 일일자금수지 비교·검증 fast-path — 거래내역 + 일일자금수지 xlsx 첨부 기반. LLM 우회.
    if _detect_fund_reconcile_intent(msgs, req.systemPrompt or ""):
        from . import expense_parser as _ep

        attachment_ids = _ep.extract_attachment_ids_from_system(req.systemPrompt or "")
        last_text = msgs[-1].get("content") or ""
        requested_date = _extract_fund_date(last_text)
        logger.warning(
            "[fund] TAKING FAST-PATH: attachments=%s date=%s",
            attachment_ids, requested_date,
        )
        return StreamingResponse(
            _stream_fund_reconcile(
                attachment_ids=attachment_ids, requested_date=requested_date
            ),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    # ── 개인카드 영수증 대조·검증 fast-path — expense xlsx 1개 기반. LLM 우회.
    #    compose(양식 작성)보다 먼저 분기(대조·검증 vs 작성 구분).
    if _detect_expense_reconcile_intent(msgs, req.systemPrompt or ""):
        from . import expense_parser as _ep

        attachment_ids = _ep.extract_attachment_ids_from_system(req.systemPrompt or "")
        logger.warning(
            "[expense-recon] TAKING FAST-PATH: attachments=%s", attachment_ids
        )
        return StreamingResponse(
            _stream_expense_reconcile(attachment_ids=attachment_ids),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    # ── 공정 개념도 fast-path — 요청 원문 .txt 첨부 기반. AI 는 스펙만, 도면은 draw.io.
    if _detect_concept_map_intent(msgs, req.systemPrompt or ""):
        from . import expense_parser as _ep

        attachment_ids = _ep.extract_attachment_ids_from_system(req.systemPrompt or "")
        logger.warning("[concept] TAKING FAST-PATH: attachments=%s", attachment_ids)
        return StreamingResponse(
            _stream_concept_map(attachment_ids=attachment_ids),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    # ── 제안서 본문 fast-path — 요청 원문 .txt 첨부 → 요구 항목 → 유사 사례 → 본문 → .docx.
    if _detect_proposal_body_intent(msgs, req.systemPrompt or ""):
        from . import expense_parser as _ep

        attachment_ids = _ep.extract_attachment_ids_from_system(req.systemPrompt or "")
        logger.warning("[proposal] TAKING FAST-PATH: attachments=%s", attachment_ids)
        return StreamingResponse(
            _stream_proposal_body(attachment_ids=attachment_ids),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    # ── 주간 회의록 보고 fast-path — 오디오 첨부 기반. LLM 우회.
    if _detect_meeting_intent(msgs):
        from . import expense_parser as _ep

        attachment_ids = _ep.extract_attachment_ids_from_system(req.systemPrompt or "")
        today = _ep.extract_today_from_system(req.systemPrompt or "")
        author = _ep.extract_user_name_from_system(req.systemPrompt or "") or ""
        logger.warning(
            "[meeting] TAKING FAST-PATH: attachments=%s today=%s author=%s",
            attachment_ids, today, author,
        )
        return StreamingResponse(
            _stream_meeting(attachment_ids=attachment_ids, today=today, author=author),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    # 연차 메일 의도 우선(키워드가 더 구체) → 그 다음 expense.
    forced_tool_choice = (
        _detect_force_leave_tool(msgs) or _detect_force_expense_tool(msgs)
    )

    # ── Fast-path — 연차 메일(compose/send) 이 강제됐고 파싱 가능하면 LLM 우회.
    # Ollama(tool_choice 미지원) 대비 + 결정론 처리. 파싱 실패 시 LLM 흐름 fallback.
    if (
        isinstance(forced_tool_choice, dict)
        and forced_tool_choice.get("function", {}).get("name")
        in ("compose_leave_email", "send_leave_email")
    ):
        leave_name = forced_tool_choice["function"]["name"]
        fast_leave_args = _try_fast_leave_dispatch(req.systemPrompt or "", msgs)
        if fast_leave_args is not None:
            logger.warning(
                "[fast_leave] TAKING FAST-PATH (skip LLM): tool=%s date=%s dur=%s kind=%s",
                leave_name, fast_leave_args.get("date"),
                fast_leave_args.get("duration_days"), fast_leave_args.get("report_kind"),
            )
            return StreamingResponse(
                _stream_fast_leave(leave_name, fast_leave_args),
                media_type="text/plain; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )
        else:
            logger.warning("[fast_leave] parser returned None → falling back to LLM.")

    # ── Fast-path — expense 도구가 강제됐고 사용자 메시지가 파싱 가능하면
    # LLM 우회하고 서버에서 dispatch 한다. 토큰 한도 무관.
    if (
        isinstance(forced_tool_choice, dict)
        and forced_tool_choice.get("function", {}).get("name") == "compose_expense_report"
    ):
        fast_args = _try_fast_expense_dispatch(req.systemPrompt or "", msgs)
        if fast_args is not None:
            # uvicorn 기본 로그 레벨이 INFO 라 .info 도 보이지만, fast-path 진입은
            # 추적이 중요하므로 warning 으로 노출 — 화면 logs 에 즉시 확인 가능.
            logger.warning(
                "[fast_expense] TAKING FAST-PATH (skip LLM): "
                "author=%s year=%d month=%d lines=%d receipts=%d",
                fast_args.get("author"),
                fast_args.get("year"),
                fast_args.get("month"),
                len(fast_args.get("lines", [])),
                len(fast_args.get("receipt_attachment_ids", [])),
            )
            return StreamingResponse(
                _stream_fast_expense(fast_args),
                media_type="text/plain; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )

        # ── 반자동 초안 — 라인 타이핑은 없지만 영수증 이미지가 첨부됐다면 비전으로
        # 금액·날짜를 추출해 편집 가능한 초안을 돌려준다(사용자가 계정과목·사유 확정).
        from . import expense_parser as _ep_draft

        draft_attachment_ids = _ep_draft.extract_attachment_ids_from_system(
            req.systemPrompt or ""
        )
        if draft_attachment_ids:
            today = _ep_draft.extract_today_from_system(req.systemPrompt or "")
            if today:
                d_year, d_month = today[0], today[1]
            else:
                _now = _dt.date.today()
                d_year, d_month = _now.year, _now.month
            # 메시지에서 'YYYY년 M월' 우선(사용자가 제출 월을 명시한 경우).
            _ym = _ep_draft._YM_RE.search(
                next((m.get("content", "") for m in reversed(msgs)
                      if m.get("role") == "user"), "")
            )
            if _ym:
                d_year, d_month = int(_ym.group(1)), int(_ym.group(2))
            d_author = (
                _ep_draft.extract_user_name_from_system(req.systemPrompt or "") or ""
            )
            logger.warning(
                "[expense_draft] TAKING VISION-DRAFT PATH: author=%s year=%d month=%d receipts=%d",
                d_author, d_year, d_month, len(draft_attachment_ids),
            )
            return StreamingResponse(
                _stream_expense_draft(
                    attachment_ids=draft_attachment_ids,
                    author=d_author,
                    year=d_year,
                    month=d_month,
                ),
                media_type="text/plain; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )
        else:
            # 파싱 실패 시 사유를 알 수 없으면 디버깅 곤란 — 사용자 메시지 head/tail 로깅.
            last_user = next(
                (m.get("content", "") for m in reversed(msgs)
                 if m.get("role") == "user"),
                "",
            )
            logger.warning(
                "[fast_expense] parser returned None → falling back to LLM. "
                "user_msg head=%r len=%d",
                last_user[:200], len(last_user),
            )

    async def gen():
        async for delta in stream_chat_with_tools(
            system_prompt=req.systemPrompt,
            messages=msgs,
            tools=TOOLS,
            dispatch=dispatch,
            temperature=req.temperature,
            top_p=req.top_p,
            seed=req.seed,
            tool_choice=forced_tool_choice,
        ):
            yield delta

    return StreamingResponse(
        gen(),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


_TITLE_SYSTEM_PROMPT = (
    "다음 대화 흐름을 한국어 한 줄 제목(20자 이하)으로 압축한다. "
    "따옴표·마침표·이모지·접두어 없이 본문만. 의문문/명령문 가능. "
    "사용자가 한국어가 아니면 사용자 언어를 따른다."
)


# 응답 본문에 섞이는 진행 표시(␜{"type": "progress", …}␜)·끊김 안내(␟ 이후)·첨부 링크는 제목 재료가 아니다
# (2026-10-01: 원클릭 작업 대화 제목이 '}, {"id": "verify", "label": …' 같은 JSON 조각으로 저장됨).
_PROGRESS_RE = re.compile(r"␜.*?␜", re.S)
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MACHINE_RE = re.compile(r'[{}\[\]]|"\s*:|\b(state|label|type|steps)\b')
TITLE_MAX = 40


def title_source(text: str) -> str:
    """제목 재료로 쓸 사람 말만 남긴다."""
    text = _PROGRESS_RE.sub(" ", text.split("␟", 1)[0])
    text = re.sub(r"␜.*", " ", text, flags=re.S)          # 닫히지 않은 진행 표시
    return re.sub(r"\s+", " ", _LINK_RE.sub(r"\1", text)).strip()


def fallback_title(user_text: str) -> str:
    """첫 질문으로 만든 제목(모델 제목이 비었거나 기계 문자열일 때)."""
    t = re.sub(r"\s+", " ", user_text).strip().lstrip("@").strip()
    t = re.split(r"(?<=[.?!。])\s|\n", t, maxsplit=1)[0]
    return t if len(t) <= 24 else t[:24].rstrip() + "…"


def usable_title(t: str) -> bool:
    return bool(t) and not _MACHINE_RE.search(t)


@app.post("/v1/title")
async def title(req: TitleRequest, _auth: None = Depends(require_internal_token)):
    """대화 제목 1줄 생성. 비스트림으로 모은 뒤 단일 문자열 반환."""
    msgs = [{**m.model_dump(), "content": title_source(m.content)} for m in req.messages]
    msgs = [m for m in msgs if m["content"]]
    user_text = next((m["content"] for m in msgs if m["role"] == "user"), "")
    chunks: list[str] = []
    if msgs:
        async for delta in stream_chat(system_prompt=_TITLE_SYSTEM_PROMPT, messages=msgs):
            chunks.append(delta)
    raw = "".join(chunks).strip().splitlines()[0] if "".join(chunks).strip() else ""
    # 따옴표/한 줄 보정.
    cleaned = raw.strip().strip("\"'`").strip()
    if not usable_title(cleaned):
        cleaned = fallback_title(user_text)
    if len(cleaned) > TITLE_MAX:
        cleaned = cleaned[:TITLE_MAX].rstrip() + "…"
    return {"title": cleaned}
