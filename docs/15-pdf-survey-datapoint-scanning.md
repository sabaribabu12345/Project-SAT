# PDF Survey Datapoint Scanning

Last updated: 2026-06-05

This document defines the first implementation slice for ingesting a survey PDF, extracting the fields it asks for, and mapping those fields to a reusable master datapoint list. It does not replace the Databricks-as-single-source rule in `13-databricks-as-single-source.md`; it adds the missing intake layer that tells the system what datapoints a new survey asks for.

## Goal

When an analyst receives a new survey PDF, the system should:

1. Inspect whether the PDF is fillable.
2. If fillable, extract AcroForm fields as high-confidence datapoint candidates.
3. If not fillable, extract likely datapoint labels from PDF text as lower-confidence candidates.
4. Persist the scan result and candidates in the control-plane DB.
5. Let an analyst map each candidate to a master datapoint.
6. Store Databricks binding metadata on the master datapoint so the resolver can later pull the value from Databricks.

## Current Slice

Implemented in this slice:

- `apps/api/pdf_scanner.py`
  - Detects AcroForm fields with `pypdf`.
  - Extracts fillable field names, tooltips/labels, inferred input kinds, and confidence.
  - Falls back to text-line candidate detection for flat PDFs.
- `survey_pdf_scans`
  - One row per scanned PDF file.
  - Stores file name, SHA-256, page count, fillable flag, candidate count, and raw scan JSON.
- `survey_pdf_datapoint_candidates`
  - One row per extracted candidate.
  - Stores source (`acroform` or `text`), label, normalized label, input kind, page number, confidence, and optional master mapping.
- `master_data_points`
  - Reusable canonical datapoint list.
  - Stores canonical name, semantic key, description, and Databricks binding metadata.
- API endpoints:
  - `POST /pdf-scans`
  - `GET /pdf-scans`
  - `GET /pdf-scans/{scan_id}`
  - `GET /pdf-scans/{scan_id}/candidates`
  - `GET /master-data-points`
  - `POST /master-data-points`
  - `PATCH /master-data-points/{data_point_id}/binding`
  - `POST /pdf-candidates/{candidate_id}/map`
- CLI:
  - `infra/scripts/scan_pdf_datapoints.py`

Implemented in the enrichment slice:

- AcroForm candidates now include widget page number and rectangle coordinates when the PDF exposes widget annotations.
- The scanner extracts nearby visible page text around each widget.
- Label selection now uses this priority:
  1. PDF tooltip/alternate label, when it is useful.
  2. Nearby visible page text, when the field name is only an internal abbreviation.
  3. Humanized field name fallback.
- Candidate rows now persist:
  - `label_source`
  - `field_rect_json`
  - `nearby_text`

Example: a raw field named `TUIT_VARY_PROG_P` can be saved with the visible label `If yes, what percentage of full-time undergraduates pay more than the tuition and fees reported in G1?` and `label_source = nearby_text`.

Implemented in the mapping/resolution slice:

- `master_data_point_aliases`
  - Stores alternate labels for the same canonical datapoint.
  - This handles cases where two survey PDFs ask for the same concept using different wording.
- Candidate-to-master suggestions
  - Compares candidate label, nearby text, and field name against master canonical names, semantic keys, and aliases.
  - Returns scored suggestions with a reason such as `label matched alias`.
- Mapped PDF scan resolution
  - Reads mapped candidates.
  - Looks up each candidate's master datapoint.
  - Uses the existing Databricks resolver path to return values.
  - Supports `literal:` bindings for local tests and real Databricks views for production.

New endpoints:

- `GET /pdf-ops`
- `POST /master-data-points/{data_point_id}/aliases`
- `GET /master-data-points/{data_point_id}/aliases`
- `GET /pdf-scans/{scan_id}/mapping-suggestions`
- `POST /pdf-scans/{scan_id}/resolve-values`
- `POST /pdf-scans/{scan_id}/publish-field-catalog`

## Why Fillable PDFs Are First

Fillable PDFs expose explicit form metadata. That is the best signal:

- field name
- field type
- tooltip/alternate label when present
- stable control identity

Flat PDFs usually only expose page text. Text extraction is useful but less precise because the scanner must infer which lines are actual datapoint prompts versus instructions or narrative text. Those candidates are therefore stored with lower confidence.

## Master Datapoint Strategy

Different survey forms may ask for the same meaning using different wording. Examples:

| Survey label | Master datapoint |
| --- | --- |
| `Total undergraduate enrollment` | `enrollment.total_undergraduates` |
| `Undergraduate headcount` | `enrollment.total_undergraduates` |
| `Number of first-time freshman applicants` | `admissions.first_time_freshman_applicants` |

The scanner does not decide this automatically yet. It creates candidates. An analyst maps each candidate to a `master_data_points.data_point_id`. Later slices can add fuzzy matching on `normalized_label` and `semantic_key` to suggest mappings.

## Databricks Binding

Each master datapoint can carry the same binding shape used by `survey_field_catalog`:

| Column | Meaning |
| --- | --- |
| `databricks_view` | Table/view to query. |
| `databricks_value_column` | Column containing the resolved value. |
| `databricks_year_column` | Optional year filter column, usually `survey_year`. |
| `transform_json` | Optional transform config for formatting or aggregation. |

This keeps the rule simple: survey PDFs define what is needed; master datapoints define the canonical meaning and how to read it from Databricks.

## CDS Registry Bridge

While the long-term master datapoint catalog is being formalized, the app can resolve known CDS PDF fields through `survey-automation/cds_sql_query_registry.md`.

Current bridge behavior:

- The registry parses markdown entries into `CdsRegistryQuery` objects.
- A known PDF field such as `AP_RECD_1ST_N`, `FRSH_GPA`, or `FT_N` can be resolved directly from its registry query without waiting for Genie to invent SQL.
- Section C mappings now use the analyst-provided 2025 admissions and GPA patterns from `Section C.sql`.
- Section I-1 mappings now use the analyst-provided faculty population pattern from `Section I.sql`, including `production.silver.ira_faculty`, faculty term `2254` for survey year 2025, and the analyst-maintained terminal degree / nonresident table.
- The loose `Section C.sql` and `Section I.sql` files are references only and should stay untracked.

## Analyst SQL Mapping

Analysts can now bring their own Databricks SQL when the registry or Genie direct resolution is not enough.

Workflow:

1. Open `/pdf-ops` and select a PDF scan.
2. Paste analyst-owned SQL in **Analyst SQL Mapping**.
3. Click **Preview SQL**. The backend executes the SQL in Databricks and stores the result columns/sample rows in `analyst_sql_queries`.
4. Click **Auto-map with Sonnet**. The backend sends the SQL, result shape, sample rows, PDF field names, labels, and datapoint intent to Databricks Model Serving using `DATABRICKS_SQL_MAPPING_MODEL` (default `databricks-claude-sonnet-4-6`).
5. The model creates `analyst_sql_mapping_drafts`; it does not write values directly.
6. The analyst approves individual drafts. Approval creates an `analyst_sql_field_mappings` row and writes the approved value into the existing PDF candidate resolved-value columns.
7. Future reruns use the saved SQL and approved mapping row/column coordinates. No model call is needed unless the analyst asks to remap.

Important behavior:

- SQL may return readable columns and rows; it does not need to return `pdf_field, value`.
- The model proposes mappings only. Analyst approval is required before a field becomes ready for review/export.
- Approved values appear in the same resolved-values table used by filled-PDF export.
- The SQL text is stored in the local control-plane DB. Do not paste credentials or secret tokens into SQL comments.

## Test Locally

Install the new dependency:

```bash
cd /Users/harshas/code/csulb/IRA/survey-agent/survey-automation-project/survey-automation
.venv/bin/python -m pip install -e ".[dev]"
```

Scan any local PDF:

```bash
.venv/bin/python infra/scripts/scan_pdf_datapoints.py "/path/to/survey.pdf" --survey-id test_pdf
```

Expected output:

```json
{
  "file_name": "survey.pdf",
  "fillable": true,
  "page_count": 12,
  "candidates": [
    {
      "source": "acroform",
      "label_text": "Total undergraduate enrollment",
      "label_source": "nearby_text",
      "page_number": 12,
      "field_rect": [111.676, 498.451, 196.222, 522.119],
      "input_kind": "text",
      "confidence": 0.85
    }
  ]
}
```

For a non-fillable PDF, `fillable` should be `false`, and candidates should have `"source": "text"` with lower confidence.

## Test Through Docker API

Put a PDF into:

```bash
survey-automation/uploads/
```

Rebuild the control plane so the new dependency and upload mount are available:

```bash
docker compose -f infra/docker-compose.yml up --build -d control-plane
```

Create a scan:

```bash
curl -s -X POST http://localhost:8010/pdf-scans \
  -H "Content-Type: application/json" \
  -d '{"survey_id":"test_pdf","file_path":"/app/uploads/YOUR_FILE.pdf"}' | jq
```

Expected output:

```json
{
  "scan_id": "pdfscan_...",
  "survey_id": "test_pdf",
  "fillable": true,
  "candidate_count": 10,
  "candidates": [
    {
      "candidate_id": "pdfcand_...",
      "label_text": "...",
      "status": "DISCOVERED"
    }
  ]
}
```

List scans if you are unsure which scan ID exists in the running API database:

```bash
curl -s http://localhost:8010/pdf-scans | jq
```

Use a `scan_id` returned by that command for mapping suggestions and value resolution.

Create a master datapoint:

```bash
curl -s -X POST http://localhost:8010/master-data-points \
  -H "Content-Type: application/json" \
  -d '{
    "data_point_id":"dp.enrollment.total_undergraduates",
    "canonical_name":"Total undergraduate enrollment",
    "semantic_key":"enrollment.total_undergraduates",
    "databricks_view":"production.silver.erss",
    "databricks_value_column":"headcount",
    "databricks_year_column":"years"
  }' | jq
```

Map a PDF candidate to the master datapoint:

```bash
curl -s -X POST http://localhost:8010/pdf-candidates/PDFCAND_ID/map \
  -H "Content-Type: application/json" \
  -d '{"master_data_point_id":"dp.enrollment.total_undergraduates"}' | jq
```

Expected output has:

```json
{
  "master_data_point_id": "dp.enrollment.total_undergraduates",
  "status": "MAPPED"
}
```

Add an alias so future PDFs with different wording can suggest the same master datapoint:

```bash
curl -s -X POST http://localhost:8010/master-data-points/dp.enrollment.total_undergraduates/aliases \
  -H "Content-Type: application/json" \
  -d '{"alias_text":"Undergraduate headcount","source":"analyst"}' | jq
```

Get mapping suggestions:

```bash
curl -s "http://localhost:8010/pdf-scans/SCAN_ID/mapping-suggestions?limit_per_candidate=3" | jq
```

Expected output:

```json
[
  {
    "candidate_id": "pdfcand_...",
    "field_name": "URL_DESTINATION_URL",
    "label_text": "Main Institution Website",
    "suggestions": [
      {
        "data_point_id": "dp.institution.website",
        "score": 100,
        "reason": "label matched canonical"
      }
    ]
  }
]
```

Resolve mapped candidates:

```bash
curl -s -X POST http://localhost:8010/pdf-scans/SCAN_ID/resolve-values \
  -H "Content-Type: application/json" \
  -d '{"survey_year":2026}' | jq
```

Expected output:

```json
{
  "scan_id": "SCAN_ID",
  "survey_year": 2026,
  "values": {
    "pdfcand_...": {
      "value": "https://www.csulb.edu",
      "master_data_point_id": "dp.institution.website",
      "canonical_name": "Main Institution Website"
    }
  },
  "missing_candidates": [],
  "unmapped_candidates": ["pdfcand_..."]
}
```

Publish mapped candidates to `survey_field_catalog` for Skyvern fill/validate:

```bash
curl -s -X POST http://localhost:8010/pdf-scans/SCAN_ID/publish-field-catalog \
  -H "Content-Type: application/json" \
  -d '{"section_id":"pdf_usnews","overwrite":true}' | jq
```

Expected output:

```json
[
  {
    "field_id": "pdf.test_pdf_enriched.acroform.url_destination_url",
    "section_id": "pdf_usnews",
    "label_text": "Main Institution Website",
    "databricks_value_column": "literal:https://www.csulb.edu",
    "status": "ACTIVE"
  }
]
```

After publishing, this catalog section can be inspected with:

```bash
curl -s http://localhost:8010/field-catalog/pdf_usnews | jq
```

## Analyst UI

Open:

```text
http://localhost:8010/pdf-ops
```

The page provides the current PDF mapping workflow in one place:

- scan a PDF path mounted under `/app/uploads`
- list existing scans
- inspect enriched candidates
- filter mapped/unmapped candidates
- load mapping suggestions
- create master datapoints and aliases
- map candidates
- resolve mapped values
- publish mapped candidates to `survey_field_catalog`

For a real Databricks binding, use:

```json
{
  "databricks_view": "production.silver.erss",
  "databricks_value_column": "headcount",
  "databricks_year_column": "years"
}
```

For a local smoke test, use:

```json
{
  "databricks_value_column": "literal:https://www.csulb.edu"
}
```

## Next Slice

The next slice should connect the published PDF catalog section to form filling:

1. Trigger `dispatch-fill` / `execute-draft-fill-pipeline` from a published PDF catalog section.
2. Add OCR support for scanned image PDFs where `pypdf` text extraction returns no useful text.
