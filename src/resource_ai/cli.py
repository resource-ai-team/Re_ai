import argparse
from pathlib import Path

from resource_ai.core.config import get_settings
from resource_ai.core.dependencies import get_repository
from resource_ai.evidence.models import SearchGroup
from resource_ai.integrations.law_api import LawOpenApiClient
from resource_ai.rag.evaluation import (
    evaluate_case,
    load_retrieval_dataset,
    meets_thresholds,
    summarize_by_group,
    summarize_evaluations,
)
from resource_ai.rag.ingestion.dataset import (
    validate_documents,
    verify_dataset_manifest,
    write_dataset_manifest,
)
from resource_ai.rag.ingestion.loader import load_jsonl, write_jsonl
from resource_ai.rag.ingestion.pipeline import collect_scope
from resource_ai.rag.ingestion.public_evidence import build_public_evidence_summaries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local multi-corpus RAG utilities")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Create pgvector tables")

    ingest = commands.add_parser("ingest", help="Ingest evidence JSONL documents")
    ingest.add_argument("path", type=Path)

    index = commands.add_parser(
        "index", help="Validate a generated evidence dataset and atomically index it"
    )
    index.add_argument(
        "path", type=Path, nargs="?", default=Path("data/official/legal_documents.jsonl")
    )
    index.add_argument(
        "--manifest",
        type=Path,
        help="Manifest path (defaults to <dataset>.manifest.json)",
    )

    search = commands.add_parser("search", help="Search evidence")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=5)
    search_scope = search.add_mutually_exclusive_group()
    search_scope.add_argument("--corpus", action="append", dest="corpus_types")
    search_scope.add_argument("--search-group", choices=[group.value for group in SearchGroup])
    search.add_argument("--tenant-id")

    collect = commands.add_parser("collect", help="Collect official Korean legal sources")
    collect.add_argument("--scope", type=Path, default=Path("data/config/legal_scope.json"))
    collect.add_argument("--output", type=Path, default=Path("data/official/legal_documents.jsonl"))

    public_evidence = commands.add_parser(
        "build-public-evidence",
        help="Build searchable reviewed summaries from the pinned public evidence bundle",
    )
    public_evidence.add_argument(
        "--scope",
        type=Path,
        default=Path("data/config/public_evidence_scope.json"),
    )
    public_evidence.add_argument(
        "--output",
        type=Path,
        default=Path("data/official/public_evidence_summaries.jsonl"),
    )

    validate = commands.add_parser("validate", help="Validate an evidence dataset")
    validate.add_argument(
        "path", type=Path, nargs="?", default=Path("data/official/legal_documents.jsonl")
    )

    evaluate = commands.add_parser("evaluate", help="Evaluate retrieval against expected evidence")
    evaluate.add_argument("--cases", type=Path, default=Path("data/config/retrieval_eval.json"))
    evaluate.add_argument("--top-k", type=int)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "build-public-evidence":
        documents = build_public_evidence_summaries(args.scope)
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
        print(
            f"Built {validation.document_count} reviewed-summary documents / "
            f"{validation.section_count} sections"
        )
        print(args.output)
        print(manifest_path)
        return

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
            f"Validated {validation.document_count} documents / {validation.section_count} sections"
        )
        print(f"Document types: {validation.document_types}")
        if validation.errors:
            for error in validation.errors:
                print(f"ERROR: {error}")
            raise SystemExit(1)
        print("Dataset validation passed")
        return

    if args.command == "index":
        manifest_path = args.manifest or args.path.with_suffix(".manifest.json")
        verify_dataset_manifest(manifest_path, args.path)
        documents = load_jsonl(args.path)
        validation = validate_documents(documents)
        if not validation.passed:
            for error in validation.errors:
                print(f"ERROR: {error}")
            raise SystemExit(1)
        repository = get_repository()
        total = repository.ingest_many(documents)
        print(f"Validated and indexed {validation.document_count} documents / {total} chunks")
        return

    repository = get_repository()
    if args.command == "evaluate":
        dataset = load_retrieval_dataset(args.cases)
        top_k = args.top_k or dataset.top_k
        evaluations = [
            evaluate_case(
                case,
                repository.search(case.query, top_k, search_group=case.search_group),
            )
            for case in dataset.cases
        ]
        for evaluation in evaluations:
            status = "PASS" if evaluation.passed else "FAIL"
            print(f"[{status}] [{evaluation.search_group}] {evaluation.query}")
            print(f"  matched: {evaluation.matched or '-'}")
            print(f"  rank: {evaluation.first_relevant_rank or '-'}")
            for heading in evaluation.result_headings:
                print(f"  - {heading}")
        summary = summarize_evaluations(evaluations)
        group_summaries = summarize_by_group(evaluations)
        for group, group_summary in group_summaries.items():
            print(
                f"  {group}: Recall@{top_k}={group_summary.recall_at_k:.1%}, "
                f"MRR={group_summary.mrr:.3f}"
            )
        print(
            f"Retrieval evaluation: Recall@{top_k}={summary.recall_at_k:.1%} "
            f"(minimum {dataset.minimum_recall_at_k:.1%}), "
            f"MRR={summary.mrr:.3f} (minimum {dataset.minimum_mrr:.3f})"
        )
        if not meets_thresholds(summary, dataset) or any(
            not meets_thresholds(group_summary, dataset)
            for group_summary in group_summaries.values()
        ):
            raise SystemExit(1)
        return

    if args.command == "init-db":
        repository.initialize(Path("migrations"))
        print("Database initialized")
        return
    if args.command == "ingest":
        documents = load_jsonl(args.path)
        validation = validate_documents(documents)
        if not validation.passed:
            for error in validation.errors:
                print(f"ERROR: {error}")
            raise SystemExit(1)
        total = repository.ingest_many(documents)
        print(f"Ingested {total} chunks")
        return
    for result in repository.search(
        args.query,
        args.top_k,
        corpus_types=args.corpus_types,
        tenant_id=args.tenant_id,
        search_group=args.search_group,
    ):
        print(f"[{result.score:.4f} {result.retrieval_method}] {result.title} {result.heading}")
        print(f"{result.content}\n{result.source_url}\n")


if __name__ == "__main__":
    main()
