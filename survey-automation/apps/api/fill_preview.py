from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.db.models import MasterDataPoint, SurveyPdfDataPointCandidate, SurveyPdfScan
from apps.api.schemas import FillPreviewResponse, FillPreviewRow


def _extract_section_label(nearby_text: str) -> str:
    import re

    match = re.search(r"Section:\s*(.+?)(?:\s*\||\n|$)", nearby_text or "")
    return match.group(1).strip() if match else ""


def load_web_fill_payload(data_path: Any) -> dict[str, str]:
    if not data_path.exists():
        return {}
    try:
        raw = json.loads(data_path.read_text())
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if value is not None and str(value) != ""}


def build_fill_preview(
    session: Session,
    *,
    scan_id: str | None,
    web_payload_path: Any,
) -> FillPreviewResponse:
    scans = list(session.execute(select(SurveyPdfScan).order_by(SurveyPdfScan.created_at.desc())).scalars())
    active_scan_id = scan_id
    active_survey_id: str | None = None
    if active_scan_id:
        scan = session.get(SurveyPdfScan, active_scan_id)
        if scan:
            active_survey_id = scan.survey_id
    elif scans:
        active_scan_id = scans[0].scan_id
        active_survey_id = scans[0].survey_id

    pdf_by_field: dict[str, SurveyPdfDataPointCandidate] = {}
    if active_scan_id:
        pdf_rows = list(
            session.execute(
                select(SurveyPdfDataPointCandidate)
                .where(SurveyPdfDataPointCandidate.scan_id == active_scan_id)
                .order_by(SurveyPdfDataPointCandidate.field_name)
            ).scalars()
        )
        pdf_by_field = {row.field_name: row for row in pdf_rows}

    masters = list(session.execute(select(MasterDataPoint).order_by(MasterDataPoint.canonical_name)).scalars())
    masters_by_name = {row.canonical_name.upper(): row for row in masters if row.canonical_name}

    web_payload = load_web_fill_payload(web_payload_path)

    field_keys: set[str] = set()
    field_keys.update(masters_by_name.keys())
    field_keys.update(pdf_by_field.keys())
    field_keys.update(web_payload.keys())

    preview_rows: list[FillPreviewRow] = []
    for field_key in sorted(field_keys, key=str.upper):
        master = masters_by_name.get(field_key.upper())
        pdf_row = pdf_by_field.get(field_key)
        web_value = web_payload.get(field_key)

        pdf_value = (pdf_row.genie_value or "").strip() if pdf_row and pdf_row.genie_value else None
        pdf_status = pdf_row.status if pdf_row else None
        pdf_confidence = pdf_row.genie_confidence if pdf_row else 0
        pdf_ready = bool(pdf_row and pdf_status == "GENIE_RESOLVED" and pdf_value)
        web_ready = bool(web_value)

        label = ""
        intent = ""
        section = ""
        if pdf_row:
            label = pdf_row.label_text or pdf_row.field_name
            intent = pdf_row.datapoint_intent or ""
            section = _extract_section_label(pdf_row.nearby_text)
        elif master:
            label = master.description or master.semantic_key or master.canonical_name
            intent = master.semantic_key or ""
            section = (master.semantic_key or "").split(".")[0]

        preview_rows.append(
            FillPreviewRow(
                field_key=field_key,
                label=label,
                intent=intent,
                section=section,
                pdf_value=pdf_value,
                pdf_status=pdf_status,
                pdf_confidence=pdf_confidence,
                web_value=web_value,
                master_data_point_id=master.data_point_id if master else None,
                databricks_view=master.databricks_view if master and master.databricks_view else None,
                pdf_ready=pdf_ready,
                web_ready=web_ready,
            )
        )

    pdf_ready_count = sum(1 for row in preview_rows if row.pdf_ready)
    web_ready_count = sum(1 for row in preview_rows if row.web_ready)
    both_ready_count = sum(1 for row in preview_rows if row.pdf_ready and row.web_ready)

    return FillPreviewResponse(
        scan_id=active_scan_id,
        survey_id=active_survey_id,
        web_payload_path=str(web_payload_path),
        total_fields=len(preview_rows),
        pdf_ready_count=pdf_ready_count,
        web_ready_count=web_ready_count,
        both_ready_count=both_ready_count,
        rows=preview_rows,
    )
