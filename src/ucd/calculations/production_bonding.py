"""FAZ 6.6 production bonding authority.

The production bonding view is not a second electromagnetic solver. It is a
scenario-oriented projection of the already authoritative closed-loop global
N-core/N-sheath network used by the electro-thermal production calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable

from ucd.calculations.production_electrothermal import (
    ProductionElectroThermalStudyResult,
    solve_production_electrothermal_study,
)
from ucd.models.project import ProjectData


REFERENCE = (
    "IEEE 575 route bonding; global N-core/N-sheath/link-box/GCC network; "
    "scenario-resolved electro-thermal production operating point"
)


@dataclass(frozen=True)
class ProductionBondingScenarioResult:
    scenario_id: str
    scenario_name: str
    circuit_currents_a: tuple[tuple[str, float], ...]
    deenergized_circuit_ids: tuple[str, ...]
    converged: bool
    methods_agree: bool
    maximum_sheath_current_a: float | None
    maximum_sheath_to_earth_voltage_v: float | None
    maximum_sheath_to_sheath_voltage_v: float | None
    maximum_gcc_current_a: float | None
    total_sheath_metal_loss_w: float | None
    lambda1: float | None
    loss_vector_fingerprint: str
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True)
class ProductionBondingStudyResult:
    scenarios: tuple[ProductionBondingScenarioResult, ...]
    reference: str = REFERENCE
    authority: str = "PRODUCTION_GLOBAL_N_CORE_N_SHEATH"
    legacy_role: str = "DIAGNOSTIC_ONLY"

    @property
    def complete(self) -> bool:
        return bool(self.scenarios) and all(item.converged and item.methods_agree for item in self.scenarios)

    def trace_lines(self) -> list[str]:
        lines = [
            "DiTuS — FAZ 6.6 Üretim Bonding Otoritesi",
            f"Otorite={self.authority}",
            f"Referans={self.reference}",
            "Legacy üç-loop BondingResult üretim otoritesi değildir; yalnız tanısal/karşılaştırma görünümüdür.",
        ]
        for item in self.scenarios:
            currents = ", ".join(f"{cid}={amps:.3f} A" for cid, amps in item.circuit_currents_a)
            lines.append(
                f"{item.scenario_id}: {currents}; out={','.join(item.deenergized_circuit_ids) or 'none'}; "
                f"Ish,max={item.maximum_sheath_current_a if item.maximum_sheath_current_a is not None else float('nan'):.6f} A; "
                f"Vsh-e,max={item.maximum_sheath_to_earth_voltage_v if item.maximum_sheath_to_earth_voltage_v is not None else float('nan'):.6f} V; "
                f"lambda1={item.lambda1 if item.lambda1 is not None else float('nan'):.9f}; "
                f"converged={item.converged}; methods_agree={item.methods_agree}"
            )
        return lines


def governing_lambda1(
    legacy_lambda1: float | None,
    production: ProductionBondingStudyResult | None,
) -> tuple[float, str] | None:
    """Largest finite λ1 of production scenarios and the legacy diagnostic view.

    Production scenarios come first so that an equal legacy value does not
    take over the reported source.
    """
    candidates: list[tuple[float, str]] = []
    if production is not None:
        for item in production.scenarios:
            if item.lambda1 is not None and isfinite(item.lambda1):
                candidates.append((float(item.lambda1), f"üretim senaryosu {item.scenario_id}"))
    if legacy_lambda1 is not None and isfinite(legacy_lambda1):
        candidates.append((float(legacy_lambda1), "legacy tanısal üç-loop"))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])


def lambda1_criterion_warning(
    legacy_lambda1: float | None,
    production: ProductionBondingStudyResult | None,
    maximum_lambda1: float,
) -> str | None:
    governing = governing_lambda1(legacy_lambda1, production)
    if governing is None or governing[0] <= float(maximum_lambda1):
        return None
    value, source = governing
    return f"λ1={value:.6f} ({source}), proje kriteri {float(maximum_lambda1):.6f} üzerinde."


def project_production_bonding_study(
    electrothermal: ProductionElectroThermalStudyResult,
) -> ProductionBondingStudyResult:
    rows: list[ProductionBondingScenarioResult] = []
    for solved in electrothermal.scenarios:
        scenario = solved.scenario
        currents = tuple((state.circuit_id, float(state.phase_current_a)) for state in scenario.circuit_states)
        outages = tuple(state.circuit_id for state in scenario.circuit_states if not state.energized)
        if solved.coupled_result is None:
            rows.append(ProductionBondingScenarioResult(
                scenario.scenario_id, scenario.scenario_name, currents, outages,
                False, False, None, None, None, None, None, None,
                solved.loss_vector_fingerprint, solved.error_code, solved.error_message,
            ))
            continue
        em = solved.coupled_result.final_global_em
        rows.append(ProductionBondingScenarioResult(
            scenario.scenario_id,
            scenario.scenario_name,
            currents,
            outages,
            bool(solved.converged),
            bool(em.methods_agree),
            float(em.maximum_sheath_current_a),
            float(em.maximum_sheath_to_earth_voltage_v),
            float(em.maximum_sheath_to_sheath_voltage_v),
            float(em.maximum_gcc_current_a),
            float(em.total_sheath_metal_loss_w),
            float(em.lambda1) if em.total_core_metal_loss_w > 1e-15 else None,
            solved.loss_vector_fingerprint,
            solved.error_code,
            solved.error_message,
        ))
    return ProductionBondingStudyResult(tuple(rows))


def solve_production_bonding_study(
    project: ProjectData,
    *,
    electrothermal_study: ProductionElectroThermalStudyResult | None = None,
    maximum_iterations: int = 20,
) -> ProductionBondingStudyResult:
    study = electrothermal_study or solve_production_electrothermal_study(
        project, maximum_iterations=maximum_iterations
    )
    return project_production_bonding_study(study)
