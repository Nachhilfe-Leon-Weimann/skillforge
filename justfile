set shell := ["bash", "-cu"]

# --- Quality ---

lint:
    uv run ruff check

format-check:
    uv run ruff format --check

typecheck:
    uv run ty check

static-checks: lint format-check typecheck openapi-check docs-check

check: static-checks test-without-db

check-all: static-checks test

# --- Contract ---

# Regenerate openapi.json (the source of truth API consumers generate clients from).
openapi:
    uv run python scripts/dump_openapi.py

# Fail if the committed openapi.json is stale relative to the current API.
openapi-check: openapi
    #!/usr/bin/env bash
    if git diff --exit-code -- openapi.json; then
        printf '\033[1;32mopenapi.json is up to date!\033[0m\n'
    else
        printf '\033[1;31mopenapi.json is stale: run `just openapi` and commit.\033[0m\n' >&2
        exit 1
    fi

# --- Docs ---

# Fail on any Markdown line-number anchor into source or dead relative link (drift guard).
docs-check:
    uv run python scripts/check_docs.py

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

test-coverage:
    uv run pytest --cov=app --cov-report=term-missing:skip-covered --cov-report=xml:coverage.xml

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

# --- Workers ---

# Run the lifecycle guardian (job reaper + operation sweeper) loop locally.
worker-reaper:
    uv run python -m app.workers.reaper

# --- Dead-letter queue ---

# List dead-lettered (FAILED) jobs with kind, last_error, failed_at.
dead-jobs:
    uv run python -m app.cli.deadletters list

# Requeue a dead-lettered job: reset it to PENDING (attempt 0) and make it claimable now.
requeue job_id:
    uv run python -m app.cli.deadletters requeue {{ job_id }}

# --- Auth ---

bootstrap-skillbot:
    uv run python -m app.core.auth.bootstrap

# --- Docker ---

docker-build image="skillforge:local":
    @test -n "${SKILLPLATFORM_READ_TOKEN:-}" || (echo 'Set SKILLPLATFORM_READ_TOKEN, for example: export SKILLPLATFORM_READ_TOKEN=$(gh auth token)' >&2; exit 1)
    docker build --secret id=github_token,env=SKILLPLATFORM_READ_TOKEN -f dockerfile -t ghcr.io/skillforge:sha-$(git rev-parse --short=12 HEAD) .
    docker tag ghcr.io/skillforge:sha-$(git rev-parse --short=12 HEAD) {{ image }}

docker-run image="skillforge:local":
    docker run --rm --env-file .env -p 8000:8000 {{ image }}

# --- Versioning ---

create-version-bump bump="patch" version="":
    gh workflow run version-bump.yml --field bump="{{ bump }}" --field version="{{ version }}"

version-info bump="patch" version="":
    @python scripts/version.py info "{{ bump }}" "{{ version }}"

release-version:
    @python scripts/version.py release

bump-version version:
    @uv version "{{ version }}"
    @just openapi

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
