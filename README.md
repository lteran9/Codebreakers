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

Run the checks locally with:

```bash
pytest
ruff check .
ruff format --check .
mypy src tests
```

The package source lives in `src/codebreakers`, and tests live in `tests`.

## Architecture notes

The engineering baseline includes a lightweight ADR set under `docs/architecture/` to make the repository's layout and decisions explicit. This helps future phases avoid accidental framework leakage and inconsistent data-handling choices.
