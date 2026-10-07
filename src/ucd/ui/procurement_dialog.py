from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QDoubleSpinBox,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ucd.calculations.procurement import (
    VIEW_BOM,
    VIEW_BOQ,
    VIEW_RFQ,
    ProcurementPackage,
    build_procurement_package,
    write_procurement_package,
)
from ucd.calculations.project_workflow import STATUS_COMPLETE, record_engine_run
from ucd.calculations.engine_precheck import evaluate_engine_precheck
from ucd.ui.engine_precheck_dialog import EnginePrecheckDialog
from ucd.models.project import ProcurementData, ProjectData
from ucd import __version__
from .background_task import run_blocking_task
from .window_layout import fit_window, DENSITY_WIDE


class ProcurementDialog(QDialog):
    """Single-screen BOQ/BOM/RFQ, metraj and drum-plan builder."""

    def __init__(self, project: ProjectData, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.package: ProcurementPackage | None = None
        self.setWindowTitle("DiTuS BOQ / BOM / RFQ Oluşturucu")
        fit_window(self, DENSITY_WIDE)

        root = QVBoxLayout(self)
        banner = QLabel(
            "Miktarlar proje güzergâhı, projeye atanmış kablo, bonding grafiği ve açık tedarik varsayımlarından türetilir. "
            "Otomatik metraj ile kullanıcı nihai miktarı ayrı tutulur."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            "font-weight:700; color:white; padding:10px; background:#17324a; border:1px solid #10293c;"
        )
        root.addWidget(banner)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        settings_box = QGroupBox("Metraj ve sipariş ayarları")
        settings_form = QFormLayout(settings_box)
        self.installation = self._percent_spin(project.procurement.installation_allowance_percent)
        self.waste = self._percent_spin(project.procurement.waste_percent)
        self.spare_cable = self._percent_spin(project.procurement.spare_cable_percent)
        self.term_tail = self._length_spin(project.procurement.termination_tail_m_per_end, 0, 100)
        self.joint_tail = self._length_spin(project.procurement.joint_tail_m_per_side, 0, 100)
        self.lead_allowance = self._percent_spin(project.procurement.bonding_lead_allowance_percent)
        self.warning_allowance = self._percent_spin(project.procurement.warning_tape_allowance_percent)
        self.cleat_spacing = self._length_spin(project.procurement.cable_cleat_spacing_m, 0.1, 10)
        self.max_drum = self._length_spin(project.procurement.maximum_drum_length_m, 10, 10000)
        self.rounding = self._length_spin(project.procurement.drum_length_rounding_m, 0.1, 100)
        self.spare_joint = QSpinBox(); self.spare_joint.setRange(0, 1000); self.spare_joint.setValue(project.procurement.spare_joint_units)
        self.spare_term = QSpinBox(); self.spare_term.setRange(0, 1000); self.spare_term.setValue(project.procurement.spare_termination_units)
        self.spare_lb = QSpinBox(); self.spare_lb.setRange(0, 1000); self.spare_lb.setValue(project.procurement.spare_link_box_units)
        self.spare_svl = QSpinBox(); self.spare_svl.setRange(0, 1000); self.spare_svl.setValue(project.procurement.spare_svl_units)
        self.include_civil = QCheckBox("Kazı, termal dolgu ve duct kalemlerini ekle"); self.include_civil.setChecked(project.procurement.include_civil_items)
        self.include_marking = QCheckBox("Cleat, ikaz bandı ve işaretlemeyi ekle"); self.include_marking.setChecked(project.procurement.include_marking_accessories)
        self.include_grounding = QCheckBox("Topraklama ve GCC/ECC kalemlerini ekle"); self.include_grounding.setChecked(project.procurement.include_grounding_items)

        for label, widget in (
            ("Montaj payı [%]", self.installation),
            ("Fire payı [%]", self.waste),
            ("Yedek kablo [%]", self.spare_cable),
            ("Terminasyon kuyruğu [m/uç]", self.term_tail),
            ("Joint kuyruğu [m/taraf]", self.joint_tail),
            ("Bonding lead payı [%]", self.lead_allowance),
            ("İkaz bandı payı [%]", self.warning_allowance),
            ("Cleat aralığı [m]", self.cleat_spacing),
            ("Azami makara boyu [m]", self.max_drum),
            ("Kesim yuvarlama [m]", self.rounding),
            ("Yedek joint [adet]", self.spare_joint),
            ("Yedek termination [adet]", self.spare_term),
            ("Yedek link box [adet]", self.spare_lb),
            ("Yedek SVL [adet]", self.spare_svl),
        ):
            settings_form.addRow(label, widget)
        settings_form.addRow(self.include_civil)
        settings_form.addRow(self.include_marking)
        settings_form.addRow(self.include_grounding)

        settings_panel = QVBoxLayout()
        settings_panel.addWidget(settings_box)
        self.rebuild_button = QPushButton("Metrajı Yeniden Hesapla")
        self.rebuild_button.clicked.connect(self.rebuild)
        self.apply_button = QPushButton("Ayarları Projeye Kaydet")
        self.apply_button.clicked.connect(self.apply_settings)
        self.export_button = QPushButton("BOQ/BOM/RFQ Çıktılarını Oluştur…")
        self.export_button.clicked.connect(self.export_package)
        settings_panel.addWidget(self.rebuild_button)
        settings_panel.addWidget(self.apply_button)
        settings_panel.addWidget(self.export_button)
        settings_panel.addStretch(1)
        settings_widget = QWidget(); settings_widget.setLayout(settings_panel)
        settings_widget.setMaximumWidth(410)
        body.addWidget(settings_widget)

        self.tabs = QTabWidget()
        self.boq_table = self._line_table()
        self.bom_table = self._line_table()
        self.rfq_table = self._rfq_table()
        self.drum_table = QTableWidget(0, 12)
        self.drum_table.setHorizontalHeaderLabels([
            "Makara", "Azami [m]", "Kesim [m]", "Fire/pay [m]", "Yedek [m]", "Toplam [m]",
            "Bakiye [m]", "Kalan [m]", "Aşım [m]", "Kesimler", "Durum", "Not"
        ])
        self.drum_table.horizontalHeader().setStretchLastSection(True)
        self.warning_table = QTableWidget(0, 2)
        self.warning_table.setHorizontalHeaderLabels(["Tür", "Açıklama"])
        self.warning_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.boq_table, "BOQ / Metraj")
        self.tabs.addTab(self.bom_table, "BOM / Malzeme")
        self.tabs.addTab(self.rfq_table, "RFQ / Teklif Listesi")
        self.tabs.addTab(self.drum_table, "Makara Planı")
        self.tabs.addTab(self.warning_table, "Varsayımlar ve Uyarılar")
        body.addWidget(self.tabs, 1)

        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_buttons.rejected.connect(self.reject)
        root.addWidget(close_buttons)
        self.rebuild()

    @staticmethod
    def _percent_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(); spin.setRange(0, 100); spin.setDecimals(2); spin.setSuffix(" %"); spin.setValue(value)
        return spin

    @staticmethod
    def _length_spin(value: float, minimum: float, maximum: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(); spin.setRange(minimum, maximum); spin.setDecimals(2); spin.setValue(value)
        return spin

    @staticmethod
    def _line_table() -> QTableWidget:
        table = QTableWidget(0, 10)
        table.setHorizontalHeaderLabels([
            "Kalem", "Kategori", "Tanım", "Teknik özellik", "Otomatik", "Nihai", "Birim", "Durum", "Dayanak", "Override"
        ])
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def _rfq_table() -> QTableWidget:
        table = QTableWidget(0, 12)
        table.setHorizontalHeaderLabels([
            "Kalem", "Tanım", "Teknik özellik", "Miktar", "Birim", "Durum", "İstenen belgeler",
            "Teklif marka/model", "Teknik uygunluk", "Birim fiyat", "Teslim", "Tedarikçi notu"
        ])
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _settings(self) -> ProcurementData:
        current = deepcopy(self.project.procurement)
        current.installation_allowance_percent = self.installation.value()
        current.waste_percent = self.waste.value()
        current.spare_cable_percent = self.spare_cable.value()
        current.termination_tail_m_per_end = self.term_tail.value()
        current.joint_tail_m_per_side = self.joint_tail.value()
        current.bonding_lead_allowance_percent = self.lead_allowance.value()
        current.warning_tape_allowance_percent = self.warning_allowance.value()
        current.cable_cleat_spacing_m = self.cleat_spacing.value()
        current.maximum_drum_length_m = self.max_drum.value()
        current.drum_length_rounding_m = self.rounding.value()
        current.spare_joint_units = self.spare_joint.value()
        current.spare_termination_units = self.spare_term.value()
        current.spare_link_box_units = self.spare_lb.value()
        current.spare_svl_units = self.spare_svl.value()
        current.include_civil_items = self.include_civil.isChecked()
        current.include_marking_accessories = self.include_marking.isChecked()
        current.include_grounding_items = self.include_grounding.isChecked()
        return current

    def rebuild(self) -> None:
        try:
            self.package = build_procurement_package(self.project, self._settings())
        except Exception as exc:
            QMessageBox.critical(self, "Metraj üretilemedi", str(exc))
            return
        self._populate_line_table(self.boq_table, self.package.lines_for_view(VIEW_BOQ))
        self._populate_line_table(self.bom_table, self.package.lines_for_view(VIEW_BOM))
        self._populate_rfq()
        self._populate_drums()
        self._populate_warnings()

    @staticmethod
    def _populate_line_table(table: QTableWidget, items) -> None:
        table.setRowCount(len(items))
        for row, item in enumerate(items):
            values = (
                item.item_id, item.category, item.description, item.technical_specification,
                f"{item.auto_quantity:.3f}", f"{item.final_quantity:.3f}", item.unit,
                item.status, item.basis.formula, item.override_rationale,
            )
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(str(value)))
        table.resizeColumnsToContents()
        table.setColumnWidth(2, 250); table.setColumnWidth(3, 430); table.setColumnWidth(8, 300)

    def _populate_rfq(self) -> None:
        assert self.package is not None
        items = self.package.lines_for_view(VIEW_RFQ)
        self.rfq_table.setRowCount(len(items))
        for row, item in enumerate(items):
            values = (
                item.item_id, item.description, item.technical_specification,
                f"{item.final_quantity:.3f}", item.unit, item.status, "\n".join(item.required_documents),
                "", "", "", "", "",
            )
            for col, value in enumerate(values):
                self.rfq_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.rfq_table.resizeColumnsToContents()
        self.rfq_table.setColumnWidth(1, 240); self.rfq_table.setColumnWidth(2, 430); self.rfq_table.setColumnWidth(6, 330)

    def _populate_drums(self) -> None:
        assert self.package is not None
        self.drum_table.setRowCount(len(self.package.drums))
        for row, drum in enumerate(self.package.drums):
            values = (
                drum.drum_id, f"{drum.maximum_length_m:.1f}", f"{drum.route_cut_length_m:.1f}",
                f"{drum.order_allowance_m:.1f}", f"{drum.spare_stock_length_m:.1f}",
                f"{drum.loaded_length_m:.1f}", f"{drum.capacity_balance_m:.1f}",
                f"{drum.remaining_capacity_m:.1f}", f"{drum.overload_m:.1f}",
                ", ".join(f"{cut.cut_id}:{cut.required_cut_length_m:g}m" for cut in drum.cuts),
                drum.assignment_status, drum.notes,
            )
            for col, value in enumerate(values): self.drum_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.drum_table.resizeColumnsToContents(); self.drum_table.setColumnWidth(9, 460); self.drum_table.setColumnWidth(11, 300)

    def _populate_warnings(self) -> None:
        assert self.package is not None
        rows = [("VARSAYIM", item) for item in self.package.assumptions] + [("UYARI", item) for item in self.package.warnings]
        self.warning_table.setRowCount(len(rows))
        for row, (kind, text) in enumerate(rows):
            self.warning_table.setItem(row, 0, QTableWidgetItem(kind)); self.warning_table.setItem(row, 1, QTableWidgetItem(text))
        self.warning_table.resizeColumnsToContents(); self.warning_table.setColumnWidth(1, 900)

    def apply_settings(self) -> None:
        self.project.procurement = self._settings()
        self.rebuild()
        QMessageBox.information(self, "Tedarik ayarları", "BOQ/BOM/RFQ ayarları proje modeline kaydedildi.")

    def export_package(self) -> None:
        precheck = evaluate_engine_precheck(self.project, "procurement")
        mascot_path = Path(__file__).resolve().parents[3] / "assets" / "ditus_mascot.png"
        decision = EnginePrecheckDialog(precheck, mascot_path, self).exec()
        if decision != EnginePrecheckDialog.RUN:
            if decision == EnginePrecheckDialog.OPEN_MISSING:
                QMessageBox.information(
                    self, "Eksik veri",
                    "Eksik girdileri ana proje ağacındaki ilgili aşamadan tamamlayın."
                )
            return
        self.rebuild()
        if self.package is None:
            return
        directory = QFileDialog.getExistingDirectory(self, "BOQ/BOM/RFQ çıktı klasörü", str(Path.home()))
        if not directory:
            return
        try:
            paths = run_blocking_task(
                self, "BOQ / BOM / RFQ", "Tedarik çıktıları (xlsx, csv, json, html, md, docx, pdf) yazılıyor…",
                write_procurement_package,
                self.package,
                directory,
                f"{self.project.project_code}_BOQ_BOM_RFQ_v{__version__}",
                ("xlsx", "csv", "json", "html", "markdown", "docx", "pdf"),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Tedarik çıktısı üretilemedi", str(exc))
            return
        record_engine_run(
            self.project, "procurement", STATUS_COMPLETE, result_count=len(paths),
            warning_count=len(self.package.warnings),
            message=f"{len(paths)} BOQ/BOM/RFQ çıktısı oluşturuldu.",
            conditional_reasons=list(self.package.warnings[:8]),
            precheck=precheck.to_dict(),
        )
        QMessageBox.information(
            self,
            "BOQ/BOM/RFQ tamamlandı",
            "Çıktılar oluşturuldu:\n" + "\n".join(f"{key.upper()}: {path}" for key, path in paths.items()),
        )
