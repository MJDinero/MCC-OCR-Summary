SHELL := /bin/bash

PYTHON ?= python3
ENV_FILE ?= .env.mcc

PROJECT_ID ?= quantify-agent
REGION ?= us-central1
SERVICE ?= mcc-ocr-summary
WORKFLOW_NAME ?= docai-pipeline
STATE_BUCKET ?= mcc-state-quantify-agent-us-central1-322786
INTAKE_GCS_BUCKET ?= mcc-intake
OUTPUT_GCS_BUCKET ?= mcc-output
SA_EMAIL ?= mcc-orch-sa@quantify-agent.iam.gserviceaccount.com
AR_REPO ?= mcc
CONCURRENCY ?= 2
CPU ?= 1
MEMORY ?= 2Gi
TIMEOUT ?= 120
MIN_INSTANCES ?= 1
MAX_INSTANCES ?= 10
LOG_LEVEL ?= INFO
DOC_AI_PROCESSOR_ID ?= 21c8becfabc49de6
DOC_AI_SPLITTER_PROCESSOR_ID ?= ec5f62394c69cb16
SUMMARY_SCHEMA_VERSION ?= 2025-10-01
DEPLOY_ENV_VARS := REGION=$(REGION),WORKFLOW_NAME=$(WORKFLOW_NAME),STATE_BUCKET=$(STATE_BUCKET),INTAKE_GCS_BUCKET=$(INTAKE_GCS_BUCKET),OUTPUT_GCS_BUCKET=$(OUTPUT_GCS_BUCKET),DOC_AI_SPLITTER_PROCESSOR_ID=$(DOC_AI_SPLITTER_PROCESSOR_ID),SUMMARY_SCHEMA_VERSION=$(SUMMARY_SCHEMA_VERSION),LOG_LEVEL=$(LOG_LEVEL)

ifneq (,$(wildcard $(ENV_FILE)))
include $(ENV_FILE)
export $(shell sed -n 's/^\([A-Z0-9_]\+\)=.*/\1/p' $(ENV_FILE))
endif

GIT_SHA ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
IMAGE_REGISTRY ?= $(REGION)-docker.pkg.dev/$(PROJECT_ID)/$(AR_REPO)/$(SERVICE)
IMAGE ?= $(IMAGE_REGISTRY):$(GIT_SHA)
ENV_ARGS := $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE),)
PYTEST ?= pytest
COMMON_PYTEST_FLAGS ?= -q --disable-warnings --maxfail=1

.PHONY: install lint type test test-integration test-e2e docker-build docker-run deploy smoke

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt -c constraints.txt
	$(PYTHON) -m pip install -r requirements-dev.txt -c constraints.txt

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m pylint --rcfile=.pylintrc src

TYPE_MODULES := src/services/metrics.py src/runtime_server.py src/services/ocr_service.py src/services/summarization_service.py src/services/storage_service.py

type:
	$(PYTHON) -m mypy --strict $(TYPE_MODULES)

test:
	$(PYTEST) $(COMMON_PYTEST_FLAGS)

test-integration:
	$(PYTEST) $(COMMON_PYTEST_FLAGS) tests/test_*integration.py

integration:
	$(PYTHON) -m pytest -m integration $(COMMON_PYTEST_FLAGS)

test-e2e:
	$(PYTEST) $(COMMON_PYTEST_FLAGS) tests/test_pipeline_endpoints.py

verify: test
	PYTHONPATH=$(PWD):$$PYTHONPATH $(PYTHON) scripts/smoke_test.py

docker-build:
	docker build --build-arg GIT_SHA=$(GIT_SHA) -t $(IMAGE) .

docker-run:
	docker run --rm -p 8080:8080 $(ENV_ARGS) $(IMAGE)

deploy:
	gcloud run deploy $(SERVICE) \
		--image $(IMAGE) \
		--region $(REGION) \
		--service-account $(SA_EMAIL) \
		--concurrency $(CONCURRENCY) \
		--cpu $(CPU) \
		--memory $(MEMORY) \
		--timeout $(TIMEOUT) \
		--min-instances $(MIN_INSTANCES) \
		--max-instances $(MAX_INSTANCES) \
		--no-allow-unauthenticated \
		--update-env-vars $(DEPLOY_ENV_VARS)

smoke:
	$(PYTHON) scripts/smoke_test.py

benchmark:
	$(PYTHON) scripts/benchmark_large_docs.py

sbom:
	$(PYTHON) -m pip freeze --all | cyclonedx-py requirements - --of JSON --output-file outputs/sbom.json

audit-deps:
	pip-audit --format json --ignore-vuln GHSA-4xh5-x5gv-qwph -o outputs/pip-audit.json
