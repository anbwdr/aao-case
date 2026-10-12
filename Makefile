# AAO case. Every step is done when `make verify` is green (PLAN.md §6).
DATAFORM := npx -y @dataform/cli@3.0.71
ENV := source infra/env.sh &&
SHELL := /bin/bash

.PHONY: help wizard setup deploy-workflow deploy-publish upload upload-ou scenario scenario-reset \
        oracle compile local local-full malloy-check verify-cloud verify lint malloy

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

# --- setup -------------------------------------------------------------------

wizard: ## Guided human-only setup (resumable; infra/wizard.sh --status for the checklist)
	infra/wizard.sh

setup: ## Idempotent GCP setup and deploys (infra/setup.sh)
	infra/setup.sh

deploy-workflow: ## Redeploy ingest/workflow.yaml
	infra/setup.sh workflow

deploy-publish: ## Redeploy the publish Cloud Run job
	infra/setup.sh publish

# --- pipeline ----------------------------------------------------------------

upload: ## Land batch 1 and wait for the pipeline (--run-now)
	ingest/upload.sh --run-now

upload-ou: ## Land batch 2: Over/Under 2.5 markets, no bets
	ingest/upload.sh --ou --run-now

scenario: ## Scenario push: season over in the *_scenario datasets (PLAN.md §4.1)
	ingest/upload.sh --scenario --run-now

scenario-reset: ## Drop every *_scenario dataset. Graded datasets are never touched
	@$(ENV) for ds in "$${SCENARIO_DATASETS[@]}"; do \
	  bq --project_id="$$PROJECT_ID" rm -r -f --dataset "$$PROJECT_ID:$$ds" && echo "dropped $$ds"; \
	done; \
	bq --project_id="$$PROJECT_ID" --location="$$BQ_LOCATION" mk --dataset "$$PROJECT_ID:raw_scenario" >/dev/null; \
	bq --project_id="$$PROJECT_ID" --location="$$BQ_LOCATION" query --nouse_legacy_sql --quiet \
	  "create table \`$$PROJECT_ID.raw_scenario.batch_manifest\` (batch_id string not null, table_name string not null, row_count int64 not null, source_uri string, loaded_at timestamp)" >/dev/null; \
	echo "raw_scenario recreated empty"

# --- checks ------------------------------------------------------------------

oracle: ## Reference KPIs on DuckDB -> checks/golden/
	uv run checks/oracle.py

compile: ## Compile Dataform (graded and scenario)
	$(DATAFORM) compile > /dev/null && $(DATAFORM) compile --vars=scenario=true > /dev/null
	@echo "dataform compile ok"

local: ## Compiled Dataform graph on DuckDB (batch 1): assertions + diff vs golden
	uv run checks/local_dataform.py

local-full: ## Same with batch 2 (O/U) and the scenario push; exports the marts for malloy-check
	uv run checks/local_dataform.py --ou --scenario --export-marts checks/.marts

malloy-check: ## Malloy model on the exported marts (DuckDB) vs golden
	cd malloy && npm ci --silent && node check.mjs ../checks/.marts

verify-cloud: ## BigQuery vs golden, Sheet vs BigQuery (skips loudly what is not set up)
	uv run checks/verify.py

lint: ## shellcheck + workflow YAML parse
	uv run --with shellcheck-py shellcheck infra/*.sh ingest/*.sh malloy/*.sh
	uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('ingest/workflow.yaml'))"
	@echo "lint ok"

verify: oracle compile lint local-full malloy-check verify-cloud ## Definition of done

# --- semantic layer ----------------------------------------------------------

malloy: ## Malloy Publisher on http://localhost:4000 (BigQuery via your ADC): packages aao, aao_scenario
	malloy/configure.sh
	cd malloy && npx -y @malloy-publisher/server@0.9.7 --server_root . --watch-env aao --init
