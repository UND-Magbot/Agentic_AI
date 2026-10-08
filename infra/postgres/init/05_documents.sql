-- ============================================================
-- UND Cortex — RAG 문서 인덱스 (pgvector)
--   사칙·매뉴얼·회의록 등 사내 텍스트 검색용. 한 chunk = 한 row.
--   feature_checklist §B "응답 출처 표시 (RAG 검색 결과 chunk 링크)" 항목과 직결.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ------------------------------------------------------------
-- documents: chunk 단위 벡터 인덱스.
--   - source_path: 원본 파일 경로 (예: docs/UND사칙_2026년_최종.xlsx)
--   - source_label: UI 출처 칩 표기용 짧은 라벨 (예: "UND 사칙 · 대구본사 · 준수사항 #13")
--   - metadata: 자유 형식 jsonb — site/category/rule_no/sheet 등.
--   - domain: 우리 5종 도메인 키 중 하나(현재 사칙은 'all' 로 둠 — UI에선 normal 도메인이 검색).
--   - embedding: 임베딩 벡터(BAAI/bge-m3 = 1024차원).
-- ------------------------------------------------------------
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

-- 같은 파일을 재인제스트할 때 식별/upsert 용 — (source_path, content 의 해시).
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_source_label
    ON documents(source_path, source_label);

CREATE INDEX IF NOT EXISTS ix_documents_domain ON documents(domain);

-- IVFFlat 인덱스 — 데이터 100~10K rows 범위에서 충분히 빠름. 더 커지면 HNSW 로 전환.
-- lists=100 은 작은 데이터셋용. cosine 거리.
CREATE INDEX IF NOT EXISTS ix_documents_embedding_cosine
    ON documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- updated_at 자동 갱신 트리거 (02_users.sql 의 trg_set_updated_at 함수 재사용).
DROP TRIGGER IF EXISTS documents_set_updated_at ON documents;
CREATE TRIGGER documents_set_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
