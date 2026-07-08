# Component Design

## 1. Databricks survey layer

Unchanged in responsibility from v2. The interface contract to the control plane tightens.

### Subcomponents

- **Snapshot builder** — freezes source data per run.
- **Metadata catalog** — `survey_catalog`, `survey_section_catalog`, `survey_field_catalog`, `survey_locator_hint_catalog` (new: prose hints consumed by Skyvern).
- **Compute engine** — deterministic SQL + PySpark.
- **Validation engine** — rule packs, produces `survey_validation_results`.
- **Review queue** — reason codes unchanged; new code `SKYVERN_VALIDATION_MISMATCH`.
- **Portal payload publisher** — writes `survey_portal_payload` with the exact values Skyvern must enter.

### New field-catalog columns

- `skyvern_goal_hint` — short prose hint, e.g. "Enrollment section, Total undergraduates field".
- `skyvern_input_kind` — `text | number | select | radio | checkbox | checkbox_group | date | textarea`.
- `skyvern_choices` — optional list for select/radio fields.
- `skyvern_readback_tolerance` — optional per-field tolerance for visual validation (numeric fuzzing, whitespace, case).

## 2. Control plane service (FastAPI + SQLite)

### Responsibilities
- Serve REST API to humans and Temporal activities.
- Persist run state, events, review queue, Skyvern task references.
- Accept Skyvern webhook callbacks (`/webhooks/skyvern`).
- Enforce state machine transitions.

### Control-plane schema (see `04-data-model-and-contracts.md`)
- `survey_runs`, `run_events`, `review_items`, `skyvern_tasks`, `feature_flags`, `secrets_refs`.
- Databricks payload mirrored into `portal_payload_cache` for fast reads by the Temporal worker (optional; Temporal can read Databricks directly if latency allows).

### Why SQLite first

- SQLite gives the first slice durable run state without introducing a new database service.
- Databricks remains authoritative for survey data and artifacts.
- Skyvern can use SQLite for phase-1 internal storage; Temporal and Skyvern storage are deployment details and should not dictate the control-plane store.

## 3. Temporal workflow

### `run_workflow(run_id)`

```text
1. call prepare_activity(run_id)
2. wait_for_signal("review_resolved") if any holds exist
3. for each section in order:
   a. call dispatch_section_fill_activity(run_id, section_id)   -> creates Skyvern fill task
   b. await webhook_signal("fill_completed", section_id)
   c. call dispatch_section_validate_activity(run_id, section_id) -> creates Skyvern validate task
   d. await webhook_signal("validate_completed", section_id)
   e. if validation fails -> create review items, pause workflow
4. wait_for_signal("submit_released")
5. call dispatch_submit_activity(run_id)
6. await webhook_signal("submit_completed")
7. call archive_activity(run_id)
```

### Why Temporal, not Prefect/LangGraph

- Survey runs are **long-running** (hours to days including human gates).
- Signals are first-class (review_resolved, submit_released).
- Retry policies per activity are critical for Skyvern flakiness.
- LangGraph is not appropriate as the core workflow (see ADR-005).

## 4. Skyvern integration

### Task shape
See `05-skyvern-task-contract.md`. Every task is a single section or a single submit action.

### Task builder
- Input: one `survey_portal_payload` slice filtered by `section_id`.
- Output: Skyvern task JSON with:
  - `url` (resolved from portal catalog; secrets injected at runtime).
  - `navigation_goal` (prose derived from section metadata).
  - `data_extraction_goal` (the readback contract for validation).
  - `actions` (list of `{action: "fill" | "select" | "check", label_hint, target_value}` items).
  - `totp_identifier` and `totp_url` when SSO is needed (fetched from secrets).

### Callback flow
- Skyvern POSTs to `POST /webhooks/skyvern` with `task_id`, `status`, `artifact_urls`, `extracted_data`.
- Control plane validates the callback signature, writes `skyvern_tasks` row, and signals the Temporal workflow.

## 5. Analyst Review UI

### Rendering
- Server-rendered FastAPI + Jinja2 + HTMX. No SPA.
- One page per run showing sections, their status, and Skyvern screenshots.
- Per-section drawer with: computed value vs filled value, mismatch diff, screenshot gallery, approve / reject / override actions.

### Actions
- **Resolve hold** — writes to `review_items`, calls control plane API, does not directly signal Temporal.
- **Approve section fill** — advances the workflow after fill validation passed.
- **Release submit** — final gate; records approver identity and timestamp; sends `submit_released` signal to Temporal.

## 6. Secrets and portal credentials

- `secrets_refs` table stores references (not values) pointing to the approved secrets store (AWS Secrets Manager, HashiCorp Vault, or Databricks secret scope).
- The Skyvern task builder resolves secrets at dispatch time and injects them into the Skyvern task payload via Skyvern's encrypted parameters feature.
- Secrets never appear in Temporal workflow inputs, the control-plane database, or logs.

## 7. Optional internal AI assist

Unchanged from v2. Allowed:
- Exception summarization (input: validation results; output: analyst-facing text).
- Assessment explanation drafting.
- Internal RAG over survey playbooks.

All served by Databricks Model Serving. No external calls.
