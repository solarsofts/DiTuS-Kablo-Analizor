from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ucd.calculations.cable_library import (
    CableLibraryInputError,
    catalog_package_from_dict,
    catalog_package_to_dict,
    merge_builtin_catalogs,
    merge_catalog_library,
)
from ucd.fileio import atomic_write_text, quarantine_file
from ucd.models.project import CableLibraryData

# ValueError covers JSONDecodeError, UnicodeDecodeError and CableLibraryInputError.
_DATABASE_CONTENT_ERRORS = (ValueError, TypeError, AttributeError, KeyError, CableLibraryInputError)


@dataclass(frozen=True)
class ApplicationDatabaseLoadStatus:
    """Loaded library plus what the UI must tell the user before saving over it."""

    library: CableLibraryData
    error_message: str = ""
    quarantined_path: Path | None = None
    save_allowed: bool = True


def _builtin_application_library() -> CableLibraryData:
    target = CableLibraryData(
        package_name="DiTuS uygulama kablo veri tabanı",
        package_revision="0.16.9.4.37",
        package_source="APPLICATION_DATABASE",
    )
    merge_builtin_catalogs(target)
    return target


def load_application_cable_database_with_status(path: str | Path) -> ApplicationDatabaseLoadStatus:
    """Load the reusable application database with manufacturer-free templates.

    The application database is independent from any active project. DiTuS
    contributes only seven generated generic templates; producer records exist
    only when the user imports or creates them. Project calculations use a
    copied snapshot, never the mutable database row itself.

    A corrupt user database never prevents the application from opening: the
    file is moved aside as ``<name>.corrupt-<time>`` before the generic
    templates are returned, so a later save cannot overwrite the user's
    catalog. A file that exists but cannot be read is left in place and saving
    is disabled for the session.
    """
    file_path = Path(path)
    if not file_path.exists():
        return ApplicationDatabaseLoadStatus(_builtin_application_library())
    try:
        content = file_path.read_bytes()
    except OSError as exc:
        return ApplicationDatabaseLoadStatus(
            _builtin_application_library(),
            error_message=(
                f"Kablo veri tabanı dosyası okunamadı ({exc}). Dosyaya dokunulmadı; yerleşik jenerik "
                f"şablonlar yüklendi ve bu oturumda veri tabanı değişiklikleri kaydedilmeyecek: {file_path}"
            ),
            save_allowed=False,
        )
    try:
        incoming = catalog_package_from_dict(json.loads(content.decode("utf-8-sig")))
        target = _builtin_application_library()
        merge_catalog_library(target, incoming, replace=True)
        return ApplicationDatabaseLoadStatus(target)
    except _DATABASE_CONTENT_ERRORS as exc:
        reason = str(exc) or type(exc).__name__
    try:
        quarantined = quarantine_file(file_path)
    except OSError as exc:
        return ApplicationDatabaseLoadStatus(
            _builtin_application_library(),
            error_message=(
                f"Kablo veri tabanı bozuk ({reason}) ve karantinaya alınamadı ({exc}). Dosyaya dokunulmadı; "
                f"bu oturumda veri tabanı değişiklikleri kaydedilmeyecek: {file_path}"
            ),
            save_allowed=False,
        )
    return ApplicationDatabaseLoadStatus(
        _builtin_application_library(),
        error_message=(
            f"Kablo veri tabanı bozuk olduğu için yüklenemedi ({reason}). Dosya "
            f"'{quarantined.name}' adıyla karantinaya alındı; yerleşik jenerik şablonlar yüklendi. "
            "Kayıtlarınızı bu dosyadan kurtarıp katalog paketi olarak yeniden içe alabilirsiniz."
        ),
        quarantined_path=quarantined,
        save_allowed=True,
    )


def load_application_cable_database(path: str | Path) -> CableLibraryData:
    """Return only the library of :func:`load_application_cable_database_with_status`."""
    return load_application_cable_database_with_status(path).library


def save_application_cable_database(library: CableLibraryData, path: str | Path) -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = catalog_package_to_dict(library)
    atomic_write_text(file_path, json.dumps(payload, ensure_ascii=False, indent=2))
    return file_path
