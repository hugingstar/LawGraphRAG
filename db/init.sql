CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS laws (
    id              BIGSERIAL PRIMARY KEY,
    law_id          TEXT NOT NULL UNIQUE,      -- 법제처 법령ID (MST/법령ID)
    law_name        TEXT NOT NULL,             -- 법령명 (예: 산업안전보건법)
    law_type        TEXT,                      -- 법률 / 시행령 / 시행규칙 / 규칙
    promulgation_date DATE,
    effective_date  DATE,
    last_synced_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS articles (
    id              BIGSERIAL PRIMARY KEY,
    law_id          BIGINT NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
    article_no      INTEGER NOT NULL,          -- 제N조 -> N
    article_no_sub  INTEGER NOT NULL DEFAULT 0, -- 제N조의M -> M (없으면 0)
    title           TEXT,                       -- 조 제목
    full_text       TEXT NOT NULL,              -- 조문 원문 전체
    effective_date  DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (law_id, article_no, article_no_sub)
);

CREATE TABLE IF NOT EXISTS article_chunks (
    id              BIGSERIAL PRIMARY KEY,
    article_id      BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    chunk_text      TEXT NOT NULL,
    char_start      INTEGER NOT NULL,          -- articles.full_text 내 시작 오프셋
    char_end        INTEGER NOT NULL,          -- articles.full_text 내 끝 오프셋
    embedding       vector(1024),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_articles_law_id ON articles(law_id);
CREATE INDEX IF NOT EXISTS idx_article_chunks_article_id ON article_chunks(article_id);

CREATE INDEX IF NOT EXISTS idx_article_chunks_trgm
    ON article_chunks USING GIN (chunk_text gin_trgm_ops);

-- HNSW index for cosine similarity search over embeddings
CREATE INDEX IF NOT EXISTS idx_article_chunks_embedding
    ON article_chunks USING hnsw (embedding vector_cosine_ops);
