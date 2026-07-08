# Survey Automation — Setup and Quick Start

## Prerequisites

- Docker Desktop running (or Podman with machine started)
- Python 3.11+
- Access to the CSULB Databricks workspace

---

## First-time setup

### 1. Configure environment

```bash
cd survey-automation
cp .env.example .env
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Fill in `.env`:

| Variable | Where to get it |
|---|---|
| `DATABRICKS_HOST` | Databricks workspace URL |
| `DATABRICKS_AUTH_TYPE` | `oauth-m2m` (recommended) or `pat` |
| `DATABRICKS_CLIENT_ID` | Service principal client ID |
| `DATABRICKS_CLIENT_SECRET` | Service principal secret |
| `DATABRICKS_SQL_WAREHOUSE_ID` | SQL Warehouse ID from Databricks UI |
| `DATABRICKS_GENIE_SPACE_ID` | Genie space ID for AI field resolution |
| `DATABRICKS_SQL_MAPPING_MODEL` | `databricks-claude-sonnet-4-6` (default) |
| `SKYVERN_API_KEY` | Any string, e.g. `local-dev-key-123` |
| `OPENAI_API_KEY` | Databricks OAuth token — run `python infra/scripts/print_model_serving_token.py` |

For PAT auth instead of OAuth M2M, set `DATABRICKS_TOKEN` and omit `CLIENT_ID`/`CLIENT_SECRET`.

**Never commit `.env` or `.streamlit/secrets.toml`.**

### 2. Start the stack

```bash
docker compose -f infra/docker-compose.yml up --build -d
```

Wait ~30 seconds, then verify:

```bash
curl -s http://localhost:8010/health
# Expected: {"status":"ok","control_plane_db":"sqlite",...}
```

### 3. Open the UI

- **PDF workflow:** http://localhost:8010/pdf-ops
- **Website workflow:** http://localhost:8010/website-ops
- **API docs:** http://localhost:8010/docs

---

## Databricks auth options

**OAuth M2M (recommended):**
```
DATABRICKS_AUTH_TYPE=oauth-m2m
DATABRICKS_CLIENT_ID=...
DATABRICKS_CLIENT_SECRET=...
```
The compose stack runs a local `dbx-openai-proxy` service that auto-refreshes tokens so Skyvern's LLM calls don't expire.

**PAT (simpler, token expires):**
```
DATABRICKS_TOKEN=dapi...
```
If using PAT, run `python infra/scripts/print_model_serving_token.py` periodically and update `OPENAI_API_KEY`.

---

## PDF Ops workflow

Open **http://localhost:8010/pdf-ops**.

**Step 1 — Upload and scan**
Upload the CDS survey PDF. The scan extracts all form fields and creates candidates.

**Step 2 — Registry resolve**
Click "Resolve via Registry". Pre-built SQL templates for CDS fields run directly against Databricks. This resolves enrollment, admissions, and graduation rate fields quickly without hitting Genie.

**Step 3 — Genie resolve**
Click "Resolve via Genie". For fields without SQL templates, Databricks Genie AI generates SQL queries and stores them. Genie is only called once per field — subsequent years re-run the stored SQL directly via "Resolve Direct".

**Step 4 — Analyst SQL Mapping (optional)**
For fields Genie can't resolve (ACT/SAT scores, housing costs, campus life data not in Databricks), an analyst can paste custom SQL:
1. Click "Analyst SQL Mapping"
2. Paste SQL, enter a name, click Preview (runs against Databricks, shows results)
3. Click "Auto Map" — Claude maps result columns to PDF field candidates
4. Review and approve each draft mapping
5. Click "Rerun Approved" to refresh values for a new survey year

**Step 5 — Export**
Click "Export Filled PDF". Downloads a filled PDF at `/pdf-exports/{filename}`.

**Current CDS 2024 resolution state (scan `pdfscan_bb676d3416e3`):**
- 1086 total candidates
- 263 GENIE_RESOLVED (registry + Genie)
- 823 GENIE_LOW_CONFIDENCE (fields where data is not in Databricks — ACT/SAT, housing, campus life require manual entry or Analyst SQL Mapping)

---

## Website Ops workflow

Open **http://localhost:8010/website-ops**.

1. Set the target form URL
2. Click "Start Workflow"
3. Skyvern AI fills each section using data from Databricks
4. Monitor live job status in the page table

For browser modes (watching Skyvern in your browser vs. headless), see [OPERATIONS.md](OPERATIONS.md#switching-browser-modes).

---

## Code deployment to Docker

**IMPORTANT:** The control plane container (`infra-control-plane-1`) copies code at build time — it is NOT volume-mounted. If you edit Python files, you must re-deploy:

```bash
# After editing any file in apps/api/:
docker cp apps/api/service.py infra-control-plane-1:/app/apps/api/service.py
docker cp apps/api/cds_query_registry.py infra-control-plane-1:/app/apps/api/cds_query_registry.py

# Then restart to pick up changes:
docker compose -f infra/docker-compose.yml restart control-plane
```

Container restarts wipe any `docker cp` changes — you must re-copy after every restart.

For larger changes, rebuild the image:
```bash
docker compose -f infra/docker-compose.yml up --build -d control-plane
```

---

## Key API endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/integrations/databricks` | Test Databricks connectivity |
| `POST` | `/pdf-scans` | Upload + scan a PDF |
| `GET` | `/pdf-scans` | List all scans |
| `GET` | `/pdf-scans/{scan_id}/candidates/by-section` | Candidates grouped by CDS section |
| `POST` | `/pdf-scans/{scan_id}/resolve-via-genie` | Run Genie resolution |
| `POST` | `/pdf-scans/{scan_id}/resolve-direct` | Re-run stored SQL (no Genie call) |
| `POST` | `/pdf-scans/{scan_id}/analyst-sql/preview` | Preview analyst SQL against Databricks |
| `POST` | `/analyst-sql/{query_id}/auto-map` | AI-map SQL columns to PDF candidates |
| `POST` | `/analyst-sql-mapping-drafts/{draft_id}/approve` | Approve a mapping draft |
| `POST` | `/analyst-sql/{query_id}/rerun-approved` | Refresh approved mapping values |
| `POST` | `/pdf-scans/{scan_id}/export-filled-pdf` | Export resolved fields to PDF |
| `GET` | `/pdf-exports/{file_name}` | Download exported PDF |
| `POST` | `/website-ops/full-workflow/jobs` | Launch a website fill job |
| `GET` | `/website-ops/full-workflow/jobs` | List website fill jobs |

Full interactive docs: http://localhost:8010/docs

---

## Local Python (for scripts)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Check Databricks connectivity:
.venv/bin/python infra/scripts/check_databricks.py

# Print a fresh model serving token (for OPENAI_API_KEY):
.venv/bin/python infra/scripts/print_model_serving_token.py
```

---

## Repository layout

```
survey-automation/
├── apps/
│   ├── api/
│   │   ├── main.py                  # FastAPI routes + HTML pages
│   │   ├── service.py               # Core business logic (scan, resolve, export)
│   │   ├── analyst_sql_mapping.py   # Analyst SQL mapping service
│   │   ├── cds_query_registry.py    # CDS field → Databricks SQL registry
│   │   ├── schemas.py               # Pydantic request/response models
│   │   ├── settings.py              # Config loaded from .env
│   │   └── db/
│   │       ├── models.py            # SQLAlchemy ORM models
│   │       ├── engine.py            # DB engine setup
│   │       └── session.py           # SessionLocal factory
│   ├── skyvern_worker/
│   │   ├── skyvern_client.py        # HTTP client for Skyvern API
│   │   └── task_builder.py          # Builds Skyvern task prompts
│   └── temporal_worker/
│       ├── workflows.py             # Temporal workflow definitions
│       ├── activities.py            # Temporal activities
│       └── signaler.py              # Sends signals to running workflows
├── infra/
│   ├── docker-compose.yml           # Full stack definition
│   └── scripts/                     # Data pull, fill, and token scripts
├── tests/                           # Pytest tests
├── data/                            # SQLite DB (gitignored)
├── OPERATIONS.md                    # Full operations guide
└── HANDOFF.md                       # Engineering journal
```

---

## Database schema (key tables)

| Table | Purpose |
|---|---|
| `survey_pdf_scans` | One row per uploaded PDF scan |
| `survey_pdf_datapoint_candidates` | One row per PDF field (scan_id, field_name, status, genie_value, genie_sql_template) |
| `genie_api_call_history` | Audit trail of all Genie API calls |
| `analyst_sql_queries` | SQL queries pasted by analysts |
| `analyst_sql_mapping_drafts` | AI-generated candidate↔column mapping proposals |
| `analyst_sql_field_mappings` | Approved mappings (write resolved values into candidates) |
| `workflow_jobs` | Website fill job history |
| `runs` | Temporal run records |

Candidate status values: `DISCOVERED` → `GENIE_RESOLVED` or `GENIE_LOW_CONFIDENCE`

- `GENIE_RESOLVED`: value found, confidence 100
- `GENIE_LOW_CONFIDENCE`: Genie generated SQL but it returned NULL (data not in Databricks)
- `DISCOVERED`: not yet attempted
