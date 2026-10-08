-- pgvector extension + 기본 스키마 골격.
-- 실제 마이그레이션은 backend의 알람빅(또는 SQL) 마이그레이션 스크립트에서 관리한다.
-- 여기서는 컨테이너 최초 기동시 한 번만 실행되어 extension만 보장한다.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
