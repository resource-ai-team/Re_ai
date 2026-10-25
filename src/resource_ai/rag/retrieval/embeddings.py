"""운영용 의미 임베딩과 테스트용 결정론적 임베딩 구현."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol


class EmbeddingProvider(Protocol):
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashEmbeddingProvider:
    """오프라인 테스트 전용 구현이며 의미 검색 품질을 검증하는 용도가 아니다."""

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension
        self.model_name = f"test-only-hash-{dimension}"

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = text.lower().split()
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class SentenceTransformerEmbeddingProvider:
    """문서와 질의 접두어를 구분해 다국어 E5 계열 임베딩을 생성한다."""

    def __init__(self, model_name: str, dimension: int) -> None:
        self.model_name = model_name
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Install local embeddings with: pip install -e '.[embeddings]'"
            ) from exc
        self.model = SentenceTransformer(model_name)
        self.dimension = dimension
        if hasattr(self.model, "get_embedding_dimension"):
            actual_dimension = self.model.get_embedding_dimension()
        else:
            actual_dimension = self.model.get_sentence_embedding_dimension()
        if actual_dimension != dimension:
            raise ValueError(
                f"Model dimension {actual_dimension} does not match EMBEDDING_DIMENSION={dimension}"
            )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        prefixed = [f"passage: {text}" for text in texts]
        vectors = self.model.encode(prefixed, normalize_embeddings=True)
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        vector = self.model.encode(f"query: {text}", normalize_embeddings=True)
        return vector.tolist()


def create_embedding_provider(provider: str, model: str, dimension: int) -> EmbeddingProvider:
    if provider == "hash":
        return HashEmbeddingProvider(dimension)
    if provider == "sentence-transformers":
        return SentenceTransformerEmbeddingProvider(model, dimension)
    raise ValueError(f"Unsupported embedding provider: {provider}")
