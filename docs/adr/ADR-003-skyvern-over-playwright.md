# ADR-003: Skyvern Over Playwright for Production Browser Work

## Status
Accepted (supersedes v2 ADR-003)

## Context
The v2 architecture used Playwright with `data-testid` selectors as the production worker and Browser MCP for interactive debugging. In practice, real survey portals (Academic Insights, Peterson's, Tableau-based admin dashboards) change DOM structure, field ordering, and label wording between cycles. Maintaining selector packs per portal per year is the dominant cost driver.

Skyvern is a self-hostable browser agent that uses vision and label reasoning to locate fields from goal-based prompts. It absorbs DOM drift at the cost of an LLM in the loop.

## Decision
Use Skyvern as the production browser automation runtime. Playwright is retained only as a local smoke-test harness against `fake-survey-form/`, not as a production execution path.

## Consequences
- Selector packs are replaced by goal hints attached to the field catalog.
- An LLM is on the critical path; a model outage blocks fills. Handled by a runbook and fallback to manual entry.
- Every Skyvern fill must be paired with a visual validation task (ADR-004) because vision-based field mapping can misfire.
- Prompt quality becomes a first-class engineering concern. Changes to `skyvern_goal_hint` go through code review.
- Skyvern's encrypted parameter feature is required to avoid credentials in task payloads.

## Rejected alternatives
- **Continue Playwright** — selector maintenance cost is unacceptable at the planned portal count.
- **Hybrid Playwright-first, Skyvern fallback** — two code paths for the same work; complexity without material benefit once Skyvern is trusted.
- **Skyvern Cloud** — violates ADR-001 (no external egress of survey data).
