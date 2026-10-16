from functools import lru_cache

from app.config import get_settings
from app.database import Database
from app.embeddings import create_embedding_provider
from app.repository import LegalRepository


@lru_cache
def get_repository() -> LegalRepository:
    settings = get_settings()
    embeddings = create_embedding_provider(
        settings.embedding_provider,
        settings.embedding_model,
        settings.embedding_dimension,
    )
    return LegalRepository(Database(settings.database_url), embeddings)

