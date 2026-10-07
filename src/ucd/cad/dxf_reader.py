from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import ezdxf


@dataclass
class DxfGeometry:
    lines: list[tuple[tuple[float, float], tuple[float, float], str]] = field(default_factory=list)
    polylines: list[tuple[list[tuple[float, float]], bool, str]] = field(default_factory=list)
    circles: list[tuple[tuple[float, float], float, str]] = field(default_factory=list)
    texts: list[tuple[tuple[float, float], str, str]] = field(default_factory=list)
    layers: set[str] = field(default_factory=set)
    # Entity type -> number of entities that could not be read and were skipped.
    skipped_entities: dict[str, int] = field(default_factory=dict)

    @property
    def skipped_entity_count(self) -> int:
        return sum(self.skipped_entities.values())

    @property
    def skipped_entities_warning(self) -> str:
        if not self.skipped_entities:
            return ""
        details = ", ".join(f"{kind}: {count}" for kind, count in sorted(self.skipped_entities.items()))
        return (
            f"DXF uyarısı: okunamayan {self.skipped_entity_count} varlık atlandı ({details}). "
            "İçe aktarılan geometri ve uzunluk eksik olabilir; DXF dosyasını kontrol edin."
        )

    def record_skipped(self, kind: str) -> None:
        self.skipped_entities[kind] = self.skipped_entities.get(kind, 0) + 1


def read_dxf_geometry(path: str | Path) -> DxfGeometry:
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    result = DxfGeometry()

    for entity in msp:
        layer = str(entity.dxf.layer)
        result.layers.add(layer)
        kind = entity.dxftype()
        if kind == "LINE":
            result.lines.append(((float(entity.dxf.start.x), float(entity.dxf.start.y)),
                                 (float(entity.dxf.end.x), float(entity.dxf.end.y)), layer))
        elif kind == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
            if len(pts) >= 2:
                result.polylines.append((pts, bool(entity.closed), layer))
        elif kind in {"POLYLINE", "POLYFACE", "POLYMESH"}:
            try:
                pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
            except Exception:
                result.record_skipped(kind)
                continue
            if len(pts) >= 2:
                result.polylines.append((pts, bool(getattr(entity, "is_closed", False)), layer))
        elif kind == "CIRCLE":
            result.circles.append(((float(entity.dxf.center.x), float(entity.dxf.center.y)),
                                   float(entity.dxf.radius), layer))
        elif kind in {"TEXT", "MTEXT"}:
            try:
                text = entity.plain_text() if kind == "MTEXT" else str(entity.dxf.text)
                insert = entity.dxf.insert
                result.texts.append(((float(insert.x), float(insert.y)), text, layer))
            except Exception:
                result.record_skipped(kind)
    return result
