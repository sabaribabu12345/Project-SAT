# Survey Automation — Operations Guide

**Audience:** Anyone running the system for the first time, or returning after a break.

---

## Architecture in one paragraph

Two workflows share a single FastAPI control plane backed by SQLite. **PDF Ops** resolves survey field values from Databricks (via a pre-built SQL registry + Databricks Genie AI), then exports a filled PDF. **Website Ops** uses Skyvern (AI browser automation) to fill the online survey portal section-by-section. Data for both workflows comes from Databricks. Temporal handles long-running Skyvern orchestration. For local development, Skyvern can connect to a real browser (Edge or Chrome) via CDP so you can watch it work.

---

## PDF Ops — Step-by-step

### Overview

```
Upload PDF → Scan → Registry Resolve → Genie Resolve → Analyst SQL Mapping → Export Filled PDF
```

Open **http://localhost:8010/pdf-ops**.

---

### Step 1 — Upload and scan the PDF

Click "Upload PDF". The scanner extracts all form fields and creates one candidate row per field in `survey_pdf_datapoint_candidates`. Initial status is `DISCOVERED`.

The active CDS 2024 scan is `pdfscan_bb676d3416e3` (1086 candidates). If you need a fresh scan, upload the PDF again — the old scan can be archived/deleted via the UI or API.

---

### Step 2 — Registry resolve (fast, no Genie calls)

Click **"Resolve via Registry"** (or POST `/pdf-scans/{scan_id}/resolve-via-genie` with `force_regenie=false`).

This runs pre-built SQL templates from `cds_query_registry.py` directly against Databricks for all known CDS fields (enrollment, admissions, graduation rates, retention, etc.). No Genie API calls are made. Candidates that resolve get status `GENIE_RESOLVED`.

Run this first — it's fast and handles the bulk of numeric fields.

**Current state after registry run:** 263 of 1086 fields resolved.

---

### Step 3 — Genie resolve (AI generates SQL)

Click **"Resolve via Genie"** (or POST `/pdf-scans/{scan_id}/resolve-via-genie`).

For fields with no SQL template, Databricks Genie AI generates a SQL query, executes it, and stores the template in `genie_sql_template`. On success, status becomes `GENIE_RESOLVED`. If Genie generates SQL but execution returns NULL, status becomes `GENIE_LOW_CONFIDENCE`.

**Important:** Genie is only called once per field. Once `genie_sql_template` is populated, subsequent calls skip Genie and re-run the stored SQL directly. This prevents wasteful API calls.

**GENIE_LOW_CONFIDENCE fields** have a stored SQL template that returned NULL — the data is not in Databricks (e.g. ACT/SAT score distributions, detailed housing costs, campus life checkboxes). Re-running Genie on these will not help. Use Analyst SQL Mapping or manual entry for these.

---

### Step 4 — Analyst SQL Mapping (for fields Genie can't resolve)

When Genie can't find data, an analyst can provide custom SQL:

1. In the `/pdf-ops` UI, click **"Analyst SQL Mapping"**
2. Enter a name (e.g. "Section B — Test Scores") and paste your SQL query
3. Set Survey Year and click **Preview** — this runs the SQL against Databricks and shows the result table
4. Click **Auto Map** — Claude Sonnet maps result columns to PDF field candidates automatically
5. Review the proposed mappings in the draft table
6. Click **Approve** on each correct mapping
7. Click **Rerun Approved** to re-execute the SQL and write values into candidates for the current survey year

**Next year:** approved mappings are stored permanently. To refresh for a new year, change the survey year and click "Rerun Approved" — no need to re-paste SQL or re-run the AI mapping.

API endpoints for this workflow:
- `POST /pdf-scans/{scan_id}/analyst-sql/preview` — preview SQL, returns result table
- `POST /analyst-sql/{query_id}/auto-map` — AI maps columns to candidates
- `POST /analyst-sql-mapping-drafts/{draft_id}/approve` — approve a mapping
- `POST /analyst-sql/{query_id}/rerun-approved` — refresh values for a new year

---

### Step 5 — Export filled PDF

Click **"Export Filled PDF"**. The system writes resolved values into the original PDF using field names and downloads it.

API: `POST /pdf-scans/{scan_id}/export-filled-pdf`
Download: `GET /pdf-exports/{filename}`

---

### Year-over-year re-use

Once a scan is resolved:
- Registry queries run fresh every year (SQL is parameterized by survey year/term)
- Genie-generated SQL is stored — use `resolve-direct` to re-run without calling Genie
- Analyst SQL mappings are stored — use `rerun-approved` to refresh values

For next year's CDS survey: upload the new PDF, run the registry, then run `resolve-direct` (not `resolve-via-genie`) to re-use all stored SQL templates.

---

## Code changes — Docker deployment

**IMPORTANT:** The control plane container (`infra-control-plane-1`) is NOT volume-mounted. Code is copied at build time. To deploy code changes without a full rebuild:

```bash
# Copy changed files into the running container:
docker cp apps/api/service.py infra-control-plane-1:/app/apps/api/service.py
docker cp apps/api/cds_query_registry.py infra-control-plane-1:/app/apps/api/cds_query_registry.py

# Restart to load changes:
docker compose -f infra/docker-compose.yml restart control-plane
```

**Container restarts wipe all docker-cp changes.** After any restart (or crash + auto-restart), re-copy your local files before running workflows.

For a full rebuild (picks up all changes, including new dependencies):
```bash
docker compose -f infra/docker-compose.yml up --build -d control-plane
```

---

## Prerequisites

- Docker Desktop running or Podman installed and the Podman machine started
- `.env` file configured (copy `.env.example`, fill in the blanks — see section below)
- For CDP mode: Microsoft Edge or Chrome installed

---

## First-time setup

### 1. Configure `.env`

```bash
cp .env.example .env
```

Required values:

| Variable | Where to get it |
|---|---|
| `DATABRICKS_HOST` | Your Databricks workspace URL |
| `DATABRICKS_CLIENT_ID` | Service principal client ID |
| `DATABRICKS_CLIENT_SECRET` | Service principal secret |
| `DATABRICKS_SQL_WAREHOUSE_ID` | SQL Warehouse ID from Databricks UI |
| `SKYVERN_API_KEY` | Any string, e.g. `local-dev-key-123` |
| `OPENAI_API_KEY` | Databricks OAuth token (run `python infra/scripts/print_model_serving_token.py`) |

Browser mode — choose one:

```bash
# Option A: CDP (watch Skyvern in your browser — recommended for dev)
BROWSER_TYPE=cdp-connect
BROWSER_REMOTE_DEBUGGING_URL=http://192.168.65.254:9222/

# Option B: CDP on Podman machine
# BROWSER_TYPE=cdp-connect
# BROWSER_REMOTE_DEBUGGING_URL=http://host.containers.internal:9222/

# Option C: Headless Chromium inside Docker or Podman (no browser window)
BROWSER_TYPE=chromium-headful
# Comment out BROWSER_REMOTE_DEBUGGING_URL
```

### 2. Start the stack

```bash
docker compose -f infra/docker-compose.yml up --build -d
# or: podman compose -f infra/docker-compose.yml up --build -d
```

Wait ~30 seconds, then verify:

```bash
curl -s http://localhost:8010/health
# Expected: {"status":"ok","control_plane_db":"sqlite","database_url":"..."}

curl -s http://localhost:8088 | grep "Survey Portal"
# Expected: line containing "Survey Portal"
```

### 3. Create Python virtualenv (for running scripts locally)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

---

## Website Ops running modes

There are three ways to trigger a website form fill. Pick one.

---

### Mode 1: `/website-ops` Web UI (easiest)

Open **http://localhost:8010/website-ops** in your browser. `/ops` remains available as an older alias.

1. Set Form URL: `http://localhost:8088/?realData=1`
2. Leave Survey Year blank (uses latest Fall term from Databricks)
3. Check "Validate pulled data before fill"
4. Click **Start Workflow**

What happens:
1. Pulls real enrollment/admissions data from Databricks → writes `fake-survey-form/fake-survey-form-data.json`
2. Sends one Skyvern task to fill all 9 sections of the fake form
3. Job status updates live in the table below the button

Jobs **persist across restarts** — you can close the browser and come back.

**Expected output when complete:**
```json
{
  "status": "completed",
  "steps": [
    {"name": "pull_real_data", "status": "completed", ...},
    {"name": "run_full_fill",  "status": "completed", ...}
  ],
  "result": {
    "run_full_fill": {"run_id": "tsk_...", "status": "completed", "step_count": 18}
  }
}
```

---

### Mode 2: Terminal scripts (most control)

**Step 1 — Start Edge with CDP** (skip if using headless mode):

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

Verify Edge is reachable:
```bash
curl -s http://127.0.0.1:9222/json/version | python3 -m json.tool | grep Browser
# Expected: "Browser": "Edg/148..."
```

Verify Skyvern container can reach Edge:
```bash
docker exec infra-skyvern-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://192.168.65.254:9222/json/version', timeout=5).status)"
# If using Podman, use:
# podman exec infra-skyvern-1 python3 -c "import urllib.request; print(urllib.request.urlopen('http://host.containers.internal:9222/json/version', timeout=5).status)"
# Expected: 200
```

**Step 2 — Pull Databricks data:**

```bash
cd /Users/harshas/code/csulb/IRA/survey-agent/survey-automation-project/survey-automation

.venv/bin/python infra/scripts/pull_real_fake_form_data.py --validate
```

Expected output:
```json
{
  "survey_year": 2025,
  "term": "4",
  "applied_total": "88341",
  "enrolled_total": "5884",
  "total_undergraduates": "36703",
  "total_graduates": "5471",
  "grand_total_enrollment": "42174",
  "validated": true
}
```

If Databricks is unavailable, skip this step — the previous `fake-survey-form-data.json` is reused.

**Step 3 — Run Skyvern fill:**

```bash
.venv/bin/python infra/scripts/run_website_form_fill.py \
  --url "http://localhost:8088/?realData=1" \
  --timeout-seconds 1800
```

Options:
```
--url              Target form URL (default: http://fake-form/?realData=1)
--timeout-seconds  Max wait time in seconds (default: 900, use 1800 for safety)
--max-steps        Skyvern planning steps (default: 80)
--browser-session-id  Reuse an existing Skyvern session (optional)
```

Expected output when done (~15–30 min):
```json
{
  "run_id": "tsk_53...",
  "status": "completed",
  "step_count": 18,
  "output": {"fill_summary": "{\"filled_count\": 95, \"submit_attempted\": false}"}
}
```

---

### Mode 3: Temporal workflow (section-by-section, for real portal)

This is the intended production path. It runs scan → validate → fill → post-fill validate per section.

**Step 1 — Create a run:**
```bash
curl -s -X POST http://localhost:8010/runs \
  -H "Content-Type: application/json" \
  -d '{"run_id":"run_001","survey_id":"usnews_main","survey_year":2025}'
```

**Step 2 — Start the Temporal workflow:**
```bash
curl -s -X POST http://localhost:8010/runs/run_001/start-workflow \
  -H "Content-Type: application/json" \
  -d '{
    "section_id": "institution",
    "portal_url": "http://localhost:8088/?realData=1",
    "workflow_mode": "draft_fill",
    "callback_timeout_seconds": 1800
  }'
```

**Step 3 — Monitor:**
```bash
# Watch events live
curl -s http://localhost:8010/runs/run_001/events | python3 -m json.tool

# Check review items (mismatches)
curl -s http://localhost:8010/runs/run_001/review-items | python3 -m json.tool

# Check metrics
curl -s http://localhost:8010/runs/run_001/metrics | python3 -m json.tool
```

Expected metrics output:
```json
{
  "tasks_total": 3,
  "tasks_fill": 1,
  "tasks_validate": 2,
  "iteration_failure_count": 0,
  "split_rate_percent": 0
}
```

Open **http://localhost:8233** (Temporal UI) to watch the workflow graph.

---

## Switching browser modes

### From CDP to headless (no browser window)

1. Edit `.env`:
   ```bash
   BROWSER_TYPE=chromium-headful
   # Comment out: BROWSER_REMOTE_DEBUGGING_URL=...
   ```

2. Restart only Skyvern:
   ```bash
  docker compose -f infra/docker-compose.yml up -d skyvern
  # or: podman compose -f infra/docker-compose.yml up -d skyvern
   ```

3. Run normally. Skyvern uses its own internal Chromium — no Edge needed.

### From headless back to CDP

1. Edit `.env`:
   ```bash
   BROWSER_TYPE=cdp-connect
  BROWSER_REMOTE_DEBUGGING_URL=http://192.168.65.254:9222/
  # If using Podman instead, use: http://host.containers.internal:9222/
   ```

2. Start Edge (see Mode 2, Step 1 above).

3. Restart Skyvern:
   ```bash
  docker compose -f infra/docker-compose.yml up -d skyvern
  # or: podman compose -f infra/docker-compose.yml up -d skyvern
   ```

---

## Why CDP is slower than headless

When using CDP (connecting to your local Edge), every Playwright action crosses:

```
Skyvern container → runtime networking → your Mac → Edge
```

This adds ~1–3s per action on top of the base ~5s LLM inference cost.

With internal headless Chromium (`BROWSER_TYPE=chromium-headful`), Playwright runs inside the Skyvern container — no network hop. Same form fill runs faster. Use CDP when you want to watch what's happening; use headless for speed.

---

## Performance reference

From actual runs on this codebase:

| Metric | CDP mode | Headless (expected) |
|---|---|---|
| Per-action gap | ~8s | ~5-6s |
| Full form (9 sections) | ~27 min | ~15-20 min |
| LLM calls per section | 2–3 (planner) | same |
| Actions per section | 2–33 | same |

The bottleneck is LLM inference time (~6–10s per step), not network or JS.

---

## Useful URLs

| URL | What it is |
|---|---|
| http://localhost:8010/pdf-ops | PDF workflow console — scan, resolve, analyst SQL, export |
| http://localhost:8010/website-ops | Website workflow console — start Skyvern fill jobs |
| http://localhost:8010/ops | Compatibility alias for website-ops |
| http://localhost:8010/health | Control plane health check |
| http://localhost:8010/docs | FastAPI auto-docs for all API endpoints |
| http://localhost:8088/?realData=1 | Fake survey form (served by nginx) |
| http://localhost:8000 | Skyvern API |
| http://localhost:8080 | Skyvern UI (task history, screenshots) |
| http://localhost:8233 | Temporal UI (workflow graph) |
| http://localhost:7233 | Temporal gRPC endpoint |

---

## Diagnostics

### Check PDF resolution status
```bash
sqlite3 data/control_plane.db "SELECT status, count(*) FROM survey_pdf_datapoint_candidates WHERE scan_id='pdfscan_bb676d3416e3' GROUP BY status;"
```

### Check which fields are still unresolved
```bash
sqlite3 data/control_plane.db "SELECT field_name, label_text, status FROM survey_pdf_datapoint_candidates WHERE scan_id='pdfscan_bb676d3416e3' AND status != 'GENIE_RESOLVED' LIMIT 20;"
```

### Check Genie call history (audit trail)
```bash
sqlite3 data/control_plane.db "SELECT field_name, genie_prompt_chars, response_status, created_at FROM genie_api_call_history ORDER BY created_at DESC LIMIT 20;"
```

### Check Databricks connectivity
```bash
curl -s http://localhost:8010/integrations/databricks | python3 -m json.tool
```

### Check all containers are healthy
```bash
docker compose -f infra/docker-compose.yml ps
# or: podman compose -f infra/docker-compose.yml ps
```
All should show `healthy` or `running`. `skyvern-postgres` must be `healthy` before `skyvern` starts.

### Check Skyvern task history
Open http://localhost:8080 — shows all tasks with screenshots and step-by-step replay.

### Check cached token usage
```bash
docker exec infra-skyvern-postgres-1 psql -U skyvern -d skyvern -tAc "
SELECT s.order, s.input_token_count, s.cached_token_count, s.last_llm_model
FROM steps s ORDER BY s.created_at DESC LIMIT 20;"
# or: podman exec infra-skyvern-postgres-1 psql -U skyvern -d skyvern -tAc "..."
```
`cached_token_count > 0` on steps with `order > 0` = prompt caching working (faster LLM calls).

### Check action timing for last run
```bash
docker exec infra-skyvern-postgres-1 psql -U skyvern -d skyvern -tAc "
SELECT a.action_order, a.action_json->>'text' as value,
       ROUND(EXTRACT(EPOCH FROM (LEAD(a.created_at) OVER (ORDER BY a.action_order) - a.created_at)), 2) as gap_sec
FROM actions a
JOIN steps s ON s.step_id = a.step_id
JOIN tasks t ON t.task_id = s.task_id
WHERE t.task_id = (SELECT task_id FROM tasks ORDER BY created_at DESC LIMIT 1)
ORDER BY a.action_order;"
# or: podman exec infra-skyvern-postgres-1 psql -U skyvern -d skyvern -tAc "..."
```

### View Databricks pull script output
```bash
docker exec infra-control-plane-1 python3 /app/infra/scripts/pull_real_fake_form_data.py --validate
# or: podman exec infra-control-plane-1 python3 /app/infra/scripts/pull_real_fake_form_data.py --validate
```

### Reset fake form to empty
Open http://localhost:8088, click **Reset** in the sidebar.

### Wipe all job history and start fresh
```bash
docker exec infra-control-plane-1 python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/control_plane.db')
conn.execute('DELETE FROM workflow_jobs')
conn.execute('DELETE FROM run_events')
conn.execute('DELETE FROM skyvern_tasks')
conn.execute('DELETE FROM runs')
conn.commit()
print('Cleared')
"
```

---

## Common errors

| Error | Cause | Fix |
|---|---|---|
| `FileNotFoundError: /app/artifacts/...` | Artifacts volume not mounted | `docker compose up -d control-plane` |
| Genie resolve produces 0 new fields | All remaining candidates already have `genie_sql_template` (GENIE_LOW_CONFIDENCE) | Use Analyst SQL Mapping for these fields |
| `GENIE_LOW_CONFIDENCE` after Genie run | Genie generated SQL but it returned NULL | Data not in Databricks — use Analyst SQL Mapping or manual entry |
| Resolved count stuck after registry run | Fields without matching SQL templates can't be resolved by registry alone | Run Genie resolve or Analyst SQL Mapping |
| Registry values are wrong | `cds_query_registry.py` was edited but not redeployed | Re-run `docker cp` to push updated file into container, then restart |
| Container restarted and edits are gone | `docker cp` changes don't survive restarts | Re-copy all edited files after every container restart |
| Auto-map produces no drafts | Section filter too narrow, or SQL result columns don't match field labels | Try without section filter, or check SQL output column names |
| `connect ECONNREFUSED 192.168.65.254:9222` | Edge not running with CDP on Docker Desktop | Run the Edge CDP launch command above |
| `connect ECONNREFUSED host.containers.internal:9222` | Edge not running with CDP on Podman | Run the Edge CDP launch command above |
| `SKYVERN_API_KEY is not configured` | Missing `.env` value | Add `SKYVERN_API_KEY=any-string` to `.env` |
| `Skyvern run finished with non-completed status: failed` | Skyvern hit max steps | Lower `SKYVERN_MAX_FIELDS_PER_TASK` in `.env`, or increase `--max-steps` |
| `maximum of 50 planning iterations` | Task too complex | Reduce fields per task — set `SKYVERN_MAX_FIELDS_PER_TASK=5` in `.env` |
| Job shows `failed` in `/website-ops` | See job detail for `stderr` | Click job ID in the run history table to see full error |
| `cached_token_count` always 0 | Databricks proxy strips cache headers | Known issue — Databricks Model Serving may not support Anthropic cache_control |

---

## What each script does

| Script | Purpose |
|---|---|
| `infra/scripts/pull_real_fake_form_data.py` | Queries Databricks (`erss`, `ersa` tables), applies gender bucket logic, writes `fake-survey-form-data.json` |
| `infra/scripts/run_website_form_fill.py` | Sends one Skyvern task to fill a website survey form from resolved JSON values |
| `infra/scripts/run_full_fake_form_fill.py` | Compatibility wrapper for older fake-form smoke-test commands |
| `infra/scripts/validate_fake_form_data.py` | Checks internal consistency of the JSON (totals match components) |
| `infra/scripts/databricks_openai_proxy.py` | OAuth proxy — refreshes Databricks tokens so Skyvern can call Claude via Databricks Model Serving |
| `infra/scripts/print_model_serving_token.py` | Prints a short-lived Databricks OAuth token for use in `.env` |
| `infra/scripts/probe_databricks_sources.py` | Checks which Databricks tables are accessible |

---

## Repository layout

```
survey-automation/
├── apps/
│   ├── api/
│   │   ├── main.py                  # FastAPI routes + HTML pages (/pdf-ops, /website-ops)
│   │   ├── service.py               # Core logic: scan, Genie resolve, direct resolve, export
│   │   ├── analyst_sql_mapping.py   # Analyst SQL mapping service (preview, auto-map, approve)
│   │   ├── cds_query_registry.py    # CDS field → Databricks SQL registry (pre-built templates)
│   │   ├── schemas.py               # Pydantic request/response models
│   │   ├── settings.py              # Config loaded from .env
│   │   └── db/
│   │       ├── models.py            # SQLAlchemy ORM (all tables)
│   │       ├── engine.py            # DB engine setup
│   │       └── session.py           # SessionLocal factory
│   ├── skyvern_worker/
│   │   ├── skyvern_client.py        # HTTP client for Skyvern API
│   │   └── task_builder.py          # Builds Skyvern task prompts per section
│   └── temporal_worker/
│       ├── workflows.py             # Temporal workflow definitions
│       ├── activities.py            # Temporal activities
│       ├── signaler.py              # Sends signals to running workflows
│       └── types.py                 # Dataclasses for activity inputs/outputs
├── infra/
│   ├── docker-compose.yml           # Full stack definition
│   └── scripts/                     # Data pull, token, and fill scripts
├── fake-survey-form/                # Static HTML form for smoke testing
├── tests/                           # Pytest unit tests
├── data/                            # SQLite control plane DB (gitignored)
├── OPERATIONS.md                    # This file
└── HANDOFF.md                       # Engineering journal (session-by-session history)
```
