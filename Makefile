.PHONY: install format lint typecheck test coverage openapi serve

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

openapi:
	python -c "from codebreakers.api.openapi import render_openapi_document as r; print(r(), end='')" > docs/api/openapi.json

serve:
	codebreakers serve
