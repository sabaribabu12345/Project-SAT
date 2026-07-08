# ADR-005: Temporal as the Orchestrator

## Status
Accepted (supersedes v2 "Prefect or Temporal" guidance)

## Context
Survey runs are long-running (often spanning days because of human review windows), require durable signals for human gates, and must retry individual activities without losing run position. The v2 plan left this open between Prefect OSS and Temporal.

Skyvern itself runs on Temporal for its internal task queue. Co-locating workflow infrastructure reduces operational surface.

## Decision
Use Temporal (self-hosted) as the orchestrator for the per-run workflow. One `RunWorkflow` per `run_id`. Activities wrap Databricks jobs and Skyvern client calls.

## Consequences
- Signals (`review_resolved`, `submit_released`, Skyvern callbacks) are native.
- Retry policies are defined per activity.
- Temporal's backing store is internal workflow infrastructure and separate from the control-plane SQLite store.
- Engineering team must learn Temporal semantics (workflow determinism, activity timeouts, signal replay).

## Rejected alternatives
- **Prefect OSS** — flow state is durable, but long-running human gates are less native; signals and pause/resume would be built on top rather than baked in.
- **LangGraph** — optimized for agent control loops, not durable business workflows with hour+ gates.
- **Databricks Workflows as sole orchestrator** — excellent for jobs, but lacks the human-gate and signal semantics we need.
