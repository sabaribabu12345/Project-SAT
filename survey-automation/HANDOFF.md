# Survey Automation v3 - Engineering Handoff

Last updated: 2026-06-05 (session 15)

Scope: cumulative handoff since the previous version, not only the May 26 runtime state.

This is the active handoff for `survey-automation-project/survey-automation`.

Canonical docs still live under [`../docs/`](../docs/), but the runnable implementation has moved ahead of some older docs. Treat this file and the current code as the operational source of truth.

For a full operations guide (setup, run modes, browser switching, diagnostics), see [`OPERATIONS.md`](OPERATIONS.md).

---

## What Changed Since The Previous Handoff (2026-06-05, session 15)

### Analyst SQL Mapping for PDF fields

Added a first slice for analyst-owned SQL mapping on `/pdf-ops`.

New workflow:
- Analyst selects a PDF scan and pastes Databricks SQL in **Analyst SQL Mapping**.
- **Preview SQL** executes the SQL through the configured Databricks SQL warehouse and stores result columns/sample rows in `analyst_sql_queries`.
- **Auto-map with Sonnet** sends SQL text, columns, sample rows, PDF field names, labels, and datapoint intent to Databricks Model Serving.
- Model output becomes `analyst_sql_mapping_drafts`; it is not applied automatically.
- Analyst approves a draft. Approval creates/updates an `analyst_sql_field_mappings` row and writes the value into the existing PDF candidate resolved-value fields.
- **Rerun approved SQL** executes the saved SQL again and refreshes approved fields without calling the model.

New tables:
- `analyst_sql_queries`
- `analyst_sql_mapping_drafts`
- `analyst_sql_field_mappings`

New endpoints:
- `POST /pdf-scans/{scan_id}/analyst-sql/preview`
- `POST /analyst-sql/{query_id}/auto-map`
- `POST /analyst-sql-mapping-drafts/{draft_id}/approve`
- `POST /analyst-sql/{query_id}/rerun-approved`

New config:
- `DATABRICKS_SQL_MAPPING_MODEL`, default `databricks-claude-sonnet-4-6`
- `DATABRICKS_SQL_MAPPING_TIMEOUT_SECONDS`, default `45`

Files changed:
- `apps/api/analyst_sql_mapping.py`
- `apps/api/db/models.py`
- `apps/api/main.py`
- `apps/api/schemas.py`
- `apps/api/settings.py`
- `tests/test_analyst_sql_mapping.py`
- `tests/test_pdf_ops_page.py`
- `README.md`
- `docs/15-pdf-survey-datapoint-scanning.md`

Verification:
- `.venv-test/bin/python -m py_compile apps/api/analyst_sql_mapping.py apps/api/main.py apps/api/schemas.py apps/api/db/models.py apps/api/settings.py`
- `.venv-test/bin/pytest tests/test_analyst_sql_mapping.py tests/test_pdf_ops_page.py tests/test_data_points_page.py -q`
- `.venv-test/bin/pytest -q`
- `rtk proxy git add -n .`

Observed:
- focused analyst SQL/PDF ops/data-points tests: `5 passed, 4 warnings`
- full suite: `93 passed, 4 warnings`
- Git add dry-run includes source/docs/tests only; local SQL reference files remain ignored.

---

## What Changed Since The Previous Handoff (2026-06-04, session 14)

### Section C and Section I CDS registry mappings

Used local analyst reference files `Section C.sql` and `Section I.sql` to update the committed CDS query registry. The SQL files remain untracked references and should not be committed.

Updated behavior:
- `Q-C1-C2` now follows the analyst Section C admissions pattern for survey year 2025:
  - Fall 2025 admissions term renders as `20254`.
  - Sex buckets use `TYLERN.GENDER(...)`.
  - Residency buckets use `RESIDENCE_CODE`.
  - Output shape remains `dimension, bucket, applied_n, admitted_n, enrolled_n, enrolled_ft_n, enrolled_pt_n`, so the existing PDF mapper continues to fill fields such as `AP_RECD_1ST_N`, `AP_ADMT_1ST_N`, and `EN_TOT_1ST_N`.
- `Q-C11-C12-C13` now follows the analyst Section C GPA pattern:
  - Uses unweighted `HS_GPA` integer bins from `production.silver.erss`.
  - Adds `FRSH_GPA` mapping for average high-school GPA.
  - Supports local PDF `FRSH_GPA_NO_SUB_*_P` fields as aliases for the all-enrolled GPA percentages.
- Added `Q-I1` for Section I-1 instructional faculty:
  - Uses `production.silver.ira_faculty`.
  - Adds registry parameter `__FACULTY_FALL_TERM__`; for survey year 2025 this renders as `2254`.
  - Adds registry parameter `__FACULTY_NRA_TM_TABLE__`; for survey year 2025 this renders as `TYLERN.CDS_FACULTY_NRA_TM_F25_TBL`.
  - Maps actual scanned PDF fields including `FT_N`, `PT_N`, `TOT_N`, `MIN_TOT_N`, `TOT_WMN_N`, `TOT_MEN_N`, `NRES_TOT_N`, `FT_DEG_TERM_N`, `MASTER_TOT_N`, `BACH_TOT_N`, `UNKNOWN_TOT_N`, and `GRAD_TOT_N`.
- `apps/api/cds_query_registry.py` now has a Section I dimensional mapper and faculty-year placeholders.
- Root and app-local `cds_sql_query_registry.md` are kept in sync.
- Root `.gitignore` now ignores `Section C.sql` and `Section I.sql` because they are analyst reference files, not project artifacts.
- Updated docs:
  - `.gitignore`
  - `docs/10-repo-layout-and-migration.md`
  - `docs/13-databricks-as-single-source.md`
  - `docs/15-pdf-survey-datapoint-scanning.md`

Verification:
- `.venv-test/bin/pytest tests/test_pdf_mapping.py -q`
- `.venv-test/bin/pytest -q`
- `rtk proxy git add -n .`

Observed:
- focused registry/PDF mapping tests: `47 passed`
- full suite: `92 passed, 4 warnings`
- Git add dry-run includes source/docs/tests/registry only; `Section C.sql` and `Section I.sql` remain ignored.

---

## What Changed Since The Previous Handoff (2026-06-04, session 13)

### Pre-push documentation refresh

Updated current setup and handoff references before GitHub push:
- `survey-automation/README.md` now uses `cd survey-automation` for first-time setup.
- `OPERATIONS.md` and current handoff run commands now point at the actual local repo path: `survey-automation-project`.
- Databricks grant and PDF scanner docs no longer reference the old checkout path.
- Git dry-run is expected to include source/docs/examples/tests only; generated data, local secrets, runtime artifacts, uploads, videos, HAR files, caches, and `.codegraph` stay ignored.

Verification:
- `.venv-test/bin/python infra/scripts/validate_fake_form_data.py ../fake-survey-form/fake-survey-form-data.example.json`
- `.venv-test/bin/pytest tests/test_website_fast_flow.py tests/test_pdf_ops_page.py tests/test_data_points_page.py -q`
- `.venv-test/bin/pytest -q`
- `rtk proxy git add -n .`
- `rtk git status --short --ignored`

Observed:
- sanitized fake-form example validation: `ok=true`
- focused website/browser/data-point tests: `15 passed, 4 warnings`
- full suite: `90 passed, 4 warnings`
- Git add dry-run includes source, docs, sanitized examples, and tests; local secrets and generated/runtime files remain ignored.

### Website flow: CDP attach, login-on-demand, shared data points

Completed the pending website-flow operator work from the Codex design:

- `/website-ops` now has a dedicated **CDP browser connection** panel:
  - shows current `BROWSER_TYPE` / CDP URL from runtime config
  - copyable Chrome and Edge launch commands
  - **Check browser connection** button (`GET /website-ops/browser-check`)
  - **Use current browser session** checkbox (auto-generates `browser_session_id` when blank)
- Added optional **Website requires login** flow:
  - username/password required when checked
  - credentials passed only through subprocess env (`WEBSITE_LOGIN_USERNAME`, `WEBSITE_LOGIN_PASSWORD`)
  - excluded from persisted `workflow_jobs.request_json` and job detail API responses
  - Skyvern prompt includes login instructions only for that run
- Added `/website-ops/browser-config` for UI bootstrap of browser settings.
- `/data-points` remains the shared master data point catalog for PDF and web flows (search, filters, resolved value preview, inline binding edit).
- Job history list now includes `portal_url` for each run.

Verification:
- `.venv-test/bin/pytest tests/test_website_fast_flow.py tests/test_pdf_ops_page.py tests/test_data_points_page.py -q`
- `.venv-test/bin/pytest -q`

Observed:
- focused website/login/browser tests: `15 passed`
- full suite: `74 passed, 4 warnings`

---

## What Changed Since The Previous Handoff (2026-06-03, session 11)

### Repo cleanup for GitHub readiness

The repository now has clearer source/runtime boundaries:
- Added a root `.gitignore` so local indexes, `.env` files, Python caches, runtime data, uploads, videos, HAR files, logs, and downloaded survey artifacts do not get staged accidentally.
- Expanded `survey-automation/.gitignore` with the same app-local generated folders and secret files.
- Replaced source-controlled mutable `fake-survey-form-data.json` with sanitized `fake-survey-form-data.example.json`; the real/generated JSON is now ignored.
- Updated the website fill and Databricks pull scripts to use the sanitized example as baseline input when generated JSON is absent.
- Added `infra/scripts/run_website_form_fill.py` as the current generic website Skyvern fill runner.
- Kept `infra/scripts/run_full_fake_form_fill.py` as a compatibility wrapper for older fake-form smoke-test commands.
- Updated `apps/api/main.py` so `/website-ops/full-workflow/jobs` calls `run_website_form_fill.py`.
- Updated current README/OPERATIONS/HANDOFF references to prefer `/website-ops` and `run_website_form_fill.py`.

Verification:
- `rtk .venv/bin/python infra/scripts/validate_fake_form_data.py ../fake-survey-form/fake-survey-form-data.example.json`
- `rtk .venv/bin/pytest tests/test_website_fast_flow.py -q`
- `rtk .venv/bin/pytest tests/test_website_fast_flow.py tests/test_pdf_ops_page.py -q`
- `rtk .venv/bin/pytest -q`
- `rtk proxy git add -n .`
- `rtk git status --short --ignored`

Observed:
- sanitized fake-form example validation: `ok=true`
- focused website runner tests: `5 passed`
- focused website/page tests: `8 passed`
- full suite: `67 passed, 3 warnings`
- Git add dry-run includes source/docs/examples/tests only; local secrets, real generated JSON, downloads, uploads, caches, runtime DB/data/artifacts, videos, HAR files, and `.codegraph` are ignored.

### Fast Skyvern website flow

The website flow now has a faster Skyvern-first path while keeping the PDF flow unchanged.

Updated behavior:
- `/website-ops` explains the fast website workflow for nontechnical users:
  - open the survey website first
  - sign in in the browser if needed
  - attach Skyvern to the current browser through CDP/current session
  - run a direct Skyvern fill using resolved values
- The page now includes:
  - `Browser session ID` for reusing the current browser session
  - `Skyvern max steps` for controlling how much navigation/filling Skyvern may do
  - CDP browser connection guidance using `BROWSER_TYPE=cdp-connect` and `BROWSER_REMOTE_DEBUGGING_URL=http://host.docker.internal:9222/`
  - a clear note that no passwords are stored by the page
- `FullWorkflowLaunchRequest` now accepts:
  - `browser_session_id`
  - `skyvern_max_steps`
- `_execute_full_workflow_job(...)` passes those values to `infra/scripts/run_website_form_fill.py` as:
  - `--browser-session-id`
  - `--max-steps`
- `run_website_form_fill.py` now has a testable `build_run_body(...)` helper.
- `run_full_fake_form_fill.py` remains as a compatibility wrapper for older fake-form smoke-test commands.
- The Skyvern prompt now tells the agent to:
  - use the already-open browser session when provided
  - match visible labels, field names, aria-labels, placeholder text, option text, and nearby section headings
  - avoid rescanning completed sections
  - avoid final Submit/Finalize/Certify
  - report `filled_count`, `skipped_count`, `readback_count`, `submit_attempted`, and `notes`

Design/plan docs added:
- `docs/superpowers/specs/2026-06-03-fast-skyvern-website-flow-design.md`
- `docs/superpowers/plans/2026-06-03-fast-skyvern-website-flow.md`

Verification:
- `rtk .venv/bin/pytest tests/test_website_fast_flow.py -q`
- `rtk .venv/bin/pytest tests/test_pdf_ops_page.py::test_website_ops_page_is_website_only -q`
- `rtk .venv/bin/pytest tests/test_website_fast_flow.py tests/test_pdf_ops_page.py -q`
- `rtk .venv/bin/pytest -q`

Observed:
- focused fast-flow tests: `2 passed`
- focused website page test: `1 passed`
- combined fast-flow/page tests: `5 passed`
- full suite: `64 passed, 3 warnings`

---

## What Changed Since The Previous Handoff (2026-06-02, session 10)

### Filled PDF export path fallback

Older PDF scan rows can store a host-local path such as:

```text
/Users/harshas/code/.../survey-automation/uploads/survey.pdf
```

That path does not exist inside the `control-plane` container. The container sees the same upload through the Docker mount:

```text
/app/uploads/survey.pdf
```

Updated behavior:
- `Settings` now includes `pdf_upload_dir`, defaulting to `/app/uploads`.
- `PdfDatapointService.export_resolved_values_to_pdf(...)` first uses the stored scan path when it exists.
- If the stored path is missing, export falls back to the configured upload directory using the stored filename or the subpath after `uploads`.

Verification:
- `rtk .venv/bin/pytest tests/test_pdf_export.py -q`
- `rtk .venv/bin/pytest -q`

Observed:
- focused export tests: `2 passed`
- full suite: `62 passed, 3 warnings`
- rebuilt `control-plane`
- `POST /pdf-scans/pdfscan_bb676d3416e3/export-filled-pdf` returned HTTP `200`
- response used `source_file_path=/app/uploads/survey.pdf`
- response reported `filled_count=154`, `skipped_count=0`, and no missing PDF fields
- `GET /pdf-exports/pdfscan_bb676d3416e3_survey_resolved.pdf` returned HTTP `200` and a 2.8 MB PDF
- downloaded PDF inspection found filled AcroForm values

---

## Earlier Session Notes

### Separate PDF and website flows

### Separate PDF and website flows

There are now two explicit operations pages:
- `/pdf-ops` — PDF scan, Genie resolve, filled-PDF export.
- `/website-ops` — website survey form workflow using the website form URL and full workflow jobs.

`/ops` remains a compatibility alias for the website workflow. The website job API also has `/website-ops/full-workflow/jobs` aliases in addition to the older `/ops/full-workflow/jobs` routes.

The pages include a small workflow switcher:
- PDF page links to **PDF flow** and **Website flow**.
- Website page links to **PDF flow** and **Website flow**.

Verification:
- `rtk .venv/bin/pytest tests/test_pdf_ops_page.py -q`
- `rtk .venv/bin/pytest -q`

Observed:
- focused flow-separation tests: `3 passed, 3 warnings`
- full suite: `61 passed, 3 warnings`
- live `/pdf-ops`, `/website-ops`, and `/ops` returned HTTP 200 after rebuilding `control-plane`

### PDF-only fill workflow

The `/pdf-ops` page now stays entirely on the PDF path:
scan -> Genie resolve -> export filled PDF.

Removed from the PDF ops UI:
- portal URL input
- `Fill from Resolved Values` button
- Skyvern-specific portal fill wording

The backend portal-fill endpoints still exist for the broader system, but they are no longer part of the PDF ops workflow.

Verification:
- `rtk .venv/bin/pytest tests/test_pdf_ops_page.py -q`
- `rtk .venv/bin/pytest -q`

Expected result:
- `/pdf-ops` now describes exporting a filled PDF, not filling a portal.
- No Skyvern language appears in the `/pdf-ops` page.

### Website draft-fill error handling

The website portal-fill endpoints previously returned an opaque HTTP 500 when the browser automation backend could not resolve the form URL.

Updated behavior:
- `POST /runs/{run_id}/execute-section-pipeline` now converts `RuntimeError` into HTTP 400.
- `POST /runs/{run_id}/execute-draft-fill-pipeline` now converts `RuntimeError` into HTTP 400.
- Website fill remains on `/website-ops` and the `/ops` compatibility alias.
- PDF export remains on `/pdf-ops`.

Regression coverage:
- Added API tests for both pipeline endpoints to confirm Skyvern errors are returned as readable 400s.

Verification:
- `rtk .venv/bin/pytest tests/test_genie_jobs_api.py tests/test_pdf_ops_page.py -q`
- `rtk .venv/bin/pytest -q`

Observed:
- focused tests: `6 passed, 3 warnings`
- full suite: `59 passed, 3 warnings`

Live smoke after rebuild:
- `GET /health` returned `{"status":"ok", ...}`
- `POST /runs/pdf_fill_live_smoke_20260602/execute-draft-fill-pipeline` with `portal_url=http://fake-form/?realData=1` returned HTTP `400`
- The response body contained the Skyvern error text instead of a generic 500

### Filled PDF export path

The current focus shifted from Genie mapping quality to exporting resolved values back into the source PDF.

New backend behavior:
- `PdfDatapointService.export_resolved_values_to_pdf(...)` fills AcroForm fields using rows where:
  - `status = "GENIE_RESOLVED"`
  - `genie_value` is non-empty
  - `field_name` matches an actual field in the source PDF
- Output defaults to `pdf_export_dir`, now configured in `Settings` as `/app/data/pdf_exports`.
- The result reports:
  - `filled_count`
  - `skipped_count`
  - `missing_pdf_fields`
  - source and output PDF paths

New API endpoints:

```text
POST /pdf-scans/{scan_id}/export-filled-pdf
GET  /pdf-exports/{file_name}
```

The POST endpoint returns a `download_url` when the output file is under the configured export directory.

### `/pdf-ops` export control

Step 4 now says **Export filled PDF** and includes:
- **Export Filled PDF** — calls `POST /pdf-scans/{scan_id}/export-filled-pdf`
- **Download filled PDF** link after export succeeds

### Verification

Automated tests:

```bash
rtk .venv/bin/pytest tests/test_pdf_export.py tests/test_pdf_ops_page.py -q
rtk .venv/bin/pytest -q
```

Observed:
- focused export/UI tests: `2 passed, 3 warnings`
- full suite: `57 passed, 3 warnings`

Live smoke test after Docker rebuild:
- Created `pdfscan_export_smoke_20260602` pointing at `/app/uploads/survey.pdf`
- Inserted one `GENIE_RESOLVED` value for real PDF field `CDS_NAME`
- Called:

```bash
POST /pdf-scans/pdfscan_export_smoke_20260602/export-filled-pdf
```

Response:

```json
{
  "scan_id": "pdfscan_export_smoke_20260602",
  "source_file_path": "/app/uploads/survey.pdf",
  "output_file_path": "/app/data/pdf_exports/pdfscan_export_smoke_20260602_survey_resolved.pdf",
  "download_url": "/pdf-exports/pdfscan_export_smoke_20260602_survey_resolved.pdf",
  "filled_count": 1,
  "skipped_count": 0,
  "missing_pdf_fields": []
}
```

Verified with `pypdf` that `CDS_NAME` in the exported PDF equals `CSULB Export Smoke`.
Verified `GET /pdf-exports/pdfscan_export_smoke_20260602_survey_resolved.pdf` returned HTTP `200` and a `2.7M` PDF.

Operational note:
- Existing scan `pdfscan_bb676d3416e3` stores a host-local `file_path`, but export now falls back to `/app/uploads/survey.pdf` when the stored path is not visible inside Docker.

---

## What Changed Since The Previous Handoff (2026-06-02, session 5)

### Genie prompt hardening for 10-field calls

`DatabricksGenieClient._build_resolution_prompt(...)` now gives Genie a more explicit instruction block:
- identify the task as survey-PDF-to-Databricks mapping
- keep the response directly executable in SQL
- return a narrow `field_name` / `value` result set
- emit one row per requested field
- return `NULL` only when a field truly cannot be resolved
- keep `field_name` exact and uppercase

The regression test `tests/test_pdf_mapping.py::test_genie_resolution_prompt_is_explicit_about_narrow_sql_output` locks this in.

### Real Genie smoke test: 10 fields, survey year 2025

Two live Genie calls were made from the control-plane container using real Databricks credentials:

1. **B1 enrollment slice** (`10` fields, `survey_year=2025`)
   - Genie returned a narrow `field_name` / `value` SQL query.
   - Result quality was weak for this slice: most values were `0`.
   - One field returned `2026` for `ACAD_YR`, which shows Genie can still ignore the requested year and why the prompt must stay explicit.

2. **H. FINANCIAL AID slice** (`10` fields, `survey_year=2025`)
   - Genie again returned a narrow query.
   - Most fields resolved to `NULL` / `0`.
   - `ACAD_YR` resolved to `2026` with confidence `85`, even though the request asked for `2025`.

Operational takeaway:
- `10`-field requests are now feasible and the parser handles the output shape.
- For this data set, the current Genie quality is still uneven on some sections, so smaller batches or section-specific retries remain the safer path for production use.

Verification:

```bash
rtk .venv/bin/pytest tests/test_pdf_mapping.py::test_genie_resolution_prompt_is_explicit_about_narrow_sql_output tests/test_pdf_mapping.py::test_genie_query_result_parser_handles_databricks_typed_value_rows -q
rtk .venv/bin/pytest -q
rtk docker-compose -f infra/docker-compose.yml up --build -d control-plane
```

Live Genie call commands were run inside the rebuilt `control-plane` container using `survey_year=2025` and `10`-field batches.

---

## What Changed Since The Previous Handoff (2026-06-02, session 4)

### `/pdf-ops` redesigned for non-technical operators

The `/pdf-ops` page was redesigned from a developer-heavy mapping console into a four-step internal operations workflow:

1. **Step 1: Scan the PDF** — enter PDF path + survey name, scan, and select an existing scan.
2. **Step 2: Resolve values with Genie** — choose survey year, Genie batch size, confidence threshold, and whether to re-run already resolved fields.
3. **Step 3: Review what will be used** — see PDF fields found, label-model explanations, resolved values, confidence/status, Genie reasons, and stored SQL.
4. **Step 4: Fill the portal** — dispatch Skyvern fill tasks from `GENIE_RESOLVED` values.

Visible legacy controls were removed from this page:
- master-data-point creation
- alias creation
- bootstrap masters
- auto-map scan
- mapping suggestions
- publish catalog
- draft mapping approvals

Those backend endpoints still exist, but `/pdf-ops` now focuses on the current Genie-first operating path.

### Tooltips and plain-language copy

Each confusing input now has a `What is this?` tooltip:
- PDF file path
- survey name
- scan selector
- survey year
- Genie batch size
- minimum confidence
- portal URL

Defaults were adjusted for the current handoff recommendations:
- `survey_id`: `cds_2024`
- `label_enrichment_candidate_limit`: `2000`
- Genie `batch_size`: `15`
- Genie `min_confidence`: `60`

### Raw backend errors shown for internal debugging

The old API wrapper compressed errors down to a short `detail` string. The new page keeps raw backend context:

```json
{
  "error": "HTTP <status> <method> <path>",
  "status": 400,
  "method": "POST",
  "path": "/pdf-scans/<scan_id>/resolve-via-genie",
  "request_body": {},
  "response_body": {}
}
```

Every user action now routes failures through `showBackendError(...)`, which writes the raw payload into the **Raw backend response** panel and the local action status.

### Verification

Commands run:

```bash
rtk .venv/bin/pytest tests/test_pdf_ops_page.py -q
rtk .venv/bin/pytest -q
rtk docker-compose -f infra/docker-compose.yml up --build -d control-plane
rtk curl -sS http://localhost:8010/health
rtk curl -sS http://localhost:8010/pdf-ops -o /tmp/pdf_ops_live.html
rtk sh -c '! rg -n "Create Master|Auto-Map Scan|Publish Catalog|Bootstrap Masters|/mapping-suggestions" /tmp/pdf_ops_live.html'
```

Observed results:
- focused `/pdf-ops` test: `1 passed, 3 warnings`
- full suite: `55 passed, 3 warnings`
- rebuilt `control-plane` container started
- live `/health`: `{"status":"ok","control_plane_db":"sqlite","database_url":"sqlite:////app/data/control_plane.db"}`
- live `/pdf-ops` contains the four-step workflow and raw backend response panel
- live `/pdf-ops` does not contain the removed legacy mapping controls listed above

Browser automation was not available via `tool_search` in this session, so verification used static tests plus served HTML checks.

---

## What Changed Since The Previous Handoff (2026-06-02, session 3)

### Genie response parser: dual-format support

Genie does not reliably produce one format. It returns either:

- **Wide format** (CASE WHEN query): one row, columns named after field names.
  `columns=["EN_FRSH_FT_MEN_N","EN_FRSH_FT_WMN_N",...], rows=[[2490, 3952,...]]`
- **Narrow format** (UNION ALL query): multiple rows, two columns `field_name` + `value`.
  `rows=[["EN_FRSH_FT_MEN_N","2490"],["EN_FRSH_FT_WMN_N","3952"],...]`

`_fetch_query_result` in [apps/api/databricks_genie_client.py](apps/api/databricks_genie_client.py) now returns `(columns, rows)` instead of just `rows`.

`resolve_many_candidates` detects the format:
- If `columns` is non-empty and is **not** a 2-column `[field_name, value]` schema → wide format, zip column names with first row.
- Otherwise → narrow format, iterate rows as `[field_name_str, value_str]` pairs.

Column names are read from `statement_response.manifest.schema.columns` in the `/query-result` response. Each resolved candidate gets `genie_column = field_name_upper` (the column it came from).

**File changed:** `apps/api/databricks_genie_client.py`
- `_fetch_query_result` signature: `→ tuple[list[str], list[list[Any]]]`
- `resolve_many_candidates`: dual-format parse logic
- `_build_resolution_prompt`: prompt now says `"I need these values with SQL query for each field:"` (matches what Genie GUI responds to; removed UNION ALL instruction)

**Test updated:** `tests/test_pdf_mapping.py::test_genie_query_result_parser_handles_databricks_typed_value_rows` — unpacks `(columns, rows)` from `_fetch_query_result`.

### Live smoke test results (2026-06-02)

Scan: `pdfscan_bb676d3416e3` (1086 enriched candidates, `survey_id=cds_2024`)

**Run 1 — batch_size=5, force_regenie=False** (26 min):
```
resolved: 56  low_confidence: 5  failed: 977  skipped: 48
```
- 56 resolved across transfer admissions + aid sections
- 977 failed = Genie timed out at 120s poll limit for ~80% of batches
- batch_size=5 → ~217 serial Genie calls; each call starts with ~48s cold start → too slow

**Run 2 — batch_size=50, force_regenie=True** (81 min, all 1086 candidates):
```
resolved: 0 (new this run)  failed: 1037  skipped: 49
DB totals: GENIE_RESOLVED=154  GENIE_LOW_CONFIDENCE=744  DISCOVERED=188
```
- 154 total GENIE_RESOLVED in DB (combined from both runs)
- batch_size=50 still fails most batches: 50-field prompt makes Genie take >120s frequently
- `resolved=0` this run because `force_regenie=True` re-ran all, but the 154 already-resolved fields were overwritten with new results (which also resolved them)

**B1 enrollment sample from DB after run 2:**
```
EN_FRSH_FT_MEN_N  = 0      ← WRONG (expected 2490)
EN_FRSH_FT_WMN_N  = 1089   ← WRONG (expected 3952)
EN_FRSH_PT_MEN_N  = 0      ← WRONG (expected 48)
EN_FRSH_PT_WMN_N  = 1449   ← WRONG (expected 54)
```
The wrong values indicate Genie's CASE WHEN logic is matching the wrong GENDER filter when given 50 fields at once. When queried individually (confirmed in GUI), Genie returns the correct values (2490, 3952, 48, 54).

### Root cause analysis and next steps for Codex

**Problem 1 — Timeout failure rate (~80% of batches fail)**

`databricks_genie_poll_timeout_seconds = 120` is too short. Each Genie conversation:
- `start-conversation` POST: ~48s (OAuth + Genie cold start)
- Processing time for 50 fields: 60-120s
- Total: 108–168s → exceeds 120s deadline regularly

**Fix options (pick one):**
1. Increase `databricks_genie_poll_timeout_seconds` to `300` in `settings.py` and `.env`
2. Reduce `batch_size` to 10-15 fields per call (fewer fields → faster Genie response)
3. Both: timeout=300, batch_size=20

**Problem 2 — Wrong values for multi-field batches**

When 50 enrollment fields are sent together, Genie's CASE WHEN logic conflates filters across fields. The GUI test with 5 B1 fields produced correct values (2490, 3952, 48, 54). The 50-field batch produced wrong values.

**Fix options (pick one):**
1. Reduce `batch_size` to 5-10 for enrollment sections, 20-30 for simpler yes/no sections
2. After getting Genie-resolved values, validate by re-running the stored SQL directly against Databricks and comparing; flag any that deviate >10%
3. Use `resolve_pdf_scan_direct` after initial Genie pass to re-execute stored SQL and catch wrong values — if direct SQL produces a different value, mark `status=NEEDS_REGENIE` and re-run single-field

**Problem 3 — `genie_sql_template` stores the shared query for all 50 fields**

All 50 fields in a batch share one SQL template. When `resolve_pdf_scan_direct` re-runs it, it gets all 50 values in one row — but the current direct-SQL runner (`resolve_pdf_scan_direct` in `service.py`) calls `query_rows(sql, row_limit=1)` and reads `rows[0][0]`, which only gets the first column. It needs to read the specific column for each candidate (`genie_column`).

**Fix required in `service.py::resolve_pdf_scan_direct`:**
```python
# Current (wrong for wide-format SQL):
_cols, rows = reader.query_rows(sql, row_limit=1)
value = str(rows[0][0]) if rows and rows[0] and rows[0][0] is not None else None

# Fix: find the column index matching genie_column
_cols, rows = reader.query_rows(sql, row_limit=1)
if rows and _cols and row.genie_column:
    try:
        col_idx = [c.upper() for c in _cols].index(row.genie_column.upper())
        value = str(rows[0][col_idx]) if rows[0][col_idx] is not None else None
    except ValueError:
        value = str(rows[0][0]) if rows[0][0] is not None else None
else:
    value = str(rows[0][0]) if rows and rows[0] and rows[0][0] is not None else None
```

### Recommended Codex action plan

In priority order:

1. **Fix poll timeout**: set `databricks_genie_poll_timeout_seconds = 300` in `settings.py`. Rebuild Docker.

2. **Fix `resolve_pdf_scan_direct` column selection** (see code above) — currently reads wrong column for wide-format SQL.

3. **Tune batch size**: set default `batch_size=15` in `GenieResolveRequest` schema and service. This keeps Genie under 120s even with timeout=120 for most sections.

4. **Add value validation**: after `resolve_pdf_scan_via_genie`, run `resolve_pdf_scan_direct` for all GENIE_RESOLVED candidates. If direct-SQL value differs from `genie_value` by >0 for numeric fields, mark `status=NEEDS_REGENIE` and log the discrepancy.

5. **Single-field retry for NEEDS_REGENIE**: add a `resolve_pdf_scan_single_field_retry` method that iterates NEEDS_REGENIE candidates one-at-a-time with a focused single-field prompt (no batching).

6. **Scale up**: once the above 5 are working, run `batch_size=15` across all 1086 fields. Expected: ~73 Genie calls × ~2min each = ~2.5 hours total. Can parallelize with `ThreadPoolExecutor(max_workers=3)` across section groups.

### Current DB state (2026-06-02)

Scan `pdfscan_bb676d3416e3`:
- `GENIE_RESOLVED`: 154 fields (values stored, SQL template stored — but some values may be wrong for B1 enrollment)
- `GENIE_LOW_CONFIDENCE`: 744 fields (empty value, conf=0 — Genie couldn't find these in Databricks)
- `DISCOVERED`: 188 fields (not yet attempted — were skipped due to batch failures)

To reset and start fresh:
```sql
UPDATE survey_pdf_datapoint_candidates
SET status='DISCOVERED', genie_sql_template='', genie_value='',
    genie_confidence=0, genie_reason='', genie_resolved_at=NULL, direct_sql_failures=0
WHERE scan_id='pdfscan_bb676d3416e3';
```

---

## What Changed Since The Previous Handoff (2026-06-01, session 2)

### Genie-first value resolution pipeline

Replaced the legacy master_data_points mapping approach with a direct Genie-resolution pipeline. Genie now returns SQL + values for each survey field; that SQL is stored for direct re-use in subsequent survey years without re-calling Genie.

#### DB changes — `survey_pdf_datapoint_candidates`

Nine new columns added (migrations fire at container startup via `on_startup`):

| Column | Type | Purpose |
|--------|------|---------|
| `genie_sql_template` | TEXT | UNION ALL SQL with `__SURVEY_YEAR__` placeholder |
| `genie_table` | TEXT | Primary table Genie used |
| `genie_column` | TEXT | Value column Genie used |
| `genie_year_column` | TEXT | Year filter column |
| `genie_value` | TEXT | Cached value for the resolved year |
| `genie_confidence` | INTEGER | 0-100 confidence from resolution |
| `genie_reason` | TEXT | Short explanation |
| `genie_resolved_at` | DATETIME | Timestamp of last Genie resolution |
| `direct_sql_failures` | INTEGER | Count of failed direct-SQL re-runs (≥3 → NEEDS_REGENIE) |

Migration applied to existing DB: all 9 columns exist in `data/control_plane.db`.

#### `master_data_points` rows deleted

All 123 rows in `master_data_points` were deleted. The table remains but is no longer in the critical path.

#### New utilities — `databricks_genie_client.py`

- `normalize_year(sql, genie_year)` — replaces hardcoded year in Genie-returned SQL with `__SURVEY_YEAR__` using word-boundary regex.
- `apply_year(template_sql, year)` — substitutes `__SURVEY_YEAR__` for direct execution.
- `GenieResolution` dataclass — `candidate_id, sql_template, table, column, year_column, value, confidence, reason`.
- `resolve_many_candidates(candidates, survey_year)` — new method:
  - Sends one Genie conversation per section-batch (up to 50 fields/call).
  - Prompt format: `field_name='X': {label} — {datapoint_intent}. Context: "{context (cleaned)}"`.
  - Parses `attachments[].query.query` for SQL, calls `/query-result` endpoint for row values.
  - Normalizes year in returned SQL before storing.
  - `_build_resolution_prompt`, `_extract_statement_id`, `_extract_sql_from_message`, `_fetch_query_result` added as helpers.

#### New service methods — `service.py`

- `resolve_pdf_scan_via_genie(scan_id, survey_year, batch_size=50, min_confidence=60, force_regenie=False)`:
  - Groups enriched candidates by section (extracted from `nearby_text` using `re.search`).
  - Batches by section (max `batch_size` per Genie call).
  - Stores `genie_sql_template`, `genie_value`, `genie_confidence`, etc. on each candidate row.
  - Sets `status = "GENIE_RESOLVED"` (confidence ≥ min) or `"GENIE_LOW_CONFIDENCE"`.
- `resolve_pdf_scan_direct(scan_id, survey_year)`:
  - Re-runs `apply_year(genie_sql_template, year)` against Databricks SQL warehouse for all already-resolved candidates.
  - Increments `direct_sql_failures` on null/error; sets `"NEEDS_REGENIE"` at ≥3 failures.
- `list_resolved_values(scan_id)` — returns all candidates with a non-empty `genie_sql_template`.
- `_extract_section_from_nearby_text(nearby_text)` — module-level helper using `re.search` (not `re.match`; section is mid-string in the format `{label} | Section: X | Original nearby text: ...`).

#### New schemas — `schemas.py`

- `GenieResolveRequest` / `GenieResolutionResult`
- `DirectResolveRequest` / `DirectResolutionResult`
- `ResolvedValueResponse`

#### New API endpoints — `main.py`

```
POST /pdf-scans/{scan_id}/resolve-via-genie
     body: { survey_year: int, batch_size?: int, min_confidence?: int, force_regenie?: bool }
     → GenieResolutionResult { scan_id, survey_year, resolved, low_confidence, failed, skipped }

POST /pdf-scans/{scan_id}/resolve-direct
     body: { survey_year: int }
     → DirectResolutionResult { scan_id, survey_year, refreshed, null_results, sql_errors, needs_regenie }

GET  /pdf-scans/{scan_id}/resolved-values
     → list[ResolvedValueResponse]
```

#### `/pdf-ops` UI additions

New "Resolve via Genie" section in the left panel (below Mapping):
- Survey year, batch size, min confidence inputs + "Force re-resolve" checkbox.
- **Resolve via Genie** button → `POST /resolve-via-genie`.
- **Refresh Direct SQL** button → `POST /resolve-direct`.
- **Load Resolved Values** button → `GET /resolved-values` → fills a table showing field_name, label, value, confidence (badge-colored by status), SQL (truncated with full SQL on hover).

#### Skyvern fill now uses Genie-resolved PDF values

Implemented after this handoff section was originally written:

- `DispatchFillRequest` and `ExecuteDraftFillPipelineRequest` now accept optional `scan_id`.
- When `scan_id` is present, `Slice1Service.dispatch_section_fill_activity` builds fill tasks directly from `survey_pdf_datapoint_candidates` rows where `status = "GENIE_RESOLVED"` and `genie_value` is non-empty.
- PDF fill uses candidate IDs as stable task field IDs and candidate labels as Skyvern label hints; it does not require `master_data_points` or `survey_field_catalog`.
- Fill task `request_json` stores `scan_id`, so completed PDF fill tasks dispatch post-fill validation from the same Genie-resolved PDF values instead of trying to bootstrap a static section catalog.
- `/pdf-ops` now stays PDF-only and no longer exposes the Skyvern portal-fill action; use **Export Filled PDF** for the scan-to-fill workflow.
- Unit coverage added for PDF Genie fill dispatch, PDF post-fill validation dispatch, and the `/pdf-ops` page action.

#### Settings changes — `settings.py`

- `databricks_genie_poll_timeout_seconds`: 45 → **120** (Genie start-conversation alone takes ~48s).
- `databricks_genie_request_timeout_seconds`: 30 → **60**.

#### Known Genie behavior

- `start-conversation` HTTP call takes ~48s (OAuth token exchange + cold start) on every new conversation.
- Each full resolution (start + poll to COMPLETED) takes 80–120s.
- Section grouping keeps same-table fields together (enrollment in one batch, graduation-rate in another) to avoid Genie searching across unrelated tables.
- `nearby_text` format: `"{label} | Section: {section_name} | Original nearby text: {raw_text}"` — section extraction uses `re.search(r"Section:\s*(.+?)(?:\s*\||\n|$)", ...)`.
- Context sent to Genie strips the `"| Original nearby text: ..."` tail (scanner noise) via regex before building the prompt.
- Genie ignores `:survey_year` parameter syntax — always hardcodes the year. We post-process with `normalize_year()`.
- GENDER values in `ira_campus_enrl_census_aggr`: `Male`, `Female`, `Nonbinary` (not `Man`/`Woman`).

#### Smoke test status

At time of handoff, the pipeline code is complete and tested (24/24 unit tests pass). Live Genie calls in Docker were attempted but killed by container restarts during the session. The pipeline is ready for a fresh end-to-end smoke test:

```bash
# 1. Start small (5 enriched fields)
curl -X POST http://localhost:8010/pdf-scans/pdfscan_bb676d3416e3/resolve-via-genie \
  -H "Content-Type: application/json" \
  -d '{"survey_year": 2024, "batch_size": 5}' \
  --max-time 300

# 2. Check what was resolved
curl http://localhost:8010/pdf-scans/pdfscan_bb676d3416e3/resolved-values | python3 -c "
import sys, json
rows = json.load(sys.stdin)
for r in rows[:10]:
    print(r['field_name'], '|', r['genie_value'], '| conf:', r['genie_confidence'])
"

# 3. Scale up (all 1086 fields across sections — will take ~10-20 min)
curl -X POST http://localhost:8010/pdf-scans/pdfscan_bb676d3416e3/resolve-via-genie \
  -H "Content-Type: application/json" \
  -d '{"survey_year": 2024, "batch_size": 50}' \
  --max-time 1800

# 4. Next year: re-run direct SQL only (no Genie)
curl -X POST http://localhost:8010/pdf-scans/pdfscan_bb676d3416e3/resolve-direct \
  -H "Content-Type: application/json" \
  -d '{"survey_year": 2025}'
```

#### Next steps (not yet implemented)

1. **Handle `NEEDS_REGENIE` automatically**: after `resolve-direct`, if any candidates have `status=NEEDS_REGENIE`, trigger a targeted re-Genie for just those fields.
2. **Genie space curations**: add explicit enum hints to the Genie space (GENDER = Male/Female/Nonbinary, etc.) to improve null rate for enrollment fields.
3. **Concurrent batch execution**: currently `resolve_many_candidates` fires one Genie conversation at a time within each section group. Could parallelize across sections using `ThreadPoolExecutor` for faster full-scan resolution.
4. **Retry nulls as single-field calls**: fields where Genie returns null in the UNION ALL (different table than section peers) need a single-field fallback call.

---

## What Changed Since The Previous Handoff (2026-06-01)

### PDF scan path migrated to OpenAI-first enrichment

The PDF scan flow now treats OpenAI PDF enrichment as the primary scan logic. The local scanner still extracts technical candidate metadata (field IDs, page/rect, input kinds), but label meaning comes from OpenAI PDF analysis.

- [`apps/api/service.py`](apps/api/service.py):
  - `scan_pdf` now uses OpenAI scan enrichment path for labels.
  - scan enrichment provider errors are surfaced as `provider: openai` in `LABEL_ENRICHMENT_FAILED` responses.
  - enriched labels are persisted with `label_source = openai_enriched`.
  - explicit fallback behavior is preserved through `allow_enrichment_fallback`.
- [`apps/api/openai_pdf_label_enrichment.py`](apps/api/openai_pdf_label_enrichment.py):
  - sends full PDF (`input_file`) plus candidate metadata to OpenAI Responses API.
  - returns explicit failures (HTTP/read/parse) instead of silently returning empty mappings.
  - captures `last_response_summary` for improved diagnostics.
  - sets `max_output_tokens` dynamically by candidate count to reduce truncation on larger scans.
  - tightened prompt contract to request one output row per candidate key.
- [`tests/test_pdf_mapping.py`](tests/test_pdf_mapping.py):
  - updated scan-path assertions from Databricks-labeled enrichment to OpenAI-labeled enrichment.
  - OpenAI whole-PDF payload tests remain active.

### Timeout and config fixes for real PDF scans

Root cause for browser failures after enabling OpenAI was request read timeout, not missing configuration.

- [`apps/api/settings.py`](apps/api/settings.py): `pdf_openai_label_enrichment_timeout_seconds` default increased from `45` to `180`.
- [`.env`](.env): `PDF_OPENAI_LABEL_ENRICHMENT_TIMEOUT_SECONDS=180` added for runtime.
- [`.env.example`](.env.example): timeout default updated to `180`.

Operational behavior validated:

- strict scan with OpenAI and low candidate limit completes and persists enriched labels.
- strict scan with higher candidate limit no longer fails on timeout path (latency remains model/load dependent).

### Environment scope clarification

For this stack, the API container (`control-plane`) reads backend env from:

- [`infra/docker-compose.yml`](infra/docker-compose.yml): `env_file: ../.env`

The frontend env file is **not** used by `control-plane`:

- `skyvern-frontend/.env` only affects the frontend service when that service is used.

So OpenAI scan settings must be set in `survey-automation/.env`, not only in `survey-automation/skyvern-frontend/.env`.

---

## What Changed Since The Previous Handoff (2026-05-26)

### PDF survey datapoint scanning: first slice added

Added the first testable slice for extracting datapoints from uploaded survey PDFs and storing them for analyst mapping:

- [`apps/api/pdf_scanner.py`](apps/api/pdf_scanner.py): scans PDFs with `pypdf`. It checks fillable AcroForm fields first, using those as high-confidence candidates. If no fillable fields exist, it falls back to text extraction and stores likely datapoint labels as lower-confidence candidates.
- [`apps/api/db/models.py`](apps/api/db/models.py): added three control-plane tables:
  - `survey_pdf_scans` — one row per scanned PDF, including SHA-256, fillable flag, page count, candidate count, and raw result JSON.
  - `survey_pdf_datapoint_candidates` — extracted fields/labels from the PDF, with source, normalized label, input kind, confidence, and optional mapping.
  - `master_data_points` — reusable canonical datapoint list with Databricks binding metadata (`databricks_view`, `databricks_value_column`, `databricks_year_column`, `transform_json`).
- [`apps/api/main.py`](apps/api/main.py): added API endpoints:
  - `POST /pdf-scans`
  - `GET /pdf-scans`
  - `GET /pdf-scans/{scan_id}`
  - `GET /pdf-scans/{scan_id}/candidates`
  - `GET /master-data-points`
  - `POST /master-data-points`
  - `PATCH /master-data-points/{data_point_id}/binding`
  - `POST /pdf-candidates/{candidate_id}/map`
- [`infra/scripts/scan_pdf_datapoints.py`](infra/scripts/scan_pdf_datapoints.py): CLI scanner for fast local testing without Docker/API.
- [`infra/docker-compose.yml`](infra/docker-compose.yml): mounted `../uploads:/app/uploads` into `control-plane` so PDFs placed under `survey-automation/uploads/` can be scanned by the Docker API.
- [`../docs/15-pdf-survey-datapoint-scanning.md`](../docs/15-pdf-survey-datapoint-scanning.md): added system design and step-by-step test instructions.

This slice intentionally does **not** pull the mapped values from Databricks yet. It creates the persistence model needed for that next step: PDF candidate → master datapoint → Databricks binding.

### PDF field enrichment: widget location and visible labels

Added the next PDF scanner improvement so fillable forms no longer depend only on internal AcroForm names:

- [`apps/api/pdf_scanner.py`](apps/api/pdf_scanner.py):
  - walks page widget annotations to capture `page_number` and `/Rect` coordinates for each field.
  - extracts nearby visible text around the widget using `pypdf` text visitors.
  - chooses labels by priority: PDF tooltip → nearby visible text → humanized field name.
  - stores label provenance as `label_source` (`tooltip`, `nearby_text`, or `field_name`).
- [`apps/api/db/models.py`](apps/api/db/models.py): added persisted candidate metadata columns:
  - `label_source`
  - `field_rect_json`
  - `nearby_text`
- [`apps/api/main.py`](apps/api/main.py): startup migration adds those columns to existing SQLite DBs.
- [`apps/api/schemas.py`](apps/api/schemas.py): API responses now include the enrichment metadata.

Local enriched rescan of `uploads/survey.pdf` created:

```text
scan_id: pdfscan_e0114704ce41
candidate_count: 1086
page_count: 50
```

Example improvement:

```text
TUIT_VARY_PROG_P
old label: Tuit Vary Prog P
new label: If yes, what percentage of full-time undergraduates pay more than the tuition and fees reported in G1?
label_source: nearby_text
page_number: 30
```

Next recommended slice: candidate-to-master suggestions, then Databricks value resolution for mapped master datapoints.

### PDF mapping/resolution: suggestions and mapped value pull

Added the next backend slice for turning scanned PDF fields into Databricks-resolvable datapoints:

- [`apps/api/db/models.py`](apps/api/db/models.py): added `master_data_point_aliases`.
- [`apps/api/service.py`](apps/api/service.py):
  - `add_master_alias`
  - `list_master_aliases`
  - `suggest_candidate_mappings`
  - `resolve_mapped_pdf_scan`
- [`apps/api/main.py`](apps/api/main.py): added endpoints:
  - `POST /master-data-points/{data_point_id}/aliases`
  - `GET /master-data-points/{data_point_id}/aliases`
  - `GET /pdf-scans/{scan_id}/mapping-suggestions`
  - `POST /pdf-scans/{scan_id}/resolve-values`
  - `POST /pdf-scans/{scan_id}/publish-field-catalog`
- [`apps/api/schemas.py`](apps/api/schemas.py): added response/request schemas for aliases, mapping suggestions, and resolved mapped values.

How it works:

1. PDF scan creates candidates from fillable fields or flat PDF text.
2. Analyst creates or reuses a `master_data_points` row for the canonical meaning.
3. Analyst adds aliases for alternate wording from other survey PDFs.
4. `mapping-suggestions` scores each candidate against canonical names, semantic keys, and aliases.
5. Analyst maps a candidate to the selected master datapoint.
6. `resolve-values` pulls values through the existing Databricks resolver path.
7. `publish-field-catalog` creates/updates `survey_field_catalog` rows from mapped candidates, copying the master datapoint's Databricks binding into the field catalog row Skyvern uses.

Local tests use `literal:` bindings, for example `databricks_value_column = literal:https://www.csulb.edu`. Real bindings should point at Databricks views and columns.

Debug note from 2026-05-26: an earlier local-only smoke scan ID (`pdfscan_e0114704ce41`) was not present in the running Docker API DB. The running API DB only had `pdfscan_4bb1d3a5221e`. Added `GET /pdf-scans` so operators can list valid scan IDs before using mapping/resolution endpoints. A new enriched scan was created through the API as `pdfscan_dc75575744ab`.

### `/pdf-ops`: analyst UI for PDF mapping

Added [`/pdf-ops`](http://localhost:8010/pdf-ops) in [`apps/api/main.py`](apps/api/main.py). It is a single-page operator UI for the PDF datapoint workflow:

- scan a PDF mounted under `/app/uploads`
- list existing scans
- inspect enriched candidates with page/label-source metadata
- filter candidates by mapped/unmapped status
- load mapping suggestions
- create master datapoints and aliases
- map candidates to master datapoints
- resolve mapped values
- publish mapped candidates to `survey_field_catalog`

This page is intentionally a thin UI over the tested JSON APIs. It does not introduce a separate frontend build system.

### Headless mode: two root-cause CDP failures fixed

The system was failing every Skyvern run with `BrowserType.connect_over_cdp: connect ECONNREFUSED 192.168.65.254:9222` even after switching away from CDP mode. Two separate issues were found and fixed:

**Fix 1 — `.env` typo:** `BROWSER_TYPE` was set to `hromium-headful` (missing leading `c`). Corrected to `chromium-headful`. `BROWSER_REMOTE_DEBUGGING_URL` was also left uncommented, which caused Skyvern's config to fall back to the CDP address. Commented it out.

```bash
# BEFORE (broken):
BROWSER_TYPE=hromium-headful
BROWSER_REMOTE_DEBUGGING_URL=http://192.168.65.254:9222/

# AFTER (fixed):
BROWSER_TYPE=chromium-headful
# BROWSER_REMOTE_DEBUGGING_URL=http://192.168.65.254:9222/
```

**Fix 2 — hardcoded `browser_address` in task payload** (root cause): [`infra/scripts/run_full_fake_form_fill.py`](infra/scripts/run_full_fake_form_fill.py) had `"browser_address": "http://192.168.65.254:9222/"` hardcoded in the Skyvern task request body. Skyvern's `browser_address` task field overrides `BROWSER_TYPE` env config entirely — the `.env` setting is irrelevant if the task body sets this. Confirmed via Postgres query:

```sql
SELECT task_id, browser_address FROM tasks ORDER BY created_at DESC LIMIT 3;
-- All recent tasks showed: browser_address = http://192.168.65.254:9222/
```

Removed the `"browser_address"` key from `run_body` in `run_full_fake_form_fill.py`. Skyvern now uses whatever `BROWSER_TYPE` is configured in `.env`.

Rebuilt control-plane after both fixes: `docker compose -f infra/docker-compose.yml up --build -d control-plane`.

### `/ops` UI: complete non-technical redesign

[`apps/api/main.py`](apps/api/main.py) — replaced the previous basic form+JSON-dump interface with a clean, non-technical single-click workflow UI at `http://localhost:8010/ops`:

- **Single "Run Survey Automation" button** — no terminal commands needed
- **"Check data consistency before filling" checkbox** — plain-language replace of `--validate` flag
- **Advanced options toggle** — hides Form URL, Survey Year, Timeout behind a disclosure. Non-technical users never see them. Default URL set to `http://fake-form/?realData=1` (Docker internal, works in headless mode without Edge).
- **Live 2-step progress stepper** — "Pull data from Databricks" → "Fill survey form". Each step shows an animated ring while running, ✓ on success, ✕ on failure.
- **Dark terminal-style live log panel** — auto-scrolls to bottom, polls every 3s via `setInterval`.
- **Plain-language error mapping** (`friendlyError()` JS function):
  - `FileNotFoundError` → "Data file not found..."
  - `SKYVERN_API_KEY` → "Skyvern API key is missing..."
  - `9222` → "Cannot reach the browser..."
  - `Databricks` → "Could not connect to Databricks..."
  - `max steps` / `maximum of` → "Skyvern hit its step limit..."
  - fallback: last 2 lines of raw error
- **Run History table** — colored status badges, elapsed time, "view log" links. Persists across restarts via SQLite `workflow_jobs` table.

---

## What Changed Since The Previous Handoff (2026-05-20)

### Performance analysis and Skyvern internals investigation

Deep-dived into what actually drives runtime cost, using real Skyvern Postgres data and LLM request artifacts from the May 19 run:

- **Planner vs executor architecture confirmed.** Skyvern uses two LLM layers:
  - *Planner* (`observer_thoughts`): 1 LLM call per section (~12s each). Plans what to do. 18 total calls for the full form.
  - *Executor* (inside each `navigate` task): 1 LLM call per step, multiple actions per step. Plans and executes all fields for a section in bulk — not 1 LLM call per field.
- **Real timing from Postgres `actions` table**: each `input_text` action takes ~8s. This is Skyvern's fixed overhead per action: DOM re-scrape + wait-for-network-idle + next screenshot. It is **not** Playwright typing speed and is **not** caused by `validateFinalSubmitState` JS.
- **`cached_token_count = 0` on all steps.** Databricks Model Serving strips Anthropic `cache_control` fields before forwarding to Claude — prompt caching does not work through the Databricks proxy. This is a Databricks platform limitation, not a code bug.
- **CDP adds ~1-3s per action** (network hop: Skyvern container → Docker host → Mac → Edge). Headless mode eliminates this.

### Skyvern client: `browser_session_id` + retry

[`apps/skyvern_worker/skyvern_client.py`](apps/skyvern_worker/skyvern_client.py):

- Added `browser_session_id: str | None = None` to all three `create_*_workflow` methods. When set, Skyvern reuses an existing authenticated browser session — no re-login between section tasks. Previously every task started a cold browser.
- Added `_request_with_retry`: retries on HTTP 429 and 5xx with exponential backoff (1s, 2s, 4s). Previously any transient Skyvern error crashed the dispatch.

### Task prompts: COMPLETE / TERMINATE criteria

[`apps/skyvern_worker/task_builder.py`](apps/skyvern_worker/task_builder.py):

- All three prompt builders (`build_fill_task`, `build_validate_task`, `build_scan_fields_task`) now include explicit `COMPLETE when...` and `TERMINATE immediately if...` instructions. Skyvern's planner uses these as hard stop signals — it stops as soon as the condition is met instead of doing an extra verification loop. Saves one full planner cycle per section on average.
- Prompts now quote field labels and include explicit field counts to reduce planner ambiguity.
- Section titles use `section_id.replace("_", " ").title()` for cleaner portal navigation hints.

### Service layer: `browser_session_id` wired through all dispatch paths

[`apps/api/service.py`](apps/api/service.py):

- `browser_session_id` parameter added to all four dispatch methods: `dispatch_section_fill_activity`, `dispatch_section_validate_activity`, `dispatch_scan_fields_activity`, `execute_section_pipeline`, `execute_draft_fill_pipeline`.
- Stored in `request_json` on each `SkyvernTask` row so `_dispatch_post_fill_validate_if_ready` can recover it without re-passing from outside.
- Added `_extract_browser_session_id_from_request` helper.

### Temporal layer: `browser_session_id` end-to-end

Four files updated to pass `browser_session_id` from API to Temporal to activities to service:

- [`apps/temporal_worker/types.py`](apps/temporal_worker/types.py): added `browser_session_id: str | None = None` to `ExecuteSectionPipelineInput` and `ExecuteDraftFillPipelineInput`.
- [`apps/temporal_worker/activities.py`](apps/temporal_worker/activities.py): passes `browser_session_id` from request to service calls.
- [`apps/temporal_worker/workflows.py`](apps/temporal_worker/workflows.py): `RunWorkflow.run` accepts `browser_session_id` and **auto-derives** `sess_{run_id}_{section_id}` if none is passed — so every section task in a run automatically gets a stable, unique session ID without the caller having to think about it.
- [`apps/temporal_worker/signaler.py`](apps/temporal_worker/signaler.py): passes `browser_session_id` as the 6th positional arg to `RunWorkflow.run`.
- [`apps/api/schemas.py`](apps/api/schemas.py): `StartWorkflowRequest` gains `browser_session_id: str | None = None`.
- [`apps/api/main.py`](apps/api/main.py): `start_temporal_workflow` route passes it through and logs it in `TEMPORAL_WORKFLOW_STARTED` event.

### `/ops` job state persisted to SQLite

Previously: `_WORKFLOW_JOBS` was an in-process Python dict — wiped on every restart.

Now:
- [`apps/api/db/models.py`](apps/api/db/models.py): added `WorkflowJob` SQLAlchemy model (`workflow_jobs` table) with columns: `job_id`, `status`, `request_json`, `steps_json`, `result_json`, `error`, `created_at`, `started_at`, `finished_at`.
- [`apps/api/main.py`](apps/api/main.py): replaced all `_WORKFLOW_JOBS` / `_WORKFLOW_JOBS_LOCK` usage with `_job_update()` and `_job_append_step()` helpers that write directly to SQLite. The three `/ops/full-workflow/jobs` routes now read from the DB.
- Job history survives restarts. All previous jobs remain visible in `/ops` after `docker compose restart`.

### `databricks_openai_proxy.py`: prompt caching injection attempt

[`infra/scripts/databricks_openai_proxy.py`](infra/scripts/databricks_openai_proxy.py):

- Added `_inject_prompt_caching()` method: injects `"cache_control": {"type": "ephemeral"}` onto the last system block and last user content block before forwarding POST requests to Databricks.
- **Status: does not work.** Databricks Model Serving strips `cache_control` fields before forwarding to Claude — `cached_token_count` remains 0 on all steps. Code left in place for when Databricks adds support, but has no effect currently.

### `fake-survey-form/app.js`: validation debounce

[`fake-survey-form/app.js`](../../fake-survey-form/app.js):

- `validateFinalSubmitState()` moved off the hot `input` event path into an 800ms debounce (`scheduleValidation`). Previously `buildAssessmentChecks()` ran on every keypress, triggering DOM queries on every character typed.
- **Practical impact: none on Skyvern runs.** The bottleneck is Skyvern's wait-for-network-idle after each action, not JS execution time. Debounce improves browser UX for human users.

### `run_full_fake_form_fill.py`: `--browser-session-id` flag

[`infra/scripts/run_full_fake_form_fill.py`](infra/scripts/run_full_fake_form_fill.py):

- Added `--browser-session-id` CLI argument. When passed, sets `browser_session_id` in the Skyvern run body so an already-authenticated session is reused.

### Docker Compose: `artifacts/` mount added

[`infra/docker-compose.yml`](infra/docker-compose.yml):

- Added `../artifacts:/app/artifacts` volume mount to `control-plane`. Without this, `pull_real_fake_form_data.py` failed with `FileNotFoundError` when writing the artifact JSON inside the container.

### `OPERATIONS.md` added

[`OPERATIONS.md`](OPERATIONS.md): new file covering complete setup, all three run modes (web UI, terminal scripts, Temporal API), browser mode switching (CDP vs headless), performance reference, useful URLs, diagnostics, common errors, and repo layout. Intended for anyone running the system for the first time.

### Tests updated

[`tests/test_service_pipeline.py`](tests/test_service_pipeline.py) and [`tests/test_temporal_signaler.py`](tests/test_temporal_signaler.py):
- `FakeSkyvernClient` methods updated to accept `browser_session_id` keyword argument.
- Temporal signaler args sequence test updated to include `None` as 6th positional arg.
- Prompt assertion updated to match new `task_builder.py` wording (`"stop without saving"` replaces `"stop and leave the page unsubmitted"`).
- All 9 tests pass.

---

## What Changed Since The Previous Handoff (2026-05-19 — preserved below)

This section summarizes the work completed after the earlier Slice 0-1 handoff.

### Databricks source discovery and real-data pull

- Confirmed access to key Databricks sources.
- Verified `production.silver.serss` was not the final table name for the current pull path; the working pull uses `production.silver.erss`.
- Checked/recorded additional useful sources:
  - `production.gold.ira_campus_enrl_census_aggr`
  - `production.reference.ira_ss_hegis_cip`
  - `production.silver.ira_faculty`
  - CMS financial aid tables under `bronze.cms.*`
- Added [`infra/scripts/pull_real_fake_form_data.py`](infra/scripts/pull_real_fake_form_data.py).
- Added [`infra/scripts/validate_fake_form_data.py`](infra/scripts/validate_fake_form_data.py).
- Implemented real admissions/enrollment pull into `fake-survey-form-data.json`.
- Added term-aware gender logic:
  - before `20254`: use `SEX_CODE`
  - `20254+`: prefer `GENDER_IDENTITY_CODE`, fallback to `SEX_CODE`
- Added enrollment-status logic for first-time freshmen, other first-year, all other undergraduates, and graduate buckets.
- Added derived totals and consistency checks for admissions, enrollment, undergraduate, graduate, and grand total values.

### Fake form relocation and Docker wiring

- Fake form folder was moved under:

  `survey-automation-project/fake-survey-form`

- Updated compose mounts so `fake-form` serves that folder on `http://localhost:8088`.
- Mounted the fake-form folder into `control-plane` so the `/ops` workflow can update `fake-survey-form-data.json` from inside Docker.
- Updated `control-plane.Dockerfile` to copy `infra/` into the image so backend jobs can run workflow scripts.

### Skyvern + local Edge CDP

- Configured Skyvern for CDP browser mode:
  - `BROWSER_TYPE=cdp-connect`
  - `BROWSER_REMOTE_DEBUGGING_URL=http://192.168.65.254:9222/`
- Changed Skyvern container debug port mapping to `9223:9222` so host `9222` remains available for local Edge CDP.
- Verified local Microsoft Edge can be controlled by Skyvern through Docker Desktop host networking.
- Captured the working Edge launch command and CDP verification commands in this handoff.
- Confirmed `host.docker.internal:9222` can fail because Edge rejects non-IP/non-localhost Host headers; `192.168.65.254` worked in this environment.

### Full fake-form Skyvern run

- Added [`infra/scripts/run_full_fake_form_fill.py`](infra/scripts/run_full_fake_form_fill.py).
- The script loads the generated JSON and sends one large `skyvern-2.0` task using CDP to local Edge.
- Completed a full end-to-end run:
  - Started: 2026-05-19 12:55:54 PM
  - Finished: 2026-05-19 1:23:28 PM
  - Duration: about 27m 34s
- Diagnosed runtime cost:
  - dominated by Skyvern's agent loop
  - not dominated by Databricks SQL
  - large prompt/full mapping increases per-step latency
  - section navigation and repeated DOM/screenshot/LLM cycles add overhead

### Non-technical workflow UI

- Added `/ops` UI in [`apps/api/main.py`](apps/api/main.py).
- Added in-memory background job execution for the full fake-form workflow:
  - pull Databricks data
  - validate JSON
  - run Skyvern full fill
  - expose job detail and parsed script outputs
- Added job API endpoints:
  - `POST /ops/full-workflow/jobs`
  - `GET /ops/full-workflow/jobs`
  - `GET /ops/full-workflow/jobs/{job_id}`
- Updated README with the UI entry point.

### Architecture clarification

- Documented the difference between the two current paths:
  - full fake-form workflow: hardcoded survey metrics and one large Skyvern task
  - dynamic real-form workflow: scan/approve catalog, resolve values, fill/validate by section
- Clarified that aggregate business logic currently lives in `pull_real_fake_form_data.py`, while `DatabricksFieldResolver` only supports simple one-column lookups.
- Identified next implementation direction: move aggregate logic into catalog-driven transforms or named resolver functions.

---

## Repository Anchor

| Path | Role |
|------|------|
| `survey-automation-project/survey-automation/` | Runnable FastAPI/control-plane/Skyvern/Temporal scaffold |
| `survey-automation-project/fake-survey-form/` | Fake survey form served by nginx on `:8088` |
| `survey-automation-project/docs/` | Architecture notes, ADRs, iteration plan |

Local workspace:

`/Users/harshas/code/csulb/IRA/survey-agent/survey-automation-project/survey-automation`

---

## Current Working Flow

There are two workflow paths in the repo.

### 1. Full fake-form workflow

This is the currently working end-to-end demo path.

1. UI/API starts a job from `GET /ops`.
2. Control-plane runs [`infra/scripts/pull_real_fake_form_data.py`](infra/scripts/pull_real_fake_form_data.py).
3. That script queries Databricks and writes `fake-survey-form-data.json`.
4. Control-plane then runs [`infra/scripts/run_full_fake_form_fill.py`](infra/scripts/run_full_fake_form_fill.py).
5. The fill script sends one full-form Skyvern task to `/v1/run/tasks`.
6. Skyvern connects to local Microsoft Edge through CDP and fills the fake form.

Important: this path is **hardcoded to the fake form payload shape**. It is useful for proving the data pull + browser fill path, but it is not yet the final dynamic real-form engine.

### 2. Dynamic section/catalog workflow

This is the intended real-form architecture, partially implemented.

1. Skyvern scans a section and creates `field_discovery_drafts`.
2. Analyst approves/rejects discovered fields into `survey_field_catalog`.
3. `DatabricksFieldResolver` resolves values for approved catalog rows.
4. Skyvern gets smaller section/chunk fill tasks.
5. Post-fill validate creates review items for mismatches.

Current limitation: only the `institution` section has meaningful default catalog fields in [`apps/skyvern_worker/task_builder.py`](apps/skyvern_worker/task_builder.py). The resolver supports simple `SELECT column FROM view WHERE year = :survey_year LIMIT 1`; complex aggregate metrics are still implemented in the dedicated fake-form pull script.

---

## What Is Implemented

### Infrastructure

- Docker Compose stack in [`infra/docker-compose.yml`](infra/docker-compose.yml):
  - `control-plane` on `:8010`
  - `temporal` on `:7233`
  - `temporal-ui` on `:8233`
  - `skyvern` on `:8000`
  - `skyvern-ui` on `:8080` / `:9090`
  - `fake-form` on `:8088`
  - `dbx-openai-proxy` for Databricks Model Serving OAuth refresh
  - `skyvern-postgres` for Skyvern internal DB
- Control-plane state remains SQLite at `./data/control_plane.db`.
- Skyvern uses Postgres because its migrations require Postgres-specific SQL.
- `control-plane.Dockerfile` now copies `infra/` into `/app/infra` so the UI can invoke scripts inside the container.
- Compose mounts `../../fake-survey-form:/app/fake-survey-form` into `control-plane` and `../../fake-survey-form:/usr/share/nginx/html:ro` into `fake-form`.

### Non-technical UI

Open:

`http://localhost:8010/ops`

Implemented in [`apps/api/main.py`](apps/api/main.py).

Endpoints:

- `GET /ops` - simple workflow console.
- `POST /ops/full-workflow/jobs` - starts background full fake-form workflow.
- `GET /ops/full-workflow/jobs` - lists jobs.
- `GET /ops/full-workflow/jobs/{job_id}` - job detail, step output, parsed script JSON.

The UI runs:

```bash
python infra/scripts/pull_real_fake_form_data.py --validate
python infra/scripts/run_full_fake_form_fill.py --url "http://localhost:8088/?realData=1"
```

### Databricks Pull

Implemented in [`infra/scripts/pull_real_fake_form_data.py`](infra/scripts/pull_real_fake_form_data.py).

Current real sources:

- Enrollment: `production.silver.erss`
- Admissions: `production.silver.ersa`

Useful sources discussed but not fully wired into the fake-form pull:

- `bronze.cms.ps_stdnt_aid_atrbt`
- `bronze.cms.ps_stdnt_awards`
- `bronze.cms.ps_stdnt_awd_per`
- `bronze.cms.ps_stdnt_awrd_actv`
- `bronze.cms.ps_stdnt_awrd_disb`
- `bronze.cms.ps_stdnt_fa_term`
- `production.gold.ira_campus_enrl_census_aggr`
- `production.reference.ira_ss_hegis_cip`
- `production.silver.ira_faculty`

Current pull logic:

- Uses latest Fall term unless `--year` is passed.
- Fall term is hardcoded as `TERM = '4'`.
- Gender bucket uses term-aware logic:
  - before `20254`: `SEX_CODE`
  - `20254+`: `GENDER_IDENTITY_CODE` first, then `SEX_CODE` fallback
- Enrollment categories:
  - `ftf`
  - `otherfy`
  - `allother`
  - `grad`
- Full-time logic:
  - undergraduate: `>= 12` term units
  - graduate/postbaccalaureate: `>= 9` term units
- Admissions:
  - applied: `production.silver.ersa`, `STUDENT_LEVEL_CODE = '1'`
  - admitted: same source, currently `ADMISSION_STATUS IN ('A', 'N')`

Known point to confirm with IRA: the admitted rule may need to be stricter than `A/N`.

### Fake-Form Fill

Implemented in [`infra/scripts/run_full_fake_form_fill.py`](infra/scripts/run_full_fake_form_fill.py).

It loads `fake-survey-form-data.json` and sends one large Skyvern task:

- `engine = skyvern-2.0`
- `run_with = agent`
- `browser_address = http://192.168.65.254:9222/`
- prompt contains full `field_name -> value` mapping

The fake form itself uses stable `name` attributes and `data-testid`s in [`../fake-survey-form/index.html`](../fake-survey-form/index.html). Skyvern fills by locating matching inputs/selects/textareas in the browser.

Observed result:

- Full run completed on 2026-05-19.
- Duration: about 27m 34s.
- Main runtime cost is Skyvern's agent loop, not Databricks.

### CDP Edge Setup

Skyvern is configured to connect to local Microsoft Edge through CDP.

Start Edge manually:

```bash
pkill -f "Microsoft Edge.app/Contents/MacOS/Microsoft Edge" || true
sleep 2
open -na "Microsoft Edge" --args \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --remote-allow-origins=\* \
  --user-data-dir=/Users/harshas/edge-cdp-profile \
  "http://localhost:8088/?realData=1"
```

Verify host CDP:

```bash
curl -s http://127.0.0.1:9222/json/version
```

Verify Skyvern container can reach Edge:

```bash
docker exec infra-skyvern-1 python - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://192.168.65.254:9222/json/version", timeout=5).status)
PY
```

Expected: `200`.

Note: `host.docker.internal:9222` can fail with Edge DevTools host-header restrictions. The working URL in this Docker Desktop setup is `http://192.168.65.254:9222/`.

Skyvern host debug port was changed to `9223:9222` so host port `9222` is reserved for Edge CDP.

---

## Key Source Files

| Area | Files |
|------|-------|
| FastAPI app, `/ops`, routes | [`apps/api/main.py`](apps/api/main.py) |
| Orchestration / persistence | [`apps/api/service.py`](apps/api/service.py) |
| DB models | [`apps/api/db/models.py`](apps/api/db/models.py) |
| Settings | [`apps/api/settings.py`](apps/api/settings.py) |
| Databricks resolver | [`apps/api/databricks_resolver.py`](apps/api/databricks_resolver.py) |
| Skyvern prompts/chunking/catalog defaults | [`apps/skyvern_worker/task_builder.py`](apps/skyvern_worker/task_builder.py) |
| Skyvern HTTP client | [`apps/skyvern_worker/skyvern_client.py`](apps/skyvern_worker/skyvern_client.py) |
| Full fake-form Databricks pull | [`infra/scripts/pull_real_fake_form_data.py`](infra/scripts/pull_real_fake_form_data.py) |
| Website Skyvern fill | [`infra/scripts/run_website_form_fill.py`](infra/scripts/run_website_form_fill.py) |
| Full fake-form Skyvern fill compatibility wrapper | [`infra/scripts/run_full_fake_form_fill.py`](infra/scripts/run_full_fake_form_fill.py) |
| Fake-form validator | [`infra/scripts/validate_fake_form_data.py`](infra/scripts/validate_fake_form_data.py) |
| Temporal workflow | [`apps/temporal_worker/workflows.py`](apps/temporal_worker/workflows.py) |
| Temporal worker | [`apps/temporal_worker/worker.py`](apps/temporal_worker/worker.py) |
| Databricks OpenAI proxy | [`infra/scripts/databricks_openai_proxy.py`](infra/scripts/databricks_openai_proxy.py) |

---

## How To Run

From repo root:

```bash
cd /Users/harshas/code/csulb/IRA/survey-agent/survey-automation-project/survey-automation
docker compose -f infra/docker-compose.yml up --build -d
```

Start Edge CDP separately using the command in the CDP section.

Open:

```text
http://localhost:8010/website-ops
```

Manual script flow:

```bash
cd /Users/harshas/code/csulb/IRA/survey-agent/survey-automation-project/survey-automation
.venv/bin/python infra/scripts/pull_real_fake_form_data.py --validate
.venv/bin/python infra/scripts/run_website_form_fill.py \
  --url "http://localhost:8088/?realData=1" \
  --timeout-seconds 1800
```

---

## Configuration Notes

Required local files:

- `.env`
- `skyvern-frontend/.env`
- `.streamlit/secrets.toml`

Important `.env` values:

- `DATABRICKS_HOST`
- `DATABRICKS_AUTH_TYPE`
- `DATABRICKS_CLIENT_ID`
- `DATABRICKS_CLIENT_SECRET`
- `DATABRICKS_SQL_WAREHOUSE_ID`
- `SKYVERN_API_KEY`
- `BROWSER_TYPE=chromium-headful` (default — headless Chromium inside Skyvern container, no Edge needed)
- `BROWSER_REMOTE_DEBUGGING_URL` — leave commented out when using `chromium-headful`; set to `http://192.168.65.254:9222/` only when using `cdp-connect`
- `BROWSER_TIMEZONE=America/Los_Angeles`
- `BROWSER_LOCALE=en-US`
- `ALLOWED_HOSTS=["localhost","127.0.0.1","10.39.14.254","fake-form"]`

Inside Docker, compose overrides:

- `SKYVERN_BASE_URL=http://skyvern:8000`
- `CONTROL_PLANE_PUBLIC_BASE_URL=http://control-plane:8010`
- `TEMPORAL_TARGET_HOST=temporal:7233`
- `TEMPORAL_SIGNAL_ENABLED=true`

---

## Dynamic Catalog Path Details

The dynamic path is implemented but not complete for the whole survey.

Relevant endpoints:

- `POST /runs`
- `POST /runs/{run_id}/scan-fields`
- `GET /runs/{run_id}/field-discovery-drafts`
- `POST /field-discovery-drafts/{draft_id}/approve`
- `POST /field-discovery-drafts/{draft_id}/reject`
- `GET /field-catalog/{section_id}`
- `PATCH /field-catalog/{field_id}/binding`
- `POST /runs/{run_id}/prepare-section-payload`
- `POST /runs/{run_id}/dispatch-fill`
- `POST /runs/{run_id}/dispatch-validate`
- `POST /runs/{run_id}/start-workflow`
- `GET /review/{run_id}`

Current resolver behavior:

```sql
SELECT <databricks_value_column> AS value
FROM <databricks_view>
WHERE <databricks_year_column> = :survey_year
LIMIT 1
```

This works for simple one-row/one-column values. It does not yet support aggregate SQL templates, multi-table joins, pivots, or Python transforms. That is why enrollment/admissions are currently handled by `pull_real_fake_form_data.py`.

Recommended implementation direction:

1. Extend catalog rows to support `transform_json` with SQL templates or named resolver functions.
2. Move enrollment/admissions logic from `pull_real_fake_form_data.py` into reusable resolver functions.
3. Add all survey sections to `DEFAULT_SECTION_FIELDS` or seed them from scan/approve.
4. Run section/chunk Skyvern tasks instead of the current one large fake-form task.
5. ~~Add persistent job state for `/ops`~~ — **Done** (2026-05-20): `/ops` job state is now persisted to the `workflow_jobs` SQLite table and survives restarts.
6. Add section-parallel execution in Temporal — run independent sections concurrently with separate `browser_session_id` values for ~3x speed improvement.

---

## Runtime Performance Notes

The completed fake-form run took about 27m 34s because the full-fill path sends one large prompt and lets Skyvern iterate:

1. scrape DOM + screenshot
2. call LLM
3. execute action through Playwright/CDP
4. wait for page state
5. repeat until complete

Highest-impact speed improvements:

- Split by section.
- Send only relevant fields per section.
- Use stable selectors aggressively.
- Lower max steps per section.
- Avoid concurrent Skyvern runs on the same CDP browser.
- For production speed/cost, use deterministic Playwright for known fields and reserve Skyvern for discovery/repair/unknown UI paths.

---

## Debugging Notes

### CDP failure

If Skyvern reports:

`connect ECONNREFUSED 192.168.65.254:9222`

**Check two things before restarting Edge:**

1. Confirm `.env` has `BROWSER_TYPE=chromium-headful` (not `cdp-connect`) and `BROWSER_REMOTE_DEBUGGING_URL` is commented out.
2. Confirm `run_website_form_fill.py` does **not** contain `"browser_address"` in `run_body` — that field overrides `BROWSER_TYPE` regardless of `.env`.

If you genuinely want CDP mode, then restart Edge with the CDP command in OPERATIONS.md and recheck `curl http://127.0.0.1:9222/json/version`.

### Skyvern planning iterations

Tune:

- `SKYVERN_MAX_FIELDS_PER_TASK`
- `SKYVERN_TASK_COMPLEXITY_BUDGET_CHARS`
- `SKYVERN_VALIDATE_MAX_STEPS`
- `SKYVERN_SCAN_MAX_STEPS`
- `SKYVERN_FILL_MAX_STEPS`

### Temporal

Use `temporalio/temporal:latest` with:

```yaml
command: ["server", "start-dev", "--ip", "0.0.0.0"]
```

Do not revert to `temporalio/auto-setup` with only `server start-dev`; that previously loaded Cassandra config and failed.

### Old failed events

SQLite keeps old `TEMPORAL_SIGNAL_FAILED` and run events. Filter by `created_at` when debugging current behavior.

---

## Verification Commands

```bash
curl -s http://localhost:8010/health | jq .
curl -s http://localhost:8010/website-ops/full-workflow/jobs | jq .
curl -s http://localhost:8088 | rg "Survey Portal"
curl -s http://127.0.0.1:9222/json/version | jq .
docker compose -f infra/docker-compose.yml ps
```

Run tests:

```bash
cd /Users/harshas/code/csulb/IRA/survey-agent/survey-automation-project/survey-automation
.venv/bin/python -m py_compile apps/api/main.py
.venv/bin/pytest -q tests/test_databricks_resolver.py tests/test_service_pipeline.py tests/test_temporal_signaler.py
```

---

## Current Known Risks

- Website workflow uses `subprocess.run`, so one long job occupies one background thread until completion. Only one job can run at a time.
- Website fill uses one large Skyvern task. Next step is section-parallel execution.
- Dynamic resolver does not yet support aggregate transforms (`transform_json` is always `{}`).
- Only the `institution` section has default catalog fields in `task_builder.py`. Other sections must be seeded via scan/approve.
- `ADMISSION_STATUS IN ('A', 'N')` should be validated with IRA before production use.
- No authentication/authorization has been added to `/website-ops` or the `/ops` compatibility alias.
- Prompt caching does not work through Databricks Model Serving (strips `cache_control` headers). Every LLM step pays full token cost.
- CDP mode adds ~1-3s per Skyvern action due to network hop. The system now defaults to `BROWSER_TYPE=chromium-headful` (headless, faster, no Edge needed). Switch to `cdp-connect` only when you need to watch the browser live.
- **`browser_address` in task body overrides `BROWSER_TYPE` entirely.** If you add `browser_address` back to `run_website_form_fill.py`, it will always use CDP regardless of `.env`.

---

## Secrets

Never commit `.env`, `.streamlit/secrets.toml`, Databricks credentials, Skyvern API keys, or raw production output artifacts.
