# Repo Layout and Migration from v2

## Target monorepo layout

```text
survey-automation/
  apps/
    api/                      # FastAPI control plane (was v2, now SQLite-backed)
      main.py
      routes/
        runs.py
        review.py
        release.py
        webhooks.py
      db/
        engine.py             # SQLAlchemy (sqlite:///./data/control_plane.db)
        models.py
        types.py              # JSON + string-array TypeDecorator for SQLite portability
        migrations/           # Alembic
      settings.py
    temporal_worker/          # NEW
      worker.py
      workflows/
        run_workflow.py
      activities/
        prepare.py
        list_sections.py
        dispatch_login.py
        dispatch_fill.py
        dispatch_validate.py
        dispatch_submit.py
        create_review_items.py
        park_for_review.py
        archive.py
    skyvern_worker/           # NEW — thin Skyvern client + task builder
      task_builder.py
      skyvern_client.py       # wraps Skyvern REST API
      validators.py           # readback tolerance evaluation
    review_ui/                # NEW — server-rendered analyst UI
      main.py
      templates/
      static/
    databricks_jobs/          # Databricks bundle (dbx / DAB)
      prepare/
      publish_payload/
  packages/
    core_models/              # Pydantic models shared across apps
    state_machine/            # Enum + transition table (ported from v2)
    portal_contracts/         # Skyvern task + webhook schemas
    audit/                    # Event type constants
    skyvern_sdk/              # Typed wrappers over Skyvern's REST endpoints
  data/
    control_plane.db          # SQLite file (gitignored)
    backups/                  # rotated file copies
  infra/
    docker-compose.yml        # Temporal (dev-server), Skyvern, control plane, review UI
    skyvern/
      Dockerfile
      env.example
    temporal/
      dynamicconfig.yaml
    databricks/
      databricks.yml          # DAB bundle
    scripts/
      seed_metadata.py
      seed_feature_flags.sql
  docs/
    adr/
    runbooks/
  tests/
    unit/
    integration/
    e2e/                      # Against fake-survey-form
  pyproject.toml
```

## Migration from v2

### Keep

- `fake-survey-form/` — smoke test target for all slices.
- `packages/core_models/models.py` — port Pydantic models, add Skyvern-specific fields.
- `packages/state_machine/run_state_machine.py` — extend with new BLOCKED transition triggers.
- `packages/audit/event_types.py` — extend with new `SKYVERN_*` events.
- `packages/portal_contracts/` — replace worker task/event messages with Skyvern task contract.
- Databricks-facing settings and `managed_mcp.py` remain available for internal SQL reads.

### Delete / archive

- `apps/survey_agent/browser_fill.py` — Playwright-based fill. Archive to `legacy/` for reference.
- `apps/survey_agent/browser_use_runner.py` — removes external LLM path.
- `apps/survey_agent/mcp_agents/` and `mcp_cli.py` — Playwright MCP dev tooling; keep only if Cursor-side debugging is still desired, otherwise archive.
- `apps/worker/` — task-pull worker replaced by Temporal + Skyvern push model.
- `apps/api/repository.py` in-memory implementation — replaced by SQLAlchemy repository against SQLite.

### Rewrite

- `apps/api/service.py` — calls Temporal client to start / signal workflows; no longer owns orchestration logic itself.
- `apps/api/main.py` — register new routes (`/webhooks/skyvern`, `/runs/{id}/release-submit`, review endpoints).

### New

- Everything under `apps/temporal_worker/`, `apps/skyvern_worker/`, `apps/review_ui/`.
- `infra/docker-compose.yml` and supporting Dockerfiles.
- Alembic migrations under `apps/api/db/migrations/`.

## Data migration

There is no production data in v2. For local development:

1. Drop the in-memory repository; run `alembic upgrade head` to create the SQLite schema at `./data/control_plane.db`.
2. Run `infra/scripts/seed_metadata.py` to load:
   - One survey (`usnews_main`) mapped to the U.S. News Best Colleges Main Survey.
   - Nine sections matching the playbook (Institution, Admissions, Enrollment, Student Background, Faculty, Graduation, Alumni, Assessment, Verification).
   - Field catalog rows with `databricks_view`, `databricks_value_column`, optional `databricks_year_column`, `playbook_reference`, and `rankings_critical` flags. Do not seed the removed `source_type` model.
   - Contacts (Anthony, Mahmoud, Laura, Tyler), portal catalog entry, secrets refs (values stubbed).
3. Feature flags seeded with `skyvern.submit_enabled = false` and `skyvern.live_portal_enabled = false`.

## Future store revisit

When migration trigger from ADR-006 fires:

1. Re-evaluate whether SQLite still fits the workload.
2. Prefer the existing Databricks platform if the control-plane state can be modeled cleanly there.
3. If a separate operational database is required, run `alembic upgrade head` against that store and migrate from SQLite with a one-shot script.
4. Flip `CONTROL_PLANE_DATABASE_URL` env var.
5. Confirm with `GET /health?deep=1` which reports dialect + migration head.

## Testing migration

Port v2 tests with these adjustments:

- `tests/test_api_runs.py` — keep `TestClient` fixtures on ephemeral SQLite for phase 1.
- `tests/test_state_machine.py` — extend with new transitions.
- New `tests/e2e/test_fake_form.py` — drives a Temporal workflow end-to-end against `fake-survey-form/` using a real Skyvern instance.

## Dependency changes

Add:
- `temporalio`
- `sqlalchemy`, `alembic` (SQLite is built into the Python stdlib; add another database driver only if a future store migration requires it)
- `httpx` (already present)
- `jinja2`, `python-multipart` (review UI)
- `pydantic-settings`

Remove from production path:
- `playwright`
- `browser-use`
- `langchain`, `langchain-openai`, `langchain-mcp-adapters`

Keep in dev extras only:
- `playwright` under `[dev]` extra for `fake-survey-form/` smoke tests.
