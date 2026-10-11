# Queued Analysis Flow (Local Stack)

Captured on 2026-10-12 against `docker compose up --build --wait`. That stack
runs PostgreSQL, a one-shot migration, the API, the outbox relay, and the
worker, all from one image. The client was
[examples/api.sh](../../examples/api.sh) with `REQUEST_ID=compose-demo-1791824080`. The result
is cut after the first of 26 ranked candidates.

## Client view

```console
# Submit a queued analysis (202 Accepted + Location header)
$ curl -X POST /v1/analyses -d '{"analyzer": "caesar-bruteforce", "text": "Wkh txlfn eurzq ira mxpsv ryhu wkh odcb grj"}'
Location: /v1/analyses/2da16c44-76b7-4714-8d5e-30385a3b3dc8

# Poll until the job finishes
{"id":"2da16c44-76b7-4714-8d5e-30385a3b3dc8","status":"succeeded","analyzer":"caesar-bruteforce","language":"english","created_at":"2026-10-12T16:54:40.851494Z","updated_at":"2026-10-12T16:54:42.557054Z","completed_at":"2026-10-12T16:54:42.557054Z","error_code":null,"version":3,"result":{"kind":"ranked-candidates","analyzer":"caesar-bruteforce","language":"english","language_version":"letter-frequency-v1","candidates":[{"rank":1,"score":-115.01980771657696,"key":"3","text":"THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG","explanation":"Shift 3 scored -115.0198 against english."}, …]}}
```

## Server view

These are the log lines for that job from all three services
(`docker compose logs api relay worker`), with fields trimmed. The client's
`X-Request-ID` is the `correlation_id` in both the API and worker logs, so
one ID finds the request and the job it started. The relay logs only counts.

```json
{"timestamp": "2026-10-12T16:54:40.864+00:00", "service": "api", "event": "analysis_submitted", "correlation_id": "compose-demo-1791824080", "job_id": "2da16c44-76b7-4714-8d5e-30385a3b3dc8", "message": "analysis_submitted analyzer=caesar-bruteforce"}
{"timestamp": "2026-10-12T16:54:40.865+00:00", "service": "api", "event": "request_completed", "correlation_id": "compose-demo-1791824080", "message": "request_completed method=POST path='/v1/analyses' status=202 duration_ms=14.6"}
{"timestamp": "2026-10-12T16:54:41.818+00:00", "service": "relay", "event": "outbox_relayed", "message": "outbox_relayed count=1"}
{"timestamp": "2026-10-12T16:54:42.560+00:00", "service": "worker", "event": "job_processed", "correlation_id": "compose-demo-1791824080", "job_id": "2da16c44-76b7-4714-8d5e-30385a3b3dc8", "message": "job_processed outcome=succeeded attempt=1"}
```

| Step | Time (UTC) | Elapsed |
| --- | --- | ---: |
| API stores the job and its outbox row in one transaction, returns 202 | 16:54:40.864 | 0 s |
| Relay publishes the outbox row to the PostgreSQL queue | 16:54:41.818 | 0.95 s |
| Worker claims the job, runs the analyzer, stores the result | 16:54:42.560 | 1.70 s |

Most of the 1.7 s is polling. The relay checks the outbox every second, and
the worker checks the queue every half second. The Caesar analysis itself
takes about 2 ms on short text (see [benchmarks.md](../benchmarks.md)). The
design is in [ADR-0009](../architecture/ADR-0009-async-analysis-worker.md)
and the job states are in
[ADR-0016](../architecture/ADR-0016-analysis-job-state-machine.md).
