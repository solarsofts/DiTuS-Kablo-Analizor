from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ucd.calculations.reporting import (
    ALL_MODULES,
    MODULE_LABELS,
    MODULE_WARNINGS,
    REPORT_DESIGN,
    REPORT_TEMPLATES,
    REPORT_TYPE_LABELS,
    CalculationResultsBundle,
    ReportConfiguration,
    ReportMetadata,
    build_project_report,
    render_project_report_markdown,
    write_project_report,
)
from ucd.calculations.project_workflow import STATUS_COMPLETE, record_engine_run
from ucd.calculations.engine_precheck import evaluate_engine_precheck
from ucd.ui.engine_precheck_dialog import EnginePrecheckDialog
from ucd.models.project import ProjectData
from ucd import __version__
from .background_task import run_blocking_task
from .window_layout import fit_window, DENSITY_WIDE


class ReportBuilderDialog(QDialog):
    """Single-screen report builder: templates + module selection + live preview.

    This deliberately avoids a long wizard. The mandatory warnings module can
    never be unchecked, so a user cannot accidentally hide blocking data or
    conditional calculation states from a generated report.
    """

    def __init__(
        self,
        project: ProjectData,
        results: CalculationResultsBundle,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.results = results
        self.current_report = None
        self.setWindowTitle("DiTuS Rapor Oluşturucu")
        fit_window(self, DENSITY_WIDE)

        root = QVBoxLayout(self)
        banner = QLabel(
            "Hazır şablon seçin, rapora girecek modülleri işaretleyin ve çıktıları tek işlemle üretin. "
            "Uyarılar ve sınırlamalar bölümü zorunludur."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet("font-weight:700; padding:8px; background:#eaf0f5; border:1px solid #c9d6df;")
        root.addWidget(banner)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        config_scroll = QScrollArea()
        config_scroll.setWidgetResizable(True)
        config_panel = QWidget()
        config_layout = QVBoxLayout(config_panel)
        config_scroll.setWidget(config_panel)
        splitter.addWidget(config_scroll)

        type_box = QGroupBox("Rapor şablonu")
        type_form = QFormLayout(type_box)
        self.type_combo = QComboBox()
        for key, label in REPORT_TYPE_LABELS.items():
            self.type_combo.addItem(label, key)
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(REPORT_DESIGN)))
        type_form.addRow("Rapor türü", self.type_combo)
        config_layout.addWidget(type_box)

        identity_box = QGroupBox("Doküman bilgileri")
        identity_form = QFormLayout(identity_box)
        self.title_edit = QLineEdit()
        self.title_edit.textEdited.connect(lambda: self.title_edit.setProperty("auto_title", False))
        self.document_edit = QLineEdit(f"{project.project_code}-RPT-001")
        self.revision_edit = QLineEdit("00")
        self.date_edit = QLineEdit()
        self.client_edit = QLineEdit()
        self.contractor_edit = QLineEdit()
        self.prepared_edit = QLineEdit()
        self.checked_edit = QLineEdit()
        self.status_edit = QLineEdit("TASLAK")
        identity_form.addRow("Başlık", self.title_edit)
        identity_form.addRow("Doküman no", self.document_edit)
        identity_form.addRow("Revizyon", self.revision_edit)
        identity_form.addRow("Yayın tarihi", self.date_edit)
        identity_form.addRow("İşveren", self.client_edit)
        identity_form.addRow("Yüklenici", self.contractor_edit)
        identity_form.addRow("Hazırlayan", self.prepared_edit)
        identity_form.addRow("Kontrol eden", self.checked_edit)
        identity_form.addRow("Onay durumu", self.status_edit)
        config_layout.addWidget(identity_box)

        modules_box = QGroupBox("Rapor bölümleri")
        modules_layout = QVBoxLayout(modules_box)
        self.module_checks: dict[str, QCheckBox] = {}
        for module in ALL_MODULES:
            checkbox = QCheckBox(MODULE_LABELS[module])
            checkbox.setProperty("module_id", module)
            if module == MODULE_WARNINGS:
                checkbox.setChecked(True)
                checkbox.setEnabled(False)
                checkbox.setToolTip("Kritik uyarılar ve sınırlamalar rapordan çıkarılamaz.")
            checkbox.toggled.connect(self.refresh_preview)
            self.module_checks[module] = checkbox
            modules_layout.addWidget(checkbox)
        config_layout.addWidget(modules_box)

        options_box = QGroupBox("Çıktı ve ayrıntı")
        options_layout = QVBoxLayout(options_box)
        self.trace_check = QCheckBox("Ayrıntılı hesap izini rapora yaz")
        self.trace_check.toggled.connect(self.refresh_preview)
        options_layout.addWidget(self.trace_check)
        self.format_checks: dict[str, QCheckBox] = {}
        for fmt, label in (
            ("docx", "Word (.docx)"),
            ("pdf", "PDF"),
            ("html", "Bağımsız HTML"),
            ("markdown", "Markdown"),
            ("json", "Makinece okunabilir JSON"),
        ):
            checkbox = QCheckBox(label)
            checkbox.setChecked(True)
            self.format_checks[fmt] = checkbox
            options_layout.addWidget(checkbox)
        config_layout.addWidget(options_box)

        buttons_row = QHBoxLayout()
        self.preview_button = QPushButton("Önizlemeyi Yenile")
        self.preview_button.clicked.connect(self.refresh_preview)
        self.export_button = QPushButton("Raporları Oluştur…")
        self.export_button.clicked.connect(self.export_reports)
        buttons_row.addWidget(self.preview_button)
        buttons_row.addWidget(self.export_button)
        config_layout.addLayout(buttons_row)
        config_layout.addStretch(1)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.addWidget(QLabel("Canlı metin önizleme"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        preview_layout.addWidget(self.preview, 1)
        splitter.addWidget(preview_panel)
        splitter.setSizes([420, 840])

        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_buttons.rejected.connect(self.reject)
        root.addWidget(close_buttons)

        self.type_combo.currentIndexChanged.connect(self.apply_template)
        self._set_default_date()
        self.apply_template()

    def _set_default_date(self) -> None:
        from datetime import date

        self.date_edit.setText(date.today().isoformat())

    def apply_template(self) -> None:
        report_type = str(self.type_combo.currentData())
        selected = set(REPORT_TEMPLATES.get(report_type, REPORT_TEMPLATES[REPORT_DESIGN]))
        for module, checkbox in self.module_checks.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(module in selected or module == MODULE_WARNINGS)
            checkbox.blockSignals(False)
        if not self.title_edit.text().strip() or self.title_edit.property("auto_title"):
            self.title_edit.setText(REPORT_TYPE_LABELS.get(report_type, "DiTuS Teknik Rapor"))
            self.title_edit.setProperty("auto_title", True)
        self.refresh_preview()

    def _configuration(self) -> ReportConfiguration:
        report_type = str(self.type_combo.currentData())
        metadata = ReportMetadata(
            report_type=report_type,
            title=self.title_edit.text().strip(),
            document_no=self.document_edit.text().strip(),
            revision=self.revision_edit.text().strip(),
            issue_date=self.date_edit.text().strip(),
            client=self.client_edit.text().strip(),
            contractor=self.contractor_edit.text().strip(),
            prepared_by=self.prepared_edit.text().strip(),
            checked_by=self.checked_edit.text().strip(),
            approval_status=self.status_edit.text().strip() or "TASLAK",
        )
        selected = tuple(module for module, checkbox in self.module_checks.items() if checkbox.isChecked())
        formats = tuple(fmt for fmt, checkbox in self.format_checks.items() if checkbox.isChecked())
        return ReportConfiguration(
            metadata=metadata,
            selected_modules=selected,
            include_detailed_trace=self.trace_check.isChecked(),
            include_empty_selected_modules=True,
            output_formats=formats,
        )

    def refresh_preview(self) -> None:
        try:
            config = self._configuration()
            self.current_report = build_project_report(self.project, config, self.results)
            self.preview.setPlainText(render_project_report_markdown(self.current_report))
        except Exception as exc:
            self.current_report = None
            self.preview.setPlainText(f"Önizleme üretilemedi:\n{exc}")

    def export_reports(self) -> None:
        precheck = evaluate_engine_precheck(self.project, "report")
        mascot_path = Path(__file__).resolve().parents[3] / "assets" / "ditus_mascot.png"
        decision = EnginePrecheckDialog(precheck, mascot_path, self).exec()
        if decision != EnginePrecheckDialog.RUN:
            if decision == EnginePrecheckDialog.OPEN_MISSING:
                QMessageBox.information(
                    self, "Eksik veri",
                    "Eksik girdileri ana proje ağacındaki ilgili aşamadan tamamlayın."
                )
            return
        config = self._configuration()
        if not config.output_formats:
            QMessageBox.warning(self, "Çıktı formatı", "En az bir çıktı formatı seçin.")
            return
        directory = QFileDialog.getExistingDirectory(self, "Rapor klasörünü seç", str(Path.home()))
        if not directory:
            return
        project, results = self.project, self.results
        base_name = f"{project.project_code}_{config.metadata.report_type}_v{__version__}"

        def _build_and_write():
            report = build_project_report(project, config, results)
            return report, write_project_report(report, directory, base_name, config.output_formats)

        try:
            report, paths = run_blocking_task(
                self, "Rapor oluşturucu", "Rapor derleniyor ve çıktı dosyaları yazılıyor…",
                _build_and_write,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Rapor üretilemedi", str(exc))
            return
        self.current_report = report
        record_engine_run(
            self.project, "report", STATUS_COMPLETE, result_count=len(paths),
            message=f"{len(paths)} rapor çıktısı oluşturuldu: " + ", ".join(sorted(paths)),
            precheck=precheck.to_dict(),
        )
        lines = ["Rapor çıktıları oluşturuldu:"]
        lines.extend(f"{key.upper()}: {path}" for key, path in paths.items())
        QMessageBox.information(self, "Rapor tamamlandı", "\n".join(lines))
