from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.api.db.models import Base, SurveyPdfDataPointCandidate, SurveyPdfScan
from apps.api.service import PdfDatapointService
from apps.api.settings import Settings


def _create_form_pdf(path: Path, field_names: list[str]) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=320, height=240)
    annotations = ArrayObject()
    fields = ArrayObject()
    for index, field_name in enumerate(field_names):
        bottom = 180 - (index * 32)
        annotation = DictionaryObject(
            {
                NameObject("/FT"): NameObject("/Tx"),
                NameObject("/T"): TextStringObject(field_name),
                NameObject("/V"): TextStringObject(""),
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/Rect"): ArrayObject(
                    [FloatObject(20), FloatObject(bottom), FloatObject(220), FloatObject(bottom + 20)]
                ),
                NameObject("/F"): FloatObject(4),
                NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
            }
        )
        ref = writer._add_object(annotation)
        annotations.append(ref)
        fields.append(ref)
    page[NameObject("/Annots")] = annotations
    writer._root_object.update(
        {
            NameObject("/AcroForm"): DictionaryObject(
                {
                    NameObject("/Fields"): fields,
                    NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
                }
            )
        }
    )
    with path.open("wb") as handle:
        writer.write(handle)


def test_export_resolved_values_to_pdf_fills_matching_acroform_fields(tmp_path) -> None:
    source_pdf = tmp_path / "source.pdf"
    output_pdf = tmp_path / "filled.pdf"
    _create_form_pdf(source_pdf, ["FIELD_A", "FIELD_B"])

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_export",
                survey_id="survey",
                file_name="source.pdf",
                file_path=str(source_pdf),
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=3,
                raw_result_json="{}",
            )
        )
        session.add_all(
            [
                SurveyPdfDataPointCandidate(
                    candidate_id="cand_a",
                    scan_id="scan_export",
                    survey_id="survey",
                    candidate_key="acroform.field_a",
                    source="acroform",
                    field_name="FIELD_A",
                    label_text="Field A",
                    normalized_label="field a",
                    input_kind="text",
                    confidence=0.95,
                    label_source="openai_enriched",
                    field_rect_json="[]",
                    nearby_text="Section: Test",
                    genie_sql_template="select '123'",
                    genie_value="123",
                    genie_confidence=90,
                    status="GENIE_RESOLVED",
                ),
                SurveyPdfDataPointCandidate(
                    candidate_id="cand_missing",
                    scan_id="scan_export",
                    survey_id="survey",
                    candidate_key="acroform.missing",
                    source="acroform",
                    field_name="MISSING_FIELD",
                    label_text="Missing field",
                    normalized_label="missing field",
                    input_kind="text",
                    confidence=0.95,
                    label_source="openai_enriched",
                    field_rect_json="[]",
                    nearby_text="Section: Test",
                    genie_sql_template="select '999'",
                    genie_value="999",
                    genie_confidence=90,
                    status="GENIE_RESOLVED",
                ),
                SurveyPdfDataPointCandidate(
                    candidate_id="cand_low",
                    scan_id="scan_export",
                    survey_id="survey",
                    candidate_key="acroform.field_b",
                    source="acroform",
                    field_name="FIELD_B",
                    label_text="Field B",
                    normalized_label="field b",
                    input_kind="text",
                    confidence=0.95,
                    label_source="openai_enriched",
                    field_rect_json="[]",
                    nearby_text="Section: Test",
                    genie_sql_template="select 'skip'",
                    genie_value="skip",
                    genie_confidence=30,
                    status="GENIE_LOW_CONFIDENCE",
                ),
            ]
        )
        session.commit()

        service = PdfDatapointService(session, settings=Settings(pdf_export_dir=str(tmp_path / "exports")))
        result = service.export_resolved_values_to_pdf(
            scan_id="scan_export",
            output_file_path=str(output_pdf),
        )

        assert result.filled_count == 1
        assert result.skipped_count == 1
        assert result.missing_pdf_fields == ["MISSING_FIELD"]
        fields = PdfReader(str(output_pdf)).get_fields()
        assert fields is not None
        assert fields["FIELD_A"].get("/V") == "123"
        assert fields["FIELD_B"].get("/V") == ""
    finally:
        session.close()


def test_export_resolved_values_to_pdf_uses_container_upload_fallback(tmp_path) -> None:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    source_pdf = upload_dir / "source.pdf"
    output_pdf = tmp_path / "filled.pdf"
    _create_form_pdf(source_pdf, ["FIELD_A"])

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_export_fallback",
                survey_id="survey",
                file_name="source.pdf",
                file_path="/missing/host/project/uploads/source.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=1,
                raw_result_json="{}",
            )
        )
        session.add(
            SurveyPdfDataPointCandidate(
                candidate_id="cand_a",
                scan_id="scan_export_fallback",
                survey_id="survey",
                candidate_key="acroform.field_a",
                source="acroform",
                field_name="FIELD_A",
                label_text="Field A",
                normalized_label="field a",
                input_kind="text",
                confidence=0.95,
                label_source="openai_enriched",
                field_rect_json="[]",
                nearby_text="Section: Test",
                genie_sql_template="select '123'",
                genie_value="123",
                genie_confidence=90,
                status="GENIE_RESOLVED",
            )
        )
        session.commit()

        service = PdfDatapointService(
            session,
            settings=Settings(
                pdf_export_dir=str(tmp_path / "exports"),
                pdf_upload_dir=str(upload_dir),
            ),
        )
        result = service.export_resolved_values_to_pdf(
            scan_id="scan_export_fallback",
            output_file_path=str(output_pdf),
        )

        assert result.source_file_path == str(source_pdf)
        fields = PdfReader(str(output_pdf)).get_fields()
        assert fields is not None
        assert fields["FIELD_A"].get("/V") == "123"
    finally:
        session.close()
