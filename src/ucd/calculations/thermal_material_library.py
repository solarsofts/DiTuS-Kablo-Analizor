"""Built-in reference thermal-material catalogue for cable-channel design.

The catalogue is intentionally conservative.  Literature rock values are kept
separate from granular backfills because crushing, grading, void ratio,
moisture and compaction can change the effective thermal resistivity greatly.
Project design values should therefore be replaced by IEEE 442 / ASTM D5334
measurements whenever the material controls cable rating.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from ucd.models.project import (
    THERMAL_STATE_DESIGN,
    ThermalDesignData,
    ThermalMaterialData,
)


LIBRARY_REVISION = "2026-07-31"
REFERENCE_SCOPE = (
    "IEC 60287-2-1:2023 steady-state trench/duct/trough thermal model; "
    "IEC 60853-3:2002 partial soil dry-out; IEEE 442-2017 soil/backfill thermal "
    "resistivity measurement; ASTM D5334-22ae1 thermal needle-probe test"
)


@dataclass(frozen=True)
class ThermalMaterialLibraryIssue:
    severity: str
    code: str
    message: str
    material_id: str = ""


def _material(
    material_id: str,
    name: str,
    category: str,
    conductivity: float,
    conductivity_min: float,
    conductivity_max: float,
    *,
    moisture: str,
    source: str,
    notes: str,
    density: float = 0.0,
    heat_capacity: float = 0.0,
    compaction: float = 0.0,
    reliability: str = "REFERENCE_ONLY",
    test_required: bool = True,
) -> ThermalMaterialData:
    rho = 1.0 / conductivity if conductivity > 0 else 0.0
    return ThermalMaterialData(
        material_id=material_id,
        name=name,
        category=category,
        thermal_resistivity_km_w=rho,
        thermal_conductivity_w_mk=conductivity,
        dry_density_kg_m3=density,
        volumetric_heat_capacity_mj_m3k=heat_capacity,
        compaction_percent=compaction,
        reference_conductivity_min_w_mk=conductivity_min,
        reference_conductivity_max_w_mk=conductivity_max,
        moisture_condition=moisture,
        test_method="IEEE 442-2017 / ASTM D5334-22ae1",
        library_scope="BUILT_IN_REFERENCE",
        requires_project_test=test_required,
        data_state=THERMAL_STATE_DESIGN,
        source_type="PUBLISHED_REFERENCE_RANGE",
        source_reference=source,
        reliability=reliability,
        notes=notes,
    )


def built_in_reference_materials() -> list[ThermalMaterialData]:
    """Return fresh copies of the built-in catalogue records."""

    records = [
        _material(
            "REF-SAND-DRY-01",
            "Kuru kum — literatür referansı",
            "NATIVE_SOIL",
            0.40,
            0.30,
            0.80,
            moisture="DRY",
            source="Literature range compiled for soil/rock heat-transfer studies; design test per IEEE 442 / ASTM D5334",
            notes=(
                "Kuru kumda düşük nem nedeniyle ısıl özdirenç yüksektir. Nihai kablo rating hesabında "
                "yerel granülometri, yoğunluk ve nem koşuluyla test edilmeden kullanılmamalıdır."
            ),
            heat_capacity=1.4,
        ),
        _material(
            "REF-THERMAL-SAND-01",
            "Kontrollü termal kum/backfill — ön tasarım hedefi",
            "THERMAL_BACKFILL",
            1.33,
            1.00,
            2.00,
            moisture="CONTROLLED_MOISTURE",
            source="IEEE 442-2017 material-test scope; project target value, not a universal material constant",
            notes=(
                "Hedef ρ≈0.75 K·m/W yalnız ön tasarım içindir. Lot bazlı kuru/ıslak yoğunluk, nem, "
                "kompaksiyon ve kuruma eğrisi doğrulanmalıdır."
            ),
            density=1800.0,
            heat_capacity=1.8,
            compaction=95.0,
            reliability="PRELIMINARY_TARGET",
        ),
        _material(
            "REF-BASALT-INTACT-01",
            "Bazalt — sağlam kaya referansı",
            "ROCK",
            1.40,
            1.18,
            1.62,
            moisture="LAB_SAMPLE_REFERENCE",
            source="Thermal conductivity of major rock types in western and central Anatolia, J. Geophysics and Engineering 14 (2017)",
            notes=(
                "Sağlam kaya numunesi referansıdır. Kırılmış bazalt dolgunun boşluk, dane boyutu, nem ve "
                "kompaksiyon nedeniyle aynı iletkenliği göstereceği kabul edilemez."
            ),
            heat_capacity=2.3,
        ),
        _material(
            "REF-LIMESTONE-INTACT-01",
            "Kalker / kireçtaşı — sağlam kaya referansı",
            "ROCK",
            2.80,
            1.70,
            4.20,
            moisture="LITERATURE_RANGE",
            source="DOE geothermal rock-property compilation; USGS common-rock conductivity references",
            notes=(
                "Sağlam kireçtaşı için geniş literatür aralığıdır. Kırmataş kalker veya hendek dolgusu için "
                "doğrudan kullanılmamalı; sıkıştırılmış numune test edilmelidir."
            ),
            heat_capacity=2.2,
        ),
        _material(
            "REF-SANDSTONE-01",
            "Kumtaşı — kuru kaya referansı",
            "ROCK",
            1.57,
            0.47,
            2.67,
            moisture="DRY_LAB_REFERENCE",
            source="Thermal conductivity of major rock types in western and central Anatolia, J. Geophysics and Engineering 14 (2017)",
            notes="Kuvars içeriği, gözeneklilik ve su doygunluğu nedeniyle aralık geniştir; proje numunesi gerekir.",
            heat_capacity=2.2,
        ),
        _material(
            "REF-CRUSHED-BASALT-01",
            "Kırmataş bazalt dolgu — test zorunlu",
            "GENERAL_FILL",
            0.83,
            0.50,
            1.80,
            moisture="PROJECT_DEPENDENT",
            source="Engineering placeholder constrained by IEEE 442 / ASTM D5334 measurement requirement",
            notes=(
                "Sağlam bazalt iletkenliği kırmataşa taşınmamıştır. Bu kayıt yalnız veri toplama ve "
                "duyarlılık analizi içindir; nihai tasarım için kompaksiyon ve nem durumunda test zorunludur."
            ),
            density=1700.0,
            heat_capacity=1.7,
            compaction=95.0,
            reliability="LOW_PLACEHOLDER",
        ),
        _material(
            "REF-CRUSHED-LIMESTONE-01",
            "Kırmataş kalker dolgu — test zorunlu",
            "GENERAL_FILL",
            1.00,
            0.60,
            2.00,
            moisture="PROJECT_DEPENDENT",
            source="Engineering placeholder constrained by IEEE 442 / ASTM D5334 measurement requirement",
            notes=(
                "Dane dağılımı ve boşluk oranı sağlam kalkerden daha belirleyicidir. Nihai rating için "
                "yerleştirilmiş yoğunluk ve tasarım neminde ölçüm zorunludur."
            ),
            density=1750.0,
            heat_capacity=1.8,
            compaction=95.0,
            reliability="LOW_PLACEHOLDER",
        ),
        _material(
            "REF-CLSM-01",
            "Akışkan kontrollü düşük dayanımlı dolgu / CLSM — test zorunlu",
            "CONCRETE_GROUT",
            1.20,
            0.80,
            1.80,
            moisture="CURED",
            source="IEEE 442-2017 includes concrete, engineered backfill and grout in measurement scope",
            notes="Karışım tasarımı, yoğunluk ve kür koşulu üretici/test raporuyla tanımlanmalıdır.",
            density=1900.0,
            heat_capacity=2.0,
            reliability="REFERENCE_ONLY",
        ),
        _material(
            "REF-BENTONITE-GROUT-01",
            "Bentonit esaslı grout — kuruma/büzülme riski",
            "CONCRETE_GROUT",
            0.80,
            0.50,
            1.30,
            moisture="SATURATED_AS_PLACED",
            source="IEEE 442-2017 measurement scope; IEC 60853-3 dry-out relevance",
            notes=(
                "Kuruma, büzülme ve boru/zemin temas kaybı ısıl performansı düşürebilir. Kuru durum değeri "
                "ve kritik kuruma sıcaklığı ayrıca ölçülmelidir."
            ),
            density=1500.0,
            heat_capacity=2.2,
            reliability="REFERENCE_ONLY",
        ),
    ]
    return deepcopy(records)


def merge_reference_materials(design: ThermalDesignData) -> int:
    """Append missing built-in records without replacing project values."""

    existing = {item.material_id for item in design.materials}
    added = 0
    for material in built_in_reference_materials():
        if material.material_id in existing:
            continue
        design.materials.append(material)
        existing.add(material.material_id)
        added += 1
    return added


def validate_material_for_final_design(material: ThermalMaterialData) -> tuple[ThermalMaterialLibraryIssue, ...]:
    issues: list[ThermalMaterialLibraryIssue] = []
    if material.thermal_resistivity_km_w <= 0:
        issues.append(ThermalMaterialLibraryIssue("ERROR", "THERMAL_VALUE_MISSING", "Isıl özdirenç sıfırdan büyük olmalıdır.", material.material_id))
    if material.requires_project_test and str(material.data_state).upper() not in {"TESTED", "AS_BUILT"}:
        issues.append(ThermalMaterialLibraryIssue(
            "WARNING", "PROJECT_TEST_REQUIRED",
            "Bu kütüphane kaydı nihai tasarım için proje numunesi/saha ölçümüyle değiştirilmelidir.",
            material.material_id,
        ))
    if material.reference_conductivity_min_w_mk and material.reference_conductivity_max_w_mk:
        if material.reference_conductivity_min_w_mk > material.reference_conductivity_max_w_mk:
            issues.append(ThermalMaterialLibraryIssue("ERROR", "REFERENCE_RANGE", "Referans iletkenlik aralığı ters tanımlanmış.", material.material_id))
    return tuple(issues)
