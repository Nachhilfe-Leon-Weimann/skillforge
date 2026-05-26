set shell := ["bash", "-cu"]

# --- Quality ---

lint:
    uv run ruff check

format-check:
    uv run ruff format --check

typecheck:
    uv run ty check

check: lint format-check typecheck test-without-db

check-all: lint format-check typecheck test

# --- FastAPI ---

skillcore := "../skillcore"

dev:
    @echo "Starting the SkillForge API server..."
    @uv run fastapi dev --reload-dir app

dev-local-core:
    @echo "Starting the SkillForge API server with editable SkillCore..."
    @uv run --with-editable {{ skillcore }} fastapi dev --reload-dir app --reload-dir {{ skillcore }}/src

# --- Testing ---

test:
    uv run pytest

test-db:
    uv run pytest -m db

test-without-db:
    uv run pytest -m "not db"

test-v:
    uv run pytest -v

test-file file:
    uv run pytest {{ file }}

test-one test:
    uv run pytest -k {{ test }}

# --- Auth ---

bootstrap-skillbot:
    uv run python -m app.core.auth.bootstrap

# --- Versioning ---

create-version-bump bump="patch" version="":
    gh workflow run version-bump.yml --field bump="{{ bump }}" --field version="{{ version }}"

version-info bump="patch" version="":
    @python scripts/version.py info "{{ bump }}" "{{ version }}"

release-version:
    @python scripts/version.py release

bump-version version:
    @python scripts/version.py bump "{{ version }}"
    @uv lock

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
