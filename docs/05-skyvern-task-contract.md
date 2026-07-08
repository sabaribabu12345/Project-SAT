# Skyvern Task Contract

## Principle

Skyvern receives **goals and values**. It never computes values, never decides validity, and never presses submit without an explicit `submit` task dispatched after a signed release.

Tasks are scoped to a **single section** (or a single submit). One task per section keeps the Skyvern reasoning window small and retries cheap.

## Task purposes

| Purpose | Description | Writes to portal? |
| --- | --- | --- |
| `login` | Authenticate into the portal. | No (session only). |
| `fill` | Navigate to a section and enter values. | Yes. |
| `validate` | Navigate to a section and read back values. | No. |
| `submit` | Press the portal's final submit button. | Yes. |

## Fill task payload

Built by `apps/skyvern_worker/task_builder.py` from a Databricks `survey_portal_payload` slice.

```json
{
  "task_type": "task_v2",
  "purpose": "fill",
  "url": "https://portal.example.edu/surveys/2026/edit",
  "navigation_goal": "Open the 'Enrollment' section. The portal has a left sidebar listing sections by number and name.",
  "data_extraction_goal": null,
  "actions": [
    {
      "action_type": "fill",
      "label_hint": "Total undergraduates",
      "goal_hint": "Enrollment section, 'Total undergraduates *' input.",
      "field_id": "enrollment.total_undergraduates",
      "input_kind": "number",
      "target_value": "33605"
    },
    {
      "action_type": "fill",
      "label_hint": "Total graduates",
      "goal_hint": "Enrollment section, 'Total graduates *' input.",
      "field_id": "enrollment.total_graduates",
      "input_kind": "number",
      "target_value": "5830"
    }
  ],
  "complete_criterion": "All listed fields have been entered. A Save action for the section has been clicked if one exists. Do not submit the full survey.",
  "terminate_criterion": "If any required target field is not found, stop and report which ones.",
  "max_steps": 40,
  "webhook_callback_url": "https://control-plane.internal/webhooks/skyvern",
  "browser_session_id": "sess_run_2026_0001",
  "extra_http_headers": {},
  "totp_identifier": null,
  "totp_url": null
}
```

### Field-level rules the builder enforces

- `input_kind: "select"` → include `choices` so Skyvern can match to the portal's option text.
- `input_kind: "checkbox_group"` → `target_value` is a list of option strings.
- `input_kind: "radio"` → `target_value` is exactly one option.
- `input_kind: "date"` → `target_value` in ISO 8601; Skyvern handles portal-specific formatting via prompt instruction.

## Validate task payload

```json
{
  "task_type": "task_v2",
  "purpose": "validate",
  "url": "https://portal.example.edu/surveys/2026/edit",
  "navigation_goal": "Open the 'Enrollment' section.",
  "data_extraction_goal": "Return the current values of the following labeled fields exactly as shown on the page.",
  "data_extraction_schema": {
    "enrollment.total_undergraduates": { "label_hint": "Total undergraduates", "kind": "number" },
    "enrollment.total_graduates":       { "label_hint": "Total graduates",      "kind": "number" }
  },
  "max_steps": 20,
  "webhook_callback_url": "https://control-plane.internal/webhooks/skyvern",
  "browser_session_id": "sess_run_2026_0001"
}
```

The control plane compares `extracted_data` against the dispatched `target_value`s, using the `skyvern_readback_tolerance` per field. Any mismatch produces a `review_items` row with reason code `SKYVERN_VALIDATION_MISMATCH`.

## Submit task payload

```json
{
  "task_type": "task_v2",
  "purpose": "submit",
  "url": "https://portal.example.edu/surveys/2026/edit",
  "navigation_goal": "Press the final submission button. The final submit control is labeled 'Submit Survey' and appears on the Verification / Submission section.",
  "complete_criterion": "A confirmation page or success toast indicates the survey has been submitted.",
  "max_steps": 10,
  "webhook_callback_url": "https://control-plane.internal/webhooks/skyvern",
  "browser_session_id": "sess_run_2026_0001"
}
```

Submit tasks are dispatched **only** by the Temporal `dispatch_submit_activity`, which checks:
- `submit_released_flag = true`
- `skyvern.submit_enabled` feature flag true
- All section validate tasks status = `SUCCEEDED` with zero open review items

## Browser sessions

Use one Skyvern `browser_session_id` per run so cookies, auth state, and draft progress persist across section tasks. The session is created by the first `login` task and destroyed by `archive_activity`.

## Prompt authoring rules

- Goals are written as **what** to do, not **how**. ("Open Enrollment section" not "click `#nav-enrollment`").
- Goals reference portal labels verbatim where known.
- No business logic, no tolerances, no conditional rules in the prompt.
- Target values are always passed as structured data, never interpolated into free-form text.

## Retries and failure modes

- Temporal retries `dispatch_section_fill_activity` up to 3 times with exponential backoff.
- Skyvern's internal retry for transient navigation failures is left enabled.
- `TIMED_OUT` or `FAILED` Skyvern status parks the workflow in `BLOCKED` and creates a review item for the operator.
- A validate-mismatch is **not** retried automatically; it always requires analyst action.
