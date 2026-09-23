"""
Shared database connection for the pipeline stages (not a stage itself).

DB_BACKEND=postgres (default) connects to the docker-compose PostgreSQL 16.
DB_BACKEND=duckdb uses a local DuckDB file for machines without Docker; the
SQL files are written to run unchanged on both.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKENDS = ("postgres", "duckdb")


def backend() -> str:
    name = os.environ.get("DB_BACKEND", "postgres").lower()
    if name not in BACKENDS:
        raise SystemExit(f"FAIL: DB_BACKEND must be one of {BACKENDS}, got {name!r}")
    return name


def connect():
    if backend() == "duckdb":
        import duckdb

        path = REPO_ROOT / os.environ.get("DUCKDB_PATH", "data/warehouse.duckdb")
        path.parent.mkdir(parents=True, exist_ok=True)
        return duckdb.connect(str(path))

    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ.get("POSTGRES_USER", "agri"),
        password=os.environ.get("POSTGRES_PASSWORD", "agri"),
        dbname=os.environ.get("POSTGRES_DB", "agri"),
    )
