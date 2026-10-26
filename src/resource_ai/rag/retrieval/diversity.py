"""Keep one ranked hit per exact law article; full context is expanded afterwards."""

import re


def distinct_articles(results, limit):
    if limit <= 0:
        raise ValueError("limit must be positive")
    output, seen = [], set()
    for hit in results:
        article = re.search(r"제\s*\d+\s*조(?:\s*의\s*\d+)?", hit.citation or "")
        annex = re.search(r"별표\s*\d+(?:\s*의\s*\d+)?", hit.citation or "")
        key = (
            (
                hit.title,
                hit.version,
                hit.source_url,
                re.sub(r"\s+", "", (annex or article).group())
                if (annex or article)
                else hit.heading,
            )
            if hit.document_type == "LAW"
            else ("chunk", hit.chunk_id)
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(hit)
        if len(output) == limit:
            break
    return output
