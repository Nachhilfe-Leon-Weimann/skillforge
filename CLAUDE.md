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
  main.py            FastAPI entry point (/, /health)
  api/v1/            endpoints: auth/ (token, clients), bot/ (runtime, jobs, students, tutors,
                     command_envs, users, authz)
  services/bot/      business logic: transitions, jobs, principals, provisioning, authz,
                     command_envs, contexts, profile, reaper, views, errors
  workers/           reaper.py (lifecycle guardian: job reaper + operation sweeper)
  cli/               deadletters.py (dead-letter list/requeue operator commands)
  core/              auth/ (OAuth2, JWT, scopes), db/ (engine, models/<schema>/), logging/, config.py
migrations/          Alembic (env.py creates schemas; baseline = explicit DDL)
tests/               api/, auth/, db/  (DB tests via @pytest.mark.db)
scripts/             dump_openapi.py, version.py
```

DB schemas: `core`, `geo`, `ext`, `bot`, `auth` - see
[`docs/DATABASE_SCHEMA.md`](docs/DATABASE_SCHEMA.md).

## Conventions

- **Write everything in English** - docs, comments, specs, configs. The codebase is not German.
- **`openapi.json` is generated** - never edit it by hand. After API changes, run `just openapi`
  and commit ([ADR 0001](docs/decisions/0001-openapi-as-contract.md)).
- **Migrations** use the direct DB URL (`DB__MIGRATION_URL`), the app uses the pooled one
  ([ADR 0002](docs/decisions/0002-pooled-vs-migration-url.md)). Drop enum types explicitly on
  downgrade; schemas are created in `migrations/env.py`.
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
