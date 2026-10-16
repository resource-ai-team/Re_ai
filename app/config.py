from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://re_ai:local_password@localhost:5432/re_ai"
    embedding_provider: str = "sentence-transformers"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dimension: int = 384
    search_top_k: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()

