from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_output_dialogs_use_current_package_version() -> None:
    report_source = (ROOT / "src/ucd/ui/report_builder_dialog.py").read_text(encoding="utf-8")
    procurement_source = (ROOT / "src/ucd/ui/procurement_dialog.py").read_text(encoding="utf-8")
    assert "from ucd import __version__" in report_source
    assert "from ucd import __version__" in procurement_source
    assert "v{__version__}" in report_source
    assert "v{__version__}" in procurement_source
    assert "v0.16.1" not in report_source
    assert "v0.16.1" not in procurement_source


def test_package_hotfix_and_project_loader_versions() -> None:
    package_source = (ROOT / "src/ucd/__init__.py").read_text(encoding="utf-8")
    main_source = (ROOT / "src/ucd/ui/main_window.py").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    application_database = (ROOT / "src/ucd/calculations/application_database.py").read_text(encoding="utf-8")
    version = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()
    assert version == "0.16.9.4.39"
    assert f'__version__ = "{version}"' in package_source
    assert "from ucd import __version__" in main_source
    assert "APP_VERSION = __version__" in main_source
    project_source = (ROOT / "src/ucd/models/project.py").read_text(encoding="utf-8")
    assert "self.project.schema_version = PROJECT_SCHEMA_VERSION" in main_source
    assert 'PROJECT_SCHEMA_VERSION = "0.16.4"' in project_source
    assert requirements.startswith(f"# DiTuS Kablo Analizör v{version}")
    assert 'package_revision="0.16.9.4.37"' in application_database
