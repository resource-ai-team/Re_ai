import argparse

from resource_ai.core.dependencies import get_repository


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one local RAG retrieval query")
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    for result in get_repository().search(args.query, args.top_k):
        print(f"[{result.score:.4f}] {result.title} / {result.heading}")
        print(result.source_url)


if __name__ == "__main__":
    main()
