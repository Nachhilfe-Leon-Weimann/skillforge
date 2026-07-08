# Architecture

This file describes the **living structure** of SkillForge - what exists *now* and how it fits
together. The *why* behind larger decisions lives in [`decisions/`](decisions/) (ADRs),
forward-looking design in [`specs/`](specs/).

## What is SkillForge?

The backend of the skill-platform. It is the **source of truth** for the desired state and
orchestrates SkillBot (the Discord bot) through three channels:

- a **REST API** (`/api/v1`, OAuth2-protected),
- a **job queue** that SkillBot pulls work from,
- **two-phase operations** (`prepare`/`commit`) for Discord state changes.

Forge **never touches the Discord API itself** - only SkillBot does.

## Layers

```
HTTP -> app/api/v1        endpoints, request/response schemas, scope checks
        app/services/bot  business logic (transitions, jobs, permissions, views)
        app/core          cross-cutting: auth, db, logging, config
                          `-> Postgres (schemas: core/geo/ext/bot/auth)
```

- **`app/main.py`** - FastAPI entry point. Mounts the v1 router, registers request logging, and
  exposes `GET /` (welcome) and `GET /health` (checks DB connectivity, `503` on failure).
- **`app/api/v1/`** - `router.py` with prefix `/api/v1` aggregates two areas:
  - `auth/` - `token.py` (OAuth2 token endpoint), `clients.py` (client management).
  - `bot/` - `runtime.py` (read: principals, contexts, command envs), `jobs.py`
    (claim/complete/fail), `students.py` & `tutors.py` (state transitions), `command_envs.py`,
    `users.py` (provisioning: register users, link/deactivate accounts, group membership),
    `authz.py` (delegated authorization check). `_transitions.py` maps service errors to HTTP
    codes, `dependencies.py` wires the scope gates.
- **`app/services/bot/`** - the actual logic, free of HTTP concerns: `transitions.py`,
  `jobs.py`, `principals.py`, `provisioning.py`, `authz.py`, `command_envs.py`, `contexts.py`,
  `profile.py`, `reaper.py`, `views.py` (immutable view models for responses), `errors.py`
  (service error hierarchy).
- **`app/core/`** - `auth/` (OAuth2, JWT, scopes, bootstrap), `db/` (async engine, sessions,
  models), `logging/` (structured logging via `skillcore`), `config.py` (settings).

## Two core concepts

### Two-phase transitions (`prepare` -> `commit`)

Discord state changes go through `bot.operation` and are deliberately decoupled: Forge plans and
reserves, SkillBot executes, Forge confirms.

- **`prepare`** ([`transitions.py:50`](../app/services/bot/transitions.py)) validates, locks the
  affected rows with `FOR UPDATE` ([`transitions.py:357`](../app/services/bot/transitions.py)),
  reserves capacity, and writes a `PREPARED` operation with a `plan` and `expires_at`
  (TTL **10 min**, `OPERATION_TTL`, [`transitions.py:41`](../app/services/bot/transitions.py)).
  Forge hands SkillBot an executable plan in return.
- **`commit`** persists the Discord results confirmed by the bot and marks the operation
  `COMMITTED`.
- Capacity counts committed workspaces **plus** outstanding `PREPARED` reservations, so parallel
  prepares cannot overbook.

Operations: `TUTOR_ACTIVATE`, `STUDENT_ACTIVATE`, `STUDENT_STASH`, `STUDENT_POP`,
`STUDENT_DEACTIVATE`, `TUTOR_DEACTIVATE`. The two off-boarding kinds tear a workspace down
(hard-deleting the workspace + channel rows and flipping `DiscordUser.active` off), the exact
inverse of activation; a tutor teardown refuses while any student still hangs under it. See
[off-boarding transitions](specs/off-boarding-transitions.md).
Rationale & trade-offs: [ADR 0003](decisions/0003-two-phase-transitions.md).

### Forge-first job queue

Forge `enqueue`s work, SkillBot polls it.

- Claiming is atomic via **`SELECT ... FOR UPDATE SKIP LOCKED`**
  ([`jobs.py`](../app/services/bot/jobs.py)) - concurrent workers never collide.
- Lifecycle `PENDING -> CLAIMED -> COMPLETED | FAILED`; failures requeue with backoff
  (`RETRY_BACKOFF = 60 s`, [`jobs.py`](../app/services/bot/jobs.py)) until `max_attempts`.
- **At-least-once**: a claimed job whose worker dies is reclaimed once its lease expires and
  delivered again, so handlers in the bot **must be idempotent**.

### Lifecycle guardian (self-healing)

A dedicated worker ([`app/workers/reaper.py`](../app/workers/reaper.py), its own `worker`
service in `compose.yml`) runs two passes every `REAPER_INTERVAL` (30 s), reusing the existing
transitions ([`reaper.py`](../app/services/bot/reaper.py)):

- **Job reaper** - reclaims `CLAIMED` jobs whose `claimed_at` is older than `JOB_LEASE` (5 min)
  via the regular `fail_job` retry path (`PENDING` with backoff, or `FAILED` once attempts are
  exhausted). The lease reclaim costs no extra attempt - the increment on `claim` carries it. It
  works in bounded batches (`REAP_BATCH_LIMIT`), draining the backlog across batches so one run
  never locks an unbounded number of rows in a single transaction.
- **Operation sweeper** - flips `PREPARED` operations past their `expires_at` to `EXPIRED`
  (the lazy commit path already did this on access; the sweep makes it active and bounded).

Every run logs one structured counter line (`jobs_reclaimed`, `jobs_dead_lettered`,
`operations_expired`, `duration_ms`).

Dead-lettered (`FAILED`) jobs have an operator path: `just dead-jobs` lists them and
`just requeue <job_id>` resets one to `PENDING` so it is claimable again
([`app/cli/deadletters.py`](../app/cli/deadletters.py)). Rationale & scope:
[ADR 0004](decisions/0004-forge-first-job-queue.md),
[lifecycle guardian spec](specs/lifecycle-guardian.md).

## Database

One Postgres DB, five schemas by domain - details in
[`DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md):

| Schema | Contents |
|---|---|
| `core` | Central business domain: party/person/company, students, tutors, subjects |
| `geo`  | Geographic reference data (PLZ/Ort) |
| `ext`  | Links from external system ids (Discord, sevDesk, Clockodo, Microsoft) to a `core.party` |
| `bot`  | SkillBot operational state: Discord topology, workspaces, permissions, job queue, operations |
| `auth` | OAuth2 clients, secrets, scopes, audit |

- **Async SQLAlchemy 2** over `asyncpg`; models under `app/core/db/models/<schema>/`, one
  `*Base` class per schema with `{"schema": ...}`.
- **Migrations** via Alembic (`migrations/`). The app uses the pooled connection, migrations the
  direct one - see [ADR 0002](decisions/0002-pooled-vs-migration-url.md). Schema/baseline
  convention: [ADR 0005](decisions/0005-multi-schema-db.md).

## Auth

OAuth2 **client credentials** (`app/core/auth/`): clients authenticate with `client_id` + an
Argon2-hashed secret at the token endpoint and receive a JWT. Endpoints are gated by **scopes**:

| Scope | Purpose |
|---|---|
| `bot:read` | Read bot API |
| `bot:write` | Write bot API |
| `auth:clients:manage` | Manage application clients |

`require_scopes()` (`app/core/auth/dependencies.py`) returns `403` on a missing scope.
`just bootstrap-skillbot` seeds the initial auth state.

## API contract

The committed **`openapi.json` is the contract**; consumers generate their clients from it.
`just openapi` regenerates it, `just openapi-check` (in CI) prevents drift. Never edit it by hand -
see [ADR 0001](decisions/0001-openapi-as-contract.md).

## Roadmap: capability arcs

The platform grows along four arcs that build on each other (details in the
[lifecycle guardian spec](specs/lifecycle-guardian.md)):

1. **Guardian** - self-healing for jobs & operations *(shipped)*.
2. **Ops plane** - read/observability layer (queue depth, funnel views, audit search) *(next)*.
3. **Eventing** - outbox + webhooks, idempotency keys on the service API.
4. **Integration sync** - generic job/worker pattern for Clockodo/sevDesk/Microsoft.

## Where do I find...?

| Question | Location |
|---|---|
| How is the DB structured? | [`DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md) |
| Why was X decided this way? | [`decisions/`](decisions/) |
| What is planned / design sketches? | [`specs/`](specs/) |
| Which commands exist? | [`../justfile`](../justfile), [`../CLAUDE.md`](../CLAUDE.md) |
| What does the API look like? | [`../openapi.json`](../openapi.json) |
