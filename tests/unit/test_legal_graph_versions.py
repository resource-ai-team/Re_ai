"""Synthetic version/citation records; not evidence of legal applicability accuracy."""

from dataclasses import replace
from datetime import date

import pytest

from resource_ai.evidence.models import LegalDocument, LegalSection
from resource_ai.legal_graph.builder import build_snapshot, build_structural_snapshot
from resource_ai.legal_graph.models import LegalGraphError
from resource_ai.legal_graph.service import LegalGraphService
from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider
from tests.legal_graph_fixtures import MemoryStore, SyntheticModel, documents


def versions(review=None):
    docs = documents()
    old = docs[0]
    if review is not None:
        old = replace(old, metadata={**old.metadata, "legal_period_review": review})
    latest = replace(
        old,
        source_id="synthetic-law-v2",
        version="synthetic-v2",
        effective_from=date(2024, 1, 1),
        metadata={"synthetic": True},
    )
    return [old, *docs[1:], latest]


def complete_period():
    return {
        "status": "reviewed_complete",
        "history_complete": True,
        "effective_from": "2020-01-01",
        "effective_until": "2023-12-31",
        "reviewed_at": "2026-10-22",
        "source_url": "https://www.law.go.kr/example-test-only",
        "locator": "Synthetic reviewed version period; not real legal evidence",
    }


@pytest.mark.parametrize("mutation", ["effective_from", "version", "content", "title", "metadata"])
def test_duplicate_source_with_conflicting_payload_cannot_overwrite(mutation):
    original = documents()[0]
    changes = {
        "effective_from": {"effective_from": date(2024, 1, 1)},
        "version": {"version": "different-version"},
        "content": {"sections": [replace(original.sections[0], content="다른 원문")]},
        "title": {"title": "다른 법령"},
        "metadata": {"metadata": {"synthetic": True, "mst": "another-view"}},
    }
    with pytest.raises(LegalGraphError) as exc:
        build_structural_snapshot(
            [original, replace(original, **changes[mutation])], dataset_sha256="a" * 64
        )
    assert exc.value.code == "source-identity-conflict"


def test_identical_duplicate_source_requires_explicit_deduplication():
    original = documents()[0]
    with pytest.raises(LegalGraphError) as exc:
        build_structural_snapshot([original, original], dataset_sha256="a" * 64)
    assert exc.value.code == "duplicate-source"


def test_same_mst_and_promulgation_with_different_effective_views_cannot_share_source_id():
    first = replace(
        documents()[0],
        version="20100723",
        effective_from=date(2010, 7, 23),
        metadata={"mst": "106853", "promulgation_date": "20100723"},
    )
    second = replace(first, effective_from=date(2011, 7, 24))
    with pytest.raises(LegalGraphError) as exc:
        build_structural_snapshot([first, second], dataset_sha256="a" * 64)
    assert exc.value.code == "source-identity-conflict"


def test_sparse_history_bound_is_search_only_and_old_period_is_unverified():
    snapshot = build_structural_snapshot(versions(), dataset_sha256="a" * 64)
    old = [
        n
        for n in snapshot.nodes
        if n.kind == "Article" and n.data["source_id"] == "synthetic-law-v1"
    ]
    assert len(old) == 3
    assert all(
        n.data["effective_until"] == n.data["search_effective_until"] == "2023-12-31" for n in old
    )
    assert all(
        n.data["effective_until_basis"] == "next_collected_snapshot_search_bound_not_legal_validity"
        for n in old
    )
    assert all(
        n.data["required_version_verified"] is False and n.data["legal_period_verified"] is False
        for n in old
    )
    assert snapshot.metadata["complete_legal_history_assumed"] is False


def test_sparse_historical_candidate_cannot_become_applicable_even_if_model_agrees():
    embeddings, model = HashEmbeddingProvider(32), SyntheticModel()
    snapshot = build_snapshot(versions(), embeddings, model, dataset_sha256="a" * 64)
    model.calls.clear()
    result = LegalGraphService(MemoryStore(snapshot), embeddings, model).run(
        "가상 과거 위탁 검토", {"as_of": "2023-01-01", "permit_confirmed": True}
    )
    assert len(result["research"]["historical_period_review_required_ids"]) == 3
    assert len(result["audit"]["unknown_article_ids"]) == 3
    assert not result["audit"]["verified_article_ids"]
    assert all(r["error_code"] == "unresolved-version" for r in result["audit"]["articles"])
    assert all(task not in {"audit_item", "audit_article", "adjudicate"} for task, _ in model.calls)


def test_explicit_reviewed_complete_period_can_be_evaluated_without_inferred_validity():
    embeddings, model = HashEmbeddingProvider(32), SyntheticModel()
    snapshot = build_snapshot(
        versions(complete_period()), embeddings, model, dataset_sha256="a" * 64
    )
    result = LegalGraphService(MemoryStore(snapshot), embeddings, model).run(
        "가상 과거 위탁 검토", {"as_of": "2023-01-01", "permit_confirmed": True}
    )
    assert len(result["audit"]["verified_article_ids"]) == 3
    assert not result["research"]["historical_period_review_required_ids"]
    assert all(
        c["legal_period_verified"] is True
        for aid, c in result["research"]["article_version_checks"].items()
        if aid in result["research"]["article_ids"]
    )
    assert result["dispatch_approved"] is False and result["route"] == "REVIEW"


@pytest.mark.parametrize(
    "changes",
    [
        {"history_complete": False},
        {"effective_from": "2019-01-01"},
        {"effective_until": "2019-01-01"},
        {"source_url": "https://untrusted.example/period"},
        {"source_url": 42},
        {"source_url": "https://www.law.go.kr:bad/period"},
        {"locator": ""},
        {"reviewed_at": "not-a-date"},
    ],
)
def test_incomplete_or_invalid_period_attestation_does_not_enable_old_article(changes):
    review = {**complete_period(), **changes}
    with pytest.raises(LegalGraphError) as exc:
        build_structural_snapshot(versions(review), dataset_sha256="a" * 64)
    assert exc.value.code == "invalid-period-review"


def test_reviewed_period_cannot_overlap_the_next_known_snapshot():
    with pytest.raises(LegalGraphError) as exc:
        build_structural_snapshot(
            versions({**complete_period(), "effective_until": "2024-01-01"}),
            dataset_sha256="a" * 64,
        )
    assert exc.value.code == "conflicting-period-review"


def case(number, decision, *, source_id, references=None, metadata_number=None, version=None):
    return LegalDocument(
        source_id=source_id,
        document_type="PRECEDENT",
        title=f"가상 판례 {number}",
        document_no=number,
        version=version or decision.replace("-", ""),
        source_url="https://www.law.go.kr/example-test-only",
        source_org="가상 법원",
        metadata={
            "synthetic": True,
            "decision_date": decision,
            "case_number": metadata_number or number,
        },
        sections=[
            LegalSection(heading="가상 사실", content="가상 판례의 사실", citation="가상 사실"),
            *(
                [
                    LegalSection(
                        heading="참조판례",
                        content=references,
                        citation="가상 참조",
                        metadata={"section_type": "참조판례"},
                    )
                ]
                if references
                else []
            ),
        ],
    )


def citation_pair(reference="대법원 2021. 8. 26. 선고 2019다226548 판결"):
    return [
        case("2023두62830", "2026-01-15", source_id="synthetic-citing", references=reference),
        case("2019다226548", "2021-08-26", source_id="synthetic-cited"),
    ]


def test_loaded_reference_resolves_exact_case_and_date_without_asserting_applicability():
    snapshot = build_structural_snapshot(citation_pair(), dataset_sha256="a" * 64)
    edge = next(e for e in snapshot.edges if e.relation == "RESOLVES_CASE")
    reference, target = snapshot.node_map()[edge.source], snapshot.node_map()[edge.target]
    assert reference.kind == "Reference" and target.kind == "Case"
    assert (
        target.data["case_number"] == reference.label == edge.data["case_number"] == "2019다226548"
    )
    assert target.data["decision_date"] == reference.data["decision_date"] == "2021-08-26"
    assert reference.data["resolved"] is True
    assert reference.data["applicability_verified"] is edge.data["applicability_verified"] is False
    assert reference.data["version_resolved"] is edge.data["version_resolved"] is False
    assert set(target.evidence_ids) <= set(edge.evidence_ids)
    assert not any(e.relation == "APPLIES" for e in snapshot.edges)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_date",
        "missing_date",
        "longer_number",
        "metadata_number",
        "version_date",
        "future_reference",
        "ambiguous_case",
    ],
)
def test_case_reference_requires_exact_number_date_and_unique_loaded_identity(mutation):
    docs = citation_pair()
    if mutation == "wrong_date":
        docs = citation_pair("대법원 2021. 8. 25. 선고 2019다226548 판결")
    elif mutation == "missing_date":
        docs = citation_pair("대법원 2019다226548 판결")
    elif mutation == "longer_number":
        docs = citation_pair("대법원 2021. 8. 26. 선고 2019다2265480 판결")
    elif mutation == "metadata_number":
        docs[1] = replace(docs[1], metadata={**docs[1].metadata, "case_number": "2019다226549"})
    elif mutation == "version_date":
        docs[1] = replace(docs[1], version="20210825")
    elif mutation == "future_reference":
        docs = citation_pair("대법원 2027. 8. 26. 선고 2019다226548 판결")
        docs[1] = case("2019다226548", "2027-08-26", source_id="synthetic-cited")
    elif mutation == "ambiguous_case":
        docs.append(replace(docs[1], source_id="synthetic-duplicate-case-number"))
    snapshot = build_structural_snapshot(docs, dataset_sha256="a" * 64)
    assert not any(e.relation == "RESOLVES_CASE" for e in snapshot.edges)
    refs = [n for n in snapshot.nodes if n.kind == "Reference"]
    assert refs and all(n.data["resolved"] is False for n in refs)


def test_cites_relation_stays_version_unresolved_after_reference_case_resolution():
    docs = documents()
    citing, cited = citation_pair()
    citing = replace(
        citing,
        sections=[
            *citing.sections,
            LegalSection(
                heading="참조조문",
                content="가상 폐자원법 제17조",
                citation="가상 조문",
                metadata={"section_type": "참조조문"},
            ),
        ],
    )
    snapshot = build_structural_snapshot([docs[0], citing, cited], dataset_sha256="a" * 64)
    assert any(e.relation == "RESOLVES_CASE" for e in snapshot.edges)
    cites = [e for e in snapshot.edges if e.relation == "CITES"]
    assert cites and all(e.data["version_resolved"] is False for e in cites)
