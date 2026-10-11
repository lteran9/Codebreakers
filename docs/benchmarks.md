# Benchmarks and Measurements

Every number in this repository's documentation comes from one of the
commands below. The raw output is committed under [evidence/](evidence/).
Rerun a command before quoting its number somewhere new. Results depend on
hardware, network, and whether the Azure apps were idle.

## What is and is not claimed

- These are **single-machine, single-client** measurements of a demo
  deployment. They are not load tests, capacity limits, or SLOs.
- Throughput figures describe one small batch. The API deliberately limits
  each client to 10 analysis submissions a minute, and the dev worker
  scales to at most 3 replicas.
- Cold-start figures depend on how long the apps were idle and on Azure
  platform behaviour that this project does not control.

## Analyzer and cipher benchmarks (local)

```bash
make benchmark          # python scripts/benchmark.py
python scripts/benchmark.py --sizes 128 2048 20000 --iterations 5
```

The script ([scripts/benchmark.py](../scripts/benchmark.py)) encrypts a fixed
English passage, then times the median of 5 runs for each analyzer and each
cipher round trip. For Caesar and Vigenère it also checks that the top
candidate's key equals the real one (`key_recovered`). Raw output:
[evidence/benchmark-2026-10-12.json](evidence/benchmark-2026-10-12.json).

Environment: Apple M5 Pro, macOS 26.4, Python 3.12.15, 2026-10-12.

| Benchmark | 128 chars | 2,048 chars | 20,000 chars (API cap) |
| --- | ---: | ---: | ---: |
| `caesar-bruteforce` | 2.0 ms | 27.5 ms | 270 ms |
| `vigenere-frequency` | 12.6 ms | 120 ms | 1,111 ms |
| `substitution-frequency` | 0.05 ms | 0.57 ms | 5.6 ms |
| `homophonic-distribution` | 0.03 ms | 0.17 ms | 1.5 ms |
| Caesar encrypt + decrypt | 0.10 ms | 1.6 ms | 14.8 ms |
| Vigenère encrypt + decrypt | 0.11 ms | 1.9 ms | 17.6 ms |
| Substitution encrypt + decrypt | 0.10 ms | 1.6 ms | 15.2 ms |
| Homophonic encrypt + decrypt | 0.08 ms | 1.2 ms | 11.1 ms |

| Key recovered? | 128 chars | 2,048 chars | 20,000 chars |
| --- | :---: | :---: | :---: |
| Caesar, shift 3 | yes | yes | yes |
| Vigenère, `LANTERN` (7 letters) | **no** | yes | yes |

What this shows:

- All analyzers scale roughly linearly with input size.
- At the 20,000-character cap, the slowest analyzer, Vigenère, finishes in
  about 1.1 s. That is inside a synchronous request budget, which is why
  [ADR-0009](architecture/ADR-0009-async-analysis-worker.md) says the queue was
  built ahead of demand.
- Vigenère key recovery needs more than 128 characters for a 7-letter key.
  See [domain.md](domain.md#limitations).

## Deployed API (Azure dev)

```bash
make azure-measure      # reads the API URL from the Terraform output
python scripts/measure_api.py --base-url <url> --requests 50 --batches 2 --batch-size 5
```

The script ([scripts/measure_api.py](../scripts/measure_api.py)) uses only
the standard library and stays inside the API's rate limits. It never prints
the URL. It measures:

- the time to the first health response;
- the client-observed latency of 50 sequential Caesar encryptions;
- two batches of 5 queued Caesar analyses. Each job's queue-to-completion
  time comes from the server's `created_at` and `completed_at`, so polling
  does not skew it.

Raw output:
[evidence/azure-dev-measure-2026-10-12.json](evidence/azure-dev-measure-2026-10-12.json).

Environment: Azure Container Apps (consumption), `westus3`, API at 0.5 vCPU /
1 GiB; worker 0.5 vCPU / 1 GiB. The client was the developer's workstation,
over the public internet. Measured 2026-10-12 09:44 PDT, after the apps had
been idle.

| Measurement | Result |
| --- | --- |
| First response from idle (API cold start) | 42.2 s |
| `POST /v1/ciphers/caesar/encrypt`, warm, n=50 | p50 46.7 ms, p95 56.9 ms, max 63.9 ms |
| `POST /v1/analyses` (202 Accepted), n=10 | p50 61.0 ms, max 88.2 ms |
| Queued analysis, first batch from idle (5 jobs) | median 59.6 s queue-to-completion |
| Queued analysis, second batch, warm (5 jobs) | median 2.7 s, max 4.2 s, 5 jobs in 4.4 s |

What this shows:

- Scale to zero has a visible price. The first request waits about 40 s for
  an API replica. The first queued job waits about 1 minute, for KEDA to
  notice the outbox and queue rows and start the relay and worker.
  [ADR-0012](architecture/ADR-0012-azure-dev-environment.md) predicted 1–2
  minutes.
- Once warm, a queued analysis completes in a few seconds. Most of that time
  is the relay and worker polling intervals, not analysis.
- Warm synchronous latency is dominated by the network round trip.

## Local stack (for comparison)

```bash
docker compose up --build --wait
python scripts/measure_api.py --base-url http://127.0.0.1:8000
```

`codebreakers serve` on its own (in-process queue) answered encrypt requests
in about 1 ms and completed queued analyses in about 0.2 s on the benchmark
machine. Those figures exclude any network and should not be compared with
Azure.
