"""PostgreSQL RAG 적재·인덱스 상태를 변경 없이 점검한다."""

from dataclasses import asdict, dataclass
from typing import Any, Literal

from resource_ai.evidence.models import CorpusType
from resource_ai.rag.retrieval.vector_store import Database

Severity = Literal["ERROR", "WARNING"]


@dataclass(frozen=True)
class CorpusAudit:
    """문서 유형별 현재·비활성 문서와 청크 개수."""

    current_documents: int = 0
    inactive_documents: int = 0
    current_chunks: int = 0
    inactive_chunks: int = 0


@dataclass(frozen=True)
class AuditIssue:
    """운영자가 확인해야 하는 데이터 품질 문제."""

    severity: Severity
    code: str
    count: int
    message: str


@dataclass(frozen=True)
class IndexAuditReport:
    """CLI와 자동화 작업에서 함께 사용하는 감사 결과."""

    corpora: dict[str, CorpusAudit]
    capabilities: dict[str, bool]
    indexes: list[str]
    embedding_dimension: int
    embedding_recipes: dict[str, int]
    issues: list[AuditIssue]

    @property
    def passed(self) -> bool:
        return not any(issue.severity == "ERROR" for issue in self.issues)

    @property
    def totals(self) -> dict[str, int]:
        return {
            "current_documents": sum(item.current_documents for item in self.corpora.values()),
            "inactive_documents": sum(item.inactive_documents for item in self.corpora.values()),
            "current_chunks": sum(item.current_chunks for item in self.corpora.values()),
            "inactive_chunks": sum(item.inactive_chunks for item in self.corpora.values()),
        }

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["passed"] = self.passed
        result["totals"] = self.totals
        return result


def _issue(
    issues: list[AuditIssue], severity: Severity, code: str, count: int, message: str
) -> None:
    if count:
        issues.append(AuditIssue(severity, code, count, message))


def audit_index(database: Database, *, embedding_dimension: int = 384) -> IndexAuditReport:
    """RAG 스키마와 데이터 품질을 읽기 전용 SQL로 점검한다."""

    with database.connect(register_vector_types=False) as connection:
        columns = {
            row[0]
            for row in connection.execute(
                """
                /* audit:columns */
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'legal_chunks'
                """
            ).fetchall()
        }
        indexes = sorted(
            row[0]
            for row in connection.execute(
                """
                /* audit:indexes */
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename IN ('legal_documents', 'legal_chunks')
                """
            ).fetchall()
        )
        stat_rows = connection.execute(
            """
            /* audit:corpus-stats */
            SELECT
                d.document_type,
                count(DISTINCT d.id) FILTER (WHERE d.is_current),
                count(DISTINCT d.id) FILTER (WHERE NOT d.is_current),
                count(c.id) FILTER (WHERE d.is_current),
                count(c.id) FILTER (WHERE NOT d.is_current)
            FROM legal_documents AS d
            LEFT JOIN legal_chunks AS c ON c.document_id = d.id
            GROUP BY d.document_type
            ORDER BY d.document_type
            """
        ).fetchall()
        check_row = connection.execute(
            """
            /* audit:data-quality */
            SELECT
                count(*) FILTER (
                    WHERE d.is_current
                      AND NOT EXISTS (
                          SELECT 1 FROM legal_chunks AS c WHERE c.document_id = d.id
                      )
                ),
                count(*) FILTER (WHERE d.is_current AND btrim(d.source_url) = ''),
                count(*) FILTER (WHERE d.is_current AND btrim(d.title) = ''),
                count(*) FILTER (
                    WHERE d.is_current
                      AND d.document_type = 'COMPANY_DOCUMENT'
                      AND nullif(btrim(d.tenant_id), '') IS NULL
                ),
                count(*) FILTER (
                    WHERE d.is_current
                      AND nullif(d.metadata ->> 'canonical_source_id', '') IS NULL
                ),
                count(*) FILTER (WHERE d.is_current AND nullif(btrim(d.version), '') IS NULL)
            FROM legal_documents AS d
            """
        ).fetchone()
        chunk_check_row = connection.execute(
            """
            /* audit:chunk-quality */
            SELECT
                count(*) FILTER (
                    WHERE c.embedding IS NULL
                       OR vector_dims(c.embedding) <> %s
                       OR vector_norm(c.embedding) = 0
                ),
                count(*) FILTER (WHERE btrim(c.heading) = '' OR btrim(c.content) = ''),
                count(*) FILTER (
                    WHERE nullif(c.metadata ->> 'source_id', '') IS NULL
                       OR nullif(c.metadata ->> 'corpus_type', '') IS NULL
                )
            FROM legal_chunks AS c
            JOIN legal_documents AS d ON d.id = c.document_id
            WHERE d.is_current
            """,
            (embedding_dimension,),
        ).fetchone()
        duplicate_row = connection.execute(
            """
            /* audit:duplicate-current */
            SELECT count(*)
            FROM (
                SELECT
                    metadata ->> 'canonical_source_id' AS canonical_source_id,
                    coalesce(tenant_id, '') AS tenant_key
                FROM legal_documents
                WHERE is_current
                  AND nullif(metadata ->> 'canonical_source_id', '') IS NOT NULL
                GROUP BY metadata ->> 'canonical_source_id', coalesce(tenant_id, '')
                HAVING count(*) > 1
            ) AS duplicate_groups
            """
        ).fetchone()
        recipe_rows = connection.execute(
            """
            /* audit:embedding-recipes */
            SELECT coalesce(metadata ->> 'embedding_recipe', '(missing)'), count(*)
            FROM legal_chunks
            GROUP BY coalesce(metadata ->> 'embedding_recipe', '(missing)')
            ORDER BY 1
            """
        ).fetchall()

    corpora = {corpus.value: CorpusAudit() for corpus in CorpusType}
    for corpus, current_docs, inactive_docs, current_chunks, inactive_chunks in stat_rows:
        corpora[corpus] = CorpusAudit(
            current_documents=current_docs,
            inactive_documents=inactive_docs,
            current_chunks=current_chunks,
            inactive_chunks=inactive_chunks,
        )

    issues: list[AuditIssue] = []
    if check_row is not None:
        _issue(
            issues,
            "ERROR",
            "CURRENT_DOCUMENT_WITHOUT_CHUNKS",
            check_row[0],
            "현재 문서에 검색 청크가 없습니다.",
        )
        _issue(
            issues,
            "ERROR",
            "MISSING_SOURCE_URL",
            check_row[1],
            "현재 문서의 원문 URL이 비어 있습니다.",
        )
        _issue(issues, "ERROR", "MISSING_TITLE", check_row[2], "현재 문서의 제목이 비어 있습니다.")
        _issue(
            issues,
            "ERROR",
            "MISSING_COMPANY_TENANT",
            check_row[3],
            "기업 문서의 tenant_id가 없습니다.",
        )
        _issue(
            issues,
            "WARNING",
            "MISSING_CANONICAL_SOURCE_ID",
            check_row[4],
            "현재 문서의 canonical_source_id가 없습니다.",
        )
        _issue(
            issues, "WARNING", "MISSING_VERSION", check_row[5], "현재 문서의 버전 정보가 없습니다."
        )
    if chunk_check_row is not None:
        _issue(
            issues,
            "ERROR",
            "INVALID_EMBEDDING",
            chunk_check_row[0],
            "임베딩이 없거나 차원 또는 벡터 값이 잘못되었습니다.",
        )
        _issue(
            issues,
            "ERROR",
            "EMPTY_CHUNK",
            chunk_check_row[1],
            "제목 또는 본문이 비어 있는 청크가 있습니다.",
        )
        _issue(
            issues,
            "WARNING",
            "MISSING_CHUNK_METADATA",
            chunk_check_row[2],
            "청크의 출처 또는 코퍼스 메타데이터가 없습니다.",
        )
    if duplicate_row is not None:
        _issue(
            issues,
            "ERROR",
            "DUPLICATE_CURRENT_VERSION",
            duplicate_row[0],
            "같은 원문의 현재 버전이 둘 이상입니다.",
        )

    return IndexAuditReport(
        corpora=corpora,
        capabilities={
            "basic_keyword_search": "search_vector" in columns,
            "context_keyword_search": "context_search_vector" in columns,
            "korean_bm25_search": "korean_search_vector" in columns,
        },
        indexes=indexes,
        embedding_dimension=embedding_dimension,
        embedding_recipes={recipe: count for recipe, count in recipe_rows},
        issues=issues,
    )


def format_audit_report(report: IndexAuditReport) -> str:
    """사람이 터미널에서 빠르게 읽을 수 있는 감사 결과를 만든다."""

    lines = ["RAG 인덱스 감사", ""]
    lines.append("코퍼스별 적재 상태")
    for corpus, stats in report.corpora.items():
        lines.append(
            f"- {corpus}: 현재 문서 {stats.current_documents}, 현재 청크 {stats.current_chunks}, "
            f"비활성 문서 {stats.inactive_documents}, 비활성 청크 {stats.inactive_chunks}"
        )
    totals = report.totals
    lines.extend(
        [
            "",
            (
                f"합계: 현재 문서 {totals['current_documents']}, "
                f"현재 청크 {totals['current_chunks']}, "
                f"비활성 문서 {totals['inactive_documents']}, "
                f"비활성 청크 {totals['inactive_chunks']}"
            ),
            "",
            "검색 기능",
        ]
    )
    capability_names = {
        "basic_keyword_search": "기본 키워드 검색",
        "context_keyword_search": "제목·인용 문맥 검색",
        "korean_bm25_search": "한국어 BM25 검색",
    }
    for key, enabled in report.capabilities.items():
        lines.append(f"- {capability_names[key]}: {'적용' if enabled else '미적용'}")
    lines.append(f"- 임베딩 레시피: {report.embedding_recipes}")
    lines.append(f"- 확인된 인덱스: {len(report.indexes)}개")
    lines.extend(["", "품질 검사"])
    if report.issues:
        lines.extend(
            f"- [{issue.severity}] {issue.code}: {issue.count}건 — {issue.message}"
            for issue in report.issues
        )
    else:
        lines.append("- 문제 없음")
    lines.extend(["", f"결과: {'PASS' if report.passed else 'FAIL'}"])
    return "\n".join(lines)
