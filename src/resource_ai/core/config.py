from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://re_ai:local_password@localhost:5432/re_ai"
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    embedding_provider: str = "sentence-transformers"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dimension: int = 384
    search_top_k: int = 5
    hybrid_candidate_multiplier: int = 4
    hybrid_rrf_k: int = 60
    law_api_oc: str = "test"
    law_api_base_url: str = "https://www.law.go.kr/DRF"
    law_api_timeout_seconds: float = 30.0
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: SecretStr = Field(default=SecretStr(""), repr=False)
    neo4j_database: str = "neo4j"
    neo4j_namespace: str = "re-ai-reference"
    legal_graph_namespace: str = "re-ai-legal"
    legal_graph_model_enabled: bool = False
    legal_graph_allow_external: bool = False
    legal_graph_model: str = "solar-pro4"
    legal_graph_timeout_seconds: int = Field(default=60, ge=5, le=180)
    upstage_api_key: SecretStr = Field(default=SecretStr(""), repr=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
