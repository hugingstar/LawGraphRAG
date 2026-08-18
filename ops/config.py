from pydantic_settings import BaseSettings, SettingsConfigDict


class OpsSettings(BaseSettings):
    """.env 이 아니라 .env.ops 를 읽는다. 앱 설정과 파일 단위로 분리해 두어야
    앱 쪽 비밀값을 바꿔도 모니터링이 같이 흔들리지 않는다."""

    model_config = SettingsConfigDict(env_file=".env.ops", extra="ignore")

    # --- 감시 대상: 웹 ---
    # api/db/neo4j 는 127.0.0.1 에만 묶여 있어 호스트가 공개한 포트로는 더 이상 닿지
    # 않는다(README "포트 정책" 참고). 대신 이 서비스가 app 네트워크(docker-compose.ops.yml
    # 의 external network)에 직접 붙어 컨테이너 이름으로 본다 — 포트도 컨테이너 내부
    # 포트(8000/5432/7687)이지 호스트에 매핑된 포트(5433 등)가 아니다.
    app_url: str = "http://api:8000"
    app_health_path: str = "/"
    app_timeout_seconds: float = 5.0

    # --- 감시 대상: Postgres ---
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_db: str = "lawowly"
    postgres_user: str = "lawowly"
    postgres_password: str = "change_me"
    postgres_timeout_seconds: int = 5

    # --- 감시 대상: Neo4j ---
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "change_me"
    neo4j_timeout_seconds: float = 5.0

    # --- 감시 대상: Docker ---
    docker_enabled: bool = True
    # 쉼표로 구분한 이름 조각과 부분 일치하는 컨테이너만 본다. 비우면 전체인데,
    # 그러면 호스트에 남아 있는 무관한 exited 컨테이너까지 잡혀 카드가 상시
    # 빨간색이 된다. 기본값은 이 프로젝트 스택으로 좁혀 둔다.
    docker_name_filter: str = "lawgraphrag"

    # --- 앱 활동/트래픽 ---
    # 액세스 로그를 읽어올 앱 컨테이너 이름. docker_enabled=false 면 수집하지 않는다.
    app_container_name: str = "lawgraphrag-api-1"

    # --- 수집 주기·보관 ---
    poll_interval_seconds: int = 30
    history_path: str = "/data/ops_history.sqlite3"
    history_retention_days: int = 7

    # --- 3D 그래프 ---
    # 한 번에 브라우저로 보내는 노드 상한. WebGL 이라도 수만 노드는 상호작용이 죽는다.
    graph_max_nodes: int = 1500

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def docker_name_filters(self) -> list[str]:
        return [part.strip() for part in self.docker_name_filter.split(",") if part.strip()]


ops_settings = OpsSettings()
