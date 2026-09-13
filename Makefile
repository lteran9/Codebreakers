.PHONY: install format lint typecheck test coverage

install:
	python -m pip install --upgrade pip
	python -m pip install -e '.[dev]'
	pre-commit install

format:
	ruff format .

lint:
	ruff check .

typecheck:
	mypy src tests

test:
	pytest

coverage:
	pytest --cov=codebreakers --cov-report=term-missing --cov-fail-under=80
