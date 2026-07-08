from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldDefinition:
    field_id: str
    label_hint: str
    input_kind: str
    required: bool = False


@dataclass(frozen=True)
class ValidateTaskSpec:
    section_id: str
    url: str
    user_prompt: str
    expected_values: dict[str, str]
    extraction_schema: dict[str, dict[str, str]]


@dataclass(frozen=True)
class ScanFieldsTaskSpec:
    section_id: str
    url: str
    user_prompt: str
    extraction_schema: dict[str, dict[str, str]]


@dataclass(frozen=True)
class FillTaskSpec:
    section_id: str
    url: str
    user_prompt: str
    expected_values: dict[str, str]
    extraction_schema: dict[str, dict[str, str]]


DEFAULT_SECTION_FIELDS: dict[str, list[FieldDefinition]] = {
    "institution": [
        FieldDefinition(field_id="institution.name", label_hint="Institution Name", input_kind="text", required=True),
        FieldDefinition(field_id="institution.city", label_hint="Institution City", input_kind="text", required=True),
        FieldDefinition(field_id="institution.state", label_hint="Institution State", input_kind="text"),
        FieldDefinition(field_id="institution.zip_code", label_hint="Zip Code", input_kind="text"),
        FieldDefinition(field_id="institution.website", label_hint="Institution Website", input_kind="text"),
        FieldDefinition(field_id="institution.phone", label_hint="Institution Phone", input_kind="text"),
        FieldDefinition(field_id="institution.control", label_hint="Control Type", input_kind="select"),
        FieldDefinition(field_id="institution.campus_setting", label_hint="Campus Setting", input_kind="select"),
        FieldDefinition(field_id="institution.calendar", label_hint="Academic Calendar", input_kind="select"),
        FieldDefinition(field_id="institution.first_term", label_hint="First Term", input_kind="text"),
        FieldDefinition(field_id="institution.accreditation", label_hint="Regional Accreditation", input_kind="text"),
        FieldDefinition(field_id="institution.ipeds_id", label_hint="IPEDS ID", input_kind="number"),
    ],
}


def get_section_field_definitions(section_id: str) -> list[FieldDefinition]:
    section_fields = DEFAULT_SECTION_FIELDS.get(section_id)
    if not section_fields:
        raise ValueError(f"Unsupported section_id for slice1: {section_id}")
    return section_fields


def split_fields_for_task(
    fields: list[FieldDefinition],
    max_fields_per_task: int,
) -> list[list[FieldDefinition]]:
    if max_fields_per_task <= 0:
        raise ValueError("max_fields_per_task must be greater than zero")
    return [fields[index : index + max_fields_per_task] for index in range(0, len(fields), max_fields_per_task)]


def build_validate_task(
    run_id: str,
    section_id: str,
    portal_url: str,
    webhook_callback_url: str,
    fields: list[FieldDefinition],
    expected_values: dict[str, str],
    chunk_index: int,
    chunk_total: int,
) -> ValidateTaskSpec:
    if not fields:
        raise ValueError("Validate task requires at least one field")

    extraction_schema = {
        field.field_id: {
            "type": "string",
            "description": f"{field.label_hint} value as currently shown on the page",
        }
        for field in fields
    }
    field_instructions = ", ".join(f'"{field.label_hint}"' for field in fields)
    section_title = section_id.replace("_", " ").title()

    user_prompt = (
        f"Navigate to the {section_title} section of the survey portal at {portal_url}. "
        "Do not edit any fields. "
        f"Read and extract the current displayed values for these {len(fields)} field(s): {field_instructions}. "
        f"This is chunk {chunk_index + 1} of {chunk_total}. "
        "COMPLETE when all listed field values have been extracted. "
        "TERMINATE immediately if the section cannot be found or a listed field label does not exist on the page. "
        f"Send callback to {webhook_callback_url} with run_id={run_id}."
    )

    return ValidateTaskSpec(
        section_id=section_id,
        url=portal_url,
        user_prompt=user_prompt,
        expected_values={field.field_id: expected_values.get(field.field_id, "") for field in fields},
        extraction_schema=extraction_schema,
    )


def build_scan_fields_task(
    run_id: str,
    section_id: str,
    portal_url: str,
    webhook_callback_url: str,
) -> ScanFieldsTaskSpec:
    section_fields = get_section_field_definitions(section_id)
    template_labels = ", ".join(field.label_hint for field in section_fields[:8])
    extraction_schema = {
        "scan_result_json": {
            "type": "string",
            "description": (
                "JSON array where each item includes label_text, input_kind, required_flag, "
                "candidate_field_id, section_name"
            ),
        }
    }
    section_title = section_id.replace("_", " ").title()
    user_prompt = (
        f"Navigate to the {section_title} section of the survey portal at {portal_url}. "
        "Do not edit any fields. "
        "Discover all visible data-entry fields in this section. "
        "Return scan_result_json as a valid JSON array, one object per field, with keys: "
        "label_text (exact label as shown), input_kind (text/number/select/radio/checkbox/textarea), "
        "required_flag (true/false), candidate_field_id (snake_case), section_name. "
        f"Expected field labels include: {template_labels}. "
        "COMPLETE when all visible fields in the section have been recorded. "
        "TERMINATE immediately if the section cannot be found on the page. "
        f"Send callback to {webhook_callback_url} with run_id={run_id}."
    )
    return ScanFieldsTaskSpec(
        section_id=section_id,
        url=portal_url,
        user_prompt=user_prompt,
        extraction_schema=extraction_schema,
    )


def build_fill_task(
    run_id: str,
    section_id: str,
    portal_url: str,
    webhook_callback_url: str,
    fields: list[FieldDefinition],
    expected_values: dict[str, str],
    chunk_index: int,
    chunk_total: int,
) -> FillTaskSpec:
    if not fields:
        raise ValueError("Fill task requires at least one field")

    extraction_schema = {
        "filled_fields_json": {
            "type": "string",
            "description": "JSON object mapping each attempted field_id to the value left in the field",
        },
        "submit_attempted": {
            "type": "string",
            "description": "Return true only if a final submit action was attempted; otherwise false",
        },
    }
    section_title = section_id.replace("_", " ").title()
    field_lines = [
        f"- {field.label_hint}: {expected_values.get(field.field_id, '')}"
        for field in fields
    ]
    user_prompt = (
        f"Navigate to the {section_title} section of the survey portal at {portal_url}. "
        f"Fill only these {len(fields)} field(s) with the exact values listed below. "
        "Do not click Submit, Finalize, Certify, Send, or any equivalent final submission control. "
        "A draft save action is allowed if required by the portal. "
        "If the only available save action would submit the survey, stop without saving. "
        f"This is chunk {chunk_index + 1} of {chunk_total}. "
        "COMPLETE when all listed fields have been filled and any draft save has been clicked. "
        "TERMINATE immediately if a listed field label cannot be found on the page. "
        "Field values:\n"
        + "\n".join(field_lines)
        + f"\nSend callback to {webhook_callback_url} with run_id={run_id}."
    )

    return FillTaskSpec(
        section_id=section_id,
        url=portal_url,
        user_prompt=user_prompt,
        expected_values={field.field_id: expected_values.get(field.field_id, "") for field in fields},
        extraction_schema=extraction_schema,
    )
