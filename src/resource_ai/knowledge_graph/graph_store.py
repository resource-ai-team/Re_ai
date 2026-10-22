"""Neo4j graph storage with audited, immutable generations and atomic activation.

Only the reference graph is imported. Account data and user cases stay outside
Neo4j. A failed import never replaces the active generation or deletes a DB.
"""

import atexit
import json
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlsplit
from uuid import uuid4

from neo4j import READ_ACCESS, GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError

from resource_ai.knowledge_graph import ontology as graph

STORE_OWNER = "re-source-neo4j-graph-v1"
_drivers = []


class GraphStoreError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def integrity(
    message="Neo4j 그래프의 구조·근거 연결 검사가 실패했습니다. 그래프를 다시 적재해 주세요.",
):
    return GraphStoreError("integrity", message)


@dataclass(frozen=True)
class Settings:
    uri: str
    username: str
    password: str = field(repr=False)
    database: str = "neo4j"
    namespace: str = "re-ai-reference"

    def __post_init__(self):
        try:
            value = urlsplit(self.uri)
            valid = (
                value.scheme in {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"}
                and value.hostname
                and not value.username
                and not value.password
                and value.path in {"", "/"}
                and not value.query
                and not value.fragment
            )
            _ = value.port  # Access validates invalid/non-numeric ports.
        except (ValueError, TypeError):
            valid = False
        if (
            not valid
            or not isinstance(self.username, str)
            or not self.username.strip()
            or not isinstance(self.password, str)
            or not self.password
            or len(self.password) > 4096
        ):
            raise GraphStoreError(
                "configuration",
                "Neo4j 주소·계정 설정을 확인해 주세요. 비밀번호를 주소에 넣지 마세요.",
            )
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,63}", self.database) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,63}", self.namespace
        ):
            raise GraphStoreError("configuration", "Neo4j database·namespace 설정을 확인해 주세요.")


def settings():
    from resource_ai.core.config import get_settings

    configured = get_settings()
    if not configured.neo4j_password.get_secret_value():
        raise GraphStoreError("not-configured", "NEO4J_PASSWORD와 Neo4j 실행 설정을 확인해 주세요.")
    return Settings(
        configured.neo4j_uri,
        configured.neo4j_username,
        configured.neo4j_password.get_secret_value(),
        configured.neo4j_database,
        configured.neo4j_namespace,
    )


@lru_cache(maxsize=4)
def get_driver(config):
    driver = GraphDatabase.driver(
        config.uri,
        auth=(config.username, config.password),
        connection_timeout=5,
        connection_acquisition_timeout=5,
        max_transaction_retry_time=0,
        max_connection_pool_size=8,
    )
    _drivers.append(driver)
    return driver


@atexit.register
def close_drivers():
    for driver in _drivers:
        try:
            driver.close()
        except (Neo4jError, DriverError, OSError):
            logging.getLogger(__name__).warning("Neo4j driver cleanup failed")


def _read(config, work):
    try:
        with get_driver(config).session(
            database=config.database, default_access_mode=READ_ACCESS
        ) as session:
            return session.execute_read(work)
    except (Neo4jError, DriverError, OSError):
        raise GraphStoreError(
            "unavailable",
            "Neo4j에 연결하지 못했습니다. 서버 실행·계정·적재 상태를 확인해 주세요. SQLite로 자동 전환하지 않습니다.",
        ) from None


def _head(tx, config):
    row = tx.run(
        "MATCH (h:ReSourceGraph {space:$space}) RETURN properties(h) AS head",
        space=config.namespace,
    ).single()
    if not row:
        raise GraphStoreError(
            "not-imported", "사용할 Neo4j 그래프가 없습니다. resource-kg import를 실행해 주세요."
        )
    head = dict(row["head"])
    if head.get("owner") != STORE_OWNER or not isinstance(head.get("generation"), str):
        raise integrity()
    row = tx.run(
        """MATCH (d:ReSourceDataset {space:$space,generation:$generation}) RETURN properties(d) AS dataset""",
        space=config.namespace,
        generation=head["generation"],
    ).single()
    if not row:
        raise integrity()
    dataset = dict(row["dataset"])
    if dataset.get("owner") != STORE_OWNER or dataset.get("ready") is not True:
        raise integrity()
    manifest = json.loads(dataset["manifest_json"])
    definition = graph.ontology()
    meta = manifest["meta"]
    if (
        meta.get("ready") != "true"
        or meta.get("owner") != graph.OWNER
        or meta.get("ontology_sha256") != graph.digest(definition)
    ):
        raise integrity()
    return head["generation"], manifest


def _node_fields(identifier, kind, label, encoded, config, generation, aliases, record=None):
    props = json.loads(encoded)
    fields = {
        "space": config.namespace,
        "generation": generation,
        "id": identifier,
        "kind": kind,
        "label": label,
        "properties_json": encoded,
    }
    for key, value in props.items():
        if key in fields:
            raise integrity()
        if (
            isinstance(value, (str, bool, int, float))
            or isinstance(value, list)
            and all(isinstance(item, str) for item in value)
        ):
            fields[key] = value
    if kind == "Material":
        fields["aliases"] = sorted(item[0] for item in aliases if item[1] == identifier)
    if record:
        fields.update(
            document_id=record["document"]["id"],
            record_sha256=record["record_sha256"],
            document_json=graph.canonical(record["document"]),
        )
    return fields


def _constraints(driver, config):
    with driver.session(database=config.database) as session:
        for name, label, properties in [
            ("resource_node_identity", "ReSourceNode", "(n.space,n.generation,n.id)"),
            ("resource_dataset_identity", "ReSourceDataset", "(n.space,n.generation)"),
            ("resource_graph_identity", "ReSourceGraph", "n.space"),
        ]:
            session.run(
                f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE {properties} IS UNIQUE"
            ).consume()


def _batches(cursor, size=500):
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield rows


def import_graph(connection, *, config=None, rebuild=False, progress=None):
    config = config or settings()
    # This is an import-stage validator, never the runtime graph backend.
    audit = graph.audit_graph(connection)
    definition = graph.ontology()
    aliases = [
        list(row)
        for row in connection.execute(
            "SELECT alias_key,material_id,alias_label,provenance FROM kg_aliases ORDER BY alias_key"
        )
    ]
    manifest = {
        "meta": graph._meta(connection),
        "definition": definition,
        "aliases": aliases,
        "node_counts": audit["node_counts"],
        "edge_counts": audit["edge_counts"],
    }
    generation = uuid4().hex
    try:
        driver = get_driver(config)
        _constraints(driver, config)
        owner = _read(
            config,
            lambda tx: tx.run(
                "MATCH (h:ReSourceGraph {space:$space}) RETURN properties(h) AS head",
                space=config.namespace,
            ).single(),
        )
        if owner and owner["head"].get("owner") != STORE_OWNER:
            raise integrity(
                "해당 Neo4j namespace의 소유 기록이 다릅니다. 기존 자료를 변경하지 않았습니다."
            )
        if not rebuild:
            try:
                active_generation, active = _read(config, lambda tx: _head(tx, config))
            except GraphStoreError as error:
                if error.code != "not-imported":
                    raise
            else:
                if active == manifest:
                    checked = audit_generation(config=config, generation=active_generation)
                    return {**checked, "status": "unchanged"}
        records = {}
        for values in connection.execute("""SELECT r.claim_id,r.record_sha256,d.id,d.kind,d.source_id,d.title,d.text,d.metadata
            FROM kg_claim_records r JOIN documents d ON d.id=r.document_id"""):
            records[values[0]] = {
                "record_sha256": values[1],
                "document": dict(
                    zip(["id", "kind", "source_id", "title", "text", "metadata"], values[2:])
                ),
            }
        with driver.session(database=config.database) as session:
            session.run(
                """CREATE (d:ReSourceDataset {space:$space,generation:$generation,owner:$owner,ready:false,manifest_json:$manifest})""",
                space=config.namespace,
                generation=generation,
                owner=STORE_OWNER,
                manifest=graph.canonical(manifest),
            ).consume()
            for kind in definition["classes"]:
                cursor = connection.execute(
                    "SELECT id,kind,label,properties FROM kg_nodes WHERE kind=? ORDER BY id",
                    (kind,),
                )
                for batch in _batches(cursor):
                    rows = [
                        _node_fields(*row, config, generation, aliases, records.get(row[0]))
                        for row in batch
                    ]
                    session.run(
                        f"UNWIND $rows AS row CREATE (n:ReSourceNode:{kind}) SET n=row", rows=rows
                    ).consume()
                if progress:
                    progress("Nodes imported: " + kind)
            for kind in definition["relations"]:
                cursor = connection.execute(
                    "SELECT id,source_id,target_id,properties FROM kg_edges WHERE kind=? ORDER BY id",
                    (kind,),
                )
                for batch in _batches(cursor):
                    rows = [
                        dict(zip(["id", "source", "target", "properties_json"], row))
                        for row in batch
                    ]
                    created = session.run(
                        f"""UNWIND $rows AS row
                        MATCH (s:ReSourceNode {{space:$space,generation:$generation,id:row.source}})
                        MATCH (t:ReSourceNode {{space:$space,generation:$generation,id:row.target}})
                        CREATE (s)-[r:{kind} {{id:row.id,properties_json:row.properties_json}}]->(t)
                        RETURN count(r) AS count""",
                        rows=rows,
                        space=config.namespace,
                        generation=generation,
                    ).single()["count"]
                    if created != len(rows):
                        raise integrity()
                if progress:
                    progress("Relationships imported: " + kind)
        checked = audit_generation(config=config, generation=generation)

        def activate(tx):
            head = tx.run(
                """MERGE (h:ReSourceGraph {space:$space})
                ON CREATE SET h.owner=$owner
                SET h.generation=coalesce(h.generation,'')
                RETURN h.owner AS owner""",
                space=config.namespace,
                owner=STORE_OWNER,
            ).single()
            if not head or head["owner"] != STORE_OWNER:
                raise integrity("활성화 중 namespace 소유 기록이 변경되었습니다.")
            row = tx.run(
                """MATCH (d:ReSourceDataset {space:$space,generation:$generation,owner:$owner})
                SET d.ready=true
                MERGE (h:ReSourceGraph {space:$space})
                SET h.generation=$generation,h.owner=$owner
                RETURN h.generation AS generation""",
                space=config.namespace,
                generation=generation,
                owner=STORE_OWNER,
            ).single()
            if not row:
                raise integrity()

        with driver.session(database=config.database) as session:
            session.execute_write(activate)
        return {**checked, "status": "activated"}
    except (Neo4jError, DriverError, OSError):
        raise GraphStoreError(
            "unavailable",
            "Neo4j 적재에 실패했습니다. 기존 활성 그래프는 전환하지 않았습니다. 서버·계정·설정을 확인해 주세요.",
        ) from None


def audit_generation(*, config=None, generation=None):
    config = config or settings()

    def inspect(tx):
        selected = generation
        if selected is None:
            selected, _ = _head(tx, config)
        record = tx.run(
            """MATCH (d:ReSourceDataset {space:$space,generation:$generation})
            RETURN d.owner AS owner,d.manifest_json AS manifest""",
            space=config.namespace,
            generation=selected,
        ).single()
        if not record or record["owner"] != STORE_OWNER:
            raise integrity()
        manifest = json.loads(record["manifest"])
        definition = graph.ontology()
        if manifest["definition"] != definition:
            raise integrity()
        # Reconstruct only the audited reference model in memory, including the
        # actual Neo4j relationship types/endpoints and projected node fields.
        with closing(sqlite3.connect(":memory:")) as local:
            local.execute("PRAGMA foreign_keys=ON")
            local.execute(
                "CREATE TABLE documents(id TEXT PRIMARY KEY,kind TEXT,source_id TEXT,title TEXT,text TEXT,metadata TEXT)"
            )
            graph._create_schema(local, definition)
            local.executemany(
                "INSERT OR REPLACE INTO kg_meta VALUES(?,?)", list(manifest["meta"].items())
            )
            for row in tx.run(
                """MATCH (n:ReSourceNode {space:$space,generation:$generation})
                RETURN properties(n) AS fields,labels(n) AS labels""",
                space=config.namespace,
                generation=selected,
            ):
                fields = dict(row["fields"])
                identifier, kind = fields["id"], fields["kind"]
                if kind not in definition["classes"] or set(row["labels"]) != {
                    "ReSourceNode",
                    kind,
                }:
                    raise integrity()
                source = None
                if kind == "TransformationClaim":
                    document = json.loads(fields["document_json"])
                    source = {"document": document, "record_sha256": fields["record_sha256"]}
                    local.execute(
                        "INSERT INTO documents VALUES(?,?,?,?,?,?)",
                        [
                            document[key]
                            for key in ["id", "kind", "source_id", "title", "text", "metadata"]
                        ],
                    )
                if fields != _node_fields(
                    identifier,
                    kind,
                    fields["label"],
                    fields["properties_json"],
                    config,
                    selected,
                    manifest["aliases"],
                    source,
                ):
                    raise integrity()
                local.execute(
                    "INSERT INTO kg_nodes VALUES(?,?,?,?)",
                    (identifier, kind, fields["label"], fields["properties_json"]),
                )
                if source:
                    local.execute(
                        "INSERT INTO kg_claim_records VALUES(?,?,?)",
                        (identifier, source["document"]["id"], source["record_sha256"]),
                    )
            local.executemany("INSERT INTO kg_aliases VALUES(?,?,?,?)", manifest["aliases"])
            for row in tx.run(
                """MATCH (s)-[r]->(t)
                WHERE (s:ReSourceNode AND s.space=$space AND s.generation=$generation)
                   OR (t:ReSourceNode AND t.space=$space AND t.generation=$generation)
                RETURN type(r) AS kind,properties(r) AS fields,
                    s.id AS source,s.space AS source_space,s.generation AS source_generation,
                    t.id AS target,t.space AS target_space,t.generation AS target_generation""",
                space=config.namespace,
                generation=selected,
            ):
                if any(
                    row[key + "_space"] != config.namespace or row[key + "_generation"] != selected
                    for key in ["source", "target"]
                ):
                    raise integrity()
                fields = dict(row["fields"])
                if set(fields) != {"id", "properties_json"}:
                    raise integrity()
                local.execute(
                    "INSERT INTO kg_edges VALUES(?,?,?,?,?)",
                    (
                        fields["id"],
                        row["kind"],
                        row["source"],
                        row["target"],
                        fields["properties_json"],
                    ),
                )
            checked = graph.audit_graph(local)
            if (
                checked["node_counts"] != manifest["node_counts"]
                or checked["edge_counts"] != manifest["edge_counts"]
            ):
                raise integrity()
            return {
                **checked,
                "backend": "neo4j",
                "generation": selected,
                "namespace": config.namespace,
            }

    try:
        return _read(config, inspect)
    except (KeyError, TypeError, ValueError, sqlite3.Error):
        raise integrity() from None


def candidates(
    connection,
    material,
    *,
    limit=600,
    config=None,
    transaction=None,
    active_snapshot=None,
    claim_ids=None,
):
    if (
        not isinstance(material, str)
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 2000
    ):
        raise ValueError("Invalid graph lookup")
    if claim_ids is not None and (
        not isinstance(claim_ids, list)
        or not 1 <= len(claim_ids) <= 100
        or any(not isinstance(value, str) or len(value) > 512 for value in claim_ids)
    ):
        raise ValueError("Invalid claim IDs")
    config = config or settings()
    alias = graph.normalize_label(material)
    source_hash = graph.source_records_digest(connection) if connection is not None else None
    definition = graph.ontology()
    expected_material = next(
        (
            "material:" + key
            for key, value in definition["materials"].items()
            if alias in {graph.normalize_label(item) for item in [key, *value["aliases"]]}
        ),
        None,
    )
    if expected_material is None:
        return []

    def lookup(tx):
        generation, manifest = active_snapshot if active_snapshot is not None else _head(tx, config)
        meta = manifest["meta"]
        if source_hash is not None and source_hash != meta.get("source_records_sha256"):
            raise GraphStoreError(
                "stale-source",
                "Neo4j 그래프와 현재 문헌 원본이 다릅니다. 원본 그래프를 다시 생성·적재해 주세요.",
            )
        matched = list(
            tx.run(
                """MATCH (m:ReSourceNode:Material {space:$space,generation:$generation})
            WHERE $alias IN m.aliases RETURN m.id AS id""",
                space=config.namespace,
                generation=generation,
                alias=alias,
            )
        )
        if len(matched) != 1 or matched[0]["id"] != expected_material:
            raise integrity()
        found = []
        rows = tx.run(
            """MATCH (m:ReSourceNode:Material {space:$space,generation:$generation,id:$material})
            MATCH (w:ReSourceNode:WasteDescription)-[:MENTIONS_MATERIAL]->(m)
            MATCH (c:ReSourceNode:TransformationClaim)-[:INPUT_LABEL]->(w)
            MATCH (c)-[:SUPPORTED_BY]->(e:ReSourceNode:Evidence)-[:FROM_SOURCE]->(s:ReSourceNode:Source)
            WHERE all(n IN [w,c,e,s] WHERE n.space=$space AND n.generation=$generation)
                AND ($claim_ids IS NULL OR c.id IN $claim_ids)
            RETURN c.id AS claim,c.properties_json AS claim_json,c.document_json AS document,
                c.record_sha256 AS record_sha256,e.id AS evidence,e.properties_json AS evidence_json,s.id AS source
            ORDER BY c.document_id LIMIT $limit""",
            space=config.namespace,
            generation=generation,
            material=expected_material,
            limit=limit,
            claim_ids=claim_ids,
        )
        seen = set()
        for value in rows:
            document = json.loads(value["document"])
            raw = json.loads(document["metadata"])
            claim = json.loads(value["claim_json"])
            evidence = json.loads(value["evidence_json"])
            identifier = raw.get("candidate_id") or "record-" + graph.digest(raw)
            if (
                value["claim"] in seen
                or value["claim"] != "claim:" + identifier
                or value["evidence"] != graph.identity("evidence", identifier)
                or value["source"] != graph.identity("source", "W2RKG-33679")
            ):
                raise integrity()
            if (
                graph.digest(document) != value["record_sha256"]
                or claim["raw_record"] != raw
                or evidence.get("record_sha256") != value["record_sha256"]
                or evidence.get("record_key") != identifier
            ):
                raise integrity()
            graph.validate_properties("TransformationClaim", claim, definition)
            seen.add(value["claim"])
            document["metadata"] = raw
            document["graph_trace"] = {
                "backend": "neo4j",
                "generation": generation,
                "ontology_id": meta["ontology_id"],
                "graph_sha256": meta["graph_sha256"],
                "claim_id": value["claim"],
                "material_node_id": expected_material,
                "lookup_path": [
                    "MENTIONS_MATERIAL",
                    "INPUT_LABEL",
                    "TransformationClaim",
                    "SUPPORTED_BY",
                ],
                "dispatch_approved": False,
            }
            found.append(document)
        return found

    try:
        return lookup(transaction) if transaction is not None else _read(config, lookup)
    except (KeyError, TypeError, ValueError):
        raise integrity() from None
