"""Injectable OCR boundary. Mock is for tests, never an uploaded document fallback."""

from copy import deepcopy
from typing import Protocol


class OCRAdapter(Protocol):
    def extract(self, content: bytes, media_type: str) -> dict: ...


class MockLayoutOCR:
    def __init__(self, layout):
        self.layout = deepcopy(layout)

    def extract(self, content, media_type):
        return {**deepcopy(self.layout), "provider": "synthetic-mock-layout", "synthetic": True}
