"""기동 시 실행하는 멱등 스키마 보정.

Base.metadata.create_all()은 '없는 테이블'만 만들 뿐 기존 테이블에 컬럼을 추가하지 못한다.
이미 데이터가 쌓인 DB를 지우지 않고 새 컬럼을 반영하기 위해 ADD COLUMN IF NOT EXISTS를 쓴다.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

_STATEMENTS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS rank TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS contact TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS site_id BIGINT REFERENCES sites(id)",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS reporter_info TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS reporter_name TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS reporter_rank TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS reporter_contact TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS location TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS background TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS situation TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS action_taken TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS damage TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT REFERENCES users(id)",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS assigned_manager_id BIGINT REFERENCES users(id)",
    "ALTER TABLE incident_events ADD COLUMN IF NOT EXISTS actor_user_id BIGINT REFERENCES users(id)",
    """CREATE TABLE IF NOT EXISTS incident_comments (
        id              BIGSERIAL PRIMARY KEY,
        incident_id     BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        author_user_id  BIGINT REFERENCES users(id),
        kind            TEXT NOT NULL DEFAULT 'comment',
        body            TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_incident_comments_incident_id ON incident_comments(incident_id)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_created_at ON incidents(created_at)",
    """CREATE TABLE IF NOT EXISTS incident_attachments (
        id                  BIGSERIAL PRIMARY KEY,
        incident_id         BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        filename            TEXT NOT NULL,
        content_type        TEXT NOT NULL,
        size_bytes          BIGINT NOT NULL,
        data                BYTEA NOT NULL,
        uploaded_by_user_id BIGINT REFERENCES users(id),
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_incident_attachments_incident_id ON incident_attachments(incident_id)",
]


def run_migrations(engine: Engine) -> None:
    with engine.begin() as connection:
        for statement in _STATEMENTS:
            connection.execute(text(statement))
