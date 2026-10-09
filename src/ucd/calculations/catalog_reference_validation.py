"""FAZ 6.8 catalog reference-rating normalization and physical-model comparison.

No IEC correction-factor table values are embedded here.  A catalog current
rating is normalized only when either the project condition already equals the
catalog reference condition or an explicit, traceable factor is supplied in
``CableCatalogRecord.reference_conditions['correction_factors']``.

This keeps licensed/national/manufacturer table data outside the open package
while making provenance and incompleteness machine-readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose
from typing import Any, Iterable

from ucd.models.project import CableCatalogRecord, ProjectData, RouteSection


REFERENCE = (
    "IEC 60287-3-1 site reference conditions; catalog ratings remain tied to their "
    "published reference conditions. DiTuS does not embed proprietary correction tables."
)

_VERIFIED_FACTOR_SOURCES = {
    "STANDARD_TABLE",
    "NATIONAL_TABLE",
    "MANUFACTURER",
    "MANUFACTURER_VERIFIED",
    "TEST_VERIFIED",
    "USER_VERIFIED",
    "LICENSED_STANDARD_USER_ENTRY",
}

_CONDITIONAL_FACTOR_SOURCES = {
    "ASSUMPTION",
    "ENGINEERING_ASSUMPTION",
    "SYNTHETIC_DEMO",
}

_NUMERIC_PARAMETERS = {
    "soil_temperature_c",
    "burial_depth_m",
    "soil_thermal_resistivity_km_w",
}

_CATEGORICAL_PARAMETERS = {
    "arrangement",
    "installation_method",
    "grouping_parallel",
}


@dataclass(frozen=True)
class CatalogCorrectionFactor:
    factor_id: str
    parameter: str
    factor: float
    reference_value: object
    target_value: object
    source_type: str
    source_reference: str
    source_id: str = ""
    status: str = "VERIFIED"


@dataclass(frozen=True)
class CatalogReferenceRegionResult:
    region_id: str
    region_name: str
    reference_ampacity_per_cable_a: float
    arithmetic_total_ampacity_a: float
    combined_factor: float | None
    adjusted_total_ampacity_a: float | None
    status: str
    applied_factors: tuple[CatalogCorrectionFactor, ...]
    missing_parameters: tuple[str, ...]
    trace: tuple[str, ...]


@dataclass(frozen=True)
class CatalogReferenceValidationResult:
    status: str
    reference_ampacity_per_cable_a: float
    target_parallel_cables_per_phase: int
    arithmetic_total_ampacity_a: float
    governing_adjusted_ampacity_a: float | None
    governing_region_id: str
    source_verified: bool
    regions: tuple[CatalogReferenceRegionResult, ...]
    warnings: tuple[str, ...]
    trace: tuple[str, ...]
    physical_model_ampacity_a: float | None = None
    physical_minus_catalog_a: float | None = None
    physical_minus_catalog_percent: float | None = None
    physical_comparison_status: str = "NOT_AVAILABLE"


@dataclass(frozen=True)
class _TargetCondition:
    region_id: str
    region_name: str
    soil_temperature_c: float | None
    burial_depth_m: float | None
    soil_thermal_resistivity_km_w: float | None
    arrangement: str
    installation_method: str
    grouping_parallel: int


def _normalize_arrangement(value: object) -> str:
    text = str(value or "").strip().upper()
    if "TREFOIL" in text:
        return "TREFOIL"
    if "FLAT" in text:
        return "FLAT"
    if "VERTICAL" in text:
        return "VERTICAL"
    if "CUSTOM" in text:
        return "CUSTOM"
    return text or "UNKNOWN"


def _normalize_installation(value: object) -> str:
    text = str(value or "").strip().upper()
    if "DIRECT" in text or "BURIED" in text:
        return "DIRECT_BURIED"
    if "DUCT" in text:
        return "DUCT_BANK"
    if "HDD" in text:
        return "HDD"
    if "TROUGH" in text:
        return "CONCRETE_TROUGH"
    if "TUNNEL" in text:
        return "TUNNEL"
    if text == "AIR" or "FREE AIR" in text:
        return "AIR"
    return "UNKNOWN"


def _reference_arrangement(ampacity_key: str, conditions: dict[str, Any]) -> str:
    key = str(ampacity_key).lower()
    # Arrangement-specific rating fields are stronger evidence than one global
    # reference-condition note.
    if "trefoil" in key:
        return "TREFOIL"
    if "flat" in key:
        return "FLAT"
    explicit = conditions.get("arrangement") or conditions.get("formation")
    if explicit:
        return _normalize_arrangement(explicit)
    return "UNKNOWN"


def _reference_installation(ampacity_key: str, conditions: dict[str, Any]) -> str:
    explicit = conditions.get("installation_method") or conditions.get("installation_profile")
    if explicit:
        return _normalize_installation(explicit)
    key = str(ampacity_key).lower()
    if "ground" in key:
        return "DIRECT_BURIED"
    if "air" in key:
        return "AIR"
    return "UNKNOWN"


def _target_conditions(project: ProjectData, parallel: int) -> tuple[_TargetCondition, ...]:
    rows: list[_TargetCondition] = []
    route_sections = [item for item in project.route_sections if float(item.length_m) > 0]
    if route_sections:
        for index, section in enumerate(route_sections, 1):
            arrangement = _normalize_arrangement(
                getattr(section, "resolved_arrangement", "") or project.design_basis.installation_profile
            )
            installation = _normalize_installation(getattr(section, "section_type", ""))
            if installation == "UNKNOWN":
                installation = _normalize_installation(project.design_basis.installation_profile)
            rows.append(_TargetCondition(
                region_id=str(section.thermal_region_id or section.name or f"ROUTE-{index:03d}"),
                region_name=str(section.name or f"Güzergâh {index}"),
                soil_temperature_c=float(section.ambient_temperature_c),
                burial_depth_m=float(section.burial_depth_m),
                soil_thermal_resistivity_km_w=float(section.soil_thermal_resistivity_km_w),
                arrangement=arrangement,
                installation_method=installation,
                grouping_parallel=int(parallel),
            ))
        return tuple(rows)
    basis = project.design_basis
    return (_TargetCondition(
        region_id="DESIGN_BASIS",
        region_name="Ön tasarım koşulu",
        soil_temperature_c=None,
        burial_depth_m=float(basis.burial_depth_m) if basis.burial_depth_m > 0 else None,
        soil_thermal_resistivity_km_w=(
            float(basis.soil_thermal_resistivity_km_w)
            if basis.soil_thermal_resistivity_km_w > 0 else None
        ),
        arrangement=_normalize_arrangement(basis.installation_profile),
        installation_method=_normalize_installation(basis.installation_profile),
        grouping_parallel=int(parallel),
    ),)


def _parse_factors(conditions: dict[str, Any]) -> tuple[CatalogCorrectionFactor, ...]:
    raw = conditions.get("correction_factors", [])
    if isinstance(raw, dict):
        raw = [dict(value, factor_id=str(key)) if isinstance(value, dict) else {} for key, value in raw.items()]
    if not isinstance(raw, list):
        return ()
    result: list[CatalogCorrectionFactor] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            continue
        parameter = str(item.get("parameter", "")).strip()
        try:
            factor = float(item.get("factor", 0.0))
        except (TypeError, ValueError):
            continue
        if parameter not in _NUMERIC_PARAMETERS | _CATEGORICAL_PARAMETERS or factor <= 0:
            continue
        source_type = str(item.get("source_type", "")).strip().upper()
        source_reference = str(item.get("source_reference", "")).strip()
        if source_type in _VERIFIED_FACTOR_SOURCES and source_reference:
            status = "VERIFIED"
        elif source_type in _CONDITIONAL_FACTOR_SOURCES:
            status = "CONDITIONAL"
        else:
            status = "UNVERIFIED"
        result.append(CatalogCorrectionFactor(
            factor_id=str(item.get("factor_id", f"CF-{index:03d}")),
            parameter=parameter,
            factor=factor,
            reference_value=item.get("reference_value"),
            target_value=item.get("target_value"),
            source_type=source_type or "UNSPECIFIED",
            source_reference=source_reference,
            source_id=str(item.get("source_id", "")),
            status=status,
        ))
    return tuple(result)


def _same_numeric(a: object, b: object) -> bool:
    try:
        return isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _same_value(parameter: str, a: object, b: object) -> bool:
    if parameter in _NUMERIC_PARAMETERS or parameter == "grouping_parallel":
        return _same_numeric(a, b)
    if parameter == "arrangement":
        return _normalize_arrangement(a) == _normalize_arrangement(b)
    if parameter == "installation_method":
        return _normalize_installation(a) == _normalize_installation(b)
    return str(a) == str(b)


def _factor_for(
    factors: Iterable[CatalogCorrectionFactor],
    parameter: str,
    reference_value: object,
    target_value: object,
) -> CatalogCorrectionFactor | None:
    for item in factors:
        if item.parameter != parameter:
            continue
        if _same_value(parameter, item.reference_value, reference_value) and _same_value(
            parameter, item.target_value, target_value
        ):
            return item
    return None


def validate_catalog_reference_rating(
    record: CableCatalogRecord,
    project: ProjectData,
    *,
    reference_ampacity_per_cable_a: float,
    ampacity_key: str,
    target_parallel_cables_per_phase: int,
    physical_model_ampacity_a: float | None = None,
) -> CatalogReferenceValidationResult:
    """Normalize one catalog rating to route conditions when traceable factors exist.

    ``physical_model_ampacity_a`` is intentionally optional and should only be
    supplied for the same applied cable snapshot / parallel-count as ``record``.
    The comparison is descriptive; it is not a tolerance-based acceptance gate.
    """

    ampacity = float(reference_ampacity_per_cable_a)
    parallel = max(1, int(target_parallel_cables_per_phase))
    arithmetic_total = ampacity * parallel if ampacity > 0 else 0.0
    conditions = dict(record.reference_conditions or {})
    factors = _parse_factors(conditions)
    warnings: list[str] = []
    trace = [REFERENCE]

    if ampacity <= 0:
        return CatalogReferenceValidationResult(
            "REFERENCE_MISSING", ampacity, parallel, arithmetic_total, None, "", False, (),
            ("Katalog referans ampacity değeri yok.",), tuple(trace), physical_model_ampacity_a,
        )

    ref_load_factor = conditions.get("load_factor")
    if ref_load_factor is None:
        warnings.append("REFERENCE_LOAD_FACTOR_MISSING")
    elif not _same_numeric(ref_load_factor, 1.0):
        warnings.append("CYCLIC_REFERENCE_NOT_STEADY_STATE")
        trace.append(
            f"Katalog yük faktörü={ref_load_factor}; IEC 60287 steady-state (%100 LF) ile skaler faktör üzerinden eşlenmedi."
        )

    reference_values: dict[str, object] = {
        "soil_temperature_c": conditions.get("soil_temperature_c"),
        "burial_depth_m": conditions.get("burial_depth_m"),
        "soil_thermal_resistivity_km_w": conditions.get("soil_thermal_resistivity_km_w"),
        "arrangement": _reference_arrangement(ampacity_key, conditions),
        "installation_method": _reference_installation(ampacity_key, conditions),
        "grouping_parallel": int(conditions.get("cables_per_phase", 1) or 1),
    }
    for key in ("soil_temperature_c", "burial_depth_m", "soil_thermal_resistivity_km_w"):
        if reference_values[key] is None:
            warnings.append(f"REFERENCE_CONDITION_MISSING:{key}")

    region_results: list[CatalogReferenceRegionResult] = []
    all_verified = True
    all_complete = True
    cyclic_unsupported = "CYCLIC_REFERENCE_NOT_STEADY_STATE" in warnings
    for target in _target_conditions(project, parallel):
        target_values: dict[str, object] = {
            "soil_temperature_c": target.soil_temperature_c,
            "burial_depth_m": target.burial_depth_m,
            "soil_thermal_resistivity_km_w": target.soil_thermal_resistivity_km_w,
            "arrangement": target.arrangement,
            "installation_method": target.installation_method,
            "grouping_parallel": target.grouping_parallel,
        }
        applied: list[CatalogCorrectionFactor] = []
        missing: list[str] = []
        combined = 1.0
        region_trace = [f"Başlangıç: {ampacity:.6f} A/kablo × {parallel} = {arithmetic_total:.6f} A aritmetik toplam."]
        for parameter, ref_value in reference_values.items():
            target_value = target_values[parameter]
            if ref_value is None or target_value is None:
                missing.append(parameter)
                continue
            if _same_value(parameter, ref_value, target_value):
                region_trace.append(f"{parameter}: referans=proje; k=1.0")
                continue
            factor = _factor_for(factors, parameter, ref_value, target_value)
            if factor is None:
                missing.append(parameter)
                region_trace.append(f"{parameter}: {ref_value} → {target_value}; kaynaklı düzeltme faktörü yok.")
                continue
            applied.append(factor)
            combined *= factor.factor
            if factor.status != "VERIFIED":
                all_verified = False
            region_trace.append(
                f"{parameter}: {ref_value} → {target_value}; k={factor.factor:.6g}; "
                f"kaynak={factor.source_type}:{factor.source_reference or 'belirtilmemiş'}"
            )
        if cyclic_unsupported:
            missing.append("steady_state_load_factor")
        complete = not missing
        if not complete:
            all_complete = False
        adjusted = arithmetic_total * combined if complete else None
        if not complete:
            status = "REFERENCE_ONLY_INCOMPLETE"
        elif all(item.status == "VERIFIED" for item in applied):
            status = "NORMALIZED_SOURCE_VERIFIED"
        else:
            status = "NORMALIZED_CONDITIONAL"
        region_results.append(CatalogReferenceRegionResult(
            target.region_id,
            target.region_name,
            ampacity,
            arithmetic_total,
            combined if complete else None,
            adjusted,
            status,
            tuple(applied),
            tuple(dict.fromkeys(missing)),
            tuple(region_trace),
        ))

    adjusted_values = [item.adjusted_total_ampacity_a for item in region_results if item.adjusted_total_ampacity_a is not None]
    governing_adjusted = min(adjusted_values) if adjusted_values and len(adjusted_values) == len(region_results) else None
    governing_region = ""
    if governing_adjusted is not None:
        governing_region = next(
            item.region_id for item in region_results
            if item.adjusted_total_ampacity_a is not None and isclose(item.adjusted_total_ampacity_a, governing_adjusted, abs_tol=1e-9)
        )

    if cyclic_unsupported:
        status = "CYCLIC_REFERENCE_REQUIRES_IEC60853"
    elif not all_complete:
        status = "REFERENCE_ONLY_INCOMPLETE"
    elif all_verified:
        status = "NORMALIZED_SOURCE_VERIFIED"
    else:
        status = "NORMALIZED_CONDITIONAL"

    physical_delta = None
    physical_delta_pct = None
    physical_status = "NOT_AVAILABLE"
    if physical_model_ampacity_a is not None and governing_adjusted is not None and governing_adjusted > 0:
        physical = float(physical_model_ampacity_a)
        physical_delta = physical - governing_adjusted
        physical_delta_pct = 100.0 * physical_delta / governing_adjusted
        if isclose(physical_delta, 0.0, abs_tol=1e-9):
            physical_status = "ALIGNED"
        elif physical_delta < 0:
            physical_status = "PHYSICAL_MODEL_LOWER"
        else:
            physical_status = "PHYSICAL_MODEL_HIGHER"
        trace.append(
            f"Fiziksel model - normalize katalog = {physical_delta:+.6f} A ({physical_delta_pct:+.6f}%). "
            "Bu fark tek başına kabul/ret toleransı değildir."
        )
    elif physical_model_ampacity_a is not None:
        physical_status = "CATALOG_NORMALIZATION_INCOMPLETE"

    if not factors:
        trace.append("Paket içinde IEC/national/manufacturer düzeltme tablosu bulunmaz; yalnız açık kaynaklı kullanıcı faktörleri tüketilir.")
    if parallel != int(reference_values["grouping_parallel"]):
        trace.append("Paralel kablo toplamı çıplak ×N ile proje rating'i sayılmaz; grouping_parallel faktörü gerekir.")

    return CatalogReferenceValidationResult(
        status,
        ampacity,
        parallel,
        arithmetic_total,
        governing_adjusted,
        governing_region,
        all_complete and all_verified,
        tuple(region_results),
        tuple(dict.fromkeys(warnings)),
        tuple(trace),
        None if physical_model_ampacity_a is None else float(physical_model_ampacity_a),
        physical_delta,
        physical_delta_pct,
        physical_status,
    )
