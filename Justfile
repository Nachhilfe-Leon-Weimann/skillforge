# --- FastAPI ---

dev:
    @echo "Starting the SkillForge API server..."
    @fastapi dev

# --- Testing ---

test:
    uv run pytest -m "not db"

test-db:
    uv run pytest -m db

test-all:
    uv run pytest

test-v:
    uv run pytest -v

test-file file:
    uv run pytest {{ file }}

test-one test:
    uv run pytest -k {{ test }}

# --- Local Postgres ---

postgres := "../infra/postgres/justfile"

pg-up:
    just --justfile {{ postgres }} up

pg-down:
    just --justfile {{ postgres }} down

pg-restart:
    just --justfile {{ postgres }} restart

pg-logs:
    just --justfile {{ postgres }} logs

pg-psql:
    just --justfile {{ postgres }} psql

pg-bash:
    just --justfile {{ postgres }} bash

pg-reset:
    just --justfile {{ postgres }} reset

pg-ps:
    just --justfile {{ postgres }} ps

pg-create-db name:
    just --justfile {{ postgres }} create-db {{ name }}

pg-drop-db name:
    just --justfile {{ postgres }} drop-db {{ name }}
