"""Persistent, typed source-claim graph. Reachability never approves a process.

Only repository reference data is imported. No user cases, cloud calls, raw
paper retrieval, comma splitting, inferred process chains or operating limits.
"""

import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path

ONTOLOGY_PATH = Path(__file__).with_name("resources") / "waste-resource-v1.json"
OWNER = "re-source-reference-ontology"
TABLES = (
    "kg_meta",
    "kg_classes",
    "kg_relations",
    "kg_nodes",
    "kg_edges",
    "kg_aliases",
    "kg_claim_records",
)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return sha256(canonical(value).encode("utf8")).hexdigest()


def normalize_label(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def identity(kind, value):
    return kind + ":" + digest(value)[:32]


def ontology():
    value = json.loads(ONTOLOGY_PATH.read_text(encoding="utf8"))
    if value["graph_schema"] != "resource-property-graph-v1":
        raise ValueError("Unknown ontology graph schema")
    for name in [*value["classes"], *value["relations"]]:
        if not re.fullmatch("[A-Za-z][A-Za-z0-9_]*", name):
            raise ValueError("Invalid ontology identifier")
    for relation in value["relations"].values():
        if any(kind not in value["classes"] for kind in relation["domain"] + relation["range"]):
            raise ValueError("Unlinked ontology relation")
    return value


def validate_properties(kind, properties, definition):
    types = {"string": str, "object": dict, "array": list, "boolean": bool}
    if kind not in definition["classes"] or not isinstance(properties, dict):
        raise ValueError("Unknown graph node class")
    for name, expected in definition["classes"][kind]["required"].items():
        value = properties.get(name)
        if not isinstance(value, types[expected]) or expected == "string" and not value.strip():
            raise ValueError("Missing graph property: " + kind + "." + name)
    if properties.get("dispatch_approved", False) is not False:
        raise ValueError("Graph cannot approve dispatch")
    if kind == "TransformationClaim" and (
        properties["components_split"] is not False
        or properties["validation"] != "unverified-extraction"
    ):
        raise ValueError("Extracted relations cannot be promoted")
    if (
        kind in {"WasteDescription", "ProcessDescription", "OutputDescription"}
        and properties["opaque"] is not True
    ):
        raise ValueError("Raw descriptions remain opaque")
    if (
        properties.get("sequence_verified", False) is not False
        or properties.get("product_verified", False) is not False
    ):
        raise ValueError("Unverified labels cannot establish sequence or products")


def _meta(connection):
    return dict(connection.execute("SELECT key,value FROM kg_meta"))


def graph_available(connection):
    found = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND lower(name) GLOB 'kg_*'"
        )
    }
    if not found:
        return False
    if not set(TABLES) <= found:
        raise sqlite3.DatabaseError("Incomplete ontology graph tables")
    meta = _meta(connection)
    definition = ontology()
    if (
        meta.get("owner") != OWNER
        or meta.get("ready") != "true"
        or meta.get("ontology_id") != definition["id"]
        or meta.get("ontology_sha256") != digest(definition)
    ):
        raise sqlite3.DatabaseError("Ontology graph is not ready")
    if meta.get("source_records_sha256") != source_records_digest(connection):
        raise sqlite3.DatabaseError("Graph source record set changed; rebuild required")
    return True


def _schema_statements():
    statements = [
        "CREATE TABLE IF NOT EXISTS kg_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS kg_classes(kind TEXT PRIMARY KEY,spec TEXT NOT NULL CHECK(json_valid(spec)))",
        "CREATE TABLE IF NOT EXISTS kg_relations(kind TEXT NOT NULL,source_kind TEXT NOT NULL REFERENCES kg_classes(kind),target_kind TEXT NOT NULL REFERENCES kg_classes(kind),PRIMARY KEY(kind,source_kind,target_kind))",
        "CREATE TABLE IF NOT EXISTS kg_nodes(id TEXT PRIMARY KEY,kind TEXT NOT NULL REFERENCES kg_classes(kind),label TEXT NOT NULL,properties TEXT NOT NULL CHECK(json_valid(properties)))",
        "CREATE TABLE IF NOT EXISTS kg_edges(id TEXT PRIMARY KEY,kind TEXT NOT NULL,source_id TEXT NOT NULL REFERENCES kg_nodes(id),target_id TEXT NOT NULL REFERENCES kg_nodes(id),properties TEXT NOT NULL CHECK(json_valid(properties)))",
        "CREATE TABLE IF NOT EXISTS kg_aliases(alias_key TEXT PRIMARY KEY,material_id TEXT NOT NULL REFERENCES kg_nodes(id),alias_label TEXT NOT NULL,provenance TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS kg_claim_records(claim_id TEXT PRIMARY KEY REFERENCES kg_nodes(id),document_id TEXT NOT NULL UNIQUE REFERENCES documents(id),record_sha256 TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS kg_nodes_kind ON kg_nodes(kind,id)",
        "CREATE INDEX IF NOT EXISTS kg_edges_forward ON kg_edges(kind,source_id,target_id)",
        "CREATE INDEX IF NOT EXISTS kg_edges_reverse ON kg_edges(kind,target_id,source_id)",
        "CREATE INDEX IF NOT EXISTS kg_edges_source ON kg_edges(source_id,id)",
        "CREATE INDEX IF NOT EXISTS kg_edges_target ON kg_edges(target_id,id)",
    ]
    for action in ["INSERT", "UPDATE"]:
        statements.append(f"""CREATE TRIGGER IF NOT EXISTS kg_edge_types_{action.lower()} BEFORE {action} ON kg_edges
            WHEN NOT EXISTS(SELECT 1 FROM kg_relations r JOIN kg_nodes s ON s.id=NEW.source_id JOIN kg_nodes t ON t.id=NEW.target_id
              WHERE r.kind=NEW.kind AND r.source_kind=s.kind AND r.target_kind=t.kind)
            BEGIN SELECT RAISE(ABORT,'Ontology edge domain/range mismatch'); END""")
    statements.append("""CREATE TRIGGER IF NOT EXISTS kg_node_type_update BEFORE UPDATE OF kind ON kg_nodes
        WHEN EXISTS(SELECT 1 FROM kg_edges e JOIN kg_nodes t ON t.id=e.target_id WHERE e.source_id=OLD.id AND NOT EXISTS
          (SELECT 1 FROM kg_relations r WHERE r.kind=e.kind AND r.source_kind=NEW.kind AND r.target_kind=t.kind))
        OR EXISTS(SELECT 1 FROM kg_edges e JOIN kg_nodes s ON s.id=e.source_id WHERE e.target_id=OLD.id AND NOT EXISTS
          (SELECT 1 FROM kg_relations r WHERE r.kind=e.kind AND r.source_kind=s.kind AND r.target_kind=NEW.kind))
        BEGIN SELECT RAISE(ABORT,'Ontology node type breaks connected edges'); END""")
    return statements


def _schema_key(sql):
    return re.sub(
        r"\s+", "", re.sub(r"\bIF\s+NOT\s+EXISTS\b", "", sql, flags=re.IGNORECASE)
    ).casefold()


def _check_owned_schema(connection):
    expected = {}
    for sql in _schema_statements():
        match = re.match(r"CREATE (TABLE|INDEX|TRIGGER) IF NOT EXISTS (\w+)", sql)
        expected[match[2]] = (match[1].lower(), _schema_key(sql))
    actual = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE lower(name) GLOB 'kg_*' OR lower(tbl_name) GLOB 'kg_*'"
    ).fetchall()
    if actual:
        names = {row[1] for row in actual}
        required = {name for name, (kind, _) in expected.items() if kind != "index"}
        if not required <= names:
            raise ValueError("Incomplete owned graph schema")
        for kind, name, table, sql in actual:
            if name.startswith("sqlite_autoindex_") and sql is None:
                continue
            if name not in expected or (kind, _schema_key(sql or "")) != expected[name]:
                raise ValueError("Unexpected or modified graph schema: " + name)
        if _meta(connection).get("owner") != OWNER:
            raise ValueError("Existing graph tables are not owned by this builder")
    # Outside tables must not cascade from graph deletion into source data.
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall():
        if name in TABLES:
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        if any(
            row[2].casefold() in TABLES
            for row in connection.execute("PRAGMA foreign_key_list(" + quoted + ")")
        ):
            raise ValueError("Outside table references owned graph: " + name)


def _create_schema(connection, definition):
    _check_owned_schema(connection)
    for statement in _schema_statements():
        connection.execute(statement)
    # Rebuild only this builder's reference graph, inside its caller savepoint.
    for table in [
        "kg_claim_records",
        "kg_aliases",
        "kg_edges",
        "kg_nodes",
        "kg_relations",
        "kg_classes",
        "kg_meta",
    ]:
        connection.execute("DELETE FROM " + table)
    connection.executemany(
        "INSERT INTO kg_classes VALUES(?,?)",
        [(key, canonical(spec)) for key, spec in definition["classes"].items()],
    )
    connection.executemany(
        "INSERT INTO kg_relations VALUES(?,?,?)",
        [
            (key, source, target)
            for key, spec in definition["relations"].items()
            for source in spec["domain"]
            for target in spec["range"]
        ],
    )
    connection.executemany(
        "INSERT INTO kg_meta VALUES(?,?)",
        [
            ("owner", OWNER),
            ("ontology_id", definition["id"]),
            ("ontology_sha256", digest(definition)),
            ("ready", "false"),
        ],
    )


def source_records_digest(connection):
    records = []
    for values in connection.execute(
        "SELECT id,kind,source_id,title,text,metadata FROM documents WHERE kind='kg_candidate' ORDER BY id"
    ):
        row = dict(zip(["id", "kind", "source_id", "title", "text", "metadata"], values))
        raw = json.loads(row["metadata"])
        records.append([raw.get("candidate_id") or "record-" + digest(raw), digest(row)])
    return digest(sorted(records))


def _documents_digest(connection):
    result = sha256()
    for row in connection.execute("SELECT * FROM documents ORDER BY id"):
        result.update(canonical(list(row)).encode("utf8"))
        result.update(b"\n")
    return result.hexdigest()


class Builder:
    def __init__(self, connection, definition):
        self.connection, self.definition, self.seen_nodes, self.seen_edges, self.terms = (
            connection,
            definition,
            {},
            {},
            {},
        )

    def node(self, identifier, kind, label, properties):
        validate_properties(kind, properties, self.definition)
        encoded = canonical(properties)
        signature = digest([kind, label, properties])
        if identifier in self.seen_nodes:
            if self.seen_nodes[identifier] != signature:
                raise ValueError("Conflicting graph node ID: " + identifier)
            return identifier
        self.connection.execute(
            "INSERT INTO kg_nodes VALUES(?,?,?,?)", (identifier, kind, label, encoded)
        )
        self.seen_nodes[identifier] = signature
        return identifier

    def edge(self, kind, source, target, **properties):
        properties = {"dispatch_approved": False, **properties}
        identifier = identity("edge", [kind, source, target])
        signature = digest(properties)
        if identifier in self.seen_edges:
            if self.seen_edges[identifier] != signature:
                raise ValueError("Conflicting graph edge")
            return identifier
        self.connection.execute(
            "INSERT INTO kg_edges VALUES(?,?,?,?,?)",
            (identifier, kind, source, target, canonical(properties)),
        )
        self.seen_edges[identifier] = signature
        return identifier

    def term(self, kind, raw):
        if not raw.strip():
            return None
        key = normalize_label(raw)
        identifier = identity(kind, key)
        if identifier not in self.terms:
            properties = {"normalization_key": key, "raw_labels": [raw], "opaque": True}
            if kind == "ProcessDescription":
                properties["sequence_verified"] = False
            if kind == "OutputDescription":
                properties["product_verified"] = False
            self.node(identifier, kind, key, properties)
            self.terms[identifier] = properties
        elif raw not in self.terms[identifier]["raw_labels"]:
            self.terms[identifier]["raw_labels"].append(raw)
        return identifier

    def flush_terms(self):
        self.connection.executemany(
            "UPDATE kg_nodes SET properties=? WHERE id=?",
            [
                (canonical({**props, "raw_labels": sorted(props["raw_labels"])}), key)
                for key, props in self.terms.items()
            ],
        )


def _materials(builder):
    for key, value in builder.definition["materials"].items():
        identifier = "material:" + key
        builder.node(
            identifier, "Material", value["label"], {"canonical_id": key, "search_scope_only": True}
        )
        for alias in {key, *value["aliases"]}:
            builder.connection.execute(
                "INSERT INTO kg_aliases VALUES(?,?,?,?)",
                (
                    normalize_label(alias),
                    identifier,
                    alias,
                    "team-declared-complete-label-alias; search family only",
                ),
            )


def _import_extractions(builder):
    connection = builder.connection
    rows = connection.execute(
        "SELECT id,kind,source_id,title,text,metadata FROM documents WHERE kind='kg_candidate' ORDER BY id"
    )
    seen, record_hashes = set(), []
    source = identity("source", "W2RKG-33679")
    builder.node(
        source,
        "Source",
        "W2RKG relation dataset",
        {
            "source_key": "W2RKG-33679",
            "origin": "dataset",
            "metadata": {
                "upstream": "https://github.com/nancycyzl/W2RKG-construction-with-LLMs",
                "upstream_commit": "6cc057a7dcfd89f67f526db6c6c69d71904ccf66",
                "attribution": "data/knowledge_graph/ATTRIBUTION.w2rkg.md",
                "license_scope": "dataset repository Apache-2.0; cited article texts are not licensed by this",
                "article_support_verified": False,
            },
        },
    )
    for values in rows:
        row = dict(zip(["id", "kind", "source_id", "title", "text", "metadata"], values))
        raw = json.loads(row["metadata"])
        if not isinstance(raw, dict) or any(
            not isinstance(raw.get(key), str)
            for key in ["waste", "transforming_process", "transformed_resource", "reference"]
        ):
            raise ValueError("Invalid source relation fields")
        candidate_id = raw.get("candidate_id") or "record-" + digest(raw)
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in seen:
            raise ValueError("Duplicate or invalid source candidate ID")
        if (
            raw.get("source_id", row["source_id"]) != "W2RKG-33679"
            or row["source_id"] != "W2RKG-33679"
        ):
            raise ValueError("Unexpected relation dataset source")
        seen.add(candidate_id)
        record_hash = digest(row)
        record_hashes.append([candidate_id, record_hash])
        claim = "claim:" + candidate_id
        builder.node(
            claim,
            "TransformationClaim",
            raw["waste"][:180] or candidate_id,
            {
                "candidate_id": candidate_id,
                "raw_record": raw,
                "validation": "unverified-extraction",
                "components_split": False,
                "dispatch_approved": False,
            },
        )
        connection.execute(
            "INSERT INTO kg_claim_records VALUES(?,?,?)", (claim, row["id"], record_hash)
        )
        evidence = identity("evidence", candidate_id)
        builder.node(
            evidence,
            "Evidence",
            "Dataset record " + candidate_id,
            {
                "record_key": candidate_id,
                "locator": "W2RKG row " + str(raw.get("upstream_row_1_based", "not-recorded")),
                "validation": "dataset-record-not-article-verification",
                "model_transfer_allowed": raw.get("model_transfer_allowed") is not False,
                "record_sha256": record_hash,
                "reference_raw": raw["reference"],
                "doi_candidates": raw.get("doi_search_candidates", []),
                "article_support_verified": False,
            },
        )
        builder.edge(
            "SUPPORTED_BY", claim, evidence, basis="automatically-extracted-dataset-record"
        )
        builder.edge("FROM_SOURCE", evidence, source)
        for field, kind, relation in [
            ("waste", "WasteDescription", "INPUT_LABEL"),
            ("transforming_process", "ProcessDescription", "PROCESS_LABEL"),
            ("transformed_resource", "OutputDescription", "OUTPUT_LABEL"),
        ]:
            term = builder.term(kind, raw[field])
            if term:
                builder.edge(
                    relation, claim, term, basis="intact-source-field; no-component-pairing"
                )
                if kind == "WasteDescription":
                    for material, definition in builder.definition["materials"].items():
                        if re.search(definition["search_pattern"], raw[field], re.IGNORECASE):
                            builder.edge(
                                "MENTIONS_MATERIAL",
                                term,
                                "material:" + material,
                                basis="lexical-hint-not-composition",
                            )
    return digest(sorted(record_hashes))


def _import_cards(builder):
    from resource_ai.knowledge_graph.dataset import SOURCES
    from resource_ai.knowledge_graph.dataset import allowed_cards as _allowed_cards

    registry = {
        row["source_id"]: row for row in json.loads(SOURCES.read_text(encoding="utf-8-sig"))
    }
    evidence_by_document = {}
    for key, card in sorted(_allowed_cards().items()):
        source_record = registry[card["source_id"]]
        source = identity("source", ["scoped-card", card["source_id"]])
        builder.node(
            source,
            "Source",
            card["source_id"],
            {
                "source_key": card["source_id"],
                "origin": "collected-source-metadata",
                "metadata": source_record,
            },
        )
        evidence = identity("evidence", ["card", key])
        evidence_by_document["process-card:" + key] = evidence
        builder.node(
            evidence,
            "Evidence",
            card["title"],
            {
                "record_key": key,
                "locator": card["locator"],
                "validation": card.get("validation_status")
                or "source-location-checked-not-domain-approval",
                "model_transfer_allowed": card.get("model_transfer_allowed") is True
                and source_record.get("model_transfer_allowed") is not False,
                "source_url": card["source_url"],
                "scope": card["scope"],
                "geography": card.get("geography"),
                "rights_status": card["rights_status"],
                "limitations": card.get("limitations", []),
                "not_proven": card.get("not_proven", []),
                "original_verification": "recorded-acquisition-metadata-not-live-file-recheck",
            },
        )
        builder.edge("FROM_SOURCE", evidence, source)
        claim = identity("scoped-claim", key)
        builder.node(
            claim,
            "ScopedClaim",
            card["title"],
            {
                "card_id": key,
                "statement": card["claim_ko"],
                "scope": card["scope"],
                "raw_record": card,
                "validation": card.get("validation_status")
                or "source-location-checked-not-domain-approval",
                "dispatch_approved": False,
            },
        )
        builder.edge(
            "SUPPORTED_BY", claim, evidence, basis="scoped-research-card-not-universal-rule"
        )
        if card.get("material") in builder.definition["materials"]:
            builder.edge("FOR_MATERIAL", claim, "material:" + card["material"])
        # These are analyst-designed verification tasks, not source-mandated limits.
        for index, requirement in enumerate(card.get("evidence_requirements", [])):
            text = requirement if isinstance(requirement, str) else canonical(requirement)
            condition = identity("condition", [claim, index, text])
            builder.node(
                condition,
                "Condition",
                text[:180],
                {
                    "text": text,
                    "origin": "scoped-card-evidence-requirement",
                    "validation": card.get("evidence_requirements_status")
                    or "analyst-designed-question",
                    "dispatch_approved": False,
                },
            )
            builder.edge("REQUIRES", claim, condition)
            builder.edge(
                "SUPPORTED_BY", condition, evidence, basis="analyst-designed-verification-question"
            )
    return evidence_by_document


def _curated_reports():
    from resource_ai.knowledge_graph.dataset import authored_reports

    yield from authored_reports()


def _import_routes(builder, evidence_by_document):
    for report in _curated_reports():
        by_id = {}
        for evidence in report["evidence"]:
            key = evidence["document_id"]
            if key not in evidence_by_document:
                source = identity("source", key)
                builder.node(
                    source,
                    "Source",
                    evidence["title"],
                    {
                        "source_key": key,
                        "origin": "repository-company-research-note",
                        "metadata": {
                            name: evidence.get(name)
                            for name in [
                                "source_url",
                                "source_records",
                                "checked_date",
                                "rights_status",
                                "limitations",
                                "scope",
                            ]
                        },
                    },
                )
                eid = identity("evidence", key)
                evidence_by_document[key] = eid
                builder.node(
                    eid,
                    "Evidence",
                    evidence["title"],
                    {
                        "record_key": key,
                        "locator": evidence["locator"],
                        "validation": "public-business-case-not-current-acceptance",
                        "model_transfer_allowed": False,
                        "source_url": evidence.get("source_url"),
                        "excerpt": evidence["excerpt"],
                        "limitations": evidence["limitations"],
                        "scope": evidence.get("scope"),
                    },
                )
                builder.edge("FROM_SOURCE", eid, source)
            by_id[evidence["id"]] = evidence_by_document[key]
        for route in report["process_graph"]["routes"]:
            # Include evidence identities and form: E01 and n1 are report-local.
            content = {
                "material": report["material"]["id"],
                "input_form": report["process_graph"].get("input_form", "unknown"),
                "route": route,
                "evidence_map": by_id,
                "evidence_fingerprint": digest(report["evidence"]),
            }
            snapshot_hash = digest(content)
            rid = "route:" + snapshot_hash[:32]
            builder.node(
                rid,
                "RouteSnapshot",
                route["label"],
                {
                    "original_route_id": route["id"],
                    "material": content["material"],
                    "input_form": content["input_form"],
                    "snapshot_hash": snapshot_hash,
                    "payload": route,
                    "evidence_map": by_id,
                    "evidence_fingerprint": content["evidence_fingerprint"],
                    "validation": "authored-conditional-template-not-field-validation",
                    "dispatch_approved": False,
                },
            )
            builder.edge("FOR_MATERIAL", rid, "material:" + content["material"])

            def cite(source, ids, by_id=by_id):
                if not ids:
                    raise ValueError("Curated occurrence has no evidence")
                for eid in ids:
                    if eid not in by_id:
                        raise ValueError("Unlinked curated evidence ID")
                    builder.edge(
                        "SUPPORTED_BY",
                        source,
                        by_id[eid],
                        basis="authored-conditional-process-research",
                    )

            cite(rid, route["evidence_ids"])
            occurrences = {}
            for group, kind, relation in [
                ("nodes", "StepOccurrence", "HAS_STEP"),
                ("edges", "LinkOccurrence", "HAS_LINK"),
                ("risks", "Risk", "HAS_RISK"),
            ]:
                for item in route[group]:
                    identifier = identity(kind, [rid, item["id"]])
                    occurrences[item["id"]] = identifier
                    properties = {
                        "original_id": item["id"],
                        "route_snapshot_id": rid,
                        "payload": item,
                        "validation": "authored-verification-question-not-operating-approval",
                    }
                    if kind == "Risk":
                        properties["dispatch_approved"] = False
                    builder.node(
                        identifier,
                        kind,
                        item.get("label") or item.get("title") or item["id"],
                        properties,
                    )
                    builder.edge(relation, rid, identifier)
                    cite(identifier, item["evidence_ids"])
            for edge in route["edges"]:
                builder.edge("FROM_STEP", occurrences[edge["id"]], occurrences[edge["from"]])
                builder.edge("TO_STEP", occurrences[edge["id"]], occurrences[edge["to"]])
            for risk in route["risks"]:
                builder.edge("TARGETS", occurrences[risk["id"]], occurrences[risk["target_id"]])
            for index, text in enumerate(route["input_conditions"]):
                identifier = identity("condition", [rid, index, text])
                builder.node(
                    identifier,
                    "Condition",
                    text,
                    {
                        "text": text,
                        "origin": "authored-route-input-question",
                        "validation": "requires-lot-and-receiver-verification",
                        "dispatch_approved": False,
                    },
                )
                builder.edge("REQUIRES", rid, identifier)
                cite(identifier, route["evidence_ids"])


def graph_digest(connection):
    result = sha256()
    for table, columns, order in [
        ("kg_nodes", "id,kind,label,properties", "id"),
        ("kg_edges", "id,kind,source_id,target_id,properties", "id"),
        ("kg_aliases", "alias_key,material_id,alias_label,provenance", "alias_key"),
        ("kg_claim_records", "claim_id,document_id,record_sha256", "claim_id"),
    ]:
        for row in connection.execute(f"SELECT {columns} FROM {table} ORDER BY {order}"):
            result.update(canonical(list(row)).encode("utf8"))
            result.update(b"\n")
    return result.hexdigest()


def build_graph(connection, *, include_curated=True, progress=None):
    if not connection.in_transaction:
        connection.execute("PRAGMA foreign_keys=ON")
    if not connection.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise ValueError("Graph builds require foreign keys enabled before the transaction")
    definition = ontology()
    previous_cache = connection.execute("PRAGMA cache_size").fetchone()[0]
    # A build touches many indexed pages. Keep a bounded 128 MiB connection
    # cache during the transaction; never change journaling or durability.
    connection.execute("PRAGMA cache_size=-131072")
    connection.execute("SAVEPOINT resource_graph_build")
    try:
        original_documents = _documents_digest(connection)
        _create_schema(connection, definition)
        builder = Builder(connection, definition)
        _materials(builder)
        if progress:
            progress("Importing complete W2RKG source claims...")
        source_digest = _import_extractions(builder)
        if progress:
            progress("Importing scoped evidence and authored process snapshots...")
        if include_curated:
            _import_routes(builder, _import_cards(builder))
            from resource_ai.knowledge_graph.planning_reference import import_reference

            import_reference(builder)
        builder.flush_terms()
        # Release large write caches before validating the persisted graph.
        del builder
        connection.execute(
            "INSERT INTO kg_meta VALUES(?,?)", ("source_records_sha256", source_digest)
        )
        connection.execute(
            "INSERT INTO kg_meta VALUES(?,?)", ("includes_curated", str(include_curated).lower())
        )
        if progress:
            progress("Auditing ontology types, provenance and source preservation...")
        result = audit_graph(connection)
        if _documents_digest(connection) != original_documents:
            raise ValueError("Graph rebuild modified source documents")
        connection.execute(
            "INSERT INTO kg_meta VALUES(?,?)", ("graph_sha256", result["graph_sha256"])
        )
        connection.execute("UPDATE kg_meta SET value='true' WHERE key='ready'")
        connection.execute("RELEASE resource_graph_build")
        return result
    except Exception:
        connection.execute("ROLLBACK TO resource_graph_build")
        connection.execute("RELEASE resource_graph_build")
        raise
    finally:
        connection.execute("PRAGMA cache_size=" + str(previous_cache))


def audit_graph(connection, *, check_source_records=True):
    _check_owned_schema(connection)
    definition = ontology()
    meta = _meta(connection)
    if meta.get("owner") != OWNER or meta.get("ontology_sha256") != digest(definition):
        raise ValueError("Ontology definition mismatch")
    expected_relations = {
        (name, source, target)
        for name, spec in definition["relations"].items()
        for source in spec["domain"]
        for target in spec["range"]
    }
    if {
        tuple(row)
        for row in connection.execute("SELECT kind,source_kind,target_kind FROM kg_relations")
    } != expected_relations:
        raise ValueError("Persisted ontology relation definitions changed")
    if {
        row[0]: json.loads(row[1]) for row in connection.execute("SELECT kind,spec FROM kg_classes")
    } != definition["classes"]:
        raise ValueError("Persisted ontology class definitions changed")
    for row in connection.execute("PRAGMA foreign_key_check"):
        if str(row[0]).startswith("kg_"):
            raise ValueError("Dangling graph reference")
    invalid = connection.execute("""SELECT COUNT(*) FROM kg_edges e LEFT JOIN kg_nodes s ON e.source_id=s.id LEFT JOIN kg_nodes t ON e.target_id=t.id
        LEFT JOIN kg_relations r ON r.kind=e.kind AND r.source_kind=s.kind AND r.target_kind=t.kind WHERE r.kind IS NULL""").fetchone()[
        0
    ]
    if invalid:
        raise ValueError("Ontology domain/range mismatch")
    kinds, descriptions, scopes, stored = {}, {}, {}, {}
    for identifier, kind, label, encoded in connection.execute(
        "SELECT id,kind,label,properties FROM kg_nodes"
    ):
        properties = json.loads(encoded)
        validate_properties(kind, properties, definition)
        kinds[identifier] = kind
        if not label.strip():
            raise ValueError("Empty graph label")
        if kind in {"WasteDescription", "ProcessDescription", "OutputDescription"}:
            descriptions[identifier] = properties["raw_labels"]
        if "route_snapshot_id" in properties:
            scopes[identifier] = properties["route_snapshot_id"]
        if kind in {
            "Evidence",
            "Source",
            "RouteSnapshot",
            "StepOccurrence",
            "LinkOccurrence",
            "Risk",
        }:
            stored[identifier] = properties
    outgoing = defaultdict(lambda: defaultdict(list))
    parents = defaultdict(list)
    for kind, source, target in connection.execute("SELECT kind,source_id,target_id FROM kg_edges"):
        outgoing[source][kind].append(target)
        if kind in {"HAS_STEP", "HAS_LINK", "HAS_RISK"}:
            parents[target].append((kind, source))
    for identifier, kind in kinds.items():
        if (
            kind
            in {
                "TransformationClaim",
                "ScopedClaim",
                "RouteSnapshot",
                "StepOccurrence",
                "LinkOccurrence",
                "Risk",
                "Condition",
            }
            and not outgoing[identifier]["SUPPORTED_BY"]
        ):
            raise ValueError("Missing graph evidence")
        if kind == "Evidence" and len(outgoing[identifier]["FROM_SOURCE"]) != 1:
            raise ValueError("Evidence source cardinality mismatch")
        if kind == "LinkOccurrence":
            for relation in ["FROM_STEP", "TO_STEP"]:
                targets = outgoing[identifier][relation]
                if len(targets) != 1 or scopes.get(targets[0]) != scopes[identifier]:
                    raise ValueError("Cross-snapshot process link")
        if kind == "Risk":
            targets = outgoing[identifier]["TARGETS"]
            if len(targets) != 1 or scopes.get(targets[0]) != scopes[identifier]:
                raise ValueError("Cross-snapshot risk target")
    # Bind every authored occurrence and citation to its original route payload,
    # not just to another node with a compatible class in the same snapshot.
    for identifier, props in stored.items():
        kind = kinds[identifier]
        if kind == "RouteSnapshot":
            content = {
                "material": props["material"],
                "input_form": props["input_form"],
                "route": props["payload"],
                "evidence_map": props["evidence_map"],
                "evidence_fingerprint": props["evidence_fingerprint"],
            }
            if (
                digest(content) != props["snapshot_hash"]
                or identifier != "route:" + props["snapshot_hash"][:32]
            ):
                raise ValueError("Route snapshot identity mismatch")
            for group, child_kind, relation in [
                ("nodes", "StepOccurrence", "HAS_STEP"),
                ("edges", "LinkOccurrence", "HAS_LINK"),
                ("risks", "Risk", "HAS_RISK"),
            ]:
                expected = [
                    identity(child_kind, [identifier, item["id"]])
                    for item in props["payload"][group]
                ]
                if len(expected) != len(set(expected)) or set(
                    outgoing[identifier][relation]
                ) != set(expected):
                    raise ValueError("Route occurrence membership mismatch")
        elif kind in {"StepOccurrence", "LinkOccurrence", "Risk"}:
            rid = props["route_snapshot_id"]
            route = stored.get(rid, {})
            relation, group = {
                "StepOccurrence": ("HAS_STEP", "nodes"),
                "LinkOccurrence": ("HAS_LINK", "edges"),
                "Risk": ("HAS_RISK", "risks"),
            }[kind]
            if kinds.get(rid) != "RouteSnapshot" or parents[identifier] != [(relation, rid)]:
                raise ValueError("Occurrence parent mismatch")
            original = [
                item for item in route["payload"][group] if item["id"] == props["original_id"]
            ]
            if (
                len(original) != 1
                or original[0] != props["payload"]
                or identifier != identity(kind, [rid, props["original_id"]])
            ):
                raise ValueError("Occurrence payload mismatch")
            if kind == "LinkOccurrence":
                for field, edge_kind in [("from", "FROM_STEP"), ("to", "TO_STEP")]:
                    if outgoing[identifier][edge_kind] != [
                        identity("StepOccurrence", [rid, props["payload"][field]])
                    ]:
                        raise ValueError("Link original endpoint mismatch")
            if kind == "Risk":
                target_kind = {"node": "StepOccurrence", "edge": "LinkOccurrence"}.get(
                    props["payload"]["target_type"]
                )
                if target_kind is None or outgoing[identifier]["TARGETS"] != [
                    identity(target_kind, [rid, props["payload"]["target_id"]])
                ]:
                    raise ValueError("Risk original target mismatch")
        else:
            continue
        route = props if kind == "RouteSnapshot" else stored[props["route_snapshot_id"]]
        wanted = {route["evidence_map"].get(eid) for eid in props["payload"]["evidence_ids"]}
        if None in wanted or set(outgoing[identifier]["SUPPORTED_BY"]) != wanted:
            raise ValueError("Route evidence binding mismatch")
    missing_process = missing_reference = claims = 0
    rows = connection.execute("""SELECT n.id,n.properties,d.id,d.kind,d.source_id,d.title,d.text,d.metadata,r.record_sha256
        FROM kg_nodes n JOIN kg_claim_records r ON r.claim_id=n.id JOIN documents d ON d.id=r.document_id WHERE n.kind='TransformationClaim' ORDER BY n.id""")
    for values in rows:
        identifier, encoded, *rest = values
        props = json.loads(encoded)
        row = dict(zip(["id", "kind", "source_id", "title", "text", "metadata"], rest[:6]))
        raw = props["raw_record"]
        if check_source_records and (raw != json.loads(row["metadata"]) or digest(row) != rest[6]):
            raise ValueError("Graph source record changed; rebuild required")
        claims += 1
        missing_process += not raw["transforming_process"].strip()
        missing_reference += not raw["reference"].strip()
        if len(outgoing[identifier]["SUPPORTED_BY"]) != 1:
            raise ValueError("Transformation evidence cardinality mismatch")
        evidence_id = outgoing[identifier]["SUPPORTED_BY"][0]
        evidence = stored[evidence_id]
        candidate_id = raw.get("candidate_id") or "record-" + digest(raw)
        source_id = identity("source", "W2RKG-33679")
        expected_evidence = {
            "record_key": candidate_id,
            "record_sha256": rest[6],
            "reference_raw": raw["reference"],
            "doi_candidates": raw.get("doi_search_candidates", []),
            "article_support_verified": False,
            "validation": "dataset-record-not-article-verification",
        }
        if (
            props["candidate_id"] != candidate_id
            or identifier != "claim:" + candidate_id
            or evidence_id != identity("evidence", candidate_id)
        ):
            raise ValueError("Transformation evidence identity mismatch")
        if (
            any(evidence.get(key) != value for key, value in expected_evidence.items())
            or outgoing[evidence_id]["FROM_SOURCE"] != [source_id]
            or stored[source_id]["source_key"] != "W2RKG-33679"
        ):
            raise ValueError("Transformation evidence binding mismatch")
        for field, relation in [
            ("waste", "INPUT_LABEL"),
            ("transforming_process", "PROCESS_LABEL"),
            ("transformed_resource", "OUTPUT_LABEL"),
        ]:
            targets = outgoing[identifier][relation]
            expected = 1 if raw[field].strip() else 0
            if (
                len(targets) != expected
                or targets
                and raw[field] not in descriptions.get(targets[0], [])
            ):
                raise ValueError("Raw source tuple was split or changed")
    if (
        claims != Counter(kinds.values())["TransformationClaim"]
        or claims
        != connection.execute(
            "SELECT COUNT(*) FROM documents WHERE kind='kg_candidate'"
        ).fetchone()[0]
    ):
        raise ValueError("Transformation source coverage mismatch")
    if check_source_records and source_records_digest(connection) != meta.get(
        "source_records_sha256"
    ):
        raise ValueError("Graph source record set changed; rebuild required")
    expected_aliases = {
        normalize_label(alias): "material:" + key
        for key, spec in definition["materials"].items()
        for alias in [key, *spec["aliases"]]
    }
    if dict(connection.execute("SELECT alias_key,material_id FROM kg_aliases")) != expected_aliases:
        raise ValueError("Material aliases differ from declared ontology")
    for alias, material in connection.execute("SELECT alias_key,material_id FROM kg_aliases"):
        if alias != normalize_label(alias) or kinds.get(material) != "Material":
            raise ValueError("Invalid material alias")
    from resource_ai.knowledge_graph.planning_reference import audit_reference

    audit_reference(connection)
    fingerprint = graph_digest(connection)
    if meta.get("ready") == "true" and meta.get("graph_sha256") != fingerprint:
        raise ValueError("Graph content digest mismatch")
    return {
        "ontology_id": definition["id"],
        "graph_schema": definition["graph_schema"],
        "graph_sha256": fingerprint,
        "node_counts": dict(sorted(Counter(kinds.values()).items())),
        "edge_counts": dict(
            sorted(
                Counter(row[0] for row in connection.execute("SELECT kind FROM kg_edges")).items()
            )
        ),
        "source_claim_count": claims,
        "missing_process_preserved": missing_process,
        "missing_reference_preserved": missing_reference,
        "source_records_checked": check_source_records,
        "components_split": False,
        "field_accuracy_validated": False,
        "dispatch_approved": False,
    }


def graph_candidates(connection, material, *, limit=600):
    if not graph_available(connection):
        return []
    if (
        not isinstance(material, str)
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 2000
    ):
        raise ValueError("Invalid graph lookup")
    material_row = connection.execute(
        "SELECT material_id FROM kg_aliases WHERE alias_key=?", (normalize_label(material),)
    ).fetchone()
    if not material_row:
        return []
    # CROSS JOIN keeps the small material-hint set first, even on freshly built
    # databases without planner statistics. Starting at all claims causes many
    # unnecessary random document reads on the complete corpus.
    rows = connection.execute(
        """SELECT DISTINCT d.id,d.kind,d.source_id,d.title,d.text,d.metadata,c.id,r.record_sha256
        FROM kg_edges hint CROSS JOIN kg_edges input ON input.kind='INPUT_LABEL' AND input.target_id=hint.source_id
        CROSS JOIN kg_nodes c ON c.id=input.source_id AND c.kind='TransformationClaim'
        CROSS JOIN kg_claim_records r ON r.claim_id=c.id CROSS JOIN documents d ON d.id=r.document_id
        WHERE hint.kind='MENTIONS_MATERIAL' AND hint.target_id=? ORDER BY d.id LIMIT ?""",
        (material_row[0], limit),
    )
    found = []
    meta = _meta(connection)
    for values in rows:
        row = dict(zip(["id", "kind", "source_id", "title", "text", "metadata"], values[:6]))
        if digest(row) != values[7]:
            raise sqlite3.DatabaseError("Graph source record changed; rebuild required")
        row["metadata"] = json.loads(row["metadata"])
        row["graph_trace"] = {
            "ontology_id": meta["ontology_id"],
            "graph_sha256": meta["graph_sha256"],
            "claim_id": values[6],
            "material_node_id": material_row[0],
            "lookup_path": [
                "MENTIONS_MATERIAL",
                "INPUT_LABEL",
                "TransformationClaim",
                "SUPPORTED_BY",
            ],
            "dispatch_approved": False,
        }
        found.append(row)
    return found


def inspect_claim(connection, candidate_id):
    if not graph_available(connection):
        raise ValueError("No built ontology graph")
    identifier = "claim:" + candidate_id
    row = connection.execute(
        "SELECT id,kind,label,properties FROM kg_nodes WHERE id=? AND kind='TransformationClaim'",
        (identifier,),
    ).fetchone()
    if row is None:
        raise ValueError("Unknown source claim")
    source = connection.execute(
        """SELECT d.id,d.kind,d.source_id,d.title,d.text,d.metadata,r.record_sha256
        FROM kg_claim_records r JOIN documents d ON d.id=r.document_id WHERE r.claim_id=?""",
        (identifier,),
    ).fetchone()
    if (
        source is None
        or digest(dict(zip(["id", "kind", "source_id", "title", "text", "metadata"], source[:6])))
        != source[6]
    ):
        raise ValueError("Graph source record changed; rebuild required")
    nodes = {
        row[0]: {"id": row[0], "kind": row[1], "label": row[2], "properties": json.loads(row[3])}
    }
    edges = []
    # Only this claim's labels/evidence, then that evidence's source; never walk
    # backward through shared process labels into a different claim's outputs.
    sources = [identifier]
    for level in range(2):
        following = []
        for source in sources:
            allowed = (
                ["INPUT_LABEL", "PROCESS_LABEL", "OUTPUT_LABEL", "SUPPORTED_BY"]
                if level == 0
                else ["FROM_SOURCE"]
            )
            placeholders = ",".join("?" for _ in allowed)
            for edge in connection.execute(
                f"SELECT id,kind,source_id,target_id,properties FROM kg_edges WHERE source_id=? AND kind IN ({placeholders}) ORDER BY id",
                (source, *allowed),
            ):
                if level == 1 and edge[1] != "FROM_SOURCE":
                    continue
                target = connection.execute(
                    "SELECT id,kind,label,properties FROM kg_nodes WHERE id=?", (edge[3],)
                ).fetchone()
                nodes[target[0]] = {
                    "id": target[0],
                    "kind": target[1],
                    "label": target[2],
                    "properties": json.loads(target[3]),
                }
                edges.append(
                    {
                        "id": edge[0],
                        "kind": edge[1],
                        "source_id": edge[2],
                        "target_id": edge[3],
                        "properties": json.loads(edge[4]),
                    }
                )
                if target[1] == "Evidence":
                    following.append(target[0])
        sources = following
    return {
        "candidate_id": candidate_id,
        "claim_id": identifier,
        "raw_record": nodes[identifier]["properties"]["raw_record"],
        "nodes": list(nodes.values()),
        "edges": edges,
        "dispatch_approved": False,
    }
