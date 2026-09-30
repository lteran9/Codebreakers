# ADR 0007: HTTP API Contract

- Status: Accepted
- Date: 2026-09-30

## Context

Phase 4 exposes cipher operations and cryptanalysis over HTTP. The contract must stay stable for later frontends, survive the move to queued analysis (Phase 6), and must not duplicate business rules that the CLI already uses.

## Decision

- **Versioning:** Public routes live under `/v1`. Health probes (`/health/live`, `/health/ready`) are unversioned because they are an operational contract, not a product one.
- **Shared behaviour:** Route handlers only translate HTTP data. Cipher routes call `composition.run_cipher` and analysis routes call `AnalysisService`, the same entry points the CLI uses.
- **Keys as strings:** Cipher keys are sent as strings, like `--key` on the CLI. The homophonic key is a JSON object encoded as a string. Only `alphabet` is exposed from `TransformOptions`, which matches the CLI.
- **Analysis as a job resource:** `POST /v1/analyses` returns `201 Created` with a `Location` header and a job body (`id`, `status`, timestamps, `result`). Analyses run synchronously for now, so jobs are returned as `succeeded`. The `status` enum already includes `pending`, `running`, and `failed`, so clients built against it keep working once analysis moves to a queue. Jobs are kept in a bounded in-process `InMemoryAnalysisRepository` (default 128 jobs, oldest evicted first) behind the `AnalysisRepository` port until PostgreSQL arrives in Phase 5. Jobs are lost on restart and are not shared between worker processes.
- **Result shape:** `result` is a discriminated union on `kind` (`ranked-candidates`, `frequency-report`, `vigenere-key-analysis`). Non-finite scores are serialised as `null`.
- **Errors:** Every error is an RFC 9457 `application/problem+json` body with a stable `code` extension, a `correlation_id`, and a `type` of `urn:codebreakers:problem:{code}`. Domain and application errors map to 4xx responses (unsupported cipher → 404, unknown analysis → 404, invalid input → 422). Unexpected failures return a generic 500 whose body never includes the exception message.
- **Correlation IDs:** A well-formed inbound `X-Request-ID` (`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`) is honoured. Otherwise a UUID4 is generated. The ID is echoed on every response, including 413 and 500 responses.
- **Limits:** Request bodies are capped at 64 KiB (413), whether the size is declared in `Content-Length` or only seen while streaming. The schemas cap `text` at 20,000 characters, `key` at 4,096, and `alphabet` at 1,024 (422). Request models reject unknown fields and do not coerce types. At the text cap, Vigenere analysis takes about 1.2 s, which is within a synchronous request budget.
- **Redaction:** Request bodies are never logged. The request log records the method, path, status, duration, and correlation ID. Validation problems list only the location, message, and type of each issue; the offending input and its context are dropped. Unexpected errors are logged by exception type only.
- **Contract review:** `docs/api/openapi.json` is committed. Operation IDs are explicit and stable. A test fails when the generated document drifts; after reviewing the change, run `make openapi` to regenerate it.

## Consequences

- The CLI and API cannot diverge on cipher or analysis behaviour, because both go through the same functions.
- Moving to queued analysis changes when the status reaches a terminal state. It does not change the resource shape.
- Clients can branch on `code` without parsing human-readable text.
- Running more than one API process needs Phase 5 persistence before `GET /v1/analyses/{id}` is reliable.
