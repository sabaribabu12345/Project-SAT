from __future__ import annotations

from pypdf import PdfWriter

from apps.api.pdf_scanner import (
    _looks_internal_field_label,
    _normalize_label,
    _select_acroform_label,
    _text_label_candidates,
    scan_pdf_datapoints,
)


def test_text_label_candidates_extracts_survey_like_lines() -> None:
    text = """
    General introduction sentence that should not become a datapoint.
    Total undergraduate enrollment:
    Number of first-time freshman applicants ______________
    This is a complete sentence.
    """

    labels = _text_label_candidates(text)

    assert "Total undergraduate enrollment:" in labels
    assert "Number of first-time freshman applicants" in labels
    assert "This is a complete sentence." not in labels


def test_normalize_label_removes_formatting_noise() -> None:
    assert _normalize_label(" Total undergraduate enrollment:  ") == "total undergraduate enrollment"


def test_scan_blank_pdf_returns_pdf_metadata_without_candidates(tmp_path) -> None:
    pdf_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    result = scan_pdf_datapoints(pdf_path, survey_id="test_pdf")

    assert result.survey_id == "test_pdf"
    assert result.file_name == "blank.pdf"
    assert result.fillable is False
    assert result.page_count == 1
    assert result.candidates == []


def test_internal_acroform_label_uses_nearby_visible_text() -> None:
    label, source, confidence = _select_acroform_label(
        field_name="TUIT_VARY_PROG_P",
        tooltip="",
        nearby_text="Tuition varies by program",
    )

    assert label == "Tuition varies by program"
    assert source == "nearby_text"
    assert confidence == 0.85


def test_acroform_tooltip_wins_when_present() -> None:
    label, source, confidence = _select_acroform_label(
        field_name="URL_DESTINATION_URL",
        tooltip="Main Institution Website",
        nearby_text="Website",
    )

    assert label == "Main Institution Website"
    assert source == "tooltip"
    assert confidence == 0.95


def test_detects_labels_derived_from_internal_field_names() -> None:
    assert _looks_internal_field_label("TUIT_VARY_PROG_P", "Tuit Vary Prog P") is True
    assert _looks_internal_field_label("URL_DESTINATION_URL", "Main Institution Website") is False
