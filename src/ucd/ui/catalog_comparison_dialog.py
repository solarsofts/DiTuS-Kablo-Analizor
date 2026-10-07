from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ucd.calculations.catalog_comparison import (
    CatalogComparisonResult,
    compare_catalog_candidates,
    render_catalog_comparison_markdown,
    write_catalog_comparison_report,
)
from ucd.models.project import ProjectData
from .background_task import run_blocking_task
from .window_layout import fit_window, DENSITY_WIDE


class CatalogComparisonDialog(QDialog):
    def __init__(
        self,
        project: ProjectData,
        candidate_ids: Iterable[str] | None = None,
        physical_model_ampacity_a: float | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.candidate_ids = tuple(candidate_ids or ())
        # Bu pencere henüz görünür değil; meşgul göstergesi çağıran pencereye bağlanır.
        self.result: CatalogComparisonResult = run_blocking_task(
            parent, "Katalog karşılaştırma", "Katalog adayları teknik olarak karşılaştırılıyor…",
            compare_catalog_candidates,
            project,
            self.candidate_ids or None,
            maximum_parallel_cables=2,
            physical_model_ampacity_a=physical_model_ampacity_a,
        )
        self.setWindowTitle("Katalog Teknik Karşılaştırma ve Doğrulama")
        fit_window(self, DENSITY_WIDE)
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        heading = QLabel(
            "Katalog Teknik Karşılaştırma — sonuçlar nihai uygunluk değildir"
        )
        heading.setStyleSheet(
            "font-size: 13pt; font-weight: 700; padding: 8px; "
            "background: #eaf0f5; border-radius: 4px;"
        )
        layout.addWidget(heading)
        notice = QLabel(
            "Sıralama yalnız sonraki doğrulama önceliğini gösterir. Katalog akım taşıma kapasitesi, "
            "referans koşulları proje güzergâhıyla eşleştirilmeden nihai rating kabul edilmez."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet("background:#fff4cc;color:#725b18;padding:7px;")
        layout.addWidget(notice)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        summary_tab = QWidget()
        summary_layout = QVBoxLayout(summary_tab)
        self.summary_table = QTableWidget(0, 17)
        self.summary_table.setHorizontalHeaderLabels([
            "Sıra", "Üretici", "Model", "Malzeme", "Kesit", "Kablo/faz",
            "Iref aritmetik", "Iref normalize", "Ref. durumu", "Tasarım I", "Norm. marj", "ΔV",
            "Fizik model", "Veri", "Kapılar", "Doğrulama", "Kaynak",
        ])
        self.summary_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.summary_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.summary_table.setAlternatingRowColors(True)
        self.summary_table.itemSelectionChanged.connect(self._candidate_selected)
        summary_layout.addWidget(self.summary_table, 1)
        tabs.addTab(summary_tab, "Aday Özeti")

        matrix_tab = QWidget()
        matrix_layout = QVBoxLayout(matrix_tab)
        self.matrix_table = QTableWidget()
        self.matrix_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.matrix_table.setAlternatingRowColors(True)
        matrix_layout.addWidget(self.matrix_table, 1)
        tabs.addTab(matrix_tab, "Parametre Matrisi")

        details_tab = QWidget()
        details_layout = QVBoxLayout(details_tab)
        self.details_text = QPlainTextEdit()
        self.details_text.setReadOnly(True)
        details_layout.addWidget(self.details_text, 1)
        tabs.addTab(details_tab, "Aday Ayrıntısı")

        report_tab = QWidget()
        report_layout = QVBoxLayout(report_tab)
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)
        report_layout.addWidget(self.report_text, 1)
        tabs.addTab(report_tab, "Rapor Önizleme")

        actions = QHBoxLayout()
        export = QPushButton("JSON + Markdown + HTML Dışa Aktar…")
        export.clicked.connect(self._export)
        actions.addWidget(export)
        actions.addStretch(1)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        actions.addWidget(close)
        layout.addLayout(actions)

    def _populate(self) -> None:
        candidates = self.result.candidates
        self.summary_table.setRowCount(len(candidates))
        for row, item in enumerate(candidates):
            values = [
                item.rank,
                item.manufacturer,
                item.model,
                item.conductor_material,
                f"{item.conductor_area_mm2:g} mm²",
                item.parallel_cables_per_phase,
                f"{item.combined_reference_ampacity_a:.1f} A",
                "—" if item.adjusted_reference_ampacity_a is None else f"{item.adjusted_reference_ampacity_a:.1f} A",
                item.reference_validation_status,
                f"{item.required_design_current_a:.1f} A",
                "—" if item.normalized_design_margin_a is None else f"{item.normalized_design_margin_a:+.1f} A",
                "—" if item.voltage_drop_percent is None else f"%{item.voltage_drop_percent:.5f}",
                item.physical_comparison_status,
                item.completion_status,
                item.iteration_gate_status,
                item.verification_status,
                item.source_quality,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(Qt.UserRole, item.candidate_id)
                self.summary_table.setItem(row, col, cell)
        self.summary_table.resizeColumnsToContents()
        self.summary_table.horizontalHeader().setStretchLastSection(True)

        self.matrix_table.setColumnCount(2 + len(candidates))
        self.matrix_table.setHorizontalHeaderLabels(
            ["Parametre", "Birim", *(item.manufacturer for item in candidates)]
        )
        self.matrix_table.setRowCount(len(self.result.parameter_rows))
        for row_index, row in enumerate(self.result.parameter_rows):
            self.matrix_table.setItem(row_index, 0, QTableWidgetItem(row.label))
            self.matrix_table.setItem(row_index, 1, QTableWidgetItem(row.unit))
            lookup = dict(row.values)
            for col_index, candidate in enumerate(candidates, 2):
                self.matrix_table.setItem(
                    row_index,
                    col_index,
                    QTableWidgetItem(lookup.get(candidate.candidate_id, "—")),
                )
        self.matrix_table.resizeColumnsToContents()
        self.matrix_table.horizontalHeader().setStretchLastSection(True)
        self.report_text.setPlainText(render_catalog_comparison_markdown(self.result))
        if candidates:
            self.summary_table.selectRow(0)
        else:
            self.details_text.setPlainText("Karşılaştırılabilir katalog adayı bulunamadı.")

    def _candidate_selected(self) -> None:
        row = self.summary_table.currentRow()
        if row < 0 or row >= len(self.result.candidates):
            return
        item = self.result.candidates[row]
        lines = [
            f"{item.rank}. {item.manufacturer} — {item.model}",
            "",
            f"Aday: {item.candidate_id}",
            f"Gerilim sınıfı: {item.voltage_class}",
            f"İletken: {item.conductor_material} {item.conductor_area_mm2:g} mm²",
            f"Kablo/faz: {item.parallel_cables_per_phase}",
            f"Katalog Iref: {item.reference_ampacity_a_per_cable:.1f} A/kablo",
            f"Aritmetik Iref toplamı: {item.combined_reference_ampacity_a:.1f} A (uygunluk rating'i değildir)",
            f"Normalize Iref: " + ("hesaplanamadı" if item.adjusted_reference_ampacity_a is None else f"{item.adjusted_reference_ampacity_a:.1f} A"),
            f"Referans doğrulama: {item.reference_validation_status}; kritik bölge: {item.governing_reference_region_id or '—'}",
            f"Normalize tasarım marjı: " + ("—" if item.normalized_design_margin_a is None else f"{item.normalized_design_margin_a:+.1f} A (%{item.design_margin_percent:+.1f})"),
            f"Fiziksel model: {item.physical_comparison_status}" + (
                "" if item.physical_model_ampacity_a is None else f"; {item.physical_model_ampacity_a:.1f} A"
            ),
            f"Gerilim düşümü: " + (
                "hesaplanamadı" if item.voltage_drop_percent is None else f"%{item.voltage_drop_percent:.5f}"
            ),
            f"Katalog ön eleme: {item.screening_status}",
            f"Veri tamamlama: {item.completion_status}",
            f"İterasyon kapıları: {item.iteration_gate_status}",
            f"Doğrulama hükmü: {item.verification_status}",
            f"Kaynak: {item.source_quality} · {item.source_page or 'sayfa belirtilmemiş'}",
            f"Katalog skalerleri: {item.catalog_scalar_count} mevcut / {item.catalog_scalar_missing_count} eksik",
            "",
            "EKSİK / KOŞULLU VERİLER",
            *(f"- {value}" for value in item.missing_or_conditional_items),
            "",
            "UYARILAR",
            *(f"- {value}" for value in item.warnings),
            "",
            "KARAR DAYANAĞI",
            *(f"- {value}" for value in item.decision_basis),
        ]
        self.details_text.setPlainText("\n".join(lines))

    def _export(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Karşılaştırma raporu klasörü",
            str(Path.home()),
        )
        if not directory:
            return
        paths = write_catalog_comparison_report(
            self.result,
            directory,
            base_name=f"{self.project.project_code}_catalog_comparison_v0.16.9.4.34",
        )
        QMessageBox.information(
            self,
            "Rapor dışa aktarıldı",
            "\n".join(f"{key}: {path}" for key, path in paths.items()),
        )
