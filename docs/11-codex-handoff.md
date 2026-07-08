# Codex Handoff

Read this file first if you are the implementation agent.

## What you are building

A survey-filling platform where:
- Databricks computes every answer deterministically.
- Temporal drives the run lifecycle.
- Skyvern (self-hosted) executes every browser action using goal-based prompts.
- A FastAPI control plane + SQLite stores phase-1 operational state.
- An analyst UI gates review and submit.
- No external LLM SaaS ever sees survey data.

## Rules you must not break

1. No external LLM APIs. Route Skyvern at Databricks Model Serving.
2. No business logic in Skyvern prompts.
3. Every `fill` task is followed by a `validate` task.
4. Final submit is only dispatched after `submit_released_flag = true` and `skyvern.submit_enabled = true`.
5. Every action emits a `run_events` row.

## Build order

Follow `docs/09-iteration-plan.md` strictly. Ship Slice 0 end-to-end before starting Slice 1. Do not optimize or generalize ahead of the current slice.

## First four tasks (Slice 0)

1. Author `infra/docker-compose.yml` bringing up Temporal, Skyvern, and the control plane. Use the env vars in `docs/06-llm-and-model-routing.md` to point Skyvern at Databricks Model Serving.
2. Initialize `apps/api/` with SQLAlchemy 2.x, Alembic, and the schema in `docs/04-data-model-and-contracts.md`. Phase-1 DB is **SQLite** at `./data/control_plane.db` (per ADR-006). Enable `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` in an engine event listener. Write models with dialect-neutral types so a future store change is not a rewrite.
3. Stand up `apps/temporal_worker/` with a no-op `RunWorkflow` that only runs `prepare_activity` (calls a stub).
4. Implement `apps/skyvern_worker/skyvern_client.py` with `create_task`, `get_task`, `cancel_task`. Wire `dispatch_login_activity` as the first real activity and prove it can open `fake-survey-form/` under `file://` via Skyvern.

## Slice 1 tasks (read before starting)

1. Implement `task_builder.build_validate_task(run_id, section_id)` that reads from Databricks `survey_portal_payload` and builds the JSON in `docs/05-skyvern-task-contract.md`.
2. Implement `/webhooks/skyvern` with HMAC verification, writing to `skyvern_tasks` and `run_events`, then signaling Temporal.
3. Implement `dispatch_section_validate_activity`.
4. Wire the analyst UI to show extracted values vs expected values for one section.

## Coding constraints

- Python 3.12.
- FastAPI + SQLAlchemy 2.x + Alembic for the control plane. Phase-1 database is SQLite; do not use Postgres-specific types (`JSONB`, `ARRAY`, `UUID`). Use the `packages/core_models` `JSONEncoded` and `StringArray` TypeDecorators.
- `temporalio` for workflows and activities.
- Pydantic v2 for all DTOs.
- No `Optional` unless a field is genuinely nullable in the data model.
- No custom retry loops around Temporal activities; use retry policies.
- No direct Skyvern calls from the control plane; go through the `skyvern_worker` package.
- No DOM-selector-based logic in the production path. Use goal hints and labels.

## Testing constraints

- Unit tests cover: task builder output, webhook signature verification, state machine transitions, readback tolerance evaluation.
- Integration tests use ephemeral SQLite; mock Skyvern with a recorded-response fake.
- E2E tests under `tests/e2e/` require the full compose stack; they run in CI nightly against `fake-survey-form/`.

## Secrets policy for code

- Never commit tokens, HMAC keys, or portal credentials.
- `.env.example` documents every variable; real `.env` is gitignored.
- Do not log secret material even at DEBUG level.

## Definition of done for Slice 3

- One end-to-end run on `fake-survey-form/` passes in CI nightly.
- `run_events` for that run contains the full expected sequence.
- Analyst UI shows screenshots from every section's fill and validate task.
- Submit release is blocked without approver identity.
- Attempted submit without `skyvern.submit_enabled` fails with a specific error and is audited.

## Playbook context

The first real target is the **CSULB U.S. News Best Colleges Main Survey**. Read `docs/13-databricks-as-single-source.md` first — it is the normative system design. Read `docs/12-usnews-main-survey-playbook-mapping.md` after, for domain context only; where the two conflict, doc 13 wins.

Key constraints derived from doc 13:

- **Every field** — CDS, query-pack, HR, Advancement, Registrar, rollover — is read from a single bound Databricks view. There is no `source_type` branching, no `external_data_requests` table, no shared-drive integration in the control plane. If a value is missing, the prepare job emits a `MISSING_IN_DATABRICKS` validation failure and the analyst pings the data team upstream.
- The `survey_field_catalog` row carries `databricks_view`, `databricks_value_column`, `databricks_year_column`; the prepare activity is a loop over fields, not a switch over source types.
- Rankings-critical sections get automatic `YOY_DRIFT` validation at ±5%.
- Portal Assessment page is its own 3-task Skyvern flow: `assessment_scan`, `assessment_resolve`, `assessment_verify`.
- Submit is gated on a `signoffs` row from a Dean / senior administrator.
- The only review `reason_code` values the control plane generates are: `MISSING_IN_DATABRICKS`, `YOY_DRIFT`, `SKYVERN_VALIDATION_MISMATCH`, `PORTAL_ASSESSMENT_FLAG`, `MANUAL_HOLD`.

Do **not** implement `external_data_requests`, `survey_contacts`, source-tag branching, shared-drive watchers, or email-ingest paths. They are explicitly out of scope.

## Open questions to raise back, not invent answers for

- Which Databricks Model Serving endpoint (Llama 3 vs DBRX) is approved for this project?
- Is the first real portal Academic Insights (U.S. News), Peterson's, or an internal Tableau workbook? Prompt tuning depends on it.
- Is the SSO path Okta, Shibboleth, or portal-native? TOTP handling depends on it.
- Retention policy for Skyvern artifact bundles in UC Volumes.
- Who owns approver role assignment in SSO group mapping.
- Exact CDS field identifiers (e.g., `B1`, `C1`, `I1`) — confirm naming with Laura for the Databricks metadata seed.
