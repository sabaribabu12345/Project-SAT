# Iteration Plan

Goal: build the vertical slice against `fake-survey-form/` first, then promote to a real portal under a feature flag.

## Slice 0 — Infra bootstrap (Week 1)

Deliverables:
- Docker Compose bringing up: Temporal server + UI, Skyvern API + worker + Chromium, and the control plane FastAPI service with SQLite mounted at `./data/control_plane.db`.
- Environment wiring so Skyvern points at Databricks Model Serving.
- Databricks workspace access verified via `databricks-sdk`.
- `fake-survey-form/` served on a static web server reachable from the Skyvern container.

Exit criteria:
- `curl` of Skyvern API returns healthy.
- `curl` of control plane `/health` returns healthy.
- A trivial Skyvern task (navigate to the fake form, report the page title) succeeds using Databricks-hosted model.

## Slice 1 — Safe Read (Week 2)

Goal: Skyvern reads data from a page and writes nothing.

Deliverables:
- `dispatch_validate_task` path implemented end-to-end against `fake-survey-form/` Institution section.
- Webhook handler verifying HMAC and signaling Temporal.
- Validate mismatch path writes `review_items`.
- Analyst UI (read-only) showing screenshots and extracted values.
- Task complexity budget + automatic chunk splitting to avoid Skyvern planning-iteration failures.
- Auto-first `scan_fields` discovery flow storing `field_discovery_drafts` for analyst approval.
- Approved `survey_field_catalog` entries with Databricks binding metadata used to build section payload.
- Missing Databricks values produce `MISSING_IN_DATABRICKS` review items.

Exit criteria:
- Temporal workflow completes a validate-only run with zero writes.
- Extracted values land in `skyvern_tasks.extracted_data_json`.
- An intentionally wrong target value produces a review item with a screenshot attached.

## Slice 2 — Draft Fill, no submit (Week 3)

Goal: Skyvern fills one section and saves a draft. Final submit is disabled.

Deliverables:
- `dispatch_section_fill_activity` against the Institution section (~10 fields).
- Visual validation step after fill (second Skyvern task, purpose `validate`).
- Analyst UI showing fill screenshots and validation diff.
- `skyvern.submit_enabled` flag enforced — attempts to dispatch submit fail in the activity.

Exit criteria:
- One section filled end-to-end on `fake-survey-form/`.
- Mismatches surface as review items before the workflow proceeds.
- No submit action is ever reached.

## Slice 3 — Analyst Gateway (Week 4)

Goal: Human sign-off unlocks the submit action on the sandbox form.

Deliverables:
- `/release-submit` API with approver identity and feature-flag checks.
- `dispatch_submit_activity` with retry policy = 1 attempt.
- Success confirmation captured as a Skyvern screenshot + extracted confirmation code.
- Audit trail shows the full sequence: review approved, submit released, submit completed.

Exit criteria:
- End-to-end run on `fake-survey-form/`: prepare → review → fill all 9 sections → validate → release → submit.
- `submit_released_flag` recorded with approver and timestamp.
- Runbook RB-05 (emergency cancel) tested.

## Slice 4 — First real portal (Week 5-6)

Goal: Onboard one real internal-facing portal under `skyvern.live_portal_enabled = true` for a single survey year.

Deliverables:
- Portal catalog entry with goal prompts reviewed by the analyst team.
- Login flow (including TOTP if applicable) via Skyvern encrypted parameters.
- First production dry run with no submit.
- Signed production run with submit.

Exit criteria:
- One real survey filled and submitted with full audit trail.
- Performance metrics baselined: median section fill time, validation mismatch rate, review turnaround.

## Slice 5+ — Generalization

- Second portal onboarded by configuration only (no new Python code).
- Playbook retrieval assistant (RAG) for analyst context.
- Dashboards on mismatch rates per field, per portal, per year.
- Automatic rerun of a single section on validation failure after analyst fix (one-click).

## What we defer indefinitely unless needed

- Multi-tenant SaaS offering.
- Browser automation outside Skyvern.
- Cross-portal unified scheduler; keep per-portal for now.
