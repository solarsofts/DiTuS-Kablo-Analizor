"""Independent IEC/IEEE closed-form validation helpers for cross bonding.

Production remains the explicit primitive/global network.  These helpers are
verification oracles only and deliberately compare longitudinal metallic
sheath I²R loss; they do not claim to represent the IEC local sheath-eddy loss
component.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import sqrt

from ucd.calculations.primitive_cim import solve_primitive_network
from ucd.models.project import BONDING_CROSS, BONDING_SOLID_BOTH_END, CableData, BondingSystemData, RouteSection


CLOSED_FORM_APPLICABLE = "APPLICABLE"
CLOSED_FORM_NOT_APPLICABLE = "CLOSED_FORM_NOT_APPLICABLE"


def unequal_minor_cross_bonding_loss_ratio(p: float, q: float) -> float:
    """IEC 60287-1-1 2.3.6.2 / IEEE 575-2014 6.7.3.1 Eq.(1).

    Minor lengths are a, p*a and q*a.  The result is the circulating-loss ratio
    cross-bonded / solid-bonded for the stated ideal applicability conditions.
    """
    p = float(p); q = float(q)
    if p <= 0.0 or q <= 0.0:
        raise ValueError("p ve q sıfırdan büyük olmalıdır.")
    den = (1.0 + p + q) ** 2
    return (1.0 + p*p + q*q - p - q - p*q) / den


def unequal_minor_cross_bonding_loss_ratio_from_lengths(lengths: tuple[float, float, float]) -> float:
    if len(lengths) != 3 or min(lengths) <= 0.0:
        raise ValueError("Üç pozitif minor-section uzunluğu gerekir.")
    a = min(float(v) for v in lengths)
    normalized = sorted(float(v) / a for v in lengths)
    return unequal_minor_cross_bonding_loss_ratio(normalized[1], normalized[2])


def modified_sectionalized_standing_voltage_factor() -> float:
    """IEEE 575-2014 Annex D.4 ideal benchmark: sqrt(3)/2."""
    return sqrt(3.0) / 2.0


@dataclass(frozen=True)
class ClosedFormNetworkCheck:
    applicability: str
    expected_ratio: float | None
    network_ratio: float | None
    absolute_error: float | None
    cross_sheath_i2r_w: float | None
    solid_sheath_i2r_w: float | None
    note: str


def compare_primitive_network_to_closed_form(
    cable: CableData,
    bonding: BondingSystemData,
    routes: list[RouteSection],
) -> ClosedFormNetworkCheck:
    """Run the same explicit network as cross- and solid-bonded and compare.

    Applicability is intentionally narrow: one classic three-minor major
    section, CROSS_BONDED source topology and equilateral/trefoil geometry.
    """
    minors = list(bonding.minor_sections)
    arrangement = str(getattr(cable, "arrangement", "")).strip().upper()
    if bonding.scheme != BONDING_CROSS or len(minors) != 3 or arrangement not in {"TREFOIL", "EQUILATERAL"}:
        return ClosedFormNetworkCheck(
            CLOSED_FORM_NOT_APPLICABLE, None, None, None, None, None,
            "Oracle yalnız klasik tek-major/üç-minor sectionalized cross-bonding ve eşkenar geometri için etkin.",
        )
    lengths = tuple(float(m.length_m) for m in minors)
    expected = unequal_minor_cross_bonding_loss_ratio_from_lengths(lengths)
    cross_model = deepcopy(bonding)
    cross_model.scheme = BONDING_CROSS
    solid_model = deepcopy(bonding)
    solid_model.scheme = BONDING_SOLID_BOTH_END
    cross = solve_primitive_network(cable, cross_model, routes)
    solid = solve_primitive_network(cable, solid_model, routes)
    if solid.total_sheath_metal_loss_w <= 1e-15:
        return ClosedFormNetworkCheck(
            CLOSED_FORM_NOT_APPLICABLE, expected, None, None,
            cross.total_sheath_metal_loss_w, solid.total_sheath_metal_loss_w,
            "Solid-bonded longitudinal sheath I²R paydası sıfıra yakın.",
        )
    ratio = cross.total_sheath_metal_loss_w / solid.total_sheath_metal_loss_w
    return ClosedFormNetworkCheck(
        CLOSED_FORM_APPLICABLE, expected, ratio, abs(ratio - expected),
        cross.total_sheath_metal_loss_w, solid.total_sheath_metal_loss_w,
        "Karşılaştırma yalnız longitudinal metallic sheath I²R bileşenidir; yerel sheath eddy-current λ1'' dahil değildir.",
    )
