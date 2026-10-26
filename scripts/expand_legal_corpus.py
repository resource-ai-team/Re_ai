"""공식 법령 수집 → 팀 E5/pgvector 적재. 외부 모델 호출 없음."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from resource_ai.core.config import get_settings
from resource_ai.core.dependencies import get_repository
from resource_ai.integrations.law_api import LawOpenApiClient
from resource_ai.rag.ingestion.dataset import (
    validate_documents,
    verify_dataset_manifest,
    write_dataset_manifest,
)
from resource_ai.rag.ingestion.legal_attachments import attach_test_standard
from resource_ai.rag.ingestion.legal_expansion import CachedOfficialCollector, content_fingerprint
from resource_ai.rag.ingestion.loader import load_jsonl, write_jsonl


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collect(args):
    scope = json.loads(args.scope.read_text(encoding="utf-8"))
    as_of = date.fromisoformat(args.as_of)
    records, documents = [], []
    settings = get_settings()
    with LawOpenApiClient(
        settings.law_api_oc, settings.law_api_base_url, settings.law_api_timeout_seconds
    ) as client:
        collector = CachedOfficialCollector(client, args.output.parent / "xml", as_of)
        for spec in scope["sources"]:
            try:
                document = collector.collect(
                    spec["title"], spec["kind"], spec["topic"], scope["dataset_version"]
                )
                document = attach_test_standard(document, args.output.parent / "attachments")
                documents.append(document)
                records.append(
                    {
                        "title": document.title,
                        "source_id": document.source_id,
                        "source_url": document.source_url,
                        "effective_from": str(document.effective_from),
                        "kind": spec["kind"],
                        "topic": spec["topic"],
                        "status": "collected",
                        "sections": len(document.sections),
                        "annex_text_missing": document.metadata["annex_text_missing"],
                        "raw_xml_sha256": document.metadata["raw_xml_sha256"],
                    }
                )
                print(f"COLLECTED {document.title}: {len(document.sections)} sections", flush=True)
            except (ValueError, OSError) as exc:
                records.append(
                    {
                        "title": spec["title"],
                        "kind": spec["kind"],
                        "topic": spec["topic"],
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                print(f"FAILED {spec['title']}: {exc}", flush=True)
            # 중단해도 성공한 문서와 실패 목록을 보존한다.
            write_jsonl(args.output, documents)
            save(
                args.output.with_suffix(".collection.json"),
                {
                    "as_of": args.as_of,
                    "dataset_version": scope["dataset_version"],
                    "sources": records,
                    "validation": {"errors": validate_documents(documents).errors},
                },
            )
    if documents:
        write_dataset_manifest(
            args.output.with_suffix(".manifest.json"),
            documents=documents,
            scope_path=args.scope,
            dataset_path=args.output,
        )
    print(json.dumps({"collected": len(documents), "failed": len(records) - len(documents)}))
    return 1 if any(r["status"] == "failed" for r in records) else 0


def index(args):
    # 팀 인덱스와 다른 모델·차원·테스트 해시 임베딩을 섞지 않는다.
    settings = get_settings()
    if (
        settings.embedding_provider != "sentence-transformers"
        or settings.embedding_model != "intfloat/multilingual-e5-small"
        or settings.embedding_dimension != 384
    ):
        raise ValueError("This corpus must use the team's multilingual-e5-small / 384 recipe")
    verify_dataset_manifest(args.output.with_suffix(".manifest.json"), args.output)
    documents = load_jsonl(args.output)
    errors = validate_documents(documents).errors
    if errors:
        raise ValueError("; ".join(errors))
    as_of = date.fromisoformat(args.as_of)
    for doc in documents:
        if (
            doc.document_type != "LAW"
            or doc.tenant_id is not None
            or doc.effective_from is None
            or doc.effective_from > as_of
            or doc.metadata.get("as_of") != args.as_of
        ):
            raise ValueError("Only validated public effective LAW documents may be indexed")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    repository = get_repository()
    rows = []
    for doc in documents:
        fingerprint = content_fingerprint(doc)
        with repository.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT d.metadata->>'content_fingerprint', count(c.id),
                       bool_and(c.metadata->>'embedding_recipe' = 'document-heading-content-v1'),
                       d.metadata->>'embedding_model', min(vector_dims(c.embedding))
                FROM legal_documents d LEFT JOIN legal_chunks c ON c.document_id=d.id
                WHERE d.source_id=%s AND d.is_current AND d.tenant_id IS NULL
                GROUP BY d.id
            """,
                (doc.source_id,),
            ).fetchone()
        if (
            existing
            and existing[0] == fingerprint
            and existing[1]
            and existing[2]
            and existing[3] == settings.embedding_model
            and existing[4] == 384
        ):
            count, status = existing[1], "unchanged"
            # 원문 벡터는 재사용하되 재수집 시각·출처 해시·권리 메타데이터는 갱신한다.
            with repository.database.connect() as connection:
                connection.execute(
                    """UPDATE legal_documents SET metadata=metadata || %s::jsonb, updated_at=now()
                       WHERE source_id=%s AND is_current AND tenant_id IS NULL
                         AND document_type='LAW'""",
                    (json.dumps(doc.metadata, ensure_ascii=False), doc.source_id),
                )
        else:
            doc = replace(
                doc,
                metadata={
                    **doc.metadata,
                    "content_fingerprint": fingerprint,
                    "embedding_model": settings.embedding_model,
                    "embedding_dimension": 384,
                },
            )
            # 문서마다 원자적으로 교체하고 이전 버전·다른 코퍼스는 보존한다.
            count, status = repository.ingest(doc), "indexed"
        rows.append(
            {
                "source_id": doc.source_id,
                "title": doc.title,
                "chunks": count,
                "status": status,
                "content_fingerprint": fingerprint,
            }
        )
        save(
            args.output.with_suffix(".index.json"),
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "as_of": args.as_of,
                "storage": "local PostgreSQL / pgvector",
                "embedding_model": settings.embedding_model,
                "dimension": 384,
                "recipe": "document-heading-content-v1",
                "sources": rows,
            },
        )
        print(f"{status.upper()} {doc.title}: {count} chunks", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["collect", "index"])
    parser.add_argument("--scope", type=Path, default=Path("data/config/legal_scope_expanded.json"))
    parser.add_argument("--as-of", default="2026-10-26")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/official/legal-expansion-2026-10-26/documents.jsonl"),
    )
    args = parser.parse_args()
    return collect(args) if args.action == "collect" else index(args)


if __name__ == "__main__":
    raise SystemExit(main())
