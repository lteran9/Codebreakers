# Contributing

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
pre-commit install
```

## Typical checks

```bash
make install
make format
make lint
make typecheck
make test
make coverage
```

`make integration` runs the PostgreSQL integration tests and needs a running
Docker daemon. The Service Bus `JobPublisher` contract is skipped unless
`CODEBREAKERS_TEST_SERVICEBUS_CONNECTION_STRING` points at a dedicated test
namespace. In CI it runs only from the manually triggered
`Service Bus integration` workflow.

## Commit and pull request guidance

- Keep changes focused and small.
- Add or update tests for user-visible behavior.
- Prefer deterministic, example-based tests for cipher rules.
- Run the full local quality gate before opening a pull request.
- Open pull requests against the default branch with a clear narrative of the change.
