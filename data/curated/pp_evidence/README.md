# PP-specific external evidence pack (2026-10-29)

Scope: new source facts not found in the existing Re_ai develop branch reference/data listings; no existing dataset modified. This is **not** an operational recommendation engine or a verified industrial process model.

## Files

- `rema_pp_small_rigid_2023.json`: selected independently structured factual thresholds for a **foreign PP small-rigid buyer guideline**. Source attribution required; do not treat as domestic legal requirement, automatic acceptance rule, or plant specification.
- `tno_pp_process_metrics_2025.csv`: **9** reported metrics/values from Kasper et al., Waste Management 191 (2025), DOI 10.1016/j.wasman.2024.11.029. Exact source context matters; 85% yield and stage losses are referenced to **pure PP input**, not input mixed PP bale mass; do not double-count overlapping losses.
- `external_dataset_catalog_2026-10.json`: new online datasets and precise acquisition state. **The KECO October CSV and two Zenodo ZIPs are not stored here**; no actual PP prices or pixel-level training labels have been invented.

## Integration boundaries

1. `material_quality/`: OCR or manual values must retain source type, evidence reference, units, method, extraction confidence and sample identity. Raw OCR confidence is not a measurement's scientific validity.
2. `knowledge_graph/` or `rules/`: PP spec thresholds as *optional* target criteria (user-selected context), not an enforceable Korean standard; require tests for prohibited materials and lot applicability.
3. New `calculators/mass_balance.py`: deterministic PP/contaminant mass balance; TNO metrics are **study-case baselines**, not per-facility default rates. Missing contamination values => conditional scenario only.
4. `calculators/scenarios.py`: KECO 2026-10 price data only **after real CSV retrieval** and grade/region/time mapping; cannot equate average recycling material price with a signed quotation.
5. `vision/`: RECLAIM images require explicit bulk download and source-specific preprocessing; object visual identity cannot establish waste composition in wt%.

## Availability and licenses

- **Curated in this repository**: factual numerical thresholds and published research values, with source links and caution flags.
- **Metadata verified, bytes NOT acquired**: KECO 2026-10 CSV (26 rows/8 regions), RECLAIM PP/PS archive (1.6 GB), 2026 PP laboratory-recyclate ZIP bundles (22.7 MB).
- **Do not copy into Git**: industrial documents, private test reports, uncertified quotations, protected raw corpora, or any archive without license review. Keep bulk downloads in `data/local/`/Azure Blob Storage and store hashes/provenance in Git.

## Primary sources

- ReMA spec: https://www.isrispecs.org/orpheus_resource/pp-small-rigid-plastic/
- Kasper et al.: https://doi.org/10.1016/j.wasman.2024.11.029
- KECO: https://www.data.go.kr/data/3076421/fileData.do
- RECLAIM PP/PS: https://zenodo.org/records/11504659
- PP laboratory recyclate: https://zenodo.org/records/19370161

Before making any result numeric in an API, verify unit, denominator, material state, data recency, provenance, and required license.
