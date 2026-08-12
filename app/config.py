from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "safetylaw"
    postgres_password: str = "change_me"
    postgres_db: str = "safetylaw"
    postgres_host: str = "db"
    postgres_port: int = 5432

    law_oc_key: str = ""

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_dim: int = 1024

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
