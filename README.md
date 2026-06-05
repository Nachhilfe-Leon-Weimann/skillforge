# SkillForge

Backend of the **skill-platform** and source of truth for SkillBot (the Discord bot).
SkillForge orchestrates the bot through an OAuth2-protected REST API, a Postgres-backed
job queue, and two-phase `prepare`/`commit` operations - Forge plans and confirms, the bot
performs the Discord actions.

```
HTTP -> app/api/v1        endpoints, schemas, scope checks
        app/services/bot  business logic (transitions, jobs, permissions)
        app/core          auth, db, logging, config
                          `-> Postgres  (core, geo, ext, bot, auth)
```

## Tech stack

Python 3.14, FastAPI, SQLAlchemy 2 (async/`asyncpg`), Alembic, PostgreSQL (Neon in prod),
Pydantic Settings, OAuth2 client credentials + JWT (PyJWT, Argon2), pytest + testcontainers,
[uv](https://docs.astral.sh/uv/), [just](https://just.systems/).

## Quickstart

```bash
uv sync          # install dependencies
just check       # lint + format + typecheck + openapi-check + fast tests (no DB)
just test        # full suite incl. DB tests
```

The DB tests spin up an ephemeral Postgres via [testcontainers](https://testcontainers.com/) -
no manual database needed (Docker required; they are skipped if Docker is unavailable).
Set `TEST_DB__URL` to run them against an existing Postgres instead.

## Running the API locally

The server needs a Postgres reachable via `DB__URL`. Bring one up however you like, e.g.:

```bash
docker run -d --name skillforge-pg -p 5432:5432 \
  -e POSTGRES_USER=skillforge -e POSTGRES_PASSWORD=skillforge -e POSTGRES_DB=skillforge \
  postgres:17
```

```bash
cp .env.example .env          # then set DB__URL + AUTH__SECRET_KEY
uv run alembic upgrade head   # apply migrations
just bootstrap-skillbot       # seed the initial auth state
just dev                      # start the API on http://localhost:8000
```

Health check: `GET /health` (checks DB connectivity). Interactive API docs at `/docs`.

## Common commands

| Command | Purpose |
|---|---|
| `just dev` | Start the API with auto-reload |
| `just check` | Lint + format + typecheck + openapi-check + tests (without DB) |
| `just test` / `just test-db` | All tests / DB-only tests (`@pytest.mark.db`, via testcontainers) |
| `just openapi` | Regenerate `openapi.json` (the API contract) |

Full list in the [`justfile`](justfile).

## Documentation

| Question | Where |
|---|---|
| How does it all fit together? | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| How is the DB structured? | [`docs/DATABASE_SCHEMA.md`](docs/DATABASE_SCHEMA.md) |
| Why was X decided this way? | [`docs/decisions/`](docs/decisions/) (ADRs) |
| What is planned? | [`docs/specs/`](docs/specs/) |
| Conventions & commands (for AI/onboarding) | [`CLAUDE.md`](CLAUDE.md) |
| What does the API look like? | [`openapi.json`](openapi.json) |
