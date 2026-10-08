#!/bin/sh
# 시드 사용자(6~7). 파일명은 02_users.sql 다음·03_conversations.sql 이전에 정렬되도록 02z_ 로 둔다
# (entrypoint 의 glob 정렬은 로케일에 따라 "_" 를 무시하므로 02_users_seed 는 02_users.sql 보다 앞선다).
# 비밀번호를 저장소에 남기지 않도록 SEED_SUPERADMIN_PASSWORD / SEED_ADMIN_PASSWORD 환경 변수로 받는다.
# postgres 공식 이미지 entrypoint 가 이 파일을 source 하므로 exit/set -e 를 쓰지 않는다.
: "${SEED_SUPERADMIN_PASSWORD:?SEED_SUPERADMIN_PASSWORD 환경 변수가 필요합니다}"
: "${SEED_ADMIN_PASSWORD:?SEED_ADMIN_PASSWORD 환경 변수가 필요합니다}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v seed_superadmin_password="$SEED_SUPERADMIN_PASSWORD" \
    -v seed_admin_password="$SEED_ADMIN_PASSWORD" <<'SQL'

-- ------------------------------------------------------------
-- 6) 시드 사용자 (bcrypt 해시는 pgcrypto의 crypt + gen_salt('bf', 12))
--    비밀번호 정책:
--      superadmin     -> $SEED_SUPERADMIN_PASSWORD
--      그 외 4명      -> $SEED_ADMIN_PASSWORD
-- ------------------------------------------------------------
INSERT INTO users (username, password_hash, alias, email, role, domain) VALUES
    ('superadmin',
     crypt(:'seed_superadmin_password', gen_salt('bf', 12)),
     '슈퍼 관리자',     'superadmin@und.local',    'superadmin',   'all'),
    ('finance_admin',
     crypt(:'seed_admin_password', gen_salt('bf', 12)),
     '재무관리 관리자', 'finance_admin@und.local', 'domain_admin', 'finance'),
    ('alex',
     crypt(:'seed_admin_password', gen_salt('bf', 12)),
     '기술영업 관리자', 'sales_admin@und.local',   'domain_admin', 'sales'),
    ('design_admin',
     crypt(:'seed_admin_password', gen_salt('bf', 12)),
     '기구설계 관리자', 'design_admin@und.local',  'domain_admin', 'design'),
    ('develop_admin',
     crypt(:'seed_admin_password', gen_salt('bf', 12)),
     '선행개발 관리자', 'develop_admin@und.local', 'domain_admin', 'develop')
ON CONFLICT (username) DO NOTHING;

-- ------------------------------------------------------------
-- 7) 시드 사용자에 권한 매핑
--    superadmin: 모든 권한
--    각 domain_admin: 자신의 도메인 admin/write/read + system.read
-- ------------------------------------------------------------

-- superadmin: 모든 권한
INSERT INTO user_permissions (user_id, permission_id, granted_by)
SELECT u.id, p.id, u.id
FROM users u, permissions p
WHERE u.username = 'superadmin'
ON CONFLICT DO NOTHING;

-- 각 도메인 관리자: 본인 도메인 admin/write/read + system.read
INSERT INTO user_permissions (user_id, permission_id, granted_by)
SELECT u.id, p.id, sa.id
FROM users u
JOIN permissions p
  ON p.domain = u.domain
  OR p.code = 'system.read'
JOIN users sa ON sa.username = 'superadmin'
WHERE u.role = 'domain_admin'
ON CONFLICT DO NOTHING;
SQL
