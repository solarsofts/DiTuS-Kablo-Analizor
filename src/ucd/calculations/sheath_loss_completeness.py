"""IEC 60287 sheath-loss completeness gate and λ1'' eddy-current term.

The global primitive network supplies longitudinal metallic sheath I²R loss.
This module decides whether IEC 60287-1-1 permits the eddy-current component
λ1'' to be neglected, calculates it for supported single-circuit trefoil/flat
geometries, or requires a traceable external value.  It is intentionally kept
separate from the network solver: the two components represent different
physical models and must retain distinct provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot, log, pi, sqrt
from typing import Mapping

from ucd.models.project import (
    BONDING_CROSS,
    BONDING_SINGLE_POINT,
    BONDING_SOLID_BOTH_END,
    CableData,
    InstallationCrossSectionData,
    ProjectData,
)

REFERENCE = "IEC 60287-1-1 clauses 2.3, 2.3.5 and 2.3.6.1"
AUTHORITY_FULL = "FULL"
AUTHORITY_BLOCKED = "BLOCKED"
AUTHORITY_ENGINEERING_PREVIEW = "ENGINEERING_PREVIEW"

SOURCE_IEC_CALCULATED = "CALCULATED_IEC_60287_1_1_2_3_6_1"
SOURCE_IEC_NOTE3 = "IEC_2.3.6.1_NOTE_3_NEGLIGIBLE"
SOURCE_IEC_SOLID = "IEC_2.3_SOLID_BOTH_END_EDDY_NOT_REQUIRED"
SOURCE_EXTERNAL = "EXTERNAL_VERIFIED"
SOURCE_UNAVAILABLE = "NOT_AVAILABLE"

# Engineering classification policy.  The standard gives closed forms for the
# ideal formations, not an arbitrary-x/y tolerance.  A candidate formation is
# therefore accepted only when its geometric departure changes λ1'' by no more
# than this small result-space budget.  The budget itself is a DiTuS policy,
# not an IEC tolerance.
FORMATION_EDDY_RELATIVE_BUDGET = 0.05


@dataclass(frozen=True)
class SheathLossCompleteness:
    authority: str
    eddy_source: str
    reason_codes: tuple[str, ...]
    notes: tuple[str, ...]
    factors_by_physical_cable: tuple[tuple[str, float], ...] = ()
    external_factor: float | None = None

    @property
    def complete(self) -> bool:
        return self.authority == AUTHORITY_FULL

    @property
    def factor_map(self) -> dict[str, float]:
        return dict(self.factors_by_physical_cable)


class SheathLossCompletenessError(ValueError):
    pass


def _screen_layer(cable: CableData):
    for layer in cable.layers:
        if str(layer.layer_type).upper() in {"WIRE_SCREEN", "METALLIC_SCREEN", "METALLIC_SHEATH"}:
            return layer
    return None


def _note3_negligible(cable: CableData) -> bool:
    """Return True only when the stored construction explicitly supports Note 3.

    A wire screen by itself is insufficient: IEC Note 3 additionally requires
    an equalizing strip or a thin sheet/foil over the wires.  We therefore do
    not infer that missing construction detail from wire_count alone.
    """
    layer = _screen_layer(cable)
    if layer is None:
        return False
    text = " ".join((str(layer.name), str(layer.notes))).lower()
    wire_like = str(layer.layer_type).upper() in {"WIRE_SCREEN", "METALLIC_SCREEN"} or int(layer.wire_count) > 0
    equalizing_or_foil = any(token in text for token in (
        "equalizing", "eşitleme", "esitleme", "foil", "thin sheet", "ince levha", "metal tape", "metal bant"
    ))
    return bool(wire_like and equalizing_or_foil)


def _is_milliken(cable: CableData) -> bool:
    return str(cable.conductor_stranding_type).strip().upper() in {"MILLIKEN", "ROUND_MILLIKEN"}


def _external_factor(
    cable: CableData,
    *,
    spacings_mm: tuple[float, ...],
    sheath_temp_c: float,
    active_formation: str,
) -> tuple[float | None, tuple[str, ...]]:
    source = str(getattr(cable, "sheath_eddy_external_source_type", "") or "").strip().upper()
    if not source:
        return None, ()
    value = float(getattr(cable, "sheath_eddy_external_factor", 0.0))
    if value < 0.0:
        return None, ("EXTERNAL_LAMBDA1_EDDY_NEGATIVE",)
    reference = str(getattr(cable, "sheath_eddy_external_reference", "") or "").strip()
    f_ref = float(getattr(cable, "sheath_eddy_external_frequency_hz", 0.0))
    d_ref = float(getattr(cable, "sheath_eddy_external_d_mm", 0.0))
    s_ref = float(getattr(cable, "sheath_eddy_external_s_mm", 0.0))
    t_ref = float(getattr(cable, "sheath_eddy_external_sheath_temperature_c", 0.0))
    formation_ref = str(getattr(cable, "sheath_eddy_external_formation_assumption", "") or "").strip().upper()
    if not reference or f_ref <= 0 or d_ref <= 0 or s_ref <= 0 or t_ref <= 0 or not formation_ref:
        return None, ("EXTERNAL_LAMBDA1_EDDY_REFERENCE_CONDITIONS_INCOMPLETE",)
    reasons: list[str] = []
    if abs(f_ref - float(cable.frequency_hz)) > 1e-6:
        reasons.append("STALE_EXTERNAL_LAMBDA1_EDDY_FREQUENCY")
    if abs(d_ref - float(cable.sheath_mean_diameter_mm)) / max(d_ref, 1e-9) > 0.01:
        reasons.append("STALE_EXTERNAL_LAMBDA1_EDDY_DIAMETER")
    if spacings_mm and any(abs(s_ref - spacing) / max(s_ref, 1e-9) > 0.01 for spacing in spacings_mm):
        reasons.append("STALE_EXTERNAL_LAMBDA1_EDDY_SPACING")
    if t_ref and abs(t_ref - sheath_temp_c) > 5.0:
        reasons.append("STALE_EXTERNAL_LAMBDA1_EDDY_TEMPERATURE")
    normalized_active = str(active_formation or "CUSTOM").strip().upper()
    if formation_ref not in {normalized_active, "VERIFIED_CUSTOM", "CUSTOM VERIFIED"} and normalized_active not in formation_ref:
        reasons.append("STALE_EXTERNAL_LAMBDA1_EDDY_FORMATION")
    if reasons:
        return None, tuple(reasons)
    return value, ()


def _sheath_geometry(cable: CableData) -> tuple[float, float]:
    layer = _screen_layer(cable)
    if layer is None or str(layer.layer_type).upper() != "METALLIC_SHEATH":
        raise SheathLossCompletenessError("IEC λ1'' kapalı formu için sürekli metalik kılıf geometrisi bulunamadı.")
    ds = float(layer.outer_diameter_mm)
    ts = 0.5 * (float(layer.outer_diameter_mm) - float(layer.inner_diameter_mm))
    if ds <= 0 or ts <= 0:
        raise SheathLossCompletenessError("Metalik kılıf dış çapı ve kalınlığı pozitif olmalıdır.")
    return ds, ts


def _sheath_resistance_ohm_m(cable: CableData, sheath_temp_c: float) -> float:
    if cable.sheath_dc_resistance_20_ohm_km > 0:
        r20_km = float(cable.sheath_dc_resistance_20_ohm_km)
    else:
        area = float(cable.sheath_cross_section_mm2)
        if area <= 0:
            raise SheathLossCompletenessError("Metalik kılıf kesiti/R20 değeri λ1'' için gerekli.")
        material = str(cable.sheath_material).strip().upper()
        rho20 = {"CU": 1.7241e-8, "COPPER": 1.7241e-8, "AL": 2.8264e-8, "ALUMINIUM": 2.8264e-8, "ALUMINUM": 2.8264e-8, "PB": 2.14e-7, "LEAD": 2.14e-7}.get(material)
        if rho20 is None:
            raise SheathLossCompletenessError(f"Metalik kılıf malzemesi için özdirenç bilinmiyor: {cable.sheath_material}")
        r20_km = rho20 * 1e9 / area
    factor = 1.0 + float(cable.sheath_temperature_coefficient_20_per_c) * (float(sheath_temp_c) - 20.0)
    if factor <= 0:
        raise SheathLossCompletenessError("Metalik kılıf sıcaklık direnç düzeltmesi pozitif değil.")
    return r20_km * factor / 1000.0


def _core_ac_resistance_ohm_m(cable: CableData, conductor_temp_c: float, spacing_m: float) -> float:
    from ucd.calculations.iec60287 import ac_resistance_at_temperature_ohm_km
    _, rac_km = ac_resistance_at_temperature_ohm_km(cable, conductor_temp_c, spacing_m)
    if rac_km <= 0:
        raise SheathLossCompletenessError("İletken AC direnci λ1'' için pozitif olmalıdır.")
    return float(rac_km) / 1000.0


def _base_terms(m: float, ratio: float, role: str) -> tuple[float, float, float]:
    if ratio <= 0 or ratio >= 1:
        raise SheathLossCompletenessError("d/(2s) oranı 0 ile 1 arasında olmalıdır.")
    m2 = m * m
    common = m2 / (1.0 + m2) * ratio * ratio
    small_m = m <= 0.1
    if role == "TREFOIL":
        l0 = 3.0 * common
        d1 = 0.0 if small_m else (1.14 * m ** 2.45 + 0.33) * ratio ** (0.92 * m + 1.66)
        d2 = 0.0
    elif role == "FLAT_CENTER":
        l0 = 6.0 * common
        d1 = 0.0 if small_m else 0.86 * m ** 3.08 * ratio ** (1.4 * m + 0.7)
        d2 = 0.0
    elif role == "FLAT_OUTER_LEADING":
        l0 = 1.5 * common
        d1 = 0.0 if small_m else 4.7 * m ** 0.7 * ratio ** (0.16 * m + 2.0)
        d2 = 0.0 if small_m else 21.0 * m ** 3.3 * ratio ** (1.47 * m + 5.06)
    elif role == "FLAT_OUTER_LAGGING":
        l0 = 1.5 * common
        d1 = 0.0 if small_m else -(0.74 * (m + 2.0) * m ** 0.5 / (2.0 + (m - 0.3) ** 2)) * ratio ** (m + 1.0)
        d2 = 0.0 if small_m else 0.92 * m ** 3.7 * ratio ** (m + 2.0)
    else:
        raise SheathLossCompletenessError(f"Desteklenmeyen λ1'' geometri rolü: {role}")
    return l0, d1, d2


def _milliken_factor(rs: float, omega: float, spacing_mm: float, d_mm: float, arrangement: str) -> float:
    x = 2.0 * omega * 1e-7 * log(2.0 * spacing_mm / d_mm)
    if x <= 0:
        raise SheathLossCompletenessError("Milliken F için kılıf reaktansı pozitif olmalıdır.")
    if arrangement == "TREFOIL":
        M = N = rs / x
    else:
        xm = 2.0 * omega * 1e-7 * log(2.0)
        if x <= xm / 3.0:
            raise SheathLossCompletenessError("Milliken F için flat X - Xm/3 pozitif olmalıdır.")
        M = rs / (x + xm)
        N = rs / (x - xm / 3.0)
    return (4.0 * M * M * N * N + (M + N) ** 2) / (4.0 * (M * M + 1.0) * (N * N + 1.0))


def iec_lambda1_eddy_factor(
    cable: CableData,
    *,
    spacing_m: float,
    role: str,
    conductor_temp_c: float,
    sheath_temp_c: float,
    apply_milliken_factor: bool,
) -> float:
    """Calculate IEC 60287-1-1 2.3.6.1 λ1'' for one sheath position."""
    spacing_mm = float(spacing_m) * 1000.0
    d_mm = float(cable.sheath_mean_diameter_mm)
    if spacing_mm <= d_mm / 2.0:
        raise SheathLossCompletenessError("Faz eksen aralığı kılıf geometrisi için yetersiz.")
    rs = _sheath_resistance_ohm_m(cable, sheath_temp_c)
    R = _core_ac_resistance_ohm_m(cable, conductor_temp_c, spacing_m)
    omega = 2.0 * pi * float(cable.frequency_hz)
    m = omega / rs * 1e-7
    l0, d1, d2 = _base_terms(m, d_mm / (2.0 * spacing_mm), role)
    ds, ts = _sheath_geometry(cable)
    area_m2 = float(cable.sheath_cross_section_mm2) * 1e-6
    rho_s = rs * area_m2
    if rho_s <= 0:
        raise SheathLossCompletenessError("Kılıf özdirenci λ1'' için pozitif olmalıdır.")
    beta1 = sqrt(4.0 * pi * omega / (1e7 * rho_s))
    gs = 1.0 + (ts / ds) ** 1.74 * (beta1 * ds * 1e-3 - 1.6)
    thickness_term = (beta1 * ts) ** 4 / (12.0e12)
    value = (rs / R) * (gs * l0 * (1.0 + d1 + d2) + thickness_term)
    if apply_milliken_factor:
        value *= _milliken_factor(rs, omega, spacing_mm, d_mm, "TREFOIL" if role == "TREFOIL" else "FLAT")
    return max(0.0, float(value))


def _active_groups(section: InstallationCrossSectionData) -> dict[tuple[str, int], list]:
    groups: dict[tuple[str, int], list] = {}
    for item in section.physical_cables:
        if not item.active:
            continue
        groups.setdefault((str(item.circuit_id), int(item.parallel_index)), []).append(item)
    return groups


def _formation(section: InstallationCrossSectionData, group: list, cable: CableData, conductor_temp_c: float, sheath_temp_c: float) -> tuple[str, float, dict[str, str]]:
    if len(group) != 3 or {str(x.phase).upper() for x in group} != {"A", "B", "C"}:
        raise SheathLossCompletenessError("IEC λ1'' otomatik sınıflandırması faz başına üç fiziksel kablo gerektirir.")
    by_phase = {str(x.phase).upper(): x for x in group}
    pts = {p: (float(v.x_m), float(v.depth_m)) for p, v in by_phase.items()}
    pairs = [("A", "B"), ("B", "C"), ("C", "A")]
    distances = [hypot(pts[a][0]-pts[b][0], pts[a][1]-pts[b][1]) for a,b in pairs]
    s_mean = sum(distances) / 3.0
    if s_mean <= 0:
        raise SheathLossCompletenessError("Faz geometrisi sıfır aralık içeriyor.")

    # Trefoil structural check plus result-space sensitivity.
    if max(abs(d-s_mean) for d in distances) / s_mean <= 0.10:
        f0 = iec_lambda1_eddy_factor(cable, spacing_m=s_mean, role="TREFOIL", conductor_temp_c=conductor_temp_c, sheath_temp_c=sheath_temp_c, apply_milliken_factor=_is_milliken(cable))
        probes = [iec_lambda1_eddy_factor(cable, spacing_m=d, role="TREFOIL", conductor_temp_c=conductor_temp_c, sheath_temp_c=sheath_temp_c, apply_milliken_factor=_is_milliken(cable)) for d in distances]
        rel = max(abs(x-f0) for x in probes) / max(abs(f0), 1e-12)
        if rel <= FORMATION_EDDY_RELATIVE_BUDGET:
            return "TREFOIL", s_mean, {p: "TREFOIL" for p in "ABC"}

    # Flat: collinear and equal adjacent spacing. Use x/y vector geometry, not label.
    ordered = sorted(group, key=lambda x: (float(x.x_m), float(x.depth_m)))
    p0, p1, p2 = ordered
    v1 = (p1.x_m-p0.x_m, p1.depth_m-p0.depth_m)
    v2 = (p2.x_m-p1.x_m, p2.depth_m-p1.depth_m)
    cross = abs(v1[0]*v2[1]-v1[1]*v2[0])
    d1m, d2m = hypot(*v1), hypot(*v2)
    if d1m > 0 and d2m > 0 and cross/(d1m*d2m) <= 0.02:
        s = 0.5*(d1m+d2m)
        if abs(d1m-d2m)/s <= 0.10:
            center_phase = str(p1.phase).upper()
            # Standard positive sequence used throughout DiTuS: A=0, B=-120, C=+120 deg.
            angles = {"A": 0.0, "B": -120.0, "C": 120.0}
            roles = {center_phase: "FLAT_CENTER"}
            for outer in (p0, p2):
                ph = str(outer.phase).upper()
                diff = (angles[ph] - angles[center_phase]) % 360.0
                roles[ph] = "FLAT_OUTER_LEADING" if abs(diff-120.0) < abs(diff-240.0) else "FLAT_OUTER_LAGGING"
            vals = [iec_lambda1_eddy_factor(cable, spacing_m=s, role=roles[p], conductor_temp_c=conductor_temp_c, sheath_temp_c=sheath_temp_c, apply_milliken_factor=_is_milliken(cable)) for p in "ABC"]
            probe_spacings = (d1m, d2m)
            rels = []
            for ph, base in zip("ABC", vals):
                probes = [iec_lambda1_eddy_factor(cable, spacing_m=sp, role=roles[ph], conductor_temp_c=conductor_temp_c, sheath_temp_c=sheath_temp_c, apply_milliken_factor=_is_milliken(cable)) for sp in probe_spacings]
                rels.append(max(abs(x-base) for x in probes)/max(abs(base),1e-12))
            if max(rels) <= FORMATION_EDDY_RELATIVE_BUDGET:
                return "FLAT", s, roles
    raise SheathLossCompletenessError("Geometri λ1'' kapalı-form duyarlılık zarfında Trefoil/Flat olarak sınıflandırılamadı (CUSTOM).")


def resolve_sheath_loss_completeness(
    project: ProjectData,
    section: InstallationCrossSectionData,
    *,
    conductor_temperatures_c: Mapping[str, float] | None = None,
    sheath_temperatures_c: Mapping[str, float] | None = None,
) -> SheathLossCompleteness:
    cable = project.cable
    scheme = str(project.bonding.scheme).upper()
    physical = [x for x in section.physical_cables if x.active]
    if not physical:
        return SheathLossCompleteness(AUTHORITY_BLOCKED, SOURCE_UNAVAILABLE, ("NO_PHYSICAL_CABLES",), ("Aktif fiziksel kablo bulunamadı.",))

    if _note3_negligible(cable):
        return SheathLossCompleteness(
            AUTHORITY_FULL, SOURCE_IEC_NOTE3, (SOURCE_IEC_NOTE3,),
            ("IEC 60287-1-1 2.3.6.1 Note 3 kapsamındaki ekran konstrüksiyonunda sheath eddy-current kaybı ihmal edilebilir.",),
            tuple((x.physical_cable_id, 0.0) for x in physical),
        )

    milliken = _is_milliken(cable)
    if scheme == BONDING_SOLID_BOTH_END and not milliken:
        return SheathLossCompleteness(
            AUTHORITY_FULL, SOURCE_IEC_SOLID, (SOURCE_IEC_SOLID,),
            ("IEC 60287-1-1 2.3: solid both-end, non-Milliken single-core branch uses circulating-current loss; λ1'' is not additionally required.",),
            tuple((x.physical_cable_id, 0.0) for x in physical),
        )

    groups = _active_groups(section)
    circuit_ids = {key[0] for key in groups}
    multiple = len(circuit_ids) != 1 or len(groups) != 1
    # External λ1'' is accepted only with reference geometry that matches every active phase group.
    group_spacings_mm: list[float] = []
    for g in groups.values():
        if len(g) == 3:
            ds = sorted(hypot(g[i].x_m-g[j].x_m, g[i].depth_m-g[j].depth_m) for i,j in ((0,1),(1,2),(2,0)))
            # Trefoil: all equal; flat: the two nearest-neighbour distances are s.
            group_spacings_mm.append(0.5 * (ds[0] + ds[1]) * 1000.0)
    representative_sheath_temp = float(cable.sheath_operating_temperature_c)
    if sheath_temperatures_c:
        representative_sheath_temp = max(float(v) for v in sheath_temperatures_c.values())
    ext, stale = _external_factor(
        cable,
        spacings_mm=tuple(group_spacings_mm),
        sheath_temp_c=representative_sheath_temp,
        active_formation=str(section.arrangement_label or "CUSTOM"),
    )
    if ext is not None:
        return SheathLossCompleteness(
            AUTHORITY_FULL, SOURCE_EXTERNAL, (SOURCE_EXTERNAL,),
            (f"Harici doğrulanmış λ1'' kullanıldı; kaynak={getattr(cable, 'sheath_eddy_external_source_type', '')}; referans={getattr(cable, 'sheath_eddy_external_reference', '')}",),
            tuple((x.physical_cable_id, float(ext)) for x in physical), float(ext),
        )
    if stale:
        return SheathLossCompleteness(AUTHORITY_BLOCKED, SOURCE_UNAVAILABLE, stale, ("Harici λ1'' referans koşulları aktif proje ile uyuşmuyor.",))
    if multiple:
        return SheathLossCompleteness(
            AUTHORITY_BLOCKED, SOURCE_UNAVAILABLE,
            ("LAMBDA1_EDDY_CLOSED_FORM_OUT_OF_SCOPE_MULTI_CIRCUIT",),
            ("IEC 60287-1-1 2.3 kapalı-form sheath-loss denklemleri tekli devre kapsamındadır; paralel/çok devre için doğrulanmış dış λ1'' gerekir.",),
        )

    group = next(iter(groups.values()))
    ctemps = conductor_temperatures_c or {}
    stemps = sheath_temperatures_c or {}
    default_ct = float(cable.max_temperature_c)
    default_st = float(cable.sheath_operating_temperature_c)
    try:
        formation, spacing_m, roles = _formation(section, group, cable, default_ct, default_st)
        factors = []
        for item in group:
            ph = str(item.phase).upper()
            factor = iec_lambda1_eddy_factor(
                cable,
                spacing_m=spacing_m,
                role=roles[ph],
                conductor_temp_c=float(ctemps.get(item.physical_cable_id, default_ct)),
                sheath_temp_c=float(stemps.get(item.physical_cable_id, default_st)),
                apply_milliken_factor=milliken,
            )
            factors.append((item.physical_cable_id, factor))
    except SheathLossCompletenessError as exc:
        return SheathLossCompleteness(
            AUTHORITY_BLOCKED, SOURCE_UNAVAILABLE,
            ("LAMBDA1_EDDY_CLOSED_FORM_CUSTOM_OR_INCOMPLETE",),
            (str(exc), "Doğrulanmış harici λ1'' olmadan IEC rating üretim otoritesi verilemez."),
        )
    note2 = ()
    if str(cable.sheath_material).strip().upper() in {"AL", "ALUMINIUM", "ALUMINUM"} and float(cable.sheath_mean_diameter_mm) > 70.0:
        note2 = ("IEC_2.3.6.1_NOTE_2_ALUMINIUM_LARGE_SHEATH",)
    return SheathLossCompleteness(
        AUTHORITY_FULL,
        SOURCE_IEC_CALCULATED,
        (SOURCE_IEC_CALCULATED,) + note2,
        (
            f"IEC 60287-1-1 2.3.6.1 λ1'' hesaplandı; formation={formation}; s={spacing_m:.6f} m; Milliken_F={'yes' if milliken else 'no'}.",
            *("IEC 2.3.6.1 Note 2: büyük/kalın alüminyum kılıfta iki terim birlikte değerlendirildi." for _ in note2),
        ),
        tuple(factors),
    )
