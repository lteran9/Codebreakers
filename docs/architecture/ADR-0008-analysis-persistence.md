# ADR-0008: Durable Analysis Jobs

## Status

Accepted

## Decision

Analysis jobs are owned by an application-layer `AnalysisRepository` protocol.
The PostgreSQL adapter uses SQLAlchemy 2 and Alembic; production schema changes
are applied with `alembic upgrade head`, never `create_all`. The API uses
PostgreSQL when `CODEBREAKERS_DATABASE_URL` is configured and otherwise keeps
the bounded in-memory adapter for local development and unit tests.

Jobs store analyzer/language parameters, lifecycle state, timestamps, result,
error code, and a version used for optimistic updates. Submitted source text is
never stored. Results, which can contain candidate plaintext, are retained for
seven days and must be purged daily by running
`python -m codebreakers.infrastructure.persistence.retention` from a scheduled
task. `CODEBREAKERS_ANALYSIS_RETENTION_DAYS` can set a different positive
retention period. Database URLs are supplied through the environment and are
not logged.

The synchronous API persists `pending`, `running`, and terminal transitions.
Cancellation is modeled but has no public operation until asynchronous workers
are introduced. Listing is newest-first and uses offset/limit pagination.

## Consequences

PostgreSQL is required for durable API deployments. A deployment must apply
migrations before starting API instances and schedule retention cleanup daily.
The in-memory repository remains suitable for tests and ephemeral local use.
