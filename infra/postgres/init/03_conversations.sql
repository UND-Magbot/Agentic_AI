-- ============================================================
-- UND Cortex — 대화 영속화 스키마 (conversations / messages)
-- 컨테이너 최초 기동 시 자동 적용. 기존 DB 에는 수동 실행.
-- ============================================================

-- ------------------------------------------------------------
-- 1) conversations
--    user_id 0(=NULL 대체)일 때는 게스트/시연용. 인증 도입 후엔 NOT NULL 권장.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(200) NOT NULL,
    domain      user_domain  NOT NULL DEFAULT 'all',
    starred     BOOLEAN      NOT NULL DEFAULT FALSE,
    started_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_conversations_user_updated
    ON conversations(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_conversations_domain
    ON conversations(domain);
CREATE INDEX IF NOT EXISTS ix_conversations_user_starred
    ON conversations(user_id, starred);

-- updated_at 자동 갱신 — users 와 동일한 trg_set_updated_at 함수 재사용 (02_users.sql 에서 정의됨).
DROP TRIGGER IF EXISTS conversations_set_updated_at ON conversations;
CREATE TRIGGER conversations_set_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- ------------------------------------------------------------
-- 2) messages
--    role: user / assistant / system. 본문은 TOAST 자동 압축(LZ4) 대상.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT      NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            VARCHAR(16) NOT NULL,        -- 'user' | 'assistant' | 'system'
    content         TEXT        NOT NULL,
    domain          user_domain,                 -- assistant 응답이 라우팅된 도메인 (NULL 허용)
    model           VARCHAR(64),                 -- 어떤 모델이 응답했나 (옵션)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_messages_conv_created
    ON messages(conversation_id, created_at);

-- ------------------------------------------------------------
-- 3) 시드 — 기존 mock-recent.ts 의 5건을 그대로 옮김.
--    소속: 각 도메인 관리자(또는 superadmin)에게 매핑.
--    updated_at 은 BASELINE 으로부터 minAgo(N) 패턴을 NOW() - INTERVAL 로 매핑.
--      f1: 35분 전, f2: 220분 전, s1: 80분 전, s2: 26시간 전,
--      d1: 15분 전, d2: 4시간 전, g1: 9시간 전
-- ------------------------------------------------------------

-- 멱등 시드: 제목 + user_id 조합이 이미 있으면 스킵.
WITH owner AS (
    SELECT id, username FROM users
)
INSERT INTO conversations (user_id, title, domain, started_at, updated_at)
SELECT u.id, v.title, v.domain::user_domain,
       NOW() - (v.minutes || ' minutes')::interval - INTERVAL '5 minutes',
       NOW() - (v.minutes || ' minutes')::interval
FROM (VALUES
    ('finance_admin', '3월 부서 예산 집행률',     'finance',     35),
    ('finance_admin', '거래처 A 미수금 정리',     'finance',    220),
    ('alex',   'Q2 파이프라인 진단',       'sales',       80),
    ('alex',   '고객사 B 후속 메일 초안',  'sales',     1560),  -- 26시간
    ('develop_admin', 'v1.42 릴리스 노트',        'develop',     15),
    ('develop_admin', '이슈 #1284 원인 분석',     'develop',    240),
    ('design_admin',  'ECO-221 영향 도면',        'design',     540)
) AS v(username, title, domain, minutes)
JOIN owner u ON u.username = v.username
WHERE NOT EXISTS (
    SELECT 1 FROM conversations c
    WHERE c.user_id = u.id AND c.title = v.title
);

-- ------------------------------------------------------------
-- 4) 가상 메시지 시드 — 각 대화별로 사실적인 user/assistant 페어.
--    제목으로 conversation 을 식별해 idempotent 하게 삽입.
-- ------------------------------------------------------------

-- 4-1) 3월 부서 예산 집행률
INSERT INTO messages (conversation_id, role, content, domain, model, created_at)
SELECT c.id, m.role, m.content, m.domain::user_domain, m.model,
       c.started_at + (m.offset_min || ' minutes')::interval
FROM conversations c
CROSS JOIN LATERAL (VALUES
    ('user',      '3월 우리 부서 예산 집행률 어떻게 돼? 영업 부서 기준으로.',
     NULL, NULL, 0),
    ('assistant', '영업본부 3월 예산 집행률은 **78.4%** 입니다.\n\n- 인건비: 92.1%\n- 출장/접대: 64.0%\n- 마케팅: 71.5%\n- 기타: 58.2%\n\n전월 대비 +6.3%p 증가했고, 분기 계획 기준 정상 범위입니다.',
     'finance', 'Qwen2.5-7B-Instruct', 1),
    ('user',      '출장비가 64%면 적은 거 아냐? 이대로 가도 괜찮아?',
     NULL, NULL, 6),
    ('assistant', '출장/접대비는 분기 누적으로 보면 41.8% 사용이라 **잔여가 충분**합니다. 다만 4월 영업 점검 일정이 몰려있어 다음 달 사용액이 평균치를 상회할 가능성이 큽니다.\n\n권장:\n1. 4월 1주차 부서장 회의 전 잔여 한도 공지\n2. 분기 말 일괄 정산 대신 주별 사용액 모니터링 도입',
     'finance', 'Qwen2.5-7B-Instruct', 7)
) AS m(role, content, domain, model, offset_min)
WHERE c.title = '3월 부서 예산 집행률'
  AND NOT EXISTS (SELECT 1 FROM messages mm WHERE mm.conversation_id = c.id);

-- 4-2) 거래처 A 미수금 정리
INSERT INTO messages (conversation_id, role, content, domain, model, created_at)
SELECT c.id, m.role, m.content, m.domain::user_domain, m.model,
       c.started_at + (m.offset_min || ' minutes')::interval
FROM conversations c
CROSS JOIN LATERAL (VALUES
    ('user',      '거래처 A 미수금 현황과 가장 오래된 항목 정리해줘.',
     NULL, NULL, 0),
    ('assistant', '거래처 A의 미수금 합계는 **₩42,580,000** 입니다.\n\n| 청구일 | 금액 | 경과일 | 비고 |\n|---|---|---|---|\n| 2025-12-18 | ₩12,800,000 | 140일 | 2차 독촉 발송 |\n| 2026-01-22 | ₩9,300,000  | 105일 | 분할 협의 중 |\n| 2026-02-09 | ₩8,650,000  | 87일  | 정상 |\n| 2026-03-04 | ₩11,830,000 | 64일  | 정상 |\n\n**가장 오래된 항목**: 2025-12-18 청구분, 140일 경과. 회수 우선순위 1로 분류 권장.',
     'finance', 'Qwen2.5-7B-Instruct', 1)
) AS m(role, content, domain, model, offset_min)
WHERE c.title = '거래처 A 미수금 정리'
  AND NOT EXISTS (SELECT 1 FROM messages mm WHERE mm.conversation_id = c.id);

-- 4-3) Q2 파이프라인 진단
INSERT INTO messages (conversation_id, role, content, domain, model, created_at)
SELECT c.id, m.role, m.content, m.domain::user_domain, m.model,
       c.started_at + (m.offset_min || ' minutes')::interval
FROM conversations c
CROSS JOIN LATERAL (VALUES
    ('user',      'Q2 파이프라인 상태 한 번 진단해줘. 위험 딜이 어디 있는지.',
     NULL, NULL, 0),
    ('assistant', 'Q2 파이프라인 총액 **₩1,820M**, Stage별 분포:\n\n- Discover: ₩640M (35%)\n- Qualify: ₩520M (29%)\n- Proposal: ₩410M (23%)\n- Negotiation: ₩250M (13%)\n\n**위험 딜 3건**:\n1. **고객사 K** ₩180M — Proposal 단계 32일 정체. 의사결정자 변경 의심.\n2. **고객사 M** ₩95M — 견적 후 2주간 무응답.\n3. **고객사 R** ₩140M — 경쟁사 PoC 동시 진행 중.\n\n3건 합 **₩415M(23%)** 으로, 클로징 확률 보정 시 분기 목표 미달 위험 약 14%p.',
     'sales', 'Qwen2.5-7B-Instruct', 1),
    ('user',      'K 고객사 후속 액션 1가지만 정해줘.',
     NULL, NULL, 4),
    ('assistant', '**액션**: 신규 의사결정자(추정: 구매팀장) 대상 30분 화상 디스커버리 미팅을 이번 주 내 확보.\n\n- 목적: 기존 제안 범위 재확인 + 결정 프로세스 매핑\n- 메일 초안 필요하시면 작성해드립니다.',
     'sales', 'Qwen2.5-7B-Instruct', 5)
) AS m(role, content, domain, model, offset_min)
WHERE c.title = 'Q2 파이프라인 진단'
  AND NOT EXISTS (SELECT 1 FROM messages mm WHERE mm.conversation_id = c.id);

-- 4-4) 고객사 B 후속 메일 초안
INSERT INTO messages (conversation_id, role, content, domain, model, created_at)
SELECT c.id, m.role, m.content, m.domain::user_domain, m.model,
       c.started_at + (m.offset_min || ' minutes')::interval
FROM conversations c
CROSS JOIN LATERAL (VALUES
    ('user',      '고객사 B 미팅 후속 메일 초안 정중한 톤으로 써줘. PoC 일정 제안 포함.',
     NULL, NULL, 0),
    ('assistant', E'제목: [UND] 지난 미팅에 감사드리며 — PoC 일정 제안의 건\n\n안녕하세요 ○○○ 차장님,\n\n지난주 미팅에서 자세한 의견 나눠 주셔서 감사합니다. 논의된 자동화 범위와 우선순위를 정리하여 PoC 일정을 아래와 같이 제안드립니다.\n\n• 1주차: 환경 셋업 + 샘플 데이터 검토\n• 2~3주차: 핵심 기능 2종 시범 적용\n• 4주차: 결과 리뷰 및 본 도입 의사결정 미팅\n\n첨부 자료는 별도로 송부드리겠습니다. 일정 가능 여부 회신 부탁드리며, 추가로 필요한 자료 있으시면 알려 주세요.\n\n감사합니다.\n— UND 영업팀 드림',
     'sales', 'Qwen2.5-7B-Instruct', 1)
) AS m(role, content, domain, model, offset_min)
WHERE c.title = '고객사 B 후속 메일 초안'
  AND NOT EXISTS (SELECT 1 FROM messages mm WHERE mm.conversation_id = c.id);

-- 4-5) v1.42 릴리스 노트
INSERT INTO messages (conversation_id, role, content, domain, model, created_at)
SELECT c.id, m.role, m.content, m.domain::user_domain, m.model,
       c.started_at + (m.offset_min || ' minutes')::interval
FROM conversations c
CROSS JOIN LATERAL (VALUES
    ('user',      '최근 일주일 main 브랜치 변경 사항으로 v1.42 릴리스 노트 작성해줘.',
     NULL, NULL, 0),
    ('assistant', E'## v1.42 (2026-05-07)\n\n### 추가\n- 사용자 인증·세션 관리 도입 (JWT, httpOnly 쿠키)\n- 로그인 페이지 + 라우트 가드 미들웨어\n- 비밀번호 재설정 모달 (사용자 메뉴)\n\n### 개선\n- 사이드바 사용자 영역 — 표시명/역할/도메인 노출\n- 한글 라벨 가독성 정리(mono → sans 전환)\n- 도메인 명칭 통일: ‘설계 → 기구설계’, ‘개발 → 선행개발’\n\n### 버그픽스\n- 응답 정지 시 좀비 스트림 차단\n- 다크 모드에서 워드마크 그라디언트 톤 반전 누락',
     'develop', 'Qwen2.5-7B-Instruct', 1)
) AS m(role, content, domain, model, offset_min)
WHERE c.title = 'v1.42 릴리스 노트'
  AND NOT EXISTS (SELECT 1 FROM messages mm WHERE mm.conversation_id = c.id);

-- 4-6) 이슈 #1284 원인 분석
INSERT INTO messages (conversation_id, role, content, domain, model, created_at)
SELECT c.id, m.role, m.content, m.domain::user_domain, m.model,
       c.started_at + (m.offset_min || ' minutes')::interval
FROM conversations c
CROSS JOIN LATERAL (VALUES
    ('user',      '이슈 #1284 (간헐적 500 에러) 재현 시나리오와 의심 모듈 정리해줘.',
     NULL, NULL, 0),
    ('assistant', '**재현 시나리오**\n1. 동시 사용자 100+ / RPS 35 이상\n2. 동일 conversation 에 짧은 간격(<200ms) 연속 send\n3. 첫 fetch 가 abort 되기 전 두 번째 fetch 가 시작\n\n**의심 모듈**\n- `backend/app/vllm_client.py:stream_chat` — 동일 세션에 두 개의 reader 가 붙는 경합\n- `frontend/components/.../chat-window.tsx:send` — abortRef 교체 직후 race\n\n**제안**\n- 클라이언트 측: send 중복 호출 가드(이전 stream 종료 await)\n- 서버 측: per-session lock 도입 또는 in-flight 시 429 반환',
     'develop', 'Qwen2.5-7B-Instruct', 1)
) AS m(role, content, domain, model, offset_min)
WHERE c.title = '이슈 #1284 원인 분석'
  AND NOT EXISTS (SELECT 1 FROM messages mm WHERE mm.conversation_id = c.id);

-- 4-7) ECO-221 영향 도면
INSERT INTO messages (conversation_id, role, content, domain, model, created_at)
SELECT c.id, m.role, m.content, m.domain::user_domain, m.model,
       c.started_at + (m.offset_min || ' minutes')::interval
FROM conversations c
CROSS JOIN LATERAL (VALUES
    ('user',      '변경관리 ECO-221 의 영향 도면과 BOM 변경 내역 정리해줘.',
     NULL, NULL, 0),
    ('assistant', '**ECO-221 — 후면 브래킷 두께 2.5 → 3.0mm**\n\n영향 도면:\n- `DWG-A-2104` (Rev.7 → Rev.8) — 후면 브래킷 단품\n- `DWG-A-2110` (Rev.4 → Rev.5) — 어셈블리\n- `DWG-A-2152` (Rev.2 → Rev.3) — 패키징 도면(고정 슬롯 0.5mm 확장 필요)\n\n**BOM 변경**\n- 부품 `BR-2104-A` 단가 ₩1,820 → ₩2,140 (+17.6%)\n- 어셈블리 `ASM-2110` 총 BOM 단가 +₩320/대\n- 연간 영향(생산 12,000대 기준): **+₩3,840,000**\n\n안전·인증 항목 영향: **없음**(부품 KC 인증 범위 내).\n\n승인 후 진행 권장.',
     'design', 'Qwen2.5-7B-Instruct', 1)
) AS m(role, content, domain, model, offset_min)
WHERE c.title = 'ECO-221 영향 도면'
  AND NOT EXISTS (SELECT 1 FROM messages mm WHERE mm.conversation_id = c.id);
