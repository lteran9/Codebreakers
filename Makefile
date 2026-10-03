.PHONY: install format lint typecheck test integration coverage openapi serve worker relay migrate

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
	pytest -m 'not integration'

integration:
	pytest -m integration -o addopts=''

coverage:
	pytest -m 'not integration' --cov=codebreakers --cov-report=term-missing --cov-fail-under=80

openapi:
	python -c "from codebreakers.api.openapi import render_openapi_document as r; print(r(), end='')" > docs/api/openapi.json

serve:
	codebreakers serve

worker:
	codebreakers worker

relay:
	codebreakers relay

migrate:
	alembic upgrade head
