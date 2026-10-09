"""Built-in editable cable-channel section templates.

Templates are drafting aids, not certified construction details. Applying a
row preserves circuit and physical-cable identities, then updates only the
parametric channel geometry, duct slots, cable coordinates/slot assignments,
and trace metadata. Custom material polygons and external heat sources remain
in the section and are therefore revalidated after application.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, sqrt
from typing import Iterable

from ucd.calculations.installation import channel_geometry_defaults, synchronise_direct_buried_geometry
from ucd.models.project import (
    DuctSlotData,
    InstallationCrossSectionData,
    PhysicalCableData,
    THERMAL_INSTALL_CONCRETE_TROUGH,
    THERMAL_INSTALL_DIRECT_BURIED,
    THERMAL_INSTALL_DUCT_BANK,
    THERMAL_INSTALL_HDD,
)


@dataclass(frozen=True)
class CableChannelTemplate:
    template_id: str
    name: str
    installation_type: str
    arrangement: str
    description: str
    trench_width_m: float
    trench_depth_m: float
    burial_depth_m: float
    phase_spacing_m: float = 0.15
    circuit_spacing_m: float = 0.90
    parallel_spacing_m: float = 0.20
    bedding_thickness_m: float = 0.15
    thermal_backfill_height_m: float = 0.45
    selected_fill_thickness_m: float = 0.25
    side_slope_h_to_v: float = 0.0
    cover_slab_enabled: bool = True
    cover_slab_depth_m: float = 0.55
    cover_slab_width_m: float = 0.70
    cover_slab_thickness_m: float = 0.05
    duct_rows: int = 0
    duct_columns: int = 0
    duct_inner_diameter_m: float = 0.13
    duct_outer_diameter_m: float = 0.16
    duct_center_spacing_m: float = 0.22


@dataclass(frozen=True)
class TemplateApplicationResult:
    template_id: str
    moved_cable_count: int
    duct_slot_count: int
    warning_messages: tuple[str, ...]


@dataclass(frozen=True)
class CircuitPlacementData:
    """Editable per-circuit placement reconstructed from physical cable x-y data.

    The record is intentionally not a new project object.  The Kablo-Kanal
    screen reads it from, and writes it back to, the existing
    ``PhysicalCableData`` coordinates so the locked project architecture and
    JSON schema remain unchanged.
    """

    circuit_id: str
    arrangement: str
    center_x_m: float
    reference_depth_m: float
    phase_spacing_m: float
    parallel_spacing_m: float


_TEMPLATES = (
    CableChannelTemplate(
        "TPL-DB-TREFOIL-1C",
        "Doğrudan gömülü · trefoil · tek devre",
        THERMAL_INSTALL_DIRECT_BURIED,
        "TREFOIL",
        "Tek devre trefoil başlangıç kesiti; kontrollü termal dolgu ve koruma plakası.",
        0.90, 1.55, 1.15, phase_spacing_m=0.15,
    ),
    CableChannelTemplate(
        "TPL-DB-FLAT-1C",
        "Doğrudan gömülü · düz · tek devre",
        THERMAL_INSTALL_DIRECT_BURIED,
        "FLAT",
        "Tek devre düz formasyon; kablo merkezleri aynı derinlikte.",
        1.25, 1.55, 1.20, phase_spacing_m=0.20, cover_slab_width_m=1.00,
    ),
    CableChannelTemplate(
        "TPL-DB-TREFOIL-2C",
        "Doğrudan gömülü · trefoil · çift devre",
        THERMAL_INSTALL_DIRECT_BURIED,
        "TREFOIL",
        "İki trefoil devre için geniş hendek başlangıç kesiti.",
        2.20, 1.65, 1.18, phase_spacing_m=0.15, circuit_spacing_m=0.95,
        cover_slab_width_m=1.80,
    ),
    CableChannelTemplate(
        "TPL-DUCT-2X3",
        "Duct bank · 2×3 slot",
        THERMAL_INSTALL_DUCT_BANK,
        "CUSTOM",
        "Altı slotlu beton/grout duct bank başlangıç kesiti.",
        1.15, 1.65, 1.18, bedding_thickness_m=0.15,
        thermal_backfill_height_m=0.0, selected_fill_thickness_m=0.30,
        duct_rows=2, duct_columns=3, cover_slab_width_m=0.95,
    ),
    CableChannelTemplate(
        "TPL-DUCT-3X3",
        "Duct bank · 3×3 slot",
        THERMAL_INSTALL_DUCT_BANK,
        "CUSTOM",
        "Dokuz slotlu beton/grout duct bank başlangıç kesiti.",
        1.20, 1.90, 1.30, bedding_thickness_m=0.15,
        thermal_backfill_height_m=0.0, selected_fill_thickness_m=0.35,
        duct_rows=3, duct_columns=3, cover_slab_width_m=1.00,
    ),
    CableChannelTemplate(
        "TPL-TROUGH-FLAT",
        "Beton kanal · düz kablo yerleşimi",
        THERMAL_INSTALL_CONCRETE_TROUGH,
        "FLAT",
        "Kapalı beton kablo kanalı için başlangıç geometrisi; hava/radyasyon modeli değildir.",
        1.30, 1.35, 0.95, phase_spacing_m=0.22,
        thermal_backfill_height_m=0.0, selected_fill_thickness_m=0.15,
        cover_slab_enabled=False,
    ),
    CableChannelTemplate(
        "TPL-HDD-3DUCT",
        "HDD · üçlü boru grubu",
        THERMAL_INSTALL_HDD,
        "CUSTOM",
        "Üç kablo/boru için yatay yönlendirilmiş sondaj başlangıç kesiti.",
        0.90, 2.50, 2.00, phase_spacing_m=0.18,
        thermal_backfill_height_m=0.0, selected_fill_thickness_m=0.0,
        cover_slab_enabled=False, duct_rows=1, duct_columns=3,
        duct_center_spacing_m=0.20,
    ),
)


def built_in_cable_channel_templates() -> tuple[CableChannelTemplate, ...]:
    return _TEMPLATES


def cable_channel_template(template_id: str) -> CableChannelTemplate:
    key = str(template_id).strip().upper()
    for item in _TEMPLATES:
        if item.template_id.upper() == key:
            return item
    raise KeyError(f"Bilinmeyen kablo-kanal şablonu: {template_id}")


def _phase_offsets(arrangement: str, spacing_m: float) -> dict[str, tuple[float, float]]:
    arrangement = str(arrangement).upper()
    spacing = max(float(spacing_m), 0.001)
    if arrangement == "FLAT":
        return {"A": (-spacing, 0.0), "B": (0.0, 0.0), "C": (spacing, 0.0)}
    if arrangement == "VERTICAL":
        return {"A": (0.0, -spacing), "B": (0.0, 0.0), "C": (0.0, spacing)}
    lower = sqrt(3.0) * spacing / 2.0
    return {"A": (0.0, -lower / 2.0), "B": (-spacing / 2.0, lower / 2.0), "C": (spacing / 2.0, lower / 2.0)}


def lock_trefoil_centres_to_outer_diameter(
    section: InstallationCrossSectionData,
    cable_outer_diameter_m: float,
    circuit_ids: Iterable[str] | None = None,
) -> int:
    """Snap complete TREFOIL groups to the real cable outside diameter.

    The group centroid and circuit/parallel identities are preserved.  This is
    deliberately idempotent and is used after editable table values are read
    back, preventing display precision from turning a touching bundle into a
    false overlap.
    """

    diameter = max(float(cable_outer_diameter_m), 0.001)
    selected = None if circuit_ids is None else {str(value) for value in circuit_ids}
    raw = _phase_offsets("TREFOIL", diameter)
    mean_x = sum(value[0] for value in raw.values()) / 3.0
    mean_y = sum(value[1] for value in raw.values()) / 3.0
    offsets = {phase: (value[0] - mean_x, value[1] - mean_y) for phase, value in raw.items()}
    groups: dict[tuple[str, int], dict[str, PhysicalCableData]] = {}
    for cable in _active_cables(section):
        if selected is not None and str(cable.circuit_id) not in selected:
            continue
        groups.setdefault((str(cable.circuit_id), int(cable.parallel_index)), {})[str(cable.phase).upper()] = cable
    moved = 0
    for by_phase in groups.values():
        if set(by_phase) != {"A", "B", "C"}:
            continue
        center_x = sum(float(by_phase[phase].x_m) for phase in "ABC") / 3.0
        center_depth = sum(float(by_phase[phase].depth_m) for phase in "ABC") / 3.0
        for phase in "ABC":
            dx, dy = offsets[phase]
            cable = by_phase[phase]
            cable.x_m = center_x + dx
            cable.depth_m = center_depth + dy
            cable.duct_slot_id = ""
            moved += 1
    return moved


def _active_cables(section: InstallationCrossSectionData) -> list[PhysicalCableData]:
    circuits = {item.circuit_id for item in section.circuits if item.active}
    values = [
        item for item in section.physical_cables
        if item.active and item.circuit_id in circuits and str(item.phase).upper() in {"A", "B", "C"}
    ]
    values.sort(key=lambda item: (item.circuit_id, int(item.parallel_index), "ABC".index(str(item.phase).upper())))
    return values


def _infer_group_arrangement(by_phase: dict[str, PhysicalCableData], fallback: str) -> tuple[str, float]:
    if not all(phase in by_phase for phase in "ABC"):
        return str(fallback or "TREFOIL").upper(), 0.15
    points = {
        phase: (float(by_phase[phase].x_m), float(by_phase[phase].depth_m))
        for phase in "ABC"
    }
    x_values = [item[0] for item in points.values()]
    y_values = [item[1] for item in points.values()]
    x_span = max(x_values) - min(x_values)
    y_span = max(y_values) - min(y_values)
    scale = max(x_span, y_span, 1e-9)
    horizontal_pair = abs(points["B"][1] - points["C"][1]) <= max(0.012, scale * 0.12)
    a_above_pair = points["A"][1] < min(points["B"][1], points["C"][1]) - max(0.006, scale * 0.06)
    a_between_pair = min(points["B"][0], points["C"][0]) <= points["A"][0] <= max(points["B"][0], points["C"][0])
    if horizontal_pair and a_above_pair and a_between_pair:
        arrangement = "TREFOIL"
        pairs = (("A", "B"), ("B", "C"), ("C", "A"))
        distances = [
            sqrt((points[a][0] - points[b][0]) ** 2 + (points[a][1] - points[b][1]) ** 2)
            for a, b in pairs
        ]
    elif y_span > x_span * 1.20:
        arrangement = "VERTICAL"
        values = sorted(y_values)
        distances = [values[index + 1] - values[index] for index in range(2)]
    else:
        arrangement = "FLAT"
        values = sorted(x_values)
        distances = [values[index + 1] - values[index] for index in range(2)]
    positive = [item for item in distances if item > 1e-9]
    spacing = sum(positive) / len(positive) if positive else 0.15
    return arrangement, spacing


def infer_circuit_placement(
    section: InstallationCrossSectionData,
    circuit_id: str,
) -> CircuitPlacementData:
    """Infer one circuit's editable formation from existing physical objects."""

    cables = [
        item for item in _active_cables(section)
        if item.circuit_id == str(circuit_id)
    ]
    if not cables:
        raise ValueError(f"Devre için etkin fiziksel kablo bulunamadı: {circuit_id}")
    groups: dict[int, dict[str, PhysicalCableData]] = {}
    for cable in cables:
        groups.setdefault(int(cable.parallel_index), {})[str(cable.phase).upper()] = cable
    inferred = [
        _infer_group_arrangement(group, section.arrangement_label)
        for group in groups.values()
    ]
    arrangement = max(
        (item[0] for item in inferred),
        key=lambda value: sum(candidate[0] == value for candidate in inferred),
    )
    spacings = [item[1] for item in inferred if item[0] == arrangement and item[1] > 0]
    phase_spacing = sum(spacings) / len(spacings) if spacings else 0.15
    centres = []
    for group in groups.values():
        values = list(group.values())
        if values:
            centres.append((
                sum(float(item.x_m) for item in values) / len(values),
                sum(float(item.depth_m) for item in values) / len(values),
            ))
    center_x = sum(item[0] for item in centres) / len(centres)
    reference_depth = sum(item[1] for item in centres) / len(centres)
    centres_x = sorted(item[0] for item in centres)
    parallel_distances = [
        centres_x[index + 1] - centres_x[index]
        for index in range(len(centres_x) - 1)
        if centres_x[index + 1] - centres_x[index] > 1e-9
    ]
    parallel_spacing = (
        sum(parallel_distances) / len(parallel_distances)
        if parallel_distances else max(phase_spacing * 1.75, 0.25)
    )
    return CircuitPlacementData(
        str(circuit_id), arrangement, center_x, reference_depth,
        phase_spacing, parallel_spacing,
    )


def reposition_circuit_cables(
    section: InstallationCrossSectionData,
    circuit_id: str,
    arrangement: str,
    *,
    center_x_m: float,
    reference_depth_m: float,
    phase_spacing_m: float,
    parallel_spacing_m: float,
    cable_outer_diameter_m: float,
) -> TemplateApplicationResult:
    """Reposition one circuit without changing other circuits or identities."""

    normalized = str(arrangement).strip().upper()
    if normalized not in {"TREFOIL", "FLAT", "VERTICAL"}:
        raise ValueError("Devre formasyonu TREFOIL, FLAT veya VERTICAL olmalıdır.")
    cables = [
        item for item in _active_cables(section)
        if item.circuit_id == str(circuit_id)
    ]
    if not cables:
        raise ValueError(f"Devre için etkin fiziksel kablo bulunamadı: {circuit_id}")
    parallel_indices = sorted({int(item.parallel_index) for item in cables})
    phase_spacing, parallel_pitch, _unused, warnings = _formation_layout_metrics(
        normalized, phase_spacing_m, cable_outer_diameter_m, parallel_spacing_m,
        999.0, len(parallel_indices),
    )
    raw_offsets = _phase_offsets(normalized, phase_spacing)
    mean_x = sum(item[0] for item in raw_offsets.values()) / 3.0
    mean_y = sum(item[1] for item in raw_offsets.values()) / 3.0
    offsets = {phase: (value[0] - mean_x, value[1] - mean_y) for phase, value in raw_offsets.items()}
    moved = 0
    for cable in cables:
        parallel_position = parallel_indices.index(int(cable.parallel_index))
        p_offset = (parallel_position - (len(parallel_indices) - 1) / 2.0) * parallel_pitch
        phase_x, phase_y = offsets[str(cable.phase).upper()]
        cable.x_m = float(center_x_m) + p_offset + phase_x
        cable.depth_m = max(float(cable_outer_diameter_m) / 2.0, float(reference_depth_m) + phase_y)
        cable.duct_slot_id = ""
        moved += 1
    formations = []
    for existing_id in sorted({item.circuit_id for item in _active_cables(section)}):
        try:
            formations.append(infer_circuit_placement(section, existing_id).arrangement)
        except ValueError:
            pass
    section.arrangement_label = formations[0] if formations and len(set(formations)) == 1 else "CUSTOM"
    section.source_reference = "USER_CIRCUIT_PLACEMENT_OVERRIDES"
    section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
    return TemplateApplicationResult(
        f"CIRCUIT:{circuit_id}:{normalized}", moved, len(section.duct_slots), warnings,
    )


def _formation_layout_metrics(
    arrangement: str,
    phase_spacing_m: float,
    cable_outer_diameter_m: float,
    requested_parallel_pitch_m: float,
    requested_circuit_pitch_m: float,
    parallel_count: int,
) -> tuple[float, float, float, tuple[str, ...]]:
    """Return physical phase spacing, parallel pitch and circuit pitch.

    TREFOIL phase centres are locked to the real cable outer diameter, so the
    three cable jackets form a touching triangular bundle.  User-entered phase
    spacing is used only by FLAT/VERTICAL and is increased when cable envelopes
    would overlap.  The function is used by visual/template layout helpers and
    does not alter the locked production calculation engines.
    """

    arrangement = str(arrangement).upper()
    diameter = max(float(cable_outer_diameter_m), 0.001)
    clearance = max(0.015, diameter * 0.12)
    # A true trefoil is a bound three-cable bundle: all phase cable jackets
    # touch and every phase centre distance equals the real cable outer
    # diameter.  A user-entered phase spacing is therefore not a trefoil
    # design variable.  FLAT/VERTICAL retain their editable centre spacing.
    phase_spacing = (
        diameter
        if arrangement == "TREFOIL"
        else max(float(phase_spacing_m), diameter + clearance)
    )
    offsets = _phase_offsets(arrangement, phase_spacing)
    x_values = [float(value[0]) for value in offsets.values()]
    formation_width = max(x_values) - min(x_values)
    parallel_pitch_min = formation_width + diameter + clearance
    parallel_pitch = max(float(requested_parallel_pitch_m), parallel_pitch_min)
    circuit_footprint = formation_width + max(0, int(parallel_count) - 1) * parallel_pitch
    circuit_pitch_min = circuit_footprint + diameter + clearance
    circuit_pitch = max(float(requested_circuit_pitch_m), circuit_pitch_min)

    warnings: list[str] = []
    if arrangement != "TREFOIL" and phase_spacing > float(phase_spacing_m) + 1e-12:
        warnings.append(
            f"Faz merkez aralığı kablo zarflarının çakışmaması için {phase_spacing:.3f} m yapıldı."
        )
    if parallel_pitch > float(requested_parallel_pitch_m) + 1e-12:
        warnings.append(
            f"Paralel formasyon merkez aralığı çakışmayı önlemek için {parallel_pitch:.3f} m yapıldı."
        )
    if circuit_pitch > float(requested_circuit_pitch_m) + 1e-12:
        warnings.append(
            f"Devre merkez aralığı formasyon zarfları için {circuit_pitch:.3f} m yapıldı."
        )
    return phase_spacing, parallel_pitch, circuit_pitch, tuple(warnings)


def reposition_existing_cables(
    section: InstallationCrossSectionData,
    arrangement: str,
    *,
    burial_depth_m: float,
    phase_spacing_m: float,
    circuit_spacing_m: float,
    parallel_spacing_m: float,
    cable_outer_diameter_m: float,
) -> TemplateApplicationResult:
    """Apply a free-air formation to existing physical cable identities.

    This is the direct connection used by the side ``Formasyon`` selector.
    Circuit/phase/parallel assignments are preserved; only x-y positions and
    the arrangement label are updated.
    """

    normalized = str(arrangement).strip().upper()
    if normalized not in {"TREFOIL", "FLAT", "VERTICAL"}:
        raise ValueError("Doğrudan formasyon seçimi TREFOIL, FLAT veya VERTICAL olmalıdır.")
    cables = _active_cables(section)
    if not cables:
        raise ValueError("Kesitte yeniden konumlandırılacak etkin fiziksel kablo bulunmuyor.")
    circuit_ids = sorted({item.circuit_id for item in cables})
    parallel_count = max(
        (len({int(item.parallel_index) for item in cables if item.circuit_id == circuit_id}) for circuit_id in circuit_ids),
        default=1,
    )
    phase_spacing, parallel_pitch, circuit_pitch, warnings = _formation_layout_metrics(
        normalized, phase_spacing_m, cable_outer_diameter_m, parallel_spacing_m,
        circuit_spacing_m, parallel_count,
    )
    offsets = _phase_offsets(normalized, phase_spacing)
    moved = 0
    for circuit_index, circuit_id in enumerate(circuit_ids):
        circuit_center = (circuit_index - (len(circuit_ids) - 1) / 2.0) * circuit_pitch
        circuit_cables = [item for item in cables if item.circuit_id == circuit_id]
        parallel_indices = sorted({int(item.parallel_index) for item in circuit_cables})
        for cable in circuit_cables:
            p_index = parallel_indices.index(int(cable.parallel_index))
            p_offset = (p_index - (len(parallel_indices) - 1) / 2.0) * parallel_pitch
            phase_x, phase_y = offsets[str(cable.phase).upper()]
            cable.x_m = circuit_center + p_offset + phase_x
            cable.depth_m = float(burial_depth_m) + phase_y
            cable.duct_slot_id = ""
            moved += 1
    section.arrangement_label = normalized
    section.duct_slots = []
    section.source_reference = f"USER_FORMATION_SELECTION:{normalized}"
    section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
    return TemplateApplicationResult(normalized, moved, 0, warnings)


def _apply_free_layout(
    section: InstallationCrossSectionData,
    template: CableChannelTemplate,
    cable_outer_diameter_m: float,
) -> tuple[int, tuple[str, ...]]:
    cables = _active_cables(section)
    circuit_ids = sorted({item.circuit_id for item in cables})
    parallel_count = max(
        (len({int(item.parallel_index) for item in cables if item.circuit_id == circuit_id}) for circuit_id in circuit_ids),
        default=1,
    )
    phase_spacing, parallel_pitch, circuit_pitch, warnings = _formation_layout_metrics(
        template.arrangement, template.phase_spacing_m, cable_outer_diameter_m,
        template.parallel_spacing_m, template.circuit_spacing_m, parallel_count,
    )
    phase_offsets = _phase_offsets(template.arrangement, phase_spacing)
    moved = 0
    for circuit_index, circuit_id in enumerate(circuit_ids):
        circuit_center = (circuit_index - (len(circuit_ids) - 1) / 2.0) * circuit_pitch
        circuit_cables = [item for item in cables if item.circuit_id == circuit_id]
        parallel_indices = sorted({int(item.parallel_index) for item in circuit_cables})
        for cable in circuit_cables:
            p_index = parallel_indices.index(int(cable.parallel_index)) if int(cable.parallel_index) in parallel_indices else 0
            p_offset = (p_index - (len(parallel_indices) - 1) / 2.0) * parallel_pitch
            phase_x, phase_y = phase_offsets[str(cable.phase).upper()]
            cable.x_m = circuit_center + p_offset + phase_x
            cable.depth_m = template.burial_depth_m + phase_y
            cable.duct_slot_id = ""
            moved += 1
    section.duct_slots = []
    return moved, warnings


def _apply_duct_layout(section: InstallationCrossSectionData, template: CableChannelTemplate) -> tuple[int, int, list[str]]:
    cables = _active_cables(section)
    requested = max(1, int(template.duct_rows) * int(template.duct_columns))
    rows = max(1, int(template.duct_rows))
    columns = max(1, int(template.duct_columns))
    warnings: list[str] = []
    if len(cables) > requested:
        rows = max(rows, int(ceil(len(cables) / columns)))
        warnings.append(
            f"Etkin kablo sayısı şablon kapasitesini aştı; duct satır sayısı otomatik {rows} yapıldı."
        )
    spacing = max(template.duct_center_spacing_m, template.duct_outer_diameter_m + 0.02)
    centre_depth = max(template.burial_depth_m, 0.30)
    slots: list[DuctSlotData] = []
    for row in range(rows):
        for column in range(columns):
            slot_number = row * columns + column + 1
            x_m = (column - (columns - 1) / 2.0) * spacing
            depth_m = centre_depth + (row - (rows - 1) / 2.0) * spacing
            slots.append(DuctSlotData(
                f"DS-{slot_number:02d}", x_m, depth_m,
                template.duct_inner_diameter_m, template.duct_outer_diameter_m,
                row + 1, column + 1, True,
            ))
    for index, cable in enumerate(cables):
        slot = slots[index]
        cable.x_m = slot.x_m
        cable.depth_m = slot.depth_m
        cable.duct_slot_id = slot.slot_id
    for cable in section.physical_cables:
        if cable not in cables:
            cable.duct_slot_id = ""
    section.duct_slots = slots
    return len(cables), len(slots), warnings


def apply_cable_channel_template(
    section: InstallationCrossSectionData,
    template_id: str,
    *,
    cable_outer_diameter_m: float = 0.105,
) -> TemplateApplicationResult:
    template = cable_channel_template(template_id)
    geometry = channel_geometry_defaults(
        template.installation_type,
        burial_depth_m=template.burial_depth_m,
        cable_span_m=max(template.trench_width_m - 0.40, cable_outer_diameter_m * 3.0),
    )
    geometry.trench_width_m = template.trench_width_m
    geometry.trench_depth_m = template.trench_depth_m
    geometry.side_slope_h_to_v = template.side_slope_h_to_v
    geometry.bedding_thickness_m = template.bedding_thickness_m
    geometry.thermal_backfill_height_m = template.thermal_backfill_height_m
    geometry.selected_fill_thickness_m = template.selected_fill_thickness_m
    geometry.cover_slab_enabled = template.cover_slab_enabled
    geometry.cover_slab_depth_m = template.cover_slab_depth_m
    geometry.cover_slab_width_m = template.cover_slab_width_m
    geometry.cover_slab_thickness_m = template.cover_slab_thickness_m
    geometry.source_reference = f"USER_SECTION_TEMPLATE:{template.template_id}"

    section.installation_type = template.installation_type
    section.arrangement_label = template.arrangement
    section.channel_geometry = geometry
    section.source_reference = f"USER_SECTION_TEMPLATE:{template.template_id}"

    warnings: list[str] = []
    if template.installation_type in {THERMAL_INSTALL_DUCT_BANK, THERMAL_INSTALL_HDD}:
        moved, slot_count, warnings = _apply_duct_layout(section, template)
        geometry.duct_bank_width_m = max(
            geometry.duct_bank_width_m,
            (max(template.duct_columns, 1) - 1) * template.duct_center_spacing_m + template.duct_outer_diameter_m + 0.10,
        )
        geometry.duct_bank_height_m = max(
            geometry.duct_bank_height_m,
            (max(template.duct_rows, 1) - 1) * template.duct_center_spacing_m + template.duct_outer_diameter_m + 0.10,
        )
        if template.installation_type == THERMAL_INSTALL_HDD:
            geometry.hdd_bore_diameter_m = max(
                geometry.hdd_bore_diameter_m,
                geometry.duct_bank_width_m + 0.10,
            )
    else:
        moved, layout_warnings = _apply_free_layout(section, template, cable_outer_diameter_m)
        warnings.extend(layout_warnings)
        slot_count = 0
        if template.installation_type == THERMAL_INSTALL_CONCRETE_TROUGH:
            geometry.trough_inner_width_m = max(0.75, template.trench_width_m - 0.30)
            geometry.trough_inner_height_m = 0.75
            geometry.trough_wall_thickness_m = 0.10
        elif template.installation_type == THERMAL_INSTALL_DIRECT_BURIED:
            synchronise_direct_buried_geometry(section, cable_outer_diameter_m)

    if section.material_regions:
        warnings.append("Özel malzeme polygonları korundu; yeni şablon geometrisine göre yeniden doğrulanmalıdır.")
    if section.external_heat_sources:
        warnings.append("Harici ısı kaynakları korundu; yeni şablon geometrisine göre yeniden doğrulanmalıdır.")
    return TemplateApplicationResult(template.template_id, moved, slot_count, tuple(warnings))
