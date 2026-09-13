# Codebreakers

A Python project for solving codebreaking problems to serve as a practical guide.

## Inspiration

This repository presents historical examples of encrypted communications, including postcards, diaries, letters, and telegrams, and demonstrates how to analyze and decrypt them using Python. Its broader objective is to illustrate enterprise-grade engineering practices, architecture, and coding standards.

## Development

Create and activate a virtual environment, then install the project with its development tools:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Run the checks:

```bash
pytest
ruff check .
ruff format --check .
mypy src
```

The package source lives in `src/codebreakers`, and tests live in `tests`.
