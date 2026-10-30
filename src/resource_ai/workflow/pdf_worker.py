"""PDF parsing in an isolated, time-bounded child; no network/model tools."""

import json
import sys

from resource_ai.workflow.documents import _pdf_text

if __name__ == "__main__":
    content = sys.stdin.buffer.read(6_000_001)
    if len(content) > 6_000_000:
        raise SystemExit(1)
    result = _pdf_text(content)
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
