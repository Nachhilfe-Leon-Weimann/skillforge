# CLAUDE.md

Anchor for AI assistants and quick onboarding. Captures commands, layout, and conventions;
the deeper *why* lives in [`docs/`](docs/).

## Commands

Everything runs through [`just`](justfile) (which wraps `uv`):

- `just dev` - API with auto-reload (`http://localhost:8000`).
- `just check` - lint + format check + typecheck + `openapi-check` + tests without DB. **Keep
  green before every commit.** `just check-all` also includes DB tests.
- `just test`, `just test-db` (only `@pytest.mark.db`), `just test-without-db`,
  `just test-one <name>`, `just test-file <path>`.
- `just openapi` - regenerate `openapi.json`; `just openapi-check` checks for drift (CI).
- `just bootstrap-skillbot` - seed the initial auth state.

DB tests provision an ephemeral Postgres via testcontainers (needs Docker; skipped without it).
Set `TEST_DB__URL` to run them against an existing database. Running the API locally needs a
Postgres reachable via `DB__URL`.

## Layout

```
app/
  main.py            FastAPI entry point (root route + app wiring)
  api/system/        health.py (/health + /health/live, /health/dependencies[/{name}], /health/workers[/{name}])
  api/v1/            endpoints: auth/ (token, clients), bot/ (runtime, jobs, operations,
                     command_envs, students, tutors, users, authz), crm/ (parties, persons, companies,
                     roles, contact_infos, relations, subjects; params + schemas); common/ (shared API
                     vocabulary: error envelope + handlers, error_responses, Page/PageParams, DBSession,
                     OpenAPI hooks)
  services/bot/      business logic: transitions, operations, jobs, principals, provisioning,
                     authz, command_envs, contexts, profile, reaper, views, errors
  services/crm/      system of record: parties (PARTY_GRAPH, load_party, saved), persons, companies,
                     roles, contact_infos, relations, subjects, inputs, errors
  services/system/   health aggregation + worker heartbeats (backs /health)
  workers/           reaper.py (lifecycle guardian: job reaper + operation sweeper)
  cli/               deadletters.py (dead-letter list/requeue operator commands)
  core/              auth/ (OAuth2, JWT, scopes), db/ (engine, models/<schema>/), logging/, config.py,
                     errors.py (HTTP-agnostic error taxonomy)
migrations/          Alembic (env.py creates schemas; baseline = explicit DDL)
tests/               api/, auth/, db/ (db/crm/: the CRM app against Postgres), workers/
                     (DB tests via @pytest.mark.db)
scripts/             coverage_summary.py, dump_openapi.py, version.py
```

DB schemas: `core`, `geo`, `ext`, `bot`, `auth`, `system` - see
[`docs/DATABASE_SCHEMA.md`](docs/DATABASE_SCHEMA.md).

## Conventions

- **Write everything in English** - docs, comments, specs, configs. The codebase is not German.
- **Reference code by symbol, not line number** - in Markdown docs, link to the file and name the
  function, class, or constant (e.g. `OPERATION_TTL` in `transitions.py`), never a bare
  `file.py:<line>` anchor. Line anchors rot on the next edit.
- **`openapi.json` is generated** - never edit it by hand. After API changes, run `just openapi`
  and commit ([ADR 0001](docs/decisions/0001-openapi-as-contract.md)).
- **Migrations** use the direct DB URL (`DB__MIGRATION_URL`), the app uses the pooled one
  ([ADR 0002](docs/decisions/0002-pooled-vs-migration-url.md)). Drop enum types explicitly on
  downgrade; schemas are created in `migrations/env.py`.
- **Endpoints follow the API conventions** ([spec](docs/specs/api-conventions.md),
  [ADR 0006](docs/decisions/0006-error-envelope.md)): guard with `require_scopes(...)` on the
  decorator; services raise taxonomy errors (`app/core/errors.py`) and endpoints neither catch them
  nor raise `HTTPException` - declare them with `responses=error_responses(...)`; lists take a
  `PageParams` subclass and return `Page[T]`; new schemas derive from `ApiModel`. Never hand-write
  401/403 docs or an `operation_id`.
- **The CRM is the system of record** ([spec](docs/specs/crm-api.md),
  [ADR 0007](docs/decisions/0007-crm-system-of-record.md)): nothing under `app/services/crm` or
  `app/api/v1/crm` imports the bot domain, and no CRM write looks at Discord state. Every write
  service ends with `saved(...)` and `load_party(...)`; a `from_model` mapper touches only what
  `PARTY_GRAPH` loads - extend the graph, never add an ad-hoc load. The error catalog is closed.
- **Discord state changes** run in two phases (`prepare`/`commit`) - Forge never touches the
  Discord API itself ([ADR 0003](docs/decisions/0003-two-phase-transitions.md)).
- **Jobs** are at-least-once; handlers must be idempotent
  ([ADR 0004](docs/decisions/0004-forge-first-job-queue.md)).
- **Spec-first** for larger arcs: first a document in [`docs/specs/`](docs/specs/)
  (problem/goals/non-goals/decision table), then implement. Reference:
  [`lifecycle-guardian.md`](docs/specs/lifecycle-guardian.md).
- **Decisions** with lasting impact go into [`docs/decisions/`](docs/decisions/) as an ADR.
- **Merge via `git ship`** (local fast-forward merge) to keep Leon's signature on `main` - not
  the GitHub rebase/squash button (`main` has a signed-commits ruleset).
- Commit style: conventional with PR number, e.g. `feat(api): ... (#34)`.

## Orientation

Big picture in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); the *why* in
[`docs/decisions/`](docs/decisions/).
