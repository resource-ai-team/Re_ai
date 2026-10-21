"""Build the three legal layers from exact, versioned official source sections."""

import hashlib
import re
from collections import defaultdict
from datetime import date, timedelta
from itertools import pairwise

from resource_ai.legal_graph.llm import PROMPT_VERSION, BudgetModel
from resource_ai.legal_graph.models import (
    BuildConfig,
    EvidenceSpan,
    GraphSnapshot,
    LegalEdge,
    LegalGraphError,
    LegalNode,
    identity,
)

FEATURES = ("actor", "conduct", "object", "context")


def feature_text(features):
    return "\n".join(f"{key}: {features.get(key, '')}" for key in FEATURES)


def iso_date(value):
    if value is None:
        return None
    text = str(value)
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return date.fromisoformat(text).isoformat()


def _outer_parts(text):
    depth, start = 0, 0
    for index, char in enumerate(text):
        depth += char in "(（"
        depth -= char in ")）"
        if char == "," and depth == 0:
            yield text[start:index].strip()
            start = index + 1
    yield text[start:].strip()


def build_structural_snapshot(documents, *, dataset_sha256):
    nodes, links, evidence = {}, {}, {}

    def edge(source, relation, target, evidence_ids, **data):
        key = source, relation, target
        if key in links:
            links[key].evidence_ids = sorted(set(links[key].evidence_ids + evidence_ids))
        else:
            links[key] = LegalEdge(
                source=source,
                relation=relation,
                target=target,
                evidence_ids=evidence_ids,
                data=data,
            )

    article_lookup = defaultdict(list)
    case_docs, interpretation_docs = [], []
    for doc in documents:
        if doc.document_type not in {"LAW", "PRECEDENT", "INTERPRETATION"}:
            raise LegalGraphError("unsupported-source", "공식 법령·판례 형식을 사용해 주세요.")
        groups = defaultdict(list)
        for index, section in enumerate(doc.sections):
            sid = identity(
                "section",
                [doc.source_id, index, hashlib.sha256(section.content.encode()).hexdigest()],
            )
            meta = {
                **section.metadata,
                "document_type": doc.document_type,
                "citation": section.citation,
                "source_org": doc.source_org,
            }
            raw_date = section.metadata.get("article_effective_date")
            effective = iso_date(raw_date) if raw_date else iso_date(doc.effective_from)
            span = EvidenceSpan(
                id=sid,
                source_id=doc.source_id,
                source_url=doc.source_url,
                title=doc.title,
                section_key=str(index),
                text=section.content,
                sha256=hashlib.sha256(section.content.encode()).hexdigest(),
                version=doc.version,
                effective_from=effective,
                metadata=meta,
            )
            evidence[sid] = span
            if doc.document_type == "LAW":
                annex = section.metadata.get("annex")
                article = section.metadata.get("article")
                group = f"별표 {annex}" if annex else article or section.heading
                groups[group].append(span)
            else:
                groups["case"].append(span)
        if doc.document_type == "LAW":
            for article, spans in groups.items():
                aid = identity("article", [doc.source_id, article])
                dates = sorted({s.effective_from.isoformat() for s in spans if s.effective_from})
                nodes[aid] = LegalNode(
                    id=aid,
                    kind="Article",
                    layers=["fact", "rule"],
                    label=f"{doc.title} {article}",
                    text="\n\n".join(s.text for s in spans),
                    evidence_ids=[s.id for s in spans],
                    data={
                        "source_id": doc.source_id,
                        "law_title": doc.title,
                        "article": article,
                        "version": doc.version,
                        "effective_from": iso_date(doc.effective_from),
                        "article_effective_dates": dates,
                        "table_structure_unverified": article.startswith("별표"),
                        "checklist_status": "not_generated",
                        "is_current": True,
                    },
                )
                article_lookup[doc.title, article].append(aid)
        elif doc.document_type == "INTERPRETATION":
            if not doc.version or not doc.effective_from:
                raise LegalGraphError(
                    "unversioned-interpretation", "공식 해석의 버전·적용 기준일이 필요합니다."
                )
            interpretation_docs.append((doc, groups["case"]))
        else:
            cid = identity("case", doc.source_id)
            spans = groups["case"]
            nodes[cid] = LegalNode(
                id=cid,
                kind="Case",
                layers=["fact", "ontology"],
                label=doc.title,
                text="\n\n".join(s.text for s in spans),
                evidence_ids=[s.id for s in spans],
                data={
                    "source_id": doc.source_id,
                    "case_number": doc.document_no or doc.metadata.get("case_number"),
                    "decision_date": iso_date(doc.metadata.get("decision_date")),
                    "feature_status": "not_extracted",
                },
            )
            case_docs.append((cid, spans))

    for aids in article_lookup.values():
        versions = sorted(aids, key=lambda aid: (nodes[aid].data["effective_from"] or "", aid))
        starts = [nodes[aid].data["effective_from"] for aid in versions]
        if len(starts) != len(set(starts)):
            raise LegalGraphError(
                "ambiguous-version", "같은 법령·조문·시행일에 여러 버전이 있어 확인이 필요합니다."
            )
        for current, following in pairwise(versions):
            if nodes[following].data["effective_from"]:
                nodes[current].data["effective_until"] = (
                    date.fromisoformat(nodes[following].data["effective_from"]) - timedelta(days=1)
                ).isoformat()

    for doc, spans in interpretation_docs:
        iid = identity("interpretation", doc.source_id)
        nodes[iid] = LegalNode(
            id=iid,
            kind="Interpretation",
            layers=["rule"],
            label=doc.title,
            text="\n\n".join(s.text for s in spans),
            evidence_ids=[s.id for s in spans],
            data={
                "source_id": doc.source_id,
                "version": doc.version,
                "version_verified": doc.metadata.get("version_verified") is True,
                "link_basis": "curated_explicit_article_references_not_model_generated",
            },
        )
        for reference in doc.metadata.get("article_references", []):
            # Full source/version identity required; bare article numbers cannot bind two laws.
            aid = identity("article", [reference["source_id"], reference["article"]])
            if aid not in nodes or nodes[aid].kind != "Article":
                raise LegalGraphError(
                    "unresolved-interpretation",
                    "공식 해석이 가리키는 법령 버전·조문이 적재되지 않았습니다.",
                )
            edge(aid, "HAS_INTERPRETATION", iid, [s.id for s in spans])

    law_names = sorted({title for title, _ in article_lookup}, key=len, reverse=True)
    for cid, spans in case_docs:
        for span in spans:
            kind = span.metadata.get("section_type")
            if kind == "참조판례":
                for match in re.finditer(r"\d{4}[가-힣]{1,3}\d+", span.text):
                    raw = match.group()
                    rid = identity("case-reference", [cid, span.id, match.start()])
                    nodes[rid] = LegalNode(
                        id=rid,
                        kind="Reference",
                        layers=["fact"],
                        label=raw,
                        text=raw,
                        evidence_ids=[span.id],
                        data={
                            "kind": "unresolved_case",
                            "start": match.start(),
                            "end": match.end(),
                            "resolved": False,
                        },
                    )
                    edge(cid, "CITES_REFERENCE", rid, [span.id])
            if kind != "참조조문":
                continue
            law, article, historical = None, None, False
            for number, part in enumerate(_outer_parts(span.text)):
                plain = re.sub(r"\([^)]*\)", "", part)
                match = re.search(r"제\d+조(?:의\d+)?", plain)
                if match:
                    prefix = plain[: match.start()].strip().strip("「」『』")
                    historical_prefix = prefix.startswith("구 ")
                    normalized = prefix.removeprefix("구 ").strip().strip("「」『』")
                    if prefix:
                        law = normalized if normalized in law_names else None
                        historical = historical_prefix
                    article = match.group()
                rid = identity("reference", [cid, span.id, number, part])
                nodes[rid] = LegalNode(
                    id=rid,
                    kind="Reference",
                    layers=["fact", "rule"],
                    label=part,
                    text=part,
                    evidence_ids=[span.id],
                    data={
                        "kind": "article_reference",
                        "law_title": law,
                        "article": article,
                        "historical": historical,
                        "version_resolved": False,
                        "source_offset": span.text.find(part),
                    },
                )
                edge(cid, "CITES_REFERENCE", rid, [span.id])
                aids = article_lookup.get((law, article), [])
                if not historical:
                    for aid in aids:
                        edge(
                            rid,
                            "POSSIBLE_CURRENT_ARTICLE"
                            if len(aids) == 1
                            else "POSSIBLE_ARTICLE_VERSION",
                            aid,
                            [span.id],
                            version_resolved=False,
                        )
                        edge(
                            cid,
                            "CITES",
                            aid,
                            [span.id],
                            version_resolved=False,
                            basis="textual_reference_not_applicability_or_version_verification",
                        )
                correspondence = re.search(r"현행\s+(제\d+조(?:의\d+)?(?:\s*제\d+항)?)", part)
                if correspondence:
                    target = identity("correspondence", [rid, correspondence.group()])
                    nodes[target] = LegalNode(
                        id=target,
                        kind="Reference",
                        layers=["rule"],
                        label=correspondence.group(),
                        text=correspondence.group(),
                        evidence_ids=[span.id],
                        data={"kind": "current_correspondence", "version_resolved": False},
                    )
                    edge(rid, "CURRENT_CORRESPONDENCE", target, [span.id])
    return GraphSnapshot(
        dataset_sha256=dataset_sha256,
        nodes=list(nodes.values()),
        edges=list(links.values()),
        evidence=list(evidence.values()),
        metadata={
            "enrichment_complete": False,
            "remaining_model_stages": [
                "case_features",
                "checklists",
                "issue_anchors",
                "communities",
            ],
            "origin": "official_source_structure_not_model_inference",
            "prompt_version": PROMPT_VERSION,
            "interpretation_scope": "Only explicit interpretation documents; no case-summary conversion",
            "paper_adaptation": "Korean regulatory issues replace Chinese criminal charges; source versions remain explicit",
        },
    )


def build_snapshot(documents, embeddings, model, *, dataset_sha256, config=None):
    config = config or BuildConfig()
    snapshot = build_structural_snapshot(documents, dataset_sha256=dataset_sha256)
    nodes, evidence = snapshot.node_map(), snapshot.evidence_map()
    edges = list(snapshot.edges)
    bounded = BudgetModel(model, config.max_model_calls)

    def link(a, relation, b, eids=(), **data):
        if not any(e.source == a and e.target == b and e.relation == relation for e in edges):
            edges.append(
                LegalEdge(source=a, relation=relation, target=b, evidence_ids=list(eids), data=data)
            )

    def issue(label, eids):
        iid = identity("issue", label)
        if iid not in nodes:
            nodes[iid] = LegalNode(
                id=iid,
                kind="Issue",
                layers=["fact", "ontology"],
                label=label,
                text=label,
                evidence_ids=list(eids),
                data={"basis": "model_extracted_hypothesis"},
            )
        else:
            nodes[iid].evidence_ids = sorted(set(nodes[iid].evidence_ids + list(eids)))
        return iid

    for node in list(nodes.values()):
        spans = [evidence[eid].model_dump(mode="json") for eid in node.evidence_ids]
        if node.kind == "Case":
            result = bounded.complete(
                "extract_case",
                {
                    "case": {"id": node.id, "label": node.label, "text": node.text},
                    "evidence": spans,
                },
            )
            if not set(result["evidence_ids"]) <= set(node.evidence_ids):
                raise LegalGraphError(
                    "model-grounding", "사례 특징의 근거가 원문과 연결되지 않습니다."
                )
            node.data.update(features=result["features"], feature_status="model_extracted")
            for key in FEATURES:
                if result["features"][key]:
                    fid = identity("feature", [node.id, key, result["features"][key]])
                    nodes[fid] = LegalNode(
                        id=fid,
                        kind="Feature",
                        layers=["ontology"],
                        label=key,
                        text=result["features"][key],
                        evidence_ids=result["evidence_ids"],
                        data={"axis": key},
                    )
                    link(node.id, "HAS_FEATURE", fid, result["evidence_ids"])
            for label in result["issues"]:
                link(
                    node.id,
                    "HAS_ISSUE",
                    issue(label, result["evidence_ids"]),
                    result["evidence_ids"],
                )
        elif node.kind == "Article" and not node.data["table_structure_unverified"]:
            result = bounded.complete(
                "extract_checklist",
                {
                    "article": {"id": node.id, "label": node.label, "text": node.text},
                    "evidence": spans,
                    "max_items": config.max_checklist_items,
                },
            )
            if len(result["items"]) > config.max_checklist_items:
                raise LegalGraphError("model-budget", "체크리스트 항목 한도를 초과했습니다.")
            for index, item in enumerate(result["items"]):
                span = evidence.get(item["evidence_id"])
                if (
                    span is None
                    or span.id not in node.evidence_ids
                    or item["quote"] not in span.text
                ):
                    raise LegalGraphError(
                        "model-grounding", "체크리스트 인용 구간이 원문에 없습니다."
                    )
                start = span.text.index(item["quote"])
                kid = identity("checklist", [node.id, index, item])
                nodes[kid] = LegalNode(
                    id=kid,
                    kind="ChecklistItem",
                    layers=["rule"],
                    label=item["question"],
                    text=item["question"],
                    evidence_ids=[span.id],
                    data={
                        **item,
                        "start": start,
                        "end": start + len(item["quote"]),
                        "basis": "model_extracted_not_expert_validated",
                        "exhaustive": False,
                    },
                )
                link(node.id, "HAS_CHECKLIST", kid, [span.id])
            node.data["checklist_status"] = "model_extracted_not_exhaustiveness_verified"
            for label in result["issues"]:
                link(node.id, "RELATED_ISSUE", issue(label, node.evidence_ids), node.evidence_ids)
    cases = sorted((n for n in nodes.values() if n.kind == "Case"), key=lambda n: n.id)
    if not cases:
        raise LegalGraphError("no-cases", "사례 기반 검색에 필요한 판례가 없습니다.")
    vectors = embeddings.embed_documents([feature_text(n.data["features"]) for n in cases])
    for node, vector in zip(cases, vectors, strict=True):
        node.vector = vector
    import igraph as ig
    import leidenalg as la

    from resource_ai.legal_graph.researcher import cosine

    similarities = {}
    for i, case in enumerate(cases):
        ranked = sorted(
            ((cosine(case.vector, other.vector), j) for j, other in enumerate(cases) if i != j),
            reverse=True,
        )
        for score, j in ranked[: config.knn_k]:
            if score > 0:
                similarities[tuple(sorted((i, j)))] = score
                link(case.id, "SIMILAR_TO", cases[j].id, similarity=score)
    graph = ig.Graph(n=len(cases), edges=list(similarities), directed=False)
    groups = (
        la.find_partition(
            graph,
            la.ModularityVertexPartition,
            weights=list(similarities.values()) or None,
            seed=config.seed,
        )
        if len(cases) > 1 and similarities
        else [[i] for i in range(len(cases))]
    )
    for members in groups:
        members = sorted(members)
        member_ids = [cases[i].id for i in members]
        member_issues = sorted(
            {
                nodes[e.target].label
                for e in edges
                if e.source in member_ids and e.relation == "HAS_ISSUE"
            }
        )
        summary = bounded.complete(
            "summarize_community",
            {
                "case_ids": member_ids,
                "features": [cases[i].data["features"] for i in members],
                "issues": member_issues,
            },
        )["summary"]
        cid = identity("community", member_ids)
        eids = sorted({eid for i in members for eid in cases[i].evidence_ids})
        nodes[cid] = LegalNode(
            id=cid,
            kind="Community",
            layers=["ontology"],
            label="사례 커뮤니티",
            text=summary,
            evidence_ids=eids,
            data={"case_ids": member_ids, "method": "Leiden", "single_case_limit": len(cases) == 1},
        )
        for member in member_ids:
            link(member, "BELONGS_TO", cid)
    to_embed = [n for n in nodes.values() if n.kind in {"Issue", "Community"}]
    if to_embed:
        for node, vector in zip(
            to_embed, embeddings.embed_documents([n.text for n in to_embed]), strict=True
        ):
            node.vector = vector
    snapshot.nodes, snapshot.edges = list(nodes.values()), edges
    snapshot.metadata.update(
        enrichment_complete=True,
        remaining_model_stages=[],
        community_method="Leiden",
        single_case_limit=len(cases) == 1,
        construction_model=bounded.name,
        embedding_model=getattr(embeddings, "model_name", type(embeddings).__name__),
        embedding_dimension=embeddings.dimension,
        model_calls=bounded.calls,
        missing_checklists=[
            n.id
            for n in nodes.values()
            if n.kind == "Article" and n.data["checklist_status"] == "not_generated"
        ],
        checklist_exhaustiveness_verified=False,
        prompt_version=PROMPT_VERSION,
    )
    return GraphSnapshot.model_validate(snapshot.model_dump(mode="json"))
