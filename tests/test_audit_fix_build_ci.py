"""Denetim düzeltmeleri — derleme, paketleme ve CI (Y-1, Y-2, O-4, D-5, D-7, K-2, K-3, K-6)."""

from __future__ import annotations

import ast
import importlib
import io
import os
import re
import sys
import tokenize
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FOLDERS = ("src", "tools", "tests", "examples")
TEST_ONLY_REQUIREMENTS = {"pytest", "openpyxl"}
_REQUIREMENT_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*([^;]*?)\s*(?:;\s*(.+?))?\s*$")


def _python_files() -> list[Path]:
    files = [ROOT / "app.py"]
    for folder in SOURCE_FOLDERS:
        files.extend(path for path in sorted((ROOT / folder).rglob("*.py")) if "__pycache__" not in path.parts)
    return files


def canonicalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_key(text: str) -> tuple[str, str, str]:
    match = _REQUIREMENT_LINE.match(text.strip())
    assert match, f"Çözümlenemeyen gereksinim: {text!r}"
    name, specifier, marker = match.groups()
    specifier = ",".join(sorted(part.replace(" ", "") for part in specifier.split(",") if part.strip()))
    marker = re.sub(r"\s+", " ", (marker or "").replace("'", '"'))
    return canonicalize_name(name), specifier, marker


def _requirements() -> list[tuple[str, str, str]]:
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return [_requirement_key(line) for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Y-1 — testlerin ve araçların kullandığı her üçüncü taraf paket bildirilmeli
# ---------------------------------------------------------------------------

def _local_module_names() -> set[str]:
    names = {"ucd", "tools", "tests", "examples"}
    for folder in (ROOT, ROOT / "src", *(ROOT / item for item in SOURCE_FOLDERS)):
        for path in folder.iterdir():
            if path.suffix == ".py":
                names.add(path.stem)
            elif path.is_dir() and (path / "__init__.py").exists():
                names.add(path.name)
    return names


def test_every_third_party_import_is_declared_in_requirements() -> None:
    declared = {name for name, _, _ in _requirements()}
    assert "openpyxl" in declared
    local = _local_module_names()
    distributions = packages_distributions()
    undeclared: dict[str, set[str]] = {}
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                top = module.split(".")[0]
                if top == "__future__" or top in sys.stdlib_module_names or top in local:
                    continue
                candidates = {canonicalize_name(top), *(canonicalize_name(item) for item in distributions.get(top, ()))}
                if not candidates & declared:
                    undeclared.setdefault(top, set()).add(path.relative_to(ROOT).as_posix())
    assert not undeclared, f"requirements.txt içinde bildirilmemiş paketler: {undeclared}"


# ---------------------------------------------------------------------------
# Y-2 — kaynak kod Python 3.11'de derlenebilmeli (PEP 701 f-string yok)
# ---------------------------------------------------------------------------

def _quote_of(token_text: str) -> str:
    body = token_text.lstrip("rRbBuUfF")
    for quote in ('"""', "'''", '"', "'"):
        if body.startswith(quote):
            return quote
    raise AssertionError(f"Tırnak çözülemedi: {token_text!r}")


def _conflicts(inner_quote: str, enclosing_quotes: list[str]) -> bool:
    for outer in enclosing_quotes:
        if len(outer) == 3:
            if inner_quote == outer:
                return True
        elif inner_quote[0] == outer:
            return True
    return False


def _pep701_only_fstring_issues(source: str, label: str) -> list[str]:
    """Python 3.12 tokenizer ile yalnız 3.12+ geçerli f-string yapılarını bulur."""

    issues: list[str] = []
    enclosing: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        where = f"{label}:{token.start[0]}"
        if token.type == tokenize.FSTRING_START:
            quote = _quote_of(token.string)
            if enclosing and _conflicts(quote, enclosing):
                issues.append(f"{where}: iç f-string dış tırnağı yeniden kullanıyor")
            enclosing.append(quote)
        elif token.type == tokenize.FSTRING_END:
            enclosing.pop()
        elif enclosing:
            if token.type == tokenize.STRING:
                if _conflicts(_quote_of(token.string), enclosing):
                    issues.append(f"{where}: f-string ifadesinde dış tırnak yeniden kullanılmış")
                if "\\" in token.string:
                    issues.append(f"{where}: f-string ifadesinde ters bölü")
            elif token.type == tokenize.FSTRING_MIDDLE and len(enclosing) > 1 and "\\" in token.string:
                issues.append(f"{where}: iç içe f-string ifadesinde ters bölü")
            elif token.type == tokenize.COMMENT:
                issues.append(f"{where}: f-string ifadesinde yorum")
            elif token.type == tokenize.NL and len(enclosing[-1]) == 1:
                issues.append(f"{where}: tek tırnaklı f-string ifadesi birden çok satıra yayılmış")
    return issues


@pytest.mark.skipif(not hasattr(tokenize, "FSTRING_START"), reason="PEP 701 tokenizer yalnız Python 3.12+")
def test_pep701_detector_flags_312_only_fstrings() -> None:
    old_reporting_line = (
        "x = f\"{getattr(item, 'circuit_id', '?')}={'OFF' if not getattr(item, 'energized', False) "
        "else f'{getattr(item, 'phase_current_a', 0.0):.3f} A'}\"\n"
    )
    assert _pep701_only_fstring_issues(old_reporting_line, "old")
    assert _pep701_only_fstring_issues("x = f'{d['k']}'\n", "quote")
    assert _pep701_only_fstring_issues("x = f\"{'\\n'.join(a)}\"\n", "backslash")
    assert _pep701_only_fstring_issues("x = f'{a +\n b}'\n", "multiline")
    assert not _pep701_only_fstring_issues("x = f\"{d['k']}\" + f'''{d['k']}''' + f'a\\n{b}'\n", "valid")
    fixed_line = (
        "x = f\"{getattr(item, 'circuit_id', '?')}={'OFF' if not getattr(item, 'energized', False) "
        "else format(getattr(item, 'phase_current_a', 0.0), '.3f') + ' A'}\"\n"
    )
    assert not _pep701_only_fstring_issues(fixed_line, "fixed")


def test_sources_compile_on_minimum_supported_python() -> None:
    issues: list[str] = []
    check_tokens = hasattr(tokenize, "FSTRING_START")
    for path in _python_files():
        label = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        try:
            compile(source, label, "exec", dont_inherit=True)
        except SyntaxError as exc:
            issues.append(f"{label}:{exc.lineno}: {exc.msg}")
            continue
        if check_tokens:
            issues.extend(_pep701_only_fstring_issues(source, label))
    assert not issues, "Python 3.11 ile derlenemeyecek sözdizimi: " + "; ".join(issues)


def test_requires_python_floor_matches_ci_matrix() -> None:
    project = _pyproject()["project"]
    assert project["requires-python"] == ">=3.11,<3.13"
    assert "'3.11'" in (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# O-4 — wheel meta verisi: bağımlılıklar, dinamik sürüm, paket verisi
# ---------------------------------------------------------------------------

def test_pyproject_dependencies_mirror_requirements() -> None:
    project = _pyproject()["project"]
    requirements = _requirements()
    runtime = {item for item in requirements if item[0] not in TEST_ONLY_REQUIREMENTS}
    test_only = {item for item in requirements if item[0] in TEST_ONLY_REQUIREMENTS}
    declared_runtime = {_requirement_key(item) for item in project["dependencies"]}
    declared_test = {_requirement_key(item) for item in project["optional-dependencies"]["test"]}
    assert {item[0] for item in test_only} == TEST_ONLY_REQUIREMENTS
    assert declared_runtime == runtime
    assert declared_test == test_only
    numpy_markers = {key[2] for key in declared_runtime if key[0] == "numpy"}
    assert numpy_markers == {'python_version >= "3.12"', 'python_version < "3.12"'}


def test_package_version_is_single_sourced_from_ucd() -> None:
    import ucd

    data = _pyproject()
    assert "version" not in data["project"]
    assert "version" in data["project"]["dynamic"]
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "ucd.__version__"}
    assert (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip() == ucd.__version__


def test_package_data_ships_every_resource_file() -> None:
    patterns = _pyproject()["tool"]["setuptools"]["package-data"]["ucd.resources"]
    resources = ROOT / "src" / "ucd" / "resources"
    covered = {path for pattern in patterns for path in resources.glob(pattern) if path.is_file()}
    data_files = {
        path
        for path in resources.rglob("*")
        if path.is_file() and path.suffix not in {".py", ".pyc"} and "__pycache__" not in path.parts
    }
    assert data_files, "ucd.resources altında veri dosyası bulunamadı"
    missing = sorted(path.relative_to(resources).as_posix() for path in data_files - covered)
    assert not missing, f"Wheel'e girmeyecek kaynak dosyaları: {missing}"
    assert {"generic_cable_profiles.json", "manufacturer_catalog_links.json", "catalogs/README.md"} <= {
        path.relative_to(resources).as_posix() for path in covered
    }


def test_generic_profiles_load_through_package_resources() -> None:
    from ucd.calculations.cable_template_generator import load_generic_profile_data

    data = load_generic_profile_data()
    assert data


# ---------------------------------------------------------------------------
# D-5 — ucd.calculations içinde aynı ad iki farklı değerle bağlanmamalı
# ---------------------------------------------------------------------------

def test_calculations_package_has_no_shadowed_exports() -> None:
    from ucd import calculations

    init_path = ROOT / "src" / "ucd" / "calculations" / "__init__.py"
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    bindings: dict[str, list[tuple[int, str | None, str | None]]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bindings.setdefault(alias.asname or alias.name, []).append((node.lineno, node.module, alias.name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.setdefault(node.name, []).append((node.lineno, None, None))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id != "__all__":
                    bindings.setdefault(target.id, []).append((node.lineno, None, None))

    shadowed: list[str] = []
    for name, entries in bindings.items():
        if len(entries) < 2:
            continue
        if any(module is None for _, module, _ in entries):
            shadowed.append(f"{name} (satır {[line for line, _, _ in entries]})")
            continue
        values = [getattr(importlib.import_module(module), original) for _, module, original in entries]
        if any(value is not values[0] for value in values[1:]):
            shadowed.append(f"{name} (satır {[line for line, _, _ in entries]})")
    assert not shadowed, f"Gölgelenen dışa aktarımlar: {shadowed}"

    exported = list(calculations.__all__)
    assert len(exported) == len(set(exported))
    assert all(hasattr(calculations, name) for name in exported)


def test_status_conditional_aliases_keep_both_meanings() -> None:
    from ucd import calculations
    from ucd.calculations import procurement, project_workflow

    assert calculations.STATUS_CONDITIONAL == project_workflow.STATUS_CONDITIONAL == "CONDITIONAL"
    assert calculations.PROCUREMENT_STATUS_CONDITIONAL == procurement.STATUS_CONDITIONAL == "CONDITIONAL_PROJECT_DATA"
    assert "PROCUREMENT_STATUS_CONDITIONAL" in calculations.__all__
    assert calculations.STATUS_CONFIRMED == procurement.STATUS_CONFIRMED
    assert calculations.STATUS_ASSUMPTION == procurement.STATUS_ASSUMPTION


# ---------------------------------------------------------------------------
# D-7 / K-6 — CI eylem sürümleri, Qt çalışma zamanı ve derleme adımı
# ---------------------------------------------------------------------------

def _ci_text() -> str:
    return (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


def test_ci_uses_node24_action_majors() -> None:
    text = _ci_text()
    assert "actions/checkout@v5" in text
    assert "actions/setup-python@v6" in text
    assert not re.search(r"actions/checkout@v[1-4]\b", text)
    assert not re.search(r"actions/setup-python@v[1-5]\b", text)


def test_ci_requires_qt_runtime_and_compiles_before_pytest() -> None:
    text = _ci_text()
    assert "DITUS_REQUIRE_QT: '1'" in text
    assert "if: runner.os == 'Linux'" in text
    for library in ("libegl1", "libgl1", "libxkbcommon0", "libfontconfig1", "libdbus-1-3"):
        assert library in text
    apt_step = text.index("apt-get install")
    install_step = text.index("python -m pip install -r requirements.txt")
    ruff_step = text.index("python -m ruff check src tools tests examples app.py")
    compile_step = text.index("python -m compileall -q src tools tests examples")
    pytest_step = text.index("run: python -m pytest")
    assert apt_step < install_step < ruff_step < compile_step < pytest_step


def test_ruff_is_a_python311_release_gate() -> None:
    data = _pyproject()
    assert data["tool"]["ruff"]["target-version"] == "py311"
    assert set(data["tool"]["ruff"]["lint"]["select"]) == {"E9", "F63", "F7", "F82"}
    assert 'python -m pip install "ruff>=0.12,<1"' in _ci_text()


def test_calculation_docstrings_are_actual_module_docstrings() -> None:
    misplaced: list[str] = []
    calculations = ROOT / "src" / "ucd" / "calculations"
    for path in sorted(calculations.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        string_statements = [
            node for node in tree.body
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ]
        if string_statements and ast.get_docstring(tree) is None:
            misplaced.append(f"{path.name}:{string_statements[0].lineno}")
    assert not misplaced, f"Modül başında olmayan docstring'ler: {misplaced}"


def test_pyside6_importorskip_calls_declare_exc_type() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "tests").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "importorskip"):
                continue
            first = node.args[0] if node.args else None
            is_pyside6 = isinstance(first, ast.Constant) and str(first.value).startswith("PySide6")
            if is_pyside6 and not any(keyword.arg == "exc_type" for keyword in node.keywords):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"exc_type belirtilmemiş importorskip çağrıları: {offenders}"


# ---------------------------------------------------------------------------
# K-2 / K-3 — depo hijyeni ve sürüm tek kaynağı
# ---------------------------------------------------------------------------

def test_gitignore_covers_generated_python_artefacts_and_local_user_data() -> None:
    entries = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    required = {
        "__pycache__/", "*.py[cod]", "*.egg-info/", "build/", "dist/", ".venv/", "venv/",
        ".pytest_cache/", ".ruff_cache/", ".coverage", "htmlcov/", "user_data/",
    }
    assert required <= entries, sorted(required - entries)


@pytest.mark.parametrize("name", ["README.md", "README_TR.md"])
def test_readme_heading_does_not_pin_a_stale_version(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    heading = next(line for line in text.splitlines() if line.startswith("# "))
    assert not re.search(r"v?\d+\.\d+\.\d+", heading), heading
    assert "VERSION.txt" in text


# ---------------------------------------------------------------------------
# O-4 — maskot dosyası yoksa veya bozuksa arayüz çökmemeli
# ---------------------------------------------------------------------------

def _mascot_variants(tmp_path: Path) -> list[Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    corrupt = tmp_path / "corrupt_mascot.png"
    corrupt.write_bytes(b"not a png")
    folder = tmp_path / "mascot_folder.png"
    folder.mkdir()
    return [tmp_path / "missing" / "ditus_mascot.png", corrupt, folder]


def test_precheck_dialog_and_identity_header_tolerate_missing_mascot(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ucd.calculations.engine_precheck import evaluate_engine_precheck
    from ucd.models.project import ProjectData
    from ucd.ui.engine_precheck_dialog import EnginePrecheckDialog
    from ucd.ui.workflow_widgets import ProjectIdentityHeader

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    precheck = evaluate_engine_precheck(ProjectData(), "bonding")
    for mascot_path in [None, *_mascot_variants(tmp_path)]:
        dialog = EnginePrecheckDialog(precheck, mascot_path)
        logos = [label for label in dialog.findChildren(QtWidgets.QLabel) if label.width() == 68]
        assert logos and all(label.pixmap().isNull() for label in logos)
        dialog.deleteLater()
    for mascot_path in _mascot_variants(tmp_path / "header"):
        header = ProjectIdentityHeader(mascot_path)
        assert header.logo.pixmap().isNull()
        header.deleteLater()
    app.processEvents()


def test_main_window_dialogs_tolerate_missing_mascot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ucd.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    opened: list[str] = []
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda self: opened.append(self.windowTitle()) or 0)
    window = MainWindow(tmp_path)
    opened.clear()
    assert not (tmp_path / "assets").exists()
    assert window.project_identity.logo.pixmap().isNull()
    window.act_yesilcam.blockSignals(True)
    window.act_yesilcam.setChecked(True)
    window._show_iteration_easter_egg("Test")
    assert opened == ["DiTuS — Vaziyet Al"]
    window.close()
    app.processEvents()
