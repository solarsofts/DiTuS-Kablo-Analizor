from __future__ import annotations

from dataclasses import replace
from math import hypot
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ucd.cad import read_dxf_geometry
from ucd.calculations import (
    FirstDesignInputError,
    apply_candidate_to_project,
    apply_load_calculation,
    generate_generic_candidates,
)
from ucd.models.project import (
    DesignBasisData,
    DesignProgressData,
    LOAD_MODE_ACTIVE_POWER,
    LOAD_MODE_APPARENT_POWER,
    LOAD_MODE_DIRECT_CURRENT,
    MATURITY_LEVEL_1,
    ProjectData,
    ROUTE_MODE_DRAW,
    ROUTE_MODE_DXF,
    ROUTE_MODE_TOTAL_LENGTH,
    RouteSection,
    SELECTION_MODE_MANUAL,
    SELECTION_MODE_RECOMMENDED,
    SELECTION_MODE_SECTION_ONLY,
    default_bonding_system,
    thermal_design_from_route_sections,
)
from .window_layout import fit_window, DENSITY_NORMAL


def geometry_total_length(geometry: object) -> float:
    total = 0.0
    for p1, p2, _layer in geometry.lines:
        total += hypot(p2[0] - p1[0], p2[1] - p1[1])
    for points, closed, _layer in geometry.polylines:
        for a, b in zip(points, points[1:]):
            total += hypot(b[0] - a[0], b[1] - a[1])
        if closed and len(points) > 2:
            total += hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1])
    return total


class NewDesignWizard(QDialog):
    """Five-step first-design workflow.

    The wizard deliberately creates a preliminary design basis. Manufacturer
    catalogue data, detailed sheath construction, site thermal tests and EMT
    inputs remain explicit later-stage requirements.
    """

    def __init__(self, parent: QWidget | None = None, initial_project: ProjectData | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Yeni Kablo Sistemi Tasarla")
        fit_window(self, DENSITY_NORMAL)
        self.project = initial_project or ProjectData()
        self.result_project: ProjectData | None = None
        self.run_first_iteration = True
        self._candidates = []
        self._dxf_path = ""

        root = QVBoxLayout(self)
        self.step_label = QLabel()
        self.step_label.setStyleSheet("font-size: 16px; font-weight: 700; padding: 8px;")
        root.addWidget(self.step_label)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self.pages.addWidget(self._build_project_page())
        self.pages.addWidget(self._build_system_page())
        self.pages.addWidget(self._build_route_page())
        self.pages.addWidget(self._build_candidate_page())
        self.pages.addWidget(self._build_review_page())

        nav = QHBoxLayout()
        self.back_btn = QPushButton("Geri")
        self.next_btn = QPushButton("İleri")
        self.cancel_btn = QPushButton("İptal")
        self.back_btn.clicked.connect(self._back)
        self.next_btn.clicked.connect(self._next)
        self.cancel_btn.clicked.connect(self.reject)
        nav.addWidget(self.cancel_btn)
        nav.addStretch(1)
        nav.addWidget(self.back_btn)
        nav.addWidget(self.next_btn)
        root.addLayout(nav)
        self._update_step()

    def _spin(self, minimum: float, maximum: float, value: float, suffix: str = "", decimals: int = 2) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        widget.setDecimals(decimals)
        widget.setSuffix(suffix)
        widget.setSingleStep(1.0 if maximum > 100 else 0.05)
        return widget

    def _build_project_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        info = QLabel(
            "Bu sihirbaz ilk tasarım temelini oluşturur. Yalnız sonucu belirleyen temel girdiler şimdi alınır; "
            "ileri bonding, SVL, topraklama ve termal test verileri ilgili aşamada istenir."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        self.project_name_edit = QLineEdit(self.project.project_name)
        self.project_code_edit = QLineEdit(self.project.project_code)
        self.description_edit = QLineEdit(self.project.description)
        form.addRow("Proje adı", self.project_name_edit)
        form.addRow("Proje kodu", self.project_code_edit)
        form.addRow("Kısa açıklama", self.description_edit)
        layout.addLayout(form)
        layout.addStretch(1)
        return page

    def _build_system_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.voltage_spin = self._spin(1.0, 500.0, 154.0, " kV", 1)
        self.frequency_combo = QComboBox(); self.frequency_combo.addItems(["50", "60"])
        self.circuit_count_spin = QSpinBox(); self.circuit_count_spin.setRange(1, 12); self.circuit_count_spin.setValue(1)
        self.active_count_spin = QSpinBox(); self.active_count_spin.setRange(1, 12); self.active_count_spin.setValue(1)
        self.n1_check = QCheckBox("N-1 yük durumunu tasarımda kullan")
        self.grounding_combo = QComboBox(); self.grounding_combo.addItems([
            "DIRECT_GROUNDED", "RESISTANCE_GROUNDED", "REACTANCE_GROUNDED", "RESONANT_GROUNDED", "UNKNOWN"
        ])
        form.addRow("Nominal hat gerilimi", self.voltage_spin)
        form.addRow("Frekans [Hz]", self.frequency_combo)
        form.addRow("Toplam devre sayısı", self.circuit_count_spin)
        form.addRow("Normalde aktif devre", self.active_count_spin)
        form.addRow("Güvenilirlik", self.n1_check)
        form.addRow("Şebeke topraklaması", self.grounding_combo)
        layout.addLayout(form)

        load_group = QGroupBox("Yük giriş biçimi")
        load_layout = QVBoxLayout(load_group)
        self.load_mode_group = QButtonGroup(self)
        self.load_mw_radio = QRadioButton("Aktif güç [MW]")
        self.load_mva_radio = QRadioButton("Görünür güç [MVA]")
        self.load_a_radio = QRadioButton("Hat akımı [A]")
        self.load_mva_radio.setChecked(True)
        for button in (self.load_mw_radio, self.load_mva_radio, self.load_a_radio):
            self.load_mode_group.addButton(button); load_layout.addWidget(button)
        values = QFormLayout()
        self.active_power_spin = self._spin(0.0, 100000.0, 200.0, " MW", 2)
        self.apparent_power_spin = self._spin(0.0, 100000.0, 200.0, " MVA", 2)
        self.direct_current_spin = self._spin(0.0, 20000.0, 800.0, " A", 1)
        self.power_factor_spin = self._spin(0.01, 1.0, 0.95, "", 3)
        self.growth_spin = self._spin(0.0, 300.0, 0.0, " %", 1)
        self.margin_spin = self._spin(0.0, 100.0, 10.0, " %", 1)
        values.addRow("Aktif güç", self.active_power_spin)
        values.addRow("Görünür güç", self.apparent_power_spin)
        values.addRow("Doğrudan akım", self.direct_current_spin)
        values.addRow("Güç faktörü", self.power_factor_spin)
        values.addRow("Gelecek büyüme", self.growth_spin)
        values.addRow("Tasarım marjı", self.margin_spin)
        load_layout.addLayout(values)
        layout.addWidget(load_group)
        self.load_summary = QLabel("Akım hesabı bir sonraki adımda gösterilir.")
        self.load_summary.setWordWrap(True)
        layout.addWidget(self.load_summary)
        return page

    def _build_route_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        mode_group = QGroupBox("Güzergâh kaynağı")
        mode_layout = QVBoxLayout(mode_group)
        self.route_group = QButtonGroup(self)
        self.route_dxf_radio = QRadioButton("DXF’den al")
        self.route_draw_radio = QRadioButton("CAD/plan alanında çiz")
        self.route_length_radio = QRadioButton("Yalnız toplam uzunlukla ön tasarım")
        self.route_length_radio.setChecked(True)
        for button in (self.route_dxf_radio, self.route_draw_radio, self.route_length_radio):
            self.route_group.addButton(button); mode_layout.addWidget(button)
        dxf_row = QHBoxLayout()
        self.dxf_edit = QLineEdit(); self.dxf_edit.setReadOnly(True)
        dxf_btn = QPushButton("DXF seç…"); dxf_btn.clicked.connect(self._select_dxf)
        dxf_row.addWidget(self.dxf_edit, 1); dxf_row.addWidget(dxf_btn)
        mode_layout.addLayout(dxf_row)
        layout.addWidget(mode_group)

        form = QFormLayout()
        self.route_length_spin = self._spin(1.0, 1000000.0, 1670.0, " m", 1)
        self.installation_combo = QComboBox(); self.installation_combo.addItems([
            "DIRECT_BURIED_TREFOIL", "DIRECT_BURIED_FLAT", "DUCT_BANK", "CONCRETE_DUCT", "TUNNEL", "MIXED_ROUTE", "UNKNOWN"
        ])
        self.burial_spin = self._spin(0.2, 50.0, 1.2, " m", 2)
        self.phase_spacing_spin = self._spin(0.01, 10.0, 0.15, " m", 3)
        self.circuit_spacing_spin = self._spin(0.01, 50.0, 0.80, " m", 2)
        self.soil_rho_spin = self._spin(0.1, 10.0, 1.20, " K·m/W", 2)
        self.soil_source_combo = QComboBox(); self.soil_source_combo.addItems([
            "MEASURED", "SPECIFICATION", "PRELIMINARY_ASSUMPTION", "UNKNOWN"
        ])
        self.material_combo = QComboBox(); self.material_combo.addItems(["AUTO", "AL", "CU"])
        self.cpp_combo = QComboBox(); self.cpp_combo.addItems(["AUTO", "1", "2"])
        form.addRow("Toplam güzergâh uzunluğu", self.route_length_spin)
        form.addRow("Kurulum profili", self.installation_combo)
        form.addRow("Yaklaşık gömülme derinliği", self.burial_spin)
        form.addRow("Fazlar arası mesafe", self.phase_spacing_spin)
        form.addRow("Devreler arası mesafe", self.circuit_spacing_spin)
        form.addRow("Toprak ısıl özdirenci", self.soil_rho_spin)
        form.addRow("Termal değer kaynağı", self.soil_source_combo)
        form.addRow("İletken tercihi", self.material_combo)
        form.addRow("Kablo/faz tercihi", self.cpp_combo)
        layout.addLayout(form)
        self.route_summary = QLabel()
        self.route_summary.setWordWrap(True)
        layout.addWidget(self.route_summary)
        return page

    def _build_candidate_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        modes = QHBoxLayout()
        self.selection_group = QButtonGroup(self)
        self.rec_radio = QRadioButton("Önerilen adaylardan seç")
        self.manual_radio = QRadioButton("Katalogdan/manüel seç")
        self.section_radio = QRadioButton("Sadece kesitle başla")
        self.rec_radio.setChecked(True)
        for button in (self.rec_radio, self.manual_radio, self.section_radio):
            self.selection_group.addButton(button); modes.addWidget(button)
        layout.addLayout(modes)
        self.candidate_table = QTableWidget(0, 9)
        self.candidate_table.setHorizontalHeaderLabels([
            "Seç", "Aday", "Gerilim sınıfı", "Malzeme", "Kesit", "Kablo/faz", "Ön ampacity", "Ön kayıp", "Olgunluk"
        ])
        self.candidate_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.candidate_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.candidate_table)
        self.candidate_note = QLabel(
            "Adaylar jenerik ön elemedir. Katalog ürünü, IEC 60287 sonucu veya satın alma önerisi değildir."
        )
        self.candidate_note.setWordWrap(True)
        layout.addWidget(self.candidate_note)
        return page

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.review_label = QLabel()
        self.review_label.setWordWrap(True)
        self.review_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.review_label, 1)
        self.run_iteration_check = QCheckBox("Sihirbaz tamamlanınca ilk birleşik hesap akışını başlat")
        self.run_iteration_check.setChecked(True)
        layout.addWidget(self.run_iteration_check)
        warning = QLabel(
            "İlk birleşik hesap ön mühendislik düzeyindedir. Üretici, saha termal testi, topraklama, arıza ve EMT verileri eksikse sonuç olgunluğu açıkça sınırlanır."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("font-weight: 600;")
        layout.addWidget(warning)
        return page

    def _selected_load_mode(self) -> str:
        if self.load_mw_radio.isChecked(): return LOAD_MODE_ACTIVE_POWER
        if self.load_a_radio.isChecked(): return LOAD_MODE_DIRECT_CURRENT
        return LOAD_MODE_APPARENT_POWER

    def _selected_route_mode(self) -> str:
        if self.route_dxf_radio.isChecked(): return ROUTE_MODE_DXF
        if self.route_draw_radio.isChecked(): return ROUTE_MODE_DRAW
        return ROUTE_MODE_TOTAL_LENGTH

    def _selected_selection_mode(self) -> str:
        if self.manual_radio.isChecked(): return SELECTION_MODE_MANUAL
        if self.section_radio.isChecked(): return SELECTION_MODE_SECTION_ONLY
        return SELECTION_MODE_RECOMMENDED

    def _collect_basis(self) -> DesignBasisData:
        return DesignBasisData(
            system_voltage_kv=self.voltage_spin.value(),
            frequency_hz=float(self.frequency_combo.currentText()),
            circuit_count=self.circuit_count_spin.value(),
            active_circuit_count=self.active_count_spin.value(),
            n_minus_one_enabled=self.n1_check.isChecked(),
            grounding_type=self.grounding_combo.currentText(),
            load_input_mode=self._selected_load_mode(),
            active_power_mw=self.active_power_spin.value(),
            apparent_power_mva=self.apparent_power_spin.value(),
            direct_current_a=self.direct_current_spin.value(),
            power_factor=self.power_factor_spin.value(),
            future_growth_percent=self.growth_spin.value(),
            design_margin_percent=self.margin_spin.value(),
            route_input_mode=self._selected_route_mode(),
            total_route_length_m=self.route_length_spin.value(),
            installation_profile=self.installation_combo.currentText(),
            burial_depth_m=self.burial_spin.value(),
            phase_spacing_m=self.phase_spacing_spin.value(),
            circuit_spacing_m=self.circuit_spacing_spin.value(),
            soil_thermal_resistivity_km_w=self.soil_rho_spin.value(),
            soil_thermal_value_source=self.soil_source_combo.currentText(),
            conductor_preference=self.material_combo.currentText(),
            cables_per_phase_preference=self.cpp_combo.currentText(),
            initial_selection_mode=self._selected_selection_mode(),
        )

    def _select_dxf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "DXF Dosyası Seç", str(Path.home()), "DXF (*.dxf)")
        if not path: return
        try:
            geometry = read_dxf_geometry(path)
            length = geometry_total_length(geometry)
            if length <= 0: raise ValueError("DXF içinde uzunluğu hesaplanabilir LINE/POLYLINE bulunamadı.")
            self._dxf_path = str(Path(path).resolve())
            self.dxf_edit.setText(self._dxf_path)
            self.route_length_spin.setValue(length)
            self.route_dxf_radio.setChecked(True)
            summary = f"DXF toplam çizgi/polyline uzunluğu: {length:.2f} çizim birimi. Birim ve doğru güzergâh katmanı kullanıcı tarafından doğrulanmalıdır."
            if geometry.skipped_entities_warning:
                summary += "\n" + geometry.skipped_entities_warning
            self.route_summary.setText(summary)
        except Exception as exc:
            QMessageBox.critical(self, "DXF", str(exc))

    def _populate_candidates(self, basis: DesignBasisData) -> None:
        self._candidates = generate_generic_candidates(basis)
        self.candidate_table.setRowCount(len(self._candidates))
        for row, c in enumerate(self._candidates):
            selected = QTableWidgetItem("●" if row == 0 else "")
            values = [selected, QTableWidgetItem(c.label), QTableWidgetItem(c.voltage_class), QTableWidgetItem(c.conductor_material),
                      QTableWidgetItem(f"{c.conductor_area_mm2:g} mm²"), QTableWidgetItem(str(c.cables_per_phase)),
                      QTableWidgetItem(f"{c.estimated_ampacity_a:.1f} A"), QTableWidgetItem(f"{c.estimated_loss_kw_km:.2f} kW/km"),
                      QTableWidgetItem(c.maturity_level)]
            for col, item in enumerate(values):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.candidate_table.setItem(row, col, item)
        self.candidate_table.selectRow(0)
        self.candidate_table.resizeColumnsToContents()
        self.candidate_table.horizontalHeader().setStretchLastSection(True)

    def _validate_step(self, index: int) -> bool:
        if index == 0 and not self.project_name_edit.text().strip():
            QMessageBox.warning(self, "Proje", "Proje adı boş olamaz."); return False
        if index == 1:
            try:
                basis = self._collect_basis(); result = apply_load_calculation(basis)
                self.load_summary.setText(
                    f"Toplam akım {result.normal_total_current_a:.1f} A · normal devre başı {result.normal_current_per_active_circuit_a:.1f} A · "
                    f"N-1 {result.n1_current_per_circuit_a:.1f} A · marjlı tasarım {result.design_current_per_circuit_a:.1f} A · "
                    f"ilk gerilim sınıfı {result.suggested_voltage_class}"
                )
            except FirstDesignInputError as exc:
                QMessageBox.warning(self, "Sistem ve yük", str(exc)); return False
        if index == 2:
            if self.route_dxf_radio.isChecked() and not self._dxf_path:
                QMessageBox.warning(self, "Güzergâh", "DXF modu seçili; önce DXF dosyasını seçin."); return False
            if self.route_length_spin.value() <= 0:
                QMessageBox.warning(self, "Güzergâh", "Güzergâh uzunluğu pozitif olmalıdır."); return False
            try:
                basis = self._collect_basis(); self._populate_candidates(basis)
            except FirstDesignInputError as exc:
                QMessageBox.warning(self, "Kablo adayları", str(exc)); return False
        if index == 3:
            if self.rec_radio.isChecked() and self.candidate_table.currentRow() < 0:
                QMessageBox.warning(self, "Kablo", "Bir başlangıç adayı seçin."); return False
            self._prepare_review()
        return True

    def _prepare_review(self) -> None:
        basis = self._collect_basis(); load = apply_load_calculation(basis)
        selected = "Manuel/katalog seçimi daha sonra" if not self.rec_radio.isChecked() else self._candidates[max(0, self.candidate_table.currentRow())].label
        self.review_label.setText(
            f"<h3>İlk Tasarım Temeli</h3>"
            f"<b>Proje:</b> {self.project_name_edit.text()} ({self.project_code_edit.text()})<br>"
            f"<b>Sistem:</b> {basis.system_voltage_kv:g} kV, {basis.frequency_hz:g} Hz, {basis.circuit_count} devre / {basis.active_circuit_count} aktif<br>"
            f"<b>Yük:</b> normal toplam {load.normal_total_current_a:.1f} A, tasarım devre başı {load.design_current_per_circuit_a:.1f} A<br>"
            f"<b>Gerilim sınıfı başlangıcı:</b> {load.suggested_voltage_class}<br>"
            f"<b>Güzergâh:</b> {basis.total_route_length_m:.1f} m, {basis.route_input_mode}, {basis.installation_profile}<br>"
            f"<b>Termal başlangıç:</b> ρth={basis.soil_thermal_resistivity_km_w:g} K·m/W ({basis.soil_thermal_value_source})<br>"
            f"<b>Kablo başlangıcı:</b> {selected}<br><br>"
            "Bu kayıt Seviye 1 — kaba elektriksel eleme olarak oluşturulacaktır."
        )

    def _build_project_result(self) -> ProjectData:
        basis = self._collect_basis(); apply_load_calculation(basis)
        basis.initial_selection_mode = self._selected_selection_mode()
        if self._candidates: basis.candidates = [replace(c) for c in self._candidates]
        project = ProjectData(
            project_name=self.project_name_edit.text().strip(),
            project_code=self.project_code_edit.text().strip() or "DITUS-KBL-001",
            description=self.description_edit.text().strip(),
            design_basis=basis,
            design_progress=DesignProgressData(system_load="COMPLETE", route="PRELIMINARY", cable="PRELIMINARY", maturity_level=MATURITY_LEVEL_1),
            cad_source=self._dxf_path,
        )
        project.cable.voltage_kv = basis.system_voltage_kv
        project.cable.frequency_hz = basis.frequency_hz
        project.cable.design_current_a = basis.design_current_per_circuit_a
        project.cable.arrangement = "Trefoil" if "TREFOIL" in basis.installation_profile else "Flat"
        project.route_sections = [RouteSection(
            "RS-01 İlk güzergâh",
            basis.total_route_length_m,
            "Standart hendek" if "DIRECT_BURIED" in basis.installation_profile else basis.installation_profile,
            basis.burial_depth_m,
            basis.soil_thermal_resistivity_km_w,
            "CS-WIZARD",
            25.0,
            phase_spacing_m=basis.phase_spacing_m,
            notes=f"Kaynak: {basis.route_input_mode}; termal değer: {basis.soil_thermal_value_source}",
        )]
        project.thermal_design = thermal_design_from_route_sections(project.route_sections)
        project.thermal_design.route_length_m = basis.total_route_length_m
        if project.thermal_design.regions:
            project.thermal_design.regions[0].overrides.update({
                "burial_depth_m": basis.burial_depth_m,
                "phase_spacing_m": basis.phase_spacing_m,
                "circuit_spacing_m": basis.circuit_spacing_m,
                "native_soil_thermal_resistivity_km_w": basis.soil_thermal_resistivity_km_w,
            })
            project.thermal_design.regions[0].source_reference = basis.soil_thermal_value_source
        if self.rec_radio.isChecked() and self._candidates:
            candidate = self._candidates[max(0, self.candidate_table.currentRow())]
            apply_candidate_to_project(candidate, basis, project.cable)
        elif self.section_radio.isChecked():
            project.cable.conductor_material = basis.conductor_preference if basis.conductor_preference in {"CU", "AL"} else "AL"
            project.cable.name = "Jenerik kesitle hızlı başlangıç"
        else:
            project.cable.name = "Kablo seçimi bekleniyor"
            project.design_progress.missing_data.append("Üretici/katalog veya manuel kablo seçimi")
        project.bonding = default_bonding_system(basis.total_route_length_m)
        project.bonding.phase_spacing_m = basis.phase_spacing_m
        return project

    def _back(self) -> None:
        self.pages.setCurrentIndex(max(0, self.pages.currentIndex() - 1)); self._update_step()

    def _next(self) -> None:
        index = self.pages.currentIndex()
        if not self._validate_step(index): return
        if index == self.pages.count() - 1:
            self.result_project = self._build_project_result()
            self.run_first_iteration = self.run_iteration_check.isChecked()
            self.accept(); return
        self.pages.setCurrentIndex(index + 1); self._update_step()

    def _update_step(self) -> None:
        labels = ["Proje", "Sistem ve yük", "Güzergâh ve ilk yerleşim", "Başlangıç kablosu", "İlk tasarım özeti"]
        index = self.pages.currentIndex()
        self.step_label.setText(f"Adım {index + 1}/5 — {labels[index]}")
        self.back_btn.setEnabled(index > 0)
        self.next_btn.setText("Projeyi Oluştur" if index == self.pages.count() - 1 else "İleri")

class StartDialog(QDialog):
    NEW_DESIGN = "NEW_DESIGN"
    REVIEW_DESIGN = "REVIEW_DESIGN"
    OPEN_PROJECT = "OPEN_PROJECT"
    SYNTHETIC_20KM_CASE = "SYNTHETIC_20KM_CASE"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.choice = ""
        self.setWindowTitle("DiTuS Kablo Analizör")
        fit_window(self, DENSITY_NORMAL, center_on=parent)
        layout = QVBoxLayout(self)
        title = QLabel("DiTuS Kablo Analizör™")
        title.setStyleSheet("font-size: 22px; font-weight: 750; padding: 12px;")
        subtitle = QLabel(
            "İşler karışmadan vaziyet alın. Yeni tasarımı yönlendirilmiş akışla oluşturun, "
            "mevcut tasarımı kontrol edin veya kayıtlı projeyi açın."
        )
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        for text, choice, description in (
            ("Yeni Kablo Sistemi Tasarla", self.NEW_DESIGN, "Sistem/yük → güzergâh → ilk yerleşim → kablo adayları → ilk birleşik hesap"),
            ("Mevcut Tasarımı Kontrol Et", self.REVIEW_DESIGN, "Boş proje alanını açar; kablo ve güzergâh verilerini manüel girerek doğrulama yaparsınız."),
            ("Projeyi Aç", self.OPEN_PROJECT, "Daha önce kaydedilmiş .ucd.json projesini açar."),
            (
                "Sentetik 20 km Örnek Hattı Aç",
                self.SYNTHETIC_20KM_CASE,
                "Herhangi bir gerçek tesis veya müşteriyi temsil etmeyen, 20 km çift devre yeraltı kablo örneğini açar.",
            ),
        ):
            button = QPushButton(text)
            button.setMinimumHeight(52)
            button.clicked.connect(lambda _checked=False, c=choice: self._choose(c))
            layout.addWidget(button)
            note = QLabel(description); note.setWordWrap(True); note.setStyleSheet("padding-left: 12px;")
            layout.addWidget(note)
        layout.addStretch(1)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()
