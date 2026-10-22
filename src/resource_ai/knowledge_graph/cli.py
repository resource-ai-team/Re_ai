import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from resource_ai.knowledge_graph.builder import build_reference, build_stage
from resource_ai.knowledge_graph.dataset import DEFAULT_DATASET, read_candidates
from resource_ai.knowledge_graph.graph_store import GraphStoreError, audit_generation


def main():
    parser = argparse.ArgumentParser(description="Audited waste-to-resource reference graph")
    parser.add_argument("command", choices=["validate", "import", "audit"])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, help="Write a validation report without credentials")
    parser.add_argument(
        "--rebuild", action="store_true", help="Import a new graph generation and preserve old ones"
    )
    args = parser.parse_args()
    try:
        if args.command == "validate":
            with closing(sqlite3.connect(":memory:")) as connection:
                result = build_stage(connection, read_candidates(args.dataset))
        elif args.command == "import":
            result = build_reference(args.dataset, rebuild=args.rebuild)
        else:
            result = audit_generation()
    except (GraphStoreError, ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf8")
    print(encoded)


if __name__ == "__main__":
    main()
