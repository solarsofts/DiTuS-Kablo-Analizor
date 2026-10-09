from __future__ import annotations

import io
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from tools.publish_integrity_audit import scan_package
from tools.run_release_acceptance import verify_engine_lock


ROOT = Path(__file__).resolve().parents[1]


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _valid_tckn() -> str:
    first_nine = [1, 0, 0, 0, 0, 0, 0, 0, 1]
    tenth = ((sum(first_nine[0:9:2]) * 7) - sum(first_nine[1:8:2])) % 10
    eleventh = (sum(first_nine) + tenth) % 10
    return "".join(str(item) for item in first_nine + [tenth, eleventh])


def _pdf_with_text(text: str) -> bytes:
    target = io.BytesIO()
    pdf = canvas.Canvas(target)
    pdf.drawString(72, 760, text)
    pdf.save()
    return target.getvalue()


def test_current_package_publish_integrity_passes() -> None:
    excluded = {
        "MANIFEST.txt",
        "PUBLISH_INTEGRITY_AUDIT_v0.16.9.4.39.json",
        "PACKAGED_TEST_RESULTS_v0.16.9.4.39.txt",
        "PUBLISH_CLEANUP_AUDIT_v0.16.9.4.39.md",
    }
    result = scan_package(ROOT, excluded_paths=excluded)
    assert result.status == "PASS", [
        (item.rule_id, item.path, item.surface, item.location, item.redacted_preview)
        for item in result.blocking_findings
    ]
    assert result.stats.pdf_files_seen >= 2
    assert result.stats.pdf_files_scanned == result.stats.pdf_files_seen
    assert result.stats.pdf_pages_scanned == result.stats.pdf_pages_seen
    assert result.stats.pdf_text_characters_scanned > 0


def test_general_patterns_block_personal_and_local_environment_data(tmp_path: Path) -> None:
    at = chr(64)
    slash = chr(92)
    samples = "\n".join(
        [
            "person" + at + "example.test",
            "C:" + slash + "Users" + slash + "operator" + slash + "Desktop" + slash + "project.json",
            "/" + "mnt/data/private/project.json",
            "+90 " + "532 111 22 33",
            _valid_tckn(),
            "192" + ".168.1.44",
        ]
    )
    _write(tmp_path, "payload.txt", samples)
    result = scan_package(tmp_path)
    rules = {item.rule_id for item in result.blocking_findings}
    assert result.status == "FAIL"
    assert {
        "email_address",
        "windows_user_path",
        "windows_absolute_path",
        "posix_local_path",
        "turkiye_phone_number",
        "turkiye_identity_number",
        "private_or_local_ipv4",
    }.issubset(rules)


def test_invalid_eleven_digit_number_is_not_misclassified_as_tckn(tmp_path: Path) -> None:
    _write(tmp_path, "technical.txt", "10000000000")
    result = scan_package(tmp_path)
    assert all(item.rule_id != "turkiye_identity_number" for item in result.blocking_findings)

    _write(tmp_path, "result.json", '{"total_core_loss_w": 8492.94360184128}')
    decimal_result = scan_package(tmp_path)
    assert all(item.rule_id != "turkiye_identity_number" for item in decimal_result.blocking_findings)


def test_json_identity_fields_are_fail_closed(tmp_path: Path) -> None:
    payload = {"metadata": {"prepared_by": "Synthetic Person"}}
    _write(tmp_path, "report.json", json.dumps(payload))
    result = scan_package(tmp_path)
    assert result.status == "FAIL"
    assert any(item.rule_id == "identity_metadata" for item in result.blocking_findings)


def test_empty_json_identity_fields_are_allowed(tmp_path: Path) -> None:
    payload = {"metadata": {"prepared_by": "", "email": ""}}
    _write(tmp_path, "report.json", json.dumps(payload))
    result = scan_package(tmp_path)
    assert result.status == "PASS"
    assert result.stats.json_identity_fields_scanned == 2


def test_pdf_text_is_actually_extracted_and_scanned(tmp_path: Path) -> None:
    address = "pdf.person" + chr(64) + "example.test"
    (tmp_path / "leak.pdf").write_bytes(_pdf_with_text(address))
    result = scan_package(tmp_path)
    assert result.status == "FAIL"
    assert result.stats.pdf_files_scanned == 1
    assert result.stats.pdf_pages_scanned == 1
    assert result.stats.pdf_text_characters_scanned > 0
    assert any(item.rule_id == "email_address" and item.surface.startswith("pdf-page") for item in result.blocking_findings)


def test_blank_pdf_is_not_reported_as_zero_match_pass(tmp_path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    target = tmp_path / "blank.pdf"
    with target.open("wb") as stream:
        writer.write(stream)
    result = scan_package(tmp_path)
    assert result.status == "FAIL"
    assert any(item.rule_id == "pdf_text_extraction_empty" for item in result.blocking_findings)
    assert result.stats.unscannable_objects == 1


def test_pdf_identity_metadata_is_scanned(tmp_path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Author": "Synthetic Person"})
    target = tmp_path / "metadata.pdf"
    with target.open("wb") as stream:
        writer.write(stream)
    result = scan_package(tmp_path)
    rules = {item.rule_id for item in result.blocking_findings}
    assert "identity_metadata" in rules
    assert "pdf_text_extraction_empty" in rules
    assert result.stats.pdf_metadata_fields_scanned > 0


def test_ooxml_core_identity_metadata_is_scanned(tmp_path: Path) -> None:
    target = tmp_path / "sample.docx"
    core = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<cp:coreProperties xmlns:cp='http://schemas.openxmlformats.org/package/2006/metadata/core-properties' "
        "xmlns:dc='http://purl.org/dc/elements/1.1/'>"
        "<dc:creator>Synthetic Person</dc:creator>"
        "</cp:coreProperties>"
    )
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("docProps/core.xml", core)
        archive.writestr("word/document.xml", "<document><body>clean</body></document>")
    result = scan_package(tmp_path)
    assert result.status == "FAIL"
    assert any(item.rule_id == "identity_metadata" for item in result.blocking_findings)
    assert result.stats.ooxml_files_scanned == 1


def test_unsupported_legacy_office_document_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "legacy.doc").write_bytes(b"binary")
    result = scan_package(tmp_path)
    assert result.status == "FAIL"
    assert any(item.rule_id == "unsupported_document_format" for item in result.blocking_findings)


def test_approved_signature_is_allowed_only_in_reviewed_path(tmp_path: Path) -> None:
    signature = "S." + "Esim"
    _write(tmp_path, "src/ucd/ui/main_window.py", signature)
    reviewed = scan_package(tmp_path)
    assert reviewed.status == "PASS"
    assert len(reviewed.allowed_findings) == 1

    _write(tmp_path, "notes.txt", signature)
    unreviewed = scan_package(tmp_path)
    assert unreviewed.status == "FAIL"
    assert any(item.rule_id == "regression_approved_designer_signature" and not item.allowed for item in unreviewed.findings)


def test_engine_directories_match_locked_v0169417_baseline() -> None:
    result = verify_engine_lock(ROOT, ROOT / "ENGINE_BASELINE_v0.16.9.4.39.sha256")
    assert result["status"] == "PASS", result
    assert result["verified_file_count"] == result["expected_file_count"] == 54
