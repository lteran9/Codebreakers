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

## Dependencies and containers

Runtime and build dependencies are locked with hashes in `requirements/`.
After changing `dependencies` in `pyproject.toml`, run `make lock`, which needs
Docker, and commit the regenerated files. A unit test fails when the lock no
longer satisfies `pyproject.toml`. Before changing the `Dockerfile` or
`compose.yaml`, run `make up smoke shutdown-check image-check scan`.

## Commit and pull request guidance

- Keep changes focused and small.
- Add or update tests for user-visible behavior.
- Prefer deterministic, example-based tests for cipher rules.
- Run the full local quality gate before opening a pull request.
- Open pull requests against the default branch with a clear narrative of the change.
