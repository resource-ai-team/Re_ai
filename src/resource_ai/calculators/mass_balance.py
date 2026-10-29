"""Deterministic two-component PP balances on a declared wet-input mass basis.

No study coefficient, facility rate or environmental factor is supplied by this module.
"""

from decimal import Decimal, localcontext


def drying_balance(mass, initial_pct, target_pct):
    mass, initial_pct, target_pct = map(Decimal, (mass, initial_pct, target_pct))
    if mass <= 0 or not 0 <= target_pct <= initial_pct < 100:
        raise ValueError("invalid wet-basis drying inputs")
    with localcontext() as ctx:
        ctx.prec = 40
        solids = mass * (1 - initial_pct / 100)
        final = solids / (1 - target_pct / 100)
        return {
            "dry_solids_kg": str(solids),
            "final_wet_mass_kg": str(final),
            "water_removal_kg": str(mass - final),
            "energy_kwh": None,
            "scope": "Water and nonvolatile solids only; no solids loss. Not oil removal, PP purity or actual dryer performance.",
        }


def minimum_removal(mass, contamination_pct, target_pct, pp_loss_pct=Decimal(0)):
    """Ideal selective removal lower bound, not a process yield prediction."""
    mass, contamination_pct, target_pct, pp_loss_pct = map(
        Decimal, (mass, contamination_pct, target_pct, pp_loss_pct)
    )
    if mass <= 0 or not all(0 <= x < 100 for x in (contamination_pct, target_pct, pp_loss_pct)):
        raise ValueError("invalid two-component mass balance")
    with localcontext() as ctx:
        ctx.prec = 40
        pp = mass * (1 - contamination_pct / 100)
        impurity = mass - pp
        pp_loss = pp * pp_loss_pct / 100
        retained_pp = pp - pp_loss
        retained_impurity = min(impurity, retained_pp * target_pct / (100 - target_pct))
        output = retained_pp + retained_impurity
        removed = impurity - retained_impurity
        return {
            k: str(v)
            for k, v in {
                "input_mass_kg": mass,
                "input_pp_kg": pp,
                "input_contaminant_kg": impurity,
                "pp_loss_kg": pp_loss,
                "minimum_contaminant_removal_kg": removed,
                "output_mass_upper_bound_kg": output,
                "residue_kg": mass - output,
                "required_removal_efficiency_pct": removed / impurity * 100
                if impurity
                else Decimal(0),
            }.items()
        }


def forward_balance(mass, contamination_pct, stages):
    with localcontext() as ctx:
        ctx.prec = 40
        total = Decimal(mass)
        pp = total * (1 - Decimal(contamination_pct) / 100)
        contaminant = total - pp
        rows = []
        for stage in stages:
            before = pp + contaminant
            pp_loss = pp * stage.pp_loss_pct / 100
            removed = contaminant * stage.contaminant_removal_pct / 100
            pp -= pp_loss
            contaminant -= removed
            rows.append(
                {
                    "stage_id": stage.stage_id,
                    "process": stage.process,
                    "input_kg": str(before),
                    "output_kg": str(pp + contaminant),
                    "pp_loss_kg": str(pp_loss),
                    "contaminant_removed_kg": str(removed),
                    "residue_kg": str(pp_loss + removed),
                    "evidence_id": stage.evidence_id,
                }
            )
        output = pp + contaminant
        return {
            "stages": rows,
            "output_kg": str(output),
            "pp_kg": str(pp),
            "contaminant_kg": str(contaminant),
            "residue_kg": str(total - output),
            "output_contamination_pct": str(contaminant / output * 100) if output else None,
            "mass_conserved": abs(
                total - output - sum((Decimal(r["residue_kg"]) for r in rows), Decimal(0))
            )
            < Decimal("1e-25"),
        }
