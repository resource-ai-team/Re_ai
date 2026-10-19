import argparse
from pathlib import Path

from resource_ai.core.dependencies import get_repository
from resource_ai.rag.ingestion.loader import load_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest legal JSONL into local PostgreSQL")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()

    repository = get_repository()
    total = sum(repository.ingest(document) for document in load_jsonl(args.path))
    print(f"Ingested {total} chunks")


if __name__ == "__main__":
    main()
