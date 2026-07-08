# Target Architecture

## System view

```text
+-----------------------------------------------------------------------------+
|                          HUMAN GOVERNANCE LAYER                             |
|  Analyst Review UI | Exception Approval | Sign-off Authority | Submit Gate  |
+---------------------------------+-------------------------------------------+
                                  ^
                                  | approval / release signals
                                  |
+---------------------------------+-------------------------------------------+
|                 CONTROL PLANE (FastAPI + SQLite)                            |
|  Run API | Review API | Release API | Audit API | Webhook ingest           |
|  run_state_machine (authoritative enum transitions)                         |
+---------------+-----------------------------------+-------------------------+
                ^                                   ^
                | writes run state                  | reconciles outcomes
                |                                   |
+---------------+---------------+     +-------------+-----------------+
|      Temporal Workflows       |     |   Databricks Data Plane       |
|  run_workflow (long-running)  |     |  Source snapshot              |
|  section_fill_activity        |     |  Field catalog + metadata     |
|  validate_activity            |     |  Compute engine               |
|  review_wait_signal           |     |  Validation engine            |
|  submit_release_signal        |     |  Portal payload publisher     |
+---------------+---------------+     |  Evidence (UC Volumes)        |
                |                     +-------------+-----------------+
                | dispatch tasks                    ^
                v                                   | payload read
+-------------------------------+                   |
|       SKYVERN CLUSTER         |<------------------+
|  (self-hosted, Docker)        |
|  Skyvern API                  |
|  Skyvern worker pool          |
|  Chromium browser pool        |
|  Skyvern SQLite (phase 1)     |
+---------------+---------------+
                |
                | calls OpenAI-compatible endpoint
                v
+-------------------------------+
|  Databricks Model Serving     |
|  (Llama 3 / DBRX)             |
+---------------+---------------+
                |
                v
+-------------------------------+
|       SURVEY PORTAL           |
+-------------------------------+
```

## Responsibility split

### Databricks
- Immutable source snapshots per run.
- Survey metadata (sections, fields, locator hints, validation rules).
- Field-level computed values.
- Validation results.
- Portal payload table (what Skyvern must fill).
- Evidence and screenshot archival (UC Volumes).

### Control plane service (FastAPI + SQLite)
- Run lifecycle API (`POST /runs`, `prepare`, `approve-review`, `release-submit`).
- Review queue CRUD for analysts.
- Webhook endpoint for Skyvern callbacks.
- Authoritative state transitions via `run_state_machine`.
- Audit event ingestion.

### Temporal
- One workflow per run.
- Activities: `prepare`, `publish_payload`, `dispatch_section_fill`, `wait_for_review`, `dispatch_validation`, `wait_for_submit_release`, `dispatch_submit`, `archive`.
- Durable retries with per-activity policies.
- Human signals: `review_resolved`, `submit_released`.

### Skyvern
- Executes a single goal-based task per section.
- Navigates, logs in, fills fields, validates fields, captures screenshots.
- Reports completion via webhook back to the control plane.
- Does not persist authoritative state; Temporal + the control plane + Databricks do.

### Humans
- Analyst: resolves review holds, approves fill, reviews filled screenshots.
- Approver: signs off, releases submit.

## Architecture principles

1. **Deterministic values, agentic navigation.** Databricks computes values; Skyvern finds the right field.
2. **Two-phase browser work.** Phase A: fill. Phase B: validate. Both are Skyvern tasks, chained by Temporal.
3. **No shared mutable state between Skyvern tasks.** Each task takes a self-contained payload.
4. **Audit trail is append-only.** Skyvern callbacks write events; never updates.
5. **Control plane is the only surface humans and workers talk to.** Temporal and Skyvern are internal.
