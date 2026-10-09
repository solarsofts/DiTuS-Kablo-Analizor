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
