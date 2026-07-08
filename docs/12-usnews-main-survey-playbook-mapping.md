# U.S. News Main Survey — Playbook → System Mapping

> **Superseded in part by `docs/13-databricks-as-single-source.md`.** That doc is the normative system design. This document remains useful as *domain context* — it explains how CSULB analysts currently gather the data (CDS, query pack, external department files, rollover) and why the playbook tags fields `[CDS]` / `[SQL]` / `[EXT]` / `[IR]`. Inside our platform none of those tags exist: every field reads from one bound Databricks view. When this doc and doc 13 conflict, doc 13 wins.

This document anchors the abstract architecture in the concrete CSULB IR&A U.S. News Best Colleges Main Survey workflow (playbook v2.0, April 2026). It is the reference a new engineer or analyst uses to understand what a "run" actually *is* in this system.

## Why this matters for the design

The real workflow is not "fill a form end-to-end". It is:

1. Copy ~75% of values **from the CSULB Common Data Set (CDS)**.
2. Run ~10 custom queries **against institutional source systems** for the non-CDS fields (first-gen %, GPA percentiles, faculty diversity grid, international grad/retention rates, transfer-out, top-10 majors).
3. Request **external department data** (Advancement for alumni giving; HR for faculty salaries) early in the cycle because they are the slowest line items.
4. Cross-check every value against prior year; any **±5% drift** pauses for analyst review before entry.
5. Enter values manually into the U.S. News portal, section by section, because there is no batch upload for the Main survey.
6. Run the **portal Assessment step** and resolve every flag (with a typed explanation) before Submit activates.
7. Require **Dean / senior administrator sign-off** before the red Submit button is even active.

Our architecture has to mirror each of those steps or it does not fit the actual job.

## Run lifecycle mapped to playbook steps

| Playbook step | System component | Artifact |
| --- | --- | --- |
| Step 1 — Setup (prior year folder, portal login, CDS open, survey PDF checklist) | Control plane creates a `survey_runs` row; operator opens the run in the analyst UI, which links prior year's archived run + CDS source references. | `survey_runs`, `run_events` (`RUN_CREATED`) |
| Step 2 — Fill CDS-aligned fields (~75%) | Databricks `prepare_from_cds` job reads the frozen CDS snapshot and populates `survey_computed_values` with source = `CDS`. | `survey_source_snapshot`, `survey_computed_values.source_snapshot_id = 'cds'` |
| Step 3 — Run the 10 non-CDS queries | Historical playbook step: analysts run the query pack (Q1-Q10 from playbook §4). Current platform rule: those values must land in Databricks first, then the control plane reads bound views or registry SQL. | `survey_computed_values` rows per field, linked to Databricks provenance |
| Step 4 — Enrollment data | Subset of Step 2/3; three years of data required (current + 2 prior). | Payload rows tagged `section_id = 'enrollment'` |
| Step 5 — Graduation and retention | Cohort year = `survey_year - 6`. Must match IPEDS Graduation Rates. Queries Q5, Q6, Q7 provide transfer-out, international grad rate, international retention. | `survey_computed_values` + `survey_validation_results` (IPEDS match rule) |
| Step 6 — Faculty data | CDS I1/I2 + Query Q4 (faculty diversity) + HR file Q8 (salaries by rank × contract length, **totals not averages**). Current CDS I-1 registry coverage uses `production.silver.ira_faculty` plus analyst-maintained NRA/terminal-master lookup data already in Databricks. | `survey_computed_values`, Databricks provenance |
| Step 7 — Alumni giving | Historical playbook step: Advancement supplies two years of CASE/CAE values, **soft-credit-only excluded**. Current platform rule: the value is only consumed after it lands in a Databricks view. | Databricks-bound value |
| Portal fill | Temporal `dispatch_section_fill_activity` → Skyvern fill task per section. | `skyvern_tasks` rows |
| Visual validation | Temporal `dispatch_section_validate_activity` → Skyvern validate task per section. | ADR-004 |
| Portal Assessment step | A dedicated Skyvern task with `purpose = 'assessment'` that navigates to the Assessment page, reads each flag, and surfaces it as a `review_item` requiring a typed explanation from the analyst. | `review_items` with `reason_code = 'PORTAL_ASSESSMENT_FLAG'` |
| Verification + Dean sign-off | Control plane enforces: `dean_signoff` row exists and `approver_role in ('dean', 'provost', 'president')` before `submit_released_flag` can be set. | `signoffs` table |
| Submit | Temporal `dispatch_submit_activity` → Skyvern submit task. Receipt screenshot saved. | `run_events.SKYVERN_SUBMIT_COMPLETED` |
| Post-submit | Save confirmation to shared drive (UC Volume mirror); update tracking log. | `run_events.RUN_ARCHIVED` |

## Historical source tag model

This section documents the old source-tag model for playbook context only. It is **not implemented** in the current platform. The current platform stores Databricks binding metadata instead: `databricks_view`, `databricks_value_column`, optional `databricks_year_column`, and `playbook_reference`.

Historically, every field in the survey carried a provenance tag:

| Playbook tag | `source_type` column value | Prepare path |
| --- | --- | --- |
| `[CDS]` | `CDS` | `prepare_from_cds` activity copies from the frozen CDS snapshot. |
| `[SQL]` | `SQL_QUERY` | `prepare_non_cds` activity runs a query from `query_catalog`. |
| `[EXT]` | `EXTERNAL_DEPT` | `external_data_requests` tracker; value lands via analyst upload or a targeted Databricks ingest. |
| `[IR]` | `IR_ROLLOVER` | Copied from prior year's archived `survey_computed_values` unless analyst marks refresh needed. |

Historically, `source_type` drove both:

- which prepare activity runs for that field
- how the analyst UI shows provenance next to the computed value

## ★ Rankings-critical fields

The playbook marks six sections as rankings-critical. The control plane tags these via `survey_field_catalog.rankings_critical = true`. Rankings-critical fields get:

- Tighter validation (`±5%` year-over-year drift creates a `review_item` automatically).
- Higher-severity review items (sorted first in the analyst UI).
- A hard gate on Skyvern submit if any rankings-critical field has an unresolved review item, regardless of `skyvern.submit_enabled`.

Rankings-critical sections (from playbook):

- Enrollment (A)
- Faculty (D)
- Class size (E)
- Graduation rates (F)
- Retention rate (G)
- Alumni giving (H)

## Query catalog

New table `query_catalog` (in Databricks metadata, not the control-plane store) records the 10 queries from playbook §4. Each row has:

- `query_id` (e.g., `Q01_first_gen_pct`)
- `name`, `description`
- `source_system` (`peoplesoft`, `nsc_studenttracker`, `ipeds_hr_aaup`, `advancement`, etc.)
- `maps_to_field_ids` — list of `field_id`s populated from this query
- `sql_text_uri` (path under `S:\IR&A Standard Reporting\Annual Surveys\U.S. News and World Report\[Year]\Queries\`)
- `last_run_ts`, `last_run_operator`, `output_uri`
- `[EXT]` rows have `source_system = 'external_request'` and point to `external_data_requests`

Query save convention (from playbook): `[Year]_USNews_Q[#]_[ShortName].sql`. The Databricks job saves outputs to the same shared-drive path *and* to a UC Volume so the platform has a governed copy.

## Historical external data requests

Historical design note: this document originally proposed a control-plane `external_data_requests` table. That table is removed from the current architecture; external values must land in Databricks upstream before this platform reads them.

| Column | Type | Notes |
| --- | --- | --- |
| `request_id` | text PK | |
| `run_id` | text | |
| `department` | text | `advancement`, `hr`, `registrar`, `ir` |
| `contact_email` | text | Configured per department. |
| `requested_at` | timestamptz | |
| `due_by` | date | |
| `received_at` | timestamptz nullable | |
| `artifact_uri` | text nullable | UC Volume path once file received. |
| `fields_populated` | text[] | `field_id`s hydrated from this file. |
| `status` | text | `REQUESTED | RECEIVED | PARSED | REJECTED` |

The analyst UI shows a dashboard of open external requests and an "age" counter to surface late ones. This matches the playbook's "start early" warning on alumni giving and faculty salaries.

## Year-over-year sanity check

Playbook §8 is a hard-coded sanity benchmark. We operationalize it as a validation rule family `YOY_DRIFT`:

- For every field with `rankings_critical = true`, compute `abs(current - prior) / prior`.
- If > 5%, emit `SurveyValidationResult(severity='WARN', status='FAIL')` and a `review_items` row with `reason_code = 'HIGH_VARIANCE'`.
- The analyst must either approve with a note, correct the source data, or re-run the query.

Prior-year values are read from the archived prior run's `survey_computed_values` (`run_status = 'ARCHIVED'` and same `survey_id`).

## Portal Assessment flags

U.S. News requires the analyst to resolve every flag on the Assessment page with a typed explanation before Submit activates. We handle this as:

1. A Skyvern task with `purpose = 'assessment_scan'` navigates to the Assessment page and returns a structured list of flags (field, portal-generated message).
2. Each flag becomes a `review_items` row with `reason_code = 'PORTAL_ASSESSMENT_FLAG'` and `context_json = { portal_message, field_id }`.
3. The analyst types an explanation into the UI, which is persisted in `review_items.resolution_note`.
4. A follow-up Skyvern task `purpose = 'assessment_resolve'` re-opens the Assessment page and enters each explanation into the corresponding text box.
5. A final `purpose = 'assessment_verify'` task confirms all flags show as resolved.

This keeps the LLM-visible text to field-labeled explanations, not survey values.

## Dean sign-off gate

New control-plane table `signoffs`:

| Column | Type |
| --- | --- |
| `signoff_id` | uuid PK |
| `run_id` | text FK |
| `role` | text | `dean`, `provost`, `president`, `other_top_official` |
| `signer_name` | text |
| `signer_title` | text |
| `signer_email` | text |
| `signer_method` | text | `docusign`, `email_confirmed`, `in_person` |
| `evidence_uri` | text | Scanned letter, email thread export, etc. |
| `signed_at` | timestamptz |

The `release-submit` endpoint requires a `signoffs` row with a role in (`dean`, `provost`, `president`, `other_top_official`) before setting `submit_released_flag`. This mirrors the playbook's hard portal rule: the U.S. News Submit button will not activate without the Dean / senior administrator's information.

## Contacts and logistics captured as config

| Playbook datum | Where it lives |
| --- | --- |
| Portal URL (`https://dataportal.usnews.com`) | `portal_catalog.url` |
| Login email (`ira-workgroup@csulb.edu`) | `secrets_refs` reference; resolved at dispatch. |
| Password (per-cycle) | Secrets store only; never in DB. |
| Shared-drive path | `portal_catalog.shared_drive_path` |
| Typical deadline (June 1) | `survey_runs.deadline` |
| Named contacts (Anthony, Mahmoud, Laura, Tyler) | `survey_contacts` table keyed by `survey_id`. |

## What this mapping forces us to build that was implicit before

- `query_catalog` with per-query audit of `last_run_ts` and output URI.
- `external_data_requests` tracker with due dates and status.
- `signoffs` gate, separate from the workflow-internal `submit_released_flag`.
- `YOY_DRIFT` validation rule family driven by `rankings_critical` tagging.
- Four distinct Skyvern task purposes for the Assessment workflow (`assessment_scan`, `assessment_resolve`, `assessment_verify` — plus `fill`, `validate`, `submit`, `login`).
- A rollover path for `[IR]` fields so that "rolled over from prior year" is a first-class provenance, not an exception.

## Non-goals for this playbook

- Automating the CDS submission itself (upstream, outside this system).
- Replacing the Financial Aid or Financial sub-surveys' playbooks. They get their own metadata packs when it's time to onboard them.
- Any automated edit of the prior-year archived data. Rollover is a read, never a write.
