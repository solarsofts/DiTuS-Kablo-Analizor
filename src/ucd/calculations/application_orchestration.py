"""Headless application-level orchestration for calculation workflows.

FAZ 7.2/7.3: calculation sequencing that used to live only in ``MainWindow``
is kept here so UI, regression tests and future CLI/CI entry points execute the
same production workflow.  This module deliberately imports no Qt classes.
"""

from __future__ import annotations

from dataclasses import dataclass

from ucd.calculations.project_geometry_runtime import (
    materialize_project_route_sections,
    solve_project_bonding,
)
from ucd.calculations.thermal_resistance import ThermalInputError, solve_section_thermal
from ucd.calculations.installation_coupling import physical_positions_for_region
from ucd.calculations.production_electrothermal import solve_production_electrothermal_study
from ucd.calculations.production_bonding import solve_production_bonding_study
from ucd.models.project import ProjectData


@dataclass(frozen=True)
class ThermalPreprocessorRun:
    results: tuple[object, ...]
    errors: tuple[str, ...]
    synchronized_section_count: int
    materialized_section_count: int

    @property
    def complete(self) -> bool:
        return bool(self.results) and not self.errors

    @property
    def conditional(self) -> bool:
        return bool(self.results) and bool(self.errors)


@dataclass(frozen=True)
class BondingProductionRun:
    electrothermal: object
    production: object
    legacy_diagnostic: object


def run_thermal_preprocessor(project: ProjectData) -> ThermalPreprocessorRun:
    """Materialize one route snapshot and solve all analytically eligible sections.

    Partial-route validation is preserved: a bad section does not suppress valid
    analytical preview/shadow results from other sections.
    """
    synchronized_sections, materialized = materialize_project_route_sections(
        project, strict=False, mutate_project=True
    )
    results: list[object] = []
    section_errors: list[str] = []
    for section in materialized.sections:
        try:
            results.append(
                solve_section_thermal(
                    project.cable,
                    section,
                    physical_positions_for_region(project, section.thermal_region_id),
                )
            )
        except ThermalInputError as exc:
            # Preserve the section identity.  The UI decides how to render the
            # error; headless callers receive the same deterministic payload.
            section_errors.append(f"{section.thermal_region_id}: {exc}")
    validation_errors = [
        f"{issue.region_id or 'GÜZERGÂH'}: {issue.code} — {issue.message}"
        for issue in materialized.classification.all_errors
    ]
    return ThermalPreprocessorRun(
        results=tuple(results),
        errors=tuple(validation_errors + section_errors),
        synchronized_section_count=len(synchronized_sections),
        materialized_section_count=len(materialized.sections),
    )


def run_bonding_production(project: ProjectData) -> BondingProductionRun:
    """Run the authoritative global bonding network plus legacy diagnostic view."""
    electrothermal = solve_production_electrothermal_study(project)
    production = solve_production_bonding_study(project, electrothermal_study=electrothermal)
    legacy = solve_project_bonding(project)
    return BondingProductionRun(
        electrothermal=electrothermal,
        production=production,
        legacy_diagnostic=legacy,
    )
