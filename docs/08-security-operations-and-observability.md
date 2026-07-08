# Security, Operations, and Observability

## Network boundary

- Skyvern runs in a private subnet with no outbound route to the public LLM providers.
- Egress allowlist: Databricks workspace host, the target portal hosts, and internal observability endpoints.
- The control plane is reachable only from the corporate network / VPN.

## Identity and authorization

| Role | Permissions |
| --- | --- |
| Analyst | View runs, resolve review items, approve fill. |
| Approver | All analyst permissions + `release-submit`. |
| Operator | Dispatch reruns, cancel Skyvern tasks, view traces. |
| Admin | Manage feature flags, secrets refs, portal catalog. |

Enforced at the control plane API via SSO-backed session + role claims.

## Secrets management

- Portal credentials, TOTP seeds, and Databricks tokens live in the approved secrets store (AWS Secrets Manager / Vault / Databricks secret scopes).
- `control_plane.secrets_refs` stores only references.
- Skyvern task payloads reference secrets via Skyvern's encrypted parameter feature so the plaintext is never logged.
- HMAC key for webhook signature verification rotates on a schedule.

## Audit model

Every action emits one `run_events` row:

- `RUN_CREATED`
- `PREPARE_STARTED` / `PREPARE_COMPLETED`
- `VALIDATION_COMPLETED`
- `REVIEW_ITEM_CREATED` / `REVIEW_ITEM_RESOLVED`
- `PORTAL_PAYLOAD_PUBLISHED`
- `SKYVERN_TASK_DISPATCHED` (purpose, section, task_id)
- `SKYVERN_TASK_COMPLETED`
- `SKYVERN_VALIDATION_MISMATCH`
- `REVIEW_APPROVED_FOR_FILL`
- `SUBMIT_RELEASED`
- `SKYVERN_SUBMIT_COMPLETED`
- `RUN_ARCHIVED`

## Observability

### Metrics (Prometheus)

- `run_duration_seconds{state}`
- `skyvern_task_duration_seconds{purpose,section}`
- `skyvern_task_failures_total{purpose,reason}`
- `validation_mismatch_total{section,field}`
- `review_item_open_age_seconds`
- `submit_release_latency_seconds`

### Logs

- Control plane structured logs (JSON, with `run_id`, `section_id`, `event_type`).
- Temporal worker logs.
- Skyvern logs (retained per Skyvern defaults; mount to internal log aggregator).

### Traces / artifacts

- Per Skyvern task: screenshot sequence, DOM snapshot, action log, model prompt/response.
- Stored under `uc://surveys/artifacts/{run_id}/{section_id}/{task_purpose}/`.
- Retention policy configured via UC table properties.

## Runbooks (to be authored post-Slice 3)

- RB-01 Skyvern task stuck in RUNNING.
- RB-02 Validation mismatch on one field.
- RB-03 Portal SSO change.
- RB-04 Databricks model endpoint outage.
- RB-05 Emergency cancel for a live submit.

## Operational guardrails

- No automatic retry of `submit` tasks.
- `skyvern.live_portal_enabled` flag gates all non-sandbox portals.
- Every new portal requires a portal-entry in `portal_catalog` with a reviewed goal prompt.
- Nightly dry-run against `fake-survey-form/` to catch prompt drift after Skyvern / model upgrades.
