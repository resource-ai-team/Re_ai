from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://re_ai:local_password@127.0.0.1:5432/re_ai"
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    embedding_provider: str = "sentence-transformers"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dimension: int = 384
    search_top_k: int = 5
    hybrid_candidate_multiplier: int = 4
    hybrid_rrf_k: int = 60
    hybrid_candidate_minimum: int = Field(default=0, ge=0, le=100)
    retrieval_article_diversity: bool = False
    reranker_provider: Literal["rules", "onnx-bge", "onnx-minilm"] = "rules"
    reranker_model_directory: str = ""
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
    legal_graph_model_provider: Literal["azure", "solar"] = "azure"
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = "gpt-6-astra"
    azure_openai_deployment_model: Literal["", "gpt-6-astra"] = ""
    azure_openai_reasoning_effort: Literal["high"] = "high"
    azure_openai_max_output_tokens: int = Field(default=16384, ge=1024, le=32768)
    azure_openai_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    upstage_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    road_routing_enabled: bool = False
    road_routing_allow_external: bool = False
    road_routing_provider: Literal["none", "kakao", "naver"] = "none"
    road_routing_timeout_seconds: float = Field(default=5, ge=1, le=15)
    road_routing_total_timeout_seconds: float = Field(default=20, ge=1, le=60)
    road_routing_max_calls: int = Field(default=10, ge=1, le=20)
    road_routing_service_token: SecretStr = Field(default=SecretStr(""), repr=False)
    kakao_mobility_rest_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    naver_maps_api_key_id: SecretStr = Field(default=SecretStr(""), repr=False)
    naver_maps_api_key: SecretStr = Field(default=SecretStr(""), repr=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
