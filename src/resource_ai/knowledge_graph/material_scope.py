"""Keep a search family distinct from the supplied resin grade."""

from resource_ai.knowledge_graph.ontology import normalize_label

PE_GRADES = {
    "hdpe": ("hdpe", "고밀도폴리에틸렌"),
    "ldpe": ("ldpe", "저밀도폴리에틸렌"),
    "lldpe": ("lldpe", "선형저밀도폴리에틸렌"),
    "mdpe": ("mdpe", "중밀도폴리에틸렌"),
}


def material_scope(label, family):
    """An alias is a user's assertion, never proof of a route's grade compatibility."""
    normalized = normalize_label(label or "").replace(" ", "")
    subtype = next((key for key, aliases in PE_GRADES.items() if normalized in aliases), None)
    return {
        "search_family": family,
        "supplied_subtype": subtype,
        "subtype_basis": "user_supplied_alias" if subtype else "not_supplied_or_not_resolved",
        "candidate_scope": "material_family_only",
        "subtype_applicability_verified": False,
    }
