# Survey Automation Platform

A system for automating CSULB's annual survey submissions. It handles two distinct workflows:

1. **PDF Ops** — scan a survey PDF, resolve field values from Databricks (via SQL registry + Databricks Genie AI), and export a filled PDF.
2. **Website Ops** — fill an online survey portal using Skyvern AI browser automation.

Both workflows are driven from a single web UI at `http://localhost:8010`.

---

## Core stack

| Component | Role |
|---|---|
| **FastAPI** | Control plane HTTP API (`apps/api/`) |
| **SQLite** | Control plane state (`data/control_plane.db`) |
| **Databricks** | Source of truth for all institutional data |
| **Databricks Genie AI** | Generates SQL for PDF field resolution |
| **Skyvern** | AI browser agent for website form fill |
| **Temporal** | Orchestrates multi-step Skyvern workflows |
| **Docker Compose** | Runs the full stack locally |

---

## Repository layout

```
survey-automation-project/
├── survey-automation/       # Main implementation (FastAPI, Skyvern, Temporal)
│   ├── apps/api/            # FastAPI control plane
│   ├── infra/               # Docker Compose + setup scripts
│   ├── tests/               # Pytest unit tests
│   ├── data/                # SQLite DB (gitignored)
│   ├── README.md            # Setup + quick-start guide
│   ├── OPERATIONS.md        # Full operations and workflow guide
│   └── HANDOFF.md           # Engineering journal (session-by-session changes)
├── fake-survey-form/        # Static HTML form for smoke testing
├── docs/                    # Architecture docs and ADRs
└── cds_sql_query_registry.md  # Reference: CDS field → Databricks SQL mapping
```

**Start here:** `survey-automation/OPERATIONS.md` — covers setup, both workflows, diagnostics, and common errors.

---

## Two main workflows

### PDF Ops (`/pdf-ops`)
1. Upload the survey PDF → scan extracts all form fields
2. Registry run resolves known fields via pre-built SQL templates (CDS query registry)
3. Genie run resolves remaining fields — Genie AI generates SQL, stores the template
4. Analyst SQL Mapping — analyst pastes custom SQL, maps it to specific fields
5. Export filled PDF

### Website Ops (`/website-ops`)
1. Skyvern AI fills a web survey form section-by-section
2. Data comes from Databricks (same source as PDF Ops)
3. Human-in-the-loop: validate before submit

---

## Governance model

- No external LLM APIs for survey data — all inference runs on Databricks Model Serving
- No automated form submit — human must explicitly approve
- No credentials or survey output in version control