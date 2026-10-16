import argparse
from pathlib import Path

from app.dependencies import get_repository
from app.parsing import load_jsonl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local legal RAG utilities")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Create pgvector tables")

    ingest = commands.add_parser("ingest", help="Ingest legal JSONL documents")
    ingest.add_argument("path", type=Path)

    search = commands.add_parser("search", help="Search legal evidence")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=5)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    repository = get_repository()
    if args.command == "init-db":
        repository.initialize(Path("migrations/001_init.sql"))
        print("Database initialized")
        return
    if args.command == "ingest":
        total = 0
        for document in load_jsonl(args.path):
            total += repository.ingest(document)
        print(f"Ingested {total} chunks")
        return
    for result in repository.search(args.query, args.top_k):
        print(f"[{result.score:.4f}] {result.title} {result.heading}")
        print(f"{result.content}\n{result.source_url}\n")


if __name__ == "__main__":
    main()

