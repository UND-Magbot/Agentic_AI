-- ============================================================
-- UND Cortex — users / permissions 스키마
-- 컨테이너 최초 기동 시 자동 적용. 기존 DB에는 수동 실행.
-- ============================================================

-- bcrypt 해시 검증/생성을 DB 측에서 가능하도록 pgcrypto 추가.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ------------------------------------------------------------
-- 1) 도메인 / 역할 ENUM
-- ------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
        CREATE TYPE user_role AS ENUM ('superadmin', 'domain_admin', 'member', 'viewer');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_domain') THEN
        -- all = superadmin 전용 (모든 도메인 관할)
        CREATE TYPE user_domain AS ENUM ('all', 'finance', 'sales', 'design', 'develop');
    END IF;
END$$;

-- ------------------------------------------------------------
-- 2) users
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL PRIMARY KEY,
    username        VARCHAR(64)  NOT NULL UNIQUE,             -- 로그인 ID
    password_hash   VARCHAR(255) NOT NULL,                    -- bcrypt 해시
    alias           VARCHAR(100),                             -- 표시 이름(닉네임/한글명)
    email           VARCHAR(255) UNIQUE,
    role            user_role    NOT NULL DEFAULT 'member',
    domain          user_domain  NOT NULL DEFAULT 'all',
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    password_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 기존 볼륨에 full_name 컬럼이 남아있다면 alias 로 rename(멱등).
-- 새 볼륨에는 처음부터 alias 로 만들어지므로 이 블록은 no-op.
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
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS ix_users_role       ON users(role);
CREATE INDEX IF NOT EXISTS ix_users_domain     ON users(domain);
CREATE INDEX IF NOT EXISTS ix_users_is_active  ON users(is_active);

-- updated_at 자동 갱신 트리거.
CREATE OR REPLACE FUNCTION trg_set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS users_set_updated_at ON users;
CREATE TRIGGER users_set_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- ------------------------------------------------------------
-- 3) permissions (권한 마스터)
-- ------------------------------------------------------------
-- code 예: finance.read, finance.write, sales.admin, system.admin ...
CREATE TABLE IF NOT EXISTS permissions (
    id          BIGSERIAL PRIMARY KEY,
    code        VARCHAR(64)  NOT NULL UNIQUE,
    domain      user_domain  NOT NULL,
    description VARCHAR(200),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_permissions_domain ON permissions(domain);

-- ------------------------------------------------------------
-- 4) user_permissions (N:M 매핑)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_permissions (
    user_id        BIGINT      NOT NULL REFERENCES users(id)       ON DELETE CASCADE,
    permission_id  BIGINT      NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    granted_by     BIGINT               REFERENCES users(id)       ON DELETE SET NULL,
    granted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, permission_id)
);

CREATE INDEX IF NOT EXISTS ix_user_permissions_user_id       ON user_permissions(user_id);
CREATE INDEX IF NOT EXISTS ix_user_permissions_permission_id ON user_permissions(permission_id);

-- ------------------------------------------------------------
-- 5) permissions 시드
-- ------------------------------------------------------------
INSERT INTO permissions (code, domain, description) VALUES
    ('system.admin',    'all',     '시스템 전체 관리(슈퍼관리자)'),
    ('system.read',     'all',     '시스템 전반 조회'),

    ('finance.admin',   'finance', '재무관리 도메인 관리'),
    ('finance.write',   'finance', '재무 데이터 등록/수정'),
    ('finance.read',    'finance', '재무 데이터 조회'),

    ('sales.admin',     'sales',   '기술영업 도메인 관리'),
    ('sales.write',     'sales',   '영업 데이터 등록/수정'),
    ('sales.read',      'sales',   '영업 데이터 조회'),

    ('design.admin',    'design',  '기구설계 도메인 관리'),
    ('design.write',    'design',  '기구설계 데이터 등록/수정'),
    ('design.read',     'design',  '기구설계 데이터 조회'),

    ('develop.admin',   'develop', '선행개발 도메인 관리'),
    ('develop.write',   'develop', '선행개발 데이터 등록/수정'),
    ('develop.read',    'develop', '선행개발 데이터 조회')
ON CONFLICT (code) DO NOTHING;
