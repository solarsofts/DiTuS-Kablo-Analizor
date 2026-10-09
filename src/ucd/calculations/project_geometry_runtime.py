"""Project-level geometry orchestration shared by UI, demos and headless runs."""

from __future__ import annotations

from copy import deepcopy

from ucd.calculations.bonding import BondingResult, solve_bonding
from ucd.calculations.installation_coupling import (
    attach_resolved_geometry_to_route_sections,
    synchronize_installation_geometry,
)
from ucd.calculations.thermal_route import (
    ThermalMaterializationResult,
    ThermalRouteInputError,
    materialize_route_sections_partial,
)
from ucd.models.project import ProjectData, RouteSection


def materialize_project_route_sections(
    project: ProjectData,
    *,
    strict: bool = True,
    mutate_project: bool = True,
) -> tuple[list[RouteSection], ThermalMaterializationResult]:
    """Synchronize once, materialize once and attach the same resolved x-y view.

    This closes the former split where thermal preprocessing consumed
    ``thermal_design`` while bonding consumed stale ``project.route_sections``.
    """

    synchronize_installation_geometry(project)
    result = materialize_route_sections_partial(project.thermal_design, project.cable)
    if strict and result.classification.all_errors:
        raise ThermalRouteInputError(
            "; ".join(issue.message for issue in result.classification.all_errors[:5])
        )
    sections = attach_resolved_geometry_to_route_sections(project, [deepcopy(item) for item in result.sections])
    if mutate_project:
        project.route_sections = [deepcopy(item) for item in sections]
    return sections, result



def resolve_project_bonding_route_sections(
    project: ProjectData,
    *,
    mutate_project: bool = True,
) -> list[RouteSection]:
    """Build a complete route-geometry view independent of thermal model scope.

    A slab/groundwater model-scope error may make an analytical thermal cell
    indeterminate, but it does not erase the physical route needed by IEEE 575.
    """

    synchronize_installation_geometry(project)
    partial = materialize_route_sections_partial(project.thermal_design, project.cable)
    by_region = {str(item.thermal_region_id): deepcopy(item) for item in partial.sections}
    resolved: list[RouteSection] = []
    for region in sorted((item for item in project.thermal_design.regions if item.enabled), key=lambda item: item.start_m):
        region_id = str(region.region_id)
        section = by_region.get(region_id)
        if section is None:
            section = RouteSection(
                name=f"{region_id} {region.name}",
                length_m=max(0.0, float(region.end_m) - float(region.start_m)),
                start_chainage_m=float(region.start_m),
                end_chainage_m=float(region.end_m),
                thermal_region_id=region_id,
                thermal_template_id=str(region.template_id),
                notes="Bonding route geometry: thermal model-scope statusundan bağımsız fiziksel segment.",
            )
        resolved.append(section)
    sections = attach_resolved_geometry_to_route_sections(project, resolved)
    if mutate_project:
        project.route_sections = [deepcopy(item) for item in sections]
    return sections

def solve_project_bonding(project: ProjectData) -> BondingResult:
    """Production/headless IEEE 575 entry point using synchronized route geometry."""

    sections = resolve_project_bonding_route_sections(project, mutate_project=True)
    return solve_bonding(project.cable, project.bonding, sections)
