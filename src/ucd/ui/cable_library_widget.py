from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
import json
from math import cos, pi, sin
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ucd.calculations.cable_library import (
    CableLibraryInputError,
    apply_catalog_record,
    cable_from_dict,
    catalog_package_from_dict,
    catalog_package_to_dict,
    catalog_record_from_cable,
    filter_catalog_records,
    merge_catalog_library,
    merge_builtin_catalogs,
    synchronize_cable_from_layers,
    update_cable_validation_state,
)
from ucd.calculations.cable_selection import (
    CatalogCandidateEvaluation,
    evaluate_catalog_candidates,
)
from ucd.fileio import atomic_write_text
from ucd.ui.cable_application_dialog import CableApplicationDialog
from ucd.ui.catalog_comparison_dialog import CatalogComparisonDialog
from ucd.ui.graphics_views import ZoomPanGraphicsView

from ucd.models.project import (
    CABLE_SOURCE_CATALOG,
    CABLE_SOURCE_MANUFACTURER_DRAWING,
    CABLE_SOURCE_TEST_REPORT,
    CABLE_SOURCE_USER_ASSUMPTION,
    CABLE_STATUS_DRAFT,
    CableData,
    CableLayerData,
    CableParameterSource,
    ProjectData,
    default_cable_layers,
    default_cable_sources,
)
from .window_layout import fit_window, DENSITY_COMPACT, DENSITY_NORMAL


_LAYER_COLORS = {
    "CONDUCTOR": "#c77b30",
    "CONDUCTOR_SCREEN": "#30363d",
    "INSULATION": "#ece6c8",
    "INSULATION_SCREEN": "#4c555e",
    "WATER_BLOCKING": "#7ca6c9",
    "METALLIC_SCREEN": "#d08a3e",
    "METALLIC_SHEATH": "#b8c2cc",
    "WIRE_SCREEN": "#d08a3e",
    "BEDDING": "#9a8d80",
    "ARMOUR": "#7c858d",
    "OUTER_SHEATH": "#1f2933",
    "JACKET": "#1f2933",
}


class CableCrossSectionView(ZoomPanGraphicsView):
    def __init__(self) -> None:
        self.scene_obj = QGraphicsScene()
        super().__init__(self.scene_obj)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setBackgroundBrush(QBrush(QColor("#f7fafc")))

    def draw_cable(self, cable: CableData) -> None:
        self.scene_obj.clear()
        layers = sorted(cable.layers, key=lambda item: item.outer_diameter_mm, reverse=True)
        if not layers:
            self.scene_obj.addText("Kablo katmanı bulunmuyor.")
            return
        maximum = max(layer.outer_diameter_mm for layer in layers)
        scale = 340.0 / max(maximum, 1.0)
        center_x = 0.0
        center_y = 0.0
        for layer in layers:
            diameter = layer.outer_diameter_mm * scale
            color = QColor(_LAYER_COLORS.get(layer.layer_type.upper(), "#b7c5d3"))
            pen = QPen(QColor("#506070"), 1.0)
            brush = QBrush(color)
            self.scene_obj.addEllipse(
                center_x - diameter / 2.0,
                center_y - diameter / 2.0,
                diameter,
                diameter,
                pen,
                brush,
            )

        conductor = next((layer for layer in cable.layers if layer.layer_type.upper() == "CONDUCTOR"), None)
        if conductor is not None and cable.conductor_segment_count > 1:
            radius = conductor.outer_diameter_mm * scale / 2.0
            for index in range(max(1, cable.conductor_segment_count)):
                angle = 2.0 * pi * index / max(1, cable.conductor_segment_count)
                self.scene_obj.addLine(
                    center_x,
                    center_y,
                    center_x + radius * cos(angle),
                    center_y + radius * sin(angle),
                    QPen(QColor("#7d481e"), 1.2),
                )

        screen = next(
            (
                layer for layer in cable.layers
                if layer.layer_type.upper() in {"METALLIC_SCREEN", "METALLIC_SHEATH", "WIRE_SCREEN"}
            ),
            None,
        )
        if screen is not None and screen.wire_count > 0 and screen.wire_diameter_mm > 0:
            count = min(screen.wire_count, 72)
            orbit = 0.25 * (screen.inner_diameter_mm + screen.outer_diameter_mm) * scale
            wire_d = max(2.0, screen.wire_diameter_mm * scale)
            for index in range(count):
                angle = 2.0 * pi * index / count
                x = center_x + orbit * cos(angle)
                y = center_y + orbit * sin(angle)
                self.scene_obj.addEllipse(
                    x - wire_d / 2.0,
                    y - wire_d / 2.0,
                    wire_d,
                    wire_d,
                    QPen(QColor("#7b4b1d"), 0.7),
                    QBrush(QColor("#d8964b")),
                )

        title = self.scene_obj.addSimpleText(
            f"{cable.manufacturer or 'Manuel'} · {cable.model or cable.name}\n"
            f"{cable.conductor_material} {cable.conductor_area_mm2:g} mm² · Ø {maximum:.1f} mm",
            QFont("Segoe UI", 10, 600),
        )
        title.setPos(-205, -225)

        legend_y = -190.0
        seen: set[str] = set()
        for layer in sorted(cable.layers, key=lambda item: item.outer_diameter_mm):
            if layer.layer_type in seen:
                continue
            seen.add(layer.layer_type)
            color = QColor(_LAYER_COLORS.get(layer.layer_type.upper(), "#b7c5d3"))
            self.scene_obj.addRect(-205, legend_y, 10, 10, QPen(Qt.NoPen), QBrush(color))
            label = self.scene_obj.addSimpleText(layer.name, QFont("Segoe UI", 8))
            label.setPos(-190, legend_y - 4)
            legend_y += 15

        self.scene_obj.setSceneRect(-230, -240, 460, 480)
        self.set_fit_bounds(self.scene_obj.sceneRect())


class CatalogReferenceConditionsDialog(QDialog):
    """Edit catalog Iref and its reference-condition / correction-factor evidence."""

    def __init__(self, record, parent=None) -> None:
        super().__init__(parent)
        self.record = record
        self.setWindowTitle("Katalog Iref ve Referans Koşulları")
        fit_window(self, DENSITY_NORMAL)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Katalog Iref yalnız yayımlandığı koşullarda geçerlidir. DiTuS paketinde lisanslı IEC/national "
            "düzeltme tabloları bulunmaz. Proje koşuluna dönüşüm için her farklı parametreye açık, kaynaklı "
            "bir düzeltme faktörü girilmelidir. LF≠1 katalog rating'i IEC 60853 çevrimsel kapsamıdır ve burada "
            "skaler IEC 60287 faktörü gibi kullanılmaz."
        )
        note.setWordWrap(True)
        note.setStyleSheet("background:#fff4cc;color:#725b18;padding:8px;")
        layout.addWidget(note)

        form = QFormLayout()
        electrical = record.catalog_electrical or {}
        conditions = record.reference_conditions or {}
        self.amp_trefoil = QDoubleSpinBox(); self.amp_trefoil.setRange(0, 10000); self.amp_trefoil.setDecimals(3); self.amp_trefoil.setSuffix(" A")
        self.amp_flat = QDoubleSpinBox(); self.amp_flat.setRange(0, 10000); self.amp_flat.setDecimals(3); self.amp_flat.setSuffix(" A")
        self.soil_temp = QDoubleSpinBox(); self.soil_temp.setRange(-50, 100); self.soil_temp.setDecimals(2); self.soil_temp.setSuffix(" °C")
        self.depth = QDoubleSpinBox(); self.depth.setRange(0, 20); self.depth.setDecimals(3); self.depth.setSuffix(" m")
        self.rho = QDoubleSpinBox(); self.rho.setRange(0, 20); self.rho.setDecimals(3); self.rho.setSuffix(" K·m/W")
        self.load_factor = QDoubleSpinBox(); self.load_factor.setRange(0, 2); self.load_factor.setDecimals(4)
        self.cpp = QDoubleSpinBox(); self.cpp.setRange(1, 20); self.cpp.setDecimals(0)
        self.arrangement = QComboBox(); self.arrangement.addItems(["TREFOIL", "FLAT", "VERTICAL", "CUSTOM", "UNKNOWN"])
        self.installation = QComboBox(); self.installation.addItems(["DIRECT_BURIED", "DUCT_BANK", "HDD", "CONCRETE_TROUGH", "TUNNEL", "AIR", "UNKNOWN"])
        self.amp_trefoil.setValue(float(electrical.get("ampacity_ground_trefoil_a", 0.0) or 0.0))
        self.amp_flat.setValue(float(electrical.get("ampacity_ground_flat_a", 0.0) or 0.0))
        self.soil_temp.setValue(float(conditions.get("soil_temperature_c", 20.0) or 20.0))
        self.depth.setValue(float(conditions.get("burial_depth_m", 0.7) or 0.7))
        self.rho.setValue(float(conditions.get("soil_thermal_resistivity_km_w", 1.0) or 1.0))
        self.load_factor.setValue(float(conditions.get("load_factor", 1.0) or 1.0))
        self.cpp.setValue(float(conditions.get("cables_per_phase", 1) or 1))
        self.arrangement.setCurrentText(str(conditions.get("arrangement", "TREFOIL")).upper())
        self.installation.setCurrentText(str(conditions.get("installation_method", "DIRECT_BURIED")).upper())
        form.addRow("Iref — toprak/trefoil", self.amp_trefoil)
        form.addRow("Iref — toprak/flat", self.amp_flat)
        form.addRow("Referans toprak sıcaklığı", self.soil_temp)
        form.addRow("Referans gömülme derinliği", self.depth)
        form.addRow("Referans toprak ρth", self.rho)
        form.addRow("Referans yük faktörü", self.load_factor)
        form.addRow("Referans kablo/faz", self.cpp)
        form.addRow("Referans formasyon", self.arrangement)
        form.addRow("Referans kurulum", self.installation)
        layout.addLayout(form)

        factor_label = QLabel(
            "Düzeltme faktörleri — JSON liste. Her kayıt: factor_id, parameter, reference_value, target_value, "
            "factor, source_type, source_reference. Sayısal interpolasyon yapılmaz; hedef değer açıkça eşleşmelidir."
        )
        factor_label.setWordWrap(True); layout.addWidget(factor_label)
        self.factors = QPlainTextEdit()
        self.factors.setPlaceholderText(
            '[\n  {"factor_id":"K-RHO-1","parameter":"soil_thermal_resistivity_km_w",'
            '"reference_value":1.0,"target_value":1.2,"factor":0.95,'
            '"source_type":"LICENSED_STANDARD_USER_ENTRY","source_reference":"IEC ... kullanıcı lisanslı tablo girdisi"}\n]'
        )
        self.factors.setPlainText(json.dumps(conditions.get("correction_factors", []), ensure_ascii=False, indent=2))
        layout.addWidget(self.factors, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        try:
            factors = json.loads(self.factors.toPlainText() or "[]")
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "Düzeltme faktörleri", f"JSON geçersiz: {exc}")
            return
        if not isinstance(factors, (list, dict)):
            QMessageBox.warning(self, "Düzeltme faktörleri", "correction_factors bir JSON liste veya nesne olmalıdır.")
            return
        self.record.catalog_electrical["ampacity_ground_trefoil_a"] = float(self.amp_trefoil.value())
        self.record.catalog_electrical["ampacity_ground_flat_a"] = float(self.amp_flat.value())
        self.record.reference_conditions.update({
            "soil_temperature_c": float(self.soil_temp.value()),
            "burial_depth_m": float(self.depth.value()),
            "soil_thermal_resistivity_km_w": float(self.rho.value()),
            "load_factor": float(self.load_factor.value()),
            "cables_per_phase": int(self.cpp.value()),
            "arrangement": self.arrangement.currentText(),
            "installation_method": self.installation.currentText(),
            "correction_factors": factors,
        })
        self.accept()


class CableLibraryWidget(QWidget):
    """Full engineering workspace for catalog records and cable construction."""

    def __init__(
        self,
        project: ProjectData,
        on_project_cable_changed: Callable[[], None] | None = None,
        parent: QWidget | None = None,
        database_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.on_project_cable_changed = on_project_cable_changed
        self.database_mode = database_mode
        merge_builtin_catalogs(self.project.cable_library)
        self.working_cable = cable_from_dict(asdict(project.cable))
        self._loading = False
        self._filtered_records = []
        self._candidate_evaluations: list[CatalogCandidateEvaluation] = []
        # The candidate tab is project-only.  Keep its first widget explicit and
        # optional so database mode can run the common refresh path safely.
        self.candidate_basis_label: QLabel | None = None
        self._build_ui()
        self.refresh()

    def set_project(self, project: ProjectData) -> None:
        self.project = project
        merge_builtin_catalogs(self.project.cable_library)
        self.working_cable = cable_from_dict(asdict(project.cable))
        self._candidate_evaluations = []
        self.refresh()

    def begin_new_manual_cable(self) -> None:
        """Start a fresh project-local cable definition in the full editor."""
        self._new_manual_cable()
        if hasattr(self, "builder_tabs"):
            # In project mode the parametric tab follows the candidate tab.
            self.builder_tabs.setCurrentIndex(0 if self.database_mode else 1)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_text = "Kablo Veri Tabanı" if self.database_mode else "Proje Kablo Editörü"
        title = QLabel(f"DiTuS Kablo Analizör™ — {title_text}")
        title.setStyleSheet("font-size: 15pt; font-weight: 750; color: #183b56;")
        motto = QLabel("İşler karışmadan vaziyet alın")
        motto.setStyleSheet("color: #5a6b7a; font-style: italic;")
        title_box.addWidget(title)
        title_box.addWidget(motto)
        header.addLayout(title_box, 1)
        self.active_label = QLabel()
        self.active_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.active_label.setStyleSheet("font-weight: 650; padding: 7px; background: #e8f0f7; border-radius: 4px;")
        header.addWidget(self.active_label)
        root.addLayout(header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_catalog_panel())
        splitter.addWidget(self._build_builder_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([500, 980])
        root.addWidget(splitter, 1)

    def _build_catalog_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 4, 0)

        filter_group = QGroupBox("Katalog filtresi")
        filter_form = QFormLayout(filter_group)
        self.filter_text = QLineEdit()
        self.filter_text.setPlaceholderText("Üretici, model, etiket veya kayıt ID")
        self.filter_manufacturer = QComboBox()
        self.filter_voltage = QComboBox()
        self.filter_material = QComboBox()
        self.filter_material.addItems(["Tümü", "Cu", "Al"])
        self.filter_area = QDoubleSpinBox()
        self.filter_area.setRange(0, 10000)
        self.filter_area.setSuffix(" mm² min")
        for widget in (self.filter_text, self.filter_manufacturer, self.filter_voltage, self.filter_material, self.filter_area):
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._refresh_catalog_table)
            elif isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(self._refresh_catalog_table)
            else:
                widget.valueChanged.connect(self._refresh_catalog_table)
        filter_form.addRow("Ara", self.filter_text)
        filter_form.addRow("Üretici", self.filter_manufacturer)
        filter_form.addRow("Gerilim sınıfı", self.filter_voltage)
        filter_form.addRow("İletken", self.filter_material)
        filter_form.addRow("Minimum kesit", self.filter_area)
        layout.addWidget(filter_group)

        self.catalog_table = QTableWidget(0, 7)
        self.catalog_table.setHorizontalHeaderLabels(
            ["ID", "Üretici", "Seri / model", "Gerilim", "İletken", "Kesit", "Durum"]
        )
        self.catalog_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.catalog_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.catalog_table.setAlternatingRowColors(True)
        self.catalog_table.itemSelectionChanged.connect(self._catalog_selection_changed)
        self.catalog_table.itemDoubleClicked.connect(lambda *_: self._apply_selected_catalog_record())
        self.catalog_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.catalog_table, 1)

        buttons = QHBoxLayout()
        for text, slot in (
            ("Paketi İçe Al", self._import_catalog),
            ("Paketi Dışa Ver", self._export_catalog),
            ("Katalog Bağlantıları", self._show_catalog_sources),
            ("Iref / Koşullar", self._edit_catalog_reference_conditions),
            ("Manuel Yeni", self._new_manual_cable),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.catalog_info = QPlainTextEdit()
        self.catalog_info.setReadOnly(True)
        self.catalog_info.setMaximumHeight(130)
        layout.addWidget(self.catalog_info)
        return panel

    def _build_builder_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 0, 0)
        tabs = QTabWidget()
        self.builder_tabs = tabs
        if not self.database_mode:
            tabs.addTab(self._build_candidate_tab(), "Proje Adayları")
        tabs.addTab(self._build_parametric_tab(), "Parametrik Kesit")
        tabs.addTab(self._build_sources_tab(), "Girdiler ve Kaynaklar")
        tabs.addTab(self._build_validation_tab(), "Doğrulama ve Hazırlık")
        layout.addWidget(tabs, 1)

        actions = QHBoxLayout()
        self.sync_button = QPushButton("Katmanlardan Hesapla")
        self.sync_button.clicked.connect(self._sync_from_layers)
        self.apply_button = QPushButton("Projeye Ata")
        self.apply_button.clicked.connect(self._apply_working_to_project)
        self.save_record_button = QPushButton("Kütüphaneye Yeni Kayıt")
        self.save_record_button.clicked.connect(self._save_working_to_library)
        actions.addWidget(self.sync_button)
        actions.addStretch(1)
        actions.addWidget(self.save_record_button)
        if not self.database_mode:
            actions.addWidget(self.apply_button)
        layout.addLayout(actions)
        return panel


    def _build_candidate_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.candidate_basis_label = QLabel()
        self.candidate_basis_label.setWordWrap(True)
        self.candidate_basis_label.setStyleSheet(
            "font-size: 10.5pt; font-weight: 650; padding: 8px; "
            "background: #eaf0f5; border-radius: 4px;"
        )
        layout.addWidget(self.candidate_basis_label)

        notice = QLabel(
            "Katalog akım taşıma kapasitesi yalnız katalogdaki referans koşullarda ön eleme değeridir. "
            "Projeye atanan aday IEC 60287, 2D termal, bonding ve arıza hesaplarıyla ayrıca doğrulanır."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #725b18; background: #fff7d6; padding: 7px;")
        layout.addWidget(notice)

        self.candidate_table = QTableWidget(0, 15)
        self.candidate_table.setHorizontalHeaderLabels([
            "Sıra", "Üretici", "Model", "Gerilim", "İletken", "Kablo/faz",
            "Katalog Iref", "Aritmetik toplam", "Normalize Iref", "Tasarım I", "Norm. marj", "ΔV", "Veri", "Hüküm", "Kaynak koşulu",
        ])
        self.candidate_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.candidate_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.candidate_table.setAlternatingRowColors(True)
        self.candidate_table.itemSelectionChanged.connect(self._candidate_selection_changed)
        self.candidate_table.itemDoubleClicked.connect(lambda *_: self._load_selected_candidate())
        layout.addWidget(self.candidate_table, 1)

        actions = QHBoxLayout()
        calculate = QPushButton("Katalog Adaylarını Hesapla")
        calculate.clicked.connect(self._calculate_catalog_candidates)
        load = QPushButton("Seçileni Kablo Oluşturucuya Al")
        load.clicked.connect(self._load_selected_candidate)
        compare = QPushButton("Seçili / İlk Adayları Teknik Karşılaştır")
        compare.clicked.connect(self._compare_catalog_candidates)
        apply_project = QPushButton("Seçileni Projeye Uygulama Sihirbazı")
        apply_project.clicked.connect(self._apply_selected_candidate_to_project)
        actions.addWidget(calculate)
        actions.addWidget(compare)
        actions.addStretch(1)
        actions.addWidget(load)
        actions.addWidget(apply_project)
        layout.addLayout(actions)

        self.candidate_detail = QPlainTextEdit()
        self.candidate_detail.setReadOnly(True)
        self.candidate_detail.setMaximumHeight(165)
        layout.addWidget(self.candidate_detail)
        return tab

    def _build_parametric_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top = QSplitter(Qt.Horizontal)
        self.cross_section = CableCrossSectionView()
        top.addWidget(self.cross_section)

        metadata = QGroupBox("Kablo kimliği")
        form = QFormLayout(metadata)
        self.meta_name = QLineEdit()
        self.meta_manufacturer = QLineEdit()
        self.meta_series = QLineEdit()
        self.meta_model = QLineEdit()
        self.meta_voltage = QLineEdit()
        self.meta_standard = QLineEdit()
        self.meta_material = QComboBox()
        self.meta_material.addItems(["Cu", "Al"])
        self.meta_area = QDoubleSpinBox()
        self.meta_area.setRange(1, 10000)
        self.meta_area.setDecimals(1)
        self.meta_area.setSuffix(" mm²")
        self.meta_stranding = QComboBox()
        self.meta_stranding.addItems(["MILLIKEN", "COMPACTED_STRANDED", "SEGMENTAL", "SOLID", "OTHER"])
        self.meta_segments = QDoubleSpinBox()
        self.meta_segments.setRange(1, 24)
        self.meta_segments.setDecimals(0)
        form.addRow("Kablo adı", self.meta_name)
        form.addRow("Üretici", self.meta_manufacturer)
        form.addRow("Seri", self.meta_series)
        form.addRow("Model", self.meta_model)
        form.addRow("Gerilim sınıfı", self.meta_voltage)
        form.addRow("Dayanak standart", self.meta_standard)
        form.addRow("İletken", self.meta_material)
        form.addRow("Nominal kesit", self.meta_area)
        form.addRow("İletken yapısı", self.meta_stranding)
        form.addRow("Segment sayısı", self.meta_segments)
        top.addWidget(metadata)
        top.setStretchFactor(0, 1)
        top.setStretchFactor(1, 0)
        top.setSizes([620, 340])
        layout.addWidget(top, 1)

        layer_group = QGroupBox("Katman konstrüksiyonu")
        layer_layout = QVBoxLayout(layer_group)
        self.layer_table = QTableWidget(0, 12)
        self.layer_table.setHorizontalHeaderLabels([
            "ID", "Katman", "Tür", "İç Ø", "Dış Ø", "Malzeme", "ρth",
            "εr", "tanδ", "Kesit", "Tel adedi / Ø", "Kaynak",
        ])
        self.layer_table.setAlternatingRowColors(True)
        self.layer_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.layer_table.itemChanged.connect(self._layer_table_changed)
        self.layer_table.horizontalHeader().setStretchLastSection(True)
        layer_layout.addWidget(self.layer_table)
        row_actions = QHBoxLayout()
        add_layer = QPushButton("Katman Ekle")
        add_layer.clicked.connect(self._add_layer)
        remove_layer = QPushButton("Seçili Katmanı Sil")
        remove_layer.clicked.connect(self._remove_layer)
        row_actions.addWidget(add_layer)
        row_actions.addWidget(remove_layer)
        row_actions.addStretch(1)
        layer_layout.addLayout(row_actions)
        layout.addWidget(layer_group, 1)
        return tab

    def _build_sources_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        note = QLabel(
            "Katalog, üretici çizimi, test raporu, hesaplanan değer ve kullanıcı varsayımı ayrı kaynak kayıtlarıdır. "
            "Dosya hash'i bilinmiyorsa boş bırakılır; doğrulanmış gibi işaretlenmez."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.source_table = QTableWidget(0, 10)
        self.source_table.setHorizontalHeaderLabels([
            "ID", "Tür", "Doküman", "Rev.", "Sayfa", "Dosya", "SHA-256", "Giren", "Doğrulandı", "Not",
        ])
        self.source_table.setAlternatingRowColors(True)
        self.source_table.itemChanged.connect(self._source_table_changed)
        self.source_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.source_table, 1)
        buttons = QHBoxLayout()
        add_source = QPushButton("Kaynak Ekle")
        add_source.clicked.connect(self._add_source)
        remove_source = QPushButton("Seçili Kaynağı Sil")
        remove_source.clicked.connect(self._remove_source)
        buttons.addWidget(add_source)
        buttons.addWidget(remove_source)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return tab

    def _build_validation_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.readiness_label = QLabel()
        self.readiness_label.setWordWrap(True)
        self.readiness_label.setStyleSheet("font-size: 11pt; font-weight: 650; padding: 8px; background: #eaf0f5;")
        self.validation_text = QPlainTextEdit()
        self.validation_text.setReadOnly(True)
        layout.addWidget(self.readiness_label)
        layout.addWidget(self.validation_text, 1)
        return tab

    def refresh(self) -> None:
        self._loading = True
        try:
            manufacturers = sorted({record.manufacturer for record in self.project.cable_library.records})
            voltages = sorted({record.voltage_class for record in self.project.cable_library.records})
            current_manufacturer = self.filter_manufacturer.currentText()
            current_voltage = self.filter_voltage.currentText()
            self.filter_manufacturer.clear()
            self.filter_manufacturer.addItems(["Tümü", *manufacturers])
            self.filter_voltage.clear()
            self.filter_voltage.addItems(["Tümü", *voltages])
            if current_manufacturer in [self.filter_manufacturer.itemText(i) for i in range(self.filter_manufacturer.count())]:
                self.filter_manufacturer.setCurrentText(current_manufacturer)
            if current_voltage in [self.filter_voltage.itemText(i) for i in range(self.filter_voltage.count())]:
                self.filter_voltage.setCurrentText(current_voltage)
            self._load_working_editor()
            self._refresh_catalog_table()
            self._refresh_candidate_basis()
        finally:
            self._loading = False

    def _refresh_catalog_table(self) -> None:
        if self._loading:
            return
        self._filtered_records = filter_catalog_records(
            self.project.cable_library,
            self.filter_manufacturer.currentText(),
            self.filter_voltage.currentText(),
            self.filter_material.currentText(),
            self.filter_area.value(),
            self.filter_text.text(),
        )
        self.catalog_table.setRowCount(len(self._filtered_records))
        for row, record in enumerate(self._filtered_records):
            values = [
                record.record_id, record.manufacturer, f"{record.series} / {record.model}",
                record.voltage_class, record.conductor_material, f"{record.conductor_area_mm2:g}", record.status,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setData(Qt.UserRole, record.record_id)
                self.catalog_table.setItem(row, col, item)
        self.catalog_table.resizeColumnsToContents()
        self.catalog_table.horizontalHeader().setStretchLastSection(True)

    def _catalog_selection_changed(self) -> None:
        row = self.catalog_table.currentRow()
        if row < 0 or row >= len(self._filtered_records):
            return
        record = self._filtered_records[row]
        try:
            self.working_cable = apply_catalog_record(record)
        except CableLibraryInputError as exc:
            QMessageBox.warning(self, "Katalog kaydı", str(exc))
            return
        self.project.cable_library.selected_record_id = record.record_id
        self.catalog_info.setPlainText(
            f"{record.record_id}\n{record.manufacturer} · {record.series} · {record.model}\n"
            f"{record.voltage_class} · {record.conductor_material} {record.conductor_area_mm2:g} mm²\n"
            f"Durum: {record.status}\nKaynaklar: {', '.join(record.source_ids) or 'yok'}\n"
            f"Kaynak sayfası: {record.source_page or 'belirtilmemiş'}\n"
            f"Veri seviyesi: {record.source_quality}\n{record.notes}"
        )
        self._load_working_editor()


    def _refresh_candidate_basis(self) -> None:
        # ``Proje Adayları`` is intentionally absent in application-database
        # mode.  ``refresh()`` is shared by both modes, therefore the optional
        # project-only label must be ignored when it was not constructed.
        label = self.candidate_basis_label
        if label is None:
            return
        basis = self.project.design_basis
        label.setText(
            f"Proje tasarım temeli: {basis.system_voltage_kv:g} kV · "
            f"normal {basis.normal_current_per_active_circuit_a:.2f} A/devre · "
            f"N-1 {basis.n1_current_per_circuit_a:.2f} A/devre · "
            f"tasarım {basis.design_current_per_circuit_a:.2f} A/devre · "
            f"güzergâh {basis.total_route_length_m:g} m · {basis.installation_profile}"
        )

    def _calculate_catalog_candidates(self) -> None:
        try:
            result = evaluate_catalog_candidates(
                self.project.cable_library, self.project.design_basis, maximum_parallel_cables=2,
                project=self.project,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Katalog aday seçimi", str(exc))
            return
        self._candidate_evaluations = list(result.evaluations)
        self._refresh_candidate_basis()
        self.candidate_table.setRowCount(len(self._candidate_evaluations))
        for row, item in enumerate(self._candidate_evaluations):
            drop = "—" if item.voltage_drop_percent is None else f"%{item.voltage_drop_percent:.4f}"
            values = [
                str(row + 1), item.manufacturer, item.model, item.voltage_class,
                f"{item.conductor_material} {item.conductor_area_mm2:g} mm²",
                str(item.parallel_cables_per_phase),
                f"{item.reference_ampacity_a_per_cable:.1f} A",
                f"{item.combined_reference_ampacity_a:.1f} A",
                "—" if item.adjusted_reference_ampacity_a is None else f"{item.adjusted_reference_ampacity_a:.1f} A",
                f"{item.required_design_current_a:.1f} A",
                "—" if item.normalized_design_margin_a is None else f"{item.normalized_design_margin_a:+.1f} A",
                drop, item.data_readiness, item.catalog_screening_status, item.reference_condition_summary,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                cell.setData(Qt.UserRole, item.candidate_id)
                if item.catalog_screening_status == "FAIL":
                    cell.setBackground(QColor("#fde2e2"))
                elif item.warnings:
                    cell.setBackground(QColor("#fff6d6"))
                self.candidate_table.setItem(row, col, cell)
        self.candidate_table.resizeColumnsToContents()
        self.candidate_table.horizontalHeader().setStretchLastSection(True)
        if self._candidate_evaluations:
            self.candidate_table.selectRow(0)
        self.candidate_detail.setPlainText("\n".join(result.trace))

    def _selected_candidate(self) -> CatalogCandidateEvaluation | None:
        row = self.candidate_table.currentRow()
        if row < 0 or row >= len(self._candidate_evaluations):
            return None
        return self._candidate_evaluations[row]

    def _candidate_selection_changed(self) -> None:
        item = self._selected_candidate()
        if item is None:
            return
        lines = [
            f"{item.manufacturer} · {item.model}",
            f"Kayıt: {item.record_id} · {item.voltage_class}",
            f"Düzen: {item.parallel_cables_per_phase} kablo/faz",
            f"Katalog referansı: {item.reference_ampacity_a_per_cable:.1f} A/kablo; aritmetik toplam {item.combined_reference_ampacity_a:.1f} A",
            f"Normalize Iref: " + ("hesaplanamadı" if item.adjusted_reference_ampacity_a is None else f"{item.adjusted_reference_ampacity_a:.1f} A"),
            f"Referans doğrulama: {item.reference_validation_status}; kaynaklı faktör={item.correction_factors_source_verified}",
            f"Tasarım akımı: {item.required_design_current_a:.1f} A; normalize marj " + (
                "—" if item.normalized_design_margin_a is None else f"{item.normalized_design_margin_a:+.1f} A"
            ),
            f"Gerilim düşümü ön hesabı: " + (
                "hesaplanamadı" if item.voltage_drop_percent is None else f"%{item.voltage_drop_percent:.5f}"
            ),
            f"Kaynak koşulu: {item.reference_condition_summary}",
            f"Veri hazırlığı: {item.data_readiness}; kaynak seviyesi: {item.source_quality}",
            f"Ön hüküm: {item.catalog_screening_status}",
            "",
            "UYARILAR",
            *(f"- {warning}" for warning in item.warnings),
        ]
        self.candidate_detail.setPlainText("\n".join(lines))

    def _compare_catalog_candidates(self) -> None:
        if not self._candidate_evaluations:
            self._calculate_catalog_candidates()
        selected_rows = sorted({index.row() for index in self.candidate_table.selectionModel().selectedRows()})
        candidate_ids = [
            self._candidate_evaluations[row].candidate_id
            for row in selected_rows
            if 0 <= row < len(self._candidate_evaluations)
        ]
        # Tek satır seçilmişse motor her katalog kaydının en iyi varyantını karşılaştırır.
        dialog = CatalogComparisonDialog(
            self.project,
            candidate_ids if len(candidate_ids) >= 2 else None,
            parent=self,
        )
        dialog.exec()

    def _load_selected_candidate(self) -> None:
        item = self._selected_candidate()
        if item is None:
            QMessageBox.information(self, "Katalog adayı", "Önce bir aday satırı seçin.")
            return
        record = next((r for r in self.project.cable_library.records if r.record_id == item.record_id), None)
        if record is None:
            QMessageBox.warning(self, "Katalog adayı", "Aday katalog kaydı bulunamadı.")
            return
        self.working_cable = apply_catalog_record(record)
        self.working_cable.parallel_cables_per_phase = item.parallel_cables_per_phase
        self.working_cable.design_current_a = item.required_design_current_a / max(1, item.parallel_cables_per_phase)
        self._load_working_editor()

    def _apply_selected_candidate_to_project(self) -> None:
        item = self._selected_candidate()
        if item is None:
            QMessageBox.information(self, "Katalog adayı", "Önce bir aday satırı seçin.")
            return
        if item.catalog_screening_status == "FAIL":
            answer = QMessageBox.question(
                self, "Uygun olmayan katalog adayı",
                "Aday katalog ön elemesinde FAIL. Yine de koşullu inceleme için uygulama sihirbazı açılsın mı?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        dialog = CableApplicationDialog(
            self.project,
            item.record_id,
            item.candidate_id,
            item.parallel_cables_per_phase,
            on_applied=self._application_completed,
            parent=self,
        )
        dialog.exec()

    def _application_completed(self) -> None:
        self.working_cable = cable_from_dict(asdict(self.project.cable))
        if self.on_project_cable_changed:
            self.on_project_cable_changed()
        self.refresh()

    def _load_working_editor(self) -> None:
        self._loading = True
        try:
            cable = self.working_cable
            self.meta_name.setText(cable.name)
            self.meta_manufacturer.setText(cable.manufacturer)
            self.meta_series.setText(cable.series)
            self.meta_model.setText(cable.model)
            self.meta_voltage.setText(cable.voltage_class)
            self.meta_standard.setText(cable.applicable_standard)
            self.meta_material.setCurrentText(cable.conductor_material if cable.conductor_material in {"Cu", "Al"} else "Cu")
            self.meta_area.setValue(cable.conductor_area_mm2)
            self.meta_stranding.setCurrentText(cable.conductor_stranding_type)
            self.meta_segments.setValue(cable.conductor_segment_count)
            self._populate_layer_table()
            self._populate_source_table()
            self.cross_section.draw_cable(cable)
            self._refresh_validation()
            active_assignments = sum(1 for item in self.project.cable_application.assignments if item.active)
            if self.database_mode:
                self.active_label.setText(
                    f"Veri tabanı\n{len(self.project.cable_library.records)} kablo kaydı\n"
                    "Projeye atama bu ekrandan yapılmaz"
                )
            else:
                assignment = "Atandı" if self.project.cable.snapshot_hash else "Atanmadı"
                self.active_label.setText(
                    f"Projeye atanmış kablo\n{self.project.cable.manufacturer or 'Manuel'} · "
                    f"{self.project.cable.model or self.project.cable.name}\n"
                    f"Durum: {assignment} · aktif bölüm {active_assignments}"
                )
        finally:
            self._loading = False

    def _pull_metadata(self) -> None:
        cable = self.working_cable
        cable.name = self.meta_name.text().strip() or "Adsız kablo"
        cable.manufacturer = self.meta_manufacturer.text().strip()
        cable.series = self.meta_series.text().strip()
        cable.model = self.meta_model.text().strip()
        cable.voltage_class = self.meta_voltage.text().strip()
        cable.applicable_standard = self.meta_standard.text().strip()
        cable.conductor_material = self.meta_material.currentText()
        cable.conductor_area_mm2 = self.meta_area.value()
        cable.conductor_stranding_type = self.meta_stranding.currentText()
        cable.conductor_segment_count = int(self.meta_segments.value())
        conductor = next((layer for layer in cable.layers if layer.layer_type.upper() == "CONDUCTOR"), None)
        if conductor is not None:
            conductor.material = cable.conductor_material
            conductor.conductor_area_mm2 = cable.conductor_area_mm2

    def _populate_layer_table(self) -> None:
        self.layer_table.setRowCount(len(self.working_cable.layers))
        for row, layer in enumerate(self.working_cable.layers):
            wire = ""
            if layer.wire_count or layer.wire_diameter_mm:
                wire = f"{layer.wire_count} / {layer.wire_diameter_mm:g}"
            values = [
                layer.layer_id, layer.name, layer.layer_type,
                f"{layer.inner_diameter_mm:g}", f"{layer.outer_diameter_mm:g}", layer.material,
                f"{layer.thermal_resistivity_km_w:g}", f"{layer.relative_permittivity:g}",
                f"{layer.dielectric_loss_tan_delta:g}", f"{layer.conductor_area_mm2:g}", wire, layer.source_id,
            ]
            for col, value in enumerate(values):
                self.layer_table.setItem(row, col, QTableWidgetItem(value))
        self.layer_table.resizeColumnsToContents()
        self.layer_table.horizontalHeader().setStretchLastSection(True)

    def _layer_table_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        row = item.row()
        if row < 0 or row >= len(self.working_cable.layers):
            return
        layer = self.working_cable.layers[row]
        try:
            values = [self.layer_table.item(row, col).text().strip() if self.layer_table.item(row, col) else "" for col in range(12)]
            layer.layer_id = values[0]
            layer.name = values[1]
            layer.layer_type = values[2]
            layer.inner_diameter_mm = float(values[3] or 0)
            layer.outer_diameter_mm = float(values[4] or 0)
            layer.material = values[5]
            layer.thermal_resistivity_km_w = float(values[6] or 0)
            layer.relative_permittivity = float(values[7] or 0)
            layer.dielectric_loss_tan_delta = float(values[8] or 0)
            layer.conductor_area_mm2 = float(values[9] or 0)
            wire_parts = [part.strip() for part in values[10].split("/")]
            layer.wire_count = int(float(wire_parts[0])) if wire_parts and wire_parts[0] else 0
            layer.wire_diameter_mm = float(wire_parts[1]) if len(wire_parts) > 1 and wire_parts[1] else 0.0
            layer.source_id = values[11]
        except ValueError:
            return
        self.cross_section.draw_cable(self.working_cable)
        self._refresh_validation()

    def _populate_source_table(self) -> None:
        self.source_table.setRowCount(len(self.working_cable.parameter_sources))
        for row, source in enumerate(self.working_cable.parameter_sources):
            values = [
                source.source_id, source.source_type, source.document_title, source.document_revision,
                source.page_reference, source.file_name, source.file_sha256, source.entered_by,
                "Evet" if source.verified else "Hayır", source.notes,
            ]
            for col, value in enumerate(values):
                self.source_table.setItem(row, col, QTableWidgetItem(value))
        self.source_table.resizeColumnsToContents()
        self.source_table.horizontalHeader().setStretchLastSection(True)

    def _source_table_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        row = item.row()
        if row < 0 or row >= len(self.working_cable.parameter_sources):
            return
        values = [self.source_table.item(row, col).text().strip() if self.source_table.item(row, col) else "" for col in range(10)]
        source = self.working_cable.parameter_sources[row]
        source.source_id, source.source_type, source.document_title, source.document_revision = values[:4]
        source.page_reference, source.file_name, source.file_sha256, source.entered_by = values[4:8]
        source.verified = values[8].lower() in {"evet", "yes", "true", "1"}
        source.notes = values[9]
        self._refresh_validation()

    def _refresh_validation(self) -> None:
        try:
            self._pull_metadata()
            report = update_cable_validation_state(self.working_cable)
        except Exception as exc:
            self.readiness_label.setText(f"Doğrulama çalıştırılamadı: {exc}")
            return
        self.readiness_label.setText(
            f"Genel: {report.status}   |   Elektriksel: {report.electrical_readiness}   |   "
            f"Bonding: {report.bonding_readiness}   |   Termal: {report.thermal_readiness}   |   "
            f"Arıza: {report.fault_readiness}"
        )
        lines = [
            "HESAPLANAN / SENKRONİZE DEĞERLER",
            "=" * 72,
            f"İletken çapı: {report.derived.conductor_diameter_mm:.3f} mm",
            f"İzolasyon dış çapı: {report.derived.insulation_outer_diameter_mm:.3f} mm",
            f"Ekran dış çapı: {report.derived.screen_outer_diameter_mm:.3f} mm",
            f"Kablo dış çapı: {report.derived.overall_diameter_mm:.3f} mm",
            f"Kapasitans: {report.derived.capacitance_uf_km:.6f} µF/km",
            f"Rdc20 iletken: {report.derived.conductor_dc_resistance_20_ohm_km:.6f} Ω/km",
            f"Rdc20 metalik kılıf: {report.derived.sheath_dc_resistance_20_ohm_km:.6f} Ω/km",
            f"Metalik kılıf ortalama çapı: {report.derived.sheath_mean_diameter_mm:.3f} mm",
            f"Metalik kılıf/ekran kesiti: {report.derived.sheath_cross_section_mm2:.3f} mm²",
            "",
            "DOĞRULAMA BULGULARI",
            "=" * 72,
        ]
        if report.issues:
            lines.extend(f"[{issue.severity}] {issue.code}: {issue.message}" for issue in report.issues)
        else:
            lines.append("Kritik tutarsızlık bulunmadı.")
        lines.extend([
            "",
            "NOT",
            "Katalog/üretici çizimi/test raporu doğrulanmadan VERIFIED kabul edilmez. "
            "Jenerik kayıtlar yalnız L1 ön tasarım içindir.",
        ])
        self.validation_text.setPlainText("\n".join(lines))

    def _sync_from_layers(self) -> None:
        self._pull_metadata()
        synchronize_cable_from_layers(self.working_cable)
        self._load_working_editor()
        QMessageBox.information(
            self,
            "Kablo konstrüksiyonu",
            "Katman geometrisinden çaplar, kapasitans, iletken/metalik-kılıf dirençleri ve termal katman girdileri güncellendi.",
        )


    def _edit_catalog_reference_conditions(self) -> None:
        row = self.catalog_table.currentRow()
        if row < 0 or row >= len(self._filtered_records):
            QMessageBox.information(self, "Katalog", "Önce bir katalog kaydı seçin.")
            return
        record = self._filtered_records[row]
        dialog = CatalogReferenceConditionsDialog(record, self)
        if dialog.exec() == QDialog.Accepted:
            self._refresh_catalog_table()
            self._candidate_evaluations = []
            self.candidate_table.setRowCount(0) if hasattr(self, "candidate_table") else None
            self.catalog_info.appendPlainText("\nFAZ 6.8: Iref/referans koşulları güncellendi; proje kablo snapshot'ı değişmedi.")

    def _apply_selected_catalog_record(self) -> None:
        row = self.catalog_table.currentRow()
        if row < 0 or row >= len(self._filtered_records):
            QMessageBox.information(self, "Katalog", "Önce bir katalog kaydı seçin.")
            return
        self.working_cable = apply_catalog_record(self._filtered_records[row])
        self._apply_working_to_project()

    def _apply_working_to_project(self) -> None:
        self._pull_metadata()
        synchronize_cable_from_layers(self.working_cable)
        report = update_cable_validation_state(self.working_cable)
        if report.has_errors:
            answer = QMessageBox.question(
                self,
                "Koşullu proje kablosu",
                "Kablo konstrüksiyonunda hata düzeyinde bulgular var. Buna rağmen projeye koşullu olarak atansın mı?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        record_id = self.working_cable.catalog_record_id
        snapshot_record = catalog_record_from_cable(
            self.working_cable,
            record_id or f"PROJECT-{self.working_cable.cable_id}",
            status=report.status,
        )
        apply_catalog_record(snapshot_record, self.project.cable)
        self.project.design_progress.cable = "COMPLETE" if report.status == "VERIFIED" else "CONDITIONAL"
        self.project.design_progress.thermal = "STALE"
        self.project.design_progress.bonding = "STALE"
        self.project.design_progress.fault_epr = "STALE"
        self.project.design_progress.svl = "STALE"
        self.project.design_progress.final_design = "NOT_READY"
        if self.on_project_cable_changed:
            self.on_project_cable_changed()
        self.refresh()
        QMessageBox.information(
            self,
            "Proje kablosu",
            f"{self.project.cable.name} projeye atandı.\n"
            "Katalog kaydı proje içine kopyalandı; katalog daha sonra değişse bile proje kablosu değişmez.\n"
            f"Durum: {self.project.cable.data_status}",
        )

    def _save_working_to_library(self) -> None:
        self._pull_metadata()
        synchronize_cable_from_layers(self.working_cable)
        suggested = self.working_cable.catalog_record_id or "CABLE-NEW-001"
        record_id, ok = QInputDialog.getText(self, "Katalog kaydı", "Benzersiz kayıt ID:", text=suggested)
        if not ok or not record_id.strip():
            return
        if any(record.record_id == record_id.strip() for record in self.project.cable_library.records):
            QMessageBox.warning(self, "Katalog kaydı", "Bu kayıt ID zaten kullanılıyor.")
            return
        report = update_cable_validation_state(self.working_cable)
        record = catalog_record_from_cable(self.working_cable, record_id.strip(), status=report.status)
        self.project.cable_library.records.append(record)
        for source in self.working_cable.parameter_sources:
            if not any(item.source_id == source.source_id for item in self.project.cable_library.sources):
                self.project.cable_library.sources.append(deepcopy(source))
        if self.on_project_cable_changed:
            self.on_project_cable_changed()
        self.refresh()

    def _new_manual_cable(self) -> None:
        cable = CableData(
            cable_id="CABLE-MANUAL",
            name="Yeni manuel kablo",
            manufacturer="",
            series="",
            model="",
            data_status=CABLE_STATUS_DRAFT,
            layers=default_cable_layers(),
            parameter_sources=default_cable_sources(),
        )
        self.working_cable = cable
        self.catalog_table.clearSelection()
        self.catalog_info.setPlainText("Yeni manuel kablo konstrüksiyonu")
        self._load_working_editor()

    def _add_layer(self) -> None:
        outer = max((layer.outer_diameter_mm for layer in self.working_cable.layers), default=0.0)
        index = len(self.working_cable.layers) + 1
        self.working_cable.layers.append(CableLayerData(
            f"L{index:02d}", "Yeni katman", "OTHER", outer, outer + 2.0,
            "", 3.5, source_id="SRC-GENERIC-001",
        ))
        self._load_working_editor()

    def _remove_layer(self) -> None:
        row = self.layer_table.currentRow()
        if row < 0:
            return
        del self.working_cable.layers[row]
        self._load_working_editor()

    def _add_source(self) -> None:
        index = len(self.working_cable.parameter_sources) + 1
        self.working_cable.parameter_sources.append(CableParameterSource(
            f"SRC-{index:03d}", CABLE_SOURCE_USER_ASSUMPTION, entered_by="Kullanıcı"
        ))
        self._load_working_editor()

    def _remove_source(self) -> None:
        row = self.source_table.currentRow()
        if row < 0:
            return
        source_id = self.working_cable.parameter_sources[row].source_id
        if any(layer.source_id == source_id for layer in self.working_cable.layers):
            QMessageBox.warning(self, "Kaynak", "Bu kaynak en az bir katmana bağlı. Önce katman kaynaklarını değiştirin.")
            return
        del self.working_cable.parameter_sources[row]
        self._load_working_editor()

    def _show_catalog_sources(self) -> None:
        try:
            payload = json.loads(
                files("ucd.resources").joinpath("manufacturer_catalog_links.json").read_text(encoding="utf-8")
            )
        except Exception as exc:
            QMessageBox.warning(self, "Katalog bağlantıları", f"Bağlantı listesi okunamadı: {exc}")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Üretici kataloglarına erişim")
        fit_window(dialog, DENSITY_COMPACT, center_on=self)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        notice = str(payload.get("notice", ""))
        items = []
        for item in payload.get("links", []):
            manufacturer = str(item.get("manufacturer", ""))
            label = str(item.get("label", "Kataloglar"))
            url = str(item.get("url", ""))
            if manufacturer and url:
                items.append(f'<li><b>{manufacturer}</b> — <a href="{url}">{label}</a></li>')
        browser.setHtml(
            "<h3>Üretici katalog sayfaları</h3>"
            f"<p>{notice}</p><ul>{''.join(items)}</ul>"
            "<p>Kullanıcı bu listeyle sınırlı değildir; kendi üretici kaydını ve kaynak zincirini ekleyebilir.</p>"
        )
        layout.addWidget(browser)
        close = QPushButton("Kapat")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def _import_catalog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "DiTuS kablo katalog paketi", "", "DiTuS Catalog (*.ditus-cable-catalog.json *.json)"
        )
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            incoming = catalog_package_from_dict(raw)
            added, updated = merge_catalog_library(self.project.cable_library, incoming, replace=False)
        except Exception as exc:
            QMessageBox.critical(self, "Katalog içe alma", str(exc))
            return
        if self.on_project_cable_changed:
            self.on_project_cable_changed()
        self.refresh()
        QMessageBox.information(self, "Katalog içe alma", f"{added} yeni kayıt eklendi; {updated} kayıt güncellendi.")

    def _export_catalog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "DiTuS kablo katalog paketi", "ditus_cable_catalog.ditus-cable-catalog.json",
            "DiTuS Catalog (*.ditus-cable-catalog.json)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".ditus-cable-catalog.json"
        try:
            atomic_write_text(
                path,
                json.dumps(catalog_package_to_dict(self.project.cable_library), ensure_ascii=False, indent=2),
            )
        except OSError as exc:
            QMessageBox.critical(self, "Katalog dışa verme", f"Katalog paketi yazılamadı:\n{exc}")
            return
        QMessageBox.information(self, "Katalog dışa verme", f"Katalog paketi yazıldı:\n{path}")
