"""런타임 멱등 스키마 마이그레이션.

Alembic 같은 마이그레이션 도구를 정식 도입하기 전까지의 임시 자리.
backend 부팅 시 한 번씩 실행되며, 이미 적용된 변경은 자동으로 no-op.

추가 컬럼 변경/리네임이 필요할 때 여기에 멱등 SQL 블록을 누적한다.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("und_cortex.migrations")


# ---------------------------------------------------------------------------
# 마이그레이션 #1 — users.full_name → users.alias
#  - 처음부터 alias 인 새 볼륨에선 IF EXISTS 가 false 라 실행되지 않음.
#  - 기존 볼륨에서만 한 번 실행되고 그 이후 호출에서도 idempotent.
# ---------------------------------------------------------------------------
_RENAME_USERS_FULL_NAME_TO_ALIAS = text(
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'full_name'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'alias'
        ) THEN
            ALTER TABLE users RENAME COLUMN full_name TO alias;
            RAISE NOTICE 'migrated users.full_name -> users.alias';
        END IF;
    END$$;
    """
)

# ---------------------------------------------------------------------------
# 마이그레이션 #2 — attachments 테이블 + 인덱스
#  CREATE TABLE IF NOT EXISTS 라 멱등.
# ---------------------------------------------------------------------------
_CREATE_ATTACHMENTS_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS attachments (
        id                BIGSERIAL PRIMARY KEY,
        user_id           BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        conversation_id   BIGINT REFERENCES conversations(id) ON DELETE CASCADE,
        message_id        BIGINT REFERENCES messages(id) ON DELETE CASCADE,
        bucket            VARCHAR(64)  NOT NULL,
        object_key        VARCHAR(512) NOT NULL,
        original_filename VARCHAR(255) NOT NULL,
        mime              VARCHAR(128) NOT NULL,
        size_bytes        BIGINT NOT NULL,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
)

# asyncpg 는 prepared statement 다중 명령(`;`로 연결된 여러 statement)을 거부하므로
# 각 인덱스 생성을 별도 statement 로 분리해 둔다. CREATE INDEX IF NOT EXISTS 자체는 멱등.
_ATTACHMENTS_INDEX_STMTS = [
    text("CREATE INDEX IF NOT EXISTS ix_attachments_user_created ON attachments(user_id, created_at DESC)"),
    text("CREATE INDEX IF NOT EXISTS ix_attachments_conversation ON attachments(conversation_id)"),
    text("CREATE INDEX IF NOT EXISTS ix_attachments_message ON attachments(message_id)"),
]


# ---------------------------------------------------------------------------
# 마이그레이션 #3 — projects / project_conversations 테이블 + 인덱스 + 트리거.
#  feature_checklist §B "프로젝트 (대화 그룹화)" 도입과 함께 누적.
#  04_projects.sql 은 새 볼륨 최초 기동 시에만 실행되므로, 기존 볼륨에서는
#  여기서 같은 형태를 보장한다.
# ---------------------------------------------------------------------------
_CREATE_PROJECTS_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS projects (
        id            BIGSERIAL PRIMARY KEY,
        user_id       BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name          VARCHAR(120) NOT NULL,
        description   VARCHAR(500),
        system_prompt TEXT,
        domain        user_domain,
        starred       BOOLEAN      NOT NULL DEFAULT FALSE,
        created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    );
    """
)

_CREATE_PROJECT_CONVERSATIONS_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS project_conversations (
        project_id      BIGINT      NOT NULL REFERENCES projects(id)      ON DELETE CASCADE,
        conversation_id BIGINT      NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (project_id, conversation_id)
    );
    """
)

_PROJECTS_INDEX_STMTS = [
    text("CREATE INDEX IF NOT EXISTS ix_projects_user_updated ON projects(user_id, updated_at DESC)"),
    text("CREATE INDEX IF NOT EXISTS ix_projects_user_starred ON projects(user_id, starred)"),
    text(
        "CREATE INDEX IF NOT EXISTS ix_project_conversations_project "
        "ON project_conversations(project_id, added_at DESC)"
    ),
    text(
        "CREATE INDEX IF NOT EXISTS ix_project_conversations_conversation "
        "ON project_conversations(conversation_id)"
    ),
]

# trg_set_updated_at 함수는 02_users.sql 에서 정의됨 — 새 볼륨에는 항상 존재.
# 기존 볼륨에도 02 가 한 번은 실행되었으므로 동일하게 존재한다고 가정 가능.
_PROJECTS_UPDATED_AT_TRIGGER = text(
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'projects_set_updated_at'
        ) THEN
            CREATE TRIGGER projects_set_updated_at
                BEFORE UPDATE ON projects
                FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
        END IF;
    END$$;
    """
)


# ---------------------------------------------------------------------------
# 마이그레이션 #4 — conversations.starred 컬럼 + 인덱스.
#  사이드바 RECENT 즐겨찾기(★) 기능. 03_conversations.sql 의 DDL 도 동시에 보강하므로
#  새 볼륨에는 처음부터 컬럼이 있고, 기존 볼륨에는 이 마이그레이션이 한 번 실행되고 idempotent.
# ---------------------------------------------------------------------------
_ADD_CONVERSATIONS_STARRED = text(
    """
    ALTER TABLE conversations
        ADD COLUMN IF NOT EXISTS starred BOOLEAN NOT NULL DEFAULT FALSE;
    """
)
_ADD_CONVERSATIONS_STARRED_INDEX = text(
    "CREATE INDEX IF NOT EXISTS ix_conversations_user_starred ON conversations(user_id, starred)"
)


# ---------------------------------------------------------------------------
# 마이그레이션 #5 — documents 테이블(RAG) + pgvector.
#  사칙·매뉴얼 등 사내 텍스트 검색용.
#  pgvector 확장은 외부 Postgres 에 superuser 가 한 번 설치 후 활성화한 상태.
#  최초엔 REAL[] 로 운영했으나 pgvector 활성화 후 vector(1024) 로 자동 전환.
# ---------------------------------------------------------------------------
_CREATE_VECTOR_EXTENSION = text("CREATE EXTENSION IF NOT EXISTS vector")

# 새 볼륨에서는 처음부터 vector(1024) 로 생성. 기존 REAL[] 컬럼은 별도 ALTER 로 변환.
_CREATE_DOCUMENTS_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS documents (
        id            BIGSERIAL PRIMARY KEY,
        source_path   VARCHAR(512) NOT NULL,
        source_label  VARCHAR(300) NOT NULL,
        content       TEXT         NOT NULL,
        metadata      JSONB        NOT NULL DEFAULT '{}'::jsonb,
        domain        user_domain  NOT NULL DEFAULT 'all',
        embedding     vector(1024),
        created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    );
    """
)

# 기존 볼륨이 REAL[] 로 만들어진 경우 vector(1024) 로 변환. 데이터는 보존.
# pgvector 의 vector type 은 pg float4[] 와 캐스팅 호환되지 않아 명시 변환 함수 사용.
_ALTER_EMBEDDING_TO_VECTOR = text(
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='documents' AND column_name='embedding' AND udt_name='_float4'
        ) THEN
            -- REAL[] (PG '{a,b,c}' 표현) → pgvector '[a,b,c]' 표현으로 명시 변환.
            ALTER TABLE documents
                ALTER COLUMN embedding TYPE vector(1024)
                USING (CASE WHEN embedding IS NULL THEN NULL
                            ELSE ('[' || array_to_string(embedding, ',') || ']')::vector END);
        END IF;
    END$$;
    """
)

_DOCUMENTS_INDEX_STMTS = [
    text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_source_label "
        "ON documents(source_path, source_label)"
    ),
    text("CREATE INDEX IF NOT EXISTS ix_documents_domain ON documents(domain)"),
    # IVFFlat 인덱스 — cosine 거리, lists=100. 100~10K rows 범위에 적합.
    # 더 커지면 lists 를 sqrt(N) 안팎으로, 또는 HNSW 로 전환 검토.
    text(
        "CREATE INDEX IF NOT EXISTS ix_documents_embedding_cosine "
        "ON documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    ),
]

_DOCUMENTS_UPDATED_AT_TRIGGER = text(
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger WHERE tgname = 'documents_set_updated_at'
        ) THEN
            CREATE TRIGGER documents_set_updated_at
                BEFORE UPDATE ON documents
                FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
        END IF;
    END$$;
    """
)


# ---------------------------------------------------------------------------
# 마이그레이션 #6 — mail_accounts 테이블 (메일 발신 계정/시그니처).
#  hiworks, naver, gmail 등 provider 별로 발신 계정·SMTP·시그니처·기본 수신자 저장.
#  user_id NULL 허용 — 1차는 단일 발신자 운영, 추후 사용자별 row 로 확장.
#  UNIQUE (provider, username) 로 동일 계정 중복 방지 + UPSERT 키.
# ---------------------------------------------------------------------------
_CREATE_MAIL_ACCOUNTS_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS mail_accounts (
        id              BIGSERIAL    PRIMARY KEY,
        user_id         BIGINT       REFERENCES users(id) ON DELETE CASCADE,
        provider        VARCHAR(32)  NOT NULL,
        username        VARCHAR(255) NOT NULL,
        password        TEXT         NOT NULL,
        from_name       VARCHAR(255),
        signature       TEXT,
        smtp_host       VARCHAR(255) NOT NULL,
        smtp_port       INTEGER      NOT NULL,
        smtp_use_ssl    BOOLEAN      NOT NULL DEFAULT TRUE,
        default_to      TEXT[]       NOT NULL DEFAULT ARRAY[]::TEXT[],
        default_cc      TEXT[]       NOT NULL DEFAULT ARRAY[]::TEXT[],
        default_cc_all  TEXT[]       NOT NULL DEFAULT ARRAY[]::TEXT[],
        is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
        is_default      BOOLEAN      NOT NULL DEFAULT FALSE,
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_mail_accounts_provider_username UNIQUE (provider, username)
    );
    """
)

_MAIL_ACCOUNTS_INDEX_STMTS = [
    text("CREATE INDEX IF NOT EXISTS ix_mail_accounts_user_provider ON mail_accounts(user_id, provider)"),
    text("CREATE INDEX IF NOT EXISTS ix_mail_accounts_provider ON mail_accounts(provider)"),
    text("CREATE INDEX IF NOT EXISTS ix_mail_accounts_is_default ON mail_accounts(is_default) WHERE is_default = TRUE"),
]

_MAIL_ACCOUNTS_UPDATED_AT_TRIGGER = text(
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger WHERE tgname = 'mail_accounts_set_updated_at'
        ) THEN
            CREATE TRIGGER mail_accounts_set_updated_at
                BEFORE UPDATE ON mail_accounts
                FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
        END IF;
    END$$;
    """
)


# ---------------------------------------------------------------------------
# 마이그레이션 #7 — vendors / vendor_aliases (수금 거래처 마스터 + 별칭 치환).
#  재무팀 수금확인거래처를 canonical 로 저장하고, 계획표 옛 표기(별칭)를 치환한다.
#  거래처별 집행일 방향(전진/후진)·회수유형을 영구 저장 — 자동기입의 근거.
#  name_norm / alias_norm 는 정규화(㈜·주식회사·공백 제거) 키로 매칭에 사용.
# ---------------------------------------------------------------------------
_CREATE_VENDORS_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS vendors (
        id               BIGSERIAL    PRIMARY KEY,
        canonical_name   VARCHAR(200) NOT NULL,
        name_norm        VARCHAR(200) NOT NULL,
        currency         VARCHAR(8),                         -- 원화 / 외화
        collection_type  VARCHAR(16)  NOT NULL DEFAULT '미정', -- 말일 / 고정일 / 어음 / 미정
        collection_day   INTEGER,                            -- 고정일형 회수일(1~31)
        direction        VARCHAR(8)   NOT NULL DEFAULT '전진', -- 후진 / 전진 / 미정
        direction_source VARCHAR(64),                        -- 실데이터 / 담당자확정 / 기본값
        note             TEXT,
        is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
        created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_vendors_name_norm UNIQUE (name_norm)
    );
    """
)

_CREATE_VENDOR_ALIASES_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS vendor_aliases (
        id         BIGSERIAL    PRIMARY KEY,
        vendor_id  BIGINT       NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
        alias      VARCHAR(200) NOT NULL,
        alias_norm VARCHAR(200) NOT NULL,
        created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_vendor_aliases_norm UNIQUE (alias_norm)
    );
    """
)

_VENDORS_INDEX_STMTS = [
    text("CREATE INDEX IF NOT EXISTS ix_vendors_active ON vendors(is_active)"),
    text("CREATE INDEX IF NOT EXISTS ix_vendor_aliases_vendor ON vendor_aliases(vendor_id)"),
]

_VENDORS_UPDATED_AT_TRIGGER = text(
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger WHERE tgname = 'vendors_set_updated_at'
        ) THEN
            CREATE TRIGGER vendors_set_updated_at
                BEFORE UPDATE ON vendors
                FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
        END IF;
    END$$;
    """
)


# 제안서·개념도 확정 기록 — 확정본을 다음 생성의 few-shot 으로 재활용(confirmed.py).
_CREATE_PROPOSAL_RECORDS_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS proposal_records (
        id                    BIGSERIAL PRIMARY KEY,
        user_id               BIGINT       NOT NULL,
        kind                  VARCHAR(32)  NOT NULL,
        title                 VARCHAR(300) NOT NULL DEFAULT '',
        request_text          TEXT         NOT NULL,
        content               JSONB        NOT NULL,
        result_attachment_ids JSONB        NOT NULL DEFAULT '[]'::jsonb,
        status                VARCHAR(16)  NOT NULL DEFAULT 'draft',
        revised_attachment_id BIGINT,
        revised_text          TEXT,
        embedding             vector(1024),
        created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        confirmed_at          TIMESTAMPTZ
    );
    """
)
_PROPOSAL_RECORDS_INDEX_STMTS = [
    text("CREATE INDEX IF NOT EXISTS ix_proposal_records_user ON proposal_records(user_id)"),
    text("CREATE INDEX IF NOT EXISTS ix_proposal_records_kind_status "
         "ON proposal_records(kind, status)"),
]

# 외부 AI(Codex 브리지) 전송 기록 — 가명 처리본만 남긴다(원래 이름은 저장하지 않음). external_gateway.py.
_CREATE_EXTERNAL_CALLS_TABLE = text(
    """
    CREATE TABLE IF NOT EXISTS external_calls (
        id             BIGSERIAL PRIMARY KEY,
        user_id        BIGINT,
        endpoint       VARCHAR(64)  NOT NULL,
        mode           VARCHAR(16)  NOT NULL,
        status         VARCHAR(16)  NOT NULL DEFAULT 'sent',
        payload_masked JSONB        NOT NULL,
        masked_counts  JSONB        NOT NULL DEFAULT '{}'::jsonb,
        created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    );
    """
)
_EXTERNAL_CALLS_INDEX_STMTS = [
    text("CREATE INDEX IF NOT EXISTS ix_external_calls_created ON external_calls(created_at)"),
]




# 제안서 작업 프로젝트 — 가이드 기준 대화형 절차(docs/design/proposal_wizard_design.md, proposal_project/).
_PROPOSAL_PROJECT_STMTS = [
    text(
        """
        CREATE TABLE IF NOT EXISTS proposal_projects (
            id            BIGSERIAL PRIMARY KEY,
            user_id       BIGINT       NOT NULL,
            title         VARCHAR(300) NOT NULL,
            stage         VARCHAR(20)  NOT NULL DEFAULT 'intake',
            request_text  TEXT         NOT NULL DEFAULT '',
            intake        JSONB        NOT NULL DEFAULT '{}'::jsonb,
            output        JSONB        NOT NULL DEFAULT '{}'::jsonb,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        );
        """
    ),
    text("CREATE INDEX IF NOT EXISTS ix_proposal_projects_user ON proposal_projects(user_id)"),
    text(
        """
        CREATE TABLE IF NOT EXISTS project_items (
            id          BIGSERIAL PRIMARY KEY,
            project_id  BIGINT      NOT NULL REFERENCES proposal_projects(id) ON DELETE CASCADE,
            code        VARCHAR(8)  NOT NULL,
            value       TEXT        NOT NULL DEFAULT '',
            unit        VARCHAR(40) NOT NULL DEFAULT '',
            status      VARCHAR(16) NOT NULL DEFAULT 'empty',
            nature      VARCHAR(40) NOT NULL DEFAULT '',
            source      VARCHAR(16) NOT NULL DEFAULT '',
            evidence    TEXT        NOT NULL DEFAULT '',
            checked_at  TIMESTAMPTZ,
            pages       JSONB       NOT NULL DEFAULT '[]'::jsonb,
            version     INT         NOT NULL DEFAULT 1,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (project_id, code)
        );
        """
    ),
    text(
        """
        CREATE TABLE IF NOT EXISTS project_item_history (
            id          BIGSERIAL PRIMARY KEY,
            project_id  BIGINT      NOT NULL REFERENCES proposal_projects(id) ON DELETE CASCADE,
            code        VARCHAR(8)  NOT NULL,
            old_value   TEXT, new_value TEXT, old_status VARCHAR(16), new_status VARCHAR(16),
            reason      TEXT        NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text(
        """
        CREATE TABLE IF NOT EXISTS project_assets (
            id             BIGSERIAL PRIMARY KEY,
            project_id     BIGINT      NOT NULL REFERENCES proposal_projects(id) ON DELETE CASCADE,
            attachment_id  BIGINT      NOT NULL,
            role           VARCHAR(16) NOT NULL,
            note           TEXT        NOT NULL DEFAULT '',
            external_ok    BOOLEAN     NOT NULL DEFAULT FALSE,
            readable       BOOLEAN,
            alt_id         VARCHAR(8),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text(
        """
        CREATE TABLE IF NOT EXISTS project_messages (
            id          BIGSERIAL PRIMARY KEY,
            project_id  BIGINT      NOT NULL REFERENCES proposal_projects(id) ON DELETE CASCADE,
            role        VARCHAR(16) NOT NULL,
            content     TEXT        NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text(
        """
        CREATE TABLE IF NOT EXISTS project_approvals (
            id           BIGSERIAL PRIMARY KEY,
            project_id   BIGINT      NOT NULL REFERENCES proposal_projects(id) ON DELETE CASCADE,
            target       VARCHAR(40) NOT NULL,
            version      INT         NOT NULL DEFAULT 1,
            approved_by  BIGINT      NOT NULL,
            approved_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    # 3~6단계 — 대안·페이지 구성·견적 행은 프로젝트에 JSON 으로(5c PPT 계약 형식 그대로),
    # 오래 걸리는 작업(공정 제안·이미지·PPT)의 진행 상태는 job 에 둔다.
    text("ALTER TABLE proposal_projects ADD COLUMN IF NOT EXISTS alternatives JSONB NOT NULL DEFAULT '[]'::jsonb"),
    text("ALTER TABLE proposal_projects ADD COLUMN IF NOT EXISTS pages JSONB NOT NULL DEFAULT '[]'::jsonb"),
    text("ALTER TABLE proposal_projects ADD COLUMN IF NOT EXISTS quote_lines JSONB NOT NULL DEFAULT '[]'::jsonb"),
    text("ALTER TABLE proposal_projects ADD COLUMN IF NOT EXISTS job JSONB NOT NULL DEFAULT '{}'::jsonb"),
    # 공정 컨셉 제안 때 떠올린 회사 경험(experience.recall 결과) — 작업 화면 "경험 기반 제안" [적용]/[빼기].
    text("ALTER TABLE proposal_projects ADD COLUMN IF NOT EXISTS experience JSONB NOT NULL DEFAULT '[]'::jsonb"),
    # 경험 카드에 대한 사용자 판정 — 적용된 지식은 먼저 떠오르고, 여러 번 빠진 지식은 더 떠올리지 않는다.
    text(
        """
        CREATE TABLE IF NOT EXISTS experience_feedback (
            id          BIGSERIAL PRIMARY KEY,
            card_table  VARCHAR(40) NOT NULL DEFAULT 'experience_cards',
            card_id     BIGINT      NOT NULL,
            project_id  BIGINT,
            user_id     BIGINT,
            action      VARCHAR(10) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text("CREATE INDEX IF NOT EXISTS ix_experience_feedback_card ON experience_feedback(card_table, card_id)"),
    text(
        """
        CREATE TABLE IF NOT EXISTS project_images (
            id             BIGSERIAL PRIMARY KEY,
            project_id     BIGINT      NOT NULL REFERENCES proposal_projects(id) ON DELETE CASCADE,
            alt_id         VARCHAR(8),
            version        INT         NOT NULL DEFAULT 1,
            attachment_id  BIGINT,
            status         VARCHAR(16) NOT NULL DEFAULT 'generating',
            prompt         TEXT        NOT NULL DEFAULT '',
            checks         JSONB       NOT NULL DEFAULT '[]'::jsonb,
            labels         JSONB       NOT NULL DEFAULT '[]'::jsonb,
            ref_sent       INT         NOT NULL DEFAULT 0,
            ref_used       INT,
            error          TEXT        NOT NULL DEFAULT '',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text("CREATE INDEX IF NOT EXISTS ix_project_images_project ON project_images(project_id)"),
    # 이 버전을 만든 사용자 수정 요청(첫 버전은 빈 값) — 버전 이력에 보여 준다.
    text("ALTER TABLE project_images ADD COLUMN IF NOT EXISTS revision TEXT NOT NULL DEFAULT ''"),
    # 회사 경험 카드 — 사례집·회사 제안서·확정 제안서를 "문제 → 해법 → 효과 → 주의점" 으로 정리한 지식.
    # 검색해 붙이는 원문(documents)과 달리, 공정 제안 때 분야별로 통째로 읽혀 먼저 제안된다(experience.py).
    text(
        """
        CREATE TABLE IF NOT EXISTS experience_cards (
            id            BIGSERIAL PRIMARY KEY,
            source_key    VARCHAR(200) NOT NULL UNIQUE,
            source_type   VARCHAR(20)  NOT NULL,
            source_title  TEXT         NOT NULL,
            evidence      VARCHAR(10)  NOT NULL,
            process       TEXT         NOT NULL DEFAULT '',
            card          JSONB        NOT NULL,
            active        BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        );
        """
    ),
    # 경험 카드 승인 — 영업 관리자가 승인해야 회상에 쓰인다(active). 시스템 적재분(기존 행)은 approved.
    text("ALTER TABLE experience_cards ADD COLUMN IF NOT EXISTS review_status VARCHAR(10) NOT NULL DEFAULT 'approved'"),
    text("ALTER TABLE experience_cards ADD COLUMN IF NOT EXISTS submitted_by BIGINT"),
    text("ALTER TABLE experience_cards ADD COLUMN IF NOT EXISTS reviewed_by BIGINT"),
    text("ALTER TABLE experience_cards ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ"),
    text("ALTER TABLE experience_cards ADD COLUMN IF NOT EXISTS review_note TEXT NOT NULL DEFAULT ''"),
    text(
        """
        CREATE TABLE IF NOT EXISTS project_outputs (
            id             BIGSERIAL PRIMARY KEY,
            project_id     BIGINT      NOT NULL REFERENCES proposal_projects(id) ON DELETE CASCADE,
            version        INT         NOT NULL,
            attachment_id  BIGINT      NOT NULL,
            filename       TEXT        NOT NULL,
            report         JSONB       NOT NULL DEFAULT '{}'::jsonb,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    # 회사 지식 카드 — 회사 소개서 등 회사 자체 자료의 제품·적용 사례·역량 정리(company_knowledge/cards.py).
    text(
        """
        CREATE TABLE IF NOT EXISTS knowledge_cards (
            id           BIGSERIAL PRIMARY KEY,
            card_key     VARCHAR(200) NOT NULL UNIQUE,
            kind         VARCHAR(20)  NOT NULL,
            name         TEXT         NOT NULL,
            evidence     VARCHAR(20)  NOT NULL,
            source_doc   TEXT         NOT NULL,
            slides       INT[]        NOT NULL DEFAULT '{}',
            card         JSONB        NOT NULL,
            active       BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        );
        """
    ),
    # 회상 단서 — "이런 상황이면 이 카드가 떠올라야 한다" 는 문장과 임베딩. knowledge_cards·experience_cards 공용
    # (card_table 로 구분, experience_cards 스키마는 건드리지 않는다). company_knowledge/recall.py 가 읽는다.
    text(
        """
        CREATE TABLE IF NOT EXISTS knowledge_cues (
            id          BIGSERIAL PRIMARY KEY,
            card_table  VARCHAR(40)  NOT NULL,
            card_id     BIGINT       NOT NULL,
            cue         TEXT         NOT NULL,
            embedding   vector(1024) NOT NULL,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE (card_table, card_id, cue)
        );
        """
    ),
    text("CREATE INDEX IF NOT EXISTS ix_knowledge_cues_card ON knowledge_cues(card_table, card_id)"),
    # 회상 결과 재사용 — 같은 상황·같은 기억 상태면 GPT 를 다시 부르지 않는다(company_knowledge/recall.py).
    text(
        """
        CREATE TABLE IF NOT EXISTS recall_cache (
            cache_key   VARCHAR(64) PRIMARY KEY,
            result      JSONB       NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    # 제품 이미지 — 제품 카드에 연결한 사진(company_knowledge/product_images.py). 컨셉 이미지를 그릴 때 외형 참고로
    # 보낸다. 소개서 추출분·직접 올린 사진 모두 영업 관리자 승인(active) 후에만 쓰인다.
    text(
        """
        CREATE TABLE IF NOT EXISTS product_images (
            id             BIGSERIAL PRIMARY KEY,
            card_id        BIGINT       NOT NULL REFERENCES knowledge_cards(id) ON DELETE CASCADE,
            object_key     TEXT         NOT NULL,
            sha256         VARCHAR(64)  NOT NULL,
            width          INT          NOT NULL,
            height         INT          NOT NULL,
            source         VARCHAR(20)  NOT NULL,
            source_ref     TEXT         NOT NULL DEFAULT '',
            review_status  VARCHAR(20)  NOT NULL DEFAULT 'pending',
            active         BOOLEAN      NOT NULL DEFAULT FALSE,
            submitted_by   BIGINT       REFERENCES users(id) ON DELETE SET NULL,
            reviewed_by    BIGINT       REFERENCES users(id) ON DELETE SET NULL,
            reviewed_at    TIMESTAMPTZ,
            created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE (card_id, sha256)
        );
        """
    ),
    text("CREATE INDEX IF NOT EXISTS ix_product_images_card ON product_images(card_id) WHERE active"),
    # 완성본 프롬프트로 수정(project_deck.edit_deck)의 출발점 — 쪽별 문구·구성 JSON.
    text("ALTER TABLE project_outputs ADD COLUMN IF NOT EXISTS deck JSONB"),
    # 로봇 스펙 DB — 타사 6축 협동로봇 비교표(company_knowledge/robot_specs.py). 회사 지식 카드와 따로 둔다.
    text(
        """
        CREATE TABLE IF NOT EXISTS robot_specs (
            id                BIGSERIAL PRIMARY KEY,
            maker             TEXT        NOT NULL,
            model             TEXT        NOT NULL,
            robot_type        TEXT        NOT NULL DEFAULT '협동로봇',
            axes              INT,
            payload_kg        NUMERIC,
            reach_mm          NUMERIC,
            repeatability_mm  NUMERIC,
            weight_kg         NUMERIC,
            ip_rating         TEXT,
            payload_class     TEXT        NOT NULL DEFAULT '',
            unverified        JSONB       NOT NULL DEFAULT '[]'::jsonb,
            source_doc        TEXT        NOT NULL DEFAULT '',
            source_slide      INT,
            active            BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (maker, model)
        );
        """
    ),
    text("CREATE INDEX IF NOT EXISTS ix_robot_specs_payload ON robot_specs(payload_kg) WHERE active"),
    # 회사 제품 추천 + 정정 학습(company_knowledge/product_recommend.py). 정정은 저장 즉시 켜지고(active),
    # 영업 관리자가 사후 검토(review_status pending→kept|off).
    text(
        """
        CREATE TABLE IF NOT EXISTS product_recommendations (
            id          BIGSERIAL PRIMARY KEY,
            user_id     BIGINT      REFERENCES users(id) ON DELETE SET NULL,
            request     TEXT        NOT NULL,
            result      JSONB       NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text(
        """
        CREATE TABLE IF NOT EXISTS product_corrections (
            id                 BIGSERIAL PRIMARY KEY,
            user_id            BIGINT      REFERENCES users(id) ON DELETE SET NULL,
            recommendation_id  BIGINT      REFERENCES product_recommendations(id) ON DELETE SET NULL,
            situation          TEXT        NOT NULL DEFAULT '',
            wrong_name         TEXT        NOT NULL DEFAULT '',
            wrong_card_id      BIGINT,
            right_name         TEXT        NOT NULL,
            right_card_id      BIGINT,
            reason             TEXT        NOT NULL,
            rule               TEXT        NOT NULL DEFAULT '',
            note               TEXT        NOT NULL DEFAULT '',
            embedding          vector(1024),
            active             BOOLEAN     NOT NULL DEFAULT TRUE,
            review_status      VARCHAR(10) NOT NULL DEFAULT 'pending',
            reviewed_by        BIGINT      REFERENCES users(id) ON DELETE SET NULL,
            reviewed_at        TIMESTAMPTZ,
            hits               INT         NOT NULL DEFAULT 0,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text("CREATE INDEX IF NOT EXISTS ix_product_corrections_active ON product_corrections(id) WHERE active"),
    # 확정 제안(사용자 2026-10-02): 추천을 [최종 제안 확정]하면 미팅·입력·선정 결과·근거·대화를 한 건으로 남긴다(고객 제안 한 건 = 한 프로젝트).
    text(
        """
        CREATE TABLE IF NOT EXISTS product_proposals (
            id                 BIGSERIAL PRIMARY KEY,
            recommendation_id  BIGINT      REFERENCES product_recommendations(id) ON DELETE SET NULL,
            user_id            BIGINT      REFERENCES users(id) ON DELETE SET NULL,
            kind               VARCHAR(10) NOT NULL DEFAULT 'other',
            customer           TEXT        NOT NULL DEFAULT '',
            title              TEXT        NOT NULL DEFAULT '',
            model              TEXT        NOT NULL DEFAULT '',
            summary            TEXT        NOT NULL DEFAULT '',
            memo               TEXT        NOT NULL DEFAULT '',
            snapshot           JSONB       NOT NULL DEFAULT '{}'::jsonb,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text("CREATE UNIQUE INDEX IF NOT EXISTS ux_product_proposals_rec ON product_proposals(recommendation_id) WHERE recommendation_id IS NOT NULL"),
    text("CREATE INDEX IF NOT EXISTS ix_product_proposals_created ON product_proposals(created_at DESC)"),
    # AI 학습 내용 = 정정(correction: A 말고 B) + 성공 사례(success: 이 공정에서 이 제품을 써서 이런 결과).
    text("ALTER TABLE product_corrections ADD COLUMN IF NOT EXISTS kind VARCHAR(10) NOT NULL DEFAULT 'correction'"),
    # 맥봇 제품 단가 DB — 회사 단가표(ATC 유선 ~ MG, company_knowledge/product_prices.py). 견적서 작성용.
    # 0원 칸은 price_status='unset'·가격 NULL(무상 아님). 최신 단가표만 active.
    text(
        """
        CREATE TABLE IF NOT EXISTS product_prices (
            id              BIGSERIAL PRIMARY KEY,
            price_list      TEXT        NOT NULL,
            effective_date  DATE,
            category        TEXT        NOT NULL,
            model           TEXT        NOT NULL,
            item            TEXT        NOT NULL,
            customer_price  NUMERIC,
            dealer_price    NUMERIC,
            price_status    VARCHAR(10) NOT NULL DEFAULT 'set',
            source_cell     TEXT        NOT NULL DEFAULT '',
            note            TEXT        NOT NULL DEFAULT '',
            active          BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (price_list, model, item)
        );
        """
    ),
    text("CREATE INDEX IF NOT EXISTS ix_product_prices_model ON product_prices(model, item) WHERE active"),
    # 견적서(company_knowledge/quote_xlsx.py) — 견적번호 = 영업 건 번호 S{YY}-{순번}(sales_deals.deal_no) 유일, 머리·품목을 남겨 같은 파일을 다시 만든다.
    text(
        """
        CREATE TABLE IF NOT EXISTS product_quotes (
            id                 BIGSERIAL PRIMARY KEY,
            recommendation_id  BIGINT      REFERENCES product_recommendations(id) ON DELETE SET NULL,
            user_id            BIGINT      REFERENCES users(id) ON DELETE SET NULL,
            quote_no           TEXT        NOT NULL UNIQUE,
            lang               VARCHAR(2)  NOT NULL DEFAULT 'ko',
            customer           TEXT        NOT NULL,
            file_name          TEXT        NOT NULL,
            header             JSONB       NOT NULL DEFAULT '{}'::jsonb,
            lines              JSONB       NOT NULL DEFAULT '[]'::jsonb,
            total              NUMERIC     NOT NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text("CREATE INDEX IF NOT EXISTS ix_product_quotes_rec ON product_quotes(recommendation_id)"),
    # 견적서 작성(quote_session, 사용자 2026-10-06) — 초안(draft)을 대화·직접 수정으로 채우고 발행(issued). 견적번호는 첫 발행 때,
    # 다시 발행하면 같은 번호에 revision +1, 이전 판은 history 에.
    text("ALTER TABLE product_quotes ALTER COLUMN quote_no DROP NOT NULL"),
    text("ALTER TABLE product_quotes ALTER COLUMN customer DROP NOT NULL"),
    text("ALTER TABLE product_quotes ALTER COLUMN file_name DROP NOT NULL"),
    text("ALTER TABLE product_quotes ALTER COLUMN total DROP NOT NULL"),
    text("ALTER TABLE product_quotes ADD COLUMN IF NOT EXISTS status VARCHAR(10) NOT NULL DEFAULT 'draft'"),
    text("ALTER TABLE product_quotes ADD COLUMN IF NOT EXISTS state JSONB NOT NULL DEFAULT '{}'::jsonb"),
    text("ALTER TABLE product_quotes ADD COLUMN IF NOT EXISTS revision INT NOT NULL DEFAULT 0"),
    text("ALTER TABLE product_quotes ADD COLUMN IF NOT EXISTS history JSONB NOT NULL DEFAULT '[]'::jsonb"),
    text("ALTER TABLE product_quotes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
]

# 영업 건 관리(sales_deals/) — 견적 → 수주 → 청구(세금계산서·입금) → 출하. 한 행이 한 건.
# 단계는 저장하지 않고 기록(won·invoices·payments·shipments·paid_full)에서 계산한다(status.derive).
# 기존 엑셀에서 옮긴 건은 imported=TRUE, 이관 때 판단이 애매했던 점은 review_alerts(확인 완료 시 review_done).
_SALES_DEALS_STMTS = [
    text(
        """
        CREATE TABLE IF NOT EXISTS sales_deals (
            id             BIGSERIAL PRIMARY KEY,
            deal_no        TEXT        NOT NULL UNIQUE,
            kind           VARCHAR(20) NOT NULL DEFAULT 'quote',
            customer       TEXT,
            contact        TEXT,
            owner          TEXT,
            title          TEXT        NOT NULL DEFAULT '',
            base_date      DATE,
            quote          JSONB,
            pay_terms      JSONB       NOT NULL DEFAULT '[]'::jsonb,
            won            BOOLEAN,
            won_date       DATE,
            invoices       JSONB       NOT NULL DEFAULT '[]'::jsonb,
            payments       JSONB       NOT NULL DEFAULT '[]'::jsonb,
            paid_full      BOOLEAN     NOT NULL DEFAULT FALSE,
            shipments      JSONB       NOT NULL DEFAULT '[]'::jsonb,
            shipped_note   BOOLEAN     NOT NULL DEFAULT FALSE,
            note           TEXT        NOT NULL DEFAULT '',
            review_alerts  JSONB       NOT NULL DEFAULT '[]'::jsonb,
            review_done    BOOLEAN     NOT NULL DEFAULT FALSE,
            imported       BOOLEAN     NOT NULL DEFAULT FALSE,
            legacy         JSONB       NOT NULL DEFAULT '{}'::jsonb,
            created_by     BIGINT      REFERENCES users(id) ON DELETE SET NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text("CREATE INDEX IF NOT EXISTS ix_sales_deals_order ON sales_deals(base_date DESC NULLS LAST, id DESC)"),
    # 회사 제품 추천의 [최종 제안 확정] → 영업 건(kind='recommend', 단계 '제품 추천 확정', 사용자 2026-10-06).
    # 미팅 내용·최종 추천·근거·대화는 product_proposals 에 두고 건에서 [미팅 정보]로 본다. 견적서를 발행하면 같은 건이 견적 단계로.
    text("ALTER TABLE sales_deals ADD COLUMN IF NOT EXISTS proposal_id BIGINT"),
    text("CREATE UNIQUE INDEX IF NOT EXISTS ux_sales_deals_proposal ON sales_deals(proposal_id) WHERE proposal_id IS NOT NULL"),
    # 발주서(고객사 주문서) 파일 — 수주 확정 때 첨부(사용자 2026-10-06). 파일은 MinIO, 여기엔 [{key, filename, mime, size, uploaded_at, uploaded_by}].
    text("ALTER TABLE sales_deals ADD COLUMN IF NOT EXISTS po_files JSONB NOT NULL DEFAULT '[]'::jsonb"),
    # 수주 뒤 진행(사용자 2026-10-07): 거래명세서(발급 정보 — 공급받는자·품목·합계) → 결제 방식(after·split·prepay, sales_deals/flow.py).
    text("ALTER TABLE sales_deals ADD COLUMN IF NOT EXISTS statement JSONB"),
    text("ALTER TABLE sales_deals ADD COLUMN IF NOT EXISTS pay_case VARCHAR(10)"),
    # 수주 진행 탭(AI 대화) — 명세서 초안·출하 정보·입금 후보·대화(sales_deals/order_agent.py). 리스트에는 싣지 않는다.
    text("ALTER TABLE sales_deals ADD COLUMN IF NOT EXISTS order_state JSONB"),
    # 건 번호 = 담당 이니셜 + 날짜(AL20261008), 화면에는 뒤에 버전(-01·-02 — 건 내용이 바뀔 때마다 +1). 드랍 상태(사용자 2026-10-08).
    text("ALTER TABLE sales_deals ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1"),
    text("ALTER TABLE sales_deals ADD COLUMN IF NOT EXISTS dropped BOOLEAN NOT NULL DEFAULT FALSE"),
    text("ALTER TABLE sales_deals ADD COLUMN IF NOT EXISTS drop_reason TEXT"),
    # 버전 = 견적서 판(사용자 2026-10-08 변경 — 상태 변경마다 +1 하던 것을 견적서 v1·v2 로). 이미 올라가 있던 버전을 판에 맞춘다.
    # (다시 변경, 사용자 2026-10-08 오후) version 은 히스토리 번호 차례(sales_deals.cur_no 와 함께, service._log) —
    # 시작할 때마다 판 번호로 되돌리던 UPDATE 는 뺐다(히스토리 번호가 매번 처음으로 돌아가므로).
    text(
        """
        CREATE TABLE IF NOT EXISTS sales_initials (
            name_key   TEXT        PRIMARY KEY,
            name       TEXT        NOT NULL,
            initials   VARCHAR(4)  NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text("INSERT INTO sales_initials (name_key, name, initials) VALUES ('alex','Alex','AL'),('victor','Victor','VT'),"
         "('lucas','Lucas','LU'),('mason','Mason','MS'),('charles','Charles','CH') ON CONFLICT (name_key) DO NOTHING"),
    # 변경 이력 — 누가 언제 무엇을(수주 확정·계산서·입금·출하·확인 완료·삭제) 했는지.
    text(
        """
        CREATE TABLE IF NOT EXISTS sales_deal_events (
            id          BIGSERIAL PRIMARY KEY,
            deal_id     BIGINT      NOT NULL REFERENCES sales_deals(id) ON DELETE CASCADE,
            user_id     BIGINT      REFERENCES users(id) ON DELETE SET NULL,
            action      VARCHAR(40) NOT NULL,
            detail      JSONB       NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text("CREATE INDEX IF NOT EXISTS ix_sales_deal_events_deal ON sales_deal_events(deal_id, created_at DESC)"),
    # 지운 건 보관(사용자 2026-10-07: 리스트에서 체크해 여러 건 삭제) — 잘못 지웠을 때 되살릴 수 있게 건 전체·변경 이력을 그대로.
    # 건번호는 지운 번호까지 보고 다음 번호를 매긴다(견적서 번호와 겹치지 않게, service.next_deal_no).
    text(
        """
        CREATE TABLE IF NOT EXISTS sales_deal_trash (
            id          BIGSERIAL PRIMARY KEY,
            deal_id     BIGINT      NOT NULL,
            deal_no     TEXT        NOT NULL,
            deal        JSONB       NOT NULL,
            events      JSONB       NOT NULL DEFAULT '[]'::jsonb,
            deleted_by  BIGINT      REFERENCES users(id) ON DELETE SET NULL,
            deleted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    text("CREATE INDEX IF NOT EXISTS ix_sales_deal_trash_no ON sales_deal_trash(deal_no)"),
    # 견적서 수기 작성(사용자 2026-10-08) — AI 추천 없이 견적부터 시작하는 프로젝트(그리퍼·AMR 등).
    # - 건 번호는 견적서 첫 발행 때 부여: [최종 제안 확정]은 번호 없이 건만 등록(deal_no NULL, UNIQUE 는 NULL 여러 개 허용)
    # - 미팅 정보 직접 입력(AI 추천 기록이 없는 건) — sales_deals.meeting
    # - 수기 견적 ↔ 영업 건 연결(추천 기록이 없으니 product_quotes.deal_id 로)
    text("ALTER TABLE sales_deals ALTER COLUMN deal_no DROP NOT NULL"),
    text("ALTER TABLE sales_deal_trash ALTER COLUMN deal_no DROP NOT NULL"),
    text("ALTER TABLE sales_deals ADD COLUMN IF NOT EXISTS meeting JSONB NOT NULL DEFAULT '{}'::jsonb"),
    text("ALTER TABLE product_quotes ADD COLUMN IF NOT EXISTS deal_id BIGINT REFERENCES sales_deals(id) ON DELETE SET NULL"),
    text("CREATE INDEX IF NOT EXISTS ix_product_quotes_deal ON product_quotes(deal_id)"),
    # 히스토리 번호(사용자 2026-10-08): 견적서 첫 발행 AL20261008-01 → 바뀔 때마다 -02·-03…, 날짜가 바뀌면 그 날짜(AL20261010-05).
    # 이력 한 줄 = 한 번호(ver_no), 그때 단계·견적 판(snap). 히스토리 줄 지우기는 기록만 숨김(hidden, 번호는 다시 안 씀).
    text("ALTER TABLE sales_deals ADD COLUMN IF NOT EXISTS cur_no VARCHAR(24)"),
    text("ALTER TABLE sales_deal_events ADD COLUMN IF NOT EXISTS ver_no VARCHAR(24)"),
    text("ALTER TABLE sales_deal_events ADD COLUMN IF NOT EXISTS snap JSONB"),
    text("ALTER TABLE sales_deal_events ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMPTZ"),
    text("ALTER TABLE sales_deal_events ADD COLUMN IF NOT EXISTS hidden_by BIGINT REFERENCES users(id) ON DELETE SET NULL"),
    # 번호는 프로젝트마다 -01부터(사용자 2026-10-08: 같은 담당·같은 날 다른 고객사도 AL20261008-01, B·C 안 붙임) —
    # 건 번호·견적번호는 여러 프로젝트가 같을 수 있고, 히스토리 번호는 한 프로젝트 안에서만 겹치지 않는다.
    text("DROP INDEX IF EXISTS ux_sales_deal_events_ver_no"),
    text("CREATE UNIQUE INDEX IF NOT EXISTS ux_sales_deal_events_deal_ver ON sales_deal_events(deal_id, ver_no) WHERE ver_no IS NOT NULL"),
    text("ALTER TABLE sales_deals DROP CONSTRAINT IF EXISTS sales_deals_deal_no_key"),
    text("CREATE INDEX IF NOT EXISTS ix_sales_deals_deal_no ON sales_deals(deal_no)"),
    text("ALTER TABLE product_quotes DROP CONSTRAINT IF EXISTS product_quotes_quote_no_key"),
    # 버그 등록(사용자 2026-10-08) — 프로필 메뉴에서 누구나. 번호 UND-00001 부터(가장 큰 번호 + 1), 사진은 MinIO bug-reports/{번호}/
    text(
        """
        CREATE TABLE IF NOT EXISTS bug_reports (
            id          BIGSERIAL PRIMARY KEY,
            report_no   VARCHAR(16) NOT NULL UNIQUE,
            title       TEXT        NOT NULL,
            content     TEXT        NOT NULL,
            photos      JSONB       NOT NULL DEFAULT '[]'::jsonb,
            status      VARCHAR(16) NOT NULL DEFAULT 'open',
            reporter_id BIGINT      REFERENCES users(id) ON DELETE SET NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
    # 옛 데이터 보관함(사용자 2026-10-08) — 예전 번호(S25-…·S26-…, 엑셀 이관분 포함) 영업 건을 건 전체·변경 이력째 옮겨 둔다.
    # 영업 건 리스트에서는 빠진다. 옮기기는 scripts/move_legacy_to_trash_bin.py(한 번).
    text(
        """
        CREATE TABLE IF NOT EXISTS trash_bin (
            id           BIGSERIAL PRIMARY KEY,
            source_table VARCHAR(40) NOT NULL,
            source_id    BIGINT      NOT NULL,
            label        TEXT,
            data         JSONB       NOT NULL,
            events       JSONB       NOT NULL DEFAULT '[]'::jsonb,
            reason       TEXT,
            moved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (source_table, source_id)
        );
        """
    ),
    # 히스토리 되돌리기(사용자 2026-10-08) — 최신 줄부터 고른 줄까지 지우고 그 아래 줄 때 내용으로. 지운 이력·되돌리기 전 내용은 여기 보관.
    text(
        """
        CREATE TABLE IF NOT EXISTS sales_deal_rollbacks (
            id           BIGSERIAL PRIMARY KEY,
            deal_id      BIGINT      NOT NULL,
            to_ver_no    VARCHAR(24) NOT NULL,
            removed      JSONB       NOT NULL DEFAULT '[]'::jsonb,
            before_state JSONB       NOT NULL,
            user_id      BIGINT      REFERENCES users(id) ON DELETE SET NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    ),
]

async def run_migrations(db: AsyncSession) -> None:
    """런타임 멱등 마이그레이션 일괄 실행."""
    await db.execute(_RENAME_USERS_FULL_NAME_TO_ALIAS)
    await db.execute(_CREATE_ATTACHMENTS_TABLE)
    for stmt in _ATTACHMENTS_INDEX_STMTS:
        await db.execute(stmt)
    await db.execute(_CREATE_PROJECTS_TABLE)
    await db.execute(_CREATE_PROJECT_CONVERSATIONS_TABLE)
    for stmt in _PROJECTS_INDEX_STMTS:
        await db.execute(stmt)
    await db.execute(_PROJECTS_UPDATED_AT_TRIGGER)
    await db.execute(_ADD_CONVERSATIONS_STARRED)
    await db.execute(_ADD_CONVERSATIONS_STARRED_INDEX)
    await db.execute(_CREATE_VECTOR_EXTENSION)
    await db.execute(_CREATE_DOCUMENTS_TABLE)
    # 기존 REAL[] 컬럼이면 vector(1024) 로 자동 변환.
    await db.execute(_ALTER_EMBEDDING_TO_VECTOR)
    for stmt in _DOCUMENTS_INDEX_STMTS:
        await db.execute(stmt)
    await db.execute(_DOCUMENTS_UPDATED_AT_TRIGGER)
    await db.execute(_CREATE_MAIL_ACCOUNTS_TABLE)
    for stmt in _MAIL_ACCOUNTS_INDEX_STMTS:
        await db.execute(stmt)
    await db.execute(_MAIL_ACCOUNTS_UPDATED_AT_TRIGGER)
    await db.execute(_CREATE_VENDORS_TABLE)
    await db.execute(_CREATE_VENDOR_ALIASES_TABLE)
    for stmt in _VENDORS_INDEX_STMTS:
        await db.execute(stmt)
    await db.execute(_VENDORS_UPDATED_AT_TRIGGER)
    await db.execute(_CREATE_PROPOSAL_RECORDS_TABLE)
    for stmt in _PROPOSAL_RECORDS_INDEX_STMTS:
        await db.execute(stmt)
    await db.execute(_CREATE_EXTERNAL_CALLS_TABLE)
    for stmt in _EXTERNAL_CALLS_INDEX_STMTS:
        await db.execute(stmt)
    for stmt in _PROPOSAL_PROJECT_STMTS:
        await db.execute(stmt)
    for stmt in _SALES_DEALS_STMTS:
        await db.execute(stmt)
    await db.commit()


async def run_migrations_on_startup() -> None:
    """lifespan startup 진입점. 자체 세션을 만들어 사용한다."""
    from .database import SessionLocal  # 순환 임포트 회피

    async with SessionLocal() as db:
        try:
            await run_migrations(db)
        except Exception:
            await db.rollback()
            raise
