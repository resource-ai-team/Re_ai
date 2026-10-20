"""Callable agent boundary. MCP transport/orchestration are separate work."""

from resource_ai.api.schemas.graph import CandidateRequest
from resource_ai.core.dependencies import get_graph_query


def find_process_candidates(**structured_input) -> dict:
    request = CandidateRequest.model_validate(structured_input)
    return get_graph_query().search(request).model_dump()
