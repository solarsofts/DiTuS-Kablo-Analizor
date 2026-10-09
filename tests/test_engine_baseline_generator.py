"""Release engine-lock generation contract."""

from __future__ import annotations

from pathlib import Path

from tools.generate_engine_baseline import render_engine_baseline, write_engine_baseline
from tools.run_release_acceptance import verify_engine_lock


ROOT = Path(__file__).resolve().parents[1]


def test_engine_baseline_generator_is_deterministic_and_verifiable(tmp_path: Path) -> None:
    first = render_engine_baseline(ROOT)
    second = render_engine_baseline(ROOT)
    assert first == second
    assert "src/ucd/calculations/export_safety.py" in first

    output = tmp_path / "engine.sha256"
    count = write_engine_baseline(ROOT, output)
    assert count == 54
    assert output.read_text(encoding="utf-8") == first
    result = verify_engine_lock(ROOT, output)
    assert result["status"] == "PASS"
    assert result["verified_file_count"] == 54


def test_engine_baseline_is_independent_of_checkout_line_endings(tmp_path: Path) -> None:
    engine_file = tmp_path / "src/ucd/calculations/example.py"
    engine_file.parent.mkdir(parents=True)
    engine_file.write_bytes(b"value = 1\nother = 2\n")
    lf_baseline = render_engine_baseline(tmp_path)

    engine_file.write_bytes(b"value = 1\r\nother = 2\r\n")
    crlf_baseline = render_engine_baseline(tmp_path)

    assert crlf_baseline == lf_baseline
