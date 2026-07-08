# Analyst SQL Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let analysts bring readable Databricks SQL, preview result rows, get Claude Sonnet 4.6 mapping proposals to PDF fields, approve those mappings, and rerun saved mappings to fill resolved values automatically.

**Architecture:** Add a small analyst-SQL layer beside the existing PDF Genie/CDS registry flow. SQL query records store the analyst SQL and preview shape; mapping draft records store model proposals pending review; approved mapping records store deterministic field-to-result extraction rules for future reruns. Approved reruns write values into existing PDF candidate resolved-value columns so PDF export and review stay unchanged.

**Tech Stack:** FastAPI, SQLAlchemy/SQLite, existing Databricks SQL reader, Databricks model serving client, inline HTML/JS operator pages, pytest/TestClient.

---

### Task 1: Database Shape And API Tests

**Files:**
- Modify: `survey-automation/apps/api/db/models.py`
- Modify: `survey-automation/apps/api/main.py`
- Test: `survey-automation/tests/test_analyst_sql_mapping.py`

- [ ] **Step 1: Write failing API tests**

Create tests that prove:
- `POST /pdf-scans/{scan_id}/analyst-sql/preview` saves SQL and returns columns/sample rows.
- `POST /analyst-sql/{query_id}/auto-map` creates pending drafts from a fake model mapper.
- `POST /analyst-sql-mapping-drafts/{draft_id}/approve` approves a draft and writes the target candidate value.
- `POST /analyst-sql/{query_id}/rerun-approved` reruns saved SQL and refreshes values without calling the model.

- [ ] **Step 2: Verify RED**

Run:

```bash
rtk .venv-test/bin/pytest tests/test_analyst_sql_mapping.py -q
```

Expected: fails because the endpoints/models do not exist.

### Task 2: Minimal Backend Implementation

**Files:**
- Modify: `survey-automation/apps/api/db/models.py`
- Modify: `survey-automation/apps/api/main.py`
- Modify: `survey-automation/apps/api/schemas.py`
- Create: `survey-automation/apps/api/analyst_sql_mapping.py`

- [ ] **Step 1: Add models**

Add:
- `AnalystSqlQuery`
- `AnalystSqlMappingDraft`
- `AnalystSqlFieldMapping`

- [ ] **Step 2: Add startup migration**

Use the existing `PRAGMA table_info` migration style in `main.py` to create the three tables for local SQLite.

- [ ] **Step 3: Add service helpers**

Implement:
- SQL preview using `DatabricksSqlValueReader.query_rows`.
- deterministic value extraction from row index and value column.
- model proposal adapter that defaults to a local heuristic but can be monkeypatched in tests and later wired to Databricks Serving Sonnet 4.6.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
rtk .venv-test/bin/pytest tests/test_analyst_sql_mapping.py -q
```

Expected: passes.

### Task 3: Analyst UI

**Files:**
- Modify: `survey-automation/apps/api/main.py`
- Test: `survey-automation/tests/test_pdf_ops_page.py`

- [ ] **Step 1: Add failing UI assertions**

Assert `/pdf-ops` includes:
- `Analyst SQL Mapping`
- `/analyst-sql/preview`
- `/auto-map`
- `Ready for review`
- `Rerun approved SQL`

- [ ] **Step 2: Implement UI section**

Add a clear nontechnical UI section to `/pdf-ops` with:
- SQL textbox
- preview button
- auto-map button
- pending drafts table
- approve action
- rerun approved SQL button
- resolved/ready counters

- [ ] **Step 3: Verify UI tests**

Run:

```bash
rtk .venv-test/bin/pytest tests/test_pdf_ops_page.py -q
```

Expected: passes.

### Task 4: Documentation And Full Verification

**Files:**
- Modify: `survey-automation/HANDOFF.md`
- Modify: `docs/15-pdf-survey-datapoint-scanning.md`

- [ ] **Step 1: Document workflow**

Explain that analyst SQL files are not committed; saved SQL mappings live in the control-plane DB and rerun directly after approval.

- [ ] **Step 2: Full tests**

Run:

```bash
rtk .venv-test/bin/pytest -q
rtk proxy git add -n .
```

Expected: all tests pass; no local SQL reference files are staged.
