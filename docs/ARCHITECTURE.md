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
HTTP -> app/api/system    liveness + health probes (dependencies, workers)
        app/api/v1        endpoints, request/response schemas, scope checks
        app/services/bot  business logic (transitions, jobs, permissions, views)
        app/services/system  health aggregation + worker heartbeats
        app/core          cross-cutting: auth, db, logging, config
                          `-> Postgres (schemas: core/geo/ext/bot/auth/system)
```

- **`app/main.py`** - FastAPI entry point. Mounts the top-level `app.api` router (which aggregates
  the `system` and `v1` routers), registers request logging and the exception handlers, exposes
  `GET /` (welcome), and finishes with `customize_openapi(app)`.
- **`app/api/system/`** - `health.py`: `GET /health` (aggregate over all dependencies **and**
  workers; `200` healthy / `503` unhealthy / `500` on error), plus `/health/live`,
  `/health/dependencies[/{name}]`, and `/health/workers[/{name}]`.
- **`app/api/v1/`** - `router.py` with prefix `/api/v1` aggregates two areas, which share the
  vocabulary in `common/` (see [API conventions](#api-conventions)):
  - `auth/` - `token.py` (OAuth2 token endpoint), `clients.py` (client management).
  - `bot/` - `runtime.py` (read: principals, contexts, command envs), `operations.py` (operation
    reads: by id + filtered list), `jobs.py` (queue reads - by id, filtered list, queue summary -
    plus claim/complete/fail), `students.py` & `tutors.py` (state transitions), `command_envs.py`,
    `users.py` (provisioning: register users, link/deactivate accounts, group membership),
    `authz.py` (delegated authorization check). `dependencies.py` wires the scope gates. Endpoints
    do not catch domain errors: the handlers in `app/api/v1/common/errors.py` map them to HTTP.
- **`app/services/bot/`** - the actual logic, free of HTTP concerns: `transitions.py`,
  `operations.py` (operation reads), `jobs.py`, `principals.py`, `provisioning.py`, `authz.py`,
  `command_envs.py`, `contexts.py`, `profile.py`, `reaper.py`, `views.py` (immutable view models for
  responses), `errors.py` (service error hierarchy).
- **`app/services/system/`** - health aggregation (`health_service.py`) and worker liveness
  (`heartbeat_service.py`), backing the `/health` tree.
- **`app/core/`** - `auth/` (OAuth2, JWT, scopes, bootstrap), `db/` (async engine, sessions,
  models), `logging/` (structured logging via `skillcore`), `errors.py` (HTTP-agnostic error
  taxonomy), `config.py` (settings).

## Two core concepts

### Two-phase transitions (`prepare` -> `commit`)

Discord state changes go through `bot.operation` and are deliberately decoupled: Forge plans and
reserves, SkillBot executes, Forge confirms.

- **`prepare`** (the `prepare_*` functions in [`transitions.py`](../app/services/bot/transitions.py))
  validates, locks the affected rows with `FOR UPDATE` (`.with_for_update()`), reserves capacity,
  and writes a `PREPARED` operation with a `plan` and `expires_at` (TTL **10 min**, `OPERATION_TTL`).
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

One Postgres DB, six schemas by domain - details in
[`DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md):

| Schema | Contents |
|---|---|
| `core` | Central business domain: party/person/company, students, tutors, subjects |
| `geo`  | Geographic reference data (PLZ/Ort) |
| `ext`  | Links from external system ids (Discord, sevDesk, Clockodo, Microsoft) to a `core.party` |
| `bot`  | SkillBot operational state: Discord topology, workspaces, permissions, job queue, operations |
| `auth` | OAuth2 clients, secrets, scopes, audit |
| `system` | Runtime/operational state: background-worker liveness heartbeats |

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

`require_scopes()` (`app/core/auth/dependencies.py`) returns the `Security` marker that guards a
route: `401` without a valid token, `403` on a missing scope. Each scope carries its description
on the `Scope` enum. `just bootstrap-skillbot` seeds the initial auth state.

## API contract

The committed **`openapi.json` is the contract**; consumers generate their clients from it.
`just openapi` regenerates it, `just openapi-check` (in CI) prevents drift. Never edit it by hand -
see [ADR 0001](decisions/0001-openapi-as-contract.md).

## API conventions

All `/api/v1` domains share one vocabulary (`app/api/v1/common/`), so an endpoint states only what
is special about it and both runtime behavior and the OpenAPI docs derive from the same
declaration. The full rules, with the *why*, are in the
[API conventions spec](specs/api-conventions.md) and
[ADR 0006](decisions/0006-error-envelope.md).

- **Operation IDs** are `{tag}_{function_name}` (`operation_id` in `openapi.py`); every route needs
  exactly one domain tag, set on its domain router.
- **Scope guards** are declared, never implemented per route: `dependencies=[require_scopes(...)]`
  on the decorator, or a parameter typed `Annotated[Principal, require_scopes(...)]` when the
  principal is needed. `customize_openapi` derives the `401`/`403` docs from the declaration.
- **Errors**: every non-2xx body is `ErrorResponse{detail, code, errors?}`. Services raise
  subclasses of the taxonomy in `app/core/errors.py` (`NotFoundError`, `ConflictError`,
  `DomainValidationError`); `STATUS_BY_ERROR` in `errors.py` maps them, the global handlers render
  them, and `responses=error_responses(...)` documents them - endpoints contain no `try/except`
  for mapped errors. Errors the API layer owns itself (OAuth2 codes, a local status mapping) are
  `ApiError` declarations. An endpoint whose transaction must commit although the request failed
  *returns* the error (`ApiError.response()`) instead of raising it - the token endpoint does, to
  keep its `TOKEN_DENIED` audit entry.
- **Lists** take a `PageParams` subclass (`limit`, `offset`, filters; unknown parameters are a
  `422`) and return `Page[Item]` - never a bare array. One exception is left: `GET /auth/clients`
  still returns an array; changing it alters the response shape and is an open decision in the
  spec (P1-5).
- **Schemas** derive from `ApiModel`: a docstring under a field becomes its OpenAPI description.

## Roadmap: capability arcs

The platform grows along four arcs that build on each other (details in the
[lifecycle guardian spec](specs/lifecycle-guardian.md)):

1. **Guardian** - self-healing for jobs & operations *(shipped)*.
2. **Ops plane** - read/observability layer (queue depth, funnel views, audit search)
   *(first slice shipped: jobs & operations read plane; audit search next)*.
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
