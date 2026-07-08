# ADR-002: Databricks as System of Record

## Status
Accepted

## Context
Authoritative survey data — source snapshots, computed values, validation results, evidence — must live in a governed analytical store. SQLite is used for phase-1 operational control-plane state, but is not the source of truth for answers. The existing Databricks platform remains the preferred place to centralize governed data.

## Decision
Databricks (Delta tables under Unity Catalog) is the system of record for:
- source snapshots per run
- survey metadata (`survey_catalog`, `survey_section_catalog`, `survey_field_catalog`)
- `survey_computed_values`
- `survey_validation_results`
- `survey_portal_payload`
- evidence and Skyvern artifact bundles (UC Volumes)

The control-plane operational store holds only:
- run lifecycle state
- audit events
- review queue
- Skyvern task tracking
- feature flags
- secret references

## Consequences
- The control plane must reconcile Skyvern outputs against Databricks payloads, not trust them.
- Databricks jobs own prepare and publish-payload steps; Temporal activities call them via the Databricks SDK.
- Replaying a run fetches authoritative values from Databricks, not from the control-plane store.
