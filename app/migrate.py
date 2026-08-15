"""기동 시 실행하는 멱등 스키마 보정.

Base.metadata.create_all()은 '없는 테이블'만 만들 뿐 기존 테이블에 컬럼을 추가하지 못한다.
이미 데이터가 쌓인 DB를 지우지 않고 새 컬럼을 반영하기 위해 ADD COLUMN IF NOT EXISTS를 쓴다.

마이그레이션은 2단계로 나뉜다. 사내 조직(부서/사업장) 기반이던 사건을 전국 행정구역 기반으로
옮기려면 `regions` 테이블이 먼저 채워져 있어야 하는데, 지역 시드는 지도 경계 파일에서
파이썬으로 생성되기 때문이다(app/regions_seed.py). 그래서:

  1) run_migrations()   - 스키마만 준비 (새 컬럼/테이블 추가, 옛 NOT NULL 완화)
  2) [지역 시드 실행]
  3) finalize_migration() - 옛 사업장 데이터를 지역으로 이관하고 구 컬럼/테이블 제거
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

# 직급(자유 텍스트) -> 직종(선택형) 전환. 기존 컬럼이 있으면 이름만 바꿔 데이터를 보존하고,
# 없으면(신규 DB) 아래 ADD COLUMN IF NOT EXISTS가 새로 만든다.
_RENAME_RANK_TO_OCCUPATION = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'rank')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'occupation')
    THEN
        ALTER TABLE users RENAME COLUMN rank TO occupation;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'incidents' AND column_name = 'reporter_rank')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'incidents' AND column_name = 'reporter_occupation')
    THEN
        ALTER TABLE incidents RENAME COLUMN reporter_rank TO reporter_occupation;
    END IF;
END $$;
"""

_STATEMENTS = [
    _RENAME_RANK_TO_OCCUPATION,
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS occupation TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS contact TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS reporter_info TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS reporter_name TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS reporter_occupation TEXT",
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
    # --- 전국 행정구역 전환 (1단계: 스키마만) ---
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS sido_code TEXT REFERENCES regions(code)",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS sigungu_code TEXT REFERENCES regions(code)",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS category_id BIGINT REFERENCES incident_categories(id)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS sido_code TEXT REFERENCES regions(code)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS sigungu_code TEXT REFERENCES regions(code)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_sido_code ON incidents(sido_code)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_sigungu_code ON incidents(sigungu_code)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_category_id ON incidents(category_id)",
    # 구 컬럼은 아직 지우지 않는다(2단계에서 이관 후 제거). 그 사이 INSERT가 막히지 않도록
    # NOT NULL만 먼저 푼다. ALTER COLUMN은 IF EXISTS를 지원하지 않아(신규 DB에는 이 컬럼이
    # 아예 없다) 존재 여부를 직접 확인한다.
    # --- 대량 법령 수집을 위한 이어달리기 표시 ---
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS content_hash TEXT",
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS graph_synced_at TIMESTAMPTZ",
    # --- 법령 분야(Domain) 계층 ---
    """CREATE TABLE IF NOT EXISTS law_categories (
        id    BIGSERIAL PRIMARY KEY,
        code  TEXT NOT NULL UNIQUE,
        name  TEXT NOT NULL
    )""",
    "ALTER TABLE laws ADD COLUMN IF NOT EXISTS department TEXT",
    "ALTER TABLE laws ADD COLUMN IF NOT EXISTS category_id BIGINT REFERENCES law_categories(id)",
    "CREATE INDEX IF NOT EXISTS idx_laws_category_id ON laws(category_id)",
    # --- 증분 동기화(개정/폐지분만 처리)를 위한 컬럼 ---
    "ALTER TABLE laws ADD COLUMN IF NOT EXISTS mst TEXT",
    "ALTER TABLE laws ADD COLUMN IF NOT EXISTS repealed_at TIMESTAMPTZ",
    """DO $$
    DECLARE col TEXT;
    BEGIN
        FOREACH col IN ARRAY ARRAY['department_id', 'site_id'] LOOP
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'incidents' AND column_name = col
            ) THEN
                EXECUTE format('ALTER TABLE incidents ALTER COLUMN %I DROP NOT NULL', col);
            END IF;
        END LOOP;
    END $$;""",
]

# 옛 사업장명 -> 통계청 행정구역 코드. 데모 데이터를 유실 없이 지역 기반으로 옮기기 위한 표.
_SITE_TO_REGION = [
    ("포항사업장", "37011"),  # 경상북도 포항시남구
    ("광양사업장", "36060"),  # 전라남도 광양시
    ("구미사업장", "37050"),  # 경상북도 구미시
    ("세종사업장", "29010"),  # 세종특별자치시 세종시
]

# 2단계. 구 컬럼이 남아 있을 때만 이관하고, 끝나면 구 컬럼/테이블을 제거한다.
# DO 블록으로 감싸 두 번째 실행부터는 통째로 건너뛰므로 멱등하다.
_FINALIZE = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'incidents' AND column_name = 'site_id'
    ) THEN
        UPDATE incidents i
        SET sigungu_code = m.code,
            sido_code    = left(m.code, 2)
        FROM sites s, (VALUES %s) AS m(site_name, code)
        WHERE i.site_id = s.id
          AND s.name = m.site_name
          AND i.sigungu_code IS NULL;

        UPDATE users u
        SET sigungu_code = m.code,
            sido_code    = left(m.code, 2)
        FROM sites s, (VALUES %s) AS m(site_name, code)
        WHERE u.site_id = s.id
          AND s.name = m.site_name
          AND u.sigungu_code IS NULL;
    END IF;
END $$;
"""

_CLEANUP = [
    "ALTER TABLE incidents DROP COLUMN IF EXISTS department_id",
    "ALTER TABLE incidents DROP COLUMN IF EXISTS site_id",
    "ALTER TABLE users DROP COLUMN IF EXISTS department_id",
    "ALTER TABLE users DROP COLUMN IF EXISTS site_id",
    "DROP TABLE IF EXISTS departments",
    "DROP TABLE IF EXISTS sites",
]


def run_migrations(engine: Engine) -> None:
    """1단계: 스키마 준비. 지역 시드보다 먼저 실행된다."""
    with engine.begin() as connection:
        for statement in _STATEMENTS:
            connection.execute(text(statement))


def finalize_migration(engine: Engine) -> None:
    """2단계: 사업장->지역 이관 후 구 스키마 제거. 지역 시드가 끝난 뒤 실행해야 한다."""
    values = ", ".join(f"('{name}', '{code}')" for name, code in _SITE_TO_REGION)
    with engine.begin() as connection:
        connection.execute(text(_FINALIZE % (values, values)))
        for statement in _CLEANUP:
            connection.execute(text(statement))
