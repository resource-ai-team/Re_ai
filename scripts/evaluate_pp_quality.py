"""Reproducible synthetic A/B/C evidence flow, no cloud calls or customer documents."""

import json
from pathlib import Path

from resource_ai.material_quality.service import (
    add_document_claims,
    add_inputs,
    assess,
    confirm,
    set_scenario,
)


def run():
    source = {
        "source_type": "operator_reported",
        "same_lot_confirmed": True,
        "sample_id": "synthetic-sample",
        "synthetic": True,
        "meaning": "lot_property",
    }
    claims = [
        {**source, "evidence_id": "synthetic-material", "field": "material", "text_value": "PP"},
        {
            **source,
            "evidence_id": "synthetic-form",
            "field": "material_form",
            "text_value": "rigid_scrap",
        },
        {
            **source,
            "evidence_id": "synthetic-mass",
            "field": "quantity_kg",
            "normalized_value": "1000",
            "unit": "kg",
        },
    ]
    target = {
        "specification_id": "synthetic-target",
        "name": "가상 목표",
        "version": "1",
        "jurisdiction": "KR",
        "forms": ["rigid_scrap"],
        "applies_to": "input_waste",
        "requirement_type": "scenario_target",
        "source": "synthetic; not a domestic threshold",
        "explicitly_selected": True,
        "applicability_confirmed": True,
        "limits": [{"field": "contamination", "max_value": "5"}],
    }
    results = {}
    for scenario in ("A_document", "B_manual", "C_conflict"):
        state = {
            "tenant_id": "synthetic-tenant",
            "case_id": "synthetic-lot",
            "revision": 1,
            "inputs": {"mode": "simulation", "assessment_on": "2026-10-29"},
        }
        add_inputs(state, {"sample_id": "synthetic-sample", "claims": claims}, "synthetic-operator")
        if scenario != "B_manual":
            layout = {
                "synthetic": True,
                "pages": [
                    {
                        "page": 1,
                        "text": "PP 시험성적서\n시료 ID: synthetic-sample\n습윤 질량 기준\n오염률 20 wt%",
                    }
                ],
            }
            report = add_document_claims(state, "synthetic-doc", layout)
            if scenario == "A_document":
                confirm(
                    state,
                    {
                        "evidence_ids": [report["claims"][0]["evidence_id"]],
                        "sample_id": "synthetic-sample",
                        "same_lot_confirmed": True,
                    },
                    "synthetic-reviewer",
                    "2026-10-29",
                )
            else:
                add_inputs(
                    state,
                    {
                        "claims": [
                            {
                                **source,
                                "evidence_id": "synthetic-operator-contamination",
                                "field": "contamination",
                                "normalized_value": "10",
                                "unit": "wt%",
                                "basis": "wet_mass",
                            }
                        ]
                    },
                    "synthetic-operator",
                )
        set_scenario(state, {"scenario": {"target": target}}, "synthetic-operator")
        results[scenario] = assess(state)
    assert results["A_document"]["level"] == 2
    assert results["B_manual"]["level"] == 1
    assert results["C_conflict"]["profile"]["conflicts"]
    return {
        "synthetic": True,
        "cloud_calls": 0,
        "industrial_accuracy_evaluation": False,
        "scenarios": results,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/evaluation/pp-quality-synthetic.json")
    args = parser.parse_args()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run(), ensure_ascii=False, indent=2) + "\n", "utf-8")
    print("Synthetic A/B/C verified; output:", path)
