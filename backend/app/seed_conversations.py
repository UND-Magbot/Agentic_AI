"""대화 시드 — 사이드바 RECENT 시연용 가상 대화.

기존 `infra/postgres/init/03_conversations.sql` 의 INSERT 부분을 Python 으로 옮긴다.
init SQL 은 Postgres 볼륨이 비어있을 때만 실행되므로, 이미 사용 중이던 볼륨에는
03 이 추가되어도 적용되지 않는다. backend 가 부팅할 때마다 멱등 시드를 보장하면
어떤 환경에서도 RECENT 가 빈 상태로 남지 않는다.

설계:
- conversations 는 (user_id, title) 조합으로 멱등 보장 (이미 있으면 스킵).
- messages 는 conversation 에 메시지가 0건일 때만 일괄 INSERT (그 외는 사용자가 직접 쌓은 흔적이라 보존).
- updated_at 은 mock-recent.ts 의 minAgo(N) 패턴을 NOW() - INTERVAL 로 옮김.
- created_at 은 conversation.started_at + offset_min 으로 부여 → SQL 정렬과 호환.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import Conversation, Message, User, UserDomain


# --------------------------------------------------------------------------
# 1) conversation 시드 — 7건 (mock-recent.ts 와 동일 제목/도메인)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _ConvSeed:
    username: str       # 소속 시드 사용자
    title: str
    domain: UserDomain
    minutes_ago: int    # updated_at = NOW() - minutes_ago


_CONVS: tuple[_ConvSeed, ...] = (
    # 도메인별 — 4개 도메인 각자 소속 사용자에게 시드.
    _ConvSeed("finance_admin", "3월 부서 예산 집행률",    UserDomain.finance,  35),
    _ConvSeed("finance_admin", "거래처 A 미수금 정리",    UserDomain.finance, 220),
    _ConvSeed("alex",          "Q2 파이프라인 진단",      UserDomain.sales,    80),
    _ConvSeed("alex",          "고객사 B 후속 메일 초안", UserDomain.sales,  1560),
    _ConvSeed("develop_admin", "v1.42 릴리스 노트",       UserDomain.develop,  15),
    _ConvSeed("develop_admin", "이슈 #1284 원인 분석",    UserDomain.develop, 240),
    _ConvSeed("design_admin",  "ECO-221 영향 도면",       UserDomain.design,  540),
    # 일반(normal) — 도메인 무관 공통 업무. 5명 사용자 각자에게 1건씩 분배해
    # 누구로 로그인해도 자기 RECENT 에 일반 대화 1건은 보이도록 한다.
    # DB 측에선 user_domain.all 로 저장되고, UI 매핑층에서 'normal' 키로 변환된다.
    _ConvSeed("superadmin",    "오늘 일정 요약",          UserDomain.all,      10),
    _ConvSeed("finance_admin", "월간 결산 회의록 정리",   UserDomain.all,      95),
    _ConvSeed("alex",          "고객 회신 지연 사과 메시지", UserDomain.all,  180),
    _ConvSeed("design_admin",  "주간 업무 보고 초안",     UserDomain.all,     360),
    _ConvSeed("develop_admin", "Slack 답장 톤 정돈",      UserDomain.all,     420),
)


# --------------------------------------------------------------------------
# 2) 가상 메시지 시드 — 제목으로 conversation 매칭, 한 번에 일괄 INSERT
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _MsgSeed:
    role: str                       # 'user' | 'assistant'
    content: str
    domain: UserDomain | None
    model: str | None
    offset_min: int                 # created_at = conversation.started_at + offset


_MODEL = "Qwen2.5-7B-Instruct"


_MESSAGES: dict[str, tuple[_MsgSeed, ...]] = {
    "3월 부서 예산 집행률": (
        _MsgSeed("user",      "3월 우리 부서 예산 집행률 어떻게 돼? 영업 부서 기준으로.", None, None, 0),
        _MsgSeed(
            "assistant",
            "영업본부 3월 예산 집행률은 **78.4%** 입니다.\n\n"
            "- 인건비: 92.1%\n"
            "- 출장/접대: 64.0%\n"
            "- 마케팅: 71.5%\n"
            "- 기타: 58.2%\n\n"
            "전월 대비 +6.3%p 증가했고, 분기 계획 기준 정상 범위입니다.",
            UserDomain.finance, _MODEL, 1,
        ),
        _MsgSeed("user",      "출장비가 64%면 적은 거 아냐? 이대로 가도 괜찮아?", None, None, 6),
        _MsgSeed(
            "assistant",
            "출장/접대비는 분기 누적으로 보면 41.8% 사용이라 **잔여가 충분**합니다. "
            "다만 4월 영업 점검 일정이 몰려있어 다음 달 사용액이 평균치를 상회할 가능성이 큽니다.\n\n"
            "권장:\n"
            "1. 4월 1주차 부서장 회의 전 잔여 한도 공지\n"
            "2. 분기 말 일괄 정산 대신 주별 사용액 모니터링 도입",
            UserDomain.finance, _MODEL, 7,
        ),
    ),
    "거래처 A 미수금 정리": (
        _MsgSeed("user", "거래처 A 미수금 현황과 가장 오래된 항목 정리해줘.", None, None, 0),
        _MsgSeed(
            "assistant",
            "거래처 A의 미수금 합계는 **₩42,580,000** 입니다.\n\n"
            "| 청구일 | 금액 | 경과일 | 비고 |\n"
            "|---|---|---|---|\n"
            "| 2025-12-18 | ₩12,800,000 | 140일 | 2차 독촉 발송 |\n"
            "| 2026-01-22 | ₩9,300,000  | 105일 | 분할 협의 중 |\n"
            "| 2026-02-09 | ₩8,650,000  | 87일  | 정상 |\n"
            "| 2026-03-04 | ₩11,830,000 | 64일  | 정상 |\n\n"
            "**가장 오래된 항목**: 2025-12-18 청구분, 140일 경과. 회수 우선순위 1로 분류 권장.",
            UserDomain.finance, _MODEL, 1,
        ),
    ),
    "Q2 파이프라인 진단": (
        _MsgSeed("user", "Q2 파이프라인 상태 한 번 진단해줘. 위험 딜이 어디 있는지.", None, None, 0),
        _MsgSeed(
            "assistant",
            "Q2 파이프라인 총액 **₩1,820M**, Stage별 분포:\n\n"
            "- Discover: ₩640M (35%)\n"
            "- Qualify: ₩520M (29%)\n"
            "- Proposal: ₩410M (23%)\n"
            "- Negotiation: ₩250M (13%)\n\n"
            "**위험 딜 3건**:\n"
            "1. **고객사 K** ₩180M — Proposal 단계 32일 정체. 의사결정자 변경 의심.\n"
            "2. **고객사 M** ₩95M — 견적 후 2주간 무응답.\n"
            "3. **고객사 R** ₩140M — 경쟁사 PoC 동시 진행 중.\n\n"
            "3건 합 **₩415M(23%)** 으로, 클로징 확률 보정 시 분기 목표 미달 위험 약 14%p.",
            UserDomain.sales, _MODEL, 1,
        ),
        _MsgSeed("user", "K 고객사 후속 액션 1가지만 정해줘.", None, None, 4),
        _MsgSeed(
            "assistant",
            "**액션**: 신규 의사결정자(추정: 구매팀장) 대상 30분 화상 디스커버리 미팅을 이번 주 내 확보.\n\n"
            "- 목적: 기존 제안 범위 재확인 + 결정 프로세스 매핑\n"
            "- 메일 초안 필요하시면 작성해드립니다.",
            UserDomain.sales, _MODEL, 5,
        ),
    ),
    "고객사 B 후속 메일 초안": (
        _MsgSeed("user", "고객사 B 미팅 후속 메일 초안 정중한 톤으로 써줘. PoC 일정 제안 포함.", None, None, 0),
        _MsgSeed(
            "assistant",
            "제목: [UND] 지난 미팅에 감사드리며 — PoC 일정 제안의 건\n\n"
            "안녕하세요 ○○○ 차장님,\n\n"
            "지난주 미팅에서 자세한 의견 나눠 주셔서 감사합니다. 논의된 자동화 범위와 우선순위를 정리하여 PoC 일정을 아래와 같이 제안드립니다.\n\n"
            "• 1주차: 환경 셋업 + 샘플 데이터 검토\n"
            "• 2~3주차: 핵심 기능 2종 시범 적용\n"
            "• 4주차: 결과 리뷰 및 본 도입 의사결정 미팅\n\n"
            "첨부 자료는 별도로 송부드리겠습니다. 일정 가능 여부 회신 부탁드리며, 추가로 필요한 자료 있으시면 알려 주세요.\n\n"
            "감사합니다.\n— UND 영업팀 드림",
            UserDomain.sales, _MODEL, 1,
        ),
    ),
    "v1.42 릴리스 노트": (
        _MsgSeed("user", "최근 일주일 main 브랜치 변경 사항으로 v1.42 릴리스 노트 작성해줘.", None, None, 0),
        _MsgSeed(
            "assistant",
            "## v1.42 (2026-05-07)\n\n"
            "### 추가\n"
            "- 사용자 인증·세션 관리 도입 (JWT, httpOnly 쿠키)\n"
            "- 로그인 페이지 + 라우트 가드 미들웨어\n"
            "- 비밀번호 재설정 모달 (사용자 메뉴)\n\n"
            "### 개선\n"
            "- 사이드바 사용자 영역 — 표시명/역할/도메인 노출\n"
            "- 한글 라벨 가독성 정리(mono → sans 전환)\n"
            "- 도메인 명칭 통일: '설계 → 기구설계', '개발 → 선행개발'\n\n"
            "### 버그픽스\n"
            "- 응답 정지 시 좀비 스트림 차단\n"
            "- 다크 모드에서 워드마크 그라디언트 톤 반전 누락",
            UserDomain.develop, _MODEL, 1,
        ),
    ),
    "이슈 #1284 원인 분석": (
        _MsgSeed("user", "이슈 #1284 (간헐적 500 에러) 재현 시나리오와 의심 모듈 정리해줘.", None, None, 0),
        _MsgSeed(
            "assistant",
            "**재현 시나리오**\n"
            "1. 동시 사용자 100+ / RPS 35 이상\n"
            "2. 동일 conversation 에 짧은 간격(<200ms) 연속 send\n"
            "3. 첫 fetch 가 abort 되기 전 두 번째 fetch 가 시작\n\n"
            "**의심 모듈**\n"
            "- `backend/app/vllm_client.py:stream_chat` — 동일 세션에 두 개의 reader 가 붙는 경합\n"
            "- `frontend/components/.../chat-window.tsx:send` — abortRef 교체 직후 race\n\n"
            "**제안**\n"
            "- 클라이언트 측: send 중복 호출 가드(이전 stream 종료 await)\n"
            "- 서버 측: per-session lock 도입 또는 in-flight 시 429 반환",
            UserDomain.develop, _MODEL, 1,
        ),
    ),
    "ECO-221 영향 도면": (
        _MsgSeed("user", "변경관리 ECO-221 의 영향 도면과 BOM 변경 내역 정리해줘.", None, None, 0),
        _MsgSeed(
            "assistant",
            "**ECO-221 — 후면 브래킷 두께 2.5 → 3.0mm**\n\n"
            "영향 도면:\n"
            "- `DWG-A-2104` (Rev.7 → Rev.8) — 후면 브래킷 단품\n"
            "- `DWG-A-2110` (Rev.4 → Rev.5) — 어셈블리\n"
            "- `DWG-A-2152` (Rev.2 → Rev.3) — 패키징 도면(고정 슬롯 0.5mm 확장 필요)\n\n"
            "**BOM 변경**\n"
            "- 부품 `BR-2104-A` 단가 ₩1,820 → ₩2,140 (+17.6%)\n"
            "- 어셈블리 `ASM-2110` 총 BOM 단가 +₩320/대\n"
            "- 연간 영향(생산 12,000대 기준): **+₩3,840,000**\n\n"
            "안전·인증 항목 영향: **없음**(부품 KC 인증 범위 내).\n\n"
            "승인 후 진행 권장.",
            UserDomain.design, _MODEL, 1,
        ),
    ),
    # ---- 일반(normal) 도메인 — 도메인 무관 공통 업무 ----
    "오늘 일정 요약": (
        _MsgSeed("user", "오늘 일정과 미팅 정리해줘. 우선순위까지 같이.", None, None, 0),
        _MsgSeed(
            "assistant",
            "**오늘 일정 (2026-05-07, 수)**\n\n"
            "| 시간 | 일정 | 우선순위 |\n"
            "|---|---|---|\n"
            "| 09:30 | 주간 임원 보고 (대회의실) | **상** |\n"
            "| 11:00 | 신규 협력사 미팅 (3F) | 중 |\n"
            "| 13:30 | 1:1 — 김OO 매니저 | 중 |\n"
            "| 15:00 | 분기 인사 검토 자료 회신 마감 | **상** |\n"
            "| 17:00 | 부서장 협의(원격) | 하 |\n\n"
            "**오늘 꼭 처리**: 주간 보고 자료 최종 검토 → 09:00 이전.",
            UserDomain.all, _MODEL, 1,
        ),
        _MsgSeed("user", "협력사 미팅 전에 챙길 것만 한 줄로.", None, None, 4),
        _MsgSeed(
            "assistant",
            "전번 미팅 회의록(2건) + NDA 사본 + 우리 측 단가표 v3 — 3개 첨부 준비.",
            UserDomain.all, _MODEL, 5,
        ),
    ),
    "월간 결산 회의록 정리": (
        _MsgSeed(
            "user",
            "오전 회의 결산 일정 논의 내용을 회의록 형식으로 정리해줘. 참석자 4명, "
            "결정 사항은 5/15 가마감, 액션 아이템 3개 정도였어.",
            None, None, 0,
        ),
        _MsgSeed(
            "assistant",
            "## 월간 결산 점검 회의록\n\n"
            "- **일시**: 2026-05-07 (수) 10:00–10:35\n"
            "- **장소**: 5F 회의실 B\n"
            "- **참석**: 김부장, 이과장, 박대리, 정사원\n\n"
            "### 안건\n"
            "1. 4월 결산 마감 일정 확정\n"
            "2. 미수금 회수 진행 점검\n"
            "3. 5월 예산 이월/조정\n\n"
            "### 결정 사항\n"
            "- 4월 결산 **5/15(금) 18:00 마감**, 5/16 임원 보고\n"
            "- 미수금 거래처 A 분할 협의 결과는 5/12까지 회신\n\n"
            "### Action Items\n"
            "| # | 담당 | 내용 | 기한 |\n"
            "|---|---|---|---|\n"
            "| 1 | 이과장 | 4월 마감 가검산 1차 공유 | 5/12 |\n"
            "| 2 | 박대리 | 거래처 A 분할 협의 회신 정리 | 5/12 |\n"
            "| 3 | 정사원 | 5월 예산 이월 신청 양식 회람 | 5/09 |",
            UserDomain.all, _MODEL, 1,
        ),
    ),
    "고객 회신 지연 사과 메시지": (
        _MsgSeed(
            "user",
            "고객한테 회신 지연 사과 메시지 정중하게 써줘. 답변은 다음주 월요일까지 드린다고.",
            None, None, 0,
        ),
        _MsgSeed(
            "assistant",
            "안녕하세요 ○○○ 님,\n\n"
            "먼저 회신이 늦어진 점 진심으로 사과드립니다. 내부 검토에 시간이 다소 소요되어 약속드린 일정보다 지연되었습니다.\n\n"
            "관련 자료는 **다음 주 월요일(5/12) 오전 중**으로 정리하여 회신드리겠습니다. "
            "그 사이 추가로 확인이 필요하신 내용이 있으시면 말씀해 주시면 우선적으로 답변드리겠습니다.\n\n"
            "다시 한 번 양해해 주셔서 감사드립니다.\n\n— UND 드림",
            UserDomain.all, _MODEL, 1,
        ),
        _MsgSeed("user", "조금 더 짧게 — 메일이 아니라 메신저용으로.", None, None, 3),
        _MsgSeed(
            "assistant",
            "안녕하세요 ○○○ 님, 회신이 늦어 죄송합니다. 정리해서 **다음 주 월요일(5/12) 오전** 중 다시 드리겠습니다. 급한 사안 있으시면 먼저 말씀해 주세요. 감사합니다.",
            UserDomain.all, _MODEL, 4,
        ),
    ),
    "주간 업무 보고 초안": (
        _MsgSeed(
            "user",
            "이번주 한 일 + 다음주 할 일로 주간 보고 짧게 작성해줘. 이슈도 한 줄.",
            None, None, 0,
        ),
        _MsgSeed(
            "assistant",
            "## 주간 업무 보고 (2026-05-07)\n\n"
            "**이번 주 한 일**\n"
            "- 신규 모델 A2 외관 컨셉 3안 1차 정리\n"
            "- 협력사 K 단가 협상 (1차) 완료, 2차 일정 확정\n"
            "- 사내 디자인 가이드 문서 v0.4 작성 (50%)\n\n"
            "**다음 주 할 일**\n"
            "- A2 컨셉안 임원 보고 자료 최종화\n"
            "- 협력사 K 2차 협상 + 후속 메일 정리\n"
            "- 디자인 가이드 v0.5 (남은 50%) — 5/16까지 1차 검토 회람\n\n"
            "**이슈**: 협력사 K 일부 단가가 가이드 상한 초과 — 별도 결재 필요.",
            UserDomain.all, _MODEL, 1,
        ),
    ),
    "Slack 답장 톤 정돈": (
        _MsgSeed(
            "user",
            "다음 메시지를 좀 더 정중한 톤으로 다듬어줘. \"그건 내일 처리할게요\"",
            None, None, 0,
        ),
        _MsgSeed(
            "assistant",
            "정중한 톤으로 다듬은 후보 3가지입니다.\n\n"
            "1. \"그 건은 내일 중 처리해서 다시 공유드리겠습니다.\"\n"
            "2. \"확인했습니다 — 내일 일정 안에서 처리하고 결과 회신드릴게요.\"\n"
            "3. \"오늘은 일정이 빡빡해서, 내일 아침에 우선 처리하고 알려드리겠습니다.\"\n\n"
            "상황(사내/외부, 상사/동료)에 따라 1번이 가장 무난, 3번이 사정 설명이 필요할 때 적합합니다.",
            UserDomain.all, _MODEL, 1,
        ),
    ),
}


# --------------------------------------------------------------------------
# 3) 멱등 시더 — backend startup 에서 호출
# --------------------------------------------------------------------------
async def _load_owner_map(db: AsyncSession, usernames: Iterable[str]) -> dict[str, User]:
    """username → User 매핑. 시드 사용자가 아직 없으면 그 항목은 건너뛴다."""
    res = await db.execute(select(User).where(User.username.in_(list(usernames))))
    return {u.username: u for u in res.scalars().all()}


async def ensure_seed_conversations(db: AsyncSession) -> dict[str, int]:
    """사이드바 RECENT 시연용 가상 대화를 idempotent 하게 보장.

    Returns:
        통계 딕셔너리 — 진단/로그용. {"conv_inserted": N, "msg_inserted": M, "skipped": K}
    """
    stats = {"conv_inserted": 0, "msg_inserted": 0, "skipped": 0}

    owners = await _load_owner_map(db, {c.username for c in _CONVS})
    if not owners:
        # 시드 사용자가 아직 없는 경우(02_users.sql 미적용). 그냥 종료.
        return stats

    now = datetime.now(timezone.utc)

    for cs in _CONVS:
        owner = owners.get(cs.username)
        if owner is None:
            stats["skipped"] += 1
            continue

        # 이미 같은 사용자에 같은 제목의 대화가 있으면 conversation 자체는 건드리지 않음.
        existing_q = await db.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.user_id == owner.id, Conversation.title == cs.title)
        )
        existing = existing_q.scalar_one_or_none()

        if existing is None:
            updated = now - timedelta(minutes=cs.minutes_ago)
            started = updated - timedelta(minutes=5)
            conv = Conversation(
                user_id=owner.id,
                title=cs.title,
                domain=cs.domain,
                started_at=started,
                updated_at=updated,
            )
            db.add(conv)
            await db.flush()  # conv.id 확보
            stats["conv_inserted"] += 1
            # 신규 conv 의 messages relationship 은 아직 lazy 상태라 직접 접근하면
            # async 컨텍스트에서 greenlet 에러가 난다(selectinload 가 select 시점만 적용).
            # 새로 만든 대화는 무조건 메시지 시드 대상으로 본다.
            needs_messages = True
        else:
            conv = existing
            # selectinload 로 미리 로드돼 있으므로 안전.
            needs_messages = not conv.messages

        # 메시지가 0건일 때만 일괄 시드. 사용자가 직접 쌓은 메시지가 있으면 보존.
        seed_msgs = _MESSAGES.get(cs.title)
        if seed_msgs and needs_messages:
            base = conv.started_at
            for ms in seed_msgs:
                db.add(
                    Message(
                        conversation_id=conv.id,
                        role=ms.role,
                        content=ms.content,
                        domain=ms.domain,
                        model=ms.model,
                        created_at=base + timedelta(minutes=ms.offset_min),
                    )
                )
                stats["msg_inserted"] += 1

    await db.commit()
    return stats


async def run_seed_on_startup() -> dict[str, int]:
    """lifespan startup 훅에서 호출하는 진입점. 자체 세션을 만들어 사용한다."""
    from .database import SessionLocal  # 순환 임포트 회피

    async with SessionLocal() as db:
        try:
            return await ensure_seed_conversations(db)
        except Exception:
            # 시드 실패는 앱 기동을 막지 않는다 — DB 미가용/스키마 누락 등 환경 이슈면 RECENT 만 비어있게.
            await db.rollback()
            raise
