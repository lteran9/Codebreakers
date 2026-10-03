# Codebreakers

Codebreakers is a Python project for exploring classical ciphers, historical encoded messages, and clean engineering practices. The repository is intentionally structured around a domain-first design so that cipher logic stays independent from CLI, API, and infrastructure concerns.

## Project goals

- Build a small, typed cipher domain with reproducible examples.
- Keep behavior testable without framework coupling.
- Establish quality gates before later phases expand the project.
- Document early architectural decisions and the text-normalization policy.

## Development setup

Create and activate a virtual environment, then install the project with its development tools:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
pre-commit install
```

Common repository commands are available through the Makefile:

```bash
make install
make format
make lint
make typecheck
make test
make coverage
```

## Quality gates

Run the fast checks locally with:

```bash
make test
ruff check .
ruff format --check .
mypy src tests
```

PostgreSQL integration tests use Testcontainers and require Docker:

```bash
make integration
```

## Command-line usage

After installing the package, the `codebreakers` console script is available:

```bash
# Encrypt with a direct --text argument
codebreakers encrypt --cipher caesar --key 3 --text "HELLO WORLD"
# KHOOR ZRUOG

# Decrypt by piping input through standard input
echo "KHOOR ZRUOG" | codebreakers decrypt --cipher caesar --key 3
# HELLO WORLD

# Use a custom alphabet
codebreakers encrypt --cipher caesar --key 2 --text "1239" --alphabet "0123456789"
# 3451

# Use Vigenere
codebreakers encrypt --cipher vigenere --key LEMON --text "ATTACK AT DAWN"
# LXFOPV EF RNHR

# Use monoalphabetic substitution
codebreakers encrypt --cipher substitution --key QWERTYUIOPASDFGHJKLZXCVBNM --text "ATTACK"
# QZZQEA
```

Run `codebreakers --help`, `codebreakers encrypt --help`, or `codebreakers decrypt --help` for full option details.

Analyze ciphertext without a key; the report is printed as JSON:

```bash
codebreakers analyze --analyzer caesar-bruteforce \
  --text "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ"
```

Analyzers: `caesar-bruteforce`, `substitution-frequency`, `vigenere-frequency`, `homophonic-distribution`. Exit codes are 0 (success), 1 (unexpected error), 2 (invalid usage or unsupported language), 3 (invalid key), 4 (invalid alphabet), 5 (unsupported cipher), 6 (unsupported analyzer), and 7 (insufficient text for analysis).

## HTTP API

Start the API locally (binds to `127.0.0.1:8000` by default), then open `http://127.0.0.1:8000/docs` for interactive OpenAPI documentation:

```bash
codebreakers serve
```

```bash
# Encrypt and decrypt
curl -s -X POST http://127.0.0.1:8000/v1/ciphers/caesar/encrypt \
  -H 'Content-Type: application/json' \
  -d '{"text": "HELLO WORLD", "key": "3"}'
# {"cipher":"caesar","operation":"encrypt","result":"KHOOR ZRUOG"}

# Submit an analysis (returns 202 with a pending job and a Location header),
# then poll it until status is succeeded, failed, or cancelled
curl -s -i -X POST http://127.0.0.1:8000/v1/analyses \
  -H 'Content-Type: application/json' \
  -d '{"analyzer": "caesar-bruteforce", "text": "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ"}'
curl -s http://127.0.0.1:8000/v1/analyses/<id>

# Probes
curl -s http://127.0.0.1:8000/health/live
curl -s http://127.0.0.1:8000/health/ready
```

Errors are RFC 9457 `application/problem+json` bodies with a machine-readable `code` and a `correlation_id` that matches the `X-Request-ID` response header. Request bodies are limited to 64 KiB, text to 20,000 characters, and keys to 4,096 characters. Analysis jobs are stored in memory by default. Set `CODEBREAKERS_DATABASE_URL` to use PostgreSQL, apply schema changes with `make migrate`, and schedule `python -m codebreakers.infrastructure.persistence.retention` daily. Results are retained for seven days by default (`CODEBREAKERS_ANALYSIS_RETENTION_DAYS`). Submitted source text is kept only until its job finishes. See [ADR-0007](docs/architecture/ADR-0007-http-api-contract.md) for the HTTP contract and [ADR-0008](docs/architecture/ADR-0008-analysis-persistence.md) for persistence and retention decisions.

### Asynchronous analysis worker

Analyses run outside the request. The API commits the job with a transactional outbox entry, then returns `202 Accepted`. A worker claims the job, runs the analyzer in a child process within its time and memory budget, and records the outcome. Over budget, the job becomes `cancelled`. If the analyzer rejects the input, the job becomes `failed`, with an `error_code` such as `insufficient-text`. This was built ahead of measured demand, as a portfolio demonstration. See [ADR-0009](docs/architecture/ADR-0009-async-analysis-worker.md).

By default (`CODEBREAKERS_ANALYSIS_QUEUE=in-process`), `codebreakers serve` runs the relay and one worker thread in the same process, so no Azure resources are needed. For independently scaled processes on Azure Service Bus:

```bash
export CODEBREAKERS_ANALYSIS_QUEUE=service-bus
export CODEBREAKERS_DATABASE_URL=postgresql://…
export CODEBREAKERS_SERVICEBUS_CONNECTION_STRING='Endpoint=sb://…'
export CODEBREAKERS_SERVICEBUS_QUEUE=analysis-jobs       # default
codebreakers serve     # API: records jobs and outbox entries only
codebreakers relay     # publishes outbox entries (safe to run several)
codebreakers worker    # consumes jobs (scale horizontally)
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `CODEBREAKERS_ANALYSIS_TIME_BUDGET_SECONDS` | `30` | Wall-clock limit per job, including child start-up |
| `CODEBREAKERS_ANALYSIS_MEMORY_LIMIT_MB` | `1024` | `RLIMIT_AS` for the child process where supported (`0` disables) |
| `CODEBREAKERS_WORKER_MAX_ATTEMPTS` | `5` | Attempts before a message is dead-lettered |
| `CODEBREAKERS_WORKER_RETRY_BASE_SECONDS` / `_MAX_SECONDS` | `2` / `60` | Exponential backoff between attempts |

A valid W3C `traceparent` header and the `X-Request-ID` correlation ID are propagated to the worker logs. To inspect, replay, or discard dead-lettered messages, use `codebreakers deadletter list | replay | discard`, as described in the [dead-letter runbook](docs/operations/dead-letter-runbook.md).

The committed contract lives in [docs/api/openapi.json](docs/api/openapi.json). A test fails when the generated schema drifts; review the change and regenerate with `make openapi`.

These are broken classical ciphers. They provide no confidentiality and must never be used to protect real secrets.

## Cryptanalysis and benchmarks

Phase 3 adds reusable text statistics, Caesar brute-force ranking, substitution frequency-analysis assistance, Vigenere key-length and candidate ranking, and homophonic token-distribution reports. Analyzer outputs include explicit language or model versions and explainable scores or measurements.

Run the deterministic benchmark script with:

```bash
python scripts/benchmark_caesar.py
```

The current quality target is that the Caesar brute-force analyzer places the documented Caesar fixture's key in the top-ranked candidate. Vigenere analysis reports ranked key lengths and candidate keys for sufficiently long ciphertext, but short inputs may not contain enough repeated structure for reliable recovery. Substitution and homophonic analysis are assistance/reporting features in this phase and do not claim to automatically solve arbitrary ciphertext.

The package source lives in `src/codebreakers`, and tests live in `tests`.

## Architecture notes

The engineering baseline includes a lightweight ADR set under `docs/architecture/` to make the repository's layout and decisions explicit. This helps future phases avoid accidental framework leakage and inconsistent data-handling choices.
