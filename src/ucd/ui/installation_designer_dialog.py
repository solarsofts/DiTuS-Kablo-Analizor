from __future__ import annotations

from .window_layout import fit_window, DENSITY_FULL, DENSITY_NORMAL

from copy import deepcopy
from dataclasses import asdict
import json
from math import isfinite, sqrt
from pathlib import Path
import re

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QTimer, QSettings
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ucd.calculations.installation import (
    InstallationInputError,
    channel_geometry_bounds,
    channel_half_width_at_depth,
    channel_polygon_vertices,
    direct_buried_envelope,
    direct_buried_warning_depths,
    synchronise_direct_buried_geometry,
    insert_material_region_vertex,
    remove_material_region_vertex,
    section_clearance_records,
    generate_standard_cross_section,
    installation_summary,
    resolved_physical_cables,
    update_channel_geometry_for_installation,
    validate_installation_design,
)
from ucd.calculations.cable_channel_templates import (
    apply_cable_channel_template,
    built_in_cable_channel_templates,
    infer_circuit_placement,
    lock_trefoil_centres_to_outer_diameter,
    reposition_circuit_cables,
    reposition_existing_cables,
)
from ucd.calculations.export_safety import SpreadsheetSafeDictWriter
from ucd.calculations.multiconductor_thermal import (
    MulticonductorThermalInputError,
    solve_multiconductor_thermal,
)
from ucd.calculations.thermal_material_library import (
    merge_reference_materials,
    validate_material_for_final_design,
)
from ucd.models.project import (
    CableChannelGeometryData,
    DuctSlotData,
    ExternalHeatSourceData,
    INSTALLATION_COUPLING_PRODUCTION_LINKED,
    InstallationCircuitData,
    InstallationCrossSectionData,
    PhysicalCableData,
    ProjectData,
    ThermalMaterialRegionData,
    THERMAL_INSTALL_DIRECT_BURIED,
    THERMAL_INSTALL_DUCT_BANK,
    THERMAL_INSTALL_HDD,
    THERMAL_INSTALL_CONCRETE_TROUGH,
    THERMAL_INSTALL_TUNNEL,
)


INSTALLATION_TYPE_TR = {
    THERMAL_INSTALL_DIRECT_BURIED: "doğrudan gömülü",
    THERMAL_INSTALL_DUCT_BANK: "boru / kanal bankası",
    THERMAL_INSTALL_HDD: "yatay yönlendirilmiş sondaj",
    THERMAL_INSTALL_CONCRETE_TROUGH: "beton kablo kanalı",
    THERMAL_INSTALL_TUNNEL: "kablo tüneli",
}

FORMATION_DISPLAY = {
    "TREFOIL": "Üçgen formasyon (TREFOIL)",
    "FLAT": "Düz formasyon (FLAT)",
    "VERTICAL": "Düşey formasyon (VERTICAL)",
    "DUCT_BANK": "Boru / kanal yerleşimi (DUCT BANK)",
    "CUSTOM": "Özel yerleşim",
}

# Stable role colors: material-ID visibility adds labels, never recolors the
# construction layers.  This prevents the trench from changing appearance
# when a database material is selected.
LAYER_COLORS = {
    # High-contrast engineering palette.  Pattern fills remain distinct even
    # on monochrome prints; colors are only a second visual cue.
    "NATIVE_SOIL": QColor("#b99a6b"),
    "GENERAL_BACKFILL": QColor("#eee5d2"),
    "SELECTED_BACKFILL": QColor("#d7b784"),
    "THERMAL_BACKFILL": QColor("#f0ca4e"),
    "BEDDING_SAND": QColor("#f8e6a1"),
    "SURFACE": QColor("#879b7e"),
    "CONCRETE": QColor("#aab4ba"),
    "DUCT_BANK": QColor("#c2d0d8"),
}

LAYER_ROLE_LABELS = {
    "NATIVE_SOIL": "Native soil / doğal zemin",
    "GENERAL_BACKFILL": "General backfill / üst dolgu",
    "SELECTED_BACKFILL": "Backfill / seçilmiş dolgu",
    "THERMAL_BACKFILL": "Thermal backfill / kablo çevresi",
    "BEDDING_SAND": "Bedding sand / yatak kumu",
    "SURFACE": "Yüzey tabakası",
    "CONCRETE": "Koruma plakası / beton",
    "DUCT_BANK": "Duct bank / grout-beton",
}


def _formation_code(value: str) -> str:
    code = str(value or "CUSTOM").strip().upper()
    return code if code in FORMATION_DISPLAY else "CUSTOM"


def _formation_display(value: str) -> str:
    return FORMATION_DISPLAY.get(_formation_code(value), FORMATION_DISPLAY["CUSTOM"])


def _section_formation_display(section: InstallationCrossSectionData) -> str:
    values: list[tuple[str, str]] = []
    for circuit_id in sorted({item.circuit_id for item in section.physical_cables if item.active}):
        try:
            values.append((circuit_id, infer_circuit_placement(section, circuit_id).arrangement))
        except ValueError:
            pass
    if not values:
        return _formation_display(section.arrangement_label)
    if len({item[1] for item in values}) == 1:
        return _formation_display(values[0][1])
    return "Karma devre yerleşimi — " + " · ".join(f"{cid}:{formation}" for cid, formation in values)


class _GeometryHandleItem(QGraphicsRectItem):
    """Small draggable handle that writes one parametric geometry value."""

    def __init__(self, key: str, callback, *, axis: str = "xy") -> None:
        super().__init__(-6.0, -6.0, 12.0, 12.0)
        self.key = key
        self.callback = callback
        self.axis = axis
        self._updating = False
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setBrush(QBrush(QColor("#f6c344")))
        self.setPen(QPen(QColor("#735c00"), 1.4))
        self.setZValue(20)
        self.setToolTip("Sürükleyerek ölçüyü değiştirin")

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.ItemPositionChange and not self._updating:
            point = QPointF(value)
            current = self.pos()
            if self.axis == "x":
                point.setY(current.y())
            elif self.axis == "y":
                point.setX(current.x())
            return point
        result = super().itemChange(change, value)
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene() is not None and not self._updating:
            self.callback(self.key, self.pos())
        return result


class _CableGraphicsItem(QGraphicsEllipseItem):
    def __init__(
        self,
        cable: PhysicalCableData,
        diameter_px: float,
        callback,
        layer_specs: tuple[tuple[float, QColor, QColor], ...] = (),
    ) -> None:
        super().__init__(-diameter_px / 2.0, -diameter_px / 2.0, diameter_px, diameter_px)
        self.cable = cable
        self.callback = callback
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        phase_key = str(cable.phase).upper()
        phase_pen = {
            "A": QColor("#b23b3b"),
            "B": QColor("#a47a00"),
            "C": QColor("#2f6fa3"),
        }.get(phase_key, QColor("#1f618d"))
        # The outer circle is the real overall cable diameter.  Construction
        # layers are rendered concentrically from the selected project cable;
        # no artificial display diameter is introduced.
        self.setBrush(QBrush(QColor("#30363a")))
        self.setPen(QPen(QColor("#202528"), 0.8))
        for order, (layer_diameter_px, fill, outline) in enumerate(layer_specs):
            size = max(1.0, min(float(layer_diameter_px), float(diameter_px)))
            layer = QGraphicsEllipseItem(-size / 2.0, -size / 2.0, size, size, self)
            layer.setBrush(QBrush(fill))
            layer.setPen(QPen(outline, 0.7))
            layer.setAcceptedMouseButtons(Qt.NoButton)
            layer.setZValue(float(order + 1))
        phase_ring = QGraphicsEllipseItem(
            -diameter_px / 2.0, -diameter_px / 2.0, diameter_px, diameter_px, self
        )
        phase_ring.setBrush(QBrush(Qt.NoBrush))
        phase_ring.setPen(QPen(phase_pen, 2.5))
        phase_ring.setAcceptedMouseButtons(Qt.NoButton)
        phase_ring.setZValue(90)
        phase = QGraphicsSimpleTextItem(phase_key, self)
        phase.setFont(QFont("Segoe UI", 6, QFont.Bold))
        phase.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        phase.setBrush(QBrush(QColor("#111111")))
        phase.setZValue(100)
        bounds = phase.boundingRect()
        phase.setPos(-bounds.width() / 2.0, -bounds.height() / 2.0)
        self.setToolTip(
            f"{cable.physical_cable_id}\nDevre: {cable.circuit_id}\n"
            f"Faz: {cable.phase} / Paralel: {cable.parallel_index}"
        )

    def itemChange(self, change, value):  # noqa: N802
        result = super().itemChange(change, value)
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene() is not None:
            self.callback(self)
        return result


class _DuctGraphicsItem(QGraphicsEllipseItem):
    def __init__(self, slot: DuctSlotData, outer_diameter_px: float, inner_diameter_px: float, callback) -> None:
        super().__init__(
            -outer_diameter_px / 2.0, -outer_diameter_px / 2.0,
            outer_diameter_px, outer_diameter_px,
        )
        self.slot = slot
        self.callback = callback
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setBrush(QBrush(QColor("#d1d9de")))
        self.setPen(QPen(QColor("#465965"), 2.0))
        inner = QGraphicsEllipseItem(
            -inner_diameter_px / 2.0, -inner_diameter_px / 2.0,
            inner_diameter_px, inner_diameter_px, self,
        )
        inner.setBrush(QBrush(QColor("#f7fafb")))
        inner.setPen(QPen(QColor("#6f7f89"), 1.2))
        inner.setAcceptedMouseButtons(Qt.NoButton)
        self.setToolTip(
            f"{slot.slot_id}\nSatır {slot.row_index} / Sütun {slot.column_index}\n"
            f"İç/dış çap: {slot.inner_diameter_m:.3f}/{slot.outer_diameter_m:.3f} m"
        )

    def itemChange(self, change, value):  # noqa: N802
        result = super().itemChange(change, value)
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene() is not None:
            self.callback(self)
        return result


class _HeatSourceGraphicsItem(QGraphicsRectItem):
    def __init__(self, source: ExternalHeatSourceData, radius_px: float, callback) -> None:
        super().__init__(-radius_px, -radius_px, radius_px * 2.0, radius_px * 2.0)
        self.source = source
        self.callback = callback
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setBrush(QBrush(QColor(246, 214, 184, 190)))
        self.setPen(QPen(QColor("#b05b1e"), 2, Qt.DashLine))
        self.setToolTip(
            f"{source.name}: {source.heat_w_m:g} W/m\n"
            "Sürükleyerek harici ısı kaynağını taşıyın"
        )

    def itemChange(self, change, value):  # noqa: N802
        result = super().itemChange(change, value)
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene() is not None:
            self.callback(self)
        return result


class _MaterialRegionGraphicsItem(QGraphicsPolygonItem):
    def __init__(self, region: ThermalMaterialRegionData, polygon: QPolygonF, callback) -> None:
        super().__init__(polygon)
        self.region = region
        self.callback = callback
        self._last_pos = QPointF(0.0, 0.0)
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        hue = sum(ord(ch) for ch in str(region.material_id)) % 360
        self.setBrush(QBrush(QColor.fromHsv(hue, 95, 220, 105)))
        self.setPen(QPen(QColor.fromHsv(hue, 150, 125), 1.8, Qt.DashLine))
        self.setToolTip(
            f"{region.region_id} — {region.name}\nMalzeme: {region.material_id}\n"
            "Bölgeyi bütün olarak sürükleyebilirsiniz"
        )

    def itemChange(self, change, value):  # noqa: N802
        result = super().itemChange(change, value)
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene() is not None:
            current = self.pos()
            delta = current - self._last_pos
            self._last_pos = QPointF(current)
            if abs(delta.x()) > 1e-9 or abs(delta.y()) > 1e-9:
                self.callback(self, delta.x(), delta.y())
        return result


class _MaterialVertexHandleItem(QGraphicsEllipseItem):
    """Draggable polygon vertex handle linked to one material region."""

    def __init__(self, region_id: str, vertex_index: int, callback, select_callback, parent=None) -> None:
        super().__init__(-5.5, -5.5, 11.0, 11.0, parent)
        self.region_id = region_id
        self.vertex_index = int(vertex_index)
        self.callback = callback
        self.select_callback = select_callback
        self._updating = False
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setBrush(QBrush(QColor("#fff3b0")))
        self.setPen(QPen(QColor("#8a6500"), 1.4))
        self.setZValue(25)
        self.setToolTip(f"{region_id} · köşe {vertex_index + 1}\nSürükleyerek köşeyi değiştirin")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.select_callback(self.region_id, self.vertex_index)
        super().mousePressEvent(event)

    def itemChange(self, change, value):  # noqa: N802
        result = super().itemChange(change, value)
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene() is not None and not self._updating:
            self.callback(self)
        return result




_SPIN_BUTTON_STYLE = """
QDoubleSpinBox, QSpinBox {
    padding-right: 26px;
    min-height: 24px;
}
QDoubleSpinBox::up-button, QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 24px;
    height: 14px;
    border-left: 1px solid #aebdca;
    border-bottom: 1px solid #c8d2dc;
    border-top-right-radius: 3px;
    background: #f4f7fa;
}
QDoubleSpinBox::down-button, QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 24px;
    height: 14px;
    border-left: 1px solid #aebdca;
    border-top: 1px solid #c8d2dc;
    border-bottom-right-radius: 3px;
    background: #f4f7fa;
}
QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover {
    background: #dceaf5;
}
QDoubleSpinBox::up-button:pressed, QSpinBox::up-button:pressed,
QDoubleSpinBox::down-button:pressed, QSpinBox::down-button:pressed {
    background: #c8ddeb;
}
"""


class _InstallationSpinControlMixin:
    """Kurulum girdilerinde güvenli ve öngörülebilir adım kontrolü.

    Mouse wheel bir QScrollArea içindeki sayısal alanın üzerinden geçerken
    değeri yanlışlıkla değiştirmemelidir. Olay üst kapsayıcıya bırakılır ve
    panel kaydırılır. Ok düğmeleri için geniş, birbirinden ayrılmış alt-kontrol
    alanları kullanılır; yukarı/aşağı tıklama ``stepBy`` üzerinden aynı kesin
    tek-adım davranışına sahiptir.
    """

    def _configure_installation_spin(self) -> None:
        self.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
        self.setAccelerated(False)
        self.setWrapping(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(30)
        self.setStyleSheet(_SPIN_BUTTON_STYLE)
        self.setToolTip(
            "Ok düğmeleri veya klavye ile tek adım değiştirin. "
            "Mouse wheel bu alanın değerini değiştirmez; paneli kaydırır."
        )

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # Bazı Windows/Qt tema birleşimlerinde stil alt-kontrolleri üst üste
        # düşerek yalnız alt okun tıklanmasına yol açabiliyor. Sağdaki 24 px
        # kontrol şeridini doğrudan iki eşit bölgeye ayırmak tema bağımsızdır.
        in_button_strip = event.position().x() >= max(0.0, self.width() - 26.0)
        if event.button() == Qt.LeftButton and in_button_strip:
            self._installation_button_press = True
            self.setFocus(Qt.MouseFocusReason)
            self.stepBy(1 if event.position().y() < self.height() / 2.0 else -1)
            event.accept()
            return
        self._installation_button_press = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if getattr(self, "_installation_button_press", False):
            self._installation_button_press = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def stepBy(self, steps: int) -> None:  # noqa: N802
        # Qt stil/tema farklılıklarında üst ve alt alt-kontrol aynı güvenilir
        # sayısal yolu kullanır. Sınırlandırma setValue tarafından yapılır.
        if not steps or self.isReadOnly():
            return
        self.setValue(self.value() + int(steps) * self.singleStep())


class _InstallationDoubleSpinBox(_InstallationSpinControlMixin, QDoubleSpinBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._configure_installation_spin()


class _InstallationIntegerSpinBox(_InstallationSpinControlMixin, QSpinBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._configure_installation_spin()


class InstallationCanvas(QGraphicsView):
    cableMoved = Signal(str, float, float)
    ductMoved = Signal(str, float, float)
    heatSourceMoved = Signal(str, float, float)
    materialRegionMoved = Signal(str, float, float)
    materialRegionVertexMoved = Signal(str, int, float, float)
    materialRegionVertexSelected = Signal(str, int)
    geometryChanged = Signal(str, float)

    def __init__(self, parent=None) -> None:
        self.scene_obj = QGraphicsScene()
        super().__init__(self.scene_obj, parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        # Read-only engineering preview: geometry cannot be edited from the
        # canvas, but the user may pan and zoom the view safely.  NoAnchor plus
        # explicit cursor-centred correction prevents wheel zoom from drifting
        # toward the right edge.
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setInteractive(False)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setCursor(Qt.OpenHandCursor)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._interactive_editing = False
        self.scale_px_m = 260.0
        self.origin_x = 620.0
        self.surface_y = 70.0
        self._section: InstallationCrossSectionData | None = None
        self._diameter_m = 0.105
        self._items: dict[str, _CableGraphicsItem] = {}
        self._duct_items: dict[str, _DuctGraphicsItem] = {}
        self._heat_items: dict[str, _HeatSourceGraphicsItem] = {}
        self._region_items: dict[str, _MaterialRegionGraphicsItem] = {}
        self._region_vertex_items: dict[tuple[str, int], _MaterialVertexHandleItem] = {}
        self._geometry_handles: dict[str, _GeometryHandleItem] = {}
        self._show_material_ids = False
        self._material_names: dict[str, str] = {}
        self._temperature_overlay = None
        self._dimension_mode = "CONSTRUCTION"
        self._show_result_labels = False
        self._render_temperature_contour = False
        self._contour_items: list[QGraphicsItem] = []
        self._last_content_rect = QRectF()
        self._zoom_fit_scale: float | None = None
        self._zoom_view_mode = "MANUAL"
        self._zoom_applying_fit = False
        self._show_object_ids = False
        self._show_detail_labels = False
        self._layer_colors = {key: QColor(value) for key, value in LAYER_COLORS.items()}
        self.setMinimumSize(300, 260)

    def wheelEvent(self, event) -> None:  # noqa: N802
        current = max(abs(float(self.transform().m11())), 1e-9)
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        zoom_in = delta > 0
        factor = 1.08 if zoom_in else 1 / 1.08
        target = current * factor
        fit_scale = self._zoom_fit_scale

        if fit_scale is not None:
            if not zoom_in and target <= fit_scale * 1.001:
                self.fit_to_section()
                event.accept()
                return
            maximum = fit_scale * 16.0
            if zoom_in and current >= maximum * 0.999:
                event.accept()
                return
            if zoom_in and target > maximum:
                factor = maximum / current

        cursor_pos = event.position().toPoint()
        scene_before = self.mapToScene(cursor_pos)
        # MANUAL is set before scale(): scrollbar appearance can synchronously
        # resize the viewport, and that must never cancel the user's wheel zoom.
        self._zoom_view_mode = "MANUAL"
        self.scale(factor, factor)
        scene_after = self.mapToScene(cursor_pos)
        correction = scene_after - scene_before
        self.translate(correction.x(), correction.y())
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._zoom_view_mode == "FIT" and not self._zoom_applying_fit:
            self.fit_to_section()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # ScrollHandDrag pans the read-only drawing; scene items remain
        # non-interactive and therefore cannot mutate the section model.
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # Double-click is intentionally inert: it must not reset or refit the
        # view.  Use the explicit Kesite Sığdır / 1:1 controls instead.
        event.accept()

    def fit_to_section(self) -> None:
        if not self._last_content_rect.isValid() or self._last_content_rect.isEmpty():
            return
        if self.viewport().width() <= 4 or self.viewport().height() <= 4:
            return
        self._zoom_applying_fit = True
        try:
            self.resetTransform()
            self.fitInView(self._last_content_rect, Qt.KeepAspectRatio)
            self._zoom_fit_scale = max(abs(float(self.transform().m11())), 1e-9)
            self._zoom_view_mode = "FIT"
        finally:
            self._zoom_applying_fit = False

    def zoom_reset(self) -> None:
        self._zoom_view_mode = "MANUAL"
        self.resetTransform()
        self.centerOn(self._last_content_rect.center() if self._last_content_rect.isValid() else QPointF())

    def _scene_xy(self, x_m: float, depth_m: float) -> tuple[float, float]:
        return self.origin_x + x_m * self.scale_px_m, self.surface_y + depth_m * self.scale_px_m

    def _model_xy(self, point: QPointF) -> tuple[float, float]:
        return (
            (point.x() - self.origin_x) / self.scale_px_m,
            max(0.0, (point.y() - self.surface_y) / self.scale_px_m),
        )

    @staticmethod
    def _as_brush(value) -> QBrush:
        return value if isinstance(value, QBrush) else QBrush(value)

    def _add_rect_m(self, left: float, top: float, width: float, height: float, brush, pen: QPen, z: float = 0.0):
        x, y = self._scene_xy(left, top)
        item = self.scene_obj.addRect(
            x, y, max(width * self.scale_px_m, 1.0), max(height * self.scale_px_m, 1.0),
            pen, self._as_brush(brush),
        )
        item.setZValue(z)
        return item

    def _polygon_scene(self, vertices_m) -> QPolygonF:
        return QPolygonF([QPointF(*self._scene_xy(float(x), float(depth))) for x, depth in vertices_m])

    def _add_polygon_m(self, vertices_m, brush, pen: QPen, z: float = 0.0):
        item = self.scene_obj.addPolygon(self._polygon_scene(vertices_m), pen, self._as_brush(brush))
        item.setZValue(z)
        return item

    def _layer_vertices(self, section: InstallationCrossSectionData, top: float, bottom: float):
        g = section.channel_geometry
        top = max(0.0, min(float(top), g.trench_depth_m))
        bottom = max(top, min(float(bottom), g.trench_depth_m))
        top_half = channel_half_width_at_depth(section, top)
        bottom_half = channel_half_width_at_depth(section, bottom)
        centre = float(g.center_x_m)
        return (
            (centre - top_half, top),
            (centre + top_half, top),
            (centre + bottom_half, bottom),
            (centre - bottom_half, bottom),
        )

    def _add_horizontal_dimension(self, label: str, left_m: float, right_m: float, depth_m: float) -> None:
        """Draw one dimension in the same scene coordinate system as the trench.

        Dimension labels deliberately scale together with their extension lines.  Earlier
        revisions used ``ItemIgnoresTransformations`` for the text only; during pan/zoom
        that mixed device and scene coordinates and made the marks appear to drift away
        from the material boundary they describe.
        """
        x1, y = self._scene_xy(left_m, depth_m)
        x2, _ = self._scene_xy(right_m, depth_m)
        pen = QPen(QColor("#334e5c"), 1.2)
        pen.setCosmetic(True)
        self.scene_obj.addLine(x1, y, x2, y, pen).setZValue(15)
        self.scene_obj.addLine(x1, y - 6, x1, y + 6, pen).setZValue(15)
        self.scene_obj.addLine(x2, y - 6, x2, y + 6, pen).setZValue(15)
        text = self.scene_obj.addSimpleText(label, QFont("Segoe UI", 7, QFont.Bold))
        text.setPos((x1 + x2) / 2.0 - text.boundingRect().width() / 2.0, y - 18)
        text.setBrush(QColor("#334e5c")); text.setZValue(15)

    def _add_vertical_dimension(
        self, label: str, x_m: float, top_m: float, bottom_m: float, *, text_side: str = "right"
    ) -> None:
        x, y1 = self._scene_xy(x_m, top_m)
        _, y2 = self._scene_xy(x_m, bottom_m)
        pen = QPen(QColor("#334e5c"), 1.2)
        pen.setCosmetic(True)
        self.scene_obj.addLine(x, y1, x, y2, pen).setZValue(15)
        self.scene_obj.addLine(x - 6, y1, x + 6, y1, pen).setZValue(15)
        self.scene_obj.addLine(x - 6, y2, x + 6, y2, pen).setZValue(15)
        text = self.scene_obj.addSimpleText(label, QFont("Segoe UI", 7, QFont.Bold))
        if str(text_side).lower() == "left":
            text_x = x - text.boundingRect().width() - 8.0
        else:
            text_x = x + 8.0
        text.setPos(text_x, (y1 + y2) / 2.0 - text.boundingRect().height() / 2.0)
        text.setBrush(QColor("#334e5c")); text.setZValue(35)

    def _add_dimension(self, text: str, x_m: float, depth_m: float) -> None:
        x, y = self._scene_xy(x_m, depth_m)
        label = self.scene_obj.addSimpleText(text, QFont("Segoe UI", 7, QFont.Bold))
        label.setPos(x + 4, y - 13)
        label.setBrush(QColor("#334e5c"))
        label.setZValue(15)

    def _add_geometry_handle(self, key: str, x_m: float, depth_m: float, axis: str) -> None:
        handle = _GeometryHandleItem(key, self._geometry_item_moved, axis=axis)
        x, y = self._scene_xy(x_m, depth_m)
        handle._updating = True
        handle.setPos(x, y)
        handle._updating = False
        self.scene_obj.addItem(handle)
        self._geometry_handles[key] = handle

    @staticmethod
    def _material_color(material_id: str, fallback: QColor, alpha: int = 170) -> QColor:
        key = str(material_id or "").strip()
        if not key:
            color = QColor(fallback)
            color.setAlpha(alpha)
            return color
        hue = sum((index + 1) * ord(ch) for index, ch in enumerate(key)) % 360
        color = QColor.fromHsv(hue, 95, 220, alpha)
        return color

    def _material_fill(self, material_id: str, fallback: QColor, alpha: int = 170) -> QColor:
        # Role colors remain stable. Material IDs are communicated by labels and
        # tooltips, not by an unpredictable hash color.
        color = QColor(fallback)
        color.setAlpha(alpha if color.alpha() == 255 else color.alpha())
        return color

    @staticmethod
    def _cable_layer_specs(cable_layers, overall_diameter_m: float, scale_px_m: float):
        """Return visible concentric construction rings for the selected cable."""
        overall_mm = max(float(overall_diameter_m) * 1000.0, 0.1)
        raw = []
        for layer in cable_layers or ():
            outer_mm = float(getattr(layer, "outer_diameter_mm", 0.0) or 0.0)
            if outer_mm <= 0.0 or outer_mm > overall_mm * 1.02:
                continue
            layer_type = str(getattr(layer, "layer_type", "")).upper()
            material = str(getattr(layer, "material", "")).upper()
            if "CONDUCTOR" in layer_type:
                fill = QColor("#c87933") if "CU" in material else QColor("#b7c0c8")
                outline = QColor("#77502e")
            elif "INSULATION" in layer_type:
                fill = QColor("#f5ead5")
                outline = QColor("#b5a078")
            elif "SEMICON" in layer_type or "SCREEN" in layer_type:
                fill = QColor("#697177")
                outline = QColor("#3f4549")
            elif "SHEATH" in layer_type or "SERVING" in layer_type:
                fill = QColor("#353b3f")
                outline = QColor("#161a1c")
            elif "ARMOUR" in layer_type:
                fill = QColor("#8b949a")
                outline = QColor("#4d565c")
            else:
                fill = QColor("#c7ced2")
                outline = QColor("#6c757a")
            raw.append((outer_mm / 1000.0 * scale_px_m, fill, outline))
        # Draw largest first, then smaller layers on top.  Near-identical
        # diameters are collapsed so labels remain readable at normal zoom.
        raw.sort(key=lambda item: item[0], reverse=True)
        result = []
        last = None
        for item in raw:
            if last is not None and abs(last - item[0]) < 1.2:
                continue
            result.append(item)
            last = item[0]
        return tuple(result[:8])

    def _add_material_label(self, material_id: str, x_m: float, depth_m: float, *, prefix: str = "") -> None:
        if not self._show_material_ids or not str(material_id or "").strip():
            return
        material_id = str(material_id).strip()
        x, y = self._scene_xy(x_m, depth_m)
        name = self._material_names.get(material_id, "")
        text_value = f"{prefix}{material_id}" + (f" · {name}" if name else "")
        label = self.scene_obj.addSimpleText(text_value, QFont("Segoe UI", 7, QFont.Bold))
        label.setPos(x + 4, y - 11)
        label.setBrush(QColor("#1d3545"))
        label.setZValue(16)

    def _add_layer_caption(self, text: str, x_m: float, depth_m: float, *, color: str = "#4a3a26") -> None:
        x, y = self._scene_xy(x_m, depth_m)
        label = self.scene_obj.addSimpleText(text, QFont("Segoe UI", 7, QFont.Bold))
        label.setBrush(QColor(color))
        label.setPos(x - label.boundingRect().width() / 2.0, y - label.boundingRect().height() / 2.0)
        label.setZValue(14)

    def _draw_layer_legend(self, section: InstallationCrossSectionData) -> None:
        if not self._show_detail_labels:
            return
        if str(section.installation_type).upper() not in {THERMAL_INSTALL_DIRECT_BURIED, THERMAL_INSTALL_DUCT_BANK}:
            return
        items = (
            ("Native soil / doğal zemin", self._layer_colors["NATIVE_SOIL"], Qt.Dense6Pattern),
            ("General backfill / üst dolgu", self._layer_colors["GENERAL_BACKFILL"], Qt.Dense5Pattern),
            ("Backfill / seçilmiş dolgu", self._layer_colors["SELECTED_BACKFILL"], Qt.Dense4Pattern),
            ("Thermal backfill / kablo çevresi", self._layer_colors["THERMAL_BACKFILL"], Qt.DiagCrossPattern),
            ("Bedding sand / yatak kumu", self._layer_colors["BEDDING_SAND"], Qt.Dense7Pattern),
        )
        # Keep the legend outside the engineering section.  It is a page
        # annotation and must never cover bedding, warning elements or cables.
        left = 930.0
        top = self.surface_y + 18.0
        panel = self.scene_obj.addRect(
            left - 10.0, top - 10.0, 292.0, len(items) * 23.0 + 20.0,
            QPen(QColor("#9aaab3"), 0.9), QBrush(QColor(255, 255, 255, 225)),
        )
        panel.setZValue(39)
        for index, (text, color, pattern) in enumerate(items):
            y = top + index * 23.0
            swatch = self.scene_obj.addRect(
                left, y, 28.0, 14.0,
                QPen(QColor("#68583f"), 0.8), QBrush(color, pattern),
            )
            swatch.setZValue(40)
            label = self.scene_obj.addSimpleText(text, QFont("Segoe UI", 7, QFont.Bold))
            label.setPos(left + 35.0, y - 1.0)
            label.setBrush(QColor("#31424d"))
            label.setZValue(40)

    @staticmethod
    def _temperature_color(value: float, minimum: float, maximum: float, alpha: int = 92) -> QColor:
        span = max(maximum - minimum, 1e-9)
        ratio = max(0.0, min(1.0, (float(value) - minimum) / span))
        hue = int(round((1.0 - ratio) * 240.0))
        color = QColor.fromHsv(hue, 230, 245, alpha)
        return color

    def _draw_temperature_contour(self) -> None:
        overlay = self._temperature_overlay
        if overlay is None or not self._render_temperature_contour:
            return
        x_edges = tuple(float(value) for value in overlay.x_edges_m)
        depth_edges = tuple(float(value) for value in overlay.depth_edges_m)
        values = tuple(tuple(float(value) for value in row) for row in overlay.temperature_c)
        ny = min(len(values), max(0, len(depth_edges) - 1))
        nx = min((len(values[0]) if ny else 0), max(0, len(x_edges) - 1))
        if nx <= 0 or ny <= 0:
            return
        flat = [values[j][i] for j in range(ny) for i in range(nx)]
        minimum, maximum = min(flat), max(flat)
        # Keep the graphics scene responsive for very fine meshes.  Coarsened
        # cells use the maximum temperature in each visual block.
        target_cells = 3200
        stride = max(1, int(((nx * ny) / target_cells) ** 0.5 + 0.999999))
        for j in range(0, ny, stride):
            j2 = min(j + stride, ny)
            for i in range(0, nx, stride):
                i2 = min(i + stride, nx)
                block = [values[jj][ii] for jj in range(j, j2) for ii in range(i, i2)]
                value = max(block)
                x0, y0 = self._scene_xy(x_edges[i], depth_edges[j])
                x1, y1 = self._scene_xy(x_edges[i2], depth_edges[j2])
                item = self.scene_obj.addRect(
                    min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0),
                    QPen(Qt.NoPen), QBrush(self._temperature_color(value, minimum, maximum)),
                )
                item.setZValue(1.65)
                self._contour_items.append(item)
        legend_x, legend_y = 930.0, 92.0
        legend_h, legend_w = 150.0, 18.0
        steps = 30
        for index in range(steps):
            ratio = index / max(steps - 1, 1)
            value = maximum - ratio * (maximum - minimum)
            item = self.scene_obj.addRect(
                legend_x, legend_y + ratio * legend_h, legend_w, legend_h / steps + 1.0,
                QPen(Qt.NoPen), QBrush(self._temperature_color(value, minimum, maximum, 210)),
            )
            item.setZValue(30)
            self._contour_items.append(item)
        for text, y in ((f"{maximum:.1f} °C", legend_y - 2), (f"{minimum:.1f} °C", legend_y + legend_h - 12)):
            label = self.scene_obj.addSimpleText(text, QFont("Segoe UI", 7, QFont.Bold))
            label.setPos(legend_x + 24, y); label.setZValue(31); label.setBrush(QColor("#213746"))
            self._contour_items.append(label)
        title = self.scene_obj.addSimpleText("2D sıcaklık", QFont("Segoe UI", 8, QFont.Bold))
        title.setPos(legend_x - 3, legend_y - 22); title.setZValue(31); title.setBrush(QColor("#213746"))
        self._contour_items.append(title)

    def clear_temperature_overlay(self) -> None:
        for item in list(self._contour_items):
            if item.scene() is self.scene_obj:
                self.scene_obj.removeItem(item)
        self._contour_items.clear()
        self._temperature_overlay = None

    def export_png(self, path: str, *, width_px: int = 2600) -> None:
        target = self.scene_obj.itemsBoundingRect().adjusted(-40, -40, 40, 40)
        ratio = target.height() / max(target.width(), 1.0)
        height_px = max(1400, int(width_px * ratio))
        image = QImage(width_px, height_px, QImage.Format_ARGB32)
        image.fill(QColor("white"))
        image.setDotsPerMeterX(11811)
        image.setDotsPerMeterY(11811)
        painter = QPainter(image)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.scene_obj.render(painter, QRectF(0, 0, width_px, height_px), target)
        painter.end()
        if not image.save(str(path), "PNG"):
            raise OSError(f"Kesit görseli kaydedilemedi: {path}")

    def _draw_layer_boundary(self, section: InstallationCrossSectionData, depth_m: float, color: str) -> None:
        depth = max(0.0, min(float(depth_m), float(section.channel_geometry.trench_depth_m)))
        half = channel_half_width_at_depth(section, depth)
        x1, y = self._scene_xy(section.channel_geometry.center_x_m - half, depth)
        x2, _ = self._scene_xy(section.channel_geometry.center_x_m + half, depth)
        pen = QPen(QColor(color), 1.35, Qt.DashLine)
        pen.setCosmetic(True)
        item = self.scene_obj.addLine(x1, y, x2, y, pen)
        item.setZValue(4.0)

    def _draw_warning_system(self, section: InstallationCrossSectionData) -> None:
        if str(section.installation_type).upper() != THERMAL_INSTALL_DIRECT_BURIED:
            return
        try:
            mesh_depth, tape_depth = direct_buried_warning_depths(section, self._diameter_m)
        except InstallationInputError:
            return
        g = section.channel_geometry
        for depth, label, color, style, width in (
            (mesh_depth, "Uyarı ağı", "#c43c35", Qt.DashLine, 2.0),
            (tape_depth, "Uyarı bandı", "#e86132", Qt.SolidLine, 4.0),
        ):
            if depth is None or depth >= g.trench_depth_m:
                continue
            half = channel_half_width_at_depth(section, depth)
            x1, y = self._scene_xy(g.center_x_m - half + 0.03, depth)
            x2, _ = self._scene_xy(g.center_x_m + half - 0.03, depth)
            pen = QPen(QColor(color), width, style)
            pen.setCosmetic(True)
            item = self.scene_obj.addLine(x1, y, x2, y, pen)
            item.setZValue(5.0)
            if self._show_detail_labels:
                caption = self.scene_obj.addSimpleText(label, QFont("Segoe UI", 7, QFont.Bold))
                caption.setBrush(QColor(color))
                caption.setPos(x2 - caption.boundingRect().width(), y - 17)
                caption.setZValue(12)

    def _draw_flat_spacers(self, section: InstallationCrossSectionData) -> None:
        g = section.channel_geometry
        if str(section.installation_type).upper() != THERMAL_INSTALL_DIRECT_BURIED or not bool(g.spacer_enabled):
            return
        radius = self._diameter_m / 2.0
        groups: dict[tuple[str, int], list[PhysicalCableData]] = {}
        for cable in section.physical_cables:
            if cable.active:
                groups.setdefault((cable.circuit_id, int(cable.parallel_index)), []).append(cable)
        arrangement_by_circuit: dict[str, str] = {}
        for circuit_id, _parallel_index in groups:
            if circuit_id not in arrangement_by_circuit:
                try:
                    arrangement_by_circuit[circuit_id] = infer_circuit_placement(section, circuit_id).arrangement
                except ValueError:
                    arrangement_by_circuit[circuit_id] = _formation_code(section.arrangement_label)
        for (circuit_id, _parallel_index), cables in groups.items():
            if arrangement_by_circuit.get(circuit_id) != "FLAT":
                continue
            ordered = sorted(cables, key=lambda item: float(item.x_m))
            for first, second in zip(ordered, ordered[1:]):
                gap = float(second.x_m) - float(first.x_m) - 2.0 * radius
                if gap <= 0.004:
                    continue
                width = min(max(0.015, float(g.spacer_width_m)), gap * 0.82)
                height = min(max(0.015, float(g.spacer_height_m)), max(self._diameter_m * 0.80, 0.025))
                center_x = (float(first.x_m) + float(second.x_m)) / 2.0
                center_depth = (float(first.depth_m) + float(second.depth_m)) / 2.0
                self._add_rect_m(
                    center_x - width / 2.0,
                    center_depth - height / 2.0,
                    width,
                    height,
                    QBrush(QColor("#c77b46"), Qt.Dense4Pattern),
                    QPen(QColor("#7f4728"), 1.2),
                    3.8,
                )

    def _draw_channel_geometry(self, section: InstallationCrossSectionData) -> None:
        g = section.channel_geometry
        left, right, bottom = channel_geometry_bounds(section)
        width = right - left
        kind = str(section.installation_type).upper()

        # Native soil is outside the excavation. The entire channel polygon is
        # first filled as general backfill, then the lower engineered layers are
        # overlaid in a fixed role palette. Material IDs never change these role
        # colors; they are shown separately as labels.
        channel_vertices = channel_polygon_vertices(section)
        self._add_polygon_m(
            channel_vertices,
            QBrush(self._layer_colors["GENERAL_BACKFILL"], Qt.Dense5Pattern),
            QPen(QColor("#5d452f"), 3.2), -20,
        )
        bedding = max(0.0, min(g.bedding_thickness_m, bottom))
        if kind == THERMAL_INSTALL_DIRECT_BURIED:
            try:
                envelope = direct_buried_envelope(section, self._diameter_m)
                bedding_top = max(0.0, min(envelope.bedding_top_m, bottom))
                bedding = max(0.0, bottom - bedding_top)
            except InstallationInputError:
                bedding_top = bottom - bedding
        else:
            bedding_top = bottom - bedding
        backfill = max(0.0, min(g.thermal_backfill_height_m, bedding_top))
        backfill_top = bedding_top - backfill
        selected = max(0.0, min(g.selected_fill_thickness_m, backfill_top))
        selected_top = backfill_top - selected

        if kind == THERMAL_INSTALL_DIRECT_BURIED:
            if selected_top > 0.0:
                self._add_polygon_m(
                    self._layer_vertices(section, 0.0, selected_top),
                    QBrush(self._layer_colors["GENERAL_BACKFILL"], Qt.Dense5Pattern),
                    QPen(QColor("#8c7757"), 1.0), -18.0,
                )
            if selected > 0.0:
                self._add_polygon_m(
                    self._layer_vertices(section, selected_top, backfill_top),
                    QBrush(self._layer_colors["SELECTED_BACKFILL"], Qt.Dense4Pattern),
                    QPen(QColor("#765a35"), 1.2), -17.0,
                )
            if backfill > 0.0:
                self._add_polygon_m(
                    self._layer_vertices(section, backfill_top, bedding_top),
                    QBrush(self._layer_colors["THERMAL_BACKFILL"], Qt.DiagCrossPattern),
                    QPen(QColor("#79620d"), 1.4), -16.0,
                )
            if bedding > 0.0:
                self._add_polygon_m(
                    self._layer_vertices(section, bedding_top, bottom),
                    QBrush(self._layer_colors["BEDDING_SAND"], Qt.Dense7Pattern),
                    QPen(QColor("#987617"), 1.2), -15.0,
                )
            for boundary_depth, boundary_color in (
                (selected_top, "#8c7757"),
                (backfill_top, "#765a35"),
                (bedding_top, "#79620d"),
            ):
                if 0.0 < boundary_depth < bottom:
                    self._draw_layer_boundary(section, boundary_depth, boundary_color)
            if self._show_detail_labels:
                if selected_top > 0.10:
                    self._add_layer_caption("GENERAL BACKFILL", g.center_x_m, selected_top / 2.0)
                if selected > 0.08:
                    self._add_layer_caption("BACKFILL", g.center_x_m, (selected_top + backfill_top) / 2.0)
                if backfill > 0.08:
                    self._add_layer_caption("THERMAL BACKFILL", g.center_x_m, (backfill_top + bedding_top) / 2.0)
                if bedding > 0.06:
                    self._add_layer_caption("BEDDING SAND", g.center_x_m, (bedding_top + bottom) / 2.0)
        elif kind == THERMAL_INSTALL_DUCT_BANK:
            bank_w = min(max(g.duct_bank_width_m, 0.10), width)
            bank_h = min(max(g.duct_bank_height_m, 0.10), max(0.10, bottom - bedding))
            active_slots = [item for item in section.duct_slots if item.active]
            active_cables = [item for item in section.physical_cables if item.active]
            depth_source = active_slots if active_slots else active_cables
            mean_depth = (
                sum(float(item.depth_m) for item in depth_source) / len(depth_source)
                if depth_source else max(0.10, bottom - bedding - bank_h / 2.0)
            )
            bank_top = max(0.0, min(bottom - bank_h, mean_depth - bank_h / 2.0))
            if bank_top > 0.0:
                self._add_polygon_m(
                    self._layer_vertices(section, 0.0, bank_top),
                    QBrush(self._layer_colors["GENERAL_BACKFILL"], Qt.Dense5Pattern),
                    QPen(QColor("#8c7757"), 1.0), -18.0,
                )
            if bedding > 0.0:
                self._add_polygon_m(
                    self._layer_vertices(section, bottom - bedding, bottom),
                    QBrush(self._layer_colors["BEDDING_SAND"], Qt.Dense7Pattern),
                    QPen(QColor("#987617"), 1.2), -15.0,
                )
            self._add_rect_m(
                g.center_x_m - bank_w / 2.0, bank_top, bank_w, bank_h,
                QBrush(self._layer_colors["DUCT_BANK"], Qt.Dense4Pattern),
                QPen(QColor("#425563"), 3.0), -14.0,
            )
            if 0.0 < bank_top < bottom:
                self._draw_layer_boundary(section, bank_top, "#647985")
            if 0.0 < bottom - bedding < bottom:
                self._draw_layer_boundary(section, bottom - bedding, "#987617")
            if self._show_detail_labels:
                self._add_layer_caption("GENERAL BACKFILL", g.center_x_m, max(0.08, bank_top / 2.0))
                self._add_layer_caption("DUCT BANK / GROUT", g.center_x_m, bank_top + bank_h / 2.0, color="#304b5a")
                if bedding > 0.06:
                    self._add_layer_caption("BEDDING SAND", g.center_x_m, bottom - bedding / 2.0)
        elif kind == THERMAL_INSTALL_CONCRETE_TROUGH:
            wall = max(0.02, g.trough_wall_thickness_m)
            iw = max(0.20, g.trough_inner_width_m)
            ih = max(0.20, g.trough_inner_height_m)
            outer_w = min(width, iw + 2 * wall)
            outer_h = min(bottom, ih + 2 * wall)
            active_cables = [item for item in section.physical_cables if item.active]
            cable_radius = max(self._diameter_m / 2.0, 0.001)
            trough_bottom = min(
                bottom,
                max((float(item.depth_m) for item in active_cables), default=bottom - wall)
                + cable_radius + wall,
            )
            x0 = g.center_x_m - outer_w / 2.0
            y0 = max(0.0, trough_bottom - outer_h)
            self._add_rect_m(x0, y0, outer_w, outer_h, self._material_fill(g.trough_material_id, QColor("#a8b3ba")), QPen(QColor("#394b55"), 3.0), 0)
            self._add_rect_m(x0 + wall, y0 + wall, max(0.01, outer_w - 2 * wall), max(0.01, outer_h - 2 * wall), QColor("#e9e0d2"), QPen(QColor("#6b747a"), 1.0), 1)
        elif kind == THERMAL_INSTALL_HDD:
            active_cables = [item for item in section.physical_cables if item.active]
            centre_depth = (
                sum(float(item.depth_m) for item in active_cables) / len(active_cables)
                if active_cables else bottom * 0.70
            )
            diameter = max(g.hdd_bore_diameter_m, 0.10)
            x, y = self._scene_xy(g.center_x_m, centre_depth)
            ellipse = self.scene_obj.addEllipse(
                x - diameter * self.scale_px_m / 2.0, y - diameter * self.scale_px_m / 2.0,
                diameter * self.scale_px_m, diameter * self.scale_px_m,
                QPen(QColor("#4e5a62"), 2.2), QBrush(QColor("#aeb7bd")),
            )
            ellipse.setZValue(0)
        elif kind == THERMAL_INSTALL_TUNNEL:
            tw = max(g.tunnel_width_m, 0.50)
            th = max(g.tunnel_height_m, 0.50)
            top = max(0.10, bottom - th)
            self._add_rect_m(g.center_x_m - tw / 2.0, top, tw, th, QColor("#c0c8cd"), QPen(QColor("#4b5962"), 2.3), 0)
            self._add_rect_m(g.center_x_m - tw / 2.0 + 0.10, top + 0.10, max(0.10, tw - 0.20), max(0.10, th - 0.20), QColor("#eef2f4"), QPen(Qt.NoPen), 1)

        if g.surface_layer_thickness_m > 0.0:
            self._add_polygon_m(
                self._layer_vertices(section, 0.0, min(g.surface_layer_thickness_m, bottom)),
                QBrush(self._layer_colors["SURFACE"], Qt.Dense3Pattern), QPen(Qt.NoPen), -10
            )
        if g.cover_slab_enabled and kind in {THERMAL_INSTALL_DIRECT_BURIED, THERMAL_INSTALL_DUCT_BANK, THERMAL_INSTALL_CONCRETE_TROUGH}:
            slab_w = min(max(g.cover_slab_width_m, 0.05), width)
            slab_t = min(max(g.cover_slab_thickness_m, 0.01), bottom)
            slab_depth = min(max(g.cover_slab_depth_m, slab_t), bottom - 0.01)
            self._add_rect_m(g.center_x_m - slab_w / 2.0, slab_depth - slab_t / 2.0, slab_w, slab_t, self._layer_colors["CONCRETE"], QPen(QColor("#4c565c"), 1.5), 3)

        if kind == THERMAL_INSTALL_DIRECT_BURIED:
            self._draw_warning_system(section)
            self._draw_flat_spacers(section)

        outline = self.scene_obj.addPolygon(
            self._polygon_scene(channel_vertices), QPen(QColor("#6a5138"), 3.2), QBrush(Qt.NoBrush)
        )
        outline.setZValue(4.2)

        # Optional material-ID engineering view.
        self._add_material_label(g.native_soil_material_id, left - 0.05, min(0.18, bottom * 0.15), prefix="Native soil: ")
        if kind == THERMAL_INSTALL_DIRECT_BURIED:
            self._add_material_label(g.bedding_material_id, g.center_x_m, max(0.02, (bedding_top + bottom) / 2.0), prefix="Bedding sand: ")
            self._add_material_label(g.thermal_backfill_material_id, g.center_x_m, max(0.02, (backfill_top + bedding_top) / 2.0), prefix="Thermal backfill: ")
            self._add_material_label(g.selected_fill_material_id, g.center_x_m, max(0.02, (selected_top + backfill_top) / 2.0), prefix="Backfill: ")
        elif kind == THERMAL_INSTALL_DUCT_BANK:
            self._add_material_label(g.duct_bank_material_id, g.center_x_m, max(0.05, bottom - bedding - g.duct_bank_height_m / 2.0), prefix="Bank: ")
        elif kind == THERMAL_INSTALL_CONCRETE_TROUGH:
            self._add_material_label(g.trough_material_id, g.center_x_m, max(0.05, bottom - g.trough_inner_height_m), prefix="Kanal: ")
        elif kind == THERMAL_INSTALL_HDD:
            self._add_material_label(g.hdd_grout_material_id, g.center_x_m, max(0.05, bottom * 0.70), prefix="Grout: ")
        if g.surface_layer_thickness_m > 0.0:
            self._add_material_label(g.surface_material_id, g.center_x_m, g.surface_layer_thickness_m / 2.0, prefix="Yüzey: ")
        if g.cover_slab_enabled:
            self._add_material_label(g.cover_slab_material_id, g.center_x_m, g.cover_slab_depth_m, prefix="Plaka: ")

        # Bottom width remains the construction width; top width expands
        # according to the side slope H:V. Handles are intentionally disabled
        # because the drawing is a read-only preview.
        bottom_half = max(g.trench_width_m, 0.01) / 2.0
        top_half = channel_half_width_at_depth(section, 0.0)
        if self._interactive_editing:
            self._add_geometry_handle("trench_width_m", g.center_x_m + bottom_half, bottom * 0.82, "x")
            self._add_geometry_handle("side_slope_h_to_v", g.center_x_m + top_half, 0.03, "x")
            self._add_geometry_handle("trench_depth_m", g.center_x_m, bottom, "y")
            if kind == THERMAL_INSTALL_DIRECT_BURIED:
                self._add_geometry_handle("bedding_thickness_m", g.center_x_m + channel_half_width_at_depth(section, bedding_top) + 0.08, bedding_top, "y")
                self._add_geometry_handle("thermal_backfill_height_m", g.center_x_m + channel_half_width_at_depth(section, backfill_top) + 0.16, backfill_top, "y")
                self._add_geometry_handle("selected_fill_thickness_m", g.center_x_m + channel_half_width_at_depth(section, selected_top) + 0.24, selected_top, "y")
            elif kind == THERMAL_INSTALL_DUCT_BANK:
                self._add_geometry_handle("duct_bank_width_m", g.center_x_m + g.duct_bank_width_m / 2.0, bottom - bedding - g.duct_bank_height_m / 2.0, "x")
                self._add_geometry_handle("duct_bank_height_m", g.center_x_m, bottom - bedding - g.duct_bank_height_m, "y")
            elif kind == THERMAL_INSTALL_CONCRETE_TROUGH:
                self._add_geometry_handle("trough_inner_width_m", g.center_x_m + g.trough_inner_width_m / 2.0, bottom - g.trough_inner_height_m / 2.0, "x")
                self._add_geometry_handle("trough_inner_height_m", g.center_x_m, bottom - g.trough_inner_height_m - 2.0 * g.trough_wall_thickness_m, "y")
            elif kind == THERMAL_INSTALL_HDD:
                centre_depth = min((item.depth_m for item in section.physical_cables if item.active), default=bottom * 0.70)
                self._add_geometry_handle("hdd_bore_diameter_m", g.center_x_m + g.hdd_bore_diameter_m / 2.0, centre_depth, "x")
            elif kind == THERMAL_INSTALL_TUNNEL:
                tunnel_top = max(0.10, bottom - g.tunnel_height_m)
                self._add_geometry_handle("tunnel_width_m", g.center_x_m + g.tunnel_width_m / 2.0, tunnel_top + g.tunnel_height_m / 2.0, "x")
                self._add_geometry_handle("tunnel_height_m", g.center_x_m, tunnel_top, "y")
            if g.cover_slab_enabled:
                self._add_geometry_handle("cover_slab_depth_m", g.center_x_m, g.cover_slab_depth_m, "y")
                self._add_geometry_handle("cover_slab_width_m", g.center_x_m + g.cover_slab_width_m / 2.0, g.cover_slab_depth_m, "x")

        if self._dimension_mode != "NONE":
            bottom_name = (
                "Hendek alt genişliği" if kind == THERMAL_INSTALL_DIRECT_BURIED
                else "Kazı alt genişliği" if kind == THERMAL_INSTALL_DUCT_BANK
                else "Kesit alt genişliği"
            )
            top_name = "Hendek üst genişliği" if kind == THERMAL_INSTALL_DIRECT_BURIED else "Kazı üst genişliği"
            self._add_horizontal_dimension(
                f"{bottom_name} = {g.trench_width_m:.2f} m",
                g.center_x_m - bottom_half, g.center_x_m + bottom_half, bottom + 0.05,
            )
            if g.side_slope_h_to_v > 0.0:
                self._add_horizontal_dimension(
                    f"{top_name} = {2.0 * top_half:.2f} m",
                    g.center_x_m - top_half, g.center_x_m + top_half, 0.08,
                )
            if kind == THERMAL_INSTALL_DUCT_BANK:
                bank_w_dimension = min(max(g.duct_bank_width_m, 0.10), width)
                self._add_horizontal_dimension(
                    f"Duct bank blok genişliği = {bank_w_dimension:.2f} m",
                    g.center_x_m - bank_w_dimension / 2.0,
                    g.center_x_m + bank_w_dimension / 2.0,
                    max(0.05, bottom - max(g.bedding_thickness_m, 0.0) + 0.035),
                )
            self._add_vertical_dimension(
                f"Toplam derinlik = {bottom:.2f} m",
                g.center_x_m + top_half + 0.12, 0.0, bottom,
            )
        if self._dimension_mode in {"CONSTRUCTION", "ALL"} and kind == THERMAL_INSTALL_DIRECT_BURIED:
            dimension_x = g.center_x_m - top_half - 0.16
            if bedding > 0.0:
                self._add_vertical_dimension(
                    f"Yatak kumu zarfı {bedding:.2f} m", dimension_x, bedding_top, bottom, text_side="left"
                )
                try:
                    envelope = direct_buried_envelope(section, self._diameter_m)
                    cover_x = dimension_x - 0.10
                    self._add_vertical_dimension(
                        f"Üst kum örtüsü {g.bedding_top_cover_m:.2f} m",
                        cover_x, envelope.bedding_top_m, envelope.cable_top_m, text_side="left",
                    )
                    self._add_vertical_dimension(
                        f"Alt kum örtüsü {g.bedding_bottom_cover_m:.2f} m",
                        cover_x - 0.10, envelope.cable_bottom_m, envelope.bedding_bottom_m, text_side="left",
                    )
                except InstallationInputError:
                    pass
            if backfill > 0.0:
                self._add_vertical_dimension(
                    f"Thermal backfill {backfill:.2f} m", dimension_x - 0.10, backfill_top, bedding_top, text_side="left"
                )
            if selected > 0.0:
                self._add_vertical_dimension(
                    f"Backfill {selected:.2f} m", dimension_x - 0.20, selected_top, backfill_top, text_side="left"
                )
        if self._dimension_mode in {"CONSTRUCTION", "ALL"} and g.cover_slab_enabled:
            self._add_vertical_dimension(
                f"Plaka kotu {g.cover_slab_depth_m:.2f} m",
                g.center_x_m + g.cover_slab_width_m / 2.0 + 0.08,
                0.0, g.cover_slab_depth_m,
            )

    def _draw_installation_integrity_overlay(self, section: InstallationCrossSectionData) -> None:
        """Show incomplete installation geometry directly on the engineering canvas."""
        kind = str(section.installation_type).upper()
        cables = [item for item in section.physical_cables if item.active]
        cable_radius = max(self._diameter_m / 2.0, 0.0005)
        g = section.channel_geometry
        messages: list[str] = []
        if kind == THERMAL_INSTALL_DUCT_BANK:
            slots = {item.slot_id: item for item in section.duct_slots if item.active}
            if cables and not slots:
                messages.append("DUCT GEOMETRİSİ EKSİK: aktif kablolar için duct slotları tanımlı değil.")
            elif any(not str(item.duct_slot_id or "") or str(item.duct_slot_id) not in slots for item in cables):
                messages.append("DUCT ATAMASI EKSİK: bazı aktif kablolar bir duct slotuna bağlı değil.")
        elif kind == THERMAL_INSTALL_HDD and cables:
            centre_depth = sum(float(item.depth_m) for item in cables) / len(cables)
            radius = max(0.0, float(g.hdd_bore_diameter_m)) / 2.0
            if any((((float(item.x_m) - float(g.center_x_m)) ** 2 + (float(item.depth_m) - centre_depth) ** 2) ** 0.5 + cable_radius) > radius + 1e-9 for item in cables):
                messages.append("HDD ZARFI YETERSİZ: kablo grubu bore çapının dışında kalıyor.")
        elif kind == THERMAL_INSTALL_CONCRETE_TROUGH and cables:
            wall = max(0.0, float(g.trough_wall_thickness_m))
            iw = max(0.0, float(g.trough_inner_width_m))
            ih = max(0.0, float(g.trough_inner_height_m))
            outer_h = ih + 2.0 * wall
            trough_bottom = min(float(g.trench_depth_m), max(float(item.depth_m) for item in cables) + cable_radius + wall)
            trough_top = max(0.0, trough_bottom - outer_h)
            inner_top, inner_bottom = trough_top + wall, trough_bottom - wall
            left, right = float(g.center_x_m) - iw / 2.0, float(g.center_x_m) + iw / 2.0
            if any(not (float(item.x_m) - cable_radius >= left - 1e-9 and float(item.x_m) + cable_radius <= right + 1e-9 and float(item.depth_m) - cable_radius >= inner_top - 1e-9 and float(item.depth_m) + cable_radius <= inner_bottom + 1e-9) for item in cables):
                messages.append("BETON KANAL ZARFI YETERSİZ: kablolardan en az biri iç boşluk yerine duvar bölgesine taşıyor.")
        if not messages:
            return
        label = self.scene_obj.addText("\n".join(messages), QFont("Segoe UI", 9, QFont.Bold))
        label.setDefaultTextColor(QColor("#a72d2d"))
        label.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        label.setPos(18, self.surface_y + 28)
        label.setZValue(80)

    def _draw_electrical_dimensions(self, section: InstallationCrossSectionData) -> None:
        if self._dimension_mode not in {"ELECTRICAL", "ALL"}:
            return
        cables = sorted(
            [item for item in section.physical_cables if item.active],
            key=lambda item: (float(item.x_m), float(item.depth_m), item.physical_cable_id),
        )
        if not cables:
            return
        for cable in cables:
            self._add_vertical_dimension(
                f"h({cable.physical_cable_id})={cable.depth_m:.2f} m",
                float(cable.x_m) + self._diameter_m / 2.0 + 0.035,
                0.0, float(cable.depth_m),
            )
        for first, second in zip(cables, cables[1:]):
            distance = ((first.x_m - second.x_m) ** 2 + (first.depth_m - second.depth_m) ** 2) ** 0.5
            depth = max(float(first.depth_m), float(second.depth_m)) + self._diameter_m / 2.0 + 0.08
            self._add_horizontal_dimension(
                f"e={distance:.3f} m",
                min(float(first.x_m), float(second.x_m)),
                max(float(first.x_m), float(second.x_m)),
                depth,
            )

    def _draw_scale_bar(self) -> None:
        if self._dimension_mode == "NONE":
            return
        x0, y0 = 55.0, 735.0
        length = self.scale_px_m
        pen = QPen(QColor("#243b4a"), 2.0)
        self.scene_obj.addLine(x0, y0, x0 + length, y0, pen).setZValue(35)
        self.scene_obj.addLine(x0, y0 - 5, x0, y0 + 5, pen).setZValue(35)
        self.scene_obj.addLine(x0 + length, y0 - 5, x0 + length, y0 + 5, pen).setZValue(35)
        label = self.scene_obj.addSimpleText("1.00 m ölçek çubuğu", QFont("Segoe UI", 7, QFont.Bold))
        label.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        label.setPos(x0 + 55, y0 - 20); label.setZValue(35); label.setBrush(QColor("#243b4a"))

    def _geometry_item_moved(self, key: str, point: QPointF) -> None:
        if self._section is None:
            return
        x_m, depth_m = self._model_xy(point)
        g = self._section.channel_geometry
        if key == "trench_width_m":
            value = max(0.20, 2.0 * abs(x_m - g.center_x_m))
        elif key == "side_slope_h_to_v":
            top_half = max(0.0, abs(x_m - g.center_x_m))
            value = max(0.0, (top_half - max(g.trench_width_m, 0.01) / 2.0) / max(g.trench_depth_m, 0.01))
        elif key == "trench_depth_m":
            value = max(0.30, depth_m)
        elif key == "bedding_thickness_m":
            value = max(0.0, g.trench_depth_m - depth_m)
        elif key == "thermal_backfill_height_m":
            value = max(0.0, g.trench_depth_m - g.bedding_thickness_m - depth_m)
        elif key == "selected_fill_thickness_m":
            value = max(0.0, g.trench_depth_m - g.bedding_thickness_m - g.thermal_backfill_height_m - depth_m)
        elif key in {"duct_bank_width_m", "trough_inner_width_m", "hdd_bore_diameter_m", "tunnel_width_m", "cover_slab_width_m"}:
            value = max(0.05, 2.0 * abs(x_m - g.center_x_m))
        elif key == "duct_bank_height_m":
            value = max(0.10, g.trench_depth_m - g.bedding_thickness_m - depth_m)
        elif key == "trough_inner_height_m":
            value = max(0.20, g.trench_depth_m - depth_m - 2.0 * g.trough_wall_thickness_m)
        elif key == "tunnel_height_m":
            value = max(0.50, g.trench_depth_m - depth_m)
        elif key == "cover_slab_depth_m":
            value = max(0.05, min(depth_m, g.trench_depth_m - 0.05))
        else:
            return
        self.geometryChanged.emit(key, round(value, 5))

    def _content_rect_for_section(self, section: InstallationCrossSectionData) -> QRectF:
        """Return the engineering object envelope, excluding page annotations."""
        points: list[tuple[float, float]] = list(channel_polygon_vertices(section))
        radius = max(self._diameter_m / 2.0, 0.02)
        for cable in section.physical_cables:
            if cable.active:
                points.extend([
                    (float(cable.x_m) - radius, max(0.0, float(cable.depth_m) - radius)),
                    (float(cable.x_m) + radius, float(cable.depth_m) + radius),
                ])
        for slot in section.duct_slots:
            if slot.active:
                r = max(float(slot.outer_diameter_m) / 2.0, 0.02)
                points.extend([(slot.x_m - r, max(0.0, slot.depth_m - r)), (slot.x_m + r, slot.depth_m + r)])
        for source in section.external_heat_sources:
            if source.active:
                r = max(float(source.radius_m), 0.03)
                points.extend([(source.x_m - r, max(0.0, source.depth_m - r)), (source.x_m + r, source.depth_m + r)])
        for region in section.material_regions:
            if region.active:
                points.extend((float(point[0]), max(0.0, float(point[1]))) for point in region.vertices_m)
        if not points:
            points = [(-0.5, 0.0), (0.5, 1.5)]
        xs = [item[0] for item in points]
        ys = [item[1] for item in points]
        span_x = max(max(xs) - min(xs), 0.50)
        margin_x = max(0.35, span_x * 0.16)
        margin_top = 0.22
        margin_bottom = max(0.25, (max(ys) - min(ys)) * 0.12)
        left_m, right_m = min(xs) - margin_x, max(xs) + margin_x
        top_m, bottom_m = max(0.0, min(ys) - margin_top), max(ys) + margin_bottom
        x1, y1 = self._scene_xy(left_m, top_m)
        x2, y2 = self._scene_xy(right_m, bottom_m)
        return QRectF(min(x1, x2), min(y1, y2), max(abs(x2 - x1), 10.0), max(abs(y2 - y1), 10.0))

    def _draw_formation_guides(self, section: InstallationCrossSectionData) -> None:
        groups: dict[tuple[str, int], list[PhysicalCableData]] = {}
        for cable in section.physical_cables:
            if cable.active and str(cable.phase).upper() in {"A", "B", "C"}:
                groups.setdefault((cable.circuit_id, int(cable.parallel_index)), []).append(cable)
        colors = [QColor("#7f2f2f"), QColor("#2e668c"), QColor("#6d5a1a"), QColor("#557346")]
        arrangement_by_circuit: dict[str, str] = {}
        for circuit_id, _parallel_index in groups:
            if circuit_id not in arrangement_by_circuit:
                try:
                    arrangement_by_circuit[circuit_id] = infer_circuit_placement(section, circuit_id).arrangement
                except ValueError:
                    arrangement_by_circuit[circuit_id] = _formation_code(section.arrangement_label)
        if all(value == "DUCT_BANK" for value in arrangement_by_circuit.values()):
            return
        circuit_label_lane: dict[str, int] = {}
        for group_index, ((circuit_id, parallel_index), cables) in enumerate(sorted(groups.items())):
            arrangement = arrangement_by_circuit.get(circuit_id, _formation_code(section.arrangement_label))
            if arrangement == "DUCT_BANK" or len(cables) < 2:
                continue
            by_phase = {str(item.phase).upper(): item for item in cables}
            ordered = [by_phase[key] for key in "ABC" if key in by_phase]
            points = [QPointF(*self._scene_xy(item.x_m, item.depth_m)) for item in ordered]
            color = colors[group_index % len(colors)]
            pen = QPen(color, 1.45, Qt.DashLine)
            pen.setCosmetic(True)
            if arrangement == "TREFOIL" and len(points) == 3:
                guide = self.scene_obj.addPolygon(QPolygonF(points + [points[0]]), pen, QBrush(Qt.NoBrush))
            else:
                if arrangement == "VERTICAL":
                    sorted_points = sorted(points, key=lambda point: (point.y(), point.x()))
                else:
                    sorted_points = sorted(points, key=lambda point: (point.x(), point.y()))
                guide = self.scene_obj.addLine(
                    sorted_points[0].x(), sorted_points[0].y(),
                    sorted_points[-1].x(), sorted_points[-1].y(), pen,
                )
            guide.setZValue(3.5)
            if self._show_detail_labels:
                center_x = sum(item.x_m for item in ordered) / len(ordered)
                top_depth = min(item.depth_m for item in ordered) - self._diameter_m / 2.0
                lane = circuit_label_lane.setdefault(circuit_id, len(circuit_label_lane))
                x, y = self._scene_xy(center_x, max(0.03, top_depth))
                label = self.scene_obj.addSimpleText(
                    f"{circuit_id} · P{parallel_index} · {arrangement}", QFont("Segoe UI", 7, QFont.Bold)
                )
                label.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
                label.setBrush(color); label.setZValue(32)
                label.setPos(x - label.boundingRect().width() / 2.0, y - 26.0 - lane * 15.0)
                leader = self.scene_obj.addLine(x, y - 4.0, x, y - 20.0 - lane * 15.0, QPen(color, 0.9))
                leader.setZValue(31)

    def draw_section(
        self,
        section: InstallationCrossSectionData,
        diameter_m: float,
        *,
        show_material_ids: bool = False,
        material_names: dict[str, str] | None = None,
        temperature_overlay=None,
        dimension_mode: str = "CONSTRUCTION",
        show_result_labels: bool = False,
        show_temperature_contour: bool = False,
        show_object_ids: bool = False,
        show_detail_labels: bool = False,
        layer_colors: dict[str, QColor] | None = None,
        cable_layers=(),
        auto_fit: bool = False,
    ) -> None:
        self._section = section
        installation_kind = str(section.installation_type).upper()
        self._diameter_verified = float(diameter_m) > 0.0
        self._diameter_m = max(0.01, float(diameter_m))
        self._show_material_ids = bool(show_material_ids)
        self._material_names = dict(material_names or {})
        self._temperature_overlay = temperature_overlay
        self._dimension_mode = str(dimension_mode or "CONSTRUCTION").upper()
        self._show_result_labels = bool(show_result_labels)
        self._render_temperature_contour = bool(show_temperature_contour)
        self._show_object_ids = bool(show_object_ids)
        self._show_detail_labels = bool(show_detail_labels)
        self._layer_colors = {
            key: QColor((layer_colors or LAYER_COLORS).get(key, fallback))
            for key, fallback in LAYER_COLORS.items()
        }
        self.scene_obj.clear()
        self._items.clear()
        self._duct_items.clear()
        self._heat_items.clear()
        self._region_items.clear()
        self._region_vertex_items.clear()
        self._contour_items.clear()
        self._geometry_handles.clear()
        width, height = 1240.0, 780.0
        self.scene_obj.setSceneRect(0, 0, width, height)
        self.scene_obj.addRect(0, 0, width, self.surface_y, QPen(Qt.NoPen), QBrush(QColor("#eef7fc")))
        native_soil = self.scene_obj.addRect(
            0, self.surface_y, width, height - self.surface_y,
            QPen(Qt.NoPen), QBrush(self._layer_colors["NATIVE_SOIL"]),
        )
        native_soil.setZValue(-30)
        soil_hatch = self.scene_obj.addRect(
            0, self.surface_y, width, height - self.surface_y,
            QPen(Qt.NoPen), QBrush(QColor(79, 55, 30, 70), Qt.Dense6Pattern),
        )
        soil_hatch.setZValue(-29)
        self.scene_obj.addLine(0, self.surface_y, width, self.surface_y, QPen(QColor("#3f5d50"), 3.2))
        surface = self.scene_obj.addSimpleText("Zemin yüzeyi / y=0", QFont("Segoe UI", 9, QFont.Bold))
        surface.setPos(16, self.surface_y - 27)
        soil_caption = self.scene_obj.addSimpleText("NATIVE SOIL / DOĞAL ZEMİN", QFont("Segoe UI", 8, QFont.Bold))
        soil_caption.setPos(18, self.surface_y + 9)
        soil_caption.setBrush(QColor("#5b4630"))
        soil_caption.setZValue(12)

        # 0.25 m minor / 1 m major grid in physical coordinates.
        for n in range(-8, 9):
            x = self.origin_x + n * 0.25 * self.scale_px_m
            pen = QPen(QColor("#b7aa96"), 0.7 if n % 4 else 1.4, Qt.DotLine if n % 4 else Qt.DashLine)
            self.scene_obj.addLine(x, self.surface_y, x, height, pen)
            if n % 4 == 0:
                label = self.scene_obj.addSimpleText(f"{n/4:+.0f} m", QFont("Segoe UI", 7))
                label.setPos(x + 3, self.surface_y + 3)
        for n in range(1, 12):
            y = self.surface_y + n * 0.25 * self.scale_px_m
            pen = QPen(QColor("#b7aa96"), 0.7 if n % 4 else 1.4, Qt.DotLine if n % 4 else Qt.DashLine)
            self.scene_obj.addLine(0, y, width, y, pen)
            if n % 4 == 0:
                label = self.scene_obj.addSimpleText(f"{n/4:.0f} m", QFont("Segoe UI", 7))
                label.setPos(4, y + 2)

        self._draw_channel_geometry(section)
        self._draw_layer_legend(section)
        self._draw_installation_integrity_overlay(section)

        # User material regions overlay the parametric soil/backfill layers but
        # remain below physical duct, cable and protection objects.
        for region in sorted(section.material_regions, key=lambda item: int(item.priority)):
            if not region.active or len(region.vertices_m) < 3:
                continue
            polygon = self._polygon_scene(region.vertices_m)
            item = _MaterialRegionGraphicsItem(region, polygon, self._material_region_item_moved)
            item.setZValue(-0.80 + min(max(int(region.priority), 0), 999) / 10000.0)
            self.scene_obj.addItem(item)
            self._region_items[region.region_id] = item
            first = region.vertices_m[0]
            x, y = self._scene_xy(float(first[0]), float(first[1]))
            label_text = region.region_id + (f" · {region.material_id}" if self._show_material_ids else "")
            label = QGraphicsSimpleTextItem(label_text, item)
            label.setFont(QFont("Segoe UI", 7, QFont.Bold))
            label.setPos(x + 3, y + 3); label.setZValue(4); label.setBrush(QColor("#334e5c"))
            if self._interactive_editing:
                for vertex_index, point in enumerate(region.vertices_m):
                    handle = _MaterialVertexHandleItem(
                        region.region_id, vertex_index,
                        self._material_vertex_item_moved, self._material_vertex_selected, item,
                    )
                    sx, sy = self._scene_xy(float(point[0]), float(point[1]))
                    handle._updating = True
                    handle.setPos(sx, sy)
                    handle._updating = False
                    self._region_vertex_items[(region.region_id, vertex_index)] = handle

        self._draw_temperature_contour()

        # Pipes/ducts belong only to DUCT_BANK.  Stale slot records from an
        # earlier mode switch must never appear in a directly-buried preview.
        for slot in (section.duct_slots if installation_kind == THERMAL_INSTALL_DUCT_BANK else []):
            if not slot.active:
                continue
            x, y = self._scene_xy(slot.x_m, slot.depth_m)
            outer_diameter = max(slot.outer_diameter_m * self.scale_px_m, 3.0)
            inner_diameter = max(min(slot.inner_diameter_m, slot.outer_diameter_m) * self.scale_px_m, 2.0)
            item = _DuctGraphicsItem(slot, outer_diameter, inner_diameter, self._duct_item_moved)
            item.setPos(x, y)
            item.setZValue(2)
            self.scene_obj.addItem(item)
            self._duct_items[slot.slot_id] = item
            if self._show_object_ids:
                label = self.scene_obj.addSimpleText(slot.slot_id, QFont("Segoe UI", 7))
                label.setPos(x + outer_diameter / 2.0 + 2, y - 8)
                label.setZValue(4)
                label.setBrush(QColor("#435866"))

        for source in section.external_heat_sources:
            if not source.active:
                continue
            x, y = self._scene_xy(source.x_m, source.depth_m)
            radius = max(source.effective_radius_m * self.scale_px_m, 8.0)
            item = _HeatSourceGraphicsItem(source, radius, self._heat_item_moved)
            item.setPos(x, y)
            item.setZValue(3)
            self.scene_obj.addItem(item)
            self._heat_items[source.source_id] = item

        cable_results = {
            item.physical_cable_id: item
            for item in getattr(self._temperature_overlay, "cables", ())
        }
        critical_id = str(getattr(self._temperature_overlay, "critical_nodal_cable_id", "") or "")
        self._draw_formation_guides(section)

        cable_diameter_px = max(self._diameter_m * self.scale_px_m, 3.0)
        cable_layer_specs = self._cable_layer_specs(cable_layers, self._diameter_m, self.scale_px_m)
        for cable in section.physical_cables:
            if not cable.active:
                continue
            item = _CableGraphicsItem(cable, cable_diameter_px, self._item_moved, cable_layer_specs)
            x, y = self._scene_xy(cable.x_m, cable.depth_m)
            item.setPos(x, y)
            item.setZValue(5)
            if cable.physical_cable_id == critical_id:
                item.setPen(QPen(QColor("#b42318"), 3.2))
                item.setBrush(QBrush(QColor("#fff1f0")))
            self.scene_obj.addItem(item)
            self._items[cable.physical_cable_id] = item
            if self._show_object_ids:
                caption = self.scene_obj.addSimpleText(cable.physical_cable_id, QFont("Segoe UI", 7))
                side = -1 if str(cable.phase).upper() == "B" else 1
                caption_x = x + side * (cable_diameter_px / 2 + 5)
                if side < 0:
                    caption_x -= caption.boundingRect().width()
                caption.setPos(caption_x, y - 9)
                caption.setBrush(QColor("#314a5c")); caption.setZValue(7)
            result = cable_results.get(cable.physical_cable_id)
            if self._show_result_labels and result is not None:
                result_text = (
                    f"T={result.nodal_conductor_temperature_c:.1f} °C · "
                    f"q={result.total_loss_w_m:.2f} W/m"
                )
                result_label = self.scene_obj.addSimpleText(result_text, QFont("Segoe UI", 7, QFont.Bold))
                result_label.setPos(x + cable_diameter_px / 2 + 3, y + 8)
                result_label.setBrush(QColor("#8b1e1e") if cable.physical_cable_id == critical_id else QColor("#28556f"))
                result_label.setZValue(8)

        self._draw_electrical_dimensions(section)
        self._draw_scale_bar()

        circuit_formations = []
        for circuit_id in sorted({item.circuit_id for item in section.physical_cables if item.active}):
            try:
                circuit_formations.append((circuit_id, infer_circuit_placement(section, circuit_id).arrangement))
            except ValueError:
                pass
        if circuit_formations and len({item[1] for item in circuit_formations}) == 1:
            formation_text = _formation_display(circuit_formations[0][1])
        elif circuit_formations:
            formation_text = "Karma devre yerleşimi · " + " · ".join(f"{cid}:{form}" for cid, form in circuit_formations)
        else:
            formation_text = _formation_display(section.arrangement_label)
        installation_text = f"{installation_kind} / {INSTALLATION_TYPE_TR.get(installation_kind, 'özel kurulum')}"
        title = self.scene_obj.addSimpleText(
            f"{section.cross_section_id} — {section.name} · {formation_text}",
            QFont("Segoe UI", 10, QFont.Bold),
        )
        title.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        title.setPos(18, 12)
        mode_badge = self.scene_obj.addSimpleText(installation_text, QFont("Segoe UI", 8, QFont.Bold))
        mode_badge.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        mode_badge.setBrush(QColor("#2f6642") if installation_kind == THERMAL_INSTALL_DIRECT_BURIED else QColor("#2f617c"))
        mode_badge.setPos(18, 36)
        mode_badge.setZValue(60)
        note = self.scene_obj.addSimpleText(
            "Ölçekli salt okunur önizleme · genel ve devre-bazlı yerleşimler sağ panelden değiştirilir.",
            QFont("Segoe UI", 8),
        )
        note.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        note.setPos(18, height - 28)
        if not bool(getattr(self, "_diameter_verified", True)):
            warning = self.scene_obj.addSimpleText(
                "UYARI: Kablo dış çapı eksik · ölçekli kablo kesiti doğrulanmadı",
                QFont("Segoe UI", 8, QFont.Bold),
            )
            warning.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
            warning.setBrush(QColor("#b42318")); warning.setZValue(60); warning.setPos(430, 38)
        self._last_content_rect = self._content_rect_for_section(section)
        if auto_fit:
            QTimer.singleShot(0, self.fit_to_section)

    def _material_vertex_selected(self, region_id: str, vertex_index: int) -> None:
        self.materialRegionVertexSelected.emit(region_id, int(vertex_index))

    def _material_vertex_item_moved(self, item: _MaterialVertexHandleItem) -> None:
        parent = item.parentItem()
        if isinstance(parent, _MaterialRegionGraphicsItem):
            polygon = parent.polygon()
            if 0 <= item.vertex_index < polygon.count():
                points = [QPointF(point) for point in polygon]
                points[item.vertex_index] = QPointF(item.pos())
                parent.setPolygon(QPolygonF(points))
        x_m, depth_m = self._model_xy(item.scenePos())
        self.materialRegionVertexMoved.emit(
            item.region_id, item.vertex_index, round(x_m, 6), round(depth_m, 6)
        )

    def _item_moved(self, item: _CableGraphicsItem) -> None:
        x_m, depth_m = self._model_xy(item.pos())
        self.cableMoved.emit(item.cable.physical_cable_id, round(x_m, 8), round(depth_m, 8))

    def _duct_item_moved(self, item: _DuctGraphicsItem) -> None:
        x_m, depth_m = self._model_xy(item.pos())
        self.ductMoved.emit(item.slot.slot_id, round(x_m, 5), round(depth_m, 5))

    def _heat_item_moved(self, item: _HeatSourceGraphicsItem) -> None:
        x_m, depth_m = self._model_xy(item.pos())
        self.heatSourceMoved.emit(item.source.source_id, round(x_m, 5), round(depth_m, 5))

    def _material_region_item_moved(self, item: _MaterialRegionGraphicsItem, dx_scene: float, dy_scene: float) -> None:
        self.materialRegionMoved.emit(
            item.region.region_id,
            round(dx_scene / self.scale_px_m, 6),
            round(dy_scene / self.scale_px_m, 6),
        )


class InstallationDesignerDialog(QDialog):
    def __init__(self, project: ProjectData, on_change=None, parent=None, initial_section_id: str = "") -> None:
        super().__init__(parent)
        self.project = project
        self.on_change = on_change
        self.design = deepcopy(project.installation_design)
        if initial_section_id and any(item.cross_section_id == initial_section_id for item in self.design.cross_sections):
            self.design.active_cross_section_id = initial_section_id
        self._loading = False
        self._selected_material_vertex: tuple[str, int] | None = None
        self._thermal_overlays: dict[str, object] = {}
        self._fit_on_next_draw = True
        self._redraw_pending = False
        self._layout_regeneration_pending = False
        self._installation_type_regeneration_pending = False
        self._settings = QSettings("DiTuS", "KabloAnalizor")
        self._layer_colors = self._load_layer_colors()
        self.setWindowTitle("DiTuS — Kablo-Kanal Düzeni v0.16.9.4.38")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        fit_window(self, DENSITY_FULL)
        self._build_ui()
        self._populate_section_selector()
        self._refresh_all()
        QTimer.singleShot(0, self._initialise_right_panel_layout)

    def _load_layer_colors(self) -> dict[str, QColor]:
        # v2 palette intentionally starts from a contrast-safe set.  Old v1
        # settings could make every construction layer nearly the same brown,
        # hiding bedding/backfill boundaries on the canvas.
        palette_version = int(self._settings.value("ui/kablo_kanal/layer_palette_version", 0) or 0)
        colors: dict[str, QColor] = {}
        for key, default in LAYER_COLORS.items():
            settings_key = f"ui/kablo_kanal/layer_color_v2/{key}"
            stored = str(self._settings.value(settings_key, default.name())) if palette_version >= 2 else default.name()
            color = QColor(stored)
            colors[key] = color if color.isValid() else QColor(default)
        if palette_version < 2:
            self._settings.setValue("ui/kablo_kanal/layer_palette_version", 2)
            for key, color in colors.items():
                self._settings.setValue(f"ui/kablo_kanal/layer_color_v2/{key}", color.name())
        return colors

    @staticmethod
    def _layer_settings_key(role: str) -> str:
        return f"ui/kablo_kanal/layer_color_v2/{role}"

    def _store_layer_colors(self) -> None:
        for key, color in self._layer_colors.items():
            self._settings.setValue(self._layer_settings_key(key), QColor(color).name())

    def _choose_layer_color(self, role: str) -> None:
        current = QColor(self._layer_colors.get(role, LAYER_COLORS.get(role, QColor("#cccccc"))))
        color = QColorDialog.getColor(current, self, f"{LAYER_ROLE_LABELS.get(role, role)} rengi")
        if not color.isValid():
            return
        self._layer_colors[role] = color
        self._store_layer_colors()
        self._refresh_layer_summary(self._section())
        self._draw_canvas()

    def _reset_layer_colors(self) -> None:
        self._layer_colors = {key: QColor(value) for key, value in LAYER_COLORS.items()}
        self._store_layer_colors()
        self._refresh_layer_summary(self._section())
        self._draw_canvas()

    @staticmethod
    def _set_swatch(button: QPushButton, color: QColor) -> None:
        button.setStyleSheet(
            f"QPushButton {{ background:{QColor(color).name()}; border:1px solid #6c7277; min-width:28px; max-width:28px; }}"
        )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        header = QLabel(
            "Her section için hendek/kanal, bedding sand, backfill, doğal zemin, duct ve fiziksel kablolar birlikte tanımlanır. "
            "Çizim salt okunur ve ölçeklidir; bütün değişiklikler sağ panelden yapılır."
        )
        header.setWordWrap(True)
        header.setStyleSheet(
            "font-size:11pt; font-weight:700; color:#173d5d; padding:8px; "
            "background:#edf4f8; border:1px solid #c7d5df; border-radius:5px;"
        )
        root.addWidget(header)

        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("Kablo-kanal düzeni:"))
        self.section_selector = QComboBox()
        self.section_selector.currentIndexChanged.connect(self._section_selected)
        selector_row.addWidget(self.section_selector, 1)
        for text, handler in (
            ("Yeni", self._add_section),
            ("Kopyala", self._duplicate_section),
            ("Sil", self._delete_section),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            selector_row.addWidget(button)
        root.addLayout(selector_row)

        template_row = QHBoxLayout()
        template_row.addWidget(QLabel("Section şablonu:"))
        self.section_template_combo = QComboBox()
        self.section_template_combo.addItem("— Şablon seçin (seçim tek başına uygulanmaz) —", "")
        for template in built_in_cable_channel_templates():
            self.section_template_combo.addItem(
                f"{template.template_id} — {template.name}", template.template_id
            )
            self.section_template_combo.setItemData(
                self.section_template_combo.count() - 1, template.description, Qt.ToolTipRole
            )
        template_row.addWidget(self.section_template_combo, 1)
        apply_template = QPushButton("Şablonu Seçili Section'a Uygula")
        apply_template.clicked.connect(self._apply_section_template)
        apply_template.setToolTip(
            "Devre ve fiziksel kablo kimliklerini korur; kanal ölçülerini, duct slotlarını ve kablo koordinatlarını yeniden düzenler."
        )
        template_row.addWidget(apply_template)
        template_note = QLabel(
            "<i>Şablon seçimi tek başına geometriyi değiştirmez; uygulama düğmesiyle section'a yazılır. "
            "Şablonlar proje doğrulaması ve saha ölçüsü yerine geçmez.</i>"
        )
        template_note.setWordWrap(True)
        template_note.setStyleSheet("font-size:8pt; color:#60727f; padding-left:4px;")
        root.addLayout(template_row)
        root.addWidget(template_note)

        view_toolbar = QGroupBox("Görünüm ve gölge sonuçlar")
        view_grid = QGridLayout(view_toolbar)
        view_grid.setContentsMargins(7, 5, 7, 5)
        view_grid.setHorizontalSpacing(7)
        view_grid.setVerticalSpacing(4)
        self.material_id_check = QCheckBox("Malzeme ID")
        self.material_id_check.setToolTip("Katmanlarda kullanılan malzeme kimliklerini gösterir.")
        self.material_id_check.toggled.connect(lambda _checked: self._draw_canvas())
        self.object_id_check = QCheckBox("Nesne ID")
        self.object_id_check.setChecked(False)
        self.object_id_check.setToolTip("Tam fiziksel kablo ve duct ID'lerini gösterir.")
        self.object_id_check.toggled.connect(lambda _checked: self._draw_canvas())
        self.detail_labels_check = QCheckBox("Detay yazıları")
        self.detail_labels_check.setChecked(False)
        self.detail_labels_check.setToolTip("Katman lejandı, katman içi yazılar ve grup etiketlerini açar.")
        self.detail_labels_check.toggled.connect(lambda _checked: self._draw_canvas())
        self.contour_region_combo = QComboBox()
        self.contour_region_combo.setMinimumWidth(170)
        self.contour_region_combo.currentIndexChanged.connect(lambda _index: self._draw_canvas())
        self.contour_check = QCheckBox("2D kontur")
        self.contour_check.setEnabled(False)
        self.contour_check.toggled.connect(lambda _checked: self._draw_canvas())
        self.result_label_check = QCheckBox("Kablo T/q")
        self.result_label_check.setToolTip("Kablo sıcaklığı ve W/m kayıp etiketlerini gösterir.")
        self.result_label_check.setEnabled(False)
        self.result_label_check.toggled.connect(lambda _checked: self._draw_canvas())
        self.contour_status_label = QLabel("<i>Kontur yok · yalnız isteğe bağlı gölge 2D önizleme</i>")
        self.contour_status_label.setWordWrap(True)
        self.contour_status_label.setStyleSheet("font-size:8pt; color:#60727f; padding:2px 4px;")
        self.contour_status_label.setToolTip(
            "Kontur, üretim IEC sonucu değildir. SHADOW_COMPARE fiziksel termal motoru seçili section için ayrıca çalıştırır."
        )
        self.dimension_mode_combo = QComboBox()
        self.dimension_mode_combo.addItem("İnşaat ölçüleri", "CONSTRUCTION")
        self.dimension_mode_combo.addItem("Elektriksel ölçüler", "ELECTRICAL")
        self.dimension_mode_combo.addItem("Tüm ölçüler", "ALL")
        self.dimension_mode_combo.addItem("Ölçüleri gizle", "NONE")
        self.dimension_mode_combo.currentIndexChanged.connect(lambda _index: self._draw_canvas())
        fit_section_button = QPushButton("Kesite Sığdır")
        fit_section_button.clicked.connect(lambda _checked=False: self.canvas.fit_to_section())
        fit_section_button.setToolTip("Yalnız hendek/kanal ve fiziksel nesne zarfına yakınlaşır.")
        zoom_reset_button = QPushButton("1:1")
        zoom_reset_button.clicked.connect(lambda _checked=False: self.canvas.zoom_reset())
        zoom_reset_button.setToolTip("Görünüm dönüşümünü 1:1 sıfırlar; model verisini değiştirmez.")
        run_contour = QPushButton("Konturu Hesapla")
        run_contour.clicked.connect(self._run_temperature_contour)
        export_button = QPushButton("Kesit Çıktısı…")
        export_button.clicked.connect(self._export_engineering_section)
        preview_mode_label = QLabel("<b>Salt okunur ölçekli önizleme</b>")
        preview_mode_label.setToolTip("Çizim tıklama veya sürüklemeyle değişmez; tüm girdiler sağ paneldedir.")
        preview_mode_label.setStyleSheet("color:#24536e; padding:3px 7px; background:#e8f2f7; border:1px solid #bfd3df;")
        geometry_report = QPushButton("Çakışma Raporu…")
        geometry_report.clicked.connect(self._show_geometry_report)

        view_grid.addWidget(preview_mode_label, 0, 0)
        view_grid.addWidget(self.material_id_check, 0, 1)
        view_grid.addWidget(self.object_id_check, 0, 2)
        view_grid.addWidget(self.detail_labels_check, 0, 3)
        view_grid.addWidget(fit_section_button, 0, 4)
        view_grid.addWidget(zoom_reset_button, 0, 5)
        view_grid.addWidget(QLabel("Ölçülendirme:"), 0, 6)
        view_grid.addWidget(self.dimension_mode_combo, 0, 7)
        view_grid.setColumnStretch(8, 1)

        view_grid.addWidget(QLabel("Kontur bölgesi:"), 1, 0)
        view_grid.addWidget(self.contour_region_combo, 1, 1, 1, 2)
        view_grid.addWidget(self.contour_check, 1, 3)
        view_grid.addWidget(self.result_label_check, 1, 4)
        view_grid.addWidget(run_contour, 1, 5)
        view_grid.addWidget(geometry_report, 1, 6)
        view_grid.addWidget(export_button, 1, 7)
        view_grid.addWidget(self.contour_status_label, 2, 0, 1, 9)
        root.addWidget(view_toolbar)

        splitter = QSplitter(Qt.Horizontal)
        self.canvas = InstallationCanvas()
        self.canvas.cableMoved.connect(self._cable_moved)
        self.canvas.ductMoved.connect(self._duct_moved)
        self.canvas.heatSourceMoved.connect(self._heat_source_moved)
        self.canvas.materialRegionMoved.connect(self._material_region_moved)
        self.canvas.materialRegionVertexMoved.connect(self._material_region_vertex_moved)
        self.canvas.materialRegionVertexSelected.connect(self._material_region_vertex_selected)
        self.canvas.geometryChanged.connect(self._geometry_handle_changed)
        splitter.addWidget(self.canvas)

        right = QWidget()
        right.setMinimumWidth(360)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._right_panel_toggle_guard = False

        self.upper_panel_toggle = QPushButton("▾ Kesit ve Katman Ayarları")
        self.upper_panel_toggle.setCheckable(True)
        self.upper_panel_toggle.setChecked(True)
        self.upper_panel_toggle.setToolTip(
            "Kesit kimliği, genel formasyon, hendek geometrisi ve malzeme katmanlarını açar."
        )
        self.upper_panel_toggle.toggled.connect(
            lambda checked: self._right_panel_toggled("upper", checked)
        )
        right_layout.addWidget(self.upper_panel_toggle)

        parameter_panel = QWidget()
        parameter_layout = QVBoxLayout(parameter_panel)
        parameter_layout.setContentsMargins(0, 0, 0, 0)
        parameter_layout.addWidget(self._build_section_form())
        parameter_layout.addWidget(self._build_preset_group())
        parameter_layout.addWidget(self._build_layer_summary_group())
        parameter_layout.addWidget(self._build_geometry_group())
        parameter_layout.addStretch(1)
        parameter_scroll = QScrollArea()
        parameter_scroll.setWidgetResizable(True)
        parameter_scroll.setWidget(parameter_panel)
        parameter_scroll.setMinimumHeight(140)
        self.parameter_scroll = parameter_scroll
        right_layout.addWidget(parameter_scroll, 1)

        lower_header = QHBoxLayout()
        self.lower_panel_toggle = QPushButton("▸ Devre / Kablo / Duct Yerleşimi")
        self.lower_panel_toggle.setCheckable(True)
        self.lower_panel_toggle.setChecked(False)
        self.lower_panel_toggle.setToolTip(
            "Devre Yerleşimi, fiziksel kablolar, duct slotları, malzeme bölgeleri ve ısı kaynaklarını açar."
        )
        self.lower_panel_toggle.toggled.connect(
            lambda checked: self._right_panel_toggled("lower", checked)
        )
        lower_header.addWidget(self.lower_panel_toggle)
        self.circuit_spacing_summary_label = QLabel("Hat aralıkları: —")
        self.circuit_spacing_summary_label.setStyleSheet("font-size:8pt; color:#365f48; padding-left:6px;")
        self.circuit_spacing_summary_label.setToolTip(
            "Devre Yerleşimi tablosundaki X merkezlerinden hesaplanan komşu hat merkez mesafeleri."
        )
        lower_header.addWidget(self.circuit_spacing_summary_label, 1)
        right_layout.addLayout(lower_header)

        lower_panel = QWidget()
        lower_panel.setMinimumHeight(180)
        self.lower_panel = lower_panel
        lower_layout = QVBoxLayout(lower_panel)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(5)
        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(150)
        self.tabs.setCurrentIndex(0)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabBar().setElideMode(Qt.ElideRight)
        self.circuit_table = self._make_table([
            "Devre ID", "Ad", "Faz sırası", "Devre akımı RMS [A]", "Legacy yük katsayısı", "Etkin",
            "Bağımsız formasyon", "X merkezi [m]", "Referans derinliği [m]",
            "Faz merkez aralığı [m] (TREFOIL: Ø kilitli)", "Paralel grup aralığı [m]",
        ])
        self.cable_table = self._make_table([
            "Fiziksel kablo ID", "Devre", "Faz", "Paralel", "x [m]", "Derinlik [m]",
            "Duct slot", "Akım override RMS [A]", "Açı override [°]", "Legacy yük katsayısı", "Etkin"
        ])
        self.duct_table = self._make_table([
            "Slot ID", "x [m]", "Derinlik [m]", "İç çap [m]", "Dış çap [m]", "Satır", "Sütun", "Etkin"
        ])
        self.material_region_table = self._make_table([
            "Bölge ID", "Ad", "Malzeme ID", "Köşeler x,derinlik [m]", "Öncelik", "Etkin"
        ])
        self.heat_table = self._make_table([
            "Kaynak ID", "Ad", "x [m]", "Derinlik [m]", "Isı [W/m]", "Yarıçap [m]", "Etkin"
        ])
        for table in (self.circuit_table, self.cable_table, self.duct_table, self.material_region_table, self.heat_table):
            table.cellChanged.connect(self._table_changed)
        tab_specs = (
            (self.circuit_table, "Devre Yerleşimi", "Devre yükleri ve devre-bazlı bağımsız formasyon/x-derinlik ayarları"),
            (self.cable_table, "Kablolar", "Fiziksel kablolar; devre/faz/paralel ve x-y atamaları"),
            (self.duct_table, "Duct", "Boru/duct slotları ve çapları"),
            (self.material_region_table, "Malzeme", "Malzeme Bölgeleri — özel termal polygonlar"),
            (self.heat_table, "Isı Kaynakları", "Harici kablo veya sıcak boru kaynakları"),
        )
        for table, title, tooltip in tab_specs:
            index = self.tabs.addTab(self._table_panel(table), title)
            self.tabs.setTabToolTip(index, tooltip)
        lower_layout.addWidget(self.tabs, 1)

        self.validation_label = QLabel()
        self.validation_label.setWordWrap(True)
        self.validation_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.validation_label.setStyleSheet("padding:5px; background:#f7fafb; border:1px solid #d5e0e6;")
        validation_scroll = QScrollArea()
        validation_scroll.setWidgetResizable(True)
        validation_scroll.setMaximumHeight(92)
        validation_scroll.setMinimumHeight(64)
        validation_scroll.setWidget(self.validation_label)
        lower_layout.addWidget(validation_scroll)
        self.lower_panel.setVisible(False)
        right_layout.addWidget(self.lower_panel, 1)
        splitter.addWidget(right)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([900, 640])
        root.addWidget(splitter, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Projeye Kaydet")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _initialise_right_panel_layout(self) -> None:
        section = str(self._settings.value("ui/kablo_kanal/right_panel_section", "upper") or "upper")
        if section not in {"upper", "lower"}:
            section = "upper"
        self._open_right_panel_section(section)
        if hasattr(self, "tabs"):
            self.tabs.setCurrentIndex(0)
        self._update_circuit_spacing_summary()

    def _set_panel_toggle_texts(self) -> None:
        if hasattr(self, "upper_panel_toggle"):
            prefix = "▾" if self.upper_panel_toggle.isChecked() else "▸"
            self.upper_panel_toggle.setText(f"{prefix} Kesit ve Katman Ayarları")
        if hasattr(self, "lower_panel_toggle"):
            prefix = "▾" if self.lower_panel_toggle.isChecked() else "▸"
            self.lower_panel_toggle.setText(f"{prefix} Devre / Kablo / Duct Yerleşimi")

    def _open_right_panel_section(self, section: str) -> None:
        if not hasattr(self, "parameter_scroll") or not hasattr(self, "lower_panel"):
            return
        target = "lower" if section == "lower" else "upper"
        self._right_panel_toggle_guard = True
        try:
            self.upper_panel_toggle.setChecked(target == "upper")
            self.lower_panel_toggle.setChecked(target == "lower")
            self.parameter_scroll.setVisible(target == "upper")
            self.lower_panel.setVisible(target == "lower")
            self._settings.setValue("ui/kablo_kanal/right_panel_section", target)
            self._set_panel_toggle_texts()
        finally:
            self._right_panel_toggle_guard = False

    def _right_panel_toggled(self, section: str, checked: bool) -> None:
        if self._right_panel_toggle_guard:
            return
        # One accordion section is always open; clicking the other header moves
        # the whole available height to that section without splitter overlap.
        if checked:
            self._open_right_panel_section(section)
            return
        # Keep one section open. Clicking the already-open header is inert;
        # clicking the other header performs the switch.
        self._open_right_panel_section(section)

    def _show_circuit_placement_panel(self) -> None:
        self._open_right_panel_section("lower")
        if hasattr(self, "tabs"):
            self.tabs.setCurrentIndex(0)
        if hasattr(self, "circuit_table"):
            self.circuit_table.setFocus(Qt.OtherFocusReason)

    def _update_circuit_spacing_summary(self) -> None:
        if not hasattr(self, "circuit_table"):
            return
        parts: list[str] = []
        gaps: list[str] = []
        for row in range(1, self.circuit_table.rowCount()):
            left_id = self._text(self.circuit_table, row - 1, 0) or f"C{row}"
            right_id = self._text(self.circuit_table, row, 0) or f"C{row + 1}"
            left_x = self._float(self._text(self.circuit_table, row - 1, 7))
            right_x = self._float(self._text(self.circuit_table, row, 7))
            gap = abs(right_x - left_x)
            parts.append(f"{left_id}↔{right_id}={gap:.3f} m")
            gaps.append(f"{gap:.3f}")
        text = "Hat aralıkları: " + (" · ".join(parts) if parts else "tek devre")
        if hasattr(self, "circuit_spacing_summary_label"):
            self.circuit_spacing_summary_label.setText(text)
        if hasattr(self, "circuit_gap_edit") and not self.circuit_gap_edit.hasFocus():
            self.circuit_gap_edit.setText("; ".join(value.replace(".", ",") for value in gaps))

    def _apply_neighbor_circuit_gaps(self) -> None:
        rows = self.circuit_table.rowCount() if hasattr(self, "circuit_table") else 0
        if rows < 2:
            QMessageBox.information(self, "Hat aralıkları", "Bağımsız hat aralığı için en az iki devre gerekir.")
            return
        gap_text = self.circuit_gap_edit.text().strip()
        if ";" in gap_text:
            values = [value.strip() for value in gap_text.split(";") if value.strip()]
        else:
            values = [value.strip() for value in gap_text.split(",") if value.strip()]
        if len(values) != rows - 1:
            QMessageBox.warning(
                self, "Hat aralıkları",
                f"Tablo sırasındaki {rows} devre için {rows - 1} komşu merkez aralığı girilmelidir. "
                "Ondalık virgül kullanıyorsanız değerleri noktalı virgülle ayırın. Örnek: 0,80; 0,70; 1,00",
            )
            return
        try:
            gaps = [float(value.replace(",", ".")) for value in values]
        except ValueError:
            QMessageBox.warning(self, "Hat aralıkları", "Aralıklar sayısal olmalıdır.")
            return
        if any(value <= 0.0 for value in gaps):
            QMessageBox.warning(self, "Hat aralıkları", "Bütün hat merkez aralıkları sıfırdan büyük olmalıdır.")
            return
        current_x = self._float(self._text(self.circuit_table, 0, 7))
        for row, gap in enumerate(gaps, start=1):
            current_x += gap
            self.circuit_table.setItem(row, 7, self._item(f"{current_x:.5f}"))
        self.circuit_table.clearSelection()
        self._apply_circuit_placements()

    def _build_section_form(self) -> QGroupBox:
        box = QGroupBox("Kablo-kanal düzeni kimliği ve bölge bağı")
        form = QGridLayout(box)
        self.section_id_edit = QLineEdit()
        self.section_name_edit = QLineEdit()
        self.section_type_combo = QComboBox()
        self.section_type_combo.addItems([
            THERMAL_INSTALL_DIRECT_BURIED,
            THERMAL_INSTALL_DUCT_BANK,
            THERMAL_INSTALL_HDD,
            THERMAL_INSTALL_CONCRETE_TROUGH,
            THERMAL_INSTALL_TUNNEL,
        ])
        self.section_type_caption = QLabel()
        self.section_type_caption.setStyleSheet("font-size:8pt; font-style:italic; color:#60727f;")
        self.section_type_caption.setWordWrap(True)
        installation_type_widget = QWidget()
        installation_type_layout = QVBoxLayout(installation_type_widget)
        installation_type_layout.setContentsMargins(0, 0, 0, 0)
        installation_type_layout.setSpacing(2)
        installation_type_layout.addWidget(self.section_type_combo)
        installation_type_layout.addWidget(self.section_type_caption)
        self.arrangement_edit = QLineEdit()
        self.arrangement_edit.setReadOnly(True)
        self.arrangement_edit.setToolTip("Yerleşim yalnız aşağıdaki Kablo Yerleşimi panelinden değiştirilir.")
        self.region_ids_edit = QLineEdit()
        self.section_source_edit = QLineEdit()
        fields = [
            ("Kesit ID", self.section_id_edit),
            ("Ad", self.section_name_edit),
            ("Kurulum", installation_type_widget),
            ("Yerleşim", self.arrangement_edit),
            ("Termal bölge ID'leri", self.region_ids_edit),
            ("Kaynak referansı", self.section_source_edit),
        ]
        for row, (label, widget) in enumerate(fields):
            form.addWidget(QLabel(label), row, 0)
            form.addWidget(widget, row, 1)
        form.setColumnStretch(1, 1)
        for widget in (
            self.section_id_edit, self.section_name_edit,
            self.region_ids_edit, self.section_source_edit,
        ):
            widget.editingFinished.connect(self._section_form_changed)
        self.section_type_combo.currentTextChanged.connect(self._update_installation_type_caption)
        self.section_type_combo.currentTextChanged.connect(self._section_form_changed)
        self._update_installation_type_caption(self.section_type_combo.currentText())
        return box

    def _update_installation_type_caption(self, installation_type: str) -> None:
        caption = INSTALLATION_TYPE_TR.get(installation_type, "özel kurulum")
        self.section_type_caption.setText(f"<i>{caption}</i>")

    def _build_layer_summary_group(self) -> QGroupBox:
        box = QGroupBox("Hendek katmanları — üstten alta")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(2, 1)
        for column, text in enumerate(("Renk", "Katman / işlev", "Kalınlık · malzeme")):
            header = QLabel(f"<b>{text}</b>")
            header.setStyleSheet("color:#304b5a; font-size:8pt;")
            grid.addWidget(header, 0, column)
        self.layer_summary_labels: dict[str, QLabel] = {}
        self.layer_color_buttons: dict[str, QPushButton] = {}
        roles = (
            "NATIVE_SOIL", "GENERAL_BACKFILL", "SELECTED_BACKFILL",
            "THERMAL_BACKFILL", "BEDDING_SAND", "DUCT_BANK",
        )
        for index, role in enumerate(roles, start=1):
            button = QPushButton("")
            button.setFixedSize(34, 23)
            button.setToolTip(f"{LAYER_ROLE_LABELS[role]} çizim rengini seç")
            button.clicked.connect(lambda _checked=False, key=role: self._choose_layer_color(key))
            self.layer_color_buttons[role] = button
            grid.addWidget(button, index, 0)
            role_label = QLabel(LAYER_ROLE_LABELS[role])
            role_label.setWordWrap(True)
            grid.addWidget(role_label, index, 1)
            value = QLabel("—")
            value.setWordWrap(True)
            value.setStyleSheet("color:#435b68; font-size:8pt;")
            self.layer_summary_labels[role] = value
            grid.addWidget(value, index, 2)
        reset = QPushButton("Katman renklerini sıfırla")
        reset.clicked.connect(self._reset_layer_colors)
        grid.addWidget(reset, len(roles) + 1, 0, 1, 3)
        note = QLabel(
            "Renk ve taramalar yalnız çizim içindir. Termal hesapta satırda gösterilen malzeme ID'sinin sayısal özellikleri kullanılır."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size:8pt; color:#60727f; font-style:italic;")
        grid.addWidget(note, len(roles) + 2, 0, 1, 3)
        return box

    def _refresh_layer_summary(self, section: InstallationCrossSectionData | None) -> None:
        for role, button in getattr(self, "layer_color_buttons", {}).items():
            self._set_swatch(button, self._layer_colors.get(role, LAYER_COLORS[role]))
        if section is None or not hasattr(self, "layer_summary_labels"):
            return
        g = section.channel_geometry
        material_map = {item.material_id: item.name for item in self.project.thermal_design.materials}
        is_duct = str(section.installation_type).upper() == THERMAL_INSTALL_DUCT_BANK
        if is_duct:
            general = max(
                0.0,
                float(g.trench_depth_m)
                - float(g.surface_layer_thickness_m)
                - float(g.duct_bank_height_m)
                - float(g.bedding_thickness_m),
            )
        else:
            general = max(
                0.0,
                float(g.trench_depth_m)
                - float(g.surface_layer_thickness_m)
                - float(g.selected_fill_thickness_m)
                - float(g.thermal_backfill_height_m)
                - float(g.bedding_thickness_m),
            )
        values = {
            "NATIVE_SOIL": f"dış ortam · {g.native_soil_material_id} · {material_map.get(g.native_soil_material_id, 'tanımsız')}",
            "GENERAL_BACKFILL": f"{general:.3f} m · {g.general_fill_material_id} · {material_map.get(g.general_fill_material_id, 'tanımsız')}",
            "SELECTED_BACKFILL": (
                "bu kurulumda uygulanmıyor" if is_duct
                else f"{g.selected_fill_thickness_m:.3f} m · {g.selected_fill_material_id} · {material_map.get(g.selected_fill_material_id, 'tanımsız')}"
            ),
            "THERMAL_BACKFILL": (
                "bu kurulumda uygulanmıyor" if is_duct
                else f"{g.thermal_backfill_height_m:.3f} m · {g.thermal_backfill_material_id} · {material_map.get(g.thermal_backfill_material_id, 'tanımsız')}"
            ),
            "BEDDING_SAND": (
                f"zarf {g.bedding_thickness_m:.3f} m · alt {g.bedding_bottom_cover_m:.3f} m · "
                f"üst {g.bedding_top_cover_m:.3f} m · yan {g.bedding_side_clearance_m:.3f} m · "
                f"{g.bedding_material_id} · {material_map.get(g.bedding_material_id, 'tanımsız')}"
            ),
            "DUCT_BANK": f"{g.duct_bank_width_m:.3f} × {g.duct_bank_height_m:.3f} m · {g.duct_bank_material_id} · {material_map.get(g.duct_bank_material_id, 'tanımsız')}",
        }
        for role, label in self.layer_summary_labels.items():
            label.setText(values[role])
            relevant = role not in {"SELECTED_BACKFILL", "THERMAL_BACKFILL", "DUCT_BANK"} or (
                role == "DUCT_BANK" and is_duct
            ) or (role in {"SELECTED_BACKFILL", "THERMAL_BACKFILL"} and not is_duct)
            label.setEnabled(relevant)
            self.layer_color_buttons[role].setEnabled(relevant)

    def _build_geometry_group(self) -> QGroupBox:
        box = QGroupBox("Kesit geometrisi ve termal katmanlar")
        self.geometry_box = box
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)

        self.installation_scope_banner = QLabel()
        self.installation_scope_banner.setWordWrap(True)
        self.installation_scope_banner.setMinimumHeight(34)
        grid.addWidget(self.installation_scope_banner, 0, 0, 1, 4)

        self.geometry_spins: dict[str, QDoubleSpinBox] = {}
        self.geometry_labels: dict[str, QLabel] = {}
        specs = [
            ("center_x_m", "Merkez x [m]", -20.0, 20.0, 0.0, 0.05),
            ("trench_width_m", "Hendek alt genişliği [m]", 0.20, 20.0, 0.80, 0.05),
            ("trench_depth_m", "Toplam kazı derinliği [m]", 0.30, 30.0, 1.50, 0.05),
            ("side_slope_h_to_v", "Yan eğim H:V", 0.0, 5.0, 0.0, 0.05),
            ("bedding_thickness_m", "Yatak kumu zarfı toplamı [m] (hesaplanan)", 0.0, 5.0, 0.15, 0.01),
            ("bedding_bottom_cover_m", "Kablo altı yatak kumu [m]", 0.0, 2.0, 0.10, 0.01),
            ("bedding_top_cover_m", "En üst kablo üstü kum [m]", 0.0, 2.0, 0.10, 0.01),
            ("bedding_side_clearance_m", "Kablo grubu yan kum payı [m]", 0.0, 5.0, 0.10, 0.01),
            ("thermal_backfill_height_m", "Termal dolgu üst yüksekliği [m]", 0.0, 10.0, 0.30, 0.05),
            ("selected_fill_thickness_m", "Seçilmiş backfill yüksekliği [m]", 0.0, 10.0, 0.30, 0.05),
            ("warning_mesh_offset_above_bedding_m", "Uyarı ağı · kum üstünden [m]", 0.0, 5.0, 0.20, 0.05),
            ("warning_tape_offset_above_bedding_m", "Uyarı bandı · kum üstünden [m]", 0.0, 5.0, 0.30, 0.05),
            ("spacer_width_m", "Spacer/bims genişliği [m]", 0.01, 1.0, 0.08, 0.01),
            ("spacer_height_m", "Spacer/bims yüksekliği [m]", 0.01, 1.0, 0.06, 0.01),
            ("surface_layer_thickness_m", "Yüzey tabakası [m]", 0.0, 5.0, 0.0, 0.02),
            ("cover_slab_width_m", "Koruma plakası genişliği [m]", 0.05, 20.0, 0.65, 0.05),
            ("cover_slab_thickness_m", "Plaka kalınlığı [m]", 0.01, 2.0, 0.05, 0.01),
            ("cover_slab_depth_m", "Plaka merkez derinliği [m]", 0.05, 30.0, 0.55, 0.05),
            ("duct_bank_width_m", "Duct bank blok genişliği [m]", 0.10, 20.0, 0.90, 0.05),
            ("duct_bank_height_m", "Duct bank blok yüksekliği [m]", 0.10, 20.0, 0.55, 0.05),
            ("trough_inner_width_m", "Kanal iç genişliği [m]", 0.20, 20.0, 0.75, 0.05),
            ("trough_inner_height_m", "Kanal iç yüksekliği [m]", 0.20, 20.0, 0.75, 0.05),
            ("trough_wall_thickness_m", "Kanal duvarı [m]", 0.02, 2.0, 0.10, 0.01),
            ("hdd_bore_diameter_m", "HDD delgi çapı [m]", 0.10, 10.0, 0.45, 0.05),
            ("tunnel_width_m", "Tünel genişliği [m]", 0.50, 20.0, 2.0, 0.10),
            ("tunnel_height_m", "Tünel yüksekliği [m]", 0.50, 20.0, 2.0, 0.10),
        ]
        for index, (key, label, minimum, maximum, value, step) in enumerate(specs):
            spin = self._double_spin(minimum, maximum, value, step)
            spin.valueChanged.connect(lambda _value, k=key: self._geometry_form_changed(k))
            self.geometry_spins[key] = spin
            label_widget = QLabel(label)
            label_widget.setWordWrap(True)
            self.geometry_labels[key] = label_widget
            row, pair = divmod(index, 2)
            row += 1
            grid.addWidget(label_widget, row, pair * 2)
            grid.addWidget(spin, row, pair * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.geometry_spins["bedding_thickness_m"].setReadOnly(True)
        self.geometry_spins["bedding_thickness_m"].setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.geometry_spins["bedding_thickness_m"].setToolTip(
            "Gerçek kablo dış çapı, formasyon ve alt/üst kum örtülerinden otomatik hesaplanır."
        )
        self.geometry_spins["trench_width_m"].setToolTip(
            "Doğrudan gömülü kurulumda kullanıcı tarafından girilen hendek alt genişliğidir. "
            "Kablo ve yan kum zarfından küçük bir değer girilirse yalnız fiziksel minimuma yükseltilir."
        )
        self.geometry_spins["duct_bank_width_m"].setToolTip(
            "Yalnız DUCT_BANK kurulumunda boru/grout blok genişliğidir; kazı alt genişliği değildir."
        )

        geometry_end_row = 1 + (len(specs) + 1) // 2
        self.trench_width_status_label = QLabel()
        self.trench_width_status_label.setWordWrap(True)
        self.trench_width_status_label.setStyleSheet("font-size:8pt; color:#365665; font-weight:700;")
        self.apply_minimum_trench_width_button = QPushButton("Fiziksel minimum genişliği uygula")
        self.apply_minimum_trench_width_button.clicked.connect(self._apply_required_trench_width)
        grid.addWidget(self.trench_width_status_label, geometry_end_row, 0, 1, 3)
        grid.addWidget(self.apply_minimum_trench_width_button, geometry_end_row, 3)
        geometry_end_row += 1

        self.geometry_checks: dict[str, QCheckBox] = {}
        check_specs = (
            ("cable_group_bottom_locked", "Kablo grubunu hendek tabanına ve alt kum örtüsüne kilitle"),
            ("warning_mesh_enabled", "Uyarı ağı etkin"),
            ("warning_tape_enabled", "Uyarı bandı etkin"),
            ("spacer_enabled", "Düz formasyonda spacer/bims göster"),
            ("cover_slab_enabled", "Koruma plakası etkin"),
        )
        for offset, (key, text) in enumerate(check_specs):
            check = QCheckBox(text)
            check.toggled.connect(lambda _checked, k=key: self._geometry_form_changed(k))
            self.geometry_checks[key] = check
            grid.addWidget(check, geometry_end_row + offset // 2, (offset % 2) * 2, 1, 2)
        self.cover_slab_check = self.geometry_checks["cover_slab_enabled"]
        geometry_end_row += (len(check_specs) + 1) // 2

        self.material_combos: dict[str, QComboBox] = {}
        self.material_labels: dict[str, QLabel] = {}
        material_specs = [
            ("native_soil_material_id", "Native soil / doğal zemin"),
            ("bedding_material_id", "Bedding sand / yatak kumu"),
            ("thermal_backfill_material_id", "Thermal backfill / kablo çevresi"),
            ("selected_fill_material_id", "Backfill / seçilmiş dolgu"),
            ("general_fill_material_id", "General backfill / üst dolgu"),
            ("surface_material_id", "Yüzey"),
            ("cover_slab_material_id", "Koruma plakası"),
            ("duct_bank_material_id", "Duct bank / grout"),
            ("trough_material_id", "Beton kanal"),
            ("hdd_grout_material_id", "HDD grout"),
        ]
        start_row = geometry_end_row + 1
        for index, (key, label) in enumerate(material_specs):
            combo = QComboBox()
            combo.currentIndexChanged.connect(lambda _index, k=key: self._geometry_material_changed(k))
            self.material_combos[key] = combo
            label_widget = QLabel(label)
            label_widget.setWordWrap(True)
            self.material_labels[key] = label_widget
            row, pair = divmod(index, 2)
            grid.addWidget(label_widget, start_row + row, pair * 2)
            grid.addWidget(combo, start_row + row, pair * 2 + 1)

        material_rows = (len(material_specs) + 1) // 2
        add_refs = QPushButton("Kaynaklı Referans Malzemeleri Projeye Ekle")
        add_refs.clicked.connect(self._merge_reference_materials)
        reset_type = QPushButton("Seçili Kurulum Tipi İçin Geometriyi Sıfırla")
        reset_type.clicked.connect(self._reset_geometry_for_type)
        grid.addWidget(add_refs, start_row + material_rows, 0, 1, 2)
        grid.addWidget(reset_type, start_row + material_rows, 2, 1, 2)
        self.geometry_scope_label = QLabel()
        self.geometry_scope_label.setWordWrap(True)
        self.geometry_scope_label.setStyleSheet("font-size:8pt; color:#596b78; font-style:italic;")
        grid.addWidget(self.geometry_scope_label, start_row + material_rows + 1, 0, 1, 4)
        return box

    def _populate_material_combos(self, section: InstallationCrossSectionData) -> None:
        materials = list(self.project.thermal_design.materials)
        for key, combo in self.material_combos.items():
            current = str(getattr(section.channel_geometry, key, ""))
            combo.blockSignals(True)
            combo.clear()
            if key == "surface_material_id":
                combo.addItem("Yok", "")
            for material in materials:
                value = float(material.thermal_resistivity_km_w or 0.0)
                suffix = " · test gerekli" if material.requires_project_test else ""
                combo.addItem(f"{material.material_id} — {material.name} · ρ={value:.3f} K·m/W{suffix}", material.material_id)
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    def _set_geometry_field_visible(self, key: str, visible: bool) -> None:
        spin = getattr(self, "geometry_spins", {}).get(key)
        label = getattr(self, "geometry_labels", {}).get(key)
        if spin is not None:
            spin.setVisible(bool(visible))
        if label is not None:
            label.setVisible(bool(visible))

    def _set_material_field_visible(self, key: str, visible: bool) -> None:
        combo = getattr(self, "material_combos", {}).get(key)
        label = getattr(self, "material_labels", {}).get(key)
        if combo is not None:
            combo.setVisible(bool(visible))
        if label is not None:
            label.setVisible(bool(visible))

    def _required_trench_bottom_width(self, section: InstallationCrossSectionData) -> tuple[float, str]:
        kind = str(section.installation_type).upper()
        g = section.channel_geometry
        if kind == THERMAL_INSTALL_DIRECT_BURIED:
            try:
                envelope = direct_buried_envelope(
                    section, max(float(self.project.cable.overall_diameter_mm) / 1000.0, 0.001)
                )
                return max(0.20, float(envelope.required_bottom_width_m)), "kablo dış zarfı + iki yan kum payı"
            except InstallationInputError:
                return max(0.20, float(g.trench_width_m)), "etkin kablo bulunamadı"
        if kind == THERMAL_INSTALL_DUCT_BANK:
            active_slots = [slot for slot in section.duct_slots if slot.active]
            slot_required = 0.10
            if active_slots:
                left = min(float(slot.x_m) - float(slot.outer_diameter_m) / 2.0 for slot in active_slots)
                right = max(float(slot.x_m) + float(slot.outer_diameter_m) / 2.0 for slot in active_slots)
                slot_required = max(0.10, right - left + 0.10)
            bank_required = max(float(g.duct_bank_width_m), slot_required)
            return max(0.20, bank_required + 0.30), "duct/grout blok + toplam 0,30 m kazı yan payı"
        return max(0.20, float(g.trench_width_m)), "kurulum tipi geometrisi"

    def _apply_required_trench_width(self) -> None:
        section = self._section()
        if section is None:
            return
        required, _reason = self._required_trench_bottom_width(section)
        g = section.channel_geometry
        if str(section.installation_type).upper() == THERMAL_INSTALL_DUCT_BANK and section.duct_slots:
            active_slots = [slot for slot in section.duct_slots if slot.active]
            if active_slots:
                left = min(float(slot.x_m) - float(slot.outer_diameter_m) / 2.0 for slot in active_slots)
                right = max(float(slot.x_m) + float(slot.outer_diameter_m) / 2.0 for slot in active_slots)
                g.duct_bank_width_m = max(float(g.duct_bank_width_m), right - left + 0.10)
        g.trench_width_m = float(required)
        g.source_reference = "USER_INTERACTIVE_GEOMETRY"
        self._invalidate_contour(section.cross_section_id)
        self._fit_on_next_draw = True
        self._refresh_all()

    def _refresh_geometry_scope(self, section: InstallationCrossSectionData | None) -> None:
        if section is None or not hasattr(self, "installation_scope_banner"):
            return
        kind = str(section.installation_type).upper()
        g = section.channel_geometry
        required, reason = self._required_trench_bottom_width(section)
        actual = float(g.trench_width_m)
        delta = actual - required
        if kind == THERMAL_INSTALL_DIRECT_BURIED:
            self.geometry_box.setTitle("Doğrudan gömülü hendek · kum zarfı ve dolgu katmanları")
            self.installation_scope_banner.setText(
                "<b>DIRECT_BURIED — doğrudan gömülü</b><br>"
                "Kablolar gerçek dış çaplarıyla bedding-sand zarfı içindedir. Hendek alt genişliği kullanıcı girdisidir; "
                "yalnız kablo zarfının fiziksel minimumundan küçük olamaz."
            )
            self.installation_scope_banner.setStyleSheet(
                "background:#eef7e9; border:1px solid #789268; color:#29482c; padding:6px;"
            )
            self.geometry_labels["trench_width_m"].setText("Hendek alt genişliği [m]")
        elif kind == THERMAL_INSTALL_DUCT_BANK:
            self.geometry_box.setTitle("Boru / duct bank kazısı · blok ve kazı ölçüleri")
            self.installation_scope_banner.setText(
                "<b>DUCT_BANK — boru / kanal bankası</b><br>"
                "Kazı alt genişliği ile duct/grout blok genişliği ayrı değerlerdir. Boru slotları yalnız bu kurulumda çizilir."
            )
            self.installation_scope_banner.setStyleSheet(
                "background:#eaf3f8; border:1px solid #6f91a5; color:#24485c; padding:6px;"
            )
            self.geometry_labels["trench_width_m"].setText("Kazı alt genişliği [m]")
        else:
            self.geometry_box.setTitle("Kurulum kesiti geometrisi")
            self.installation_scope_banner.setText(
                f"<b>{kind} — {INSTALLATION_TYPE_TR.get(kind, 'özel kurulum')}</b><br>"
                "Bu kurulum tipi kendi özel geometri alanlarıyla tanımlanır."
            )
            self.installation_scope_banner.setStyleSheet(
                "background:#f2f4f5; border:1px solid #8b989f; color:#344b57; padding:6px;"
            )
            self.geometry_labels["trench_width_m"].setText("Kazı / kesit alt genişliği [m]")
        status_color = "#2f6b3c" if delta >= -1e-9 else "#b42318"
        status = "uygun" if delta >= -1e-9 else f"{abs(delta):.3f} m yetersiz"
        self.trench_width_status_label.setText(
            f"Girilen alt genişlik {actual:.3f} m · fiziksel minimum {required:.3f} m ({reason}) · {status}."
        )
        self.trench_width_status_label.setStyleSheet(
            f"font-size:8pt; color:{status_color}; font-weight:700;"
        )
        self.apply_minimum_trench_width_button.setVisible(
            kind in {THERMAL_INSTALL_DIRECT_BURIED, THERMAL_INSTALL_DUCT_BANK}
        )

    def _geometry_visibility(self, installation_type: str) -> None:
        kind = str(installation_type).upper()
        direct = kind == THERMAL_INSTALL_DIRECT_BURIED
        duct = kind == THERMAL_INSTALL_DUCT_BANK
        trough = kind == THERMAL_INSTALL_CONCRETE_TROUGH
        hdd = kind == THERMAL_INSTALL_HDD
        tunnel = kind == THERMAL_INSTALL_TUNNEL

        common = {"center_x_m", "trench_width_m", "trench_depth_m", "side_slope_h_to_v", "surface_layer_thickness_m"}
        direct_only = {
            "bedding_thickness_m", "bedding_bottom_cover_m", "bedding_top_cover_m",
            "bedding_side_clearance_m", "thermal_backfill_height_m", "selected_fill_thickness_m",
            "warning_mesh_offset_above_bedding_m", "warning_tape_offset_above_bedding_m",
            "spacer_width_m", "spacer_height_m",
        }
        duct_only = {"bedding_thickness_m", "duct_bank_width_m", "duct_bank_height_m"}
        trough_only = {"trough_inner_width_m", "trough_inner_height_m", "trough_wall_thickness_m"}
        hdd_only = {"hdd_bore_diameter_m"}
        tunnel_only = {"tunnel_width_m", "tunnel_height_m"}
        slab = {"cover_slab_width_m", "cover_slab_thickness_m", "cover_slab_depth_m"}
        visible = set(common)
        if direct:
            visible |= direct_only | slab
        elif duct:
            visible |= duct_only | slab
        elif trough:
            visible |= trough_only | slab
        elif hdd:
            visible |= hdd_only
        elif tunnel:
            visible |= tunnel_only
        for key in self.geometry_spins:
            self._set_geometry_field_visible(key, key in visible)
            self.geometry_spins[key].setEnabled(key in visible)
        self.geometry_spins["bedding_thickness_m"].setEnabled(False)

        for key in ("cable_group_bottom_locked", "warning_mesh_enabled", "warning_tape_enabled", "spacer_enabled"):
            if key in getattr(self, "geometry_checks", {}):
                self.geometry_checks[key].setVisible(direct)
                self.geometry_checks[key].setEnabled(direct)
        self.cover_slab_check.setVisible(direct or duct or trough)
        self.cover_slab_check.setEnabled(direct or duct or trough)
        if "spacer_enabled" in getattr(self, "geometry_checks", {}):
            self.geometry_checks["spacer_enabled"].setEnabled(
                direct and _formation_code(self._section().arrangement_label if self._section() else "") == "FLAT"
            )
        if hasattr(self, "preset_depth"):
            locked = bool(self._section().channel_geometry.cable_group_bottom_locked) if self._section() is not None else False
            self.preset_depth.setEnabled(not (direct and locked))

        direct_materials = {"native_soil_material_id", "bedding_material_id", "thermal_backfill_material_id", "selected_fill_material_id", "general_fill_material_id", "surface_material_id", "cover_slab_material_id"}
        duct_materials = {"native_soil_material_id", "bedding_material_id", "general_fill_material_id", "surface_material_id", "cover_slab_material_id", "duct_bank_material_id"}
        trough_materials = {"native_soil_material_id", "general_fill_material_id", "surface_material_id", "cover_slab_material_id", "trough_material_id"}
        hdd_materials = {"native_soil_material_id", "hdd_grout_material_id"}
        tunnel_materials = {"native_soil_material_id", "trough_material_id"}
        material_visible = direct_materials if direct else duct_materials if duct else trough_materials if trough else hdd_materials if hdd else tunnel_materials
        for key in self.material_combos:
            self._set_material_field_visible(key, key in material_visible)

        if direct:
            self.geometry_scope_label.setText(
                "Doğrudan gömülü kesitte hendek alt genişliği kullanıcı tarafından girilir. Kablo sayısı/formasyonu büyürse "
                "uygulama yalnız gerekli kablo + yan kum zarfı minimumunu korur; daha büyük proje genişliği değiştirilmez."
            )
        elif duct:
            self.geometry_scope_label.setText(
                "DUCT_BANK kesitinde kazı alt genişliği, duct/grout blok genişliği ve boru iç/dış çapları ayrı parametrelerdir. "
                "Doğrudan gömülü bedding/thermal-backfill alanları bu modda kullanılmaz."
            )
        else:
            self.geometry_scope_label.setText(
                "Çizim salt okunur ve ölçeklidir; bu kurulum tipinin geometri alanları yalnız sağ panelden değiştirilir."
            )
        self._refresh_geometry_scope(self._section())

    def _synchronise_direct_buried_editor(self, section: InstallationCrossSectionData) -> None:
        if str(section.installation_type).upper() != THERMAL_INSTALL_DIRECT_BURIED:
            return
        try:
            envelope = synchronise_direct_buried_geometry(
                section,
                self.project.cable.overall_diameter_mm / 1000.0,
            )
        except InstallationInputError:
            return
        if hasattr(self, "geometry_spins"):
            previous = self._loading
            self._loading = True
            try:
                for key in ("trench_width_m", "trench_depth_m", "bedding_thickness_m"):
                    if key in self.geometry_spins:
                        self.geometry_spins[key].setValue(float(getattr(section.channel_geometry, key)))
                if hasattr(self, "preset_depth"):
                    layout = self._layout_controls_from_section(section)
                    self.preset_depth.setValue(float(layout["depth"]))
            finally:
                self._loading = previous
        if hasattr(self, "cable_depth_summary_label"):
            self.cable_depth_summary_label.setText(
                f"üst dış yüzey {envelope.cable_top_m:.3f} m · merkez {min(item.depth_m for item in section.physical_cables if item.active):.3f} m · "
                f"kum üstü {envelope.bedding_top_m:.3f} m"
            )

    def _geometry_form_changed(self, key: str) -> None:
        if self._loading:
            return
        section = self._section()
        if section is None:
            return
        if key in getattr(self, "geometry_checks", {}):
            value = self.geometry_checks[key].isChecked()
        else:
            value = self.geometry_spins[key].value()
        setattr(section.channel_geometry, key, value)
        if str(section.installation_type).upper() == THERMAL_INSTALL_DIRECT_BURIED:
            self._synchronise_direct_buried_editor(section)
        self._geometry_visibility(section.installation_type)
        section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
        self._invalidate_contour(section.cross_section_id)
        self._refresh_layer_summary(section)
        self._draw_canvas(section)
        self._refresh_validation()

    def _geometry_material_changed(self, key: str) -> None:
        if self._loading:
            return
        section = self._section()
        if section is None:
            return
        setattr(section.channel_geometry, key, str(self.material_combos[key].currentData() or ""))
        section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
        self._invalidate_contour(section.cross_section_id)
        self._refresh_layer_summary(section)
        self._draw_canvas(section)
        self._refresh_validation()

    def _geometry_handle_changed(self, key: str, value: float) -> None:
        if self._loading:
            return
        section = self._section()
        if section is None or key not in self.geometry_spins:
            return
        setattr(section.channel_geometry, key, float(value))
        section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
        self._invalidate_contour(section.cross_section_id)
        self._loading = True
        try:
            self.geometry_spins[key].setValue(float(value))
        finally:
            self._loading = False
        self._queue_canvas_redraw(section)
        self._refresh_validation()

    def _merge_reference_materials(self) -> None:
        added = merge_reference_materials(self.project.thermal_design)
        section = self._section()
        if section is not None:
            self._populate_material_combos(section)
        if added and self.on_change is not None:
            self.on_change()
        QMessageBox.information(
            self, "Termal malzeme kütüphanesi",
            f"Projeye {added} yeni kaynaklı referans malzeme eklendi. Mevcut proje değerleri değiştirilmedi."
        )

    def _reset_geometry_for_type(self) -> None:
        section = self._section()
        if section is None:
            return
        update_channel_geometry_for_installation(section, section.installation_type, reset_dimensions=True)
        self._fit_on_next_draw = True
        self._refresh_all()

    def _build_preset_group(self) -> QGroupBox:
        box = QGroupBox("Kablo yerleşimi — ölçekli çizim bu panelden yenilenir")
        grid = QGridLayout(box)
        self.preset_arrangement = QComboBox()
        for code in ("TREFOIL", "FLAT", "VERTICAL", "CUSTOM"):
            self.preset_arrangement.addItem(FORMATION_DISPLAY[code], code)
        custom_index = self.preset_arrangement.findData("CUSTOM")
        if custom_index >= 0:
            self.preset_arrangement.setItemData(
                custom_index,
                "Devreler sekmesinde farklı formasyon veya farklı x-derinlik uygulanmıştır.",
                Qt.ToolTipRole,
            )
        self.preset_arrangement.currentIndexChanged.connect(self._preset_arrangement_changed)
        self.preset_circuits = _InstallationIntegerSpinBox(); self.preset_circuits.setRange(1, 12); self.preset_circuits.setValue(1)
        self.preset_parallel = _InstallationIntegerSpinBox(); self.preset_parallel.setRange(1, 12); self.preset_parallel.setValue(1)
        self.preset_phase_orders = QLineEdit("ABC")
        self.preset_loads = QLineEdit(str(round(self.project.design_basis.design_current_per_circuit_a or self.project.cable.design_current_a)))
        self.preset_depth = self._double_spin(0.05, 30.0, 1.20, 0.05)
        self.preset_phase_spacing = self._double_spin(0.01, 10.0, 0.15, 0.01)
        self.preset_phase_spacing.setToolTip(
            "Yalnız FLAT/VERTICAL formasyonunda faz kablo merkezleri arası mesafedir. "
            "Duct slot aralığı kanal geometrisi alanlarından yönetilir. TREFOIL'de kullanılmaz: "
            "faz merkez mesafesi otomatik olarak gerçek kablo dış çapına eşittir."
        )
        self.preset_circuit_spacing = self._double_spin(0.01, 20.0, 0.80, 0.05)
        self.preset_parallel_spacing = self._double_spin(0.01, 10.0, 0.25, 0.01)
        self.preset_parallel_spacing.setToolTip(
            "Aynı fazda birden fazla paralel kablo grubu olduğunda grup merkezleri arası mesafedir. "
            "Farklı hat/devreler arasındaki mesafe değildir."
        )
        self.preset_circuit_spacing.setToolTip(
            "Ortak otomatik yerleşimde bütün komşu devre merkezlerini eşit aralıkla kurar. "
            "Farklı C1-C2, C2-C3 aralıkları için Devre Yerleşimi sekmesini kullanın."
        )
        self.preset_duct_rows = _InstallationIntegerSpinBox(); self.preset_duct_rows.setRange(1, 20); self.preset_duct_rows.setValue(2)
        self.preset_duct_cols = _InstallationIntegerSpinBox(); self.preset_duct_cols.setRange(1, 20); self.preset_duct_cols.setValue(3)
        self.preset_duct_rows.setToolTip("Yalnız DUCT BANK formasyonunda boru ızgarasının satır sayısıdır.")
        self.preset_duct_cols.setToolTip("Yalnız DUCT BANK formasyonunda boru ızgarasının sütun sayısıdır; hatlar arası mesafe değildir.")
        for count_widget in (self.preset_circuits, self.preset_parallel, self.preset_duct_rows, self.preset_duct_cols):
            count_widget.valueChanged.connect(self._layout_structure_changed)
        self.preset_phase_orders.editingFinished.connect(self._layout_structure_changed)
        self.preset_loads.editingFinished.connect(self._layout_structure_changed)
        diameter_mm = float(self.project.cable.overall_diameter_mm or 0.0)
        self.cable_diameter_label = QLabel(
            f"Ø {diameter_mm:.1f} mm · çizimde gerçek ölçek"
            if diameter_mm > 0.0 else
            "Dış çap eksik · ölçekli kesit doğrulanmadı"
        )
        self.cable_diameter_label.setStyleSheet(
            "font-weight:700; color:#284f68;" if diameter_mm > 0.0 else
            "font-weight:700; color:#b42318; background:#fff1f0; padding:3px;"
        )
        self.preset_depth.setToolTip(
            "Hendek tabanı kilidi kapalıysa formasyonun referans merkez derinliğidir. "
            "Kilit açıkken kablo grubu gerçek çap ve alt kum örtüsüyle hendek tabanına bağlanır."
        )
        self.cable_depth_summary_label = QLabel("—")
        self.cable_depth_summary_label.setStyleSheet("font-weight:700; color:#365f48;")
        self.duct_capacity_label = QLabel()
        self.duct_capacity_label.setStyleSheet("font-weight:700; color:#415b69;")
        for spin in (self.preset_depth, self.preset_phase_spacing, self.preset_circuit_spacing, self.preset_parallel_spacing):
            spin.valueChanged.connect(self._layout_numeric_changed)
        widgets = [
            ("Formasyon", self.preset_arrangement), ("Devre / hat sayısı", self.preset_circuits),
            ("Faz başına paralel kablo", self.preset_parallel), ("Faz sıraları", self.preset_phase_orders),
            ("Devre akımları [A]", self.preset_loads), ("Formasyon referans derinliği [m]", self.preset_depth),
            ("Faz merkez aralığı [m] (FLAT/VERTICAL)", self.preset_phase_spacing),
            ("Devre merkez aralığı [m]", self.preset_circuit_spacing),
            ("Paralel grup merkez aralığı [m]", self.preset_parallel_spacing), ("Duct satır", self.preset_duct_rows),
            ("Duct sütun", self.preset_duct_cols), ("Kablo dış çapı", self.cable_diameter_label),
            ("Kablo/kum derinlikleri", self.cable_depth_summary_label), ("Duct kapasitesi", self.duct_capacity_label),
        ]
        self.preset_label_by_widget: dict[QWidget, QLabel] = {}
        for index, (label, widget) in enumerate(widgets):
            row, pair = divmod(index, 2)
            label_widget = QLabel(label)
            self.preset_label_by_widget[widget] = label_widget
            grid.addWidget(label_widget, row, pair * 2)
            grid.addWidget(widget, row, pair * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        generate = QPushButton("Devre / kablo sayısını ve yerleşimi uygula")
        generate.clicked.connect(self._apply_preset)
        grid.addWidget(generate, (len(widgets) + 1) // 2, 2, 1, 2)
        self.preset_status_label = QLabel(
            "<i>Formasyon, derinlik ve aralıklar mevcut kabloları anında yeniden çizer. Devre/hat veya paralel kablo sayısı "
            "değiştirildiğinde uygulama düğmesi yeni fiziksel kablo listesini üretir. Farklı devre formasyonları ve farklı x-derinlikler "
            "için alttaki Devre Yerleşimi sekmesi kullanılır. Çizim alanı salt okunurdur.</i>"
        )
        self.preset_status_label.setWordWrap(True)
        self.preset_status_label.setStyleSheet("font-size:8pt; color:#60727f;")
        grid.addWidget(self.preset_status_label, (len(widgets) + 1) // 2 + 1, 0, 1, 4)
        self._update_duct_capacity_label()
        self._update_preset_field_visibility()
        return box

    def _set_preset_field_visible(self, widget: QWidget, visible: bool) -> None:
        widget.setVisible(visible)
        label = getattr(self, "preset_label_by_widget", {}).get(widget)
        if label is not None:
            label.setVisible(visible)

    def _update_preset_field_visibility(self) -> None:
        if not hasattr(self, "preset_arrangement"):
            return
        arrangement = str(self.preset_arrangement.currentData() or "CUSTOM").upper()
        parallel_active = int(self.preset_parallel.value()) > 1
        multiple_circuits = int(self.preset_circuits.value()) > 1
        section = self._section()
        duct_active = bool(section and str(section.installation_type).upper() == THERMAL_INSTALL_DUCT_BANK)
        phase_spacing_active = arrangement in {"FLAT", "VERTICAL"}
        self._set_preset_field_visible(self.preset_phase_spacing, phase_spacing_active)
        self._set_preset_field_visible(self.preset_parallel_spacing, parallel_active)
        self._set_preset_field_visible(self.preset_circuit_spacing, multiple_circuits)
        self._set_preset_field_visible(self.preset_duct_rows, duct_active)
        self._set_preset_field_visible(self.preset_duct_cols, duct_active)
        self._set_preset_field_visible(self.duct_capacity_label, duct_active)

    def _update_duct_capacity_label(self) -> None:
        if not hasattr(self, "duct_capacity_label"):
            return
        capacity = int(self.preset_duct_rows.value()) * int(self.preset_duct_cols.value())
        required = int(self.preset_circuits.value()) * int(self.preset_parallel.value()) * 3
        state = "yeterli" if capacity >= required else f"{required - capacity} slot eksik; satır otomatik artırılır"
        self.duct_capacity_label.setText(f"{capacity} slot / {required} kablo · {state}")

    @staticmethod
    def _double_spin(minimum: float, maximum: float, value: float, step: float) -> QDoubleSpinBox:
        spin = _InstallationDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(4)
        spin.setSingleStep(step)
        spin.setValue(value)
        return spin

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _table_panel(self, table: QTableWidget) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        if hasattr(self, "circuit_table") and table is self.circuit_table:
            table.setMinimumHeight(180)
        layout.addWidget(table, 1)

        row = QHBoxLayout()
        add = QPushButton("Ekle")
        delete = QPushButton("Seçili Satırları Sil")
        add.clicked.connect(lambda: self._add_table_row(table))
        delete.clicked.connect(lambda: self._delete_table_rows(table))
        row.addWidget(add)
        row.addWidget(delete)
        row.addStretch(1)
        layout.addLayout(row)

        if hasattr(self, "circuit_table") and table is self.circuit_table:
            gap_row = QHBoxLayout()
            gap_label = QLabel("Komşu hat merkez aralıkları [m] (tablo sırası):")
            gap_label.setToolTip(
                "C1-C2, C2-C3, C3-C4 ... aralıklarını virgülle girer. İlk devrenin X merkezi sabit kalır; "
                "sonraki devrelerin X merkezleri ardışık olarak hesaplanır."
            )
            self.circuit_gap_edit = QLineEdit()
            self.circuit_gap_edit.setPlaceholderText("Örnek: 0,80; 0,70; 1,00")
            self.circuit_gap_edit.setToolTip(gap_label.toolTip())
            apply_gaps = QPushButton("Komşu aralıkları uygula")
            apply_gaps.clicked.connect(self._apply_neighbor_circuit_gaps)
            gap_row.addWidget(gap_label)
            gap_row.addWidget(self.circuit_gap_edit, 1)
            gap_row.addWidget(apply_gaps)
            layout.addLayout(gap_row)

            apply_circuit = QPushButton("Seçili devre yerleşimini bağımsız uygula")
            apply_circuit.clicked.connect(self._apply_circuit_placements)
            apply_circuit.setToolTip(
                "Seçili devre satırındaki formasyon, X merkezi, referans derinliği ve aralıkları yalnız o devrenin fiziksel kablolarına uygular. "
                "Hiç satır seçili değilse tüm devre satırları uygulanır."
            )
            layout.addWidget(apply_circuit)
            note = QLabel(
                "<i>Hatlar arasındaki bağımsız mesafe, her devrenin X merkeziyle belirlenir. Üstteki 'Devre merkez aralığı' "
                "bütün devreleri eşit aralıkla kurar; bu sekmedeki komşu aralıklar veya X merkezleri farklı C1-C2, C2-C3 ... "
                "mesafeleri için kullanılır. Paralel grup aralığı farklı hatların mesafesi değildir. "
                "TREFOIL satırında faz merkez aralığı gerçek kablo dış çapına kilitlidir.</i>"
            )
            note.setWordWrap(True)
            note.setStyleSheet("font-size:8pt; color:#60727f; padding:2px 4px;")
            layout.addWidget(note)

        if hasattr(self, "material_region_table") and table is self.material_region_table:
            material_row = QHBoxLayout()
            add_vertex = QPushButton("Köşe Ekle")
            delete_vertex = QPushButton("Seçili Köşeyi Sil")
            add_vertex.clicked.connect(self._add_material_region_vertex)
            delete_vertex.clicked.connect(self._delete_selected_material_region_vertex)
            material_row.addWidget(add_vertex)
            material_row.addWidget(delete_vertex)
            self.vertex_selection_label = QLabel("Köşe seçilmedi")
            self.vertex_selection_label.setStyleSheet("font-size:8pt; font-style:italic; color:#60727f;")
            material_row.addWidget(self.vertex_selection_label)
            material_row.addStretch(1)
            layout.addLayout(material_row)
        return panel

    def _section(self) -> InstallationCrossSectionData | None:
        section_id = self.section_selector.currentData()
        return next((item for item in self.design.cross_sections if item.cross_section_id == section_id), None)

    def _apply_section_template(self) -> None:
        section = self._section()
        if section is None:
            return
        try:
            self._read_tables(section)
        except (ValueError, InstallationInputError) as exc:
            QMessageBox.warning(self, "Section şablonu", f"Tablo değerleri okunamadı:\n{exc}")
            return
        template_id = str(self.section_template_combo.currentData() or "")
        if not template_id:
            return
        answer = QMessageBox.question(
            self, "Section şablonu",
            "Seçili şablon kanal ölçülerini, duct slotlarını ve fiziksel kablo koordinatlarını yeniden düzenler. "
            "Devre/kablo kimlikleri, özel malzeme polygonları ve harici ısı kaynakları korunur. Devam edilsin mi?",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            result = apply_cable_channel_template(
                section, template_id,
                cable_outer_diameter_m=self.project.cable.overall_diameter_mm / 1000.0,
            )
        except (KeyError, ValueError, InstallationInputError) as exc:
            QMessageBox.warning(self, "Section şablonu", str(exc))
            return
        self._invalidate_contour(section.cross_section_id)
        self._fit_on_next_draw = True
        self._refresh_all()
        message = (
            f"{result.template_id} uygulandı. {result.moved_cable_count} fiziksel kablo yeniden konumlandırıldı; "
            f"{result.duct_slot_count} duct slotu oluşturuldu."
        )
        if result.warning_messages:
            message += "\n\n" + "\n".join(f"• {item}" for item in result.warning_messages)
        QMessageBox.information(self, "Section şablonu", message)

    def _show_geometry_report(self) -> None:
        section = self._section()
        if section is None:
            return
        try:
            self._read_tables(section)
        except (ValueError, InstallationInputError) as exc:
            QMessageBox.warning(self, "Geometri / çakışma raporu", f"Tablo değerleri okunamadı:\n{exc}")
            return
        issues = [
            item for item in validate_installation_design(
                self.design, cable_outer_diameter_m=self.project.cable.overall_diameter_mm / 1000.0
            )
            if item.cross_section_id in {"", section.cross_section_id}
        ]
        clearances = section_clearance_records(
            section, cable_outer_diameter_m=self.project.cable.overall_diameter_mm / 1000.0
        )
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Geometri / Çakışma Raporu — {section.cross_section_id}")
        fit_window(dialog, DENSITY_NORMAL, center_on=self)
        layout = QVBoxLayout(dialog)
        summary = QLabel(
            f"<b>{section.cross_section_id} — {section.name}</b><br>"
            f"{sum(item.severity == 'ERROR' for item in issues)} hata · "
            f"{sum(item.severity == 'WARNING' for item in issues)} uyarı · "
            f"{sum(item.status == 'FAIL' for item in clearances)} fiziksel çakışma/uygunsuzluk"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        tabs = QTabWidget()
        issue_table = QTableWidget(len(issues), 5)
        issue_table.setHorizontalHeaderLabels(["Seviye", "Kod", "Nesne", "Section", "Açıklama"])
        for row, item in enumerate(issues):
            values = [item.severity, item.code, item.object_id, item.cross_section_id, item.message]
            for column, value in enumerate(values):
                issue_table.setItem(row, column, QTableWidgetItem(str(value)))
        issue_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        issue_table.setAlternatingRowColors(True)
        issue_table.horizontalHeader().setStretchLastSection(True)
        tabs.addTab(issue_table, f"Doğrulama ({len(issues)})")
        clearance_table = QTableWidget(len(clearances), 7)
        clearance_table.setHorizontalHeaderLabels([
            "Durum", "Kategori", "Nesne A", "Nesne B", "Net açıklık [m]", "Fit sınırı [m]", "Açıklama"
        ])
        for row, item in enumerate(clearances):
            values = [
                item.status, item.category, item.object_a, item.object_b,
                f"{item.actual_clearance_m:.5f}", f"{item.required_clearance_m:.5f}", item.message,
            ]
            for column, value in enumerate(values):
                clearance_table.setItem(row, column, QTableWidgetItem(str(value)))
        clearance_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        clearance_table.setAlternatingRowColors(True)
        clearance_table.horizontalHeader().setStretchLastSection(True)
        tabs.addTab(clearance_table, f"Açıklıklar ({len(clearances)})")
        layout.addWidget(tabs, 1)
        note = QLabel(
            "<i>Fit sınırı 0 m yalnız geometrik çakışma kontrolüdür. Proje/işveren/standart kaynaklı minimum yapım aralıkları "
            "uygulama özelinde ayrıca tanımlanmalı ve doğrulanmalıdır.</i>"
        )
        note.setWordWrap(True); note.setStyleSheet("color:#60727f;")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _material_name_map(self) -> dict[str, str]:
        return {item.material_id: item.name for item in self.project.thermal_design.materials}

    @staticmethod
    def _overlay_key(cross_section_id: str, region_id: str) -> str:
        return f"{cross_section_id}::{region_id}"

    def _populate_contour_regions(self, section: InstallationCrossSectionData) -> None:
        current = str(self.contour_region_combo.currentData() or "")
        self.contour_region_combo.blockSignals(True)
        self.contour_region_combo.clear()
        for region_id in section.region_ids:
            region = next((item for item in self.project.thermal_design.regions if item.region_id == region_id), None)
            label = f"{region_id} — {region.name}" if region is not None else region_id
            self.contour_region_combo.addItem(label, region_id)
        if not section.region_ids:
            self.contour_region_combo.addItem("Bağlı termal bölge yok", "")
        index = self.contour_region_combo.findData(current)
        self.contour_region_combo.setCurrentIndex(max(0, index))
        self.contour_region_combo.blockSignals(False)
        region_id = str(self.contour_region_combo.currentData() or "")
        available = self._overlay_key(section.cross_section_id, region_id) in self._thermal_overlays
        self.contour_check.setEnabled(available)
        self.result_label_check.setEnabled(available)
        if not available:
            self.contour_check.setChecked(False)
            self.result_label_check.setChecked(False)

    def _draw_canvas(self, section: InstallationCrossSectionData | None = None) -> None:
        section = section or self._section()
        if section is None:
            return
        region_id = str(self.contour_region_combo.currentData() or "") if hasattr(self, "contour_region_combo") else ""
        overlay = self._thermal_overlays.get(self._overlay_key(section.cross_section_id, region_id))
        show_contour = bool(getattr(self, "contour_check", None) and self.contour_check.isChecked())
        show_result_labels = bool(getattr(self, "result_label_check", None) and self.result_label_check.isChecked())
        dimension_mode = (
            str(self.dimension_mode_combo.currentData() or "CONSTRUCTION")
            if hasattr(self, "dimension_mode_combo") else "CONSTRUCTION"
        )
        auto_fit = bool(self._fit_on_next_draw)
        self._fit_on_next_draw = False
        self.canvas.draw_section(
            section,
            self.project.cable.overall_diameter_mm / 1000.0,
            show_material_ids=bool(getattr(self, "material_id_check", None) and self.material_id_check.isChecked()),
            material_names=self._material_name_map(),
            temperature_overlay=overlay,
            dimension_mode=dimension_mode,
            show_result_labels=show_result_labels,
            show_temperature_contour=show_contour,
            show_object_ids=bool(getattr(self, "object_id_check", None) and self.object_id_check.isChecked()),
            show_detail_labels=bool(getattr(self, "detail_labels_check", None) and self.detail_labels_check.isChecked()),
            layer_colors=self._layer_colors,
            cable_layers=self.project.cable.layers,
            auto_fit=auto_fit,
        )

    def _queue_canvas_redraw(self, section: InstallationCrossSectionData | None = None) -> None:
        """Coalesce redraws and avoid clearing QGraphicsScene inside itemChange."""
        if self._redraw_pending:
            return
        self._redraw_pending = True
        section_id = section.cross_section_id if section is not None else ""

        def _run() -> None:
            self._redraw_pending = False
            current = self._section()
            if current is None:
                return
            if section_id and current.cross_section_id != section_id:
                return
            self._draw_canvas(current)

        QTimer.singleShot(0, _run)

    def _invalidate_contour(self, cross_section_id: str) -> None:
        for key in [key for key in self._thermal_overlays if key.startswith(f"{cross_section_id}::")]:
            self._thermal_overlays.pop(key, None)
        current = self._section()
        if current is not None and current.cross_section_id == cross_section_id:
            self.contour_check.blockSignals(True)
            self.contour_check.setChecked(False)
            self.contour_check.setEnabled(False)
            self.contour_check.blockSignals(False)
            self.result_label_check.blockSignals(True)
            self.result_label_check.setChecked(False)
            self.result_label_check.setEnabled(False)
            self.result_label_check.blockSignals(False)
            self.canvas.clear_temperature_overlay()
            if hasattr(self, "contour_status_label"):
                self.contour_status_label.setText(
                    "<i>Geometri değişti · önceki gölge 2D kontur geçersiz kılındı</i>"
                )

    def _run_temperature_contour(self) -> None:
        section = self._section()
        if section is None:
            return
        try:
            self._read_tables(section)
        except (ValueError, InstallationInputError) as exc:
            QMessageBox.warning(self, "2D sıcaklık konturu", f"Tablo değerleri okunamadı:\n{exc}")
            return
        region_id = str(self.contour_region_combo.currentData() or "")
        if not region_id:
            QMessageBox.warning(self, "2D sıcaklık konturu", "Seçili kesite bağlı bir termal bölge bulunmuyor.")
            return
        working = deepcopy(self.project)
        working.installation_design = deepcopy(self.design)
        try:
            result = solve_multiconductor_thermal(working, mesh_scale=1.5)
        except MulticonductorThermalInputError as exc:
            QMessageBox.warning(self, "2D sıcaklık konturu", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "2D sıcaklık konturu", f"Kontur hesabı çalıştırılamadı:\n{exc}")
            return
        overlay = next((
            item for item in result.regions
            if item.cross_section_id == section.cross_section_id and item.region_id == region_id
        ), None)
        if overlay is None:
            QMessageBox.warning(
                self, "2D sıcaklık konturu",
                f"{section.cross_section_id}/{region_id} için gölge termal sonuç oluşmadı.",
            )
            return
        self._thermal_overlays[self._overlay_key(section.cross_section_id, region_id)] = overlay
        self.contour_check.setEnabled(True)
        self.result_label_check.setEnabled(True)
        self.contour_check.setChecked(True)
        self.contour_status_label.setText(
            f"<b>Gölge 2D önizleme</b> · SHADOW_COMPARE · {region_id} · "
            f"Tmax={overlay.maximum_nodal_conductor_temperature_c:.2f} °C"
        )
        self._draw_canvas(section)
        QMessageBox.information(
            self, "2D sıcaklık konturu",
            f"{region_id} konturu hazır. Maksimum iletken sıcaklığı "
            f"{overlay.maximum_nodal_conductor_temperature_c:.2f} °C.",
        )

    @staticmethod
    def _safe_filename(value: str) -> str:
        value = re.sub(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü._-]+", "_", str(value).strip())
        return value.strip("._") or "kablo_kanal_kesiti"

    def _export_engineering_section(self) -> None:
        section = self._section()
        if section is None:
            return
        try:
            self._read_tables(section)
        except (ValueError, InstallationInputError) as exc:
            QMessageBox.warning(self, "Mühendislik kesit çıktısı", f"Tablo değerleri okunamadı:\n{exc}")
            return
        default_name = self._safe_filename(
            f"{self.project.project_name}_{section.cross_section_id}_kablo_kanal_kesiti.png"
        )
        path_text, _selected = QFileDialog.getSaveFileName(
            self, "Mühendislik kesit çıktısı", default_name, "PNG görseli (*.png)"
        )
        if not path_text:
            return
        image_path = Path(path_text)
        if image_path.suffix.lower() != ".png":
            image_path = image_path.with_suffix(".png")
        try:
            self._draw_canvas(section)
            self.canvas.export_png(str(image_path))
            json_path = image_path.with_name(image_path.stem + "_model.json")
            csv_path = image_path.with_name(image_path.stem + "_objects.csv")
            validation_path = image_path.with_name(image_path.stem + "_validation.csv")
            material_ids = {
                section.channel_geometry.native_soil_material_id,
                section.channel_geometry.bedding_material_id,
                section.channel_geometry.thermal_backfill_material_id,
                section.channel_geometry.selected_fill_material_id,
                section.channel_geometry.general_fill_material_id,
                section.channel_geometry.surface_material_id,
                section.channel_geometry.cover_slab_material_id,
                section.channel_geometry.duct_bank_material_id,
                section.channel_geometry.trough_material_id,
                section.channel_geometry.hdd_grout_material_id,
                *(item.material_id for item in section.material_regions),
            } - {""}
            materials = [
                asdict(item) for item in self.project.thermal_design.materials
                if item.material_id in material_ids
            ]
            payload = {
                "application_version": "0.16.9.4.14",
                "project_name": self.project.project_name,
                "section": asdict(section),
                "materials": materials,
                "contour_region_id": str(self.contour_region_combo.currentData() or ""),
                "contour_visible": bool(self.contour_check.isChecked()),
                "material_id_view": bool(self.material_id_check.isChecked()),
                "dimension_mode": str(self.dimension_mode_combo.currentData() or "CONSTRUCTION"),
                "result_labels_visible": bool(self.result_label_check.isChecked()),
            }
            json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = SpreadsheetSafeDictWriter(handle, fieldnames=[
                    "object_type", "object_id", "name", "circuit", "phase", "parallel",
                    "x_m", "depth_m", "material_id", "vertex_index", "value", "unit",
                ])
                writer.writeheader()
                g = section.channel_geometry
                for key, value in asdict(g).items():
                    if isinstance(value, (str, int, float, bool)):
                        writer.writerow({"object_type": "GEOMETRY", "object_id": section.cross_section_id, "name": key, "value": value})
                for item in section.physical_cables:
                    writer.writerow({
                        "object_type": "CABLE", "object_id": item.physical_cable_id,
                        "circuit": item.circuit_id, "phase": item.phase, "parallel": item.parallel_index,
                        "x_m": item.x_m, "depth_m": item.depth_m,
                    })
                for item in section.duct_slots:
                    writer.writerow({
                        "object_type": "DUCT", "object_id": item.slot_id,
                        "x_m": item.x_m, "depth_m": item.depth_m,
                        "value": item.outer_diameter_m, "unit": "m dış çap",
                    })
                for region in section.material_regions:
                    for index, point in enumerate(region.vertices_m):
                        writer.writerow({
                            "object_type": "MATERIAL_VERTEX", "object_id": region.region_id,
                            "name": region.name, "x_m": point[0], "depth_m": point[1],
                            "material_id": region.material_id, "vertex_index": index + 1,
                        })
                for item in section.external_heat_sources:
                    writer.writerow({
                        "object_type": "HEAT_SOURCE", "object_id": item.source_id, "name": item.name,
                        "x_m": item.x_m, "depth_m": item.depth_m, "value": item.heat_w_m, "unit": "W/m",
                    })
            issues = [
                item for item in validate_installation_design(
                    self.design, cable_outer_diameter_m=self.project.cable.overall_diameter_mm / 1000.0
                )
                if item.cross_section_id in {"", section.cross_section_id}
            ]
            clearances = section_clearance_records(
                section, cable_outer_diameter_m=self.project.cable.overall_diameter_mm / 1000.0
            )
            with validation_path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = SpreadsheetSafeDictWriter(handle, fieldnames=[
                    "record_type", "severity_status", "code_category", "object_a", "object_b",
                    "actual_clearance_m", "required_clearance_m", "message",
                ])
                writer.writeheader()
                for item in issues:
                    writer.writerow({
                        "record_type": "VALIDATION", "severity_status": item.severity,
                        "code_category": item.code, "object_a": item.object_id,
                        "object_b": item.cross_section_id, "message": item.message,
                    })
                for item in clearances:
                    writer.writerow({
                        "record_type": "CLEARANCE", "severity_status": item.status,
                        "code_category": item.category, "object_a": item.object_a, "object_b": item.object_b,
                        "actual_clearance_m": f"{item.actual_clearance_m:.6f}",
                        "required_clearance_m": f"{item.required_clearance_m:.6f}",
                        "message": item.message,
                    })
        except Exception as exc:
            QMessageBox.critical(self, "Mühendislik kesit çıktısı", f"Çıktı oluşturulamadı:\n{exc}")
            return
        QMessageBox.information(
            self, "Mühendislik kesit çıktısı",
            f"Kesit paketi oluşturuldu:\n{image_path.name}\n{json_path.name}\n{csv_path.name}",
        )

    def _selected_material_region(self) -> ThermalMaterialRegionData | None:
        section = self._section()
        if section is None:
            return None
        region_id = self._selected_material_vertex[0] if self._selected_material_vertex else ""
        if not region_id:
            rows = self.material_region_table.selectionModel().selectedRows()
            if rows:
                region_id = self._text(self.material_region_table, rows[0].row(), 0)
        return next((item for item in section.material_regions if item.region_id == region_id), None)

    def _material_region_vertex_selected(self, region_id: str, vertex_index: int) -> None:
        self._selected_material_vertex = (region_id, int(vertex_index))
        if hasattr(self, "vertex_selection_label"):
            self.vertex_selection_label.setText(f"{region_id} · köşe {vertex_index + 1}")
        for row in range(self.material_region_table.rowCount()):
            if self._text(self.material_region_table, row, 0) == region_id:
                self.material_region_table.selectRow(row)
                break

    def _material_region_vertex_moved(self, region_id: str, vertex_index: int, x_m: float, depth_m: float) -> None:
        if self._loading:
            return
        section = self._section()
        if section is None:
            return
        region = next((item for item in section.material_regions if item.region_id == region_id), None)
        if region is None or not (0 <= int(vertex_index) < len(region.vertices_m)):
            return
        region.vertices_m[int(vertex_index)] = [float(x_m), max(0.0, float(depth_m))]
        region.source_reference = "USER_INTERACTIVE_GEOMETRY"
        section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
        self._invalidate_contour(section.cross_section_id)
        self._selected_material_vertex = (region_id, int(vertex_index))
        self._loading = True
        try:
            self._populate_material_region_table(section)
        finally:
            self._loading = False
        self._refresh_validation()

    def _add_material_region_vertex(self) -> None:
        section = self._section()
        region = self._selected_material_region()
        if section is None or region is None:
            QMessageBox.warning(self, "Malzeme polygonu", "Önce bir malzeme bölgesi veya polygon köşesi seçin.")
            return
        try:
            region.vertices_m, inserted = insert_material_region_vertex(region.vertices_m)
        except InstallationInputError as exc:
            QMessageBox.warning(self, "Malzeme polygonu", str(exc))
            return
        region.source_reference = "USER_INTERACTIVE_GEOMETRY"
        section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
        self._selected_material_vertex = (region.region_id, inserted)
        self._invalidate_contour(section.cross_section_id)
        self._refresh_all()
        self._material_region_vertex_selected(region.region_id, inserted)

    def _delete_selected_material_region_vertex(self) -> None:
        section = self._section()
        if section is None or self._selected_material_vertex is None:
            QMessageBox.warning(self, "Malzeme polygonu", "Silmek için kanvasta bir polygon köşesi seçin.")
            return
        region_id, vertex_index = self._selected_material_vertex
        region = next((item for item in section.material_regions if item.region_id == region_id), None)
        if region is None:
            return
        try:
            region.vertices_m = remove_material_region_vertex(region.vertices_m, vertex_index)
        except InstallationInputError as exc:
            QMessageBox.warning(self, "Malzeme polygonu", str(exc))
            return
        region.source_reference = "USER_INTERACTIVE_GEOMETRY"
        section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
        self._selected_material_vertex = None
        if hasattr(self, "vertex_selection_label"):
            self.vertex_selection_label.setText("Köşe seçilmedi")
        self._invalidate_contour(section.cross_section_id)
        self._refresh_all()

    def _populate_section_selector(self) -> None:
        current = self.design.active_cross_section_id
        self.section_selector.blockSignals(True)
        self.section_selector.clear()
        for section in self.design.cross_sections:
            self.section_selector.addItem(f"{section.cross_section_id} — {section.name}", section.cross_section_id)
        index = self.section_selector.findData(current)
        self.section_selector.setCurrentIndex(max(0, index))
        self.section_selector.blockSignals(False)

    def _section_selected(self, *_args) -> None:
        section = self._section()
        if section is not None:
            self.design.active_cross_section_id = section.cross_section_id
        self._fit_on_next_draw = True
        self._refresh_all()

    def _layout_controls_from_section(self, section: InstallationCrossSectionData) -> dict[str, object]:
        active_circuits = [item for item in section.circuits if item.active]
        active_cables = [item for item in section.physical_cables if item.active]
        arrangement = _formation_code(section.arrangement_label)
        circuit_ids = [item.circuit_id for item in active_circuits] or sorted({item.circuit_id for item in active_cables})
        parallel_count = max((int(item.parallel_index) for item in active_cables), default=1)
        depths = [float(item.depth_m) for item in active_cables]

        groups: dict[tuple[str, int], dict[str, PhysicalCableData]] = {}
        for cable in active_cables:
            groups.setdefault((cable.circuit_id, int(cable.parallel_index)), {})[str(cable.phase).upper()] = cable
        phase_distances: list[float] = []
        group_centres: dict[tuple[str, int], tuple[float, float]] = {}
        for key, by_phase in groups.items():
            points = [(float(item.x_m), float(item.depth_m)) for item in by_phase.values()]
            if points:
                group_centres[key] = (
                    sum(point[0] for point in points) / len(points),
                    sum(point[1] for point in points) / len(points),
                )
            if all(phase in by_phase for phase in "ABC"):
                ordered = [by_phase[phase] for phase in "ABC"]
                if arrangement == "TREFOIL":
                    for first, second in ((0, 1), (1, 2), (2, 0)):
                        phase_distances.append(sqrt(
                            (ordered[first].x_m - ordered[second].x_m) ** 2
                            + (ordered[first].depth_m - ordered[second].depth_m) ** 2
                        ))
                elif arrangement == "VERTICAL":
                    values = sorted(float(item.depth_m) for item in ordered)
                    phase_distances.extend(values[index + 1] - values[index] for index in range(len(values) - 1))
                else:
                    values = sorted(float(item.x_m) for item in ordered)
                    phase_distances.extend(values[index + 1] - values[index] for index in range(len(values) - 1))

        parallel_distances: list[float] = []
        for circuit_id in circuit_ids:
            centres = sorted(
                (parallel, centre) for (cid, parallel), centre in group_centres.items() if cid == circuit_id
            )
            parallel_distances.extend(
                abs(centres[index + 1][1][0] - centres[index][1][0])
                for index in range(len(centres) - 1)
            )
        circuit_centres: list[float] = []
        for circuit_id in circuit_ids:
            values = [centre[0] for (cid, _parallel), centre in group_centres.items() if cid == circuit_id]
            if values:
                circuit_centres.append(sum(values) / len(values))
        circuit_centres.sort()
        circuit_distances = [
            circuit_centres[index + 1] - circuit_centres[index]
            for index in range(len(circuit_centres) - 1)
        ]
        phase_spacing_value = (
            sum(phase_distances) / len(phase_distances)
            if phase_distances else self.preset_phase_spacing.value()
        )
        depth_value = sum(depths) / len(depths) if depths else 1.20
        if arrangement == "TREFOIL" and phase_distances:
            depth_value -= sqrt(3.0) * phase_spacing_value / 12.0
        return {
            "arrangement": arrangement,
            "circuit_count": max(1, len(circuit_ids)),
            "parallel_count": max(1, parallel_count),
            "phase_orders": ", ".join(item.phase_order for item in active_circuits) or "ABC",
            # 17 significant digits guarantee a Python float round-trip; UI text must
            # never quantize an electrical operating point merely by being displayed.
            "loads": ", ".join(format(float(item.load_current_a), ".17g") for item in active_circuits) or "0",
            "depth": depth_value,
            "phase_spacing": phase_spacing_value,
            "parallel_spacing": sum(parallel_distances) / len(parallel_distances) if parallel_distances else self.preset_parallel_spacing.value(),
            "circuit_spacing": sum(circuit_distances) / len(circuit_distances) if circuit_distances else self.preset_circuit_spacing.value(),
            "duct_rows": max((int(item.row_index) for item in section.duct_slots if item.active), default=self.preset_duct_rows.value()),
            "duct_cols": max((int(item.column_index) for item in section.duct_slots if item.active), default=self.preset_duct_cols.value()),
        }

    def _refresh_all(self) -> None:
        section = self._section()
        if section is None:
            return
        self._loading = True
        try:
            if str(section.installation_type).upper() == THERMAL_INSTALL_DIRECT_BURIED:
                try:
                    synchronise_direct_buried_geometry(
                        section,
                        self.project.cable.overall_diameter_mm / 1000.0,
                    )
                except InstallationInputError:
                    pass
            self.section_id_edit.setText(section.cross_section_id)
            self.section_name_edit.setText(section.name)
            self.section_type_combo.setCurrentText(section.installation_type)
            self._update_installation_type_caption(section.installation_type)
            self.arrangement_edit.setText(_section_formation_display(section))
            if hasattr(self, "preset_arrangement"):
                layout = self._layout_controls_from_section(section)
                index = self.preset_arrangement.findData(layout["arrangement"])
                if index >= 0:
                    self.preset_arrangement.setCurrentIndex(index)
                self.preset_circuits.setValue(int(layout["circuit_count"]))
                self.preset_parallel.setValue(int(layout["parallel_count"]))
                self.preset_phase_orders.setText(str(layout["phase_orders"]))
                self.preset_loads.setText(str(layout["loads"]))
                self.preset_depth.setValue(float(layout["depth"]))
                self.preset_phase_spacing.setValue(max(0.01, float(layout["phase_spacing"])))
                self.preset_parallel_spacing.setValue(max(0.01, float(layout["parallel_spacing"])))
                self.preset_circuit_spacing.setValue(max(0.01, float(layout["circuit_spacing"])))
                self.preset_duct_rows.setValue(max(1, int(layout["duct_rows"])))
                self.preset_duct_cols.setValue(max(1, int(layout["duct_cols"])))
                diameter_mm = float(self.project.cable.overall_diameter_mm or 0.0)
                self.cable_diameter_label.setText(
                    f"Ø {diameter_mm:.1f} mm · çizimde gerçek ölçek"
                    if diameter_mm > 0.0 else
                    "Dış çap eksik · ölçekli kesit doğrulanmadı"
                )
                self.cable_diameter_label.setStyleSheet(
                    "font-weight:700; color:#284f68;" if diameter_mm > 0.0 else
                    "font-weight:700; color:#b42318; background:#fff1f0; padding:3px;"
                )
                self._update_duct_capacity_label()
                self.preset_depth.setEnabled(
                    not (
                        str(section.installation_type).upper() == THERMAL_INSTALL_DIRECT_BURIED
                        and bool(section.channel_geometry.cable_group_bottom_locked)
                    )
                )
                if str(section.installation_type).upper() == THERMAL_INSTALL_DIRECT_BURIED:
                    try:
                        envelope = direct_buried_envelope(
                            section, self.project.cable.overall_diameter_mm / 1000.0
                        )
                        self.cable_depth_summary_label.setText(
                            f"üst dış yüzey {envelope.cable_top_m:.3f} m · kum üstü {envelope.bedding_top_m:.3f} m"
                        )
                    except InstallationInputError:
                        self.cable_depth_summary_label.setText("—")
                else:
                    self.cable_depth_summary_label.setText("kurulum tipine göre")
            if hasattr(self, "section_template_combo"):
                template_id = ""
                source_ref = str(section.source_reference or "")
                if source_ref.startswith("USER_SECTION_TEMPLATE:"):
                    template_id = source_ref.split(":", 1)[1]
                self.section_template_combo.blockSignals(True)
                template_index = self.section_template_combo.findData(template_id)
                self.section_template_combo.setCurrentIndex(max(0, template_index))
                self.section_template_combo.blockSignals(False)
            self.region_ids_edit.setText(", ".join(section.region_ids))
            self.section_source_edit.setText(section.source_reference)
            for key, spin in self.geometry_spins.items():
                spin.setValue(float(getattr(section.channel_geometry, key)))
            for key, check in getattr(self, "geometry_checks", {}).items():
                check.setChecked(bool(getattr(section.channel_geometry, key)))
            self._populate_material_combos(section)
            self._refresh_layer_summary(section)
            self._geometry_visibility(section.installation_type)
            self._populate_circuit_table(section)
            self._populate_cable_table(section)
            self._populate_duct_table(section)
            self._populate_material_region_table(section)
            self._populate_heat_table(section)
            self._populate_contour_regions(section)
            self._draw_canvas(section)
        finally:
            self._loading = False
        self._refresh_validation()

    @staticmethod
    def _item(value, *, checked: bool | None = None) -> QTableWidgetItem:
        item = QTableWidgetItem("" if value is None else str(value))
        if checked is not None:
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            item.setText("")
        return item

    def _apply_circuit_placements(self) -> None:
        section = self._section()
        if section is None:
            return
        try:
            self._read_tables(section)
        except (ValueError, InstallationInputError) as exc:
            QMessageBox.warning(self, "Devre yerleşimi", f"Tablo değerleri okunamadı:\n{exc}")
            return
        selected_rows = sorted({index.row() for index in self.circuit_table.selectedIndexes()})
        rows = selected_rows or list(range(self.circuit_table.rowCount()))
        warnings: list[str] = []
        moved = 0
        for row in rows:
            circuit_id = self._text(self.circuit_table, row, 0)
            formation_widget = self.circuit_table.cellWidget(row, 6)
            if isinstance(formation_widget, QComboBox):
                arrangement = str(formation_widget.currentData() or "TREFOIL")
            else:
                arrangement = "TREFOIL"
            try:
                result = reposition_circuit_cables(
                    section, circuit_id, arrangement,
                    center_x_m=self._float(self._text(self.circuit_table, row, 7)),
                    reference_depth_m=self._float(self._text(self.circuit_table, row, 8), 1.20),
                    phase_spacing_m=self._float(self._text(self.circuit_table, row, 9), 0.15),
                    parallel_spacing_m=self._float(self._text(self.circuit_table, row, 10), 0.25),
                    cable_outer_diameter_m=max(self.project.cable.overall_diameter_mm / 1000.0, 0.001),
                )
            except (ValueError, InstallationInputError) as exc:
                QMessageBox.warning(self, "Devre yerleşimi", f"{circuit_id}: {exc}")
                return
            moved += result.moved_cable_count
            warnings.extend(f"{circuit_id}: {item}" for item in result.warning_messages)
        if str(section.installation_type).upper() == THERMAL_INSTALL_DIRECT_BURIED:
            section.channel_geometry.cable_group_bottom_locked = False
            try:
                synchronise_direct_buried_geometry(
                    section, max(self.project.cable.overall_diameter_mm / 1000.0, 0.001),
                    anchor_to_trench_bottom=False, expand_trench_width=True,
                )
            except InstallationInputError:
                pass
        self._invalidate_contour(section.cross_section_id)
        self._fit_on_next_draw = True
        self._refresh_all()
        self._update_circuit_spacing_summary()
        message = f"{len(rows)} devrenin bağımsız yerleşimi uygulandı; {moved} fiziksel kablo yeniden konumlandırıldı."
        if warnings:
            message += "\n\n" + "\n".join(f"• {item}" for item in warnings)
        QMessageBox.information(self, "Devre yerleşimi", message)


    def _update_circuit_phase_spacing_cell(self, row: int) -> None:
        """Lock TREFOIL phase spacing to the selected cable outer diameter."""
        if not hasattr(self, "circuit_table") or row < 0 or row >= self.circuit_table.rowCount():
            return
        formation = self.circuit_table.cellWidget(row, 6)
        arrangement = (
            str(formation.currentData() or "TREFOIL").upper()
            if isinstance(formation, QComboBox)
            else "TREFOIL"
        )
        item = self.circuit_table.item(row, 9)
        if item is None:
            item = self._item("")
            self.circuit_table.setItem(row, 9, item)
        if arrangement == "TREFOIL":
            diameter_m = max(float(self.project.cable.overall_diameter_mm) / 1000.0, 0.001)
            self.circuit_table.blockSignals(True)
            item.setText(f"{diameter_m:.5f}")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setToolTip(
                "TREFOIL üç kablonun temas eden demetidir. Faz merkez mesafesi "
                "kablo dış çapına eşittir ve kullanıcı tarafından değiştirilemez."
            )
            self.circuit_table.blockSignals(False)
        else:
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            item.setToolTip("FLAT/VERTICAL formasyonunda düzenlenebilir faz merkez aralığı.")

    def _populate_circuit_table(self, section: InstallationCrossSectionData) -> None:
        self.circuit_table.setRowCount(len(section.circuits))
        for row, circuit in enumerate(section.circuits):
            values = [circuit.circuit_id, circuit.name, circuit.phase_order, circuit.load_current_a, circuit.load_factor]
            for col, value in enumerate(values):
                item = self._item(value)
                if col == 4:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    item.setToolTip(
                        "Legacy alan: kararlı durum RMS akımını ölçeklemez. IEC 60853 kayıp-yük faktörü μ, Geçici/Çevrimsel yük profilinden otomatik türetilir."
                    )
                self.circuit_table.setItem(row, col, item)
            self.circuit_table.setItem(row, 5, self._item("", checked=circuit.active))
            try:
                placement = infer_circuit_placement(section, circuit.circuit_id)
            except ValueError:
                placement = None
            formation = QComboBox()
            for code in ("TREFOIL", "FLAT", "VERTICAL"):
                formation.addItem(FORMATION_DISPLAY[code], code)
            current = placement.arrangement if placement is not None else _formation_code(section.arrangement_label)
            index = formation.findData(current)
            formation.setCurrentIndex(max(0, index))
            formation.setToolTip(
                "Bu seçim yalnız 'bağımsız uygula' düğmesiyle ilgili devreye yazılır. "
                "TREFOIL seçildiğinde faz merkez aralığı gerçek kablo dış çapına kilitlenir."
            )
            formation.currentIndexChanged.connect(
                lambda _index, row=row: self._update_circuit_phase_spacing_cell(row)
            )
            self.circuit_table.setCellWidget(row, 6, formation)
            numeric = (
                placement.center_x_m if placement is not None else 0.0,
                placement.reference_depth_m if placement is not None else 1.20,
                placement.phase_spacing_m if placement is not None else 0.15,
                placement.parallel_spacing_m if placement is not None else 0.25,
            )
            for offset, value in enumerate(numeric, start=7):
                self.circuit_table.setItem(row, offset, self._item(f"{float(value):.5f}"))
            self._update_circuit_phase_spacing_cell(row)
        self.circuit_table.resizeColumnsToContents()
        self.circuit_table.setColumnWidth(6, 190)
        self.circuit_table.setColumnWidth(7, 115)
        self.circuit_table.setColumnWidth(8, 145)
        self.circuit_table.setColumnWidth(9, 145)
        self.circuit_table.setColumnWidth(10, 155)
        self._update_circuit_spacing_summary()

    def _populate_cable_table(self, section: InstallationCrossSectionData) -> None:
        self.cable_table.setRowCount(len(section.physical_cables))
        for row, cable in enumerate(section.physical_cables):
            values = [
                cable.physical_cable_id, cable.circuit_id, cable.phase, cable.parallel_index,
                f"{cable.x_m:.8f}", f"{cable.depth_m:.8f}", cable.duct_slot_id,
                cable.current_override_a,
                "" if cable.current_angle_override_deg is None else cable.current_angle_override_deg,
                cable.load_factor,
            ]
            for col, value in enumerate(values):
                item = self._item(value)
                if col == 9:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    item.setToolTip(
                        "Legacy alan: fiziksel kablo RMS akım override değerini ölçeklemez ve üretim hesabında kullanılmaz."
                    )
                self.cable_table.setItem(row, col, item)
            self.cable_table.setItem(row, 10, self._item("", checked=cable.active))
        self.cable_table.resizeColumnsToContents()

    def _populate_duct_table(self, section: InstallationCrossSectionData) -> None:
        self.duct_table.setRowCount(len(section.duct_slots))
        for row, slot in enumerate(section.duct_slots):
            values = [slot.slot_id, slot.x_m, slot.depth_m, slot.inner_diameter_m, slot.outer_diameter_m, slot.row_index, slot.column_index]
            for col, value in enumerate(values):
                self.duct_table.setItem(row, col, self._item(value))
            self.duct_table.setItem(row, 7, self._item("", checked=slot.active))
        self.duct_table.resizeColumnsToContents()

    @staticmethod
    def _vertices_text(vertices) -> str:
        return "; ".join(f"{float(point[0]):.5f},{float(point[1]):.5f}" for point in vertices)

    @staticmethod
    def _parse_vertices(value: str) -> list[list[float]]:
        vertices: list[list[float]] = []
        for token in str(value).replace("|", ";").split(";"):
            token = token.strip()
            if not token:
                continue
            parts = [item.strip() for item in token.replace(" ", ",").split(",") if item.strip()]
            if len(parts) != 2:
                raise ValueError(f"Köşe 'x,derinlik' biçiminde olmalıdır: {token!r}")
            vertices.append([float(parts[0].replace(",", ".")), float(parts[1].replace(",", "."))])
        return vertices

    def _populate_material_region_table(self, section: InstallationCrossSectionData) -> None:
        self.material_region_table.setRowCount(len(section.material_regions))
        for row, region in enumerate(section.material_regions):
            values = [
                region.region_id, region.name, region.material_id,
                self._vertices_text(region.vertices_m), region.priority,
            ]
            for col, value in enumerate(values):
                self.material_region_table.setItem(row, col, self._item(value))
            self.material_region_table.setItem(row, 5, self._item("", checked=region.active))
        self.material_region_table.resizeColumnsToContents()

    def _populate_heat_table(self, section: InstallationCrossSectionData) -> None:
        self.heat_table.setRowCount(len(section.external_heat_sources))
        for row, source in enumerate(section.external_heat_sources):
            values = [source.source_id, source.name, source.x_m, source.depth_m, source.heat_w_m, source.effective_radius_m]
            for col, value in enumerate(values):
                self.heat_table.setItem(row, col, self._item(value))
            self.heat_table.setItem(row, 6, self._item("", checked=source.active))
        self.heat_table.resizeColumnsToContents()

    def _section_form_changed(self, *_args) -> None:
        if self._loading:
            return
        section = self._section()
        if section is None:
            return
        old_id = section.cross_section_id
        new_id = self.section_id_edit.text().strip() or old_id
        section.cross_section_id = new_id
        section.name = self.section_name_edit.text().strip() or section.name
        new_installation_type = self.section_type_combo.currentText()
        installation_changed = str(section.installation_type).upper() != str(new_installation_type).upper()
        if installation_changed:
            update_channel_geometry_for_installation(section, new_installation_type, reset_dimensions=False)
        else:
            section.installation_type = new_installation_type
        if installation_changed and hasattr(self, "preset_arrangement"):
            # A kurulum-tipi değişikliği geometri olayıdır. Bir sonraki otomatik
            # preset üretiminde eski kanal zarfını taşımak yerine yeni kurulumun
            # kendi parametric defaults'u kullanılır; elektriksel devre kimliği
            # ve tam hassasiyetli akımlar ayrıca korunur.
            self._installation_type_regeneration_pending = True
            current_formation = _formation_code(section.arrangement_label)
            target_arrangement = "CUSTOM" if current_formation in {"DUCT_BANK", "HDD"} else current_formation
            self.preset_arrangement.blockSignals(True)
            target_index = self.preset_arrangement.findData(target_arrangement)
            if target_index >= 0:
                self.preset_arrangement.setCurrentIndex(target_index)
            self.preset_arrangement.blockSignals(False)
            if str(new_installation_type).upper() != THERMAL_INSTALL_DIRECT_BURIED and hasattr(self, "preset_status_label"):
                self.preset_status_label.setText(
                    "<b>Model kapsamı:</b> Bu kurulum tipinde otomatik analitik T4 kullanılmaz. "
                    "Nodal termal çözümü seçin veya kaynaklandırılmış manuel T4 girin."
                )
            self._schedule_layout_regeneration()
        section.region_ids = [v.strip() for v in self.region_ids_edit.text().replace(";", ",").split(",") if v.strip()]
        section.source_reference = self.section_source_edit.text().strip()
        if self.design.active_cross_section_id == old_id:
            self.design.active_cross_section_id = new_id
        self._populate_section_selector()
        self.section_selector.setCurrentIndex(max(0, self.section_selector.findData(new_id)))
        self._refresh_validation()

    def _add_section(self) -> None:
        index = len(self.design.cross_sections) + 1
        section_id = f"ICS-{index:02d}"
        existing = {s.cross_section_id for s in self.design.cross_sections}
        while section_id in existing:
            index += 1
            section_id = f"ICS-{index:02d}"
        section = generate_standard_cross_section(
            cross_section_id=section_id,
            name=f"Fiziksel kesit {index}",
            arrangement="TREFOIL",
            circuit_count=max(1, self.project.design_basis.circuit_count),
            parallel_cables_per_phase=max(1, self.project.cable.parallel_cables_per_phase),
            phase_spacing_m=max(0.01, self.project.design_basis.phase_spacing_m),
            circuit_spacing_m=max(0.01, self.project.design_basis.circuit_spacing_m),
            burial_depth_m=max(0.05, self.project.design_basis.burial_depth_m),
            outer_diameter_m=self.project.cable.overall_diameter_mm / 1000.0,
            circuit_load_currents_a=[self.project.design_basis.design_current_per_circuit_a],
        )
        self.design.cross_sections.append(section)
        self.design.active_cross_section_id = section_id
        self._populate_section_selector()
        self.section_selector.setCurrentIndex(self.section_selector.findData(section_id))

    def _duplicate_section(self) -> None:
        section = self._section()
        if section is None:
            return
        clone = deepcopy(section)
        base = section.cross_section_id + "-COPY"
        new_id = base
        index = 2
        existing = {s.cross_section_id for s in self.design.cross_sections}
        while new_id in existing:
            new_id = f"{base}-{index}"
            index += 1
        clone.cross_section_id = new_id
        clone.name = section.name + " — kopya"
        clone.region_ids = []
        self.design.cross_sections.append(clone)
        self.design.active_cross_section_id = new_id
        self._populate_section_selector()
        self.section_selector.setCurrentIndex(self.section_selector.findData(new_id))

    def _delete_section(self) -> None:
        if len(self.design.cross_sections) <= 1:
            QMessageBox.warning(self, "Kurulum kesiti", "Projede en az bir fiziksel kesit kalmalıdır.")
            return
        section = self._section()
        if section is None:
            return
        if QMessageBox.question(self, "Kurulum kesiti", f"{section.cross_section_id} silinsin mi?") != QMessageBox.Yes:
            return
        self.design.cross_sections.remove(section)
        self.design.active_cross_section_id = self.design.cross_sections[0].cross_section_id
        self._populate_section_selector()
        self._refresh_all()

    @staticmethod
    def _split_values(text: str) -> list[str]:
        return [value.strip() for value in text.replace(";", ",").split(",") if value.strip()]

    def _preset_arrangement_changed(self, *_args) -> None:
        self._update_preset_field_visibility()
        if self._loading:
            return
        normalized = str(self.preset_arrangement.currentData() or "CUSTOM").upper()
        section = self._section()
        if section is None:
            return
        if normalized == "CUSTOM":
            if hasattr(self, "preset_status_label"):
                self.preset_status_label.setText(
                    "<b>Karma/devre bazlı yerleşim:</b> Ayrı devre konumları Devreler sekmesinden uygulanır. "
                    "Tüm devreleri yeniden ortak formasyona almak için TREFOIL, FLAT veya VERTICAL seçin."
                )
            return
        try:
            result = reposition_existing_cables(
                section, normalized,
                burial_depth_m=self.preset_depth.value(),
                phase_spacing_m=self.preset_phase_spacing.value(),
                circuit_spacing_m=self.preset_circuit_spacing.value(),
                parallel_spacing_m=self.preset_parallel_spacing.value(),
                cable_outer_diameter_m=self.project.cable.overall_diameter_mm / 1000.0,
            )
        except (ValueError, InstallationInputError) as exc:
            if hasattr(self, "preset_status_label"):
                self.preset_status_label.setText(f"<b style='color:#a72d2d'>Formasyon uygulanamadı:</b> {exc}")
            return
        if str(section.installation_type).upper() == THERMAL_INSTALL_DIRECT_BURIED:
            self._synchronise_direct_buried_editor(section)
        active = [item for item in section.physical_cables if item.active]
        if active and str(section.installation_type).upper() != THERMAL_INSTALL_DIRECT_BURIED:
            span = max(item.x_m for item in active) - min(item.x_m for item in active)
            section.channel_geometry.trench_width_m = max(
                section.channel_geometry.trench_width_m,
                span + self.project.cable.overall_diameter_mm / 1000.0 + 0.30,
            )
            section.channel_geometry.trench_depth_m = max(
                section.channel_geometry.trench_depth_m,
                max(item.depth_m for item in active) + 0.25,
            )
        self._invalidate_contour(section.cross_section_id)
        self._fit_on_next_draw = False
        warning_text = " ".join(result.warning_messages)
        formation_note = (
            f"TREFOIL faz merkezleri kablo dış çapına (Ø {self.project.cable.overall_diameter_mm:.1f} mm) kilitlendi. "
            if normalized == "TREFOIL" else ""
        )
        if hasattr(self, "preset_status_label"):
            self.preset_status_label.setText(
                f"<b>{_formation_display(normalized)} uygulandı:</b> {result.moved_cable_count} fiziksel kablo yeniden konumlandı. "
                f"{formation_note}{warning_text}"
            )
        self._refresh_all()

    def _layout_structure_changed(self, *_args) -> None:
        self._update_duct_capacity_label()
        self._update_preset_field_visibility()
        if self._loading:
            return
        self._schedule_layout_regeneration()

    def _schedule_layout_regeneration(self) -> None:
        if self._loading or self._layout_regeneration_pending:
            return
        self._layout_regeneration_pending = True
        def apply_later() -> None:
            self._layout_regeneration_pending = False
            if not self._loading:
                self._apply_preset(automatic=True)
        QTimer.singleShot(120, apply_later)

    def _layout_numeric_changed(self, *_args) -> None:
        if self._loading:
            return
        # Spacing and depth are non-destructive: preserve all circuit/phase/
        # parallel identities and redraw immediately. Count changes remain on
        # the explicit apply button because they create/delete physical objects.
        self._preset_arrangement_changed()

    def _apply_preset(self, *_args, automatic: bool = False) -> None:
        section = self._section()
        if section is None:
            return
        if str(self.preset_arrangement.currentData() or "CUSTOM").upper() == "CUSTOM":
            message = (
                "Karma/devre bazlı yerleşimde devre sayısı veya kablo listesi genel şablonla yeniden üretilemez. "
                "Devreler sekmesindeki bağımsız yerleşim alanlarını kullanın veya önce ortak bir formasyon seçin."
            )
            if automatic and hasattr(self, "preset_status_label"):
                self.preset_status_label.setText(f"<b style='color:#a66b00'>{message}</b>")
            else:
                QMessageBox.information(self, "Kablo yerleşimi", message)
            return
        preserved_circuits = deepcopy(section.circuits)
        preserved_physical = {item.physical_cable_id: deepcopy(item) for item in section.physical_cables}
        if automatic:
            # Automatic geometry regeneration may NEVER round-trip electrical
            # currents through a formatted text box. Use the model values at
            # full precision and preserve phase order/identity.
            active_circuits = [item for item in section.circuits if item.active]
            phase_orders = [str(item.phase_order) for item in active_circuits]
            loads = [float(item.load_current_a) for item in active_circuits]
        else:
            phase_orders = self._split_values(self.preset_phase_orders.text())
            try:
                loads = [float(v.replace(",", ".")) for v in self._split_values(self.preset_loads.text())]
            except ValueError:
                message = "Devre akımları sayısal ve virgülle ayrılmış olmalıdır."
                QMessageBox.warning(self, "Hazır yerleşim", message)
                return
        type_change = bool(self._installation_type_regeneration_pending)
        preserved_geometry = None if type_change else deepcopy(section.channel_geometry)
        preserved_regions = deepcopy(section.material_regions)
        preserved_heat_sources = deepcopy(section.external_heat_sources)
        try:
            generated = generate_standard_cross_section(
                cross_section_id=section.cross_section_id,
                name=section.name,
                arrangement=str(self.preset_arrangement.currentData() or "CUSTOM"),
                installation_type=section.installation_type,
                circuit_count=self.preset_circuits.value(),
                parallel_cables_per_phase=self.preset_parallel.value(),
                phase_spacing_m=self.preset_phase_spacing.value(),
                circuit_spacing_m=self.preset_circuit_spacing.value(),
                parallel_group_spacing_m=self.preset_parallel_spacing.value(),
                burial_depth_m=self.preset_depth.value(),
                outer_diameter_m=self.project.cable.overall_diameter_mm / 1000.0,
                phase_orders=phase_orders,
                circuit_load_currents_a=loads,
                region_ids=section.region_ids,
                duct_rows=self.preset_duct_rows.value(),
                duct_columns=self.preset_duct_cols.value(),
            )
        except InstallationInputError as exc:
            if automatic and hasattr(self, "preset_status_label"):
                self.preset_status_label.setText(f"<b style='color:#a72d2d'>{exc}</b>")
            else:
                QMessageBox.warning(self, "Hazır yerleşim", str(exc))
            return
        layout_warnings: tuple[str, ...] = ()
        if str(self.preset_arrangement.currentData() or "CUSTOM") in {"TREFOIL", "FLAT", "VERTICAL"}:
            result = reposition_existing_cables(
                generated, str(self.preset_arrangement.currentData() or "CUSTOM"),
                burial_depth_m=self.preset_depth.value(),
                phase_spacing_m=self.preset_phase_spacing.value(),
                circuit_spacing_m=self.preset_circuit_spacing.value(),
                parallel_spacing_m=self.preset_parallel_spacing.value(),
                cable_outer_diameter_m=self.project.cable.overall_diameter_mm / 1000.0,
            )
            layout_warnings = result.warning_messages
        if preserved_geometry is not None:
            generated.channel_geometry = preserved_geometry
            update_channel_geometry_for_installation(
                generated, generated.installation_type, reset_dimensions=False
            )
        else:
            # generate_standard_cross_section already created geometry defaults
            # for the newly selected installation type. Keep that coherent
            # envelope instead of carrying the previous installation's trench.
            update_channel_geometry_for_installation(
                generated, generated.installation_type, reset_dimensions=False
            )
        generated.material_regions = preserved_regions
        generated.external_heat_sources = preserved_heat_sources

        if automatic:
            prior_by_id = {item.circuit_id: item for item in preserved_circuits}
            for circuit in generated.circuits:
                prior = prior_by_id.get(circuit.circuit_id)
                if prior is not None:
                    circuit.name = prior.name
                    circuit.phase_order = prior.phase_order
                    circuit.load_current_a = prior.load_current_a
                    circuit.load_factor = prior.load_factor
                    circuit.active = prior.active
                    circuit.cable_snapshot_id = prior.cable_snapshot_id
                    circuit.notes = prior.notes
            for cable in generated.physical_cables:
                prior = preserved_physical.get(cable.physical_cable_id)
                if prior is not None:
                    cable.current_override_a = prior.current_override_a
                    cable.current_angle_override_deg = prior.current_angle_override_deg
                    cable.active = prior.active
        if str(generated.installation_type).upper() == THERMAL_INSTALL_DIRECT_BURIED:
            try:
                synchronise_direct_buried_geometry(
                    generated,
                    self.project.cable.overall_diameter_mm / 1000.0,
                )
            except InstallationInputError:
                pass
        if str(generated.installation_type).upper() == THERMAL_INSTALL_DUCT_BANK and generated.duct_slots:
            active_slots = [slot for slot in generated.duct_slots if slot.active]
            if active_slots:
                max_outer = max(float(slot.outer_diameter_m) for slot in active_slots)
                min_x = min(float(slot.x_m) - float(slot.outer_diameter_m) / 2.0 for slot in active_slots)
                max_x = max(float(slot.x_m) + float(slot.outer_diameter_m) / 2.0 for slot in active_slots)
                min_depth = min(float(slot.depth_m) - float(slot.outer_diameter_m) / 2.0 for slot in active_slots)
                max_depth = max(float(slot.depth_m) + float(slot.outer_diameter_m) / 2.0 for slot in active_slots)
                generated.channel_geometry.duct_bank_width_m = max(
                    generated.channel_geometry.duct_bank_width_m,
                    (max_x - min_x) + max(0.15, max_outer * 0.80),
                )
                generated.channel_geometry.duct_bank_height_m = max(
                    generated.channel_geometry.duct_bank_height_m,
                    (max_depth - min_depth) + max(0.15, max_outer * 0.80),
                )
                generated.channel_geometry.trench_width_m = max(
                    generated.channel_geometry.trench_width_m,
                    generated.channel_geometry.duct_bank_width_m + 0.30,
                )
                generated.channel_geometry.trench_depth_m = max(
                    generated.channel_geometry.trench_depth_m,
                    max_depth + generated.channel_geometry.bedding_thickness_m + 0.15,
                )
        generated.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
        generated.source_reference = section.source_reference or "USER_GENERATED_PRESET"
        generated.notes = section.notes
        active = [item for item in generated.physical_cables if item.active]
        if active and str(generated.installation_type).upper() != THERMAL_INSTALL_DIRECT_BURIED:
            span = max(item.x_m for item in active) - min(item.x_m for item in active)
            generated.channel_geometry.trench_width_m = max(
                generated.channel_geometry.trench_width_m,
                span + self.project.cable.overall_diameter_mm / 1000.0 + 0.30,
            )
            generated.channel_geometry.trench_depth_m = max(
                generated.channel_geometry.trench_depth_m,
                max(item.depth_m for item in active) + 0.25,
            )
        index = self.design.cross_sections.index(section)
        self.design.cross_sections[index] = generated
        self._installation_type_regeneration_pending = False
        self._fit_on_next_draw = True
        if hasattr(self, "preset_status_label"):
            warning_text = " ".join(layout_warnings)
            formation_note = (
                f"TREFOIL faz merkezleri kablo dış çapına (Ø {self.project.cable.overall_diameter_mm:.1f} mm) kilitlendi. "
                if generated.arrangement_label == "TREFOIL" else ""
            )
            self.preset_status_label.setText(
                f"<b>{generated.arrangement_label} yerleşimi üretildi:</b> "
                f"{len(generated.physical_cables)} fiziksel kablo. {formation_note}{warning_text}"
            )
        self._refresh_all()

    def _cable_moved(self, physical_id: str, x_m: float, depth_m: float) -> None:
        if self._loading:
            return
        section = self._section()
        if section is None:
            return
        cable = next((item for item in section.physical_cables if item.physical_cable_id == physical_id), None)
        if cable is None:
            return
        # Snap to the nearest active duct centre when the cable is released close
        # enough to fit inside it. This keeps the physical x-y coordinate and
        # the duct-slot assignment as a single authoritative state.
        nearest = None
        nearest_distance = float("inf")
        cable_radius = max(0.001, self.project.cable.overall_diameter_mm / 2000.0)
        for slot in section.duct_slots:
            if not slot.active:
                continue
            distance = ((x_m - slot.x_m) ** 2 + (depth_m - slot.depth_m) ** 2) ** 0.5
            threshold = max(0.04, slot.inner_diameter_m / 2.0 - cable_radius + 0.025)
            if distance <= threshold and distance < nearest_distance:
                nearest = slot
                nearest_distance = distance
        if nearest is not None:
            cable.x_m = float(nearest.x_m)
            cable.depth_m = float(nearest.depth_m)
            cable.duct_slot_id = nearest.slot_id
            graphics_item = self.canvas._items.get(cable.physical_cable_id)
            if graphics_item is not None:
                x_scene, y_scene = self.canvas._scene_xy(cable.x_m, cable.depth_m)
                if abs(graphics_item.pos().x() - x_scene) > 0.5 or abs(graphics_item.pos().y() - y_scene) > 0.5:
                    graphics_item.setPos(x_scene, y_scene)
        else:
            cable.x_m = x_m
            cable.depth_m = depth_m
            cable.duct_slot_id = ""
        section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
        self._invalidate_contour(section.cross_section_id)
        self._loading = True
        try:
            self._populate_cable_table(section)
        finally:
            self._loading = False
        self._refresh_validation()

    def _duct_moved(self, slot_id: str, x_m: float, depth_m: float) -> None:
        if self._loading:
            return
        section = self._section()
        if section is None:
            return
        slot = next((item for item in section.duct_slots if item.slot_id == slot_id), None)
        if slot is None:
            return
        slot.x_m = float(x_m); slot.depth_m = max(0.001, float(depth_m))
        for cable in section.physical_cables:
            if cable.duct_slot_id == slot_id:
                cable.x_m = slot.x_m; cable.depth_m = slot.depth_m
                item = self.canvas._items.get(cable.physical_cable_id)
                if item is not None:
                    x_scene, y_scene = self.canvas._scene_xy(cable.x_m, cable.depth_m)
                    item.setPos(x_scene, y_scene)
        section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
        self._invalidate_contour(section.cross_section_id)
        self._loading = True
        try:
            self._populate_duct_table(section)
            self._populate_cable_table(section)
        finally:
            self._loading = False
        self._refresh_validation()

    def _heat_source_moved(self, source_id: str, x_m: float, depth_m: float) -> None:
        if self._loading:
            return
        section = self._section()
        if section is None:
            return
        source = next((item for item in section.external_heat_sources if item.source_id == source_id), None)
        if source is None:
            return
        source.x_m = float(x_m); source.depth_m = max(0.001, float(depth_m))
        section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
        self._invalidate_contour(section.cross_section_id)
        self._loading = True
        try:
            self._populate_heat_table(section)
        finally:
            self._loading = False
        self._refresh_validation()

    def _material_region_moved(self, region_id: str, dx_m: float, depth_delta_m: float) -> None:
        if self._loading or (abs(dx_m) < 1e-12 and abs(depth_delta_m) < 1e-12):
            return
        section = self._section()
        if section is None:
            return
        region = next((item for item in section.material_regions if item.region_id == region_id), None)
        if region is None:
            return
        region.vertices_m = [
            [float(point[0]) + float(dx_m), max(0.0, float(point[1]) + float(depth_delta_m))]
            for point in region.vertices_m
        ]
        region.source_reference = "USER_INTERACTIVE_GEOMETRY"
        section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
        self._invalidate_contour(section.cross_section_id)
        self._loading = True
        try:
            self._populate_material_region_table(section)
        finally:
            self._loading = False
        self._refresh_validation()

    @staticmethod
    def _text(table: QTableWidget, row: int, col: int) -> str:
        item = table.item(row, col)
        return item.text().strip() if item is not None else ""

    @staticmethod
    def _checked(table: QTableWidget, row: int, col: int) -> bool:
        item = table.item(row, col)
        return bool(item is not None and item.checkState() == Qt.Checked)

    @staticmethod
    def _float(value: str, default: float = 0.0) -> float:
        value = value.strip()
        if not value:
            return default
        return float(value.replace(",", "."))

    def _table_changed(self, *_args) -> None:
        if self._loading:
            return
        section = self._section()
        if section is None:
            return
        try:
            self._read_tables(section)
            if self.sender() in {self.cable_table, self.duct_table, self.material_region_table, self.heat_table}:
                section.channel_geometry.source_reference = "USER_INTERACTIVE_GEOMETRY"
            self._invalidate_contour(section.cross_section_id)
        except (ValueError, InstallationInputError) as exc:
            self.validation_label.setText(f"<b>Tablo değeri okunamadı:</b> {exc}")
            self.validation_label.setStyleSheet("color:#a72d2d; padding:5px;")
            return
        self._draw_canvas(section)
        self._refresh_validation()

    def _read_tables(self, section: InstallationCrossSectionData) -> None:
        existing_circuits = {item.circuit_id: item for item in section.circuits}
        existing_cables = {item.physical_cable_id: item for item in section.physical_cables}
        existing_ducts = {item.slot_id: item for item in section.duct_slots}
        existing_regions = {item.region_id: item for item in section.material_regions}
        existing_heat = {item.source_id: item for item in section.external_heat_sources}

        circuits: list[InstallationCircuitData] = []
        for row in range(self.circuit_table.rowCount()):
            circuit_id = self._text(self.circuit_table, row, 0)
            previous = existing_circuits.get(circuit_id)
            circuits.append(InstallationCircuitData(
                circuit_id=circuit_id,
                name=self._text(self.circuit_table, row, 1),
                phase_order=self._text(self.circuit_table, row, 2).upper(),
                load_current_a=self._float(self._text(self.circuit_table, row, 3)),
                load_factor=self._float(self._text(self.circuit_table, row, 4), 1.0),
                active=self._checked(self.circuit_table, row, 5),
                cable_snapshot_id=(previous.cable_snapshot_id if previous else ""),
                notes=(previous.notes if previous else ""),
            ))
        cables: list[PhysicalCableData] = []
        for row in range(self.cable_table.rowCount()):
            physical_id = self._text(self.cable_table, row, 0)
            previous = existing_cables.get(physical_id)
            angle_text = self._text(self.cable_table, row, 8)
            cables.append(PhysicalCableData(
                physical_cable_id=physical_id,
                circuit_id=self._text(self.cable_table, row, 1),
                phase=self._text(self.cable_table, row, 2).upper(),
                parallel_index=int(self._float(self._text(self.cable_table, row, 3), 1.0)),
                x_m=self._float(self._text(self.cable_table, row, 4)),
                depth_m=self._float(self._text(self.cable_table, row, 5)),
                cable_snapshot_id=(previous.cable_snapshot_id if previous else ""),
                duct_slot_id=self._text(self.cable_table, row, 6),
                rotation_deg=(previous.rotation_deg if previous else 0.0),
                current_override_a=self._float(self._text(self.cable_table, row, 7)),
                current_angle_override_deg=(self._float(angle_text) if angle_text else None),
                load_factor=self._float(self._text(self.cable_table, row, 9), 1.0),
                active=self._checked(self.cable_table, row, 10),
                notes=(previous.notes if previous else ""),
            ))
        ducts: list[DuctSlotData] = []
        for row in range(self.duct_table.rowCount()):
            slot_id = self._text(self.duct_table, row, 0)
            previous = existing_ducts.get(slot_id)
            ducts.append(DuctSlotData(
                slot_id=slot_id,
                x_m=self._float(self._text(self.duct_table, row, 1)),
                depth_m=self._float(self._text(self.duct_table, row, 2)),
                inner_diameter_m=self._float(self._text(self.duct_table, row, 3), 0.13),
                outer_diameter_m=self._float(self._text(self.duct_table, row, 4), 0.16),
                row_index=int(self._float(self._text(self.duct_table, row, 5), 1)),
                column_index=int(self._float(self._text(self.duct_table, row, 6), 1)),
                active=self._checked(self.duct_table, row, 7),
                notes=(previous.notes if previous else ""),
            ))
        material_regions: list[ThermalMaterialRegionData] = []
        for row in range(self.material_region_table.rowCount()):
            region_id = self._text(self.material_region_table, row, 0)
            previous = existing_regions.get(region_id)
            material_regions.append(ThermalMaterialRegionData(
                region_id=region_id,
                name=self._text(self.material_region_table, row, 1),
                material_id=self._text(self.material_region_table, row, 2),
                vertices_m=self._parse_vertices(self._text(self.material_region_table, row, 3)),
                priority=int(self._float(self._text(self.material_region_table, row, 4), 100)),
                active=self._checked(self.material_region_table, row, 5),
                role=(previous.role if previous else "CUSTOM_THERMAL_REGION"),
                source_reference=(previous.source_reference if previous else "USER_INTERACTIVE_GEOMETRY"),
                notes=(previous.notes if previous else ""),
            ))
        heat: list[ExternalHeatSourceData] = []
        for row in range(self.heat_table.rowCount()):
            source_id = self._text(self.heat_table, row, 0)
            previous = existing_heat.get(source_id)
            heat.append(ExternalHeatSourceData(
                source_id=source_id,
                name=self._text(self.heat_table, row, 1),
                x_m=self._float(self._text(self.heat_table, row, 2)),
                depth_m=self._float(self._text(self.heat_table, row, 3)),
                heat_w_m=self._float(self._text(self.heat_table, row, 4)),
                effective_radius_m=self._float(self._text(self.heat_table, row, 5), 0.05),
                active=self._checked(self.heat_table, row, 6),
                source_type=(previous.source_type if previous else "OTHER_CABLE_OR_PIPE"),
                notes=(previous.notes if previous else ""),
            ))
        section.circuits = circuits
        section.physical_cables = cables
        section.duct_slots = ducts
        section.material_regions = material_regions
        section.external_heat_sources = heat

        trefoil_circuits: list[str] = []
        for row in range(self.circuit_table.rowCount()):
            formation = self.circuit_table.cellWidget(row, 6)
            if isinstance(formation, QComboBox) and str(formation.currentData() or "").upper() == "TREFOIL":
                trefoil_circuits.append(self._text(self.circuit_table, row, 0))
        if trefoil_circuits:
            lock_trefoil_centres_to_outer_diameter(
                section,
                max(float(self.project.cable.overall_diameter_mm) / 1000.0, 0.001),
                trefoil_circuits,
            )

    def _add_table_row(self, table: QTableWidget) -> None:
        section = self._section()
        if section is None:
            return
        if table is self.circuit_table:
            index = len(section.circuits) + 1
            section.circuits.append(InstallationCircuitData(f"C{index}", f"Devre {index}", "ABC", 0.0))
        elif table is self.cable_table:
            index = len(section.physical_cables) + 1
            circuit_id = section.circuits[0].circuit_id if section.circuits else "C1"
            section.physical_cables.append(PhysicalCableData(f"PC-{index:02d}", circuit_id, "A", 1, 0.0, 1.2))
        elif table is self.duct_table:
            index = len(section.duct_slots) + 1
            section.duct_slots.append(DuctSlotData(f"DS-{index:02d}", 0.0, 1.2, row_index=index, column_index=1))
        elif table is self.material_region_table:
            index = len(section.material_regions) + 1
            g = section.channel_geometry
            top = max(0.0, g.trench_depth_m - g.bedding_thickness_m - g.thermal_backfill_height_m)
            bottom = max(top + 0.05, g.trench_depth_m - g.bedding_thickness_m)
            left = g.center_x_m - min(g.trench_width_m * 0.40, 0.40)
            right = g.center_x_m + min(g.trench_width_m * 0.40, 0.40)
            material_id = g.thermal_backfill_material_id or g.native_soil_material_id
            section.material_regions.append(ThermalMaterialRegionData(
                f"MR-{index:02d}", f"Özel termal bölge {index}", material_id,
                [[left, top], [right, top], [right, bottom], [left, bottom]],
                priority=100 + index,
            ))
        elif table is self.heat_table:
            index = len(section.external_heat_sources) + 1
            section.external_heat_sources.append(ExternalHeatSourceData(f"HS-{index:02d}", f"Harici kaynak {index}", 0.0, 1.2))
        self._refresh_all()

    def _delete_table_rows(self, table: QTableWidget) -> None:
        section = self._section()
        if section is None:
            return
        rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        target = (
            section.circuits if table is self.circuit_table else
            section.physical_cables if table is self.cable_table else
            section.duct_slots if table is self.duct_table else
            section.material_regions if table is self.material_region_table else
            section.external_heat_sources
        )
        for row in rows:
            if 0 <= row < len(target):
                target.pop(row)
        self._refresh_all()

    def _installation_visual_integrity_findings(self, section: InstallationCrossSectionData) -> list[tuple[str, str]]:
        """UI/data-integrity checks for installation-specific containment.

        These checks do not alter any physics solver. They prevent an incomplete
        physical section from being presented as a coherent engineering drawing.
        """
        findings: list[tuple[str, str]] = []
        kind = str(section.installation_type).upper()
        active_cables = [item for item in section.physical_cables if item.active]
        cable_radius = max(float(self.project.cable.overall_diameter_mm) / 2000.0, 0.0005)
        g = section.channel_geometry

        if kind == THERMAL_INSTALL_DUCT_BANK:
            slots = {item.slot_id: item for item in section.duct_slots if item.active}
            if active_cables and not slots:
                findings.append(("ERROR", "DUCT_LAYOUT_MISSING: Aktif kablolar var ancak aktif duct slot geometrisi yok. Hazır yerleşimi uygulayın veya duct slotlarını tanımlayın."))
            for cable in active_cables:
                slot_id = str(cable.duct_slot_id or "")
                slot = slots.get(slot_id)
                if slot is None:
                    findings.append(("ERROR", f"DUCT_ASSIGNMENT_MISSING: {cable.physical_cable_id} aktif bir duct slotuna bağlı değil."))
                    continue
                centre_error = ((float(cable.x_m) - float(slot.x_m)) ** 2 + (float(cable.depth_m) - float(slot.depth_m)) ** 2) ** 0.5
                if centre_error > 1e-5:
                    findings.append(("ERROR", f"DUCT_CABLE_COORDINATE_MISMATCH: {cable.physical_cable_id} ile {slot.slot_id} merkezleri {centre_error:.6f} m farklı."))
                if cable_radius >= float(slot.inner_diameter_m) / 2.0:
                    findings.append(("ERROR", f"CABLE_TOO_LARGE_FOR_DUCT: {cable.physical_cable_id} dış çapı {slot.slot_id} iç çapına sığmıyor."))

        elif kind == THERMAL_INSTALL_CONCRETE_TROUGH and active_cables:
            wall = max(0.0, float(g.trough_wall_thickness_m))
            iw = max(0.0, float(g.trough_inner_width_m))
            ih = max(0.0, float(g.trough_inner_height_m))
            outer_h = ih + 2.0 * wall
            trough_bottom = min(
                float(g.trench_depth_m),
                max(float(item.depth_m) for item in active_cables) + cable_radius + wall,
            )
            trough_top = max(0.0, trough_bottom - outer_h)
            inner_top = trough_top + wall
            inner_bottom = trough_bottom - wall
            left = float(g.center_x_m) - iw / 2.0
            right = float(g.center_x_m) + iw / 2.0
            for cable in active_cables:
                if not (
                    float(cable.x_m) - cable_radius >= left - 1e-9
                    and float(cable.x_m) + cable_radius <= right + 1e-9
                    and float(cable.depth_m) - cable_radius >= inner_top - 1e-9
                    and float(cable.depth_m) + cable_radius <= inner_bottom + 1e-9
                ):
                    findings.append(("ERROR", f"CABLE_INTERSECTS_TROUGH_WALL: {cable.physical_cable_id} beton kanalın iç boşluğunda değil."))

        elif kind == THERMAL_INSTALL_HDD and active_cables:
            centre_x = float(g.center_x_m)
            centre_depth = sum(float(item.depth_m) for item in active_cables) / len(active_cables)
            bore_radius = max(0.0, float(g.hdd_bore_diameter_m)) / 2.0
            for cable in active_cables:
                radial = ((float(cable.x_m) - centre_x) ** 2 + (float(cable.depth_m) - centre_depth) ** 2) ** 0.5
                if radial + cable_radius > bore_radius + 1e-9:
                    findings.append(("ERROR", f"CABLE_OUTSIDE_HDD_BORE: {cable.physical_cable_id} HDD zarfının dışında; bore çapı/yerleşim yeniden tanımlanmalı."))
        return findings

    def _refresh_validation(self) -> None:
        issues = validate_installation_design(
            self.design,
            cable_outer_diameter_m=self.project.cable.overall_diameter_mm / 1000.0,
        )
        errors = [item for item in issues if item.severity == "ERROR"]
        warnings = [item for item in issues if item.severity == "WARNING"]
        section = self._section()
        visual_findings = self._installation_visual_integrity_findings(section) if section is not None else []
        visual_errors = [message for severity, message in visual_findings if severity == "ERROR"]
        visual_warnings = [message for severity, message in visual_findings if severity == "WARNING"]
        resolved_count = 0
        max_current = 0.0
        if section is not None:
            try:
                resolved = resolved_physical_cables(section)
                resolved_count = len(resolved)
                max_current = max((item.current_a for item in resolved), default=0.0)
            except InstallationInputError:
                pass
        material_issues = []
        if section is not None:
            material_map = {item.material_id: item for item in self.project.thermal_design.materials}
            ids = {
                section.channel_geometry.native_soil_material_id,
                section.channel_geometry.bedding_material_id,
                section.channel_geometry.thermal_backfill_material_id,
                section.channel_geometry.selected_fill_material_id,
                section.channel_geometry.general_fill_material_id,
                section.channel_geometry.surface_material_id,
                section.channel_geometry.cover_slab_material_id,
                section.channel_geometry.duct_bank_material_id,
                section.channel_geometry.trough_material_id,
                section.channel_geometry.hdd_grout_material_id,
                *(item.material_id for item in section.material_regions if item.active),
            } - {""}
            for material_id in ids:
                material = material_map.get(material_id)
                if material is not None:
                    material_issues.extend(validate_material_for_final_design(material))
        details = "<br>".join(
            [f"• {item.message}" for item in issues[:4]]
            + [f"• {message}" for message in (visual_errors + visual_warnings)[:4]]
            + [f"• {item.message}" for item in material_issues[:2]]
        )
        total_errors = len(errors) + len(visual_errors)
        total_warnings = len(warnings) + len(visual_warnings)
        self.validation_label.setText(
            f"<b>Doğrulama:</b> {total_errors} hata / {total_warnings} uyarı · "
            f"{resolved_count} etkin fiziksel kablo · en yüksek atanmış akım {max_current:.2f} A · "
            f"solver_coupling_mode={self.design.solver_coupling_mode}"
            + (f"<br>{details}" if details else "")
            + "<br><i>v0.16.9.4.14: TREFOIL faz merkezleri gerçek kablo dış çapından otomatik üretilir; tablo koordinat yuvarlaması temas demetini çakışmaya çeviremez. Kaydedilen fiziksel x-y ve kanal katmanları üretim hesaplarına bağlanır; Geometri kaydı mevcut sonuçları geçersiz kılar ve yeniden hesap ister.</i>"
        )
        self.validation_label.setStyleSheet(
            "color:#a72d2d; padding:5px;" if total_errors else
            "color:#8a5a00; padding:5px;" if total_warnings else
            "color:#24613b; padding:5px;"
        )

    def _save(self) -> None:
        section = self._section()
        if section is not None:
            try:
                self._read_tables(section)
                if str(section.installation_type).upper() == THERMAL_INSTALL_DIRECT_BURIED:
                    synchronise_direct_buried_geometry(
                        section,
                        self.project.cable.overall_diameter_mm / 1000.0,
                    )
            except (ValueError, InstallationInputError) as exc:
                QMessageBox.warning(self, "Kurulum ve kesit", f"Tablolarda/geometride geçersiz değer var:\n{exc}")
                return
        issues = validate_installation_design(
            self.design,
            cable_outer_diameter_m=self.project.cable.overall_diameter_mm / 1000.0,
        )
        errors = [item for item in issues if item.severity == "ERROR"]
        if errors:
            answer = QMessageBox.question(
                self,
                "Kurulum doğrulaması",
                f"Fiziksel kesitte {len(errors)} doğrulama hatası var. Taslak olarak yine de kaydedilsin mi?",
            )
            if answer != QMessageBox.Yes:
                return
        self.design.solver_coupling_mode = INSTALLATION_COUPLING_PRODUCTION_LINKED
        self.design.model_revision = "0.16.9.4.14"
        self.project.installation_design = deepcopy(self.design)
        # Close the designer before the main-window recalculation question is
        # shown; otherwise the still-modal editor can obscure the prompt.
        self.accept()
        if self.on_change is not None:
            self.on_change()
