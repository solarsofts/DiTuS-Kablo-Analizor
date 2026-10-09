from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


PROJECT_SCHEMA_VERSION = "0.16.4"


def schema_version_tuple(value: Any) -> tuple[int, ...] | None:
    """Parse a dotted schema version such as "0.16.4" into ``(0, 16, 4)``.

    Trailing zero parts are dropped so "0.3" and "0.3.0" compare equal.
    Returns ``None`` when the value is not purely dotted decimal digits.
    """
    text = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.\d+)*", text, flags=re.ASCII):
        return None
    parts = [int(part) for part in text.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_schema_newer_than_supported(value: Any) -> bool:
    """True when a file schema is newer than :data:`PROJECT_SCHEMA_VERSION`."""
    parsed = schema_version_tuple(value)
    return parsed is not None and parsed > schema_version_tuple(PROJECT_SCHEMA_VERSION)


INTERNAL_THERMAL_AUTO = "AUTO_GEOMETRY"
INTERNAL_THERMAL_MANUAL = "MANUAL"
EXTERNAL_THERMAL_AUTO = "AUTO_IMAGE"
EXTERNAL_THERMAL_MIXED = "AUTO_MIXED_ZONE"
EXTERNAL_THERMAL_MANUAL = "MANUAL"

BONDING_CROSS = "CROSS_BONDED"
BONDING_SINGLE_POINT = "SINGLE_POINT"
BONDING_SOLID_BOTH_END = "SOLID_BOTH_END"


LOAD_MODE_ACTIVE_POWER = "ACTIVE_POWER_MW"
LOAD_MODE_APPARENT_POWER = "APPARENT_POWER_MVA"
LOAD_MODE_DIRECT_CURRENT = "DIRECT_CURRENT_A"

ROUTE_MODE_TOTAL_LENGTH = "TOTAL_LENGTH"
ROUTE_MODE_DXF = "DXF"
ROUTE_MODE_DRAW = "DRAW"

SELECTION_MODE_RECOMMENDED = "RECOMMENDED"
SELECTION_MODE_MANUAL = "MANUAL"
SELECTION_MODE_SECTION_ONLY = "SECTION_ONLY"

MATURITY_LEVEL_1 = "L1_PRELIMINARY_SCREENING"
MATURITY_LEVEL_2 = "L2_IEC60287_ROUTE"
MATURITY_LEVEL_3 = "L3_BONDING_SHEATH"
MATURITY_LEVEL_4 = "L4_FAULT_EPR_SVL"
MATURITY_LEVEL_5 = "L5_TRANSIENT_AS_BUILT"


SOURCE_SCOPE_UNDERGROUND_ONLY = "UNDERGROUND_ONLY"


@dataclass
class SourceValueRecord:
    """Traceable value extracted from a project document or calculation sheet.

    Source records deliberately preserve contradictory values.  The audit
    layer reports conflicts; it never silently chooses which source is true.
    """

    record_id: str
    parameter_key: str
    value: Any
    unit: str = ""
    context: str = ""
    source_reference: str = ""
    confidence: str = "MEDIUM"
    status: str = "SOURCE_REPORTED"
    notes: str = ""


@dataclass
class SourceConflictRecord:
    conflict_id: str
    severity: str
    parameter_key: str
    title: str
    record_ids: list[str] = field(default_factory=list)
    disposition: str = "UNRESOLVED"
    notes: str = ""


@dataclass
class ProjectSourceAuditData:
    source_name: str = ""
    source_file: str = ""
    scope: str = SOURCE_SCOPE_UNDERGROUND_ONLY
    excluded_scopes: list[str] = field(default_factory=list)
    records: list[SourceValueRecord] = field(default_factory=list)
    conflicts: list[SourceConflictRecord] = field(default_factory=list)
    missing_required_data: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class GenericCableCandidate:
    candidate_id: str
    label: str
    conductor_material: str
    conductor_area_mm2: float
    cables_per_phase: int
    voltage_class: str
    estimated_ampacity_a: float
    estimated_loss_kw_km: float
    estimated_margin_a: float
    recommendation_type: str = "DENGELI_BASLANGIC"
    maturity_level: str = MATURITY_LEVEL_1
    status: str = "ON_HESAP"
    notes: list[str] = field(default_factory=list)


@dataclass
class DesignBasisData:
    system_voltage_kv: float = 154.0
    frequency_hz: float = 50.0
    circuit_count: int = 1
    active_circuit_count: int = 1
    n_minus_one_enabled: bool = False
    grounding_type: str = "DIRECT_GROUNDED"

    load_input_mode: str = LOAD_MODE_APPARENT_POWER
    active_power_mw: float = 0.0
    apparent_power_mva: float = 200.0
    direct_current_a: float = 0.0
    power_factor: float = 0.95
    future_growth_percent: float = 0.0
    design_margin_percent: float = 10.0

    normal_total_current_a: float = 0.0
    normal_current_per_active_circuit_a: float = 0.0
    n1_current_per_circuit_a: float = 0.0
    design_current_per_circuit_a: float = 0.0
    suggested_voltage_class: str = "87/150 (170) kV"

    route_input_mode: str = ROUTE_MODE_TOTAL_LENGTH
    total_route_length_m: float = 1670.0
    installation_profile: str = "DIRECT_BURIED_TREFOIL"
    burial_depth_m: float = 1.20
    phase_spacing_m: float = 0.15
    circuit_spacing_m: float = 0.80
    soil_thermal_resistivity_km_w: float = 1.20
    soil_thermal_value_source: str = "PRELIMINARY_ASSUMPTION"
    conductor_preference: str = "AUTO"
    cables_per_phase_preference: str = "AUTO"

    initial_selection_mode: str = SELECTION_MODE_RECOMMENDED
    selected_candidate_id: str = ""
    candidates: list[GenericCableCandidate] = field(default_factory=list)


@dataclass
class DesignProgressData:
    system_load: str = "MISSING"
    route: str = "PRELIMINARY"
    cable: str = "PRELIMINARY"
    thermal: str = "NOT_RUN"
    bonding: str = "NOT_RUN"
    fault_epr: str = "NOT_RUN"
    svl: str = "NOT_RUN"
    final_design: str = "NOT_READY"
    maturity_level: str = MATURITY_LEVEL_1
    missing_data: list[str] = field(default_factory=list)


@dataclass
class DesignWorkflowData:
    """Persisted guided-workflow state and traceable calculation run registry.

    ``engine_runs`` stores simple JSON-compatible dictionaries so legacy project
    files remain readable without a parallel calculation-result model. Each run
    record contains the engine status, input signature, component signatures,
    timestamps, counts and the reason for a blocked/conditional/stale state.
    """

    current_stage_id: str = "system_load"
    last_recommended_stage_id: str = "system_load"
    stage_notes: dict[str, str] = field(default_factory=dict)
    last_evaluated_at: str = ""
    engine_runs: dict[str, dict[str, Any]] = field(default_factory=dict)


TRANSIENT_INITIAL_CYCLIC = "CYCLIC_STEADY"
TRANSIENT_INITIAL_STEADY = "STEADY_AT_FIRST_POINT"
TRANSIENT_INITIAL_USER = "USER_TEMPERATURE"


@dataclass
class LoadProfilePoint:
    time_h: float
    current_multiplier: float
    label: str = ""


@dataclass
class TransientLoadProfile:
    profile_id: str
    name: str
    duration_h: float = 24.0
    interpolation: str = "STEP"
    points: list[LoadProfilePoint] = field(default_factory=list)
    notes: str = ""


def default_transient_profiles() -> list[TransientLoadProfile]:
    return [
        TransientLoadProfile(
            "DAILY",
            "24 saatlik işletme çevrimi",
            24.0,
            "STEP",
            [
                LoadProfilePoint(0.0, 0.55, "Gece düşük yük"),
                LoadProfilePoint(6.0, 0.65, "Sabah yükselişi"),
                LoadProfilePoint(9.0, 0.85, "Gündüz"),
                LoadProfilePoint(12.0, 1.00, "Öğle tepe"),
                LoadProfilePoint(16.0, 0.90, "Öğleden sonra"),
                LoadProfilePoint(19.0, 1.05, "Akşam tepe"),
                LoadProfilePoint(22.0, 0.70, "Gece düşüşü"),
                LoadProfilePoint(24.0, 0.55, "Çevrim sonu"),
            ],
            "Akımlar tasarım akımının çarpanı olarak saklanır.",
        ),
        TransientLoadProfile(
            "EMERGENCY",
            "Acil yük örneği",
            12.0,
            "STEP",
            [
                LoadProfilePoint(0.0, 0.70, "Başlangıç"),
                LoadProfilePoint(2.0, 1.00, "Ön yük"),
                LoadProfilePoint(3.0, 1.25, "Acil yük"),
                LoadProfilePoint(9.0, 1.25, "Acil yük sonu"),
                LoadProfilePoint(10.0, 0.80, "Toparlanma"),
                LoadProfilePoint(12.0, 0.70, "Son"),
            ],
            "Örnek profildir; gerçek işletme/koruma senaryosuyla değiştirilmelidir.",
        ),
    ]


@dataclass
class TransientThermalStudyData:
    name: str = "IEC 60853 Geçici ve Çevrimsel Termal Çalışma"
    active_profile_id: str = "DAILY"
    time_step_minutes: float = 30.0
    transient_mesh_scale: float = 1.25
    initial_condition_mode: str = TRANSIENT_INITIAL_CYCLIC
    user_initial_conductor_temperature_c: float = 40.0
    maximum_preconditioning_cycles: int = 4
    cyclic_convergence_tolerance_c: float = 0.10
    normal_temperature_limit_c: float = 90.0
    emergency_temperature_limit_c: float = 105.0
    emergency_duration_h: float = 6.0
    calculate_cyclic_rating: bool = True
    calculate_emergency_rating: bool = True
    selected_region_ids: list[str] = field(default_factory=list)
    cable_outer_heat_capacity_mj_m3k: float = 1.60
    default_soil_heat_capacity_mj_m3k: float = 2.00
    default_backfill_heat_capacity_mj_m3k: float = 1.80
    default_concrete_heat_capacity_mj_m3k: float = 2.00
    default_duct_heat_capacity_mj_m3k: float = 1.50
    default_air_heat_capacity_mj_m3k: float = 0.0012
    profiles: list[TransientLoadProfile] = field(default_factory=default_transient_profiles)
    notes: str = ""



THERMAL_STATE_DESIGN = "DESIGN"
THERMAL_STATE_TESTED = "TESTED"
THERMAL_STATE_AS_BUILT = "AS_BUILT"

THERMAL_TRANSITION_ABRUPT = "ABRUPT"
THERMAL_TRANSITION_GRADUAL = "GRADUAL"

THERMAL_INSTALL_DIRECT_BURIED = "DIRECT_BURIED"
THERMAL_INSTALL_DUCT_BANK = "DUCT_BANK"
THERMAL_INSTALL_HDD = "HDD"
THERMAL_INSTALL_CONCRETE_TROUGH = "CONCRETE_TROUGH"
THERMAL_INSTALL_TUNNEL = "TUNNEL"


@dataclass
class ThermalMaterialData:
    material_id: str
    name: str
    category: str = "NATIVE_SOIL"
    thermal_resistivity_km_w: float = 1.20
    thermal_conductivity_w_mk: float = 0.0
    dry_density_kg_m3: float = 0.0
    wet_density_kg_m3: float = 0.0
    moisture_percent: float = 0.0
    volumetric_heat_capacity_mj_m3k: float = 0.0
    compaction_percent: float = 0.0
    critical_dryout_temperature_c: float = 0.0
    dry_state_thermal_resistivity_km_w: float = 0.0
    temperature_coefficient_per_c: float = 0.0
    anisotropy_ratio: float = 1.0
    reference_conductivity_min_w_mk: float = 0.0
    reference_conductivity_max_w_mk: float = 0.0
    moisture_condition: str = "UNSPECIFIED"
    test_method: str = ""
    library_scope: str = "PROJECT"
    requires_project_test: bool = False
    data_state: str = THERMAL_STATE_DESIGN
    source_type: str = "PRELIMINARY_ASSUMPTION"
    source_reference: str = ""
    reliability: str = "LOW"
    notes: str = ""


@dataclass
class ThermalCrossSectionTemplate:
    template_id: str
    name: str
    installation_type: str = THERMAL_INSTALL_DIRECT_BURIED
    arrangement: str = "Trefoil"
    burial_depth_m: float = 1.20
    phase_spacing_m: float = 0.15
    circuit_spacing_m: float = 0.80
    trench_width_m: float = 0.80
    trench_depth_m: float = 1.50
    bedding_thickness_m: float = 0.15
    side_backfill_width_m: float = 0.20
    cable_cover_height_m: float = 0.30
    selected_upper_fill_thickness_m: float = 0.0
    general_upper_fill_thickness_m: float = 0.50
    surface_layer_thickness_m: float = 0.0
    groundwater_depth_m: float = 99.0
    native_soil_material_id: str = "MAT-NATIVE-01"
    bedding_material_id: str = "MAT-TB-01"
    side_backfill_material_id: str = "MAT-TB-01"
    cable_cover_material_id: str = "MAT-TB-01"
    selected_upper_fill_material_id: str = "MAT-FILL-01"
    general_fill_material_id: str = "MAT-FILL-01"
    surface_material_id: str = ""
    external_thermal_mode: str = "AUTO_MIXED_ZONE"
    manual_t4_km_w: float = 0.0
    backfill_effective_radius_m: float = 0.0
    data_state: str = THERMAL_STATE_DESIGN
    source_reference: str = ""
    notes: str = ""

    # v0.12 2D nodal steady-state geometry and boundary settings. Values are
    # intentionally stored in the cross-section template so route regions can
    # reuse a verified mesh/geometry profile and override only local changes.
    nodal_enabled: bool = True
    nodal_domain_half_width_m: float = 4.0
    nodal_domain_depth_m: float = 6.0
    nodal_base_step_m: float = 0.20
    nodal_refined_step_m: float = 0.05
    nodal_refinement_radius_m: float = 0.40
    nodal_max_cells: int = 30000
    surface_boundary_type: str = "FIXED_TEMPERATURE"  # FIXED_TEMPERATURE / CONVECTIVE
    surface_temperature_c: float = 0.0
    deep_soil_temperature_c: float = 0.0
    surface_temperature_uses_ambient: bool = True
    deep_soil_temperature_uses_ambient: bool = True
    surface_heat_transfer_w_m2k: float = 12.0
    cable_effective_conductivity_w_mk: float = 12.0
    groundwater_conductivity_multiplier: float = 1.25
    parallel_cable_spacing_m: float = 0.20

    # Duct-bank / HDD idealized 2D geometry. For direct-buried sections these
    # fields are ignored. They are explicit rather than hidden assumptions.
    duct_inner_diameter_m: float = 0.13
    duct_outer_diameter_m: float = 0.16
    duct_material_id: str = "MAT-DUCT-01"
    duct_fill_material_id: str = "MAT-AIR-01"
    grout_material_id: str = "MAT-CONCRETE-01"
    # Optional region/template-specific material-property overrides. Zero means
    # use the referenced material-library value.
    duct_thermal_resistivity_km_w: float = 0.0
    duct_fill_thermal_resistivity_km_w: float = 0.0
    grout_thermal_resistivity_km_w: float = 0.0
    duct_bank_width_m: float = 0.90
    duct_bank_height_m: float = 0.55


@dataclass
class ThermalRegion:
    region_id: str
    name: str
    start_m: float
    end_m: float
    template_id: str
    transition_type: str = THERMAL_TRANSITION_ABRUPT
    enabled: bool = True
    data_state: str = THERMAL_STATE_DESIGN
    source_reference: str = ""
    overrides: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    @property
    def length_m(self) -> float:
        return max(0.0, float(self.end_m) - float(self.start_m))


@dataclass
class ThermalDesignData:
    name: str = "Termal Güzergâh ve Kesit Modeli"
    route_length_m: float = 1670.0
    active_data_state: str = THERMAL_STATE_DESIGN
    coverage_tolerance_m: float = 0.05
    materials: list[ThermalMaterialData] = field(default_factory=list)
    templates: list[ThermalCrossSectionTemplate] = field(default_factory=list)
    regions: list[ThermalRegion] = field(default_factory=list)


def default_thermal_materials() -> list[ThermalMaterialData]:
    return [
        ThermalMaterialData(
            "MAT-NATIVE-01", "Doğal zemin — ön tasarım", "NATIVE_SOIL", 1.20,
            volumetric_heat_capacity_mj_m3k=2.00,
            data_state=THERMAL_STATE_DESIGN, source_type="PRELIMINARY_ASSUMPTION",
            reliability="LOW", notes="Saha/laboratuvar ölçümüyle değiştirilmelidir.",
        ),
        ThermalMaterialData(
            "MAT-TB-01", "Termal dolgu — ön tasarım", "THERMAL_BACKFILL", 0.75,
            dry_density_kg_m3=1800.0, volumetric_heat_capacity_mj_m3k=1.80, compaction_percent=95.0,
            data_state=THERMAL_STATE_DESIGN, source_type="PRELIMINARY_ASSUMPTION",
            reliability="LOW", notes="Lot bazlı test ve as-built kompaksiyon teyidi gerekir.",
        ),
        ThermalMaterialData(
            "MAT-FILL-01", "Genel dolgu — ön tasarım", "GENERAL_FILL", 1.50,
            volumetric_heat_capacity_mj_m3k=1.80,
            data_state=THERMAL_STATE_DESIGN, source_type="PRELIMINARY_ASSUMPTION",
            reliability="LOW",
        ),
        ThermalMaterialData(
            "MAT-CONCRETE-01", "Beton / grout — ön tasarım", "CONCRETE_GROUT", 1.00,
            volumetric_heat_capacity_mj_m3k=2.00,
            data_state=THERMAL_STATE_DESIGN, source_type="PRELIMINARY_ASSUMPTION",
            reliability="LOW",
        ),
        ThermalMaterialData(
            "MAT-ASPHALT-01", "Asfalt — ön tasarım", "SURFACE", 1.20,
            volumetric_heat_capacity_mj_m3k=2.00,
            data_state=THERMAL_STATE_DESIGN, source_type="PRELIMINARY_ASSUMPTION",
            reliability="LOW",
        ),
        ThermalMaterialData(
            "MAT-DUCT-01", "HDPE/PE duct — ön tasarım", "DUCT", 3.50,
            volumetric_heat_capacity_mj_m3k=1.50,
            data_state=THERMAL_STATE_DESIGN, source_type="PRELIMINARY_ASSUMPTION",
            reliability="LOW", notes="Üretici ısıl iletkenlik değeriyle değiştirilmelidir.",
        ),
        ThermalMaterialData(
            "MAT-AIR-01", "Duct içi eşdeğer hava boşluğu", "DUCT_FILL", 8.00,
            volumetric_heat_capacity_mj_m3k=0.0012,
            data_state=THERMAL_STATE_DESIGN, source_type="PRELIMINARY_ASSUMPTION",
            reliability="LOW", notes="Doğal konveksiyon/radyasyon için eşdeğer iletim ön modeli.",
        ),
    ]


def default_thermal_templates() -> list[ThermalCrossSectionTemplate]:
    return [
        ThermalCrossSectionTemplate(
            "TPL-DG-TREFOIL-TB01", "Doğrudan gömülü / trefoil / termal dolgu",
            THERMAL_INSTALL_DIRECT_BURIED, "Trefoil", 1.20, 0.15, 0.80,
            0.80, 1.50, 0.15, 0.20, 0.30, 0.0, 0.50, 0.0, 99.0,
            "MAT-NATIVE-01", "MAT-TB-01", "MAT-TB-01", "MAT-TB-01",
            "MAT-FILL-01", "MAT-FILL-01", "", "AUTO_MIXED_ZONE", 0.0, 0.0,
            notes="Karışık zemin eşdeğer T4 ön modeli; v0.12 2D nodal sonuçla karşılaştırılır.",
        ),
        ThermalCrossSectionTemplate(
            "TPL-DUCT-MANUAL", "Boru bankası / manuel T4",
            THERMAL_INSTALL_DUCT_BANK, "Trefoil", 1.50, 0.15, 0.80,
            1.00, 1.80, 0.15, 0.20, 0.30, 0.0, 0.50, 0.20, 99.0,
            "MAT-NATIVE-01", "MAT-CONCRETE-01", "MAT-CONCRETE-01", "MAT-CONCRETE-01",
            "MAT-FILL-01", "MAT-FILL-01", "MAT-ASPHALT-01", "MANUAL", 1.10, 0.0,
            notes="Manuel T4 IEC karşılaştırması için korunur; v0.12 2D duct/grout geometrisi ayrıca çözülür.",
        ),
        ThermalCrossSectionTemplate(
            "TPL-HDD-MANUAL", "HDD / manuel T4",
            THERMAL_INSTALL_HDD, "Trefoil", 6.00, 0.15, 0.80,
            1.00, 7.00, 0.10, 0.10, 0.20, 0.0, 0.50, 0.0, 99.0,
            "MAT-NATIVE-01", "MAT-CONCRETE-01", "MAT-CONCRETE-01", "MAT-CONCRETE-01",
            "MAT-FILL-01", "MAT-FILL-01", "", "MANUAL", 1.35, 0.0,
            notes="v0.12 2D orta-kesit çözümü aktiftir; HDD giriş/çıkışı için yerel 3D doğrulama gerekir.",
        ),
    ]


def thermal_design_from_route_sections(route_sections: list["RouteSection"]) -> ThermalDesignData:
    materials = default_thermal_materials()
    templates = default_thermal_templates()
    regions: list[ThermalRegion] = []
    cursor = 0.0
    for index, section in enumerate(route_sections, start=1):
        section_type = str(section.section_type).lower()
        if "hdd" in section_type:
            template_id = "TPL-HDD-MANUAL"
        elif any(token in section_type for token in ("beton", "duct", "boru", "kanal")):
            template_id = "TPL-DUCT-MANUAL"
        else:
            template_id = "TPL-DG-TREFOIL-TB01"
        end = cursor + max(0.0, float(section.length_m))
        regions.append(ThermalRegion(
            f"TR-{index:02}", section.name, cursor, end, template_id,
            data_state=THERMAL_STATE_DESIGN,
            overrides={
                "burial_depth_m": float(section.burial_depth_m),
                "phase_spacing_m": float(section.phase_spacing_m),
                "ambient_temperature_c": float(section.ambient_temperature_c),
                "native_soil_thermal_resistivity_km_w": float(section.soil_thermal_resistivity_km_w),
                "manual_t4_km_w": float(section.external_thermal_resistance_t4_km_w),
                "cross_section_id": str(section.cross_section_id),
                "section_type": str(section.section_type),
            },
            notes=section.notes,
        ))
        cursor = end
    if not regions:
        regions = [ThermalRegion("TR-01", "Tüm güzergâh", 0.0, 1670.0, "TPL-DG-TREFOIL-TB01")]
        cursor = 1670.0
    return ThermalDesignData(
        route_length_m=cursor,
        materials=materials,
        templates=templates,
        regions=regions,
    )


def default_thermal_design() -> ThermalDesignData:
    return thermal_design_from_route_sections([
        RouteSection("RS-01 Standart hendek", 1250.0),
        RouteSection(
            "RS-02 Yol geçişi", 140.0, "Beton kanal", 1.5, 1.5, "CS-04", 30.0,
            EXTERNAL_THERMAL_MANUAL, 0.15, 1.10,
            "Özel geçiş: manuel T4 / ileride nodal doğrulama",
        ),
        RouteSection(
            "RS-03 HDD", 280.0, "HDD", 6.0, 1.8, "CS-07", 25.0,
            EXTERNAL_THERMAL_MANUAL, 0.15, 1.35,
            "HDD: manuel T4 / ileride 2D-3D doğrulama",
        ),
    ])




CABLE_SOURCE_CATALOG = "CATALOG"
CABLE_SOURCE_MANUFACTURER_DRAWING = "MANUFACTURER_DRAWING"
CABLE_SOURCE_TEST_REPORT = "TEST_REPORT"
CABLE_SOURCE_CALCULATED = "CALCULATED"
CABLE_SOURCE_STANDARD_DERIVED = "STANDARD_DERIVED"
CABLE_SOURCE_USER_ASSUMPTION = "USER_ASSUMPTION"

CABLE_STATUS_DRAFT = "DRAFT"
CABLE_STATUS_CONDITIONAL = "CONDITIONAL"
CABLE_STATUS_VERIFIED = "VERIFIED"

CABLE_VALUE_CATALOG = "CATALOG_AVAILABLE"
CABLE_VALUE_DERIVED = "CALCULATED_DERIVED"
CABLE_VALUE_USER = "USER_ENTERED"
CABLE_VALUE_MANUFACTURER_REQUIRED = "MANUFACTURER_CONFIRMATION_REQUIRED"
CABLE_VALUE_ASSUMPTION = "ENGINEERING_ASSUMPTION"
CABLE_VALUE_MISSING = "MISSING"

CONFLICT_USE_SOURCE = "USE_SOURCE_RECORD"
CONFLICT_USE_USER_VALUE = "USE_USER_VALUE"
CONFLICT_CREATE_SCENARIOS = "CREATE_SEPARATE_SCENARIOS"
CONFLICT_UNRESOLVED = "UNRESOLVED"


@dataclass
class CableParameterSource:
    source_id: str
    source_type: str = CABLE_SOURCE_USER_ASSUMPTION
    document_title: str = ""
    document_revision: str = ""
    page_reference: str = ""
    file_name: str = ""
    file_sha256: str = ""
    entered_by: str = ""
    verified: bool = False
    notes: str = ""


@dataclass
class CableLayerData:
    layer_id: str
    name: str
    layer_type: str
    inner_diameter_mm: float
    outer_diameter_mm: float
    material: str = ""
    thermal_resistivity_km_w: float = 0.0
    relative_permittivity: float = 0.0
    dielectric_loss_tan_delta: float = 0.0
    conductor_area_mm2: float = 0.0
    wire_count: int = 0
    wire_diameter_mm: float = 0.0
    source_id: str = ""
    notes: str = ""


@dataclass
class CableCatalogRecord:
    record_id: str
    manufacturer: str
    series: str
    model: str
    voltage_class: str
    conductor_material: str
    conductor_area_mm2: float
    construction_type: str = "SINGLE_CORE_XLPE"
    standard: str = "IEC 60840 / IEC 62067"
    status: str = CABLE_STATUS_DRAFT
    cable_snapshot: dict[str, Any] = field(default_factory=dict)
    source_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    notes: str = ""

    # v0.15 catalog source values. These are deliberately separated from the
    # parametric calculation snapshot: a catalog ampacity is a reference
    # condition, not the project's final IEC/2D rating. Missing values remain
    # absent instead of being inferred from neighbouring products.
    catalog_dimensions: dict[str, Any] = field(default_factory=dict)
    catalog_electrical: dict[str, Any] = field(default_factory=dict)
    reference_conditions: dict[str, Any] = field(default_factory=dict)
    source_quality: str = "CATALOG_ONLY"
    source_page: str = ""


@dataclass
class CableLibraryData:
    records: list[CableCatalogRecord] = field(default_factory=list)
    sources: list[CableParameterSource] = field(default_factory=list)
    selected_record_id: str = ""
    package_name: str = "DiTuS yerel kablo kütüphanesi"
    package_revision: str = "0.1"
    package_source: str = "LOCAL_PROJECT"
    builtin_catalogs_loaded: bool = False


@dataclass
class CableData:
    """Common cable data shared by all electrical and thermal solvers.

    v0.15 keeps the proven scalar fields used by the existing solvers, while
    adding a traceable parametric construction.  Catalog records are copied
    into the project as immutable snapshots; calculations never depend on a
    mutable external catalog row.
    """

    cable_id: str = "CABLE-001"
    manufacturer: str = ""
    series: str = ""
    model: str = ""
    voltage_class: str = "87/150 (170) kV"
    construction_type: str = "SINGLE_CORE_XLPE"
    applicable_standard: str = "IEC 60840 / IEC 62067"
    catalog_record_id: str = ""
    snapshot_id: str = ""
    snapshot_hash: str = ""
    snapshot_created_at: str = ""
    data_status: str = CABLE_STATUS_DRAFT
    parametric_mode: bool = True
    conductor_stranding_type: str = "MILLIKEN"
    conductor_segment_count: int = 6
    conductor_wire_count: int = 0
    conductor_shape: str = "ROUND"
    conductor_insulation_system: str = "EXTRUDED"
    milliken_wire_profile: str = "UNKNOWN"
    skin_effect_coefficient_ks: float = 0.0  # 0 = IEC construction table resolver
    proximity_effect_coefficient_kp: float = 0.0  # 0 = IEC construction table resolver
    layers: list[CableLayerData] = field(default_factory=list)
    parameter_sources: list[CableParameterSource] = field(default_factory=list)
    validation_notes: list[str] = field(default_factory=list)

    name: str = "Kablo Sistemi 1"
    voltage_kv: float = 154.0
    frequency_hz: float = 50.0
    design_current_a: float = 800.0
    conductor_material: str = "Cu"
    conductor_area_mm2: float = 1200.0
    insulation: str = "XLPE"
    max_temperature_c: float = 90.0
    reference_ambient_c: float = 25.0
    arrangement: str = "Trefoil"

    # Electrical loss inputs
    dc_resistance_20_ohm_km: float = 0.0
    temperature_coefficient_20_per_c: float = 0.00393
    skin_effect_factor: float = 0.025
    proximity_effect_factor: float = 0.015
    capacitance_uf_km: float = 0.20
    dielectric_loss_tan_delta: float = 0.001
    sheath_loss_factor: float = 0.05
    armour_loss_factor: float = 0.0

    # Metallic sheath/screen inputs.
    sheath_material: str = "Cu"
    sheath_cross_section_mm2: float = 95.0
    sheath_dc_resistance_20_ohm_km: float = 0.0
    sheath_temperature_coefficient_20_per_c: float = 0.00393
    sheath_operating_temperature_c: float = 70.0
    sheath_mean_diameter_mm: float = 82.0
    sheath_gmr_mm: float = 0.0  # 0 = mean sheath radius approximation

    # Optional externally verified IEC 60287-1-1 λ1'' eddy-current component.
    # This is used only when the closed-form branch is outside its applicability
    # scope (for example CUSTOM geometry or multi-circuit installations).
    sheath_eddy_external_factor: float = 0.0
    sheath_eddy_external_source_type: str = ""
    sheath_eddy_external_reference: str = ""
    sheath_eddy_external_frequency_hz: float = 0.0
    sheath_eddy_external_sheath_temperature_c: float = 0.0
    sheath_eddy_external_d_mm: float = 0.0
    sheath_eddy_external_s_mm: float = 0.0
    sheath_eddy_external_formation_assumption: str = ""

    internal_thermal_mode: str = INTERNAL_THERMAL_AUTO
    thermal_resistance_t1_km_w: float = 0.30
    thermal_resistance_t2_km_w: float = 0.12
    thermal_resistance_t3_km_w: float = 0.05

    conductor_diameter_mm: float = 40.0
    conductor_gmr_mm: float = 0.0  # 0 = equivalent area-derived GMR
    t1_outer_diameter_mm: float = 76.0
    t2_outer_diameter_mm: float = 88.0
    overall_diameter_mm: float = 105.0
    t1_thermal_resistivity_km_w: float = 3.5
    t2_thermal_resistivity_km_w: float = 3.5
    t3_thermal_resistivity_km_w: float = 3.5

    conductors_per_cable: int = 1
    parallel_cables_per_phase: int = 1


def default_cable_sources(
    voltage_class: str = "87/150 (170) kV",
) -> list[CableParameterSource]:
    """Return provenance records for a manufacturer-free generic seed.

    The normative tables are referenced, not redistributed. Geometry details
    that standards do not prescribe remain explicit engineering assumptions.
    """
    from ucd.calculations.cable_template_generator import generic_parameter_sources

    return generic_parameter_sources(voltage_class)


def default_cable_layers(
    conductor_material: str = "Cu",
    conductor_area_mm2: float = 1200.0,
    overall_diameter_mm: float | None = None,
    *,
    voltage_class: str = "87/150 (170) kV",
    screen_area_mm2: float | None = None,
    screen_profile: str = "",
    stranding_type: str = "",
) -> list[CableLayerData]:
    """Build a continuous parametric layer chain.

    ``overall_diameter_mm`` is retained only as a backward-compatible call
    argument. It is deliberately ignored: FAZ 2 makes overall diameter an
    output of the conductor→insulation→screen→outer-sheath chain.
    """
    from ucd.calculations.cable_template_generator import build_parametric_layers

    return build_parametric_layers(
        conductor_material,
        conductor_area_mm2,
        voltage_class=voltage_class,
        screen_area_mm2=screen_area_mm2,
        screen_profile=screen_profile,
        stranding_type=stranding_type,
    )


def _generic_catalog_cable(
    cable_id: str, material: str, area: float, model: str, overall_diameter_mm: float
) -> CableData:
    """Backward-compatible helper returning a generated conditional cable."""
    from ucd.calculations.cable_template_generator import build_generic_cable, profile_id_for_voltage

    profile_id = profile_id_for_voltage("87/150 (170) kV")
    cable = build_generic_cable(
        record_id=cable_id,
        profile_id=profile_id,
        material=material,
        area_mm2=area,
        screen_area_mm2=150.0,
        screen_profile="HV-BOND-01",
        stranding="MILLIKEN",
    )
    cable.model = model
    return cable


def default_cable_library() -> CableLibraryData:
    """Return seven manufacturer-free generic seed templates."""
    from ucd.calculations.cable_template_generator import build_generic_template_library

    return build_generic_template_library()


@dataclass
class RouteSection:
    name: str
    length_m: float
    section_type: str = "Standart hendek"
    burial_depth_m: float = 1.2
    soil_thermal_resistivity_km_w: float = 1.2
    cross_section_id: str = "CS-01"
    ambient_temperature_c: float = 25.0
    external_thermal_mode: str = EXTERNAL_THERMAL_AUTO
    phase_spacing_m: float = 0.15
    external_thermal_resistance_t4_km_w: float = 0.85
    notes: str = ""
    start_chainage_m: float = 0.0
    end_chainage_m: float = 0.0
    thermal_region_id: str = ""
    thermal_template_id: str = ""
    thermal_data_state: str = THERMAL_STATE_DESIGN
    backfill_thermal_resistivity_km_w: float = 0.0
    backfill_effective_radius_m: float = 0.0
    surface_thermal_correction_km_w: float = 0.0
    # FAZ 6.4 partial soil dry-out cache. Material records remain the source
    # of truth; route/headless consumers receive the resolved native-soil pair.
    soil_critical_dryout_temperature_c: float = 0.0
    soil_dry_state_thermal_resistivity_km_w: float = 0.0
    soil_dryout_data_state: str = ""
    soil_dryout_source_reference: str = ""
    # FAZ 4 runtime geometry cache.  Accepted physical x-y remains the source
    # of truth; these fields keep route/headless consumers on the same snapshot.
    geometry_basis: str = "LEGACY_SCALAR"
    geometry_fingerprint: str = ""
    resolved_arrangement: str = ""
    bonding_circuit_id: str = ""
    bonding_parallel_index: int = 1
    phase_positions_m: dict[str, list[float]] = field(default_factory=dict)
    geometry_trace: list[str] = field(default_factory=list)


@dataclass
class BondingNode:
    """Cable accessory point on route.

    SECTIONALIZING_JOINT is deliberately separate from the surface/vault link
    box.  The link box is connected to this node with bonding leads.
    """

    node_id: str
    name: str
    position_m: float
    node_type: str = "SECTIONALIZING_JOINT"  # TERMINATION / SECTIONALIZING_JOINT
    earth_resistance_ohm: float = 0.20
    grounded: bool = False


@dataclass
class BondingLinkBox:
    link_box_id: str
    name: str
    joint_node_id: str
    position_m: float
    lead_length_m: float = 3.0
    lead_type: str = "COAXIAL"  # SINGLE / TWISTED_PAIR / COAXIAL
    contains_svl: bool = True
    accessible: bool = True
    svl_candidate_id: str = ""


@dataclass
class SvlCandidate:
    candidate_id: str
    manufacturer: str = ""
    model: str = ""
    technology: str = "MOV-ZnO"
    mcov_rms_v: float = 0.0
    tov_1s_rms_v: float = 0.0
    tov_10s_rms_v: float = 0.0
    tov_100s_rms_v: float = 0.0
    residual_voltage_peak_v: float = 0.0
    energy_capacity_kj: float = 0.0
    nominal_discharge_current_ka: float = 0.0
    connection_options: str = "STAR_GROUNDED,STAR_FLOATING,DELTA"
    source: str = "USER"
    notes: str = ""


def default_svl_candidates() -> list[SvlCandidate]:
    """Illustrative records used only to exercise the selection workflow.

    They are deliberately marked as non-procurement data and must be replaced
    with manufacturer-certified curves before project approval.
    """

    return [
        SvlCandidate(
            "DEMO-MOV-3", "ÖRNEK", "MOV 3 kV", "MOV-ZnO", 3000.0, 4200.0, 3600.0, 3300.0,
            9000.0, 12.0, 10.0, source="ILLUSTRATIVE_TEST_DATA",
            notes="Yalnız yazılım iş akışı testi; üretici verisi değildir.",
        ),
        SvlCandidate(
            "DEMO-MOV-6", "ÖRNEK", "MOV 6 kV", "MOV-ZnO", 6000.0, 8400.0, 7200.0, 6600.0,
            15000.0, 20.0, 10.0, source="ILLUSTRATIVE_TEST_DATA",
            notes="Yalnız yazılım iş akışı testi; üretici verisi değildir.",
        ),
        SvlCandidate(
            "DEMO-MOV-9", "ÖRNEK", "MOV 9 kV", "MOV-ZnO", 9000.0, 12600.0, 10800.0, 9900.0,
            21000.0, 30.0, 20.0, source="ILLUSTRATIVE_TEST_DATA",
            notes="Yalnız yazılım iş akışı testi; üretici verisi değildir.",
        ),
    ]


@dataclass
class SvlSystemData:
    name: str = "SVL ve Yalıtım Koordinasyonu"
    connection_mode: str = "STAR_GROUNDED"
    emergency_voltage_multiplier: float = 1.20
    continuous_voltage_margin_percent: float = 10.0

    # Values below zero/blank are not guessed. Fault and EMT studies supply them.
    fault_tov_rms_v: float = 0.0
    fault_tov_duration_s: float = 0.0
    required_energy_kj: float = 0.0
    required_discharge_current_ka: float = 0.0
    current_rise_ka_per_us: float = 0.0

    lead_inductance_uh_per_m: float = 1.0
    joint_interrupt_impulse_withstand_peak_v: float = 60000.0
    jacket_impulse_withstand_peak_v: float = 30000.0
    maximum_protective_level_fraction: float = 0.75
    energy_margin_percent: float = 20.0
    selected_candidate_id: str = ""
    candidates: list[SvlCandidate] = field(default_factory=default_svl_candidates)


FAULT_THREE_PHASE = "THREE_PHASE"
FAULT_PHASE_PHASE = "PHASE_PHASE"
FAULT_SINGLE_PHASE_GROUND = "SINGLE_PHASE_GROUND"


@dataclass
class FaultScenario:
    scenario_id: str
    name: str
    fault_type: str = FAULT_SINGLE_PHASE_GROUND
    fault_current_a: float = 31500.0
    faulted_phase: str = "A"
    second_phase: str = "B"
    duration_s: float = 0.50
    enabled: bool = True
    notes: str = ""


def default_fault_scenarios() -> list[FaultScenario]:
    return [
        FaultScenario("F-3PH", "Üç Faz Simetrik Arıza", FAULT_THREE_PHASE, 31500.0, "A", "B", 0.50, True),
        FaultScenario("F-PP", "Faz-Faz Arızası A-B", FAULT_PHASE_PHASE, 31500.0, "A", "B", 0.50, True),
        FaultScenario("F-SLG", "Tek Faz-Toprak Arızası A-G", FAULT_SINGLE_PHASE_GROUND, 31500.0, "A", "B", 0.50, True),
    ]


@dataclass
class FaultStudyData:
    name: str = "Arıza, EPR ve Power-Frequency TOV"
    solver_mode: str = "PRIMITIVE_CIM"
    auto_transfer_worst_tov_to_svl: bool = True
    tov_duration_multiplier: float = 2.0
    include_dielectric_charging_during_fault: bool = False
    scenarios: list[FaultScenario] = field(default_factory=default_fault_scenarios)


@dataclass
class BondingMinorSection:
    section_id: str
    name: str
    start_node_id: str
    end_node_id: str
    length_m: float
    phase_order: str = "ABC"
    route_reference: str = ""
    major_index: int = 1


@dataclass
class BondingConnection:
    # v0.5 uses link_box_id. node_id is retained for v0.4 JSON migration and
    # for transparent traceability of the associated sectionalizing joint.
    link_box_id: str = ""
    node_id: str = ""
    from_sheath: str = "A"
    to_sheath: str = "B"
    connection_type: str = "CROSS"  # CROSS / SOLID_GROUND


@dataclass
class BondingSystemData:
    name: str = "Cross-Bonding Sistemi"
    scheme: str = BONDING_CROSS
    phase_spacing_m: float = 0.15
    # Explicit target group for the current single-circuit IEEE 575 model.
    # Parallel-circuit magnetic coupling remains a later physical extension.
    target_circuit_id: str = "C1"
    target_parallel_index: int = 1
    link_box_contact_resistance_mohm: float = 0.20
    bonding_lead_resistance_mohm: float = 0.50
    bonding_lead_resistance_mohm_per_m: float = 0.058
    auto_apply_lambda1: bool = True

    # Design/optimization criteria.  These are project criteria, not universal
    # normative limits.  150 V is supplied as the initial 154 kV Turkey profile.
    normal_sheath_voltage_limit_v: float = 150.0
    maximum_bonding_lead_length_m: float = 15.0
    maximum_lambda1: float = 0.05
    optimization_max_iterations: int = 120
    optimization_snap_m: float = 1.0

    # Solver hierarchy. v0.8 introduces a primitive-conductor network solved
    # independently by CIM/MNA and Node Voltage. Legacy loop solvers remain as
    # transparent previews and regression references.
    solver_mode: str = "PRIMITIVE_CIM"  # PRIMITIVE_CIM / NODE_VOLTAGE / COUPLED_LOOP_MATRIX / INDEPENDENT_LOOP_PREVIEW
    sheath_mutual_coupling_enabled: bool = True

    # Primitive power-frequency network / earth-return settings.
    earth_resistivity_ohm_m: float = 100.0
    earth_return_model: str = "SIMPLIFIED_CARSON"
    include_dielectric_charging: bool = True
    compare_cim_nv: bool = True
    bonding_lead_inductance_uh_per_m: float = 0.35
    link_box_contact_inductance_uh: float = 0.10
    ground_bus_contact_resistance_mohm: float = 0.20
    minimum_branch_impedance_ohm: float = 1.0e-6

    # Optional parallel ground continuity / earth continuity conductor.
    gcc_enabled: bool = False
    gcc_material: str = "Cu"
    gcc_area_mm2: float = 240.0
    gcc_dc_resistance_20_ohm_km: float = 0.0
    gcc_temperature_coefficient_20_per_c: float = 0.00393
    gcc_operating_temperature_c: float = 60.0
    gcc_gmr_mm: float = 0.0
    gcc_x_offset_m: float = 0.0
    gcc_depth_offset_m: float = 0.30
    gcc_ground_at_major_boundaries: bool = True
    gcc_ground_at_link_boxes: bool = False

    nodes: list[BondingNode] = field(default_factory=list)
    link_boxes: list[BondingLinkBox] = field(default_factory=list)
    minor_sections: list[BondingMinorSection] = field(default_factory=list)
    connections: list[BondingConnection] = field(default_factory=list)


def _cyclic_connections(link_box_id: str, node_id: str) -> list[BondingConnection]:
    return [
        BondingConnection(link_box_id, node_id, "A", "B", "CROSS"),
        BondingConnection(link_box_id, node_id, "B", "C", "CROSS"),
        BondingConnection(link_box_id, node_id, "C", "A", "CROSS"),
    ]


def default_bonding_system(total_length_m: float = 1670.0) -> BondingSystemData:
    total = max(float(total_length_m), 3.0)
    boundaries = [0.0, total / 3.0, 2.0 * total / 3.0, total]
    nodes = [
        BondingNode("T1", "Başlangıç Terminasyonu", boundaries[0], "TERMINATION", 0.20, True),
        BondingNode("J1", "Sectionalizing Joint 1", boundaries[1], "SECTIONALIZING_JOINT", 0.0, False),
        BondingNode("J2", "Sectionalizing Joint 2", boundaries[2], "SECTIONALIZING_JOINT", 0.0, False),
        BondingNode("T2", "Bitiş Terminasyonu", boundaries[3], "TERMINATION", 0.20, True),
    ]
    link_boxes = [
        BondingLinkBox("LB1", "Link Box 1", "J1", boundaries[1], 3.0, "COAXIAL", True, True),
        BondingLinkBox("LB2", "Link Box 2", "J2", boundaries[2], 3.0, "COAXIAL", True, True),
    ]
    sections = [
        BondingMinorSection("MS1", "Minor Section 1", "T1", "J1", boundaries[1], "ABC", "", 1),
        BondingMinorSection("MS2", "Minor Section 2", "J1", "J2", boundaries[2] - boundaries[1], "ABC", "", 1),
        BondingMinorSection("MS3", "Minor Section 3", "J2", "T2", boundaries[3] - boundaries[2], "ABC", "", 1),
    ]
    connections = _cyclic_connections("LB1", "J1") + _cyclic_connections("LB2", "J2")
    return BondingSystemData(nodes=nodes, link_boxes=link_boxes, minor_sections=sections, connections=connections)


INSTALLATION_STATE_DRAFT = "DRAFT"
INSTALLATION_STATE_VERIFIED = "VERIFIED"
INSTALLATION_COORDINATE_SYSTEM = "X_HORIZONTAL_DEPTH_POSITIVE_DOWN_M"
INSTALLATION_COUPLING_DESIGN_ONLY = "DESIGN_ONLY"
INSTALLATION_COUPLING_LEGACY_PROJECTION = "LEGACY_PROJECTION"
INSTALLATION_COUPLING_PRODUCTION_LINKED = "PRODUCTION_LINKED"


@dataclass
class InstallationCircuitData:
    """Electrical loading and phase-sequence record for one physical circuit.

    ``load_current_a`` is the total RMS phase current of the circuit. Unless a
    physical cable has an explicit current override, this current is divided
    among the active parallel cables of the same phase or resolved by the
    production global network. ``load_factor`` is retained only for project-file
    compatibility; it MUST NOT scale steady-state RMS current. IEC 60853
    current-load and loss-load factors are derived from ``TransientLoadProfile``.
    """

    circuit_id: str
    name: str
    phase_order: str = "ABC"
    load_current_a: float = 0.0
    load_factor: float = 1.0
    active: bool = True
    cable_snapshot_id: str = ""
    notes: str = ""


@dataclass
class CableChannelGeometryData:
    """Parametric trench/channel geometry shared by the interactive editor.

    Coordinates use metres and positive depth below ground level.  The model
    is deliberately additive: legacy projects receive conservative defaults
    without changing existing cable coordinates or solver results.
    """

    geometry_mode: str = "PARAMETRIC_TRENCH"
    center_x_m: float = 0.0
    trench_width_m: float = 0.80
    trench_depth_m: float = 1.50
    side_slope_h_to_v: float = 0.0
    # ``bedding_thickness_m`` remains the solver-facing total sand-envelope
    # height.  v0.16.9.4.5 derives it from the real cable envelope plus the
    # construction covers below and above the cable group.
    bedding_thickness_m: float = 0.15
    bedding_bottom_cover_m: float = 0.10
    bedding_top_cover_m: float = 0.10
    bedding_side_clearance_m: float = 0.10
    cable_group_bottom_locked: bool = True
    thermal_backfill_height_m: float = 0.30
    selected_fill_thickness_m: float = 0.30
    warning_mesh_enabled: bool = True
    warning_mesh_offset_above_bedding_m: float = 0.20
    warning_tape_enabled: bool = True
    warning_tape_offset_above_bedding_m: float = 0.30
    spacer_enabled: bool = True
    spacer_height_m: float = 0.06
    spacer_width_m: float = 0.08
    surface_layer_thickness_m: float = 0.0
    cover_slab_enabled: bool = False
    cover_slab_width_m: float = 0.65
    cover_slab_thickness_m: float = 0.05
    cover_slab_depth_m: float = 0.55
    duct_bank_width_m: float = 0.90
    duct_bank_height_m: float = 0.55
    trough_inner_width_m: float = 0.75
    trough_inner_height_m: float = 0.75
    trough_wall_thickness_m: float = 0.10
    hdd_bore_diameter_m: float = 0.45
    tunnel_width_m: float = 2.00
    tunnel_height_m: float = 2.00
    native_soil_material_id: str = "MAT-NATIVE-01"
    bedding_material_id: str = "MAT-TB-01"
    thermal_backfill_material_id: str = "MAT-TB-01"
    selected_fill_material_id: str = "MAT-FILL-01"
    general_fill_material_id: str = "MAT-FILL-01"
    surface_material_id: str = ""
    cover_slab_material_id: str = "MAT-CONCRETE-01"
    duct_bank_material_id: str = "MAT-CONCRETE-01"
    trough_material_id: str = "MAT-CONCRETE-01"
    hdd_grout_material_id: str = "MAT-CONCRETE-01"
    source_reference: str = "LEGACY_PARAMETRIC_DEFAULT"
    notes: str = ""


@dataclass
class DuctSlotData:
    slot_id: str
    x_m: float
    depth_m: float
    inner_diameter_m: float = 0.13
    outer_diameter_m: float = 0.16
    row_index: int = 1
    column_index: int = 1
    active: bool = True
    notes: str = ""


@dataclass
class ThermalMaterialRegionData:
    """User-defined 2D thermal material polygon in section coordinates.

    Vertices are ``[x_m, depth_m]`` pairs.  Higher ``priority`` regions
    override lower-priority user regions, while physical duct/slab/cable
    objects remain authoritative in the nodal material map.
    """

    region_id: str
    name: str
    material_id: str
    vertices_m: list[list[float]] = field(default_factory=list)
    priority: int = 100
    active: bool = True
    role: str = "CUSTOM_THERMAL_REGION"
    source_reference: str = "USER_INTERACTIVE_GEOMETRY"
    notes: str = ""


@dataclass
class ExternalHeatSourceData:
    source_id: str
    name: str
    x_m: float
    depth_m: float
    heat_w_m: float = 0.0
    effective_radius_m: float = 0.05
    active: bool = True
    source_type: str = "OTHER_CABLE_OR_PIPE"
    notes: str = ""


@dataclass
class PhysicalCableData:
    physical_cable_id: str
    circuit_id: str
    phase: str
    parallel_index: int
    x_m: float
    depth_m: float
    cable_snapshot_id: str = ""
    duct_slot_id: str = ""
    rotation_deg: float = 0.0
    current_override_a: float = 0.0
    current_angle_override_deg: float | None = None
    load_factor: float = 1.0
    active: bool = True
    notes: str = ""


@dataclass
class InstallationCrossSectionData:
    cross_section_id: str
    name: str
    installation_type: str = THERMAL_INSTALL_DIRECT_BURIED
    arrangement_label: str = "CUSTOM"
    region_ids: list[str] = field(default_factory=list)
    circuits: list[InstallationCircuitData] = field(default_factory=list)
    physical_cables: list[PhysicalCableData] = field(default_factory=list)
    duct_slots: list[DuctSlotData] = field(default_factory=list)
    material_regions: list[ThermalMaterialRegionData] = field(default_factory=list)
    external_heat_sources: list[ExternalHeatSourceData] = field(default_factory=list)
    channel_geometry: CableChannelGeometryData = field(default_factory=CableChannelGeometryData)
    coordinate_system: str = INSTALLATION_COORDINATE_SYSTEM
    data_state: str = INSTALLATION_STATE_DRAFT
    source_reference: str = ""
    notes: str = ""


@dataclass
class InstallationDesignData:
    name: str = "Kablo-Kanal Düzeni ve Fiziksel Kesit Modeli"
    active_cross_section_id: str = ""
    cross_sections: list[InstallationCrossSectionData] = field(default_factory=list)
    solver_coupling_mode: str = INSTALLATION_COUPLING_PRODUCTION_LINKED
    model_revision: str = "0.16.9.4.34"
    notes: str = ""


def _installation_phase_positions(
    arrangement: str,
    burial_depth_m: float,
    phase_spacing_m: float,
) -> dict[str, tuple[float, float]]:
    from math import sqrt

    depth = max(float(burial_depth_m), 0.01)
    spacing = max(float(phase_spacing_m), 0.001)
    normalized = str(arrangement).strip().lower()
    if normalized in {"flat", "düz", "duz", "horizontal"}:
        return {"A": (-spacing, depth), "B": (0.0, depth), "C": (spacing, depth)}
    if normalized in {"vertical", "düşey", "dusey"}:
        return {"A": (0.0, max(0.01, depth - spacing)), "B": (0.0, depth), "C": (0.0, depth + spacing)}
    lower = depth + sqrt(3.0) * spacing / 2.0
    return {"A": (0.0, depth), "B": (-spacing / 2.0, lower), "C": (spacing / 2.0, lower)}


def _normalise_legacy_trefoil_contact(
    section: InstallationCrossSectionData,
    cable_outer_diameter_m: float,
    source_model_revision: str = "",
) -> None:
    """Lock legacy-generated TREFOIL groups to touching cable centres.

    Only generated legacy projections are normalised. User-authored CUSTOM or
    verified physical coordinates are never moved during project loading.
    """

    def revision_key(value: str) -> tuple[int, ...]:
        parts: list[int] = []
        for token in str(value or "").replace("-", ".").split("."):
            digits = "".join(ch for ch in token if ch.isdigit())
            if digits:
                parts.append(int(digits))
        return tuple(parts)

    # v0.16.9.4.11 and later already save the intended physical coordinates.
    # Never reinterpret a current/future project's user-edited geometry.
    source_key = revision_key(source_model_revision)
    if source_key and source_key >= revision_key("0.16.9.4.11"):
        return
    if str(section.arrangement_label or "").strip().upper() != "TREFOIL":
        return
    if str(section.source_reference or "").strip().upper() not in {
        "LEGACY_PROJECT_PROJECTION", "MIGRATED_PARAMETRIC_DEFAULT"
    }:
        return
    from math import hypot, sqrt

    spacing = max(0.001, float(cable_outer_diameter_m))
    groups = sorted({
        (item.circuit_id, int(item.parallel_index))
        for item in section.physical_cables if item.active
    })
    for circuit_id, parallel_index in groups:
        by_phase = {
            str(item.phase).upper(): item
            for item in section.physical_cables
            if item.active and item.circuit_id == circuit_id
            and int(item.parallel_index) == parallel_index
            and str(item.phase).upper() in {"A", "B", "C"}
        }
        if set(by_phase) != {"A", "B", "C"}:
            continue
        distances = [
            hypot(
                float(by_phase[first].x_m) - float(by_phase[second].x_m),
                float(by_phase[first].depth_m) - float(by_phase[second].depth_m),
            )
            for first, second in (("A", "B"), ("B", "C"), ("C", "A"))
        ]
        mean_distance = sum(distances) / 3.0
        # A legacy auto-generated TREFOIL is equilateral.  If the user has
        # moved even one phase independently, preserve those authored x-y
        # coordinates instead of treating them as a migration default.
        if max(distances) - min(distances) > max(1.0e-6, mean_distance * 1.0e-5):
            continue
        x_center = (min(item.x_m for item in by_phase.values()) + max(item.x_m for item in by_phase.values())) / 2.0
        top_depth = min(float(item.depth_m) for item in by_phase.values())
        lower_depth = top_depth + sqrt(3.0) * spacing / 2.0
        by_phase["A"].x_m, by_phase["A"].depth_m = x_center, top_depth
        by_phase["B"].x_m, by_phase["B"].depth_m = x_center - spacing / 2.0, lower_depth
        by_phase["C"].x_m, by_phase["C"].depth_m = x_center + spacing / 2.0, lower_depth
    note = "v0.16.9.4.11 legacy TREFOIL merkezleri gerçek kablo dış çapına kilitlendi."
    if note not in section.notes:
        section.notes = (section.notes + " " + note).strip()


def _normalise_rounded_trefoil_contact(
    section: InstallationCrossSectionData,
    cable_outer_diameter_m: float,
) -> None:
    """Repair exact TREFOIL contact lost only through coordinate display rounding.

    Current projects may contain five-decimal x/depth values written by the
    Kablo-Kanal table.  A complete, near-equilateral group whose three sides
    are already within the coordinate-resolution tolerance of the selected
    cable diameter is snapped back to the exact diameter.  Other authored
    geometries are untouched.
    """

    from math import hypot, sqrt

    diameter = max(0.001, float(cable_outer_diameter_m))
    tolerance = max(2.0e-5, diameter * 5.0e-4)
    groups = sorted({
        (str(item.circuit_id), int(item.parallel_index))
        for item in section.physical_cables if item.active
    })
    repaired = 0
    for circuit_id, parallel_index in groups:
        by_phase = {
            str(item.phase).upper(): item
            for item in section.physical_cables
            if item.active and str(item.circuit_id) == circuit_id
            and int(item.parallel_index) == parallel_index
            and str(item.phase).upper() in {"A", "B", "C"}
        }
        if set(by_phase) != {"A", "B", "C"}:
            continue
        distances = [
            hypot(
                float(by_phase[first].x_m) - float(by_phase[second].x_m),
                float(by_phase[first].depth_m) - float(by_phase[second].depth_m),
            )
            for first, second in (("A", "B"), ("B", "C"), ("C", "A"))
        ]
        contact_errors = [abs(value - diameter) for value in distances]
        if max(contact_errors) > tolerance:
            continue
        if max(contact_errors) <= 1.0e-12:
            continue
        center_x = sum(float(by_phase[phase].x_m) for phase in "ABC") / 3.0
        center_depth = sum(float(by_phase[phase].depth_m) for phase in "ABC") / 3.0
        lower = sqrt(3.0) * diameter / 2.0
        offsets = {
            "A": (0.0, -2.0 * lower / 3.0),
            "B": (-diameter / 2.0, lower / 3.0),
            "C": (diameter / 2.0, lower / 3.0),
        }
        for phase in "ABC":
            dx, dy = offsets[phase]
            by_phase[phase].x_m = center_x + dx
            by_phase[phase].depth_m = center_depth + dy
        repaired += 1
    if repaired:
        note = f"v0.16.9.4.14 {repaired} TREFOIL grup koordinat yuvarlaması gerçek dış çapa yeniden kilitlendi."
        if note not in section.notes:
            section.notes = (section.notes + " " + note).strip()


def _default_channel_geometry_for_section(
    installation_type: str,
    *,
    trench_width_m: float = 0.80,
    trench_depth_m: float = 1.50,
    bedding_thickness_m: float = 0.15,
    thermal_backfill_height_m: float = 0.30,
    selected_fill_thickness_m: float = 0.30,
    surface_layer_thickness_m: float = 0.0,
    duct_bank_width_m: float = 0.90,
    duct_bank_height_m: float = 0.55,
) -> CableChannelGeometryData:
    kind = str(installation_type or THERMAL_INSTALL_DIRECT_BURIED).upper()
    geometry = CableChannelGeometryData(
        trench_width_m=max(0.20, float(trench_width_m)),
        trench_depth_m=max(0.30, float(trench_depth_m)),
        bedding_thickness_m=max(0.0, float(bedding_thickness_m)),
        thermal_backfill_height_m=max(0.0, float(thermal_backfill_height_m)),
        selected_fill_thickness_m=max(0.0, float(selected_fill_thickness_m)),
        surface_layer_thickness_m=max(0.0, float(surface_layer_thickness_m)),
        duct_bank_width_m=max(0.10, float(duct_bank_width_m)),
        duct_bank_height_m=max(0.10, float(duct_bank_height_m)),
    )
    if kind == THERMAL_INSTALL_DUCT_BANK:
        geometry.geometry_mode = "PARAMETRIC_DUCT_BANK"
        geometry.thermal_backfill_height_m = 0.0
    elif kind == THERMAL_INSTALL_CONCRETE_TROUGH:
        geometry.geometry_mode = "PARAMETRIC_CONCRETE_TROUGH"
        geometry.thermal_backfill_height_m = 0.0
    elif kind == THERMAL_INSTALL_HDD:
        geometry.geometry_mode = "PARAMETRIC_HDD"
        geometry.cover_slab_enabled = False
        geometry.thermal_backfill_height_m = 0.0
    elif kind == THERMAL_INSTALL_TUNNEL:
        geometry.geometry_mode = "PARAMETRIC_TUNNEL"
        geometry.cover_slab_enabled = False
        geometry.thermal_backfill_height_m = 0.0
    return geometry


def default_installation_design(
    cable: CableData,
    basis: DesignBasisData,
    thermal_design: ThermalDesignData,
) -> InstallationDesignData:
    """Project legacy scalars into explicit, region-bound physical objects.

    Each existing thermal region receives its own draft physical cross-section.
    This avoids presenting a duct-bank or HDD region as though it shared the
    first route region's geometry.  From v0.16.9.4.11 the accepted section is
    production-linked through a compatibility adapter: analytical kernels keep
    their equations while their geometry-dependent inputs are refreshed.
    """

    templates = {item.template_id: item for item in thermal_design.templates}
    regions = list(thermal_design.regions) or [None]
    circuit_count = max(1, int(basis.circuit_count or basis.active_circuit_count or 1))
    parallel_count = max(1, int(cable.parallel_cables_per_phase or 1))
    total_current = float(basis.design_current_per_circuit_a or 0.0)
    if total_current <= 0:
        total_current = float(cable.design_current_a) * parallel_count

    cross_sections: list[InstallationCrossSectionData] = []
    for section_index, region in enumerate(regions, start=1):
        template = templates.get(region.template_id) if region is not None else None
        overrides = dict(getattr(region, "overrides", {}) or {})

        def numeric(name: str, fallback: float) -> float:
            value = overrides.get(name, getattr(template, name, fallback))
            try:
                return float(value)
            except (TypeError, ValueError):
                return float(fallback)

        arrangement = str(getattr(template, "arrangement", cable.arrangement) or cable.arrangement)
        installation_type = str(
            getattr(template, "installation_type", THERMAL_INSTALL_DIRECT_BURIED)
            or THERMAL_INSTALL_DIRECT_BURIED
        )
        burial_depth = max(0.01, numeric("burial_depth_m", basis.burial_depth_m))
        phase_spacing = max(0.001, numeric("phase_spacing_m", basis.phase_spacing_m))
        if str(arrangement).strip().upper() == "TREFOIL":
            phase_spacing = max(0.001, cable.overall_diameter_mm / 1000.0)
        circuit_spacing = max(0.001, numeric("circuit_spacing_m", basis.circuit_spacing_m))
        parallel_spacing = max(
            numeric("parallel_cable_spacing_m", 0.20),
            cable.overall_diameter_mm / 1000.0 * 1.10,
        )
        base = _installation_phase_positions(arrangement, burial_depth, phase_spacing)

        circuits: list[InstallationCircuitData] = []
        physical: list[PhysicalCableData] = []
        for circuit_index in range(1, circuit_count + 1):
            circuit_id = f"C{circuit_index}"
            circuits.append(InstallationCircuitData(
                circuit_id,
                f"Devre {circuit_index}",
                "ABC",
                total_current,
                1.0,
                circuit_index <= max(1, int(basis.active_circuit_count or circuit_count)),
                cable.snapshot_id,
                "v0.16.2.x devre/yük girdilerinden başlangıç kaydı üretildi.",
            ))
            circuit_offset = (circuit_index - (circuit_count + 1) / 2.0) * circuit_spacing
            for phase in ("A", "B", "C"):
                x0, depth0 = base[phase]
                for parallel_index in range(1, parallel_count + 1):
                    parallel_offset = (parallel_index - (parallel_count + 1) / 2.0) * parallel_spacing
                    physical.append(PhysicalCableData(
                        f"{circuit_id}-{phase}-{parallel_index}",
                        circuit_id,
                        phase,
                        parallel_index,
                        x0 + circuit_offset + parallel_offset,
                        depth0,
                        cable.snapshot_id,
                    ))

        region_id = str(getattr(region, "region_id", "") or "")
        region_name = str(getattr(region, "name", "") or "")
        cross_section_id = f"ICS-{section_index:02d}"
        notes = (
            "v0.16.3 legacy projection; kullanıcı gerçek x-y/slot/faz sırası doğrulaması yapmalıdır."
        )
        if installation_type == THERMAL_INSTALL_DUCT_BANK:
            notes += " Duct slot bilgisi eski skaler girdilerde bulunmadığı için ayrıca atanmalıdır."
        trench_depth = numeric("trench_depth_m", max(1.50, burial_depth + 0.30))
        bedding_thickness = numeric("bedding_thickness_m", 0.15)
        legacy_cable_cover = numeric("cable_cover_height_m", 0.30)
        # ThermalCrossSectionTemplate.cable_cover_height_m is the vertical
        # cover above the cable crown. CableChannelGeometryData stores the
        # full engineered-backfill layer measured upward from bedding.  The
        # migration therefore includes the distance between bedding top and
        # cable crown; otherwise old projects would silently lose cover and
        # their BOQ/thermal results would jump merely by opening v0.16.9.4.11.
        cable_radius = max(0.0005, cable.overall_diameter_mm / 2000.0)
        cable_crown = min((item.depth_m - cable_radius for item in physical), default=burial_depth - cable_radius)
        bedding_top = max(0.0, trench_depth - bedding_thickness)
        migrated_backfill_layer = max(0.0, bedding_top - cable_crown + legacy_cable_cover)
        channel_geometry = _default_channel_geometry_for_section(
            installation_type,
            trench_width_m=numeric("trench_width_m", 0.80),
            trench_depth_m=trench_depth,
            bedding_thickness_m=bedding_thickness,
            thermal_backfill_height_m=migrated_backfill_layer,
            selected_fill_thickness_m=numeric("selected_upper_fill_thickness_m", 0.0),
            surface_layer_thickness_m=numeric("surface_layer_thickness_m", 0.0),
            duct_bank_width_m=numeric("duct_bank_width_m", 0.90),
            duct_bank_height_m=numeric("duct_bank_height_m", 0.55),
        )
        channel_geometry.native_soil_material_id = str(getattr(template, "native_soil_material_id", "MAT-NATIVE-01"))
        channel_geometry.bedding_material_id = str(getattr(template, "bedding_material_id", "MAT-TB-01"))
        channel_geometry.thermal_backfill_material_id = str(getattr(template, "side_backfill_material_id", "MAT-TB-01"))
        channel_geometry.selected_fill_material_id = str(getattr(template, "selected_upper_fill_material_id", "MAT-FILL-01"))
        channel_geometry.general_fill_material_id = str(getattr(template, "general_fill_material_id", "MAT-FILL-01"))
        channel_geometry.surface_material_id = str(getattr(template, "surface_material_id", ""))
        channel_geometry.duct_bank_material_id = str(getattr(template, "grout_material_id", "MAT-CONCRETE-01"))
        cross_sections.append(InstallationCrossSectionData(
            cross_section_id=cross_section_id,
            name=(f"{region_id} — {region_name}" if region_id else "Ana fiziksel kurulum kesiti"),
            installation_type=installation_type,
            arrangement_label=arrangement.upper(),
            region_ids=[region_id] if region_id else [],
            circuits=circuits,
            physical_cables=physical,
            duct_slots=[],
            external_heat_sources=[],
            channel_geometry=channel_geometry,
            coordinate_system=INSTALLATION_COORDINATE_SYSTEM,
            data_state=INSTALLATION_STATE_DRAFT,
            source_reference="LEGACY_PROJECT_PROJECTION",
            notes=notes,
        ))

    return InstallationDesignData(
        active_cross_section_id=cross_sections[0].cross_section_id,
        cross_sections=cross_sections,
        solver_coupling_mode=INSTALLATION_COUPLING_PRODUCTION_LINKED,
        notes="v0.16.9.4.14 TREFOIL merkezleri gerçek kablo dış çapına otomatik kilitlenir; koordinat gösterim yuvarlaması temas geometrisini çakışmaya çeviremez. Fiziksel Kablo-Kanal geometrisi üretim hesaplarına bağlıdır.",
    )


@dataclass
class CableCompletionItem:
    item_id: str
    parameter_key: str
    label: str
    category: str
    status: str = CABLE_VALUE_MISSING
    value: Any = None
    unit: str = ""
    source_reference: str = ""
    required_for: list[str] = field(default_factory=list)
    blocking: bool = False
    notes: str = ""


@dataclass
class RouteCableAssignment:
    assignment_id: str
    route_section_name: str
    cable_snapshot_id: str
    catalog_record_id: str = ""
    parallel_cables_per_phase: int = 1
    active: bool = True
    notes: str = ""


@dataclass
class SourceConflictDecision:
    conflict_id: str
    action: str = CONFLICT_UNRESOLVED
    selected_record_ids: list[str] = field(default_factory=list)
    resolved_value: Any = None
    unit: str = ""
    rationale: str = ""
    decided_by: str = ""
    decided_at: str = ""


@dataclass
class CableApplicationData:
    selected_candidate_id: str = ""
    selected_catalog_record_id: str = ""
    applied_snapshot_id: str = ""
    applied_snapshot_hash: str = ""
    assignments: list[RouteCableAssignment] = field(default_factory=list)
    completion_items: list[CableCompletionItem] = field(default_factory=list)
    conflict_decisions: list[SourceConflictDecision] = field(default_factory=list)
    application_status: str = "NOT_APPLIED"
    last_iteration_status: str = "NOT_RUN"
    last_iteration_trace: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class ProcurementQuantityOverride:
    item_id: str
    quantity: float
    rationale: str = ""
    entered_by: str = ""
    entered_at: str = ""


@dataclass
class ProcurementData:
    """Project-level BOQ/BOM/RFQ settings and traceable quantity overrides.

    Percentages and reserves are explicit project assumptions. The quantity
    engine never hides them inside route length or accessory counts.
    """

    installation_allowance_percent: float = 1.0
    waste_percent: float = 2.0
    spare_cable_percent: float = 0.0
    termination_tail_m_per_end: float = 5.0
    joint_tail_m_per_side: float = 2.0
    bonding_lead_allowance_percent: float = 10.0
    warning_tape_allowance_percent: float = 5.0
    cable_cleat_spacing_m: float = 1.0
    maximum_drum_length_m: float = 1000.0
    drum_length_rounding_m: float = 1.0
    spare_joint_units: int = 0
    spare_termination_units: int = 0
    spare_link_box_units: int = 0
    spare_svl_units: int = 0
    include_civil_items: bool = True
    include_marking_accessories: bool = True
    include_grounding_items: bool = True
    quantity_overrides: list[ProcurementQuantityOverride] = field(default_factory=list)
    notes: str = ""


CALC_METHOD_PHYSICAL_AUTO = "PHYSICAL_AUTO"
CALC_METHOD_CERTIFIED_INPUT = "CERTIFIED_INPUT"
CALC_METHOD_MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
CALC_METHOD_LEGACY_COEFFICIENT = "LEGACY_COEFFICIENT"

CALC_STATUS_CALCULATED = "CALCULATED"
CALC_STATUS_VERIFIED = "VERIFIED"
CALC_STATUS_PRELIMINARY_ONLY = "PRELIMINARY_ONLY"
CALC_STATUS_REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"


@dataclass
class ParameterProvenanceRecord:
    """Traceable metadata for a solver input or derived parameter.

    ``value_snapshot`` records the value at the moment the provenance was
    declared.  The actual engineering value remains in its existing v0.16.3
    scalar field, so this additive layer cannot silently change a solver input.
    """

    record_id: str
    parameter_path: str
    label: str
    value_snapshot: Any
    unit: str = ""
    category: str = ""
    method: str = CALC_METHOD_LEGACY_COEFFICIENT
    status: str = CALC_STATUS_PRELIMINARY_ONLY
    source_type: str = "PROJECT_INPUT"
    source_reference: str = ""
    source_page: str = ""
    standard_reference: str = ""
    validity_scope: str = ""
    confidence: str = "MEDIUM"
    override_reason: str = ""
    notes: str = ""
    updated_at: str = ""


PHYSICAL_PARAMETER_MODE_SHADOW = "SHADOW_COMPARE"
PHYSICAL_PARAMETER_MODE_DISABLED = "DISABLED"


@dataclass
class PhysicalParameterStudyData:
    """Configuration and last shadow result of the v0.16.4 cable-parameter engine.

    The engine is intentionally non-authoritative in v0.16.4.  It calculates
    physical alternatives and compares them with the locked scalar inputs, but
    it cannot replace values used by IEC 60287 or other legacy solvers.
    """

    mode: str = PHYSICAL_PARAMETER_MODE_SHADOW
    target_temperature_c: float = 90.0
    selected_route_section_name: str = ""
    persist_last_result: bool = True
    last_run_at: str = ""
    last_result: dict[str, Any] = field(default_factory=dict)
    model_revision: str = "0.16.4"
    notes: str = (
        "v0.16.4 SHADOW_COMPARE: fiziksel parametreler hesaplanır ve kilitli "
        "v0.16.3.1 skaler girdilerle karşılaştırılır; solver girdileri değiştirilmez."
    )


@dataclass
class CalculationPolicyData:
    """Project-level policy for physical, certified and legacy inputs."""

    default_method: str = CALC_METHOD_PHYSICAL_AUTO
    allow_legacy_for_preliminary: bool = True
    block_final_with_legacy_coefficients: bool = True
    parameter_records: list[ParameterProvenanceRecord] = field(default_factory=list)
    policy_revision: str = "0.16.4"
    last_audited_at: str = ""
    notes: str = (
        "v0.16.4 fiziksel kablo parametrelerini shadow-mode hesaplar; kilitli "
        "v0.16.3.1 solver girdilerini ve sayısal sonuç yolunu değiştirmez."
    )


@dataclass
class ProjectData:
    schema_version: str = PROJECT_SCHEMA_VERSION
    project_name: str = "Yeni DiTuS Kablo Projesi"
    project_code: str = "DITUS-KBL-001"
    description: str = ""
    standards_profile: str = "IEC + IEEE + CIGRE"
    design_basis: DesignBasisData = field(default_factory=DesignBasisData)
    design_progress: DesignProgressData = field(default_factory=DesignProgressData)
    workflow: DesignWorkflowData = field(default_factory=DesignWorkflowData)
    cable: CableData = field(default_factory=CableData)
    cable_library: CableLibraryData = field(default_factory=CableLibraryData)
    cable_application: CableApplicationData = field(default_factory=CableApplicationData)
    procurement: ProcurementData = field(default_factory=ProcurementData)
    installation_design: InstallationDesignData = field(default_factory=InstallationDesignData)
    route_sections: list[RouteSection] = field(
        default_factory=lambda: [
            RouteSection("RS-01 Standart hendek", 1250.0),
            RouteSection(
                "RS-02 Yol geçişi", 140.0, "Beton kanal", 1.5, 1.5, "CS-04", 30.0,
                EXTERNAL_THERMAL_MANUAL, 0.15, 1.10,
                "Özel geçiş: manuel T4 / ileride nodal doğrulama",
            ),
            RouteSection(
                "RS-03 HDD", 280.0, "HDD", 6.0, 1.8, "CS-07", 25.0,
                EXTERNAL_THERMAL_MANUAL, 0.15, 1.35,
                "HDD: manuel T4 / ileride 2D-3D doğrulama",
            ),
        ]
    )
    thermal_design: ThermalDesignData = field(default_factory=default_thermal_design)
    bonding: BondingSystemData = field(default_factory=default_bonding_system)
    svl: SvlSystemData = field(default_factory=SvlSystemData)
    fault_study: FaultStudyData = field(default_factory=FaultStudyData)
    transient_study: TransientThermalStudyData = field(default_factory=TransientThermalStudyData)
    source_audit: ProjectSourceAuditData = field(default_factory=ProjectSourceAuditData)
    calculation_policy: CalculationPolicyData = field(default_factory=CalculationPolicyData)
    physical_parameter_study: PhysicalParameterStudyData = field(default_factory=PhysicalParameterStudyData)
    cad_source: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    modified_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def __post_init__(self) -> None:
        generated_cable_layers = not self.cable.layers
        if generated_cable_layers:
            self.cable.layers = default_cable_layers(
                self.cable.conductor_material,
                self.cable.conductor_area_mm2,
                self.cable.overall_diameter_mm,
                voltage_class=self.cable.voltage_class,
                screen_area_mm2=self.cable.sheath_cross_section_mm2,
                stranding_type=self.cable.conductor_stranding_type,
            )
        if not self.cable.parameter_sources:
            self.cable.parameter_sources = default_cable_sources(self.cable.voltage_class)
        if not self.cable_library.records:
            self.cable_library = default_cable_library()

        # FAZ 2 invariant: every seed, imported project and catalog snapshot is
        # synchronized before any solver or UI can observe stale scalar geometry.
        from ucd.calculations.cable_library import (
            normalize_catalog_library,
            synchronize_cable_from_layers,
            update_cable_validation_state,
        )

        synchronize_cable_from_layers(
            self.cable, overwrite_electrical=generated_cable_layers
        )
        update_cable_validation_state(self.cable)
        normalize_catalog_library(self.cable_library)

        if not self.installation_design.cross_sections:
            self.installation_design = default_installation_design(
                self.cable, self.design_basis, self.thermal_design
            )

    def to_dict(self, *, touch_modified: bool = False) -> dict[str, Any]:
        """Serialize without changing engineering values.

        Missing v0.16.4 provenance records are materialized before save; this
        touches metadata only and leaves all locked solver scalar inputs intact.
        """
        from ucd.calculations.calculation_policy import bootstrap_calculation_policy

        bootstrap_calculation_policy(self)
        if touch_modified:
            self.modified_at = datetime.now().isoformat(timespec="seconds")
        payload = asdict(self)
        payload["schema_version"] = PROJECT_SCHEMA_VERSION
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProjectData":
        """Load v0.1-v0.16.4 projects while preserving legacy inputs, reports and procurement settings."""

        schema_version = str(raw.get("schema_version", "0.1"))
        cable_raw = dict(raw.get("cable", {}))
        route_raw = [dict(item) for item in raw.get("route_sections", [])]

        version_key = schema_version_tuple(schema_version)
        if version_key is not None:
            legacy_thermal = version_key < (0, 3)
        else:
            legacy_thermal = schema_version not in {"0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "0.10", "0.11", "0.12", "0.13", "0.14", "0.15", "0.16", "0.16.1", "0.16.2", "0.16.3", "0.16.3.1", "0.16.4"}

        if legacy_thermal and "internal_thermal_mode" not in cable_raw:
            cable_raw["internal_thermal_mode"] = INTERNAL_THERMAL_MANUAL
        if legacy_thermal:
            for item in route_raw:
                item.setdefault("external_thermal_mode", EXTERNAL_THERMAL_MANUAL)
                item.setdefault("phase_spacing_m", 0.15)

        layer_fields = CableLayerData.__dataclass_fields__
        source_fields = CableParameterSource.__dataclass_fields__
        raw_cable_layers = [
            CableLayerData(**{k: v for k, v in dict(item).items() if k in layer_fields})
            for item in cable_raw.get("layers", []) if isinstance(item, dict)
        ]
        raw_cable_sources = [
            CableParameterSource(**{k: v for k, v in dict(item).items() if k in source_fields})
            for item in cable_raw.get("parameter_sources", []) if isinstance(item, dict)
        ]
        cable_raw["layers"] = raw_cable_layers
        cable_raw["parameter_sources"] = raw_cable_sources

        cable_fields = CableData.__dataclass_fields__
        route_fields = RouteSection.__dataclass_fields__
        routes = [
            RouteSection(**{k: v for k, v in item.items() if k in route_fields}) for item in route_raw
        ] or cls().route_sections

        thermal_raw = raw.get("thermal_design")
        if not isinstance(thermal_raw, dict):
            thermal_design = thermal_design_from_route_sections(routes)
        else:
            material_fields = ThermalMaterialData.__dataclass_fields__
            template_fields = ThermalCrossSectionTemplate.__dataclass_fields__
            region_fields = ThermalRegion.__dataclass_fields__
            design_fields = ThermalDesignData.__dataclass_fields__
            materials = [
                ThermalMaterialData(**{k: v for k, v in dict(item).items() if k in material_fields})
                for item in thermal_raw.get("materials", []) if isinstance(item, dict)
            ]
            templates = [
                ThermalCrossSectionTemplate(**{k: v for k, v in dict(item).items() if k in template_fields})
                for item in thermal_raw.get("templates", []) if isinstance(item, dict)
            ]
            regions = [
                ThermalRegion(**{k: v for k, v in dict(item).items() if k in region_fields})
                for item in thermal_raw.get("regions", []) if isinstance(item, dict)
            ]
            base_thermal = {
                k: v for k, v in thermal_raw.items()
                if k in design_fields and k not in {"materials", "templates", "regions"}
            }
            fallback_thermal = thermal_design_from_route_sections(routes)
            # v0.12 adds explicit duct and duct-fill materials. Preserve all
            # user records while appending only genuinely missing defaults so
            # older projects can run the 2D solver without silent replacement.
            if materials:
                material_ids = {item.material_id for item in materials}
                materials = materials + [
                    item for item in fallback_thermal.materials
                    if item.material_id not in material_ids
                ]
            else:
                materials = fallback_thermal.materials
            thermal_design = ThermalDesignData(
                **base_thermal,
                materials=materials,
                templates=templates or fallback_thermal.templates,
                regions=regions or fallback_thermal.regions,
            )

        bonding_raw = raw.get("bonding")
        if not isinstance(bonding_raw, dict):
            bonding = default_bonding_system(sum(section.length_m for section in routes))
        else:
            node_fields = BondingNode.__dataclass_fields__
            link_fields = BondingLinkBox.__dataclass_fields__
            minor_fields = BondingMinorSection.__dataclass_fields__
            connection_fields = BondingConnection.__dataclass_fields__
            bonding_fields = BondingSystemData.__dataclass_fields__
            base_kwargs = {
                k: v for k, v in bonding_raw.items()
                if k in bonding_fields and k not in {"nodes", "link_boxes", "minor_sections", "connections"}
            }

            raw_nodes = [dict(item) for item in bonding_raw.get("nodes", [])]
            raw_minors = [dict(item) for item in bonding_raw.get("minor_sections", [])]
            raw_connections = [dict(item) for item in bonding_raw.get("connections", [])]
            raw_link_boxes = [dict(item) for item in bonding_raw.get("link_boxes", [])]

            # v0.4 represented a link box and sheath sectionalizing joint as the
            # same node.  Migrate them into separate physical/electrical objects.
            if not raw_link_boxes and any(str(item.get("node_type", "")).upper() == "LINK_BOX" for item in raw_nodes):
                id_map: dict[str, str] = {}
                new_nodes: list[dict[str, Any]] = []
                link_boxes: list[dict[str, Any]] = []
                joint_index = 0
                for item in raw_nodes:
                    old_id = str(item.get("node_id", ""))
                    if str(item.get("node_type", "")).upper() == "LINK_BOX":
                        joint_index += 1
                        new_id = f"J{joint_index}"
                        while any(str(candidate.get("node_id")) == new_id for candidate in raw_nodes):
                            new_id = f"J{joint_index}_{old_id}"
                        id_map[old_id] = new_id
                        new_nodes.append({
                            "node_id": new_id,
                            "name": f"Sectionalizing Joint {joint_index}",
                            "position_m": item.get("position_m", 0.0),
                            "node_type": "SECTIONALIZING_JOINT",
                            "earth_resistance_ohm": item.get("earth_resistance_ohm", 0.0),
                            "grounded": False,
                        })
                        link_boxes.append({
                            "link_box_id": old_id or f"LB{joint_index}",
                            "name": item.get("name", f"Link Box {joint_index}"),
                            "joint_node_id": new_id,
                            "position_m": item.get("position_m", 0.0),
                            "lead_length_m": 3.0,
                            "lead_type": "COAXIAL",
                            "contains_svl": True,
                            "accessible": True,
                        })
                    else:
                        copied = dict(item)
                        copied.setdefault("grounded", str(copied.get("node_type", "")).upper() == "TERMINATION")
                        new_nodes.append(copied)
                for item in raw_minors:
                    item["start_node_id"] = id_map.get(str(item.get("start_node_id", "")), item.get("start_node_id", ""))
                    item["end_node_id"] = id_map.get(str(item.get("end_node_id", "")), item.get("end_node_id", ""))
                for item in raw_connections:
                    old_node = str(item.get("node_id", ""))
                    item["link_box_id"] = old_node
                    item["node_id"] = id_map.get(old_node, old_node)
                raw_nodes = new_nodes
                raw_link_boxes = link_boxes

            nodes = [BondingNode(**{k: v for k, v in item.items() if k in node_fields}) for item in raw_nodes]
            for node in nodes:
                if node.node_type.upper() == "TERMINATION":
                    node.grounded = True

            link_boxes = [
                BondingLinkBox(**{k: v for k, v in item.items() if k in link_fields})
                for item in raw_link_boxes
            ]
            minors = [
                BondingMinorSection(**{k: v for k, v in item.items() if k in minor_fields})
                for item in raw_minors
            ]
            connections = [
                BondingConnection(**{k: v for k, v in item.items() if k in connection_fields})
                for item in raw_connections
            ]
            bonding = BondingSystemData(
                **base_kwargs,
                nodes=nodes,
                link_boxes=link_boxes,
                minor_sections=minors,
                connections=connections,
            )
            if not bonding.nodes or not bonding.minor_sections:
                fallback = default_bonding_system(sum(section.length_m for section in routes))
                for setting_name, setting_value in base_kwargs.items():
                    setattr(fallback, setting_name, setting_value)
                bonding = fallback

        svl_raw = raw.get("svl")
        if not isinstance(svl_raw, dict):
            svl = SvlSystemData()
        else:
            candidate_fields = SvlCandidate.__dataclass_fields__
            system_fields = SvlSystemData.__dataclass_fields__
            candidates = [
                SvlCandidate(**{k: v for k, v in dict(item).items() if k in candidate_fields})
                for item in svl_raw.get("candidates", [])
                if isinstance(item, dict)
            ]
            base_svl = {
                k: v for k, v in svl_raw.items()
                if k in system_fields and k != "candidates"
            }
            svl = SvlSystemData(**base_svl, candidates=candidates or default_svl_candidates())

        fault_raw = raw.get("fault_study")
        if not isinstance(fault_raw, dict):
            fault_study = FaultStudyData()
        else:
            scenario_fields = FaultScenario.__dataclass_fields__
            study_fields = FaultStudyData.__dataclass_fields__
            scenarios = [
                FaultScenario(**{k: v for k, v in dict(item).items() if k in scenario_fields})
                for item in fault_raw.get("scenarios", [])
                if isinstance(item, dict)
            ]
            base_fault = {
                k: v for k, v in fault_raw.items()
                if k in study_fields and k != "scenarios"
            }
            fault_study = FaultStudyData(**base_fault, scenarios=scenarios or default_fault_scenarios())

        design_basis_raw = raw.get("design_basis")
        if not isinstance(design_basis_raw, dict):
            design_basis = DesignBasisData(
                system_voltage_kv=float(cable_raw.get("voltage_kv", 154.0)),
                frequency_hz=float(cable_raw.get("frequency_hz", 50.0)),
                direct_current_a=float(cable_raw.get("design_current_a", 0.0)),
                load_input_mode=LOAD_MODE_DIRECT_CURRENT,
                design_current_per_circuit_a=float(cable_raw.get("design_current_a", 0.0)),
                design_margin_percent=0.0,
                total_route_length_m=sum(section.length_m for section in routes),
            )
        else:
            basis_fields = DesignBasisData.__dataclass_fields__
            candidate_fields = GenericCableCandidate.__dataclass_fields__
            candidates = [
                GenericCableCandidate(**{k: v for k, v in dict(item).items() if k in candidate_fields})
                for item in design_basis_raw.get("candidates", []) if isinstance(item, dict)
            ]
            basis_kwargs = {
                k: v for k, v in design_basis_raw.items()
                if k in basis_fields and k != "candidates"
            }
            design_basis = DesignBasisData(**basis_kwargs, candidates=candidates)

        transient_raw = raw.get("transient_study")
        if not isinstance(transient_raw, dict):
            transient_study = TransientThermalStudyData()
        else:
            point_fields = LoadProfilePoint.__dataclass_fields__
            profile_fields = TransientLoadProfile.__dataclass_fields__
            study_fields = TransientThermalStudyData.__dataclass_fields__
            profiles: list[TransientLoadProfile] = []
            for profile_raw in transient_raw.get("profiles", []):
                if not isinstance(profile_raw, dict):
                    continue
                points = [
                    LoadProfilePoint(**{k: v for k, v in dict(item).items() if k in point_fields})
                    for item in profile_raw.get("points", []) if isinstance(item, dict)
                ]
                kwargs = {k: v for k, v in profile_raw.items() if k in profile_fields and k != "points"}
                profiles.append(TransientLoadProfile(**kwargs, points=points))
            base_transient = {
                k: v for k, v in transient_raw.items()
                if k in study_fields and k != "profiles"
            }
            transient_study = TransientThermalStudyData(
                **base_transient, profiles=profiles or default_transient_profiles()
            )

        library_raw = raw.get("cable_library")
        if not isinstance(library_raw, dict):
            cable_library = CableLibraryData()
        else:
            record_fields = CableCatalogRecord.__dataclass_fields__
            library_fields = CableLibraryData.__dataclass_fields__
            library_sources = [
                CableParameterSource(**{k: v for k, v in dict(item).items() if k in source_fields})
                for item in library_raw.get("sources", []) if isinstance(item, dict)
            ]
            library_records = [
                CableCatalogRecord(**{k: v for k, v in dict(item).items() if k in record_fields})
                for item in library_raw.get("records", []) if isinstance(item, dict)
            ]
            library_kwargs = {
                k: v for k, v in library_raw.items()
                if k in library_fields and k not in {"records", "sources"}
            }
            cable_library = CableLibraryData(
                **library_kwargs, records=library_records, sources=library_sources
            )

        application_raw = raw.get("cable_application")
        if not isinstance(application_raw, dict):
            cable_application = CableApplicationData()
        else:
            completion_fields = CableCompletionItem.__dataclass_fields__
            assignment_fields = RouteCableAssignment.__dataclass_fields__
            decision_fields = SourceConflictDecision.__dataclass_fields__
            application_fields = CableApplicationData.__dataclass_fields__
            completion_items = [
                CableCompletionItem(**{k: v for k, v in dict(item).items() if k in completion_fields})
                for item in application_raw.get("completion_items", []) if isinstance(item, dict)
            ]
            assignments = [
                RouteCableAssignment(**{k: v for k, v in dict(item).items() if k in assignment_fields})
                for item in application_raw.get("assignments", []) if isinstance(item, dict)
            ]
            conflict_decisions = [
                SourceConflictDecision(**{k: v for k, v in dict(item).items() if k in decision_fields})
                for item in application_raw.get("conflict_decisions", []) if isinstance(item, dict)
            ]
            application_kwargs = {
                k: v for k, v in application_raw.items()
                if k in application_fields and k not in {"completion_items", "assignments", "conflict_decisions"}
            }
            cable_application = CableApplicationData(
                **application_kwargs, completion_items=completion_items, assignments=assignments,
                conflict_decisions=conflict_decisions
            )


        installation_raw = raw.get("installation_design")
        if not isinstance(installation_raw, dict):
            installation_design = default_installation_design(
                CableData(**{k: v for k, v in cable_raw.items() if k in cable_fields}),
                design_basis,
                thermal_design,
            )
        else:
            circuit_fields = InstallationCircuitData.__dataclass_fields__
            cable_install_fields = PhysicalCableData.__dataclass_fields__
            duct_fields = DuctSlotData.__dataclass_fields__
            material_region_fields = ThermalMaterialRegionData.__dataclass_fields__
            heat_fields = ExternalHeatSourceData.__dataclass_fields__
            geometry_fields = CableChannelGeometryData.__dataclass_fields__
            section_fields = InstallationCrossSectionData.__dataclass_fields__
            design_install_fields = InstallationDesignData.__dataclass_fields__
            cross_sections: list[InstallationCrossSectionData] = []
            for section_raw in installation_raw.get("cross_sections", []):
                if not isinstance(section_raw, dict):
                    continue
                circuits = [
                    InstallationCircuitData(**{k: v for k, v in dict(item).items() if k in circuit_fields})
                    for item in section_raw.get("circuits", []) if isinstance(item, dict)
                ]
                physical_cables = [
                    PhysicalCableData(**{k: v for k, v in dict(item).items() if k in cable_install_fields})
                    for item in section_raw.get("physical_cables", []) if isinstance(item, dict)
                ]
                duct_slots = [
                    DuctSlotData(**{k: v for k, v in dict(item).items() if k in duct_fields})
                    for item in section_raw.get("duct_slots", []) if isinstance(item, dict)
                ]
                material_regions = [
                    ThermalMaterialRegionData(**{k: v for k, v in dict(item).items() if k in material_region_fields})
                    for item in section_raw.get("material_regions", []) if isinstance(item, dict)
                ]
                heat_sources = [
                    ExternalHeatSourceData(**{k: v for k, v in dict(item).items() if k in heat_fields})
                    for item in section_raw.get("external_heat_sources", []) if isinstance(item, dict)
                ]
                geometry_raw = section_raw.get("channel_geometry", {})
                if isinstance(geometry_raw, dict) and geometry_raw:
                    channel_geometry = CableChannelGeometryData(
                        **{k: v for k, v in geometry_raw.items() if k in geometry_fields}
                    )
                else:
                    section_kind = str(section_raw.get("installation_type", THERMAL_INSTALL_DIRECT_BURIED))
                    cable_depths = [float(item.depth_m) for item in physical_cables if item.active]
                    cable_xs = [float(item.x_m) for item in physical_cables if item.active]
                    burial = min(cable_depths, default=1.20)
                    span = max(cable_xs, default=0.20) - min(cable_xs, default=-0.20)
                    channel_geometry = _default_channel_geometry_for_section(
                        section_kind,
                        trench_width_m=max(0.80, span + 0.40),
                        trench_depth_m=max(1.20, burial + 0.35),
                    )
                    channel_geometry.source_reference = "MIGRATED_PARAMETRIC_DEFAULT"
                kwargs = {
                    k: v for k, v in section_raw.items()
                    if k in section_fields and k not in {
                        "circuits", "physical_cables", "duct_slots", "material_regions", "external_heat_sources",
                        "channel_geometry"
                    }
                }
                cross_sections.append(InstallationCrossSectionData(
                    **kwargs,
                    circuits=circuits,
                    physical_cables=physical_cables,
                    duct_slots=duct_slots,
                    material_regions=material_regions,
                    external_heat_sources=heat_sources,
                    channel_geometry=channel_geometry,
                ))
            install_kwargs = {
                k: v for k, v in installation_raw.items()
                if k in design_install_fields and k != "cross_sections"
            }
            source_installation_model_revision = str(
                installation_raw.get("model_revision", "") or ""
            )
            installation_design = InstallationDesignData(
                **install_kwargs, cross_sections=cross_sections
            )
            if not installation_design.cross_sections:
                installation_design = default_installation_design(
                    CableData(**{k: v for k, v in cable_raw.items() if k in cable_fields}),
                    design_basis,
                    thermal_design,
                )
            if installation_design.solver_coupling_mode in {
                INSTALLATION_COUPLING_DESIGN_ONLY, INSTALLATION_COUPLING_LEGACY_PROJECTION, ""
            }:
                installation_design.solver_coupling_mode = INSTALLATION_COUPLING_PRODUCTION_LINKED
            cable_diameter_m = max(0.001, float(cable_raw.get("overall_diameter_mm", 0.0) or 0.0) / 1000.0)
            for section in installation_design.cross_sections:
                _normalise_legacy_trefoil_contact(
                    section, cable_diameter_m, source_installation_model_revision
                )
                _normalise_rounded_trefoil_contact(section, cable_diameter_m)
            installation_design.model_revision = "0.16.9.4.34"

        policy_raw = raw.get("calculation_policy")
        if not isinstance(policy_raw, dict):
            calculation_policy = CalculationPolicyData()
        else:
            provenance_fields = ParameterProvenanceRecord.__dataclass_fields__
            policy_fields = CalculationPolicyData.__dataclass_fields__
            parameter_records = [
                ParameterProvenanceRecord(**{k: v for k, v in dict(item).items() if k in provenance_fields})
                for item in policy_raw.get("parameter_records", []) if isinstance(item, dict)
            ]
            policy_kwargs = {
                k: v for k, v in policy_raw.items()
                if k in policy_fields and k != "parameter_records"
            }
            calculation_policy = CalculationPolicyData(
                **policy_kwargs, parameter_records=parameter_records
            )

        physical_raw = raw.get("physical_parameter_study")
        if isinstance(physical_raw, dict):
            physical_fields = PhysicalParameterStudyData.__dataclass_fields__
            physical_parameter_study = PhysicalParameterStudyData(
                **{k: v for k, v in physical_raw.items() if k in physical_fields}
            )
        else:
            physical_parameter_study = PhysicalParameterStudyData()


        procurement_raw = raw.get("procurement")
        if not isinstance(procurement_raw, dict):
            procurement = ProcurementData()
        else:
            procurement_fields = ProcurementData.__dataclass_fields__
            override_fields = ProcurementQuantityOverride.__dataclass_fields__
            overrides = [
                ProcurementQuantityOverride(**{k: v for k, v in dict(item).items() if k in override_fields})
                for item in procurement_raw.get("quantity_overrides", []) if isinstance(item, dict)
            ]
            procurement_kwargs = {
                k: v for k, v in procurement_raw.items()
                if k in procurement_fields and k != "quantity_overrides"
            }
            procurement = ProcurementData(**procurement_kwargs, quantity_overrides=overrides)

        progress_raw = raw.get("design_progress")
        if isinstance(progress_raw, dict):
            progress_fields = DesignProgressData.__dataclass_fields__
            design_progress = DesignProgressData(**{k: v for k, v in progress_raw.items() if k in progress_fields})
        else:
            design_progress = DesignProgressData()

        workflow_raw = raw.get("workflow")
        if isinstance(workflow_raw, dict):
            workflow_fields = DesignWorkflowData.__dataclass_fields__
            workflow = DesignWorkflowData(**{k: v for k, v in workflow_raw.items() if k in workflow_fields})
        else:
            workflow = DesignWorkflowData()

        audit_raw = raw.get("source_audit")
        if not isinstance(audit_raw, dict):
            source_audit = ProjectSourceAuditData()
        else:
            value_fields = SourceValueRecord.__dataclass_fields__
            conflict_fields = SourceConflictRecord.__dataclass_fields__
            audit_fields = ProjectSourceAuditData.__dataclass_fields__
            source_records = [
                SourceValueRecord(**{k: v for k, v in dict(item).items() if k in value_fields})
                for item in audit_raw.get("records", []) if isinstance(item, dict)
            ]
            source_conflicts = [
                SourceConflictRecord(**{k: v for k, v in dict(item).items() if k in conflict_fields})
                for item in audit_raw.get("conflicts", []) if isinstance(item, dict)
            ]
            audit_kwargs = {
                k: v for k, v in audit_raw.items()
                if k in audit_fields and k not in {"records", "conflicts"}
            }
            source_audit = ProjectSourceAuditData(
                **audit_kwargs, records=source_records, conflicts=source_conflicts
            )

        return cls(
            schema_version=PROJECT_SCHEMA_VERSION,
            project_name=raw.get("project_name", "Yeni DiTuS Kablo Projesi"),
            project_code=raw.get("project_code", "DITUS-KBL-001"),
            description=raw.get("description", ""),
            standards_profile=raw.get("standards_profile", "IEC + IEEE + CIGRE"),
            design_basis=design_basis,
            design_progress=design_progress,
            workflow=workflow,
            cable=CableData(**{k: v for k, v in cable_raw.items() if k in cable_fields}),
            cable_library=cable_library,
            cable_application=cable_application,
            installation_design=installation_design,
            procurement=procurement,
            route_sections=routes,
            thermal_design=thermal_design,
            bonding=bonding,
            svl=svl,
            fault_study=fault_study,
            transient_study=transient_study,
            source_audit=source_audit,
            calculation_policy=calculation_policy,
            physical_parameter_study=physical_parameter_study,
            cad_source=raw.get("cad_source", ""),
            created_at=raw.get("created_at", datetime.now().isoformat(timespec="seconds")),
            modified_at=raw.get("modified_at", datetime.now().isoformat(timespec="seconds")),
        )
