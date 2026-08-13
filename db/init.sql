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

-- 사내 사건 모니터링 / 심층 검토 워크플로우
CREATE TABLE IF NOT EXISTS departments (
    id      BIGSERIAL PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS sites (
    id      BIGSERIAL PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE
);

-- requester: 부서 신청자 / manager: 안전부서 관리자
CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'requester',
    rank            TEXT,                          -- 직급
    contact         TEXT,                          -- 연락처
    department_id   BIGINT REFERENCES departments(id),
    site_id         BIGINT REFERENCES sites(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 서버 측 세션. 쿠키에는 추측 불가능한 토큰만 담긴다.
CREATE TABLE IF NOT EXISTS user_sessions (
    token       TEXT PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id              BIGSERIAL PRIMARY KEY,
    department_id   BIGINT NOT NULL REFERENCES departments(id),
    site_id         BIGINT NOT NULL REFERENCES sites(id),
    -- 구조화된 신고 항목
    reporter_name    TEXT,                     -- 작성자 이름
    reporter_rank    TEXT,                     -- 작성자 직급
    reporter_contact TEXT,                     -- 작성자 연락처
    reporter_info   TEXT,                      -- (구버전) 통합 인적사항
    occurred_at     TIMESTAMPTZ,               -- 사고일시
    location        TEXT,                      -- 사고장소
    background      TEXT,                      -- 사고경위
    situation       TEXT,                      -- 당시상황
    action_taken    TEXT,                      -- 조치내용
    damage          TEXT,                      -- 피해상황
    -- 위 항목을 합쳐 만든 분석 대상 원문. citations의 char offset 기준이므로 사후 변경하지 않는다.
    statement       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'review_requested',
    citations       JSON,
    reviewer_note   TEXT,
    created_by_user_id BIGINT REFERENCES users(id),
    assigned_manager_id BIGINT REFERENCES users(id), -- 최초로 "검토 시작"을 누른 담당자
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_incidents_site_id ON incidents(site_id);
CREATE INDEX IF NOT EXISTS idx_incidents_department_id ON incidents(department_id);

-- 사건 상태 변경 이력(감사 추적). incidents.status는 현재 상태 조회용 비정규화 값이고,
-- 실제 검토 이력의 근거는 이 테이블에 append-only로 누적된다.
CREATE TABLE IF NOT EXISTS incident_events (
    id              BIGSERIAL PRIMARY KEY,
    incident_id     BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    status          TEXT NOT NULL,
    note            TEXT,
    actor_user_id   BIGINT REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_incident_events_incident_id ON incident_events(incident_id);
CREATE INDEX IF NOT EXISTS idx_incidents_created_at ON incidents(created_at);

-- 요청자 <-> 안전부서 스레드 (보완 요청 / 보완 내용 / 추가 문의 / 최종 결과)
CREATE TABLE IF NOT EXISTS incident_comments (
    id              BIGSERIAL PRIMARY KEY,
    incident_id     BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    author_user_id  BIGINT REFERENCES users(id),
    kind            TEXT NOT NULL DEFAULT 'comment',
    body            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_incident_comments_incident_id ON incident_comments(incident_id);

-- 사건에 첨부된 증빙 파일(PDF, DOCX 등). 별도 파일 스토리지 없이 DB에 직접 저장한다.
CREATE TABLE IF NOT EXISTS incident_attachments (
    id                  BIGSERIAL PRIMARY KEY,
    incident_id         BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    filename            TEXT NOT NULL,
    content_type        TEXT NOT NULL,
    size_bytes          BIGINT NOT NULL,
    data                BYTEA NOT NULL,
    uploaded_by_user_id BIGINT REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_incident_attachments_incident_id ON incident_attachments(incident_id);
