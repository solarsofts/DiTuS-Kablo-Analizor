from __future__ import annotations

from dataclasses import dataclass
from math import acosh, hypot, log, pi, sqrt

from ucd.calculations.installation import physical_cable_contact_tolerance_m
from ucd.calculations.phase_geometry import PhaseGeometryError, normalize_arrangement, phase_slot_offsets_m
from ucd.models.project import (
    EXTERNAL_THERMAL_AUTO,
    EXTERNAL_THERMAL_MIXED,
    EXTERNAL_THERMAL_MANUAL,
    INTERNAL_THERMAL_AUTO,
    INTERNAL_THERMAL_MANUAL,
    CableData,
    RouteSection,
    THERMAL_INSTALL_DIRECT_BURIED,
)


THERMAL_REFERENCE = "IEC 60287-2-1:2023 thermal-resistance scope; implementation pre-validation"
IMAGE_METHOD_REFERENCE = "Direct-buried homogeneous-soil image-method thermal matrix; CIGRE TB 880 regression pending"


class ThermalInputError(ValueError):
    pass


@dataclass(frozen=True)
class InternalThermalResult:
    t1_km_w: float
    t2_km_w: float
    t3_km_w: float
    mode: str
    source: str
    trace: tuple[str, ...]


@dataclass(frozen=True)
class ExternalThermalResult:
    effective_t4_km_w: float
    phase_t4_km_w: tuple[float, ...]
    matrix_km_w: tuple[tuple[float, ...], ...]
    positions_m: tuple[tuple[float, float], ...]
    mode: str
    source: str
    trace: tuple[str, ...]


@dataclass(frozen=True)
class SectionThermalResult:
    section_name: str
    internal: InternalThermalResult
    external: ExternalThermalResult


def _positive(name: str, value: float, allow_zero: bool = False) -> float:
    value = float(value)
    if (allow_zero and value < 0) or (not allow_zero and value <= 0):
        comparator = "negatif olamaz" if allow_zero else "sıfırdan büyük olmalı"
        raise ThermalInputError(f"{name} {comparator}: {value}")
    return value


def cylindrical_layer_resistance_km_w(
    inner_diameter_mm: float,
    outer_diameter_mm: float,
    thermal_resistivity_km_w: float,
) -> float:
    """Thermal resistance per unit length of a homogeneous concentric layer.

    T = rho/(2*pi) * ln(D_outer/D_inner)
    """

    d_in = _positive("İç çap", inner_diameter_mm)
    d_out = _positive("Dış çap", outer_diameter_mm)
    rho = _positive("Isıl özdirenç", thermal_resistivity_km_w, allow_zero=True)
    if d_out <= d_in:
        raise ThermalInputError(f"Dış çap iç çaptan büyük olmalı: {d_out} <= {d_in} mm")
    return rho / (2.0 * pi) * log(d_out / d_in)


def resolve_internal_thermal_resistance(cable: CableData) -> InternalThermalResult:
    mode = str(cable.internal_thermal_mode or INTERNAL_THERMAL_MANUAL).strip().upper()
    if mode == INTERNAL_THERMAL_MANUAL:
        t1 = _positive("T1", cable.thermal_resistance_t1_km_w, allow_zero=True)
        t2 = _positive("T2", cable.thermal_resistance_t2_km_w, allow_zero=True)
        t3 = _positive("T3", cable.thermal_resistance_t3_km_w, allow_zero=True)
        return InternalThermalResult(
            t1,
            t2,
            t3,
            mode,
            "Kullanıcı/üretici manuel T1-T3",
            (
                "İç termal mod = MANUAL",
                f"T1/T2/T3 = {t1:.6f} / {t2:.6f} / {t3:.6f} K·m/W",
            ),
        )
    if mode != INTERNAL_THERMAL_AUTO:
        raise ThermalInputError(f"Bilinmeyen iç termal mod: {cable.internal_thermal_mode}")

    d0 = _positive("İletken çapı", cable.conductor_diameter_mm)
    d1 = _positive("T1 dış sınır çapı", cable.t1_outer_diameter_mm)
    d2 = _positive("T2 dış sınır çapı", cable.t2_outer_diameter_mm)
    d3 = _positive("Kablo dış çapı", cable.overall_diameter_mm)
    if not d0 < d1 < d2 < d3:
        raise ThermalInputError(
            "Eşdeğer katman çapları artan sırada olmalı: "
            f"iletken={d0}, T1={d1}, T2={d2}, dış={d3} mm"
        )

    t1 = cylindrical_layer_resistance_km_w(d0, d1, cable.t1_thermal_resistivity_km_w)
    t2 = cylindrical_layer_resistance_km_w(d1, d2, cable.t2_thermal_resistivity_km_w)
    t3 = cylindrical_layer_resistance_km_w(d2, d3, cable.t3_thermal_resistivity_km_w)
    return InternalThermalResult(
        t1,
        t2,
        t3,
        mode,
        "Eşdeğer konsantrik katman geometrisi",
        (
            "İç termal mod = AUTO_GEOMETRY",
            f"Çaplar d0/d1/d2/d3 = {d0:.3f} / {d1:.3f} / {d2:.3f} / {d3:.3f} mm",
            "T = rho/(2*pi)*ln(Dout/Din)",
            f"rho1/rho2/rho3 = {cable.t1_thermal_resistivity_km_w:.4f} / "
            f"{cable.t2_thermal_resistivity_km_w:.4f} / {cable.t3_thermal_resistivity_km_w:.4f} K·m/W",
            f"T1/T2/T3 = {t1:.6f} / {t2:.6f} / {t3:.6f} K·m/W",
            "Not: Bu eşdeğer tek damarlı model ayrıntılı üretici katman haritası ve TB 880 doğrulaması bekler.",
        ),
    )


def cable_positions_m(cable: CableData, section: RouteSection) -> tuple[tuple[float, float], ...]:
    """Return analytical cable-centre coordinates for one circuit.

    ``burial_depth_m`` is always the shallowest active cable-axis depth and
    ``phase_spacing_m`` is the adjacent centre-to-centre pitch.  CUSTOM uses
    explicit x-y coordinates through ``resolve_external_thermal_resistance``.
    """

    depth = _positive("Gömülme derinliği", section.burial_depth_m)
    spacing = _positive("Faz eksen aralığı", section.phase_spacing_m)
    try:
        normalized = normalize_arrangement(cable.arrangement)
        slots = phase_slot_offsets_m(normalized, spacing, "ABC")
    except PhaseGeometryError as exc:
        raise ThermalInputError(str(exc)) from exc

    if normalized == "SINGLE":
        return ((0.0, depth),)
    if normalized == "VERTICAL":
        diameter_m = _positive("Kablo dış çapı", cable.overall_diameter_mm) / 1000.0
        tolerance = physical_cable_contact_tolerance_m(diameter_m)
        if spacing < diameter_m - tolerance:
            raise ThermalInputError(
                "VERTICAL_PHASE_OVERLAP: Düşey formasyonda komşu faz eksen aralığı "
                f"kablo dış çapından küçük olamaz: aralık={spacing:.6f} m, çap={diameter_m:.6f} m"
            )
    return tuple((x, depth + y) for x, y in slots.values())


def direct_buried_thermal_matrix_km_w(
    positions_m: tuple[tuple[float, float], ...],
    cable_outer_diameter_mm: float,
    soil_thermal_resistivity_km_w: float,
) -> tuple[tuple[float, ...], ...]:
    """Build a steady-state image-method resistance matrix in homogeneous soil.

    Diagonal terms use acosh(h/r), equivalent to ln(u+sqrt(u²-1)),
    u=2h/D. Off-diagonal terms use ln(d_image/d_actual).
    """

    diameter_m = _positive("Kablo dış çapı", cable_outer_diameter_mm) / 1000.0
    radius_m = diameter_m / 2.0
    rho = _positive("Toprak ısıl özdirenci", soil_thermal_resistivity_km_w)
    if not positions_m:
        raise ThermalInputError("T4 matrisi için kablo konumu yok.")

    rows: list[tuple[float, ...]] = []
    for i, (xi, hi) in enumerate(positions_m):
        hi = _positive(f"Faz {i + 1} eksen derinliği", hi)
        if hi <= radius_m:
            raise ThermalInputError(
                f"Faz {i + 1} ekseni yüzeye çok yakın: h={hi:.4f} m, yarıçap={radius_m:.4f} m"
            )
        row: list[float] = []
        for j, (xj, hj) in enumerate(positions_m):
            hj = _positive(f"Faz {j + 1} eksen derinliği", hj)
            if i == j:
                value = rho / (2.0 * pi) * acosh(hi / radius_m)
            else:
                actual = hypot(xi - xj, hi - hj)
                image = hypot(xi - xj, hi + hj)
                if actual < diameter_m - physical_cable_contact_tolerance_m(diameter_m):
                    raise ThermalInputError(
                        f"Faz {i + 1}-{j + 1} kabloları fiziksel olarak çakışıyor: "
                        f"eksen mesafesi={actual:.4f} m, dış çap={diameter_m:.4f} m"
                    )
                value = rho / (2.0 * pi) * log(image / actual)
            row.append(value)
        rows.append(tuple(row))
    return tuple(rows)



def mixed_zone_direct_buried_thermal_matrix_km_w(
    positions_m: tuple[tuple[float, float], ...],
    cable_outer_diameter_mm: float,
    native_soil_thermal_resistivity_km_w: float,
    backfill_thermal_resistivity_km_w: float,
    backfill_effective_radius_m: float,
    surface_correction_km_w: float = 0.0,
) -> tuple[tuple[float, ...], ...]:
    """Approximate a finite low/high-resistivity backfill zone.

    The far field and mutual terms are first evaluated in the native soil. A
    near-field cylindrical correction replaces native soil by the assigned
    backfill material between cable radius and an equivalent backfill radius.
    This is a transparent pre-nodal route-screening model, not a substitute for
    a 2D solution for rectangular trenches, ducts, HDDs or surface layers.
    """

    native = direct_buried_thermal_matrix_km_w(
        positions_m,
        cable_outer_diameter_mm,
        native_soil_thermal_resistivity_km_w,
    )
    diameter_m = _positive("Kablo dış çapı", cable_outer_diameter_mm) / 1000.0
    radius_m = diameter_m / 2.0
    equivalent_radius = _positive("Eşdeğer termal dolgu yarıçapı", backfill_effective_radius_m)
    if equivalent_radius <= radius_m:
        raise ThermalInputError(
            f"Eşdeğer dolgu yarıçapı kablo yarıçapından büyük olmalı: {equivalent_radius:.4f} <= {radius_m:.4f} m"
        )
    native_rho = _positive("Doğal zemin ısıl özdirenci", native_soil_thermal_resistivity_km_w)
    backfill_rho = _positive("Termal dolgu ısıl özdirenci", backfill_thermal_resistivity_km_w)
    if backfill_rho > native_rho:
        raise ThermalInputError(
            "MIXED_ZONE_POOR_BACKFILL_REQUIRES_NODAL: Termal dolgu ısıl özdirenci doğal "
            f"zeminden yüksek (dolgu={backfill_rho:.4f}, doğal={native_rho:.4f} K·m/W). "
            "Kötü dolgunun karşılıklı ısınma etkisi bu analitik ön modelin kapsamı dışındadır; "
            "2D nodal çözümü veya kaynaklandırılmış manuel T4 kullanın."
        )
    surface = _positive("Yüzey termal düzeltmesi", surface_correction_km_w, allow_zero=True)
    near_field_delta = (backfill_rho - native_rho) / (2.0 * pi) * log(equivalent_radius / radius_m)

    rows: list[tuple[float, ...]] = []
    for i, row in enumerate(native):
        values = list(row)
        corrected_diagonal = values[i] + near_field_delta + surface
        if corrected_diagonal < 0.0:
            raise ThermalInputError(
                "MIXED_ZONE_NEGATIVE_RESISTANCE: Karışık-zemin düzeltmesi negatif öz ısıl "
                f"direnç üretti (faz={i + 1}, değer={corrected_diagonal:.6f} K·m/W). "
                "2D nodal çözümü kullanın."
            )
        values[i] = corrected_diagonal
        rows.append(tuple(values))
    return tuple(rows)


def resolve_external_thermal_resistance(
    cable: CableData,
    section: RouteSection,
    explicit_positions_m: tuple[tuple[float, float], ...] | None = None,
) -> ExternalThermalResult:
    mode = str(section.external_thermal_mode or EXTERNAL_THERMAL_MANUAL).strip().upper()
    if mode == EXTERNAL_THERMAL_MANUAL:
        t4 = _positive("T4", section.external_thermal_resistance_t4_km_w, allow_zero=True)
        return ExternalThermalResult(
            t4,
            (t4,),
            ((t4,),),
            ((0.0, section.burial_depth_m),),
            mode,
            "Kullanıcı/üretici manuel T4",
            (
                "Dış termal mod = MANUAL",
                f"T4 = {t4:.6f} K·m/W",
            ),
        )
    if mode not in {EXTERNAL_THERMAL_AUTO, EXTERNAL_THERMAL_MIXED}:
        raise ThermalInputError(f"Bilinmeyen dış termal mod: {section.external_thermal_mode}")
    installation_type = str(section.section_type or THERMAL_INSTALL_DIRECT_BURIED).strip().upper()
    if installation_type in {
        "STANDART HENDEK",
        "STANDARD TRENCH",
        "DIRECT BURIED",
        "DIRECT_BURIED",
        "DOĞRUDAN GÖMÜLÜ",
        "DOGRUDAN GOMULU",
    }:
        installation_type = THERMAL_INSTALL_DIRECT_BURIED
    if installation_type != THERMAL_INSTALL_DIRECT_BURIED:
        raise ThermalInputError(
            "ANALYTIC_MODEL_SCOPE_REQUIRES_NODAL: Otomatik analitik dış termal model yalnız "
            f"DIRECT_BURIED kurulumunda geçerlidir; kurulum={installation_type}, mod={mode}. "
            "Nodal çözümü seçin veya kaynaklandırılmış pozitif manuel T4 girin."
        )

    positions = tuple(explicit_positions_m) if explicit_positions_m is not None else cable_positions_m(cable, section)
    if mode == EXTERNAL_THERMAL_MIXED:
        matrix = mixed_zone_direct_buried_thermal_matrix_km_w(
            positions,
            cable.overall_diameter_mm,
            section.soil_thermal_resistivity_km_w,
            section.backfill_thermal_resistivity_km_w,
            section.backfill_effective_radius_m,
            section.surface_thermal_correction_km_w,
        )
    else:
        matrix = direct_buried_thermal_matrix_km_w(
            positions,
            cable.overall_diameter_mm,
            section.soil_thermal_resistivity_km_w,
        )
    phase_t4 = tuple(sum(row) for row in matrix)
    effective = max(phase_t4)
    matrix_lines = tuple(
        f"Rth satır {idx + 1} = " + ", ".join(f"{value:.6f}" for value in row)
        for idx, row in enumerate(matrix)
    )
    position_text = "; ".join(f"P{idx + 1}=({x:.4f}, {h:.4f}) m" for idx, (x, h) in enumerate(positions))
    return ExternalThermalResult(
        effective,
        phase_t4,
        matrix,
        positions,
        mode,
        (
            "Karışık zemin eşdeğer image-method matrisi"
            if mode == EXTERNAL_THERMAL_MIXED
            else "Homojen toprakta doğrudan gömülü image-method matrisi"
        ),
        (
            f"Dış termal mod = {mode}",
            (
                f"Yerleşim = gerçek Kablo-Kanal x-y; kablo sayısı = {len(positions)}"
                if explicit_positions_m is not None
                else f"Yerleşim = {cable.arrangement}; eksen aralığı = {section.phase_spacing_m:.4f} m"
            ),
            f"Kablo dış çapı = {cable.overall_diameter_mm:.3f} mm; doğal zemin rho = "
            f"{section.soil_thermal_resistivity_km_w:.4f} K·m/W",
            *(
                (
                    f"Termal dolgu rho = {section.backfill_thermal_resistivity_km_w:.4f} K·m/W; "
                    f"eşdeğer yarıçap = {section.backfill_effective_radius_m:.4f} m; "
                    f"yüzey düzeltmesi = {section.surface_thermal_correction_km_w:.4f} K·m/W",
                )
                if mode == EXTERNAL_THERMAL_MIXED else ()
            ),
            f"Konumlar: {position_text}",
            *matrix_lines,
            "Faz eşdeğer T4 = " + " / ".join(f"{value:.6f}" for value in phase_t4) + " K·m/W",
            f"Muhafazakâr etkin T4 = max(faz) = {effective:.6f} K·m/W",
            (
                "Not: Karışık-zemin eşdeğer yarıçap ön modeli; dikdörtgen hendek ve yüzey etkileri için 2D nodal doğrulama gerekir."
                if mode == EXTERNAL_THERMAL_MIXED
                else "Not: Homojen toprak, izotermal yüzey ve eşit faz kaybı varsayımı; kanal/HDD için kullanılmamalıdır."
            ),
        ),
    )


def solve_section_thermal(
    cable: CableData,
    section: RouteSection,
    explicit_positions_m: tuple[tuple[float, float], ...] | None = None,
) -> SectionThermalResult:
    return SectionThermalResult(
        section.name,
        resolve_internal_thermal_resistance(cable),
        resolve_external_thermal_resistance(cable, section, explicit_positions_m),
    )


def solve_project_thermal(cable: CableData, sections: list[RouteSection]) -> list[SectionThermalResult]:
    if not sections:
        raise ThermalInputError("Termal ön işlem için güzergâh bölümü yok.")
    return [solve_section_thermal(cable, section) for section in sections]
