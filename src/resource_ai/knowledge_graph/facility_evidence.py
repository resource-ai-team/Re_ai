"""State separately what a published site claim establishes and what it does not."""


def assess_binding(transition, capability):
    basis = capability.basis if capability else "missing_facility"
    direct = basis == "published_site_process"
    return {
        "binding_basis": basis,
        "published_site_process": "source_support" if direct else "needs_verification",
        "specific_lot_specifications": "needs_verification",
        "current_permit_scope": "needs_verification",
        "current_acceptance": "needs_verification",
        "available_capacity": "needs_verification",
        "handoff_specifications": "needs_verification",
        "ready_for_dispatch": False,
        "source_ids": sorted(
            set(transition.source_ids + (capability.source_ids if capability else []))
        ),
        "next_action": (
            "공개 공정과 이번 로트의 규격을 대조하고 현행 허가·인수·용량을 확인"
            if direct
            else "해당 시설의 소재별 설비·공정 수행 근거를 먼저 확보하고 로트 조건을 대조"
        )
        if capability
        else "시설 후보와 공정 수행 근거를 확보",
    }
