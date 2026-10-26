"""Local Korean noun/compound retrieval. No query is sent to a hosted model."""

from __future__ import annotations

import re

RECIPE = "kiwi-0.24.0-cong-legal-nouns-compounds-v1"
SCORING = "fielded-bm25-k1-1.2-b-0.75-v1"
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


def index_coverage(connection, recipe=RECIPE):
    """Read-only public index readiness; an empty corpus is not ready."""
    rows = connection.execute(
        """
        SELECT d.document_type,count(c.id),count(c.id) FILTER (
            WHERE c.korean_search_vector IS NOT NULL
              AND c.metadata->>'korean_lexical_recipe'=%s)
        FROM legal_documents d JOIN legal_chunks c ON c.document_id=d.id
        WHERE d.is_current AND d.tenant_id IS NULL
          AND d.document_type IN ('LAW','PRECEDENT')
          AND (d.document_type<>'LAW' OR d.effective_from<=CURRENT_DATE)
        GROUP BY d.document_type ORDER BY d.document_type
        """,
        (recipe,),
    ).fetchall()
    total = sum(row[1] for row in rows)
    indexed = sum(row[2] for row in rows)
    return {
        "eligible_chunks": total,
        "indexed_chunks": indexed,
        "missing_chunks": total - indexed,
        "complete": total > 0 and total == indexed,
        "by_corpus": {
            kind: {"eligible": count, "indexed": matched} for kind, count, matched in rows
        },
    }


def lexical_search(connection, query, tokenizer, limit, corpus_types):
    """Fielded BM25 over public, current, recipe-compatible legal chunks only.

    IDF and length statistics are computed in the same statement as the matches,
    so edits, expiry and corpus filters cannot leave a stale statistics cache.
    Length is distinct indexed lexemes, not raw Korean syllables. The existing
    index deduplicates terms per field; TF is consequently field presence with
    title=2, heading/citation=3, body=1, rather than raw occurrence counts.
    No query text or company document enters a persistent side index.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Korean lexical limit must be an integer between 1 and 100")
    if not corpus_types or not set(corpus_types) <= {"LAW", "PRECEDENT"}:
        raise ValueError("Korean lexical search requires an explicit public legal corpus")
    tsquery = tokenizer.query(query)
    if not tsquery:
        return []
    terms = tsquery.split(" | ")
    return connection.execute(
        """
        WITH eligible AS MATERIALIZED (
            SELECT c.id,c.document_id,c.korean_search_vector AS sv
            FROM legal_chunks c JOIN legal_documents d ON d.id=c.document_id
            WHERE d.is_current AND d.tenant_id IS NULL
              AND d.document_type=ANY(%s::text[])
              AND (d.document_type<>'LAW' OR d.effective_from<=CURRENT_DATE)
              AND c.metadata->>'korean_lexical_recipe'=%s
              AND c.korean_search_vector IS NOT NULL
        ), corpus AS (
            SELECT count(*)::float8 AS n,avg(length(sv)) AS avgdl FROM eligible
        ), matching AS MATERIALIZED (
            SELECT * FROM eligible WHERE sv @@ to_tsquery('simple',%s)
        ), postings AS MATERIALIZED (
            SELECT m.id,length(m.sv) AS dl,x.lexeme,
                2.0*cardinality(array_positions(x.weights,'A'::text)) +
                3.0*cardinality(array_positions(x.weights,'B'::text)) +
                    cardinality(array_positions(x.weights,'C'::text)) +
                    cardinality(array_positions(x.weights,'D'::text)) AS tf,
                count(*) OVER (PARTITION BY x.lexeme) AS df
            FROM matching m CROSS JOIN LATERAL unnest(m.sv) x
            WHERE x.lexeme=ANY(%s::text[])
        ), scores AS (
            SELECT p.id,sum(
                ln(1+(n-df+0.5)/(df+0.5)) *
                (tf*2.2)/(tf+1.2*(0.25+0.75*dl/NULLIF(avgdl,0)))
            ) AS score
            FROM postings p CROSS JOIN corpus GROUP BY p.id
        )
        SELECT c.id,d.title,d.document_type,c.heading,c.content,c.citation,d.source_url,
               d.effective_from,s.score,
               d.version,c.page_no,COALESCE(c.citation,c.metadata->>'source_position'),
               jsonb_build_object('document',d.metadata,'chunk',c.metadata)
        FROM scores s JOIN legal_chunks c ON c.id=s.id
        JOIN legal_documents d ON d.id=c.document_id
        ORDER BY s.score DESC,c.id
        LIMIT %s
        """,
        (list(corpus_types), tokenizer.recipe, tsquery, terms, limit),
    ).fetchall()
