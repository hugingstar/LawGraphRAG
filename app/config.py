from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "lawowly"
    postgres_password: str = "change_me"
    postgres_db: str = "lawowly"
    postgres_host: str = "db"
    postgres_port: int = 5432

    law_oc_key: str = ""

    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-lite-latest"

    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_dim: int = 1024

    # 후보 재순위(app/rerank.py). 로컬 GPU 모델이라 API 쿼터와 무관하지만 첫 실행 시
    # 모델을 내려받고(약 2GB) GPU 메모리를 더 쓰므로, 끄고 싶으면 RERANK_ENABLED=false.
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_enabled: bool = True

    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "change_me"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
