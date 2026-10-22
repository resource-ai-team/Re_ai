"""Callable agent boundary. MCP transport/orchestration are separate work."""

from resource_ai.api.schemas.graph import CandidateRequest
from resource_ai.api.schemas.kg_planning import PathPlanningRequest
from resource_ai.api.schemas.kg_pool import CandidatePoolRequest
from resource_ai.core.dependencies import get_graph_query


def find_process_candidates(**structured_input) -> dict:
    request = CandidateRequest.model_validate(structured_input)
    return get_graph_query().search(request).model_dump()


def get_candidate_pool_page(**structured_input) -> dict:
    """Agent-facing view with blocked evidence and customer context withheld.

    Follow pagination.next_cursor until null before claiming enumeration is complete.
    The legacy find_process_candidates remains an explicitly limited lookup.
    """
    request = CandidatePoolRequest.model_validate(
        {**structured_input, "audience": "external_model"}
    )
    return get_graph_query().candidate_pool(request).model_dump()


def get_process_plan_page(**structured_input) -> dict:
    """Read graph-compatible paths, never claim permission, price or recommendation.

    Check pagination AND search_scope bounds before claiming complete enumeration.
    """
    request = PathPlanningRequest.model_validate({**structured_input, "audience": "external_model"})
    return get_graph_query().process_plans(request).model_dump()
