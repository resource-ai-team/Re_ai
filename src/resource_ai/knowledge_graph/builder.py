"""Build an isolated import stage; Neo4j remains the runtime store."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from resource_ai.knowledge_graph import ontology
from resource_ai.knowledge_graph.dataset import DEFAULT_DATASET, read_candidates


def build_stage(connection: sqlite3.Connection, rows: list[dict], *, include_curated=True):
    """Only a fresh, owned reference staging database is accepted."""
    tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    if tables:
        raise ValueError("Use a fresh KG staging database; never pass the service database")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "CREATE TABLE documents(id TEXT PRIMARY KEY,kind TEXT,source_id TEXT,title TEXT,text TEXT,metadata TEXT)"
    )
    connection.executemany(
        "INSERT INTO documents VALUES(?,?,?,?,?,?)",
        [
            (
                f"public-kg:{i}",
                "kg_candidate",
                row.get("source_id", "W2RKG-33679"),
                row["waste"],
                " | ".join(
                    row[key]
                    for key in (
                        "waste",
                        "transforming_process",
                        "transformed_resource",
                        "reference",
                    )
                ),
                json.dumps(row, ensure_ascii=False),
            )
            for i, row in enumerate(rows)
        ],
    )
    connection.commit()
    return ontology.build_graph(connection, include_curated=include_curated)


def build_reference(path: Path = DEFAULT_DATASET, *, config=None, progress=None, rebuild=False):
    from resource_ai.knowledge_graph.graph_store import import_graph

    rows = read_candidates(path)
    with closing(sqlite3.connect(":memory:")) as connection:
        build_stage(connection, rows)
        return import_graph(connection, config=config, progress=progress, rebuild=rebuild)
