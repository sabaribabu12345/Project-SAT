# Constraints and Non-Negotiables

## Primary constraints

### C1. No external LLM egress for survey data

Survey source data, computed values, evidence, portal DOM snapshots, and Skyvern reasoning traces must not leave the approved boundary.

Allowed:
- Databricks Model Serving (Llama 3, DBRX, or any Databricks-hosted endpoint).
- Self-hosted model endpoints inside the same network boundary as Skyvern.

Forbidden:
- OpenAI, Anthropic, Google, Mistral hosted APIs receiving survey payloads.
- Skyvern Cloud (managed SaaS) for any non-public portal.
- Third-party vision/OCR SaaS (Skyvern's built-in vision path via an approved model only).

### C2. Human review before fill; human sign-off before submit

The workflow must support:
- Analyst review of validation failures and variance holds before a Skyvern fill task is dispatched.
- Analyst review of Skyvern's filled screenshots before the final submit action.
- Signed release recorded in the control plane before Skyvern is allowed to press "Submit".

### C3. Deterministic correctness for values

All structured and numeric field values are computed in Databricks and passed into Skyvern as **data**, not derived by Skyvern. Skyvern may interpret goals ("go to the Enrollment section"), but never values ("what should total undergraduates be?").

### C4. Visual validation after every write

Every Skyvern `fill` action is immediately followed by a `validate` step that reads back the field from the page and compares to the target value. Mismatches block the section and enqueue a review item.

### C5. Idempotent, restartable runs

A run must be resumable from the last section checkpoint. Skyvern tasks are scoped per section, and Temporal persists the run's position. A failed fill does not corrupt earlier sections.

## Engineering non-negotiables

1. No business logic inside Skyvern task prompts (no "if variance > 10% then hold").
2. No final submit without `submit_released_flag = true` in the control plane.
3. Every Skyvern action emits an audit event keyed to `run_id`, `section_id`, `field_id`.
4. Every Skyvern screenshot and trace URL is stored in the audit trail.
5. Every validation failure is reviewable before retry.
6. Every portal credential is fetched from a secrets store at runtime — never embedded in Skyvern task payloads, Temporal workflow inputs, or git.

## Design implications

- Skyvern is a **thin execution layer**. Task definitions are generated from Databricks metadata, not hand-authored per portal.
- Temporal owns state durability; Skyvern's task queue is an implementation detail.
- SQLite is the phase-1 operational DB for runs, events, tasks, and review items. Databricks is the analytical and authoritative DB for values and evidence.
- The control plane service never trusts Skyvern's output as authoritative — it reconciles against the Databricks payload.
