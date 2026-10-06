.PHONY: install format lint typecheck test integration coverage openapi serve worker relay migrate \
	lock image image-check up down smoke shutdown-check scan sbom \
	tf-fmt tf-validate tf-test tf-scan tf-check tf-init tf-plan tf-apply \
	azure-image azure-migrate azure-smoke

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

# --- Azure infrastructure (see docs/operations/azure-runbook.md) ---

TERRAFORM ?= terraform
TF_ENV ?= dev
TF_DIR := infra/azure
TF_OUTPUT = $(TERRAFORM) -chdir=$(TF_DIR) output -raw
IMAGE_TAG ?= $(shell git rev-parse HEAD)
CHECKOV_IMAGE := bridgecrew/checkov:3.3.24@sha256:395ab21f8dc5d457f05001598638a98d142a98e264d610650c084d0ef2e1b4a5

tf-fmt:
	$(TERRAFORM) fmt -check -recursive infra

tf-validate:
	for dir in infra/bootstrap $(TF_DIR); do \
		$(TERRAFORM) -chdir=$$dir init -backend=false -input=false > /dev/null && \
		$(TERRAFORM) -chdir=$$dir validate || exit 1; \
	done

# Mock providers: no Azure credentials needed. Checks both environments' inputs.
tf-test:
	$(TERRAFORM) -chdir=$(TF_DIR) init -backend=false -input=false > /dev/null
	$(TERRAFORM) -chdir=$(TF_DIR) test -var-file=environments/dev.tfvars
	$(TERRAFORM) -chdir=$(TF_DIR) test -var-file=environments/prod.tfvars

tf-scan:
	docker run --rm -v "$(CURDIR):/src:ro" -w /src $(CHECKOV_IMAGE) \
		--directory infra --framework terraform --compact --quiet

tf-check: tf-fmt tf-validate tf-test tf-scan

tf-init:
	@test -n "$(TF_STATE_ACCOUNT)" || \
		{ echo "Set TF_STATE_ACCOUNT to the bootstrap storage_account_name output."; exit 1; }
	$(TERRAFORM) -chdir=$(TF_DIR) init -reconfigure \
		-backend-config=environments/$(TF_ENV).backend.hcl \
		-backend-config=storage_account_name=$(TF_STATE_ACCOUNT)

# Save the plan for review; tf-apply applies exactly that reviewed plan.
tf-plan:
	$(TERRAFORM) -chdir=$(TF_DIR) plan -var-file=environments/$(TF_ENV).tfvars \
		-var image_tag=$(IMAGE_TAG) -out=$(TF_ENV).tfplan

tf-apply:
	$(TERRAFORM) -chdir=$(TF_DIR) apply $(TF_ENV).tfplan

# Builds in Azure (linux/amd64) from the clean Git tree; no local Docker push.
azure-image:
	@test "$(IMAGE_TAG)" = "$$(git rev-parse HEAD)" || \
		{ echo "IMAGE_TAG must match the checked-out Git SHA."; exit 1; }
	@test -z "$$(git status --porcelain)" || \
		{ echo "Commit changes first: the tag is the Git SHA."; exit 1; }
	az acr build --registry $$($(TF_OUTPUT) container_registry_name) \
		--image codebreakers:$(IMAGE_TAG) --platform linux/amd64 .

azure-migrate:
	scripts/run_azure_job.sh $$($(TF_OUTPUT) resource_group_name) $$($(TF_OUTPUT) migration_job_name)

azure-smoke:
	python scripts/smoke_test.py --base-url $$($(TF_OUTPUT) api_url) --timeout 300
