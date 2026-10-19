import argparse
from pathlib import Path

from resource_ai.core.config import get_settings
from resource_ai.core.dependencies import get_repository
from resource_ai.integrations.law_api import LawOpenApiClient
from resource_ai.rag.evaluation import (
    evaluate_case,
    load_retrieval_cases,
    summarize_evaluations,
)
from resource_ai.rag.ingestion.dataset import validate_documents, write_dataset_manifest
from resource_ai.rag.ingestion.loader import load_jsonl, write_jsonl
from resource_ai.rag.ingestion.pipeline import collect_scope


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local legal RAG utilities")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Create pgvector tables")

    ingest = commands.add_parser("ingest", help="Ingest legal JSONL documents")
    ingest.add_argument("path", type=Path)

    search = commands.add_parser("search", help="Search legal evidence")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=5)

    collect = commands.add_parser("collect", help="Collect official Korean legal sources")
    collect.add_argument("--scope", type=Path, default=Path("data/config/legal_scope.json"))
    collect.add_argument("--output", type=Path, default=Path("data/official/legal_documents.jsonl"))

    validate = commands.add_parser("validate", help="Validate an official legal dataset")
    validate.add_argument(
        "path", type=Path, nargs="?", default=Path("data/official/legal_documents.jsonl")
    )

    evaluate = commands.add_parser("evaluate", help="Evaluate retrieval against expected evidence")
    evaluate.add_argument(
        "--cases", type=Path, default=Path("data/config/retrieval_eval.json")
    )
    evaluate.add_argument("--top-k", type=int, default=5)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "collect":
        settings = get_settings()
        with LawOpenApiClient(
            settings.law_api_oc,
            settings.law_api_base_url,
            settings.law_api_timeout_seconds,
        ) as client:
            documents = collect_scope(client, args.scope)
        validation = validate_documents(documents)
        if not validation.passed:
            for error in validation.errors:
                print(f"ERROR: {error}")
            raise SystemExit(1)
        write_jsonl(args.output, documents)
        manifest_path = args.output.with_suffix(".manifest.json")
        write_dataset_manifest(
            manifest_path,
            documents=documents,
            scope_path=args.scope,
            dataset_path=args.output,
        )
        section_count = sum(len(document.sections) for document in documents)
        print(f"Collected {len(documents)} documents / {section_count} sections")
        print(args.output)
        print(manifest_path)
        return

    if args.command == "validate":
        validation = validate_documents(load_jsonl(args.path))
        print(
            f"Validated {validation.document_count} documents / "
            f"{validation.section_count} sections"
        )
        print(f"Document types: {validation.document_types}")
        if validation.errors:
            for error in validation.errors:
                print(f"ERROR: {error}")
            raise SystemExit(1)
        print("Dataset validation passed")
        return

    repository = get_repository()
    if args.command == "evaluate":
        evaluations = [
            evaluate_case(case, repository.search(case.query, args.top_k))
            for case in load_retrieval_cases(args.cases)
        ]
        for evaluation in evaluations:
            status = "PASS" if evaluation.passed else "FAIL"
            print(f"[{status}] {evaluation.query}")
            print(f"  matched: {evaluation.matched or '-'}")
            for heading in evaluation.result_headings:
                print(f"  - {heading}")
        summary = summarize_evaluations(evaluations)
        print(
            f"Retrieval evaluation: {summary.passed}/{summary.total} passed "
            f"({summary.pass_rate:.1%})"
        )
        if summary.passed != summary.total:
            raise SystemExit(1)
        return

    if args.command == "init-db":
        repository.initialize(Path("migrations"))
        print("Database initialized")
        return
    if args.command == "ingest":
        total = 0
        for document in load_jsonl(args.path):
            total += repository.ingest(document)
        print(f"Ingested {total} chunks")
        return
    for result in repository.search(args.query, args.top_k):
        print(
            f"[{result.score:.4f} {result.retrieval_method}] "
            f"{result.title} {result.heading}"
        )
        print(f"{result.content}\n{result.source_url}\n")


if __name__ == "__main__":
    main()
