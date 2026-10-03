# ADR-0008: Durable Analysis Jobs

## Status

Accepted (source-text handling and lifecycle amended by
[ADR-0009](ADR-0009-async-analysis-worker.md))

## Decision

Analysis jobs are owned by an application-layer `AnalysisRepository` protocol.
The PostgreSQL adapter uses SQLAlchemy 2 and Alembic; production schema changes
are applied with `alembic upgrade head`, never `create_all`. The API uses
PostgreSQL when `CODEBREAKERS_DATABASE_URL` is configured and otherwise keeps
the bounded in-memory adapter for local development and unit tests.

Jobs store analyzer/language parameters, lifecycle state, timestamps, result,
error code, and a version used for optimistic updates. Since ADR-0009, the
worker also needs the submitted source text. It is kept in
`analysis_job_inputs` only until the job reaches a terminal state, deleted in
that same transaction, and cascade-deleted with its job by the retention
purge. Results, which can contain candidate plaintext, are retained for
seven days and must be purged daily by running
`python -m codebreakers.infrastructure.persistence.retention` from a scheduled
task. `CODEBREAKERS_ANALYSIS_RETENTION_DAYS` can set a different positive
retention period. Database URLs are supplied through the environment and are
not logged.

The API persists `pending` jobs with an outbox entry. Workers persist the
`running` claim (with `attempts` and `lease_expires_at`) and terminal
transitions (see ADR-0009). Cancellation is internal: jobs that exceed their
time or memory budget become `cancelled`. There is no public cancel
operation. Listing is newest-first and uses offset/limit pagination.

## Consequences

PostgreSQL is required for durable API deployments. A deployment must apply
migrations before starting API instances and schedule retention cleanup daily.
The in-memory repository remains suitable for tests and ephemeral local use.
