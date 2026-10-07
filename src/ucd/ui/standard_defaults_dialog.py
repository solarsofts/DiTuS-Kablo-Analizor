"""Ayarlar → Standart Katsayıları ve Varsayılanlar.

Bu ekran hesap motorlarının evrensel zorunlu veri kapısı değildir. Kurumun veya
kullanıcının tercih ettiği proje başlangıç değerlerini ve dış kaynaklı katsayıları
provenance ile saklayan isteğe bağlı bir profil alanıdır.

Hesap motorları öncelikle aktif proje/kablo verisini ve kod içinde uygulanan
standart denklem/katsayı resolver'larını kullanır. Proje/site/malzeme özelindeki
değerler burada boş olsa dahi, proje verisi tam ise motor yalnız bu nedenle
bloklanmaz.

Uygulama standart metni, tablo görüntüsü veya açıklama metni çoğaltmaz; referans
kimliği ve kullanıcının kendi kaynak bilgisini saklar.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ucd.fileio import atomic_write_text

from .window_layout import DENSITY_NORMAL, fit_window

__all__ = [
    "StandardDefaults",
    "StandardDefaultsDialog",
    "CoefficientEntry",
    "PROVENANCE_CHOICES",
    "load_standard_defaults",
    "save_standard_defaults",
    "missing_default_fields",
]

# Provenance seçenekleri.  "Standart nüshası" kullanıcının kendi lisanslı
# kopyasıdır; DiTuS o metni taşımaz.
PROVENANCE_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "— seçilmedi —"),
    ("MANUFACTURER", "Üretici veri sayfası"),
    ("USER_STANDARD_COPY", "Kendi standart nüsham"),
    ("DERIVED_FROM_MEASURED_RAC_RDC", "Ölçülen Rac/Rdc'den geri hesap"),
    ("ORGANISATION_PACK", "Kurum onaylı katsayı paketi"),
)


@dataclass
class CoefficientEntry:
    """Tek bir ön tanım değeri ve kaynağı."""

    value: float = 0.0
    provenance: str = ""
    reference: str = ""

    @property
    def is_complete(self) -> bool:
        return self.value > 0.0 and bool(self.provenance)


@dataclass
class StandardDefaults:
    """Tüm haller için kullanıcı ön tanımları."""

    schema: str = "ditus-standard-defaults/1"

    # --- İletken: ks / kp -------------------------------------------------
    ks_round_solid: CoefficientEntry = field(default_factory=lambda: CoefficientEntry(1.0, "USER_STANDARD_COPY"))
    kp_round_solid: CoefficientEntry = field(default_factory=lambda: CoefficientEntry(1.0, "USER_STANDARD_COPY"))
    ks_round_stranded: CoefficientEntry = field(default_factory=CoefficientEntry)
    kp_round_stranded: CoefficientEntry = field(default_factory=CoefficientEntry)
    ks_milliken_cu: CoefficientEntry = field(default_factory=CoefficientEntry)
    kp_milliken_cu: CoefficientEntry = field(default_factory=CoefficientEntry)
    ks_milliken_al: CoefficientEntry = field(default_factory=CoefficientEntry)
    kp_milliken_al: CoefficientEntry = field(default_factory=CoefficientEntry)

    # --- Zemin ve ortam ---------------------------------------------------
    soil_resistivity_km_w: CoefficientEntry = field(default_factory=CoefficientEntry)
    ambient_ground_temperature_c: CoefficientEntry = field(default_factory=CoefficientEntry)
    burial_depth_m: CoefficientEntry = field(default_factory=CoefficientEntry)
    solar_radiation_w_m2: CoefficientEntry = field(default_factory=CoefficientEntry)

    # --- Yalıtkan ---------------------------------------------------------
    relative_permittivity: CoefficientEntry = field(default_factory=CoefficientEntry)
    dielectric_tan_delta: CoefficientEntry = field(default_factory=CoefficientEntry)

    # --- Kılıf / ekran ----------------------------------------------------
    sheath_eddy_lambda1_second: CoefficientEntry = field(default_factory=CoefficientEntry)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: (asdict(value) if isinstance(value, CoefficientEntry) else value)
            for key, value in asdict(self).items()
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StandardDefaults":
        item = cls()
        for key, value in (payload or {}).items():
            if not hasattr(item, key):
                continue
            current = getattr(item, key)
            if isinstance(current, CoefficientEntry) and isinstance(value, dict):
                setattr(
                    item,
                    key,
                    CoefficientEntry(
                        float(value.get("value", 0.0) or 0.0),
                        str(value.get("provenance", "") or ""),
                        str(value.get("reference", "") or ""),
                    ),
                )
        return item


# Alan tanımı: (nitelik, etiket, birim, standart adresi, zorunlu mu)
FIELD_GROUPS: tuple[tuple[str, str, tuple[tuple[str, str, str, str, bool], ...]], ...] = (
    (
        "conductor",
        "İletken — ks / kp",
        (
            ("ks_round_solid", "ks — yuvarlak masif", "—",
             "IEC 60287-1-1 Çizelge 2 · yuvarlak masif satırı. Düzeltmesiz hal için 1,0.", True),
            ("kp_round_solid", "kp — yuvarlak masif", "—",
             "IEC 60287-1-1 Çizelge 2 · yuvarlak masif satırı. Düzeltmesiz hal için 1,0.", True),
            ("ks_round_stranded", "ks — yuvarlak çok telli", "—",
             "IEC 60287-1-1 Çizelge 2 · iletken malzemesi ve yalıtım sistemine karşılık gelen satır.", True),
            ("kp_round_stranded", "kp — yuvarlak çok telli", "—",
             "IEC 60287-1-1 Çizelge 2 · ekstrüde ve kâğıt/akışkan yalıtım için ayrı değer verir.", True),
            ("ks_milliken_cu", "ks — Cu Milliken", "—",
             "IEC 60287-1-1 Çizelge 2 · Cu dilimli Milliken; tel yalıtımı ve büküm yönüne göre değişir.", False),
            ("kp_milliken_cu", "kp — Cu Milliken", "—",
             "IEC 60287-1-1 Çizelge 2 · Cu dilimli Milliken satırı.", False),
            ("ks_milliken_al", "ks — Al Milliken", "—",
             "IEC 60287-1-1 Çizelge 2 · Al dilimli Milliken satırı.", False),
            ("kp_milliken_al", "kp — Al Milliken", "—",
             "IEC 60287-1-1 Çizelge 2 · Al dilimli Milliken satırı.", False),
        ),
    ),
    (
        "soil",
        "Zemin ve Ortam",
        (
            ("soil_resistivity_km_w", "Zemin ısıl özdirenci", "K·m/W",
             "IEC 60287-3-1 md. 4.2.3 Çizelge 2 · zemin nem durumuna göre. Ulusal tablo varsa o önceliklidir.", True),
            ("ambient_ground_temperature_c", "Ortam zemin sıcaklığı (1 m)", "°C",
             "IEC 60287-3-1 md. 4.2.2 Çizelge 1 · iklim sınıfına göre 1 m derinlikteki değer.", True),
            ("burial_depth_m", "Standart gömme derinliği", "m",
             "IEC 60287-3-1 md. 4.2.2 · proje verisi yoksa standart derinlik. "
             "Derinlik kablo eksenine veya trefoil merkezine ölçülür (IEC 60287-2-1 sembol L).", True),
            ("solar_radiation_w_m2", "Güneş ışınımı", "W/m²",
             "IEC 60287-3-1 md. 4.2.4 · ulusal değer yoksa kullanılacak yoğunluk.", False),
        ),
    ),
    (
        "dielectric",
        "Yalıtkan",
        (
            ("relative_permittivity", "Bağıl dielektrik sabiti εr", "—",
             "IEC 60287-1-1 Çizelge 1 · yalıtkan malzemesine karşılık gelen satır.", True),
            ("dielectric_tan_delta", "Kayıp faktörü tan δ", "—",
             "IEC 60287-1-1 Çizelge 1 · yalıtkan malzemesine karşılık gelen satır.", True),
        ),
    ),
    (
        "sheath",
        "Kılıf / Ekran",
        (
            ("sheath_eddy_lambda1_second", "λ1″ fuko kayıp faktörü", "—",
             "IEC 60287-1-1 md. 2.3.6.1 · yalnız CUSTOM formasyon veya paralel devrede elle gerekir. "
             "Tel ekran + eşitleme şeridi konstrüksiyonunda md. 2.3.6.1 Not 3 gereği ihmal edilebilir.", False),
        ),
    ),
)

_ALL_FIELDS = {name: (label, unit, clause, required)
               for _key, _title, rows in FIELD_GROUPS
               for name, label, unit, clause, required in rows}


def defaults_path(root: Path) -> Path:
    return Path(root) / "ditus-standard-defaults.json"


def load_standard_defaults(path: Path) -> StandardDefaults:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return StandardDefaults()
    if not isinstance(payload, dict):
        return StandardDefaults()
    try:
        return StandardDefaults.from_dict(payload)
    except (TypeError, AttributeError, KeyError, ValueError):
        return StandardDefaults()


def save_standard_defaults(path: Path, defaults: StandardDefaults) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(defaults.to_dict(), ensure_ascii=False, indent=2))


def missing_default_fields(defaults: StandardDefaults) -> list[tuple[str, str, str]]:
    """Zorunlu olup tamamlanmamış alanları (nitelik, etiket, madde) döndür."""

    missing: list[tuple[str, str, str]] = []
    for name, (label, _unit, clause, required) in _ALL_FIELDS.items():
        if not required:
            continue
        entry = getattr(defaults, name, None)
        if isinstance(entry, CoefficientEntry) and not entry.is_complete:
            missing.append((name, label, clause))
    return missing


class _EntryRow(QWidget):
    """Değer + provenance + kaynak notu + madde adresi taşıyan tek satır."""

    def __init__(self, name: str, label: str, unit: str, clause: str, required: bool,
                 entry: CoefficientEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self.required = required

        box = QGroupBox(f"{label}{'  *' if required else ''}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 6)
        outer.addWidget(box)

        grid = QGridLayout(box)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(10)

        self.value_spin = QDoubleSpinBox()
        self.value_spin.setDecimals(4)
        self.value_spin.setRange(0.0, 10_000.0)
        self.value_spin.setSingleStep(0.01)
        self.value_spin.setValue(float(entry.value))
        self.value_spin.setSpecialValueText("— girilmedi —")
        grid.addWidget(QLabel("Değer"), 0, 0)
        grid.addWidget(self.value_spin, 0, 1)
        grid.addWidget(QLabel(unit), 0, 2)

        self.provenance_combo = QComboBox()
        for code, text in PROVENANCE_CHOICES:
            self.provenance_combo.addItem(text, code)
        index = self.provenance_combo.findData(entry.provenance)
        self.provenance_combo.setCurrentIndex(max(0, index))
        grid.addWidget(QLabel("Kaynak"), 0, 3)
        grid.addWidget(self.provenance_combo, 0, 4)

        self.reference_edit = QComboBox()
        self.reference_edit.setEditable(True)
        self.reference_edit.setInsertPolicy(QComboBox.NoInsert)
        self.reference_edit.addItems(
            ["", "Üretici veri sayfası rev.", "Standart nüshası", "Ölçüm raporu no.", "Kurum paketi sürümü"]
        )
        self.reference_edit.setCurrentText(entry.reference)
        grid.addWidget(QLabel("Referans"), 0, 5)
        grid.addWidget(self.reference_edit, 0, 6)
        grid.setColumnStretch(6, 1)

        hint = QLabel(f"Nereden bulunur: {clause}")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#41576b; font-size:9pt;")
        grid.addWidget(hint, 1, 0, 1, 7)

        self.status = QLabel("")
        self.status.setStyleSheet("font-size:9pt;")
        grid.addWidget(self.status, 2, 0, 1, 7)

        self.value_spin.valueChanged.connect(self._refresh_status)
        self.provenance_combo.currentIndexChanged.connect(self._refresh_status)
        self._refresh_status()

    def _refresh_status(self) -> None:
        entry = self.entry()
        if entry.is_complete:
            self.status.setText("✔ Tamam")
            self.status.setStyleSheet("color:#24653a; font-weight:700; font-size:9pt;")
        elif self.required:
            self.status.setText("● Profil eksik — proje verisi tam ise hesap motoru çalışmaya devam eder")
            self.status.setStyleSheet("color:#8f2020; font-weight:700; font-size:9pt;")
        else:
            self.status.setText("○ İsteğe bağlı")
            self.status.setStyleSheet("color:#536675; font-size:9pt;")

    def entry(self) -> CoefficientEntry:
        return CoefficientEntry(
            float(self.value_spin.value()),
            str(self.provenance_combo.currentData() or ""),
            self.reference_edit.currentText().strip(),
        )


class StandardDefaultsDialog(QDialog):
    """Tüm haller için standart katsayı ön tanımlarını toplayan ekran."""

    def __init__(self, defaults: StandardDefaults, storage_path: Path,
                 parent: QWidget | None = None, focus_field: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("DiTuS — Standart Katsayıları ve Varsayılanlar")
        self.setModal(True)
        self._storage_path = Path(storage_path)
        self._rows: dict[str, _EntryRow] = {}
        self.result_defaults = defaults

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        banner = QLabel(
            "Bu ekran isteğe bağlı kurum/kullanıcı ön tanım profilidir; hesap motorlarının evrensel kapısı değildir. "
            "Aktif proje ve kablo verisi her zaman önceliklidir. Buradaki değerler kaynak/provenance ile saklanır ve "
            "standardın metni veya tablo düzeni uygulamaya kopyalanmaz."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            "background:#eef4fa; border:1px solid #ccd7e1; border-radius:5px; padding:8px; color:#173d5d;"
        )
        layout.addWidget(banner)

        self.tabs = QTabWidget()
        for _key, title, rows in FIELD_GROUPS:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(6, 6, 6, 6)
            for name, label, unit, clause, required in rows:
                row = _EntryRow(name, label, unit, clause, required, getattr(defaults, name))
                row.value_spin.valueChanged.connect(self._refresh_summary)
                row.provenance_combo.currentIndexChanged.connect(self._refresh_summary)
                self._rows[name] = row
                page_layout.addWidget(row)
            page_layout.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, title)
        layout.addWidget(self.tabs, 1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setFrameShape(QFrame.StyledPanel)
        self.summary.setStyleSheet("padding:7px; border-radius:4px;")
        layout.addWidget(self.summary)

        actions = QHBoxLayout()
        import_button = QPushButton("Paketten içe aktar…")
        export_button = QPushButton("Paket olarak dışa aktar…")
        import_button.clicked.connect(self._import_pack)
        export_button.clicked.connect(self._export_pack)
        actions.addWidget(import_button)
        actions.addWidget(export_button)
        actions.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Kaydet")
        buttons.button(QDialogButtonBox.Cancel).setText("Vazgeç")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)

        self._refresh_summary()
        if focus_field:
            self.focus_on(focus_field)
        fit_window(self, DENSITY_NORMAL, center_on=parent)

    # ------------------------------------------------------------------
    def focus_on(self, field_name: str) -> None:
        """Eksik alandan gelen yönlendirmede doğru sekmeyi ve satırı aç."""

        for index, (_key, _title, rows) in enumerate(FIELD_GROUPS):
            if any(name == field_name for name, *_rest in rows):
                self.tabs.setCurrentIndex(index)
                row = self._rows.get(field_name)
                if row is not None:
                    row.value_spin.setFocus(Qt.OtherFocusReason)
                return

    def collect(self) -> StandardDefaults:
        defaults = StandardDefaults()
        for name, row in self._rows.items():
            setattr(defaults, name, row.entry())
        return defaults

    def _refresh_summary(self) -> None:
        missing = missing_default_fields(self.collect())
        if missing:
            self.summary.setText(
                f"<b>{len(missing)} önerilen profil alanı eksik.</b> Bu durum tek başına fizik motorunu "
                "bloklamaz. Eksik profil alanları: " + ", ".join(label for _name, label, _clause in missing[:6])
                + ("…" if len(missing) > 6 else "")
            )
            self.summary.setStyleSheet(
                "padding:7px; border-radius:4px; background:#fce8e8; color:#8f2020;"
            )
        else:
            self.summary.setText("<b>Ön tanım profili tamamlandı.</b> Aktif proje değerleri yine hesapta önceliklidir.")
            self.summary.setStyleSheet(
                "padding:7px; border-radius:4px; background:#e8f7ed; color:#24653a;"
            )

    def _accept(self) -> None:
        defaults = self.collect()
        try:
            save_standard_defaults(self._storage_path, defaults)
        except OSError as exc:
            QMessageBox.critical(
                self, "Ön tanımlar kaydedilemedi", f"Ön tanım dosyası yazılamadı:\n{self._storage_path}\n\n{exc}"
            )
            return
        self.result_defaults = defaults
        self.accept()

    def _import_pack(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Katsayı paketi seç", "", "DiTuS katsayı paketi (*.json)"
        )
        if not path:
            return
        loaded = load_standard_defaults(Path(path))
        for name, row in self._rows.items():
            entry = getattr(loaded, name, None)
            if isinstance(entry, CoefficientEntry):
                row.value_spin.setValue(entry.value)
                index = row.provenance_combo.findData(entry.provenance)
                row.provenance_combo.setCurrentIndex(max(0, index))
                row.reference_edit.setCurrentText(entry.reference)
        self._refresh_summary()

    def _export_pack(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Katsayı paketini kaydet", "ditus-standard-defaults.json",
            "DiTuS katsayı paketi (*.json)"
        )
        if not path:
            return
        try:
            save_standard_defaults(Path(path), self.collect())
        except OSError as exc:
            QMessageBox.warning(self, "Paket kaydedilemedi", str(exc))
