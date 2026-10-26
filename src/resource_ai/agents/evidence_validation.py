"""KG reference를 로컬 Technical RAG 근거와 연결하고 미확인을 보존한다."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

from resource_ai.api.schemas.evidence_validation import (
    CandidateEvidenceValidation,
    EvidenceAssessment,
    RagEvidenceHit,
    ReferenceSearch,
    ReferenceValidationRequest,
    ReferenceValidationResponse,
)
from resource_ai.evidence.models import SearchGroup, SearchResult
from resource_ai.knowledge_graph.graph_store import integrity
from resource_ai.knowledge_graph.ontology import identity

_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_EXPLICIT_STATUSES = {"SUPPORTED", "CONFLICT", "SCOPE_MISMATCH"}

LIMITATIONS = [
    "KG reference와 DOI는 조사 후보이며 논문 내용의 지지를 자동으로 뜻하지 않습니다.",
    "DOI·URL 일치만으로 SUPPORTED를 부여하지 않고 검수된 연결 메타데이터를 요구합니다.",
    "원문 이용 권한이 없는 자료는 RAG에 저장하지 않으며 출처 식별자만 유지합니다.",
    "UNKNOWN 또는 충돌이 남은 후보는 PASS 처리하지 않습니다.",
]


def _url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.query, "")
    )


def _dois(*values: object) -> set[str]:
    found: set[str] = set()
    for value in values:
        if isinstance(value, str):
            found.update(match.group(0).rstrip(".,;)").lower() for match in _DOI_RE.finditer(value))
        elif isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
            found.update(_dois(*value))
    return found


def _metadata(result: SearchResult) -> dict:
    metadata = result.metadata or {}
    document = metadata.get("document") if isinstance(metadata.get("document"), dict) else {}
    chunk = metadata.get("chunk") if isinstance(metadata.get("chunk"), dict) else {}
    return {**document, **chunk}


def _reference(evidence: dict) -> EvidenceAssessment:
    properties = evidence.get("properties") or evidence
    source = evidence.get("source") or {}
    source_metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    reference = properties.get("reference_raw") or properties.get("reference")
    doi_candidates = sorted(
        _dois(
            reference,
            properties.get("doi_candidates", []),
            source_metadata.get("doi_candidates", []),
        )
    )
    source_url = (
        properties.get("source_url") or source.get("url") or source_metadata.get("source_url")
    )
    return EvidenceAssessment(
        kg_evidence_id=evidence["evidence_id"],
        reference=reference,
        doi_candidates=doi_candidates,
        kg_source_url=source_url,
        locator=properties.get("locator"),
        reasons=["reference_not_yet_verified"],
    )


def _query(packet: dict, assessment: EvidenceAssessment, *, retry: bool) -> str:
    source_record = packet.get("source_record") or {}
    metadata = source_record.get("metadata") if isinstance(source_record, dict) else {}
    parts = [
        metadata.get("waste"),
        metadata.get("transforming_process"),
        metadata.get("transformed_resource"),
        assessment.reference,
        " ".join(assessment.doi_candidates),
    ]
    if retry:
        parts.extend([assessment.kg_source_url, assessment.locator, "원문 적용 범위 반례"])
    return " ".join(str(part).strip() for part in parts if part).strip()


def _linked_status(assessment: EvidenceAssessment, result: SearchResult) -> tuple[str, str]:
    metadata = _metadata(result)
    linked_ids = metadata.get("kg_evidence_ids", [])
    if isinstance(linked_ids, str):
        linked_ids = [linked_ids]
    explicit = str(metadata.get("kg_claim_status", "")).upper()
    if assessment.kg_evidence_id in linked_ids and explicit in _EXPLICIT_STATUSES:
        return explicit, "reviewed_explicit_kg_link"

    reference_dois = set(assessment.doi_candidates)
    hit_dois = _dois(
        result.source_url, result.citation, result.source_position, metadata.get("doi")
    )
    same_url = _url(assessment.kg_source_url) == _url(result.source_url) and _url(result.source_url)
    if reference_dois & hit_dois or same_url:
        return "UNKNOWN", "same_reference_not_semantically_audited"
    return "UNKNOWN", "retrieved_without_verified_kg_link"


def _hit(result: SearchResult) -> RagEvidenceHit:
    return RagEvidenceHit(
        chunk_id=result.chunk_id,
        title=result.title,
        source_url=result.source_url,
        citation=result.citation,
        source_position=result.source_position,
        retrieval_method=result.retrieval_method,
    )


def _merge(assessment: EvidenceAssessment, results: list[SearchResult], search_id: str) -> None:
    assessment.search_ids.append(search_id)
    statuses = []
    for result in results:
        status, reason = _linked_status(assessment, result)
        statuses.append(status)
        assessment.reasons.append(reason)
        assessment.rag_chunk_ids.append(result.chunk_id)
        assessment.rag_source_urls.append(result.source_url)
        locator = result.citation or result.source_position
        if locator:
            assessment.citations.append(locator)
    if "CONFLICT" in statuses:
        assessment.status = "CONFLICT"
    elif "SCOPE_MISMATCH" in statuses:
        assessment.status = "SCOPE_MISMATCH"
    elif "SUPPORTED" in statuses:
        assessment.status = "SUPPORTED"
    assessment.reasons = sorted(set(assessment.reasons))
    assessment.rag_chunk_ids = sorted(set(assessment.rag_chunk_ids))
    assessment.rag_source_urls = sorted(set(assessment.rag_source_urls))
    assessment.citations = sorted(set(assessment.citations))


class ReferenceValidationService:
    def __init__(self, graph, repository):
        self.graph = graph
        self.repository = repository

    def _search(self, query: str, top_k: int) -> tuple[str, list[SearchResult]]:
        try:
            results = self.repository.search(query, top_k, search_group=SearchGroup.TECHNICAL)
            return ("retrieved" if results else "no_hits"), results
        except Exception:  # noqa: BLE001 - 제공자 오류를 UNKNOWN 근거 공백으로 보존한다.
            return "unavailable", []

    def run(self, request: ReferenceValidationRequest) -> ReferenceValidationResponse:
        cursor = None
        pages = 0
        graph_trace = None
        candidates: list[CandidateEvidenceValidation] = []
        searches: list[ReferenceSearch] = []
        search_count = 0

        for _ in range(request.max_pages):
            page = self.graph.candidate_pool(request.query.model_copy(update={"cursor": cursor}))
            pages += 1
            if graph_trace is None:
                graph_trace = page.graph_trace
            elif graph_trace != page.graph_trace:
                raise integrity("조회 중 KG 세대가 변경되었습니다.")

            for item in page.items:
                packet = item.research_packet
                if packet is None:
                    candidates.append(
                        CandidateEvidenceValidation(
                            candidate_id=item.candidate_id,
                            candidate_kind=item.kind,
                            status="UNKNOWN",
                            route="REVIEW",
                            blockers=["kg_evidence_withheld"],
                        )
                    )
                    continue
                assessments = [_reference(row) for row in packet.get("evidence", [])]
                for assessment in assessments:
                    for attempt in range(request.max_critic_retries + 1):
                        query = _query(packet, assessment, retry=attempt > 0)
                        search_id = identity(
                            "reference-search",
                            [item.candidate_id, assessment.kg_evidence_id, attempt, query],
                        )
                        if search_count >= request.max_reference_queries or not query:
                            status, results = "budget_exhausted", []
                        else:
                            search_count += 1
                            status, results = self._search(query, request.technical_top_k)
                        searches.append(
                            ReferenceSearch(
                                search_id=search_id,
                                kg_evidence_id=assessment.kg_evidence_id,
                                query=query or assessment.kg_evidence_id,
                                attempt=attempt,
                                status=status,
                                results=[_hit(result) for result in results],
                            )
                        )
                        _merge(assessment, results, search_id)
                        if assessment.status != "UNKNOWN" or status in {
                            "unavailable",
                            "budget_exhausted",
                        }:
                            break

                statuses = {row.status for row in assessments}
                if "CONFLICT" in statuses:
                    status, route = "CONFLICT", "REVISE"
                elif "SCOPE_MISMATCH" in statuses:
                    status, route = "SCOPE_MISMATCH", "REVIEW"
                elif assessments and statuses == {"SUPPORTED"}:
                    status, route = "SUPPORTED", "PASS"
                else:
                    status, route = "UNKNOWN", "REVIEW"
                candidates.append(
                    CandidateEvidenceValidation(
                        candidate_id=item.candidate_id,
                        candidate_kind=item.kind,
                        evidence=assessments,
                        status=status,
                        route=route,
                        blockers=[] if route == "PASS" else ["kg_reference_not_resolved"],
                    )
                )

            cursor = page.pagination.next_cursor
            if bool(cursor) != page.pagination.has_more:
                raise integrity("KG 후보 조회 커서와 has_more가 일치하지 않습니다.")
            if not cursor:
                break

        routes = {candidate.route for candidate in candidates}
        route = "REVISE" if "REVISE" in routes else "PASS" if routes == {"PASS"} else "REVIEW"
        return ReferenceValidationResponse(
            graph_trace=graph_trace or {},
            coverage={
                "pages_read": pages,
                "candidate_count": len(candidates),
                "reference_count": sum(len(candidate.evidence) for candidate in candidates),
                "search_count": search_count,
                "next_cursor": cursor,
                "complete": cursor is None,
            },
            candidates=candidates,
            searches=searches,
            route=route,
            limitations=LIMITATIONS,
        )
