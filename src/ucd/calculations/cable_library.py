from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from hashlib import sha256
import json
from math import log, pi, sqrt
from typing import Any, Iterable
from pathlib import Path

from ucd.models.project import (
    CABLE_SOURCE_CALCULATED,
    CABLE_SOURCE_USER_ASSUMPTION,
    CABLE_STATUS_CONDITIONAL,
    CABLE_STATUS_DRAFT,
    CABLE_STATUS_VERIFIED,
    CableCatalogRecord,
    CableData,
    CableLayerData,
    CableLibraryData,
    CableParameterSource,
)
from ucd.calculations.cable_template_generator import (
    build_generic_template_library,
    estimate_cable_mass_kg_km,
    evaluate_catalog_validation_gates,
    thermal_resistivity_for_material,
)


REFERENCE = (
    "DiTuS v0.15 cable construction and catalog model. Catalog values, manufacturer drawings, "
    "test reports, calculated values and user assumptions remain distinguishable."
)


class CableLibraryInputError(ValueError):
    pass


@dataclass(frozen=True)
class CableValidationIssue:
    severity: str  # ERROR / WARNING / INFO
    code: str
    message: str
    layer_id: str = ""
    field_name: str = ""


@dataclass(frozen=True)
class CableDerivedValues:
    conductor_diameter_mm: float
    insulation_outer_diameter_mm: float
    screen_outer_diameter_mm: float
    overall_diameter_mm: float
    capacitance_uf_km: float
    conductor_dc_resistance_20_ohm_km: float
    sheath_dc_resistance_20_ohm_km: float
    sheath_mean_diameter_mm: float
    sheath_cross_section_mm2: float
    t1_thermal_resistivity_km_w: float
    t2_thermal_resistivity_km_w: float
    t3_thermal_resistivity_km_w: float


@dataclass(frozen=True)
class CableValidationReport:
    status: str
    issues: tuple[CableValidationIssue, ...]
    electrical_readiness: str
    bonding_readiness: str
    thermal_readiness: str
    fault_readiness: str
    derived: CableDerivedValues

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "ERROR" for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.severity == "WARNING" for issue in self.issues)


_MATERIAL_RESISTIVITY_OHM_MM2_M = {
    "CU": 0.017241,
    "COPPER": 0.017241,
    "AL": 0.028264,
    "ALUMINIUM": 0.028264,
    "ALUMINUM": 0.028264,
}


def _dataclass_from_dict(cls: type, raw: dict[str, Any]):
    allowed = {item.name for item in fields(cls)}
    return cls(**{key: value for key, value in raw.items() if key in allowed})


def cable_from_dict(raw: dict[str, Any]) -> CableData:
    layer_raw = raw.get("layers", [])
    source_raw = raw.get("parameter_sources", [])
    layers = [
        _dataclass_from_dict(CableLayerData, dict(item))
        for item in layer_raw
        if isinstance(item, dict)
    ]
    sources = [
        _dataclass_from_dict(CableParameterSource, dict(item))
        for item in source_raw
        if isinstance(item, dict)
    ]
    kwargs = {
        key: value
        for key, value in raw.items()
        if key in {item.name for item in fields(CableData)}
        and key not in {"layers", "parameter_sources"}
    }
    cable = CableData(**kwargs, layers=layers, parameter_sources=sources)
    if cable.layers:
        synchronize_cable_from_layers(cable, overwrite_electrical=False)
    return cable


def _canonical_cable_payload(cable: CableData) -> dict[str, Any]:
    payload = asdict(cable)
    for key in ("snapshot_id", "snapshot_hash", "snapshot_created_at", "validation_notes"):
        payload.pop(key, None)
    return payload


def cable_snapshot_hash(cable: CableData) -> str:
    # Hash the same synchronized representation that is persisted/applied.
    # This closes the old gap where callers could hash stale scalar geometry
    # while create_project_snapshot() subsequently changed it.
    normalized = cable_from_dict(deepcopy(asdict(cable)))
    synchronize_cable_from_layers(normalized, overwrite_electrical=False)
    update_cable_validation_state(normalized)
    encoded = json.dumps(
        _canonical_cable_payload(normalized), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def create_project_snapshot(cable: CableData, record_id: str = "") -> CableData:
    snapshot = cable_from_dict(deepcopy(asdict(cable)))
    synchronize_cable_from_layers(snapshot, overwrite_electrical=False)
    update_cable_validation_state(snapshot)
    stamp = datetime.now().isoformat(timespec="seconds")
    digest = cable_snapshot_hash(snapshot)
    snapshot.snapshot_id = f"SNAP-{digest[:12].upper()}"
    snapshot.snapshot_hash = digest
    snapshot.snapshot_created_at = stamp
    if record_id:
        snapshot.catalog_record_id = record_id
    return snapshot


def catalog_record_from_cable(
    cable: CableData,
    record_id: str,
    manufacturer: str | None = None,
    series: str | None = None,
    model: str | None = None,
    status: str | None = None,
) -> CableCatalogRecord:
    if not record_id.strip():
        raise CableLibraryInputError("Katalog kayıt kimliği boş bırakılamaz.")
    synchronized = cable_from_dict(deepcopy(asdict(cable)))
    synchronize_cable_from_layers(synchronized, overwrite_electrical=False)
    update_cable_validation_state(synchronized)
    snapshot = create_project_snapshot(synchronized, record_id)
    return CableCatalogRecord(
        record_id=record_id.strip(),
        manufacturer=(manufacturer if manufacturer is not None else cable.manufacturer) or "KULLANICI",
        series=(series if series is not None else cable.series) or "Manuel",
        model=(model if model is not None else cable.model) or cable.name,
        voltage_class=cable.voltage_class,
        conductor_material=cable.conductor_material,
        conductor_area_mm2=cable.conductor_area_mm2,
        construction_type=cable.construction_type,
        standard=cable.applicable_standard,
        status=status or cable.data_status,
        cable_snapshot=asdict(snapshot),
        source_ids=[source.source_id for source in cable.parameter_sources],
        tags=["PROJECT_CREATED"],
        notes="Proje kablo oluşturucusundan kaydedildi.",
    )


def apply_catalog_record(record: CableCatalogRecord, target: CableData | None = None) -> CableData:
    if not record.cable_snapshot:
        raise CableLibraryInputError(f"{record.record_id} kayıt snapshot'ı içermiyor.")
    _normalise_catalog_record_snapshot(record)
    source = cable_from_dict(deepcopy(record.cable_snapshot))
    synchronize_cable_from_layers(source, overwrite_electrical=False)
    update_cable_validation_state(source)
    snapshot = create_project_snapshot(source, record.record_id)
    if target is None:
        return snapshot
    for item in fields(CableData):
        setattr(target, item.name, deepcopy(getattr(snapshot, item.name)))
    return target


def _layer_by_type(layers: Iterable[CableLayerData], *types: str) -> CableLayerData | None:
    requested = {value.upper() for value in types}
    return next((layer for layer in layers if layer.layer_type.upper() in requested), None)


def _equivalent_rho(
    layers: list[CableLayerData], inner_mm: float, outer_mm: float, fallback: float
) -> float:
    if outer_mm <= inner_mm or inner_mm <= 0:
        return fallback
    weighted = 0.0
    total = 0.0
    for layer in layers:
        lo = max(inner_mm, layer.inner_diameter_mm)
        hi = min(outer_mm, layer.outer_diameter_mm)
        if hi <= lo or lo <= 0:
            continue
        weight = log(hi / lo)
        # Metallic layers have no insulating-material rho in the central
        # IEC 60287 profile and must not recursively inherit the previous
        # scalar fallback. Only explicitly modelled insulating layers
        # contribute to the equivalent radial material property.
        if layer.thermal_resistivity_km_w <= 0:
            continue
        rho = layer.thermal_resistivity_km_w
        weighted += rho * weight
        total += weight
    return weighted / total if total > 0 else fallback


def _resistance_ohm_km(material: str, area_mm2: float) -> float:
    rho = _MATERIAL_RESISTIVITY_OHM_MM2_M.get(material.strip().upper())
    if rho is None or area_mm2 <= 0:
        return 0.0
    return rho * 1000.0 / area_mm2


def derive_from_layers(cable: CableData) -> CableDerivedValues:
    layers = sorted(cable.layers, key=lambda layer: (layer.inner_diameter_mm, layer.outer_diameter_mm))
    conductor = _layer_by_type(layers, "CONDUCTOR")
    insulation = _layer_by_type(layers, "INSULATION")
    screen = _layer_by_type(layers, "METALLIC_SCREEN", "METALLIC_SHEATH", "WIRE_SCREEN")
    outer = _layer_by_type(layers, "OUTER_SHEATH", "JACKET")

    conductor_d = conductor.outer_diameter_mm if conductor else cable.conductor_diameter_mm
    insulation_outer = insulation.outer_diameter_mm if insulation else cable.t1_outer_diameter_mm
    screen_outer = screen.outer_diameter_mm if screen else cable.t2_outer_diameter_mm
    overall = max((layer.outer_diameter_mm for layer in layers), default=cable.overall_diameter_mm)
    if outer is not None:
        overall = outer.outer_diameter_mm

    area = (
        conductor.conductor_area_mm2
        if conductor and conductor.conductor_area_mm2 > 0
        else cable.conductor_area_mm2
    )
    conductor_material = conductor.material if conductor and conductor.material else cable.conductor_material
    screen_area = (
        screen.conductor_area_mm2
        if screen and screen.conductor_area_mm2 > 0
        else cable.sheath_cross_section_mm2
    )
    screen_material = screen.material if screen and screen.material else cable.sheath_material
    sheath_mean = (
        0.5 * (screen.inner_diameter_mm + screen.outer_diameter_mm)
        if screen is not None
        else cable.sheath_mean_diameter_mm
    )

    epsilon_r = insulation.relative_permittivity if insulation and insulation.relative_permittivity > 0 else 2.3
    inner_insulation_d = insulation.inner_diameter_mm if insulation else conductor_d
    if insulation_outer > inner_insulation_d > 0:
        capacitance = 2.0 * pi * 8.8541878128e-12 * epsilon_r / log(
            insulation_outer / inner_insulation_d
        ) * 1.0e9
    else:
        capacitance = cable.capacitance_uf_km

    t1_rho = _equivalent_rho(layers, max(conductor_d, 0.001), insulation_outer, cable.t1_thermal_resistivity_km_w)
    t2_rho = _equivalent_rho(layers, max(insulation_outer, 0.001), screen_outer, cable.t2_thermal_resistivity_km_w)
    t3_rho = _equivalent_rho(layers, max(screen_outer, 0.001), overall, cable.t3_thermal_resistivity_km_w)

    return CableDerivedValues(
        conductor_diameter_mm=conductor_d,
        insulation_outer_diameter_mm=insulation_outer,
        screen_outer_diameter_mm=screen_outer,
        overall_diameter_mm=overall,
        capacitance_uf_km=capacitance,
        conductor_dc_resistance_20_ohm_km=_resistance_ohm_km(conductor_material, area),
        sheath_dc_resistance_20_ohm_km=_resistance_ohm_km(screen_material, screen_area),
        sheath_mean_diameter_mm=sheath_mean,
        sheath_cross_section_mm2=screen_area,
        t1_thermal_resistivity_km_w=t1_rho,
        t2_thermal_resistivity_km_w=t2_rho,
        t3_thermal_resistivity_km_w=t3_rho,
    )


def synchronize_cable_from_layers(
    cable: CableData, *, overwrite_electrical: bool = True
) -> CableDerivedValues:
    # Known insulation/jacket materials are resolved from the central
    # IEC 60287-2-1 profile on every creation/import path. This prevents
    # stale package-local values from becoming hidden calculation inputs.
    for layer in cable.layers:
        central_rho = thermal_resistivity_for_material(layer.material)
        if central_rho is not None:
            layer.thermal_resistivity_km_w = central_rho
    derived = derive_from_layers(cable)
    conductor = _layer_by_type(cable.layers, "CONDUCTOR")
    insulation = _layer_by_type(cable.layers, "INSULATION")
    screen = _layer_by_type(cable.layers, "METALLIC_SCREEN", "METALLIC_SHEATH", "WIRE_SCREEN")

    if conductor is not None:
        if conductor.conductor_area_mm2 > 0:
            cable.conductor_area_mm2 = conductor.conductor_area_mm2
        if conductor.material:
            cable.conductor_material = conductor.material
    if screen is not None:
        if screen.material:
            cable.sheath_material = screen.material
        cable.sheath_cross_section_mm2 = derived.sheath_cross_section_mm2
    if insulation is not None:
        if insulation.material:
            cable.insulation = insulation.material
        if insulation.dielectric_loss_tan_delta > 0:
            cable.dielectric_loss_tan_delta = insulation.dielectric_loss_tan_delta

    cable.conductor_diameter_mm = derived.conductor_diameter_mm
    cable.t1_outer_diameter_mm = derived.insulation_outer_diameter_mm
    cable.t2_outer_diameter_mm = derived.screen_outer_diameter_mm
    cable.overall_diameter_mm = derived.overall_diameter_mm
    if overwrite_electrical or cable.capacitance_uf_km <= 0:
        cable.capacitance_uf_km = derived.capacitance_uf_km
    cable.sheath_mean_diameter_mm = derived.sheath_mean_diameter_mm
    cable.t1_thermal_resistivity_km_w = derived.t1_thermal_resistivity_km_w
    cable.t2_thermal_resistivity_km_w = derived.t2_thermal_resistivity_km_w
    cable.t3_thermal_resistivity_km_w = derived.t3_thermal_resistivity_km_w
    if overwrite_electrical or cable.dc_resistance_20_ohm_km <= 0:
        cable.dc_resistance_20_ohm_km = derived.conductor_dc_resistance_20_ohm_km
    if overwrite_electrical or cable.sheath_dc_resistance_20_ohm_km <= 0:
        cable.sheath_dc_resistance_20_ohm_km = derived.sheath_dc_resistance_20_ohm_km
    return derived


def validate_cable(cable: CableData) -> CableValidationReport:
    issues: list[CableValidationIssue] = []
    layers = sorted(cable.layers, key=lambda layer: (layer.inner_diameter_mm, layer.outer_diameter_mm))
    if not layers:
        issues.append(CableValidationIssue("ERROR", "NO_LAYERS", "Parametrik kablo katmanı bulunmuyor."))
    previous_outer = 0.0
    source_ids = {source.source_id for source in cable.parameter_sources}
    verified_sources = {source.source_id for source in cable.parameter_sources if source.verified}
    for layer in layers:
        if layer.inner_diameter_mm < 0 or layer.outer_diameter_mm <= layer.inner_diameter_mm:
            issues.append(CableValidationIssue(
                "ERROR", "LAYER_DIAMETER", f"{layer.name}: iç/dış çap sırası geçersiz.", layer.layer_id
            ))
        if layer.inner_diameter_mm + 1e-6 < previous_outer:
            issues.append(CableValidationIssue(
                "ERROR", "LAYER_OVERLAP", f"{layer.name}: önceki katmanla geometrik çakışma var.", layer.layer_id
            ))
        elif layer.inner_diameter_mm - previous_outer > 0.25:
            issues.append(CableValidationIssue(
                "WARNING", "LAYER_GAP", f"{layer.name}: önceki katmanla {layer.inner_diameter_mm - previous_outer:.3f} mm boşluk var.", layer.layer_id
            ))
        previous_outer = max(previous_outer, layer.outer_diameter_mm)
        if layer.source_id and layer.source_id not in source_ids:
            issues.append(CableValidationIssue(
                "WARNING", "SOURCE_MISSING", f"{layer.name}: {layer.source_id} kaynak kaydı bulunamadı.", layer.layer_id
            ))
        if not layer.source_id:
            issues.append(CableValidationIssue(
                "WARNING", "SOURCE_UNASSIGNED", f"{layer.name}: veri kaynağı atanmadı.", layer.layer_id
            ))

    conductor = _layer_by_type(layers, "CONDUCTOR")
    insulation = _layer_by_type(layers, "INSULATION")
    screen = _layer_by_type(layers, "METALLIC_SCREEN", "METALLIC_SHEATH", "WIRE_SCREEN")
    outer = _layer_by_type(layers, "OUTER_SHEATH", "JACKET")
    armour = _layer_by_type(layers, "ARMOUR")

    if conductor is None:
        issues.append(CableValidationIssue("ERROR", "NO_CONDUCTOR", "İletken katmanı bulunmuyor."))
    elif conductor.conductor_area_mm2 <= 0:
        issues.append(CableValidationIssue("ERROR", "NO_CONDUCTOR_AREA", "İletken kesiti tanımlı değil.", conductor.layer_id))
    if insulation is None:
        issues.append(CableValidationIssue("ERROR", "NO_INSULATION", "Ana izolasyon katmanı bulunmuyor."))
    elif insulation.relative_permittivity <= 0:
        issues.append(CableValidationIssue(
            "WARNING", "EPSILON_ASSUMED", "İzolasyon bağıl permitivitesi eksik; hesap varsayımı kullanılacak.", insulation.layer_id
        ))
    if screen is None:
        issues.append(CableValidationIssue("ERROR", "NO_METALLIC_SCREEN", "Metalik kılıf/ekran katmanı bulunmuyor."))
    else:
        if screen.conductor_area_mm2 <= 0:
            issues.append(CableValidationIssue("ERROR", "NO_SCREEN_AREA", "Metalik ekran kesiti tanımlı değil.", screen.layer_id))
        if screen.wire_count > 0 and screen.wire_diameter_mm > 0 and screen.conductor_area_mm2 > 0:
            geometric = screen.wire_count * pi * screen.wire_diameter_mm ** 2 / 4.0
            difference = abs(geometric - screen.conductor_area_mm2) / screen.conductor_area_mm2
            if difference > 0.05:
                issues.append(CableValidationIssue(
                    "ERROR", "SCREEN_GEOMETRY_MISMATCH",
                    f"Ekran tel geometrisi {geometric:.2f} mm², kayıtlı kesit {screen.conductor_area_mm2:.2f} mm²; fark %{difference*100:.1f}.",
                    screen.layer_id,
                ))
        elif screen.conductor_area_mm2 > 0:
            issues.append(CableValidationIssue(
                "WARNING", "SCREEN_WIRE_DETAIL_MISSING",
                "Toplam ekran kesiti var; tel sayısı/çapı bilinmiyor. Dağılımlı ekran hesabı koşullu.", screen.layer_id,
            ))
    if outer is None:
        issues.append(CableValidationIssue("WARNING", "NO_OUTER_SHEATH", "Dış kılıf katmanı açıkça tanımlanmadı."))

    # Metalik kılıf/ekran ve zırh aynı fiziksel katman değildir.  λ1 metalik
    # kılıf/ekran kaybını, λ2 ise yalnız gerçek bir zırh katmanı varsa zırh
    # kaybını temsil eder.  Eski projelerdeki skaler alanlar korunur.
    if armour is None and cable.armour_loss_factor > 1e-12:
        issues.append(CableValidationIssue(
            "WARNING", "ARMOUR_FACTOR_WITHOUT_LAYER",
            "Zırh katmanı tanımlı değil ancak λ2 sıfırdan büyük. Zırh kaybı doğrulanamaz.",
            field_name="armour_loss_factor",
        ))
    if armour is not None and cable.armour_loss_factor <= 1e-12:
        issues.append(CableValidationIssue(
            "WARNING", "ARMOUR_LAYER_WITH_ZERO_FACTOR",
            "Zırh katmanı var ancak λ2=0. Zırh kaybı hesap dışı veya henüz doğrulanmamış.",
            layer_id=armour.layer_id, field_name="armour_loss_factor",
        ))

    if not cable.voltage_class.strip():
        issues.append(CableValidationIssue("ERROR", "NO_VOLTAGE_CLASS", "Kablo gerilim sınıfı tanımlı değil."))
    if cable.max_temperature_c <= cable.reference_ambient_c:
        issues.append(CableValidationIssue("ERROR", "TEMPERATURE_LIMIT", "İletken sıcaklık limiti ortam sıcaklığından büyük olmalı."))

    derived = derive_from_layers(cable)
    if derived.overall_diameter_mm <= derived.conductor_diameter_mm:
        issues.append(CableValidationIssue("ERROR", "OVERALL_DIAMETER", "Kablo dış çapı iletken çapından büyük değil."))
    if derived.capacitance_uf_km <= 0:
        issues.append(CableValidationIssue("ERROR", "CAPACITANCE", "Kapasitans hesaplanamadı."))

    critical_source_ids = {
        layer.source_id for layer in (conductor, insulation, screen, outer) if layer is not None and layer.source_id
    }
    if critical_source_ids and not critical_source_ids.issubset(verified_sources):
        issues.append(CableValidationIssue(
            "WARNING", "UNVERIFIED_CRITICAL_SOURCE",
            "Kritik konstrüksiyon verilerinin en az biri doğrulanmış üretici/test kaynağına bağlı değil.",
        ))

    electrical = "COMPLETE" if conductor and insulation and derived.capacitance_uf_km > 0 else "MISSING"
    bonding = "COMPLETE" if screen and derived.sheath_cross_section_mm2 > 0 else "MISSING"
    thermal = "COMPLETE" if insulation and outer and derived.overall_diameter_mm > 0 else "MISSING"
    fault = "COMPLETE" if screen and screen.conductor_area_mm2 > 0 and screen.source_id in verified_sources else "CONDITIONAL"

    if any(issue.severity == "ERROR" for issue in issues):
        status = "FAIL"
    elif any(issue.severity == "WARNING" for issue in issues):
        status = CABLE_STATUS_CONDITIONAL
    else:
        status = CABLE_STATUS_VERIFIED
    return CableValidationReport(status, tuple(issues), electrical, bonding, thermal, fault, derived)


def update_cable_validation_state(cable: CableData) -> CableValidationReport:
    report = validate_cable(cable)
    cable.data_status = report.status
    cable.validation_notes = [f"{issue.severity}:{issue.code}:{issue.message}" for issue in report.issues]
    return report


def filter_catalog_records(
    library: CableLibraryData,
    manufacturer: str = "",
    voltage_class: str = "",
    conductor_material: str = "",
    minimum_area_mm2: float = 0.0,
    text: str = "",
) -> list[CableCatalogRecord]:
    manufacturer_u = manufacturer.strip().upper()
    voltage_u = voltage_class.strip().upper()
    material_u = conductor_material.strip().upper()
    text_u = text.strip().upper()
    result: list[CableCatalogRecord] = []
    for record in library.records:
        if manufacturer_u and manufacturer_u not in {"TÜMÜ", "ALL"} and record.manufacturer.upper() != manufacturer_u:
            continue
        if voltage_u and voltage_u not in {"TÜMÜ", "ALL"} and voltage_u not in record.voltage_class.upper():
            continue
        if material_u and material_u not in {"TÜMÜ", "ALL"} and record.conductor_material.upper() != material_u:
            continue
        if record.conductor_area_mm2 < minimum_area_mm2:
            continue
        haystack = " ".join([
            record.record_id, record.manufacturer, record.series, record.model,
            record.voltage_class, record.conductor_material, " ".join(record.tags), record.notes,
        ]).upper()
        if text_u and text_u not in haystack:
            continue
        result.append(record)
    return sorted(result, key=lambda item: (item.manufacturer, item.voltage_class, item.conductor_material, item.conductor_area_mm2, item.model))


def catalog_package_to_dict(library: CableLibraryData) -> dict[str, Any]:
    normalize_catalog_library(library)
    return {
        "format": "DITUS_CABLE_CATALOG",
        "schema_version": "0.15",
        "package": asdict(library),
    }


def _normalise_catalog_record_snapshot(record: CableCatalogRecord) -> None:
    """Make layer geometry the sole solver geometry and evaluate catalog gates.

    Direct catalog Rdc/capacitance values remain authoritative electrical
    inputs. A published overall diameter or kg/km value is *not* copied into
    solver geometry; it is retained as CAT evidence and compared against the
    generated construction.
    """
    if not record.cable_snapshot:
        return
    cable = cable_from_dict(deepcopy(record.cable_snapshot))
    synchronize_cable_from_layers(cable, overwrite_electrical=False)

    direct_rdc20 = _get_catalog_number(record.catalog_electrical, "conductor_rdc20_ohm_km")
    direct_capacitance = _get_catalog_number(record.catalog_electrical, "capacitance_uf_km")
    if direct_rdc20 > 0:
        cable.dc_resistance_20_ohm_km = direct_rdc20
    if direct_capacitance > 0:
        cable.capacitance_uf_km = direct_capacitance

    gates = evaluate_catalog_validation_gates(record, cable)
    reference = dict(record.reference_conditions or {})
    reference["validation_gates"] = gates
    record.reference_conditions = reference
    if gates.get("status") == "FAIL":
        record.status = CABLE_STATUS_CONDITIONAL
        for failure in gates.get("failures", []):
            note = f"VALIDATION_GATE:{failure}"
            if note not in cable.validation_notes:
                cable.validation_notes.append(note)
        gate_summary = " ".join(str(item) for item in gates.get("failures", []))
        if gate_summary and gate_summary not in record.notes:
            record.notes = (record.notes + " " + gate_summary).strip()

    # Imported records are never promoted solely because both scalar gates pass.
    # Missing/assumed internal construction keeps the existing conditional state.
    update_cable_validation_state(cable)
    if record.status == CABLE_STATUS_CONDITIONAL:
        cable.data_status = CABLE_STATUS_CONDITIONAL

    cable.snapshot_id = ""
    cable.snapshot_hash = ""
    cable.snapshot_created_at = ""
    record.cable_snapshot = asdict(cable)


def normalize_catalog_library(library: CableLibraryData) -> CableLibraryData:
    """Synchronize every record loaded, seeded, merged or prepared for export."""
    for record in library.records:
        _normalise_catalog_record_snapshot(record)
    valid_ids = {record.record_id for record in library.records}
    if library.selected_record_id not in valid_ids:
        library.selected_record_id = library.records[0].record_id if library.records else ""
    return library


def _get_catalog_number(mapping: dict[str, Any], key: str) -> float:
    value = mapping.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def catalog_package_from_dict(raw: dict[str, Any]) -> CableLibraryData:
    if not isinstance(raw, dict) or raw.get("format") != "DITUS_CABLE_CATALOG":
        raise CableLibraryInputError("Dosya DiTuS kablo katalog paketi değil.")
    package = raw.get("package")
    if not isinstance(package, dict):
        raise CableLibraryInputError("Katalog paket içeriği eksik.")
    raw_sources = package.get("sources", [])
    raw_records = package.get("records", [])
    if not isinstance(raw_sources, list) or not isinstance(raw_records, list):
        raise CableLibraryInputError("Katalog paketindeki kaynak/kayıt listesi geçersiz.")
    sources = [
        _dataclass_from_dict(CableParameterSource, dict(item))
        for item in raw_sources
        if isinstance(item, dict)
    ]
    records = [
        _dataclass_from_dict(CableCatalogRecord, dict(item))
        for item in raw_records
        if isinstance(item, dict)
    ]
    for record in records:
        _normalise_catalog_record_snapshot(record)
    kwargs = {
        key: value for key, value in package.items()
        if key in {item.name for item in fields(CableLibraryData)} and key not in {"sources", "records"}
    }
    return CableLibraryData(**kwargs, sources=sources, records=records)


def merge_catalog_library(target: CableLibraryData, incoming: CableLibraryData, replace: bool = False) -> tuple[int, int]:
    normalize_catalog_library(incoming)
    source_by_id = {source.source_id: source for source in target.sources}
    for source in incoming.sources:
        if replace or source.source_id not in source_by_id:
            source_by_id[source.source_id] = deepcopy(source)
    record_by_id = {record.record_id: record for record in target.records}
    added = 0
    updated = 0
    for record in incoming.records:
        if record.record_id in record_by_id:
            if replace:
                record_by_id[record.record_id] = deepcopy(record)
                updated += 1
        else:
            record_by_id[record.record_id] = deepcopy(record)
            added += 1
    target.sources = sorted(source_by_id.values(), key=lambda item: item.source_id)
    target.records = sorted(record_by_id.values(), key=lambda item: item.record_id)
    normalize_catalog_library(target)
    return added, updated


def load_catalog_package_file(path: str | Path) -> CableLibraryData:
    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CableLibraryInputError(f"Katalog paketi okunamadı: {file_path}") from exc
    except json.JSONDecodeError as exc:
        raise CableLibraryInputError(f"Katalog paketi JSON biçimi geçersiz: {file_path}") from exc
    return catalog_package_from_dict(raw)


def builtin_catalog_directory() -> Path:
    """Return the package resource directory reserved for built-in catalogs.

    FAZ 2 ships no manufacturer packages here. The path no longer depends on
    ``examples/`` or a source-tree parent count and remains valid in frozen
    layouts where package resources are bundled beside ``ucd``.
    """
    return Path(__file__).resolve().parents[1] / "resources" / "catalogs"


def merge_builtin_catalogs(
    target: CableLibraryData,
    directory: str | Path | None = None,
    replace: bool = False,
) -> tuple[int, int, tuple[str, ...]]:
    """Merge manufacturer-free built-in templates or an explicit external directory.

    With no directory argument, seven generated generic templates are merged.
    Passing a directory preserves the legacy/user workflow for importing a set
    of ``*.ditus-cable-catalog.json`` packages, but no such manufacturer data is
    distributed by DiTuS.
    """
    if directory is None:
        incoming = build_generic_template_library()
        added, updated = merge_catalog_library(target, incoming, replace=replace)
        target.builtin_catalogs_loaded = True
        return added, updated, (
            f"Jenerik koşullu şablonlar: +{added}, güncel {updated}; üretici verisi paketlenmedi.",
        )

    root = Path(directory)
    if not root.exists():
        return 0, 0, (f"Katalog klasörü bulunamadı: {root}",)
    added_total = 0
    updated_total = 0
    messages: list[str] = []
    for package_path in sorted(root.glob("*.ditus-cable-catalog.json")):
        try:
            incoming = load_catalog_package_file(package_path)
            added, updated = merge_catalog_library(target, incoming, replace=replace)
            added_total += added
            updated_total += updated
            messages.append(f"{package_path.name}: +{added}, güncel {updated}")
        except CableLibraryInputError as exc:
            messages.append(f"{package_path.name}: HATA — {exc}")
    target.builtin_catalogs_loaded = True
    return added_total, updated_total, tuple(messages)
