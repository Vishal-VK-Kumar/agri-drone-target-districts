PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python

# .env is created from .env.example on first run; variables on the make
# command line (e.g. `make load DB_BACKEND=duckdb`) override it
-include .env
export

.PHONY: up down manifest load stage alias analyse

.env:
	cp .env.example .env

$(PY): requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.txt
	touch $(PY)

up: .env
	docker compose up -d --wait

down:
	docker compose down

manifest: $(PY)
	$(PY) python/01_download.py

load: manifest
	$(PY) python/02_load.py

stage: load
	$(PY) python/03_stage.py

alias: stage
	$(PY) python/04_district_lineage.py

analyse:
	@echo "analyse: marts and analysis stages are not built yet" >&2
	@exit 1
