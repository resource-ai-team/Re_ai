from dataclasses import dataclass

from resource_ai.evidence.models import CorpusType


@dataclass(frozen=True)
class TextChunk:
    content: str
    index: int


@dataclass(frozen=True)
class ChunkPolicy:
    max_chars: int
    overlap_chars: int


CORPUS_CHUNK_POLICIES = {
    CorpusType.LAW: ChunkPolicy(1200, 150),
    CorpusType.PRECEDENT: ChunkPolicy(1400, 180),
    CorpusType.TECHNICAL_LITERATURE: ChunkPolicy(1600, 200),
    CorpusType.HISTORICAL_CASE: ChunkPolicy(1000, 120),
    CorpusType.COMPANY_DOCUMENT: ChunkPolicy(1200, 150),
}


def chunk_for_corpus(text: str, corpus_type: str) -> list[TextChunk]:
    try:
        policy = CORPUS_CHUNK_POLICIES[CorpusType(corpus_type)]
    except ValueError as exc:
        raise ValueError(f"Unsupported corpus type: {corpus_type}") from exc
    return chunk_text(text, policy.max_chars, policy.overlap_chars)


def chunk_text(text: str, max_chars: int = 1200, overlap_chars: int = 150) -> list[TextChunk]:
    """Split a document section while preferring paragraph boundaries."""
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not normalized:
        return []
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be between 0 and max_chars")
    if len(normalized) <= max_chars:
        return [TextChunk(content=normalized, index=0)]

    paragraphs = normalized.split("\n")
    chunks: list[TextChunk] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(TextChunk(current, len(chunks)))
                current = ""
            start = 0
            while start < len(paragraph):
                end = min(start + max_chars, len(paragraph))
                chunks.append(TextChunk(paragraph[start:end], len(chunks)))
                if end == len(paragraph):
                    break
                start = end - overlap_chars
            continue

        candidate = paragraph if not current else f"{current}\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
            continue

        chunks.append(TextChunk(current, len(chunks)))
        overlap = current[-overlap_chars:] if overlap_chars else ""
        current = f"{overlap}\n{paragraph}" if overlap else paragraph

    if current:
        chunks.append(TextChunk(current, len(chunks)))
    return chunks
