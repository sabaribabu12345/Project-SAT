# Fake Survey Portal Test Form

This is a static HTML survey portal built for browser automation testing.

## What is included

- Multi-section survey form with portal-style navigation
- Prefilled sample answers based on the uploaded survey questions/answers reference
- Stable `data-testid` selectors for automation
- Save Draft, autosave, Export JSON, Reset
- Assessment section with computed checks
- Verification section with fake review and submit gates
- Final submit button that stays disabled until validation conditions are met

## Files

- `index.html` — main fake survey UI
- `styles.css` — styling
- `app.js` — client-side logic, autosave, fake validation and submit flow
- `fake-survey-form-data.example.json` — sanitized baseline payload for local smoke tests

`fake-survey-form-data.json` is a generated local file and is ignored by Git. The Databricks pull script writes that file when real values are pulled for a run.

## How to run

### Option 1: open directly

Open `index.html` in a browser.

### Option 2: serve locally

Python:

```bash
cd fake-survey-form
python3 -m http.server 8000
```

Then open:

```text
http://localhost:8000
```

## Useful selectors

Examples:

- `data-testid="institution-name"`
- `data-testid="applied-total"`
- `data-testid="grand-total-enrollment"`
- `data-testid="student-faculty-ratio"`
- `data-testid="alumni-donors"`
- `data-testid="assessment-list"`
- `data-testid="verification-accuracy"`
- `data-testid="final-submit"`

## Notes

This is a representative fake survey, not a full 1:1 recreation of the source survey.
It is designed to help test:

- navigation
- field filling
- table inputs
- checkboxes/radio buttons
- validation logic
- state transitions
- review and submit flows

If you want, this can be extended into:

- a much larger full-survey mock
- a multi-page app
- a backend-powered test portal
- randomized datasets per run

## Automation architecture (Databricks + browser)

The repo ships an **agent-shaped** CLI:

1. **Plan** — Default **`auto`**: if you pass `--json`, columns are **HTML ∩ JSON keys** (good when you have a sample export). With **`--plan-mode html-all`** (and no schema JSON required for SQL-backed runs), targets **every** `name=` in the HTML so dynamic survey shapes and `SELECT *` rows only supply keys that exist in the row.
2. **Data plane** — `survey-form-agent fill --source json-file` reads the JSON row. `--source databricks-mcp-sql` uses **Databricks Managed MCP (SQL)** with `DATABRICKS_SURVEY_ROW_SQL` returning **one row** (any columns; the plan + `slice_row` pick what maps to the form).
3. **Browser plane** — **`--executor playwright`** (default): deterministic fills, no LLM. **`--executor browser-use`** (optional `pip install -e ".[browser-use]"`): LLM-driven automation per [Browser Use](https://docs.browser-use.com/quickstart); policy-sensitive because the task includes payload values.

Commands (from `survey-automation-plan-v2/survey-automation` after `pip install -e ".[worker]"`):

```bash
survey-form-agent plan
survey-form-agent plan --plan-mode html-all
survey-form-agent fill --headed
survey-form-agent fill --plan-mode html-all --source databricks-mcp-sql
```

HTML tweak: total cells for graduation cohort **C / G / rate** now expose `name="grad_c_total"`, `name="grad_g_total"`, and `name="grad_rate_total"` so assessment JS and automation stay aligned.
