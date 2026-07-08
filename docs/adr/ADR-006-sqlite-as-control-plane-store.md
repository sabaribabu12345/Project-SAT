# ADR-006: SQLite as the Control-Plane Operational Store (Phase 1)

## Status
Accepted (phase 1). Revisit only when SQLite no longer fits the operational workload.

## Context
The v2 code used an in-memory repository, which loses state on restart. A durable, transactional store is needed for run state, audit events, review queue, Skyvern task tracking, feature flags, and secret references.

For the first implementation phase we want the lowest operational surface: a single control-plane service with state stored on its own filesystem, no separate DB process to run. Skyvern can also use SQLite for its internal phase-1 storage. Temporal's persistence and Skyvern's storage are internal to their deployments and should not dictate the control plane's store.

Databricks remains the system of record for survey data, metadata, computed values, validation results, and artifacts. The SQLite database is only the operational control-plane store.

## Decision

Phase 1 uses **SQLite** as the control-plane operational store:

- Single file: `./data/control_plane.db` (path configurable).
- Accessed via SQLAlchemy 2.x + Alembic.
- WAL mode enabled (`PRAGMA journal_mode=WAL`) for concurrent reads during writes.
- Foreign keys enforced (`PRAGMA foreign_keys=ON`).
- Backed up by file copy on a schedule; rotation kept under `./data/backups/`.
- Models use dialect-neutral SQLAlchemy types so a future store change is possible without rewriting the domain model.

Temporal and Skyvern keep their own internal storage. Skyvern should use SQLite in phase 1 when supported. These storage choices are deployment details and do not change this control-plane decision.

## Consequences

- Zero-ops for phase 1: no DB server to run.
- Single-writer limitation: one FastAPI worker process writes; additional workers are read-only or use a request-queue pattern. Acceptable for phase 1 (single analyst team, dozens of runs per cycle).
- Webhook callbacks and Temporal activities serialize writes through the same SQLite file. Keep transactions short; avoid long `SELECT ... FOR UPDATE`-style patterns (SQLite does not support them).
- JSON columns are stored as `TEXT` with application-side serialization; keep queries by JSON fields minimal.
- If SQLite stops fitting, first evaluate whether the existing Databricks platform can own the additional state. Add a separate operational database only if the workload requires one.

## Revisit triggers

Revisit the control-plane store when any of the following is true:

- More than one write-capable control-plane worker is required.
- Concurrent analyst writes exceed approximately 5/sec sustained.
- A single run's event log exceeds approximately 1M rows.
- Multi-tenant deployment is required.
- Operational reporting needs are better served by centralizing more state in Databricks.

## Rejected alternatives

- **New Postgres service in phase 1** — technically reasonable, but adds a DB to operate before there is throughput pressure justifying it.
- **In-memory with periodic snapshot** — loses durability guarantees we need for audit.
- **DuckDB / embedded analytical store** — analytical, not OLTP.
