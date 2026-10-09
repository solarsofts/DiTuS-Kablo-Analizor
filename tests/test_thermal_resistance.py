from __future__ import annotations

import math

from ucd.calculations.thermal_resistance import (
    ThermalInputError,
    cylindrical_layer_resistance_km_w,
    resolve_external_thermal_resistance,
    resolve_internal_thermal_resistance,
)
from ucd.models.project import (
    EXTERNAL_THERMAL_AUTO,
    EXTERNAL_THERMAL_MANUAL,
    EXTERNAL_THERMAL_MIXED,
    INTERNAL_THERMAL_AUTO,
    INTERNAL_THERMAL_MANUAL,
    CableData,
    RouteSection,
)


def test_cylindrical_layer_formula() -> None:
    value = cylindrical_layer_resistance_km_w(40.0, 80.0, 3.5)
    expected = 3.5 / (2.0 * math.pi) * math.log(2.0)
    assert math.isclose(value, expected, rel_tol=1e-12)


def test_auto_internal_geometry_produces_positive_t1_t2_t3() -> None:
    cable = CableData(internal_thermal_mode=INTERNAL_THERMAL_AUTO)
    result = resolve_internal_thermal_resistance(cable)
    assert result.t1_km_w > result.t2_km_w > 0
    assert result.t3_km_w > 0
    assert result.mode == INTERNAL_THERMAL_AUTO


def test_manual_internal_values_are_preserved() -> None:
    cable = CableData(
        internal_thermal_mode=INTERNAL_THERMAL_MANUAL,
        thermal_resistance_t1_km_w=0.31,
        thermal_resistance_t2_km_w=0.12,
        thermal_resistance_t3_km_w=0.06,
    )
    result = resolve_internal_thermal_resistance(cable)
    assert (result.t1_km_w, result.t2_km_w, result.t3_km_w) == (0.31, 0.12, 0.06)


def test_auto_external_includes_mutual_heating() -> None:
    cable = CableData(arrangement="Flat", overall_diameter_mm=100.0)
    section = RouteSection(
        "Flat",
        10.0,
        burial_depth_m=1.2,
        soil_thermal_resistivity_km_w=1.0,
        external_thermal_mode=EXTERNAL_THERMAL_AUTO,
        phase_spacing_m=0.2,
    )
    result = resolve_external_thermal_resistance(cable, section)
    assert len(result.phase_t4_km_w) == 3
    self_term = result.matrix_km_w[1][1]
    assert result.effective_t4_km_w > self_term
    assert result.phase_t4_km_w[1] >= result.phase_t4_km_w[0]


def test_deeper_burial_increases_external_resistance() -> None:
    cable = CableData(arrangement="Single")
    shallow = resolve_external_thermal_resistance(
        cable,
        RouteSection("S", 1.0, burial_depth_m=0.8, external_thermal_mode=EXTERNAL_THERMAL_AUTO),
    )
    deep = resolve_external_thermal_resistance(
        cable,
        RouteSection("D", 1.0, burial_depth_m=2.0, external_thermal_mode=EXTERNAL_THERMAL_AUTO),
    )
    assert deep.effective_t4_km_w > shallow.effective_t4_km_w


def test_manual_external_value_is_preserved() -> None:
    cable = CableData()
    section = RouteSection(
        "Manual", 1.0, external_thermal_mode=EXTERNAL_THERMAL_MANUAL,
        external_thermal_resistance_t4_km_w=1.234,
    )
    result = resolve_external_thermal_resistance(cable, section)
    assert result.effective_t4_km_w == 1.234


def test_overlapping_cables_are_rejected() -> None:
    cable = CableData(arrangement="Flat", overall_diameter_mm=105.0)
    section = RouteSection(
        "Overlap", 1.0, external_thermal_mode=EXTERNAL_THERMAL_AUTO, phase_spacing_m=0.05,
    )
    try:
        resolve_external_thermal_resistance(cable, section)
    except ThermalInputError:
        pass
    else:
        raise AssertionError("ThermalInputError expected")


def test_touching_trefoil_cables_are_valid_not_overlap() -> None:
    cable = CableData(arrangement="Trefoil", overall_diameter_mm=105.0)
    section = RouteSection(
        "Touching trefoil",
        1.0,
        burial_depth_m=1.2,
        external_thermal_mode=EXTERNAL_THERMAL_AUTO,
        phase_spacing_m=0.105,
    )
    result = resolve_external_thermal_resistance(cable, section)
    assert len(result.positions_m) == 3
    for i, first in enumerate(result.positions_m):
        for second in result.positions_m[i + 1:]:
            assert math.isclose(math.hypot(first[0] - second[0], first[1] - second[1]), 0.105, abs_tol=1e-12)


def test_mixed_zone_with_poorer_backfill_requires_nodal_solution() -> None:
    cable = CableData(arrangement="Trefoil", overall_diameter_mm=100.0)
    section = RouteSection(
        "Poor backfill",
        1.0,
        section_type="DIRECT_BURIED",
        burial_depth_m=1.0,
        soil_thermal_resistivity_km_w=1.0,
        external_thermal_mode=EXTERNAL_THERMAL_MIXED,
        phase_spacing_m=0.1,
        backfill_thermal_resistivity_km_w=1.5,
        backfill_effective_radius_m=0.3,
    )
    try:
        resolve_external_thermal_resistance(cable, section)
    except ThermalInputError as exc:
        assert "MIXED_ZONE_POOR_BACKFILL_REQUIRES_NODAL" in str(exc)
    else:
        raise AssertionError("Poor-backfill mixed-zone model must fail closed")


def test_mixed_zone_negative_diagonal_is_rejected_instead_of_clamped() -> None:
    cable = CableData(arrangement="Single", overall_diameter_mm=100.0)
    section = RouteSection(
        "Over-correction",
        1.0,
        section_type="DIRECT_BURIED",
        burial_depth_m=0.2,
        soil_thermal_resistivity_km_w=5.0,
        external_thermal_mode=EXTERNAL_THERMAL_MIXED,
        phase_spacing_m=0.1,
        backfill_thermal_resistivity_km_w=0.01,
        backfill_effective_radius_m=20.0,
    )
    try:
        resolve_external_thermal_resistance(cable, section)
    except ThermalInputError as exc:
        assert "MIXED_ZONE_NEGATIVE_RESISTANCE" in str(exc)
    else:
        raise AssertionError("Negative corrected resistance must not be silently clamped")


def test_beton_hendek_is_not_misclassified_as_direct_buried() -> None:
    section = RouteSection(
        "Concrete trench",
        1.0,
        section_type="Beton hendek",
        external_thermal_mode=EXTERNAL_THERMAL_AUTO,
    )
    try:
        resolve_external_thermal_resistance(CableData(), section)
    except ThermalInputError as exc:
        assert "ANALYTIC_MODEL_SCOPE_REQUIRES_NODAL" in str(exc)
    else:
        raise AssertionError("Concrete trench must require nodal or manual T4")
