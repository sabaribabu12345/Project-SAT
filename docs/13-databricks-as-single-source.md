# Databricks Is the Single Source of All Data

Supersedes the source-tag provenance model in `docs/12-usnews-main-survey-playbook-mapping.md`. Keep that doc for the *human workflow* context (how CSULB currently pulls data), but for our system the simplification is:

> **Every survey data point lives in Databricks.** There is exactly one data plane. The control plane never pulls from PeopleSoft, shared drives, Advancement spreadsheets, or HR files directly. If a value is needed for a survey, it must first land in a Databricks table/view that our prepare job reads.

This is the most important simplification in the whole platform. Everything else flows from it.

## Schema ownership: we never write to existing Databricks tables

The control plane and its supporting metadata live in a **new Unity Catalog schema** that this project owns end-to-end:

- `surveys_automation.*` — our schema. We create, alter, and seed every table and view here.
- `surveys.core.*`, `surveys.usnews_main.*`, and any other upstream schema — **read-only** to us. We do not ALTER them, add columns to them, or write rows into them. Upstream pipelines (CDS publisher, query pack, HR ingest, Advancement ingest, Registrar ingest) continue to own those schemas exactly as they do today.

If an existing upstream view is missing a column we need (for example a `survey_year` filter), the correct response is to **create a thin projection view** in `surveys_automation.*` that wraps the upstream view. We never push the requirement back upstream as a schema change.

This means the platform can be installed in a workspace without coordinating any DDL on production tables. Uninstalling it is `DROP SCHEMA surveys_automation CASCADE`.

## What disappears

The following tables / concepts from earlier docs are **removed**:

- `external_data_requests` — gone. Advancement / HR / Registrar data is ingested into Databricks by upstream pipelines. The control plane is unaware of email chains or shared-drive paths.
- `source_type` enum (`CDS | SQL_QUERY | EXTERNAL_DEPT | IR_ROLLOVER`) — gone. Every field has the same source: **a Databricks view**.
- The `[CDS]` / `[SQL]` / `[EXT]` / `[IR]` tagging as a structural concern — it becomes a comment in the field catalog, not a control-flow branch.
- Shared-drive paths in the control plane — gone. Shared drives are an analyst convenience, not part of our contract.

## What stays

- `survey_field_catalog` — still the metadata for each field (id, portal label, input kind, Skyvern goal hint, rankings-critical flag, readback tolerance).
- `survey_portal_payload` — still the contract Skyvern consumes.
- `survey_validation_results`, `survey_review_queue`, `survey_computed_values` — unchanged.
- All control-plane tables (runs, events, tasks, review items, signoffs, feature flags) — unchanged.

## New: the bound view per field

The field catalog and its Skyvern metadata live in **`surveys_automation.survey_field_catalog`** — a new table we own, not an alteration of any existing `surveys.core.*` table. Every row carries **one** column that the prepare job uses to fetch its value from whatever upstream view is authoritative:

| Column | Example |
| --- | --- |
| `databricks_view` | `surveys.usnews_main.v_enrollment_total_undergraduates` (upstream, read-only) |
| `databricks_value_column` | `value` |
| `databricks_year_column` | `survey_year` (nullable; used when the view carries multi-year history) |

The prepare activity is now trivially simple:

```sql
SELECT {databricks_value_column} AS value
FROM   {databricks_view}
WHERE  survey_year = :survey_year
```

If the value isn't there, prepare fails for that field with a `MISSING_IN_DATABRICKS` validation result, and a review item tells the analyst which upstream pipeline needs attention. The control plane never tries to recover by reaching outside Databricks.

## Upstream pipelines (out of scope, but required to exist)

For reference, these are the Databricks pipelines that must exist upstream of our prepare job. They are owned by the data engineering team, not this project. Our only contract is that their output is a set of stable views in `surveys.usnews_main.*`.

| Pipeline | Produces views like | Original source (per playbook) |
| --- | --- | --- |
| CDS publisher | `v_cds_b1_*`, `v_cds_c1_*`, `v_cds_i1_*` | CSULB CDS submission |
| Query pack | `v_first_gen_pct`, `v_hs_gpa_percentiles`, `v_top10_majors`, `v_faculty_diversity`, `v_international_grad_rate`, `v_international_retention_rate`, `v_transfer_out_rate` | PeopleSoft / NSC StudentTracker |
| HR ingest | `v_faculty_salaries_by_rank` | HR / AAUP / IPEDS HR |
| Advancement ingest | `v_alumni_of_record`, `v_alumni_solicited`, `v_alumni_donors` | Advancement / CASE-CAE file |
| Registrar ingest | `v_programs_majors`, `v_programs_minors` | Registrar |
| Rollover helper | `v_prior_year_value_by_field` | Prior year's `survey_computed_values` |

How each upstream pipeline gets its data (email, shared drive, scheduled NSC pull, nightly HR dump) is **not our problem**. Our problem starts at the view.

## Candidate upstream tables from campus SMEs

These table notes are source-catalog context for building the `surveys_automation.survey_field_catalog` bindings. They are **not** control-plane branching logic; each survey field should still point at one bound `databricks_view` / `databricks_value_column` / `databricks_year_column`.

| Area | Candidate table/view | SME note |
| --- | --- | --- |
| Financial aid / Pell | `bronze.cms.ps_stdnt_awrd_disb` | Previously used to track Pell data. |
| Financial aid | `bronze.cms.ps_stdnt_aid_atrbt` | Potential upstream source; confirm field-level contract before binding. |
| Financial aid | `bronze.cms.ps_stdnt_awards` | Potential upstream source; confirm field-level contract before binding. |
| Financial aid | `bronze.cms.ps_stdnt_awd_per` | Potential upstream source; confirm field-level contract before binding. |
| Financial aid | `bronze.cms.ps_stdnt_awrd_actv` | Potential upstream source; confirm field-level contract before binding. |
| Financial aid | `bronze.cms.ps_stdnt_fa_term` | Potential upstream source; confirm field-level contract before binding. |
| Enrollment | `production.silver.erss` | Tyler: gold standard for enrollment because it has census data. |
| Admissions | `production.silver.ersa` | Tyler: gold standard for admissions. |
| Enrollment related | `production.silver.serss`, `production.silver.ersd`, `production.silver.ersd_supplemental` | Laura: useful production silver sources to evaluate. |
| Faculty CDS | `production.silver.ira_faculty` | Tyler: source for the CDS faculty portion. |
| HEGIS/CIP | `production.reference.ira_ss_hegis_cip` | Laura: Oracle `SS_HEGIS_CIP` copy; likely needs annual update because CIP codes can change. |

## Current CDS query registry status

The runnable implementation also carries a markdown SQL registry at `survey-automation/cds_sql_query_registry.md`. This is the bridge between scanned PDF field names and Databricks-backed values while the longer-term bound-view catalog is being formalized.

Current 2025 registry coverage includes:

| CDS section | Registry query | Databricks sources | Notes |
| --- | --- | --- | --- |
| C1-C2 | `Q-C1-C2` | `production.silver.ersa`, `production.silver.erss` | Updated from analyst `Section C.sql`; uses Fall 2025 term `20254`, `TYLERN.GENDER(...)`, `RESIDENCE_CODE`, and actual PDF fields such as `AP_RECD_1ST_MEN_N`, `AP_ADMT_1ST_N`, and `EN_TOT_1ST_N`. |
| C11-C12 | `Q-C11-C12-C13` | `production.silver.erss` | Updated from analyst `Section C.sql`; uses unweighted `HS_GPA` bins and maps average GPA to `FRSH_GPA` when that field exists. |
| I-1 | `Q-I1` | `production.silver.ira_faculty`, analyst-provided `TYLERN.CDS_FACULTY_NRA_TM_F25_TBL` | Added from analyst `Section I.sql`; uses faculty term `2254` for survey year 2025 and maps actual PDF fields such as `FT_N`, `MIN_TOT_N`, `FT_DEG_TERM_N`, `MASTER_TOT_N`, and `GRAD_TOT_N`. |

`Section C.sql` and `Section I.sql` are local analyst references only. They should not be committed; the committed artifact is the normalized registry entry plus tests.

## Field catalog shape

Table: **`surveys_automation.survey_field_catalog`** (owned by this project). Minimum columns after this simplification:

| Column | Notes |
| --- | --- |
| `field_id` | e.g., `enrollment.total_undergraduates` |
| `survey_id` | |
| `section_id` | |
| `portal_label` | The label shown on the portal. |
| `skyvern_goal_hint` | Prose hint for Skyvern. |
| `skyvern_input_kind` | `text | number | select | radio | checkbox | checkbox_group | date | textarea` |
| `skyvern_choices` | For select/radio/checkbox_group. |
| `skyvern_readback_tolerance` | JSON; per-field tolerance for visual validation. |
| `rankings_critical` | Drives YOY-drift validation and submit-gate. |
| `databricks_view` | **Required.** Upstream view this field reads (read-only to us). |
| `databricks_value_column` | Default `value`. |
| `databricks_year_column` | Nullable. |
| `playbook_reference` | Free text: `CDS B1`, `Query Q1`, `HR file`, etc. Comment-level only. |

`playbook_reference` is human documentation, not logic. The platform does not branch on its contents.

## Prepare activity, simplified

```python
def prepare_activity(run_id: str) -> None:
    run = repo.get_run(run_id)
    fields = databricks.list_fields_for_survey(run.survey_id)

    for field in fields:
        row = databricks.query_view(
            view=field.databricks_view,
            value_column=field.databricks_value_column,
            year_column=field.databricks_year_column,
            survey_year=run.survey_year,
        )
        if row is None:
            repo.record_validation_fail(
                run_id=run_id,
                field_id=field.field_id,
                rule_id="MISSING_IN_DATABRICKS",
                message=f"No row in {field.databricks_view} for year {run.survey_year}",
            )
            continue

        repo.upsert_computed_value(
            run_id=run_id,
            field_id=field.field_id,
            raw_value=row.value,
            source_ref=field.databricks_view,
        )

    repo.run_yoy_drift_check(run_id)
    repo.publish_portal_payload(run_id)
```

No branches on source type. No external request tracker. No file watchers. Just a loop over fields reading from their bound views.

## Tables we own in `surveys_automation`

| Table | Purpose |
| --- | --- |
| `surveys_automation.survey_field_catalog` | Field metadata + Skyvern hints + bound upstream view pointer. |
| `surveys_automation.survey_portal_payload` | Resolved per-run payload that Skyvern consumes. |
| `surveys_automation.survey_computed_values` | Per-run resolved values (one row per run x field). |
| `surveys_automation.survey_validation_results` | Per-run validation outcomes. |
| `surveys_automation.survey_source_snapshot` | Per-run snapshot pointer of upstream views at prepare time. |
| `surveys_automation.v_prior_year_value_by_field` | View over our own archived `survey_computed_values` for YOY lookup. |

All DDL lives under `infra/databricks/migrations/`. None of these names collide with existing `surveys.core.*` or `surveys.usnews_main.*` objects, and every write path targets `surveys_automation.*` only.

## YOY drift still applies

Prior year values come from `surveys_automation.v_prior_year_value_by_field`, which reads our own archived `surveys_automation.survey_computed_values`. We do **not** require an upstream pipeline to produce a prior-year view for us. The ±5% rule for rankings-critical fields is unchanged.

## Review items: fewer reason codes

After simplification, these are the only reason codes the control plane generates:

- `MISSING_IN_DATABRICKS` — a bound view returned no row.
- `YOY_DRIFT` — >5% change YoY for a rankings-critical field.
- `SKYVERN_VALIDATION_MISMATCH` — readback ≠ target.
- `PORTAL_ASSESSMENT_FLAG` — flag raised by the U.S. News Assessment page.
- `MANUAL_HOLD` — analyst-created hold (override path).

Gone: `PENDING_EXTERNAL`, `EXTERNAL_REJECTED`, and other codes we introduced to manage the shared-drive/email dance.

## Control-plane SQLite schema: trim

Remove from the schema:

- `external_data_requests`
- `survey_contacts` (nice-to-have, but not needed for the first slice; push to a future iteration)
- `portal_catalog.shared_drive_path`

Keep everything else as defined in `docs/04-data-model-and-contracts.md`.

## Analyst experience

The analyst UI no longer shows "waiting for Advancement" cards. Instead it shows:

- Per-field provenance: `surveys.usnews_main.v_alumni_donors` with a Databricks link.
- Last-refreshed timestamp of that view (from Databricks metadata).
- YOY diff for each rankings-critical field.

If a value is missing or looks stale, the analyst's action is to **ping the data team** to refresh the upstream view. The control plane does not mediate that conversation.

## Implication for the playbook mapping doc

`docs/12-usnews-main-survey-playbook-mapping.md` stays as domain context for understanding how CSULB currently gets data, but all its references to `external_data_requests`, `source_type`, and the four-way tag model are superseded by this doc. When the two conflict, this doc wins.

## Implication for the codex handoff

`docs/11-codex-handoff.md`:

- Do not implement `external_data_requests`, `survey_contacts`, source-tag branching, or shared-drive integration.
- Implement a single `prepare_activity` that loops over `survey_field_catalog` rows and reads from `databricks_view`.
- All upstream data landing is assumed. If a view is empty, that's a validation failure — not a workflow state.

## Why this is correct

- **One data plane means one failure mode.** A missing value is always the same bug: an upstream view didn't land. One runbook covers it.
- **We touch no existing tables.** All writes go to `surveys_automation.*`, a schema we own. The upstream `surveys.core.*` and `surveys.usnews_main.*` objects are read-only. Install and uninstall are safe.
- **No control-plane code depends on the shape of upstream sources.** CDS changes its structure? HR changes its file format? That's the data team's problem, not ours.
- **Tests are trivial.** A prepare unit test mocks a Databricks client. No email, file drop, or NSC integration surfaces.
- **Platform onboards new surveys faster.** To add the Financial Aid survey, we add field-catalog rows pointing at new views. No new tables, no new pipelines inside our repo.
