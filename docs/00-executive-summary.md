# Executive Summary

## Goal

Replace the brittle selector-based Playwright worker in `survey-automation-plan-v2/` with a **Skyvern-driven agentic browser worker**, without weakening the deterministic correctness and human-in-the-loop guarantees of the existing design.

## One-line architecture

**Databricks computes truth, Temporal controls run state, Skyvern executes portal actions against a goal-based task contract, SQLite stores phase-1 operational control-plane state, and humans retain approval authority before every final submit.**

## Why Skyvern

University survey portals (US News Academic Insights, Peterson's, Tableau-based dashboards, SSO-gated admin tools) change DOM, labels, and field ordering every cycle. A deterministic Playwright worker with `data-testid` selectors is not a realistic production target across dozens of institutions. Skyvern's vision-grounded agent model absorbs those UI changes by reasoning about goals and labels, not selectors.

We pay for this with:

- an LLM in the loop for navigation and field mapping
- a mandatory **visual validation** step after every write (ADR-004)
- stricter governance on what Skyvern is allowed to do autonomously

Skyvern **never** decides values. Values come from the Databricks-computed payload. Skyvern is told *what to enter*; it figures out *where to enter it*.

## High-confidence decisions

1. **Databricks remains the system of record** (ADR-002). All numeric answers are computed deterministically in Databricks. Skyvern is a field mapper and form filler, not a calculator.
2. **No external LLM egress** (ADR-001). Skyvern is configured to use **Databricks Model Serving** via the OpenAI-compatible endpoint. The Skyvern deployment must not have outbound access to hosted LLM APIs.
3. **Skyvern replaces the production browser worker** (ADR-003). Playwright remains available only as a local smoke-test harness for the `fake-survey-form/` sandbox.
4. **Every Skyvern write is followed by a visual validation step** (ADR-004). Validation failures park the run in `BLOCKED` for analyst review.
5. **Temporal owns run durability** (ADR-005). Skyvern's own worker queue is used for individual browser tasks, but the survey run lifecycle (prepare, review, fill, submit) is a Temporal workflow.
6. **SQLite is the phase-1 control-plane operational store** (ADR-006). The in-memory repository in v2 is replaced. Databricks remains authoritative for survey values. Revisit the store only when SQLite no longer fits the operational workload.

7. **The first real survey is CSULB's U.S. News Best Colleges Main Survey.** Field catalog, query catalog, external-data-request tracking, and rankings-critical validation rules are modeled directly on the CSULB IR&A playbook (see `docs/12-usnews-main-survey-playbook-mapping.md`).
8. **Final submit is always human-gated.** Skyvern is not allowed to press the portal's final submit button without a signed `submit_released_flag` in the control plane.

## What to build first

A vertical slice on the existing `fake-survey-form/`:

1. Temporal workflow for a single run.
2. SQLite schema for runs, events, tasks, review items.
3. Databricks job producing a portal payload for one section.
4. Skyvern task builder that converts a payload into a goal-based task.
5. Skyvern worker running against `fake-survey-form/`.
6. Visual validation loop wired to Skyvern's observer step.
7. Analyst review UI (server-rendered) showing screenshots + diffs.
8. Submit release gate wired to a separate Temporal signal.

## What to defer

- Multi-survey template onboarding wizard.
- Automatic locator self-healing (Skyvern absorbs most of this anyway).
- SSO flows with TOTP (tackle after the first internal portal).
- Streaming live video to the analyst UI (use screenshot gallery first).

## Non-goals

- Recreating `apps/survey_agent/` (the Playwright/browser-use CLI from v2) as a production path. It stays as a dev-only smoke harness.
- Letting Skyvern invent field values, tolerances, or validation outcomes.
- Letting the LLM decide when a hold can be waived.
