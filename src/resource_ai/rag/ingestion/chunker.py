from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    content: str
    index: int


def chunk_text(text: str, max_chars: int = 1200, overlap_chars: int = 150) -> list[TextChunk]:
    """Split long legal sections while preferring paragraph boundaries."""
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
