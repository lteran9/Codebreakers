.PHONY: install format lint typecheck test integration coverage openapi serve worker relay migrate \
	lock image image-check up down smoke shutdown-check scan sbom

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

# --- Containers (see docs/architecture/ADR-0010-containers-local-stack.md) ---

IMAGE ?= codebreakers:local
PYTHON_IMAGE := $(shell sed -n 's/^ARG PYTHON_IMAGE=//p' Dockerfile)
TRIVY_IMAGE := aquasec/trivy:0.75.0@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa
SCAN_DIR := build/container
TRIVY := docker run --rm -v "$(CURDIR):/src:ro" -v "$(CURDIR)/$(SCAN_DIR):/work" \
	-v codebreakers-trivy-cache:/root/.cache/ $(TRIVY_IMAGE)

# Resolve on the runtime base image so Linux environment markers apply.
lock:
	docker run --rm -v "$(CURDIR):/src" -w /src $(PYTHON_IMAGE) sh -c '\
		pip install --quiet --root-user-action=ignore --disable-pip-version-check pip-tools && \
		pip-compile --quiet --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url \
			--output-file requirements/runtime.txt pyproject.toml && \
		pip-compile --quiet --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url \
			--output-file requirements/build.txt requirements/build.in'

image:
	docker build --tag $(IMAGE) .

image-check:
	scripts/check_image.sh $(IMAGE)

up:
	docker compose up --build --wait

down:
	docker compose down

smoke:
	python scripts/smoke_test.py

shutdown-check:
	scripts/check_shutdown.sh

$(SCAN_DIR)/image.tar: FORCE
	mkdir -p $(SCAN_DIR)
	docker save --output $@ $(IMAGE)

# Report HIGH and CRITICAL; fail on unreviewed CRITICAL vulnerabilities or any secret.
scan: $(SCAN_DIR)/image.tar
	$(TRIVY) image --quiet --input /work/image.tar --scanners vuln \
		--severity HIGH,CRITICAL --exit-code 0
	$(TRIVY) image --quiet --input /work/image.tar --scanners vuln \
		--ignorefile /src/.trivyignore --severity CRITICAL --exit-code 1
	$(TRIVY) image --quiet --input /work/image.tar --scanners secret \
		--image-config-scanners secret --ignorefile /src/.trivyignore --exit-code 1

sbom: $(SCAN_DIR)/image.tar
	$(TRIVY) image --quiet --input /work/image.tar --format cyclonedx \
		--output /work/sbom.cdx.json
	@echo "SBOM written to $(SCAN_DIR)/sbom.cdx.json"

FORCE:
