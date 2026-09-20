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
  api/v1/            endpoints: auth/ (token, clients, me), bot/ (runtime, jobs, operations,
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
.github/             workflows: ci.yml, build.yml, release.yml (release-please -> build -> publish -> deploy),
                     deploy.yml, triage.yml (issues/PRs -> org project + Module + author as assignee;
                     closed issue/PR -> current iteration; issue closed without a type -> comment;
                     module = repo variable PROJECT_MODULE);
                     scripts/deploy-dokploy.sh (the only code that talks to Dokploy)
scripts/             coverage_summary.py, dump_openapi.py
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
- **History on `main`: one PR per slice** ([spec](docs/specs/release-flow.md), decision D). A slice is one
  requirement of a spec (`P0-3`) - not the whole spec, not a single fixup: an arc lands as ~6-8 commits, not
  ~40 and not 1-2, so `git bisect` and the story both survive. Every commit on `main` is green on its own and
  conventional. Fold fixups and `docs(specs): tick` commits into the slice they belong to: tick a spec's
  checkboxes in the PR that fulfils them, never in a follow-up. A squash merge does the folding by itself;
  before a `git ship`, fold by hand.
- **Releases are release-please PRs** ([spec](docs/specs/release-flow.md)): never bump the version, edit
  `CHANGELOG.md` or create a tag by hand - ship the `chore(main): release X.Y.Z` PR. Commit messages on
  `main` feed the changelog and the version bump, so conventional types matter (`feat`, `fix`, `!`). Nothing
  deploys on a plain push to `main`; only prod exists.
- **Merge with `gh pr merge <n> --squash --delete-branch --auto`** (short: `-sd --auto`). It waits for the
  required `check`, then squash-merges: one GitHub-signed commit per PR, subject = PR title + PR number, so
  the PR title must be the conventional message (for a single-commit PR GitHub takes that commit's subject
  instead). `git ship` (local fast-forward, keeps Leon's signature) only works when the branch tip already
  carries a green `check` - `main` rejects an unpushed, still-running or cancelled tip. Never the rebase or
  merge-commit button (unsigned commits, non-linear history). Keep PRs independent: after a squash, a PR
  stacked on top conflicts with `main`.
- Commit style: conventional with PR number, e.g. `feat(api): ... (#34)`.

## Orientation

Big picture in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); the *why* in
[`docs/decisions/`](docs/decisions/).
