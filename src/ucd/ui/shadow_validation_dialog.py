from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ucd.calculations.shadow_validation import (
    ShadowValidationInputError,
    ShadowValidationResult,
    render_shadow_validation,
    run_shadow_validation,
)
from ucd.models.project import ProjectData
from .background_task import run_blocking_task
from .window_layout import fit_window, DENSITY_WIDE


def _cell(value: object) -> QTableWidgetItem:
    item = QTableWidgetItem(str(value))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _value(value: float | None) -> str:
    return "-" if value is None else f"{value:.9g}"


class ShadowValidationDialog(QDialog):
    """Read-only v0.16.8 validation and legacy/physical comparison window."""

    def __init__(self, project: ProjectData, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.result: ShadowValidationResult | None = None
        self.setWindowTitle("DiTuS — Fiziksel Motor Doğrulama ve Shadow Karşılaştırma")
        fit_window(self, DENSITY_WIDE)

        layout = QVBoxLayout(self)
        notice = QLabel(
            "v0.16.8 SHADOW_VALIDATION — kilitli IEC/bonding/nodal üretim yolu ile kapalı çevrim fiziksel "
            "motor aynı proje snapshot'ında karşılaştırılır. Numerik/korunum kapıları, model kapsamı, "
            "kaynak kökeni ve yayımlanmış benchmark kanıtı ayrı gösterilir. Hiçbir proje veya hesap girdisi değiştirilmez."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet(
            "font-weight:700; color:#174a6e; background:#eef6fb; border:1px solid #a8c7dc; "
            "padding:8px; border-radius:5px;"
        )
        layout.addWidget(notice)

        controls = QGridLayout()
        controls.addWidget(QLabel("2D ağ ölçeği"), 0, 0)
        self.mesh_scale = QDoubleSpinBox()
        self.mesh_scale.setRange(0.75, 4.0)
        self.mesh_scale.setDecimals(2)
        self.mesh_scale.setValue(3.0)
        controls.addWidget(self.mesh_scale, 0, 1)

        controls.addWidget(QLabel("Kapalı çevrim maks. iterasyon"), 0, 2)
        self.closed_iterations = QSpinBox()
        self.closed_iterations.setRange(3, 60)
        self.closed_iterations.setValue(15)
        controls.addWidget(self.closed_iterations, 0, 3)

        controls.addWidget(QLabel("Ampacity maks. iterasyon"), 0, 4)
        self.rating_iterations = QSpinBox()
        self.rating_iterations.setRange(2, 40)
        self.rating_iterations.setValue(10)
        controls.addWidget(self.rating_iterations, 0, 5)

        run_button = QPushButton("Doğrulama ve Shadow Karşılaştırmayı Çalıştır")
        run_button.clicked.connect(self.run_validation)
        controls.addWidget(run_button, 0, 6)
        controls.setColumnStretch(6, 1)
        layout.addLayout(controls)

        self.summary = QLabel("Henüz doğrulama çalıştırılmadı.")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("padding:7px; font-weight:700;")
        layout.addWidget(self.summary)

        self.tabs = QTabWidget()

        self.metric_table = QTableWidget(0, 10)
        self.metric_table.setHorizontalHeaderLabels([
            "Kategori", "Metrik", "Legacy", "Fiziksel", "Birim", "Δ", "Δ [%]",
            "Durum", "Neden kodu", "Açıklama",
        ])
        self.metric_table.setAlternatingRowColors(True)
        self.tabs.addTab(self.metric_table, "Legacy ↔ Fiziksel")

        self.gate_table = QTableWidget(0, 8)
        self.gate_table.setHorizontalHeaderLabels([
            "Kategori", "Kapı", "Durum", "Bloke", "Ölçülen", "Kabul sınırı", "Başlık", "Mesaj",
        ])
        self.gate_table.setAlternatingRowColors(True)
        self.tabs.addTab(self.gate_table, "Kabul Kapıları")

        self.benchmark_table = QTableWidget(0, 7)
        self.benchmark_table.setHorizontalHeaderLabels([
            "Benchmark", "Başlık", "Durum", "Bloke", "Vaka", "Kanıt", "Açıklama",
        ])
        self.benchmark_table.setAlternatingRowColors(True)
        self.tabs.addTab(self.benchmark_table, "IEC/CIGRE Benchmark")

        self.trace_edit = QPlainTextEdit()
        self.trace_edit.setReadOnly(True)
        self.tabs.addTab(self.trace_edit, "İz, Kapsam ve Sınırlar")
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def run_validation(self) -> None:
        try:
            self.result = run_blocking_task(
                self, "Shadow doğrulama", "Doğrulama ve shadow karşılaştırma çalışıyor…",
                run_shadow_validation,
                self.project,
                mesh_scale=float(self.mesh_scale.value()),
                maximum_closed_loop_iterations=int(self.closed_iterations.value()),
                maximum_rating_iterations=int(self.rating_iterations.value()),
            )
        except ShadowValidationInputError as exc:
            QMessageBox.critical(self, "Shadow doğrulama girdi/çözüm hatası", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Shadow doğrulama beklenmeyen hata", f"Beklenmeyen hata:\n{exc}")
            return
        self._populate()

    def _populate(self) -> None:
        result = self.result
        if result is None:
            return
        self.summary.setText(
            f"Öneri={result.promotion_recommendation}; bloke kapı={result.blocking_gate_count}; "
            f"başarısız/bloke={result.failed_gate_count}; uyarı={result.warning_gate_count}; "
            f"fiziksel Tmax={result.physical_coupled.final_thermal.maximum_nodal_conductor_temperature_c:.3f} °C; "
            f"fiziksel network sheath-loss ratio={result.physical_coupled.final_global_em.lambda1:.8f}"
        )
        ready = result.final_design_ready
        self.summary.setStyleSheet(
            "padding:7px; font-weight:700; color:#1c6b38;" if ready
            else "padding:7px; font-weight:700; color:#9b4b00;"
        )

        self.metric_table.setRowCount(len(result.metrics))
        for row, item in enumerate(result.metrics):
            values = [
                item.category, item.label, _value(item.legacy_value), _value(item.physical_value),
                item.unit, _value(item.absolute_difference),
                "-" if item.difference_percent is None else f"{item.difference_percent:+.4f}",
                item.status, item.reason_code, item.explanation,
            ]
            for col, value in enumerate(values):
                self.metric_table.setItem(row, col, _cell(value))

        self.gate_table.setRowCount(len(result.gates))
        for row, item in enumerate(result.gates):
            values = [
                item.category, item.gate_id, item.status, "EVET" if item.blocking else "HAYIR",
                item.measured_value, item.acceptance_limit, item.label, item.message,
            ]
            for col, value in enumerate(values):
                self.gate_table.setItem(row, col, _cell(value))

        self.benchmark_table.setRowCount(len(result.benchmarks))
        for row, item in enumerate(result.benchmarks):
            values = [
                item.benchmark_id, item.title, item.status, "EVET" if item.blocking else "HAYIR",
                item.case_count, item.evidence_reference or "-", item.message,
            ]
            for col, value in enumerate(values):
                self.benchmark_table.setItem(row, col, _cell(value))

        for table in (self.metric_table, self.gate_table, self.benchmark_table):
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)
        self.trace_edit.setPlainText(render_shadow_validation(result))
        self.tabs.setCurrentWidget(self.gate_table)
