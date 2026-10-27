from contextlib import nullcontext

from resource_ai.rag.audit import audit_index, format_audit_report


class Result:
    def __init__(self, *, rows=None, row=None):
        self.rows = rows or []
        self.row = row

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


class AuditConnection:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))
        marker = query.split("/* audit:", 1)[1].split(" */", 1)[0]
        return self.results[marker]


class AuditDatabase:
    def __init__(self, results):
        self.connection = AuditConnection(results)
        self.register_vector_types = None

    def connect(self, *, register_vector_types=True):
        self.register_vector_types = register_vector_types
        return nullcontext(self.connection)


def _results(*, invalid_embeddings=0, missing_urls=0):
    return {
        "columns": Result(rows=[("search_vector",), ("context_search_vector",)]),
        "indexes": Result(rows=[("legal_chunks_embedding_hnsw_idx",)]),
        "corpus-stats": Result(
            rows=[
                ("LAW", 2, 1, 10, 3),
                ("TECHNICAL_LITERATURE", 1, 0, 2, 0),
            ]
        ),
        "data-quality": Result(row=(0, missing_urls, 0, 0, 0, 1)),
        "chunk-quality": Result(row=(invalid_embeddings, 0, 0)),
        "duplicate-current": Result(row=(0,)),
        "embedding-recipes": Result(rows=[("(missing)", 3), ("document-heading-content-v1", 12)]),
    }


def test_audit_reports_counts_capabilities_and_warnings() -> None:
    database = AuditDatabase(_results())

    report = audit_index(database)

    assert database.register_vector_types is False
    assert report.passed is True
    assert report.totals == {
        "current_documents": 3,
        "inactive_documents": 1,
        "current_chunks": 12,
        "inactive_chunks": 3,
    }
    assert report.corpora["COMPANY_DOCUMENT"].current_documents == 0
    assert report.capabilities == {
        "basic_keyword_search": True,
        "context_keyword_search": True,
        "korean_bm25_search": False,
    }
    assert [issue.code for issue in report.issues] == ["MISSING_VERSION"]
    assert report.to_dict()["passed"] is True


def test_audit_fails_for_critical_data_errors() -> None:
    report = audit_index(AuditDatabase(_results(invalid_embeddings=2, missing_urls=1)))

    assert report.passed is False
    assert [(issue.code, issue.count) for issue in report.issues] == [
        ("MISSING_SOURCE_URL", 1),
        ("MISSING_VERSION", 1),
        ("INVALID_EMBEDDING", 2),
    ]


def test_human_report_distinguishes_unavailable_pipeline_features() -> None:
    report = audit_index(AuditDatabase(_results()))

    output = format_audit_report(report)

    assert "제목·인용 문맥 검색: 적용" in output
    assert "한국어 BM25 검색: 미적용" in output
    assert "결과: PASS" in output
