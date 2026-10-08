-- ============================================================
-- UND Cortex — 프로젝트(대화 그룹화) 스키마
--   feature_checklist.md §B "프로젝트 (대화 그룹화)" 항목.
--   Claude.ai 의 "Projects" 와 동일한 컨셉:
--     - 사용자가 의미 단위로 대화를 묶을 수 있는 그룹.
--     - 그룹 단위 system_prompt(=Instructions)로 컨텍스트를 주입.
--     - description 은 카드 미리보기용 짧은 한 줄(또는 비어둠).
-- ============================================================

-- ------------------------------------------------------------
-- 1) projects
--    소유자(user_id) 가 삭제되면 프로젝트도 함께 정리(CASCADE).
--    domain 은 사이드바/카드에서 색 스트립용. 프로젝트 자체는 도메인 잠금이 아니라
--    "대화의 권장 도메인" 정도로 운영. NULL = 미지정(자동).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            VARCHAR(120) NOT NULL,
    description     VARCHAR(500),
    system_prompt   TEXT,                      -- Instructions (선택)
    domain          user_domain,               -- 권장 도메인(선택)
    starred         BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_projects_user_updated
    ON projects(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_projects_user_starred
    ON projects(user_id, starred);

DROP TRIGGER IF EXISTS projects_set_updated_at ON projects;
CREATE TRIGGER projects_set_updated_at
    BEFORE UPDATE ON projects
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- ------------------------------------------------------------
-- 2) project_conversations (N:M 매핑)
--    한 대화는 0~1개 프로젝트에 속할 수 있다(현재 정책: 1개로 운용).
--    UNIQUE(conversation_id) 로 강제하면 향후 정책 변경 시 작은 마이그레이션이
--    필요해지므로, 우선은 PK(project_id, conversation_id) 만 두고 1대1 운용은
--    애플리케이션 측에서 보장한다.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_conversations (
    project_id       BIGINT      NOT NULL REFERENCES projects(id)      ON DELETE CASCADE,
    conversation_id  BIGINT      NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    added_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS ix_project_conversations_project
    ON project_conversations(project_id, added_at DESC);
CREATE INDEX IF NOT EXISTS ix_project_conversations_conversation
    ON project_conversations(conversation_id);

-- ------------------------------------------------------------
-- 3) 시연 시드 — 도메인 관리자별 1건씩.
--    캡처(_capture/projects_ui_v1.jpg) 의 "matthew/realman/Leo/terry/Jinny/Noah"
--    톤을 차용해 사내 도메인 맥락에 맞춘 이름으로 변환.
-- ------------------------------------------------------------
INSERT INTO projects (user_id, name, description, system_prompt, domain)
SELECT u.id, v.name, v.description, v.system_prompt, v.domain::user_domain
FROM (VALUES
    ('finance_admin', '월간 결산 자동화',
     '매월 결산 보고서 작성을 보조하는 컨텍스트.',
     E'당신은 재무 결산 어시스턴트다.\n- 결산 항목은 한국 회계 기준에 맞춰 분류한다.\n- 숫자는 천단위 콤마, 통화는 ₩ 표기.\n- 출처가 불명확한 수치는 "추정" 으로 명시한다.',
     'finance'),
    ('alex',   'Q2 영업 캠페인',
     '2분기 캠페인 메일/스크립트/제안서 작성을 모은 작업방.',
     E'당신은 B2B 영업 카피라이터다.\n- 정중한 비즈니스 톤(존칭).\n- 핵심은 첫 두 문장에 배치.\n- 거래처별 직함이 주어지면 그대로 사용한다.',
     'sales'),
    ('develop_admin', '플랫폼 v1.5 백로그',
     '다음 마이너 릴리스 이슈/RFC/릴리스 노트 초안.',
     E'당신은 시니어 백엔드 엔지니어다.\n- 답변은 가능한 한 코드 또는 의사코드로 제시.\n- 트레이드오프를 1~2줄로 같이 설명.',
     'develop'),
    ('design_admin',  'ECO 변경 검토',
     '진행 중인 ECO(설계 변경)의 영향도/원가/도면 정리방.',
     E'당신은 기구설계 변경관리 보조자다.\n- 부품번호와 도면번호는 표기 그대로 인용.\n- 수치는 SI 단위 + 허용오차 같이 표기.',
     'design')
) AS v(username, name, description, system_prompt, domain)
JOIN users u ON u.username = v.username
WHERE NOT EXISTS (
    SELECT 1 FROM projects p
    WHERE p.user_id = u.id AND p.name = v.name
);
