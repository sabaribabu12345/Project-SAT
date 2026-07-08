# ADR-004: Mandatory Visual Validation After Every Write

## Status
Accepted

## Context
Vision-based field mapping can place the right value in the wrong field. Without a readback step, a mismatch is only caught after submission, which is unrecoverable for many portals.

## Decision
Every Skyvern `fill` task for a section is immediately followed by a Skyvern `validate` task that re-opens the section and reads back each filled field. The control plane compares each readback to the dispatched `target_value` using the per-field `skyvern_readback_tolerance`. Any mismatch:

1. Appends a `SKYVERN_VALIDATION_MISMATCH` event.
2. Creates a `review_items` row with the field id, expected value, observed value, and screenshot URI.
3. Parks the Temporal workflow in `BLOCKED`.

The workflow resumes only after an analyst resolves the review item (either by correcting the portal manually, re-dispatching the fill, or overriding with documented justification).

## Consequences
- Every section touches the portal twice (fill + validate), doubling browser time per section.
- Validation tasks are cheaper to rerun than fill tasks; auto-retry for validate is permitted once.
- The analyst UI must surface mismatches clearly with before/after screenshots.
- Rejected fields block the whole section from proceeding to avoid cascading errors.

## Rejected alternatives
- **DOM-only readback inside the fill task** — collapses the two phases but loses the fresh-eyes check; if the fill model misidentified a field, the readback in the same reasoning context will often repeat the error.
- **Sample-based validation** — validating only N% of fields — unacceptable given the cost of a wrong submission.
