# Codebreakers Implementation Roadmap

This roadmap grows Codebreakers from a small Python package into a deployed, observable application without introducing infrastructure before it is useful. Each phase should end with a working, demonstrable increment. Complete phases in order unless a task is explicitly marked optional.

## Guiding Architecture

Use a clean architecture with dependencies pointing inward:

```text
CLI / HTTP API / worker
          |
          v
application services and ports
          |
          v
pure cipher and cryptanalysis domain
          ^
          |
database / messaging / telemetry adapters
```

The domain must not import FastAPI, SQLAlchemy, Azure SDKs, or CLI libraries. That keeps cipher behavior independently testable and makes every external technology replaceable. Start with one deployable application and one worker; do not split the project into microservices unless independent scaling or ownership creates a real need.

### Target Technology Stack

- Python 3.12, type hints, dataclasses, and protocols
- FastAPI and Pydantic for HTTP boundaries and validation
- Typer for the command-line interface
- SQLAlchemy 2 and Alembic with PostgreSQL for persistence
- Azure Service Bus for queued analysis jobs
- pytest, Hypothesis, Testcontainers, Ruff, and mypy for quality
- OpenTelemetry and Azure Application Insights for observability
- Docker with multi-stage builds and non-root runtime users
- GitHub Actions for continuous integration and deployment
- Terraform for reproducible Azure infrastructure
- Azure Container Apps, Container Registry, Key Vault, PostgreSQL, Service Bus, and Application Insights

## Phase 0: Engineering Baseline

**Outcome:** Contributors get deterministic local checks and documented design decisions before product behavior expands.

**Why now:** Quality gates are cheap to establish while the repository is small. They prevent later phases from accumulating inconsistent style, weak typing, or unrepeatable setup steps.

### Build

- [x] Add `.gitignore` entries for virtual environments, caches, coverage, build artifacts, IDE files, and local environment files.
- [x] Add `pre-commit` to the development dependencies and configure hooks for Ruff linting, Ruff formatting, and basic file hygiene.
- [x] Enable branch coverage with `pytest-cov`; set an initial achievable threshold and increase it as behavior is added.
- [x] Configure pytest unit and integration markers so cloud or container tests can be excluded from the fast local suite.
- [x] Add a `Makefile` or platform-neutral task runner with commands for `install`, `format`, `lint`, `typecheck`, and `test`.
- [x] Add a GitHub Actions CI workflow for Python 3.12 that installs the package, runs Ruff, runs mypy, and runs pytest with coverage.
- [x] Add Dependabot or Renovate configuration for Python and GitHub Actions dependency updates.
- [x] Create `docs/architecture/` and record short Architecture Decision Records (ADRs) for the source layout, clean architecture, and initial Azure target.
- [x] Record a text-handling ADR fixing the Unicode policy: normalization form (for example NFC), case folding versus preservation, code points versus grapheme clusters, and how digits and non-English symbols in the historical material are treated.
- [x] Add `CONTRIBUTING.md` with setup, test, commit, and pull-request guidance.

### Verify

- [x] A fresh clone can run all checks using only the documented commands.
- [x] CI runs on pull requests and rejects formatting, typing, or test failures.
- [x] No production package is introduced without an immediate use case.

## Phase 1: Cipher Domain and Caesar Vertical Slice

**Outcome:** The package can encrypt and decrypt Caesar cipher text through a small, stable domain API.

**Why now:** Caesar is simple enough to expose design flaws without hiding them behind algorithmic complexity. This phase defines the extension pattern that later ciphers will follow.

### Build

- [x] Create `src/codebreakers/domain/` with `ciphers/`, `models.py`, and `errors.py`; keep these modules free of framework imports.
- [x] Define an immutable request or value object for text, alphabet, case policy, and treatment of symbols outside the alphabet. Keep the key out of this shared object because key types differ per cipher (Caesar `int`, Vigenere `str`, substitution permutation, homophonic one-to-many map).
- [x] Define a typed, generic `Cipher[KeyT]` protocol with `encrypt` and `decrypt` operations so each cipher keeps a strictly typed key under `mypy --strict`. Avoid forcing cryptanalysis into this protocol because not every cipher has the same analysis capabilities.
- [x] Decide and document whether keys are passed per operation or bound at cipher construction, and capture the choice in an ADR because the Phase 3 cipher registry depends on it (stateless instances versus keyed factories).
- [x] Implement an `Alphabet` value object that validates non-empty, unique symbols and provides index lookup and modular rotation.
- [x] Implement `CaesarCipher`, including positive, negative, and oversized shifts normalized modulo the alphabet length.
- [x] Decide and document whether case is preserved and whether punctuation, whitespace, and unknown symbols pass through or raise validation errors.
- [x] Apply a single Unicode normalization step at the domain boundary (per the text-handling ADR) so index lookups cannot break on composed versus decomposed characters.
- [x] Export only intentional public types from package `__init__.py` modules.
- [x] Add a simple application service in `src/codebreakers/application/` that selects an operation and invokes a supplied cipher without knowing its concrete implementation.

### Test

- [x] Add example-based tests using known plaintext and ciphertext pairs.
- [x] Add boundary tests for empty text, zero shift, negative shift, full
  alphabet rotation, Unicode policy, and invalid alphabets.
- [x] Add Hypothesis and property tests proving
  `decrypt(encrypt(text, key), key) == normalize(text)` for supported input,
  stating the invariant over normalized input so pass-through or stripping
  policies do not falsify it.
- [x] Test application behavior against a small fake cipher to prove the
  application layer depends on the protocol rather than `CaesarCipher`.

### Exit Criteria

- [x] The domain has no I/O or framework dependencies.
- [x] The Caesar implementation is fully typed and its public behavior is
  documented with examples.
- [x] Unit and property tests pass locally and in CI.

## Phase 2: Command-Line Product

**Outcome:** A user can exercise the Caesar implementation from a polished CLI.

**Why now:** A CLI provides the quickest usable interface and validates the application boundary before HTTP, persistence, and cloud infrastructure arrive.

### Build

- [ ] Add Typer as a production dependency and expose a `codebreakers` console script through `pyproject.toml`.
- [ ] Create `src/codebreakers/cli/` as an adapter over application services.
- [ ] Create a single shared composition/wiring module that both the CLI and the later HTTP API import, so there is one source of truth for which ciphers exist and how they are constructed.
- [ ] Implement `encrypt` and `decrypt` commands with cipher, key, alphabet, and input options.
- [ ] Support direct text and standard input so commands compose in shell pipelines; write results to standard output and diagnostics to standard error.
- [ ] Return documented non-zero exit codes for invalid keys, invalid alphabets, unsupported ciphers, and unexpected failures.
- [ ] Add `--help` examples without leaking stack traces during normal errors.

### Test

- [ ] Test commands with Typer's test runner, including successful output, standard input, help text, and each expected exit code.
- [ ] Confirm CLI tests use real application services but no database or network.
- [ ] Package a wheel and verify the console entry point in a clean environment.

### Exit Criteria

- [ ] `codebreakers encrypt --cipher caesar ...` and the matching decrypt command form a complete round trip.
- [ ] The README contains a short installation and CLI usage example.

## Phase 3: Cipher Catalog and Cryptanalysis

**Outcome:** The project supports representative cipher families and can solve selected ciphertext without being given the key.

**Why now:** Multiple algorithms test whether the domain abstractions genuinely generalize. Separating cryptanalysis from encryption also models capabilities instead of building one oversized interface.

### Build the Cipher Catalog

- [ ] Implement monoalphabetic substitution with a validated bijective key.
- [ ] Implement Vigenere as the first polyalphabetic cipher, with a validated repeating keyword and explicit key-advance behavior for non-alphabet symbols.
- [ ] Implement a homophonic substitution model where plaintext symbols map to one or more unique ciphertext tokens. Encryption is non-deterministic because it selects among homophones, so accept an injected seeded random source to keep it reproducible; decryption stays deterministic.
- [ ] Create an explicit cipher registry in the shared composition module (the same one the CLI and API import); do not use runtime subclass discovery or import side effects.
- [ ] Store historical examples and expected results as versioned fixtures with
  provenance and licensing notes.

### Build Cryptanalysis Capabilities

- [ ] Create a separate `domain/cryptanalysis/` package with typed `Analyzer`
  protocols and ranked candidate result models.
- [ ] Add reusable text statistics: symbol counts, n-gram counts, index of coincidence, and configurable language scoring.
- [ ] Implement Caesar brute-force analysis that returns every shift ranked by an English-language score.
- [ ] Implement frequency-analysis assistance for substitution ciphers before attempting a fully automated solver.
- [ ] Implement Vigenere key-length estimation using index of coincidence or Kasiski examination, then rank candidate keys.
- [ ] Treat automated homophonic solving as an optional advanced milestone;
  first provide frequency and symbol-distribution reports.
- [ ] Version language models or frequency data and make the selected language
  explicit in analysis requests.

### Test and Benchmark

- [ ] Add unit and property tests for every cipher's round-trip invariant over
  normalized input, seeding the random source for homophonic encryption.
- [ ] Add tests for malformed, duplicate, incomplete, and ambiguous keys.
- [ ] Create a corpus of known examples that tests solver ranking quality.
- [ ] Add deterministic benchmark scripts for increasing ciphertext sizes;
  record input size, runtime, Python version, and machine details.
- [ ] Set documented quality targets, such as placing the correct Caesar key in the top candidate and recovering known Vigenere keys for sufficient text.

### Exit Criteria

- [ ] New cipher implementations require no changes to existing cipher classes.
- [ ] Decryption is deterministic for every cipher, and encryption is deterministic except for homophonic substitution, which is intentionally non-deterministic yet reproducible under a fixed seed. Analyzers return explainable scores and ranked candidates.
- [ ] Algorithm limitations and minimum useful ciphertext lengths are documented.

## Phase 4: HTTP API

**Outcome:** Cipher operations and analysis submission are accessible through a
versioned, documented HTTP interface.

**Why now:** The proven application layer can be exposed without placing business rules in route functions. The API also creates the contract that cloud deployment and future frontends will consume.

### Build

- [ ] Add FastAPI, Pydantic, and an ASGI server as production dependencies.
- [ ] Create `src/codebreakers/api/` with an application factory, versioned routers under `/v1`, schemas, dependency providers, and exception handlers.
- [ ] Implement `POST /v1/ciphers/{cipher}/encrypt` and `/decrypt`.
- [ ] Implement `POST /v1/analyses`; initially run small analyses synchronously but shape the response around a job resource for later queueing.
- [ ] Implement `GET /health/live` for process health and `GET /health/ready` for dependency readiness.
- [ ] Apply request-size limits, strict schema validation, and redaction rules so plaintext, ciphertext, and keys do not appear in routine logs.
- [ ] Generate stable OpenAPI operation IDs and include request/response examples.
- [ ] Define consistent problem responses with machine-readable error codes and
  correlation IDs.

### Test

- [ ] Add API contract tests with FastAPI's test client for success, validation, unsupported ciphers, payload limits, and error serialization.
- [ ] Verify domain exceptions map to intentional 4xx responses and unexpected failures map to sanitized 5xx responses.
- [ ] Save and review the generated OpenAPI document for accidental contract
  changes; add snapshot checking only if maintenance remains practical.

### Exit Criteria

- [ ] OpenAPI documentation describes every public endpoint and schema.
- [ ] Route handlers only translate HTTP data and call application services.
- [ ] The CLI and API exercise the same application and domain behavior.

## Phase 5: PostgreSQL Persistence

**Outcome:** Analysis jobs and their lifecycle survive process restarts.

**Why now:** Persistence is warranted once analysis has a job identity and state.
Cipher encryption and decryption should remain stateless.

### Build

- [ ] Define an application-layer `AnalysisRepository` protocol before choosing database models.
- [ ] Model job identity, cipher type, sanitized parameters, status, timestamps, result summary, error code, and optimistic concurrency/version information.
- [ ] Define explicit states such as `pending`, `running`, `succeeded`, `failed`, and `cancelled`, with validated transitions.
- [ ] Add SQLAlchemy 2 and a PostgreSQL driver; implement the repository adapter under `infrastructure/persistence/`.
- [ ] Add Alembic and commit an initial schema migration. Never initialize the production schema with `create_all`.
- [ ] Add `POST /v1/analyses`, `GET /v1/analyses/{id}`, and a paginated listing endpoint backed by the repository.
- [ ] Decide a retention policy for sensitive source text and results. Prefer short retention or user opt-in storage; avoid persisting raw keys unnecessarily.
- [ ] Keep database URLs in environment-based settings and redact them from logs.

### Test

- [ ] Use an in-memory repository fake for application service tests.
- [ ] Use Testcontainers with real PostgreSQL for repository integration tests; do not rely on SQLite where PostgreSQL semantics matter.
- [ ] Test migrations from an empty database and from the previous schema version.
- [ ] Test pagination, concurrent updates, transition rejection, and retention deletion behavior.

### Exit Criteria

- [ ] Restarting the API does not lose submitted analysis metadata or results.
- [ ] Application and domain modules do not import SQLAlchemy.
- [ ] CI runs unit tests on every change and PostgreSQL integration tests on pull requests where Docker is available.

## Phase 6: Asynchronous Analysis Worker

**Outcome:** Expensive cryptanalysis runs outside API request processes and can scale independently.

**Why now:** Once analysis jobs are durable, queueing solves a real reliability and latency problem rather than adding distributed-system complexity for show.

### Build

- [ ] Gate this phase on evidence of need: build the queue and worker only once an analyzer's p95 runtime exceeds the HTTP request budget (for example the substitution hill-climbing solver). If it is built earlier to demonstrate the pattern for the portfolio, say so explicitly in the ADR rather than framing it as demand-driven.
- [ ] Define a `JobPublisher` application port and a serializable message schema containing a schema version and job ID, not the complete sensitive payload.
- [ ] Implement an Azure Service Bus adapter and retain an in-process adapter for fast tests and local demonstrations.
- [ ] Create `src/codebreakers/worker/` with a composition root that consumes a job, claims it atomically, runs the requested analyzer, and stores the result.
- [ ] Make processing idempotent so redelivery cannot create duplicate results or invalid state transitions.
- [ ] Configure bounded retries, exponential backoff, lock renewal, message completion, and dead-letter handling.
- [ ] Add cancellation checks and per-job time or resource budgets.
- [ ] Propagate correlation and trace context from API request to queue message and worker execution.
- [ ] Document how dead-letter messages are inspected, replayed, or discarded.

### Test

- [ ] Contract-test every `JobPublisher` adapter against shared behavior tests.
- [ ] Test duplicate delivery, transient failure, poison messages, cancellation, and worker termination during processing.
- [ ] Add an integration test against a local Service Bus-compatible strategy if support is adequate; otherwise use a dedicated Azure test namespace in a manually triggered CI workflow.

### Exit Criteria

- [ ] The API acknowledges a submitted job quickly and exposes status polling.
- [ ] A worker outage leaves jobs recoverable, and duplicate delivery is safe.
- [ ] API and worker can be scaled and deployed independently.

## Phase 7: Containers and Local Production Simulation

**Outcome:** The API and worker run as secure, reproducible container images with local supporting services.

**Why now:** Containerization is most informative after real runtime processes, database migrations, health checks, and configuration requirements exist.

### Build

- [ ] Create a multi-stage `Dockerfile` with dependency/build and minimal runtime stages, deterministic installs, an unprivileged user, and no development tools in the final image.
- [ ] Use one image with separate API and worker startup commands to avoid duplicating dependencies and release versions.
- [ ] Add `.dockerignore` for Git data, virtual environments, caches, tests where appropriate, secrets, and local artifacts.
- [ ] Add Docker Compose for API, worker, PostgreSQL, and any local messaging substitute. Include named volumes and health-based startup dependencies.
- [ ] Add a one-shot migration service or documented migration command rather than running competing migrations in every API replica.
- [ ] Handle termination signals and configure graceful shutdown for in-flight HTTP requests and worker messages.
- [ ] Document all environment variables in `.env.example` using fake values.

### Verify

- [ ] Build the image from a clean checkout and run it without bind-mounting the source tree.
- [ ] Run smoke tests against the Compose stack: health, encrypt, submit analysis, worker completion, and result retrieval.
- [ ] Scan the image with Trivy or an equivalent scanner and generate an SBOM.
- [ ] Confirm the runtime user is non-root and no credentials exist in image layers or build arguments.

### Exit Criteria

- [ ] One documented command starts a production-like local stack.
- [ ] API and worker containers pass health and graceful-shutdown checks.
- [ ] Image scanning has no unreviewed critical vulnerabilities.

## Phase 8: Azure Infrastructure as Code

**Outcome:** A reproducible Azure environment hosts the API, worker, data, queue, secrets, and telemetry with least-privilege identities.

**Why now:** Deploying the completed container boundaries makes infrastructure choices verifiable and keeps local and cloud architectures aligned.

### Design

- [ ] Choose one low-cost Azure region and define separate `dev` and `prod` environment inputs; deploy `dev` first.
- [ ] Record an ADR choosing Terraform, including remote state and provider
  version strategy.
- [ ] Draw a deployment diagram and list trust boundaries, public endpoints, and expected monthly cost before provisioning resources.
- [ ] Decide whether PostgreSQL and Service Bus are required in the first cloud demo. Document cheaper temporary substitutions rather than silently changing architecture.

### Provision with Terraform

- [ ] Create a resource group and Log Analytics workspace.
- [ ] Create Azure Container Registry and grant pull access through managed identity rather than registry passwords.
- [ ] Create a Container Apps environment and deploy separate API and worker apps from the same versioned image.
- [ ] Configure API ingress, revision mode, CPU/memory limits, health probes, minimum/maximum replicas, and HTTP scaling rules.
- [ ] Configure worker scaling from Service Bus queue depth, including scale to zero where job latency requirements permit it.
- [ ] Create Azure Database for PostgreSQL Flexible Server, database, network rules, backups, retention, and the smallest suitable development SKU.
- [ ] Create a Service Bus namespace, analysis queue, dead-letter policy, retry limits, and message lock settings.
- [ ] Create Key Vault and store only secrets that cannot use identity-based access. Reference secrets from Container Apps rather than copying them into Terraform outputs.
- [ ] Create Application Insights and connect API and worker telemetry.
- [ ] Assign narrowly scoped managed identity roles for ACR pull, Service Bus send/receive, Key Vault read, and telemetry.
- [ ] Add tags for application, environment, owner, and cost center; configure a budget alert to prevent portfolio infrastructure surprises.
- [ ] Store Terraform state in an Azure Storage account with locking, encryption, restricted access, and separate state per environment.

### Verify

- [ ] Run `terraform fmt`, `validate`, and a security scanner such as Checkov in CI; review every plan before apply.
- [ ] Deploy `dev`, execute end-to-end smoke tests, and verify queue-based scaling.
- [ ] Confirm the API has no database, queue, or registry passwords where managed identity is supported.
- [ ] Destroy and recreate the development environment from code to prove reproducibility, accounting for stateful data safeguards.

### Exit Criteria

- [ ] A public HTTPS API completes a queued analysis end to end.
- [ ] Infrastructure can be reproduced from Terraform plus documented bootstrap
  steps, with no portal-only configuration.
- [ ] Cost, backup, retention, identity, and teardown decisions are documented.

## Phase 9: Delivery, Security, and Observability

**Outcome:** Releases are automated, diagnosable, and protected by practical software supply-chain and runtime controls.

**Why now:** Deployment creates operational responsibilities. This phase turns a working cloud demo into evidence of production engineering judgment.

### Continuous Delivery

- [ ] Build images in GitHub Actions using immutable Git SHA tags; never deploy `latest` as the release identity.
- [ ] Cache dependencies and Docker layers without weakening reproducibility.
- [ ] Run unit, property, integration, migration, container smoke, dependency, secret, IaC, and image scans at appropriate workflow stages.
- [ ] Authenticate GitHub Actions to Azure using OpenID Connect federation, not a long-lived service-principal secret.
- [ ] Push to ACR, deploy a new Container Apps revision, run smoke tests, and only then shift traffic.
- [ ] Protect production with a GitHub environment, required review, and a clear rollback command to the previous healthy revision.
- [ ] Publish release notes and attach an SBOM and provenance where practical.

### Observability

- [ ] Emit structured JSON logs with timestamp, level, service, environment, version, correlation ID, job ID, and event name.
- [ ] Add OpenTelemetry traces across FastAPI, SQLAlchemy, Service Bus publishing, and worker consumption.
- [ ] Record RED metrics for the API and job throughput, duration, failure, retries, dead-letter count, and queue age for workers.
- [ ] Create an Azure dashboard or workbook for API health and job processing.
- [ ] Add alerts for sustained server errors, unhealthy revisions, dead-lettered messages, old queued jobs, and PostgreSQL capacity.
- [ ] Verify telemetry excludes plaintext, ciphertext, keys, database URLs, authorization headers, and secret values.
- [ ] Write a runbook for each alert, plus deployment rollback, queue backlog, dead-letter recovery, database restore, and credential compromise.

### Security

- [ ] Create a concise threat model covering untrusted text, denial of service, sensitive historical material, dependency compromise, queue tampering, and unauthorized data access.
- [ ] State plainly in the threat model and user-facing docs that these are broken classical ciphers offering zero confidentiality and must never protect real secrets.
- [ ] Frame log redaction and data-sensitivity controls around user-submitted content privacy (for example a real historical document someone is analysing), not key secrecy, so the controls are not security theatre over non-secret keys.
- [ ] Add API authentication only when user-specific saved jobs are introduced; use Microsoft Entra ID rather than a custom password system.
- [ ] Enforce authorization by resource ownership if accounts are added.
- [ ] Add rate limiting and workload limits before advertising a public endpoint.
- [ ] Pin GitHub Actions by commit SHA and review dependency update automation.
- [ ] Document vulnerability response and data deletion procedures.

### Exit Criteria

- [ ] A tagged release can progress from source to Azure without local commands.
- [ ] A failed rollout can return to the previous revision with documented steps.
- [ ] An operator can find a request and its worker job using one correlation ID.
- [ ] Alerts are tested deliberately rather than assumed to work.

## Phase 10: Portfolio and Documentation Finish

**Outcome:** A reviewer can understand the problem, run the project, inspect the engineering decisions, and see the deployed system without reverse engineering the repository.

**Why now:** Strong implementation is not self-explanatory. Concise evidence and honest trade-offs make the project useful in interviews and on a CV.

### Build the Project Narrative

- [ ] Rewrite the README around the actual delivered capabilities, not planned features. Include a short demo, architecture diagram, local quick start, test commands, deployed API link, and documentation index.
- [ ] Add `docs/domain.md` explaining cipher abstractions, analysis capabilities, scoring, limitations, and links to historical source material.
- [ ] Add `docs/operations.md` covering configuration, deployment, migration, observability, backup, restore, rollback, and teardown.
- [ ] Keep ADRs for consequential choices and alternatives: protocols, job state machine, PostgreSQL, Service Bus, Container Apps, Terraform, and identity.
- [ ] Publish benchmark results with reproducible commands and avoid unsupported
  performance claims.
- [ ] Add an API usage collection or executable examples using `curl`.
- [ ] Add a short screen recording or screenshots showing CLI use, OpenAPI, queued analysis, Azure telemetry, and a deployment workflow.
- [ ] Add a roadmap section distinguishing completed work from future ideas.

### Prepare CV and Interview Evidence

- [ ] Capture measurable facts: number of cipher families, property-test cases, coverage, image size, deployment time, request latency, job throughput, and monthly development-environment cost.
- [ ] Prepare a two-minute architecture explanation focused on boundaries and why the system evolved in phases.
- [ ] Prepare examples of one rejected design, one production failure simulated in testing, and one security or cost trade-off.
- [ ] Prefer precise CV language such as: "Built and deployed a typed Python cryptanalysis service on Azure Container Apps with asynchronous Service Bus workers, PostgreSQL persistence, OpenTelemetry, and Terraform-based delivery."
- [ ] Do not claim enterprise scale, zero downtime, or security guarantees that were not measured and demonstrated.

### Final Exit Criteria

- [ ] A new contributor can run the project locally from the README.
- [ ] The public demo has a controlled cost and can be shut down cleanly.
- [ ] CI, deployment, rollback, telemetry, and core cipher behavior are each demonstrated by a repeatable artifact or test.

## Cross-Phase Definition of Done

Apply these checks to every pull request or incremental milestone:

- [ ] Public behavior has tests, including failure and boundary cases.
- [ ] `pytest`, `ruff check .`, `ruff format --check .`, and `mypy src` pass.
- [ ] New configuration and operational behavior are documented.
- [ ] No secret, key, plaintext, or sensitive ciphertext is committed or logged.
- [ ] Dependencies point inward; framework concerns remain in adapters.
- [ ] Database changes have forward migrations and a tested deployment strategy.
- [ ] User-visible and message schemas remain backward compatible or are explicitly versioned.
- [ ] Performance-sensitive changes include a repeatable measurement.
- [ ] The smallest useful increment is merged before starting the next large
  architectural feature.

## Recommended Release Milestones

These are release tags applied at milestones, distinct from the per-phase
`phase-{n}-short-description` feature branches.

- **v0.1 - Library:** Caesar domain API, tests, quality gates, and documentation.
- **v0.2 - CLI:** Installable command-line encryption and decryption workflow.
- **v0.3 - Cipher toolkit:** Substitution, Vigenere, homophonic models, and basic cryptanalysis with benchmarks.
- **v0.4 - Service:** Versioned FastAPI application with documented contracts.
- **v0.5 - Durable jobs:** PostgreSQL persistence and asynchronous worker flow.
- **v0.6 - Containerized:** Reproducible API/worker images and local Compose stack.
- **v0.7 - Azure:** Terraform-managed development deployment on Container Apps.
- **v1.0 - Portfolio release:** Automated delivery, operational telemetry, security review, polished documentation, and a repeatable public demo.
