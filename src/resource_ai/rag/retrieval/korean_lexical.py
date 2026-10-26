"""Local Korean noun/compound retrieval. No query is sent to a hosted model."""

from __future__ import annotations

import re

RECIPE = "kiwi-0.24.0-cong-legal-nouns-compounds-v1"
_SAFE = re.compile(r"^[a-z0-9가-힣]+$")
_STOP = frozenset(
    {"무엇", "어디", "언제", "누구", "어떤", "질문", "답변", "설명", "등", "것", "수", "때"}
)


class KoreanLegalTokenizer:
    recipe = RECIPE

    def __init__(self):
        import kiwipiepy

        if kiwipiepy.__version__ != "0.24.0":
            raise RuntimeError(
                "Korean lexical index requires kiwipiepy==0.24.0; rebuild on version changes"
            )
        self.kiwi = kiwipiepy.Kiwi(num_workers=1, model_type="cong")

    def terms(self, text: str) -> tuple[str, ...]:
        if len(text) > 12000:
            raise ValueError("Korean lexical input exceeds local tokenization budget")
        tokens = self.kiwi.tokenize(text)
        terms, run = [], []
        for token in tokens:
            noun = token.tag.startswith("NN") or token.tag in {"SL", "SN"}
            suffix = token.tag == "XSN"
            if not (noun or suffix) or (run and run[-1].start + run[-1].len != token.start):
                run = []
            if not (noun or suffix):
                continue
            form = token.form.lower()
            if noun and len(form) >= 2 and form not in _STOP and _SAFE.fullmatch(form):
                terms.append(form)
            run.append(token)
            for count in (2, 3):
                if len(run) < count:
                    continue
                joined = "".join(t.form.lower() for t in run[-count:])
                if 2 <= len(joined) <= 30 and joined not in _STOP and _SAFE.fullmatch(joined):
                    terms.append(joined)
        return tuple(dict.fromkeys(terms))

    def text(self, value: str) -> str:
        return " ".join(self.terms(value))

    def query(self, value: str) -> str:
        # Terms are local analyzed forms, never user-supplied tsquery operators.
        return " | ".join(self.terms(value)[:32])


def lexical_search(connection, query, tokenizer, limit, corpus_types):
    tsquery = tokenizer.query(query)
    if not tsquery:
        return []
    return connection.execute(
        """
        SELECT c.id,d.title,d.document_type,c.heading,c.content,c.citation,d.source_url,
               d.effective_from,ts_rank_cd(c.korean_search_vector,to_tsquery('simple',%s),32),
               d.version,c.page_no,COALESCE(c.citation,c.metadata->>'source_position'),
               jsonb_build_object('document',d.metadata,'chunk',c.metadata)
        FROM legal_chunks c JOIN legal_documents d ON d.id=c.document_id
        WHERE d.is_current AND d.tenant_id IS NULL AND d.document_type=ANY(%s::text[])
          AND (d.document_type<>'LAW' OR d.effective_from<=CURRENT_DATE)
          AND c.metadata->>'korean_lexical_recipe'=%s
          AND c.korean_search_vector @@ to_tsquery('simple',%s)
        ORDER BY ts_rank_cd(c.korean_search_vector,to_tsquery('simple',%s),32) DESC,c.id
        LIMIT %s
        """,
        (tsquery, list(corpus_types), tokenizer.recipe, tsquery, tsquery, limit),
    ).fetchall()
