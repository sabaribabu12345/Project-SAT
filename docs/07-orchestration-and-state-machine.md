# Orchestration and State Machine

## Temporal workflow

### Workflow id convention

`survey-run::{survey_id}::{survey_year}::{run_id}`

### Inputs

```python
@dataclass
class RunWorkflowInput:
    run_id: str
    survey_id: str
    survey_year: int
    created_by: str
```

### Pseudocode

```python
@workflow.defn
class RunWorkflow:
    def __init__(self) -> None:
        self.review_resolved = False
        self.fill_signals: dict[str, SkyvernCallback] = {}
        self.validate_signals: dict[str, SkyvernCallback] = {}
        self.submit_released = False
        self.submit_signal: SkyvernCallback | None = None

    @workflow.run
    async def run(self, input: RunWorkflowInput) -> None:
        await workflow.execute_activity(
            prepare_activity, input.run_id,
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        await workflow.wait_condition(lambda: self.review_resolved)

        sections = await workflow.execute_activity(
            list_sections_activity, input.run_id,
            start_to_close_timeout=timedelta(seconds=30),
        )

        # Login once per run.
        await workflow.execute_activity(
            dispatch_login_activity, input.run_id,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        for section_id in sections:
            await workflow.execute_activity(
                dispatch_section_fill_activity,
                args=(input.run_id, section_id),
                start_to_close_timeout=timedelta(minutes=30),
            )
            await workflow.wait_condition(lambda: section_id in self.fill_signals)
            if self.fill_signals[section_id].status != "SUCCEEDED":
                await workflow.execute_activity(
                    park_for_review_activity,
                    args=(input.run_id, section_id, "FILL_FAILED"),
                )
                return

            await workflow.execute_activity(
                dispatch_section_validate_activity,
                args=(input.run_id, section_id),
                start_to_close_timeout=timedelta(minutes=15),
            )
            await workflow.wait_condition(lambda: section_id in self.validate_signals)

            mismatches = self.validate_signals[section_id].mismatches
            if mismatches:
                await workflow.execute_activity(
                    create_review_items_activity,
                    args=(input.run_id, section_id, mismatches),
                )
                await workflow.wait_condition(lambda: self.review_resolved)

        await workflow.wait_condition(lambda: self.submit_released)

        await workflow.execute_activity(
            dispatch_submit_activity, input.run_id,
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=RetryPolicy(maximum_attempts=1),  # never auto-retry submit
        )
        await workflow.wait_condition(lambda: self.submit_signal is not None)

        await workflow.execute_activity(
            archive_activity, input.run_id,
            start_to_close_timeout=timedelta(minutes=10),
        )

    @workflow.signal
    def on_review_resolved(self, _: dict) -> None:
        self.review_resolved = True

    @workflow.signal
    def on_fill_completed(self, cb: SkyvernCallback) -> None:
        self.fill_signals[cb.section_id] = cb

    @workflow.signal
    def on_validate_completed(self, cb: SkyvernCallback) -> None:
        self.validate_signals[cb.section_id] = cb

    @workflow.signal
    def on_submit_released(self, _: dict) -> None:
        self.submit_released = True

    @workflow.signal
    def on_submit_completed(self, cb: SkyvernCallback) -> None:
        self.submit_signal = cb
```

## Activities

| Activity | Idempotent? | Notes |
| --- | --- | --- |
| `prepare_activity` | Yes | Delegates to Databricks job; result written to Databricks and optionally cached in the control plane. |
| `list_sections_activity` | Yes | Reads section order from Databricks. |
| `dispatch_login_activity` | Yes (by browser_session_id) | Creates Skyvern login task. |
| `dispatch_section_fill_activity` | Yes (by (run_id, section_id, payload_version)) | Generates Skyvern task payload; POSTs to Skyvern API. |
| `dispatch_section_validate_activity` | Yes (same key) | |
| `create_review_items_activity` | Yes | Uses deterministic review_item_id hash. |
| `park_for_review_activity` | Yes | |
| `dispatch_submit_activity` | **No** | Checks flags and `submit_released_flag` before dispatch. Single attempt. |
| `archive_activity` | Yes | Finalizes UC Volume manifest, closes browser session, marks run `ARCHIVED`. |

## Control plane state machine

```text
CREATED
  └─> PREPARING ──> READY_FOR_REVIEW ──> READY_FOR_FILL ──> FILLING
                               │                                │
                               │                                ├─> BLOCKED ──> READY_FOR_FILL
                               │                                │
                               │                                └─> FILLED ──> SUBMIT_READY ──> SUBMITTED ──> ARCHIVED
                               │
                               └─> BLOCKED ──> READY_FOR_REVIEW
```

Transitions are enforced in SQLite via CHECK constraints and in code via `packages/state_machine/run_state_machine.py` (ported from v2).

New transitions vs v2:

- `FILLING → BLOCKED` on Skyvern validate mismatch.
- `BLOCKED → READY_FOR_FILL` when all mismatch-review items are resolved and Temporal is signaled.
- `FILLED → SUBMIT_READY` only via `/release-submit`.

## Why Temporal over Prefect (ADR-005)

- Long-lived workflows with human signals are native.
- Signal replay is deterministic, so review resolution and webhook callbacks are safe to re-deliver.
- Activity retry policies per operation type.
- Durable workflow state and native signals without pushing orchestration into the control-plane database.
