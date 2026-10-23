"""Local-only LCI inspection and screening estimation CLI."""

import argparse
import json
from pathlib import Path

from resource_ai.calculators.lci import (
    candidate_from_plan,
    compare_candidates,
    evaluate_candidate,
    load_local_datasets,
)


def main():
    parser = argparse.ArgumentParser(description="KEITI PET LCI screening estimates")
    parser.add_argument(
        "command",
        choices=["inspect", "evaluate", "evaluate-kg"],
        help="Inspect installed LCI, evaluate explicit candidates, or adapt ProcessPlan records",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data/local/lci"),
        help="Directory holding locally obtained, licensed KEITI ZIP files",
    )
    parser.add_argument("--candidates", type=Path, help="JSON list of candidate objects")
    parser.add_argument("--material", default="PET", help="Only PET is currently supported")
    parser.add_argument("--waste-form", default=None, help="Explicit confirmed feedstock form")
    parser.add_argument("--input-mass-kg", default=None, help="Waste input quantity for KG plans")
    args = parser.parse_args()
    datasets = load_local_datasets(args.data)
    if not datasets:
        parser.error("No supported LCI ZIP files found in --data")
    if args.command == "inspect":
        result = [dataset.to_dict() for dataset in datasets]
    else:
        if args.candidates is None:
            parser.error("--candidates JSON path is required")
        rows = json.loads(args.candidates.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or not rows:
            parser.error("--candidates must contain a nonempty JSON list")
        if args.command == "evaluate-kg":
            if args.input_mass_kg is None:
                parser.error("--input-mass-kg is required for KG plans")
            rows = [
                candidate_from_plan(
                    row,
                    material=args.material,
                    input_mass_kg=args.input_mass_kg,
                    waste_form=args.waste_form,
                )
                for row in rows
            ]
        evaluations = [evaluate_candidate(row, datasets) for row in rows]
        result = {"results": evaluations, "comparison": compare_candidates(evaluations)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
