# Data Model and Contracts

> **Note:** `docs/13-databricks-as-single-source.md` supersedes parts of this document. Specifically: `external_data_requests`, `survey_contacts`, and `portal_catalog.shared_drive_path` are **removed** from the control-plane schema. The `survey_field_catalog` carries `databricks_view` / `databricks_value_column` / `databricks_year_column` columns, and every field is read from a single bound Databricks view. Where this doc and doc 13 conflict, doc 13 wins.

Two stores:

- **Databricks** — authoritative survey data and metadata.
  - **Upstream schemas** (`surveys.core.*`, `surveys.usnews_main.*`, etc.) are **read-only** to us. Owned by the data-engineering pipelines (CDS publisher, query pack, HR ingest, Advancement ingest, Registrar ingest). We never ALTER or write to them.
  - **Our own schema `surveys_automation.*`** holds the field catalog, portal payload, computed values, validation results, and source snapshots. All DDL and writes on the Databricks side target this schema only. See `docs/13-databricks-as-single-source.md`.
  - Every survey value lives in Databricks; the control plane never reads from PeopleSoft, HR files, shared drives, or email attachments directly.
- **SQLite (control plane, phase 1)** — operational run state, events, tasks, review queue, signoffs. File at `./data/control_plane.db`. See ADR-006 for revisit triggers; all SQLAlchemy models are written to be portable if the store changes later.

## Databricks tables (Delta / UC)

All tables below live in the project-owned schema **`surveys_automation`**. Nothing in this section ALTERs or writes to `surveys.core.*` or any other upstream schema.

### `surveys_automation.survey_field_catalog`

Columns:

| Column | Type | Notes |
| --- | --- | --- |
| `skyvern_goal_hint` | STRING | Prose hint used in Skyvern task prompt. |
| `skyvern_input_kind` | STRING | Enum: `text | number | select | radio | checkbox | checkbox_group | date | textarea`. |
| `skyvern_choices` | ARRAY<STRING> | Present for `select | radio | checkbox_group`. |
| `skyvern_readback_tolerance` | STRING | JSON; see below. |
| `databricks_view` | STRING | **Required.** Fully-qualified Databricks view the prepare job reads this field from (e.g., `surveys.usnews_main.v_enrollment_total_undergraduates`). |
| `databricks_value_column` | STRING | Column in the view that holds the value. Default `value`. |
| `databricks_year_column` | STRING | Nullable. Column used to filter by `survey_year` when the view carries multi-year history. |
| `rankings_critical` | BOOLEAN | Drives YOY-drift validation and submit-gate. |
| `playbook_reference` | STRING | Free-text human documentation (e.g., `CDS B1`, `Query Q1`, `HR file`). Comment-level only; the platform does not branch on it. |

`skyvern_readback_tolerance` JSON shape:

```json
{
  "kind": "numeric | exact | normalized_text",
  "numeric_abs": 0,
  "numeric_pct": 0,
  "strip_whitespace": true,
  "case_insensitive": true
}
```

### `surveys_automation.survey_portal_payload`

Key columns used by Skyvern:

- `run_id`, `section_id`, `page_order`, `field_id`
- `portal_label`, `portal_locator_key` (deprecated for Skyvern path; kept for Playwright fallback)
- `skyvern_goal_hint`
- `value_to_enter`
- `fill_allowed`
- `payload_version`

## Control-plane tables (SQLite phase 1 / portable SQLAlchemy models)

All tables have `created_at` and `updated_at` columns (`TIMESTAMP` in SQLite). The SQLAlchemy models should avoid dialect-specific types so the control-plane store can move later if required. `uuid` columns are stored as `TEXT` in SQLite. Array-like columns are stored as JSON arrays in a `TEXT` column; a SQLAlchemy `TypeDecorator` keeps this portable.

### `survey_runs`

| Column | Type | Notes |
| --- | --- | --- |
| `run_id` | text PK | |
| `survey_id` | text | FK to Databricks catalog (string reference, not enforced). |
| `survey_year` | int | |
| `current_state` | text | Enum; enforced by CHECK constraint. |
| `created_by` | text | |
| `payload_version` | int default 0 | Incremented when payload republished. |
| `submit_released_flag` | bool default false | |
| `submit_released_by` | text nullable | |
| `submit_released_at` | timestamptz nullable | |
| `temporal_workflow_id` | text nullable | |
| `temporal_run_id` | text nullable | |

### `run_events` (append-only)

| Column | Type |
| --- | --- |
| `event_id` | uuid PK |
| `run_id` | text FK |
| `event_type` | text |
| `actor_type` | text |
| `actor_id` | text |
| `event_ts` | timestamptz |
| `payload_json` | jsonb |

Indexes: `(run_id, event_ts)`, `(event_type)`.

### `review_items`

| Column | Type |
| --- | --- |
| `review_item_id` | uuid PK |
| `run_id` | text FK |
| `section_id` | text |
| `field_id` | text nullable |
| `reason_code` | text |
| `current_status` | text default 'OPEN' |
| `resolution_type` | text nullable |
| `resolution_note` | text nullable |
| `resolved_by` | text nullable |
| `resolved_at` | timestamptz nullable |
| `context_json` | jsonb |

### `skyvern_tasks`

| Column | Type |
| --- | --- |
| `skyvern_task_id` | text PK | (Skyvern's own id) |
| `run_id` | text FK |
| `section_id` | text |
| `purpose` | text | `fill | validate | submit | login` |
| `status` | text | `QUEUED | RUNNING | SUCCEEDED | FAILED | TIMED_OUT | CANCELED` |
| `dispatched_at` | timestamptz |
| `completed_at` | timestamptz nullable |
| `artifact_bundle_uri` | text nullable | UC Volume path |
| `screenshot_uris` | text[] |
| `extracted_data_json` | jsonb nullable |
| `error_message` | text nullable |

### `feature_flags`

| Column | Type |
| --- | --- |
| `flag_key` | text PK |
| `enabled` | bool |
| `scope_json` | jsonb |

Seeded flags:
- `skyvern.submit_enabled` (default `false` until Slice 3 is signed off)
- `skyvern.live_portal_enabled` (default `false`)

### `secrets_refs`

| Column | Type |
| --- | --- |
| `secret_key` | text PK |
| `backend` | text | `aws_secrets_manager | vault | databricks_scope` |
| `reference` | text | ARN / path / scope key |
| `purpose` | text |

### `signoffs`

Dean / senior-administrator sign-off gate.

| Column | Type |
| --- | --- |
| `signoff_id` | uuid PK |
| `run_id` | text FK |
| `role` | text | `dean | provost | president | other_top_official` |
| `signer_name` | text |
| `signer_title` | text |
| `signer_email` | text |
| `signer_method` | text | `docusign | email_confirmed | in_person` |
| `evidence_uri` | text |
| `signed_at` | timestamptz |

### `portal_catalog`

| Column | Type |
| --- | --- |
| `portal_id` | text PK |
| `url` | text |
| `login_email_ref` | text | FK to `secrets_refs.secret_key` |
| `password_ref` | text | FK to `secrets_refs.secret_key` |
| `totp_ref` | text nullable |
| `default_deadline_mmdd` | text | e.g., `06-01` |

## Control plane API

Additions and changes from v2.

### `POST /runs/{run_id}/prepare`
Unchanged. Triggers Databricks prepare job through Temporal activity.

### `POST /runs/{run_id}/approve-review`
Transitions `READY_FOR_REVIEW` → `READY_FOR_FILL`. Requires zero open review items.

### `POST /runs/{run_id}/release-submit`
Transitions `FILLED` → `SUBMIT_READY`. Requires:
- `skyvern.submit_enabled` flag true.
- All section fills completed with validation pass.
- Approver identity recorded.

### `POST /webhooks/skyvern`

Request (HMAC-signed):

```json
{
  "skyvern_task_id": "task_abc",
  "status": "SUCCEEDED",
  "purpose": "fill",
  "run_id": "run_2026_0001",
  "section_id": "enrollment",
  "artifact_bundle_uri": "uc://surveys/artifacts/run_2026_0001/enrollment/fill.zip",
  "screenshot_uris": ["uc://.../step_01.png"],
  "extracted_data": { "total_undergraduates": "33605" },
  "error_message": null
}
```

The handler:
1. Verifies HMAC signature.
2. Upserts `skyvern_tasks`.
3. Appends `run_events` row.
4. Signals the Temporal workflow with `{signal_name: "fill_completed" | "validate_completed" | "submit_completed", section_id}`.

### `GET /runs/{run_id}/review`
Returns review queue for the analyst UI, joined with field metadata and Skyvern screenshots.

### `POST /review-items/{id}/resolve`
Records analyst resolution. If all review items for a run are resolved and run is `BLOCKED`, transitions to `READY_FOR_REVIEW`.

## Temporal workflow signals

| Signal | Payload | Sender |
| --- | --- | --- |
| `review_resolved` | `{resolver_id}` | Control plane (after last hold cleared). |
| `skyvern_fill_completed` | `{section_id, skyvern_task_id, status}` | Webhook handler. |
| `skyvern_validate_completed` | `{section_id, skyvern_task_id, status, mismatches}` | Webhook handler. |
| `submit_released` | `{approver_id}` | Control plane (after `/release-submit`). |
| `skyvern_submit_completed` | `{skyvern_task_id, status}` | Webhook handler. |
