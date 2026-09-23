# SkillForge

Central service of the **skill-platform** and system of record for the people of the tutoring
business and how they relate (the CRM). What SkillForge is for - a hub for central data, identity and
permissions, not the backend of any one frontend - is laid out in the
[project sketch](docs/PROJECT.md).

Today SkillForge also orchestrates SkillBot (the Discord bot) through an OAuth2-protected REST
API, a Postgres-backed job queue, and two-phase `prepare`/`commit` operations - Forge plans and
confirms, the bot performs the Discord actions.

```
HTTP -> app/api/v1        endpoints, schemas, scope checks
        app/services/bot  business logic (transitions, jobs, permissions)
        app/core          auth, db, logging, config
                          `-> Postgres  (core, geo, ext, bot, auth, system)
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

Health check: `GET /health` aggregates the health of all dependencies and background workers
(sub-routes: `/health/live`, `/health/dependencies[/{name}]`, `/health/workers[/{name}]`).
Interactive API docs at `/docs`.

## Python client

Each SkillForge release publishes the generated [`skillforge-client`](https://pypi.org/project/skillforge-client/)
package from the matching `openapi.json` contract:

```bash
uv add skillforge-client
```

The generated client source lives only in `build/`; its maintained README and package metadata
templates live in `clients/python/templates/`.

## Releasing

Releases are driven by [release-please](https://github.com/googleapis/release-please): it keeps a
release PR (`chore(main): release X.Y.Z`) up to date with the next version and `CHANGELOG.md`, both
derived from the conventional commits on `main`. **Merging that PR (`gh pr merge <n> -sd --auto`) is the
release** - the `Release` workflow then tags `vX.Y.Z`, builds `ghcr.io/nachhilfe-leon-weimann/skillforge:vX.Y.Z`,
publishes `skillforge-client==X.Y.Z` and deploys through the Dokploy API, failing unless `GET /health`
reports the new version. To deploy the current release again: run the `Deploy` workflow by hand.
The why is in [`docs/specs/release-flow.md`](docs/specs/release-flow.md).

If a `Release` run fails after the release exists, fix the cause and use **Re-run failed jobs** - never
*Re-run all jobs*: release-please would find the release already created, report no new release, and every
later job would be skipped while the run turns green. To rebuild only the image, dispatch `Build` for the
tag; to deploy the current release again, dispatch `Deploy` with its version.

[`compose.yml`](compose.yml) pins the deployed version: the release PR rewrites its `image:` tags to `vX.Y.Z`
together with the version bump, so `main` names what prod runs and a restart of the stack cannot pull a
different version. `:latest` is still published, but nothing deploys from it.

### Rolling back

There is no automatic rollback. To go back to an earlier release:

1. Open a PR that sets the earlier tag on every `image:` line of `compose.yml` and merge it. Title it
   `chore(deploy): roll back to vX.Y.Z` - a `chore` neither shows up in the changelog nor causes a release.
2. Dispatch `Deploy` with that earlier version; the run fails unless `GET /health` reports it.

Nothing has to be undone afterwards: the next release PR rewrites the tags to its own version.

This rolls back the app, never the database. If a release in between shipped an Alembic migration, the earlier
image's `migrate` service cannot locate the database's revision and the deployment fails - fix forward
instead, or first downgrade the schema from the newer image (`alembic downgrade <revision>`).

## Common commands

| Command | Purpose |
|---|---|
| `just dev` | Start the API with auto-reload |
| `just check` | Lint + format + typecheck + openapi-check + tests (without DB) |
| `just test` / `just test-db` | All tests / DB-only tests (`@pytest.mark.db`, via testcontainers) |
| `just openapi` | Regenerate `openapi.json` (the API contract) |
| `just test-clients` | Generate, build, and smoke-test the distributable API clients |

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
