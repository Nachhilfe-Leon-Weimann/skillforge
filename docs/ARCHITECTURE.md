# Architecture

This file describes the **living structure** of SkillForge - what exists *now* and how it fits
together. What SkillForge is *for*, its borders and principles, is in the [project sketch](PROJECT.md);
the *why* behind larger decisions lives in [`decisions/`](decisions/) (ADRs), forward-looking
design in [`specs/`](specs/).

## What is SkillForge?

The central service of the skill-platform. Today it is the **source of truth** for the desired
state and orchestrates SkillBot (the Discord bot) through three channels:

- a **REST API** (`/api/v1`, OAuth2-protected),
- a **job queue** that SkillBot pulls work from,
- **two-phase operations** (`prepare`/`commit`) for Discord state changes.

Forge **never touches the Discord API itself** - only SkillBot does.

## Layers

```
HTTP -> app/api/system    liveness + health probes (dependencies, workers)
        app/api/v1        endpoints, request/response schemas, scope checks
        app/services/bot  business logic (transitions, jobs, permissions, views)
        app/services/crm  system of record: parties, roles, contact infos, relations, subjects
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
- **`app/api/v1/`** - `router.py` with prefix `/api/v1` aggregates three areas, which share the
  vocabulary in `common/` (see [API conventions](#api-conventions)):
  - `auth/` - `token.py` (OAuth2 token endpoint, three grants), `revoke.py` (logout), `password.py`
    (redeem a one-time token), `users.py` (accounts), `clients.py` (client management), `me.py`.
  - `bot/` - `runtime.py` (read: principals, contexts, command envs), `operations.py` (operation
    reads: by id + filtered list), `jobs.py` (queue reads - by id, filtered list, queue summary -
    plus claim/complete/fail), `students.py` & `tutors.py` (state transitions), `command_envs.py`,
    `users.py` (provisioning: register users, link/deactivate accounts, group membership),
    `authz.py` (delegated authorization check). `dependencies.py` wires the scope gates. Endpoints
    do not catch domain errors: the handlers in `app/api/v1/common/errors.py` map them to HTTP.
  - `crm/` - one module per resource: `parties.py` (list, detail, guarded delete), `persons.py` &
    `companies.py` (typed create and update), `roles.py`, `contact_infos.py`, `relations.py`,
    `subjects.py`. `params.py` holds the path and query vocabulary, `schemas.py` the read and write
    models with their `from_model` mappers (see [CRM](#crm)).
- **`app/services/bot/`** - the actual logic, free of HTTP concerns: `transitions.py`,
  `operations.py` (operation reads), `jobs.py`, `principals.py`, `provisioning.py`, `authz.py`,
  `command_envs.py`, `contexts.py`, `profile.py`, `reaper.py`, `views.py` (immutable view models for
  responses), `errors.py` (service error hierarchy).
- **`app/services/crm/`** - the CRM services, same shape as the bot's (function modules, `session`
  first, no commits, no `app.api` imports): `parties.py` (`PARTY_GRAPH`, `load_party`, `saved`,
  list, delete), `persons.py`, `companies.py`, `roles.py`, `contact_infos.py`, `relations.py`,
  `subjects.py`, `inputs.py` (enums, input dataclasses and `normalize_contact_value`, shared with the
  API), `errors.py` (the error catalog). Never imports the bot domain.
- **`app/services/system/`** - health aggregation (`health_service.py`) and worker liveness
  (`heartbeat_service.py`), backing the `/health` tree.
- **`app/core/`** - `auth/` (OAuth2, JWT, scopes, roles, reach, accounts and sessions, bootstrap),
  `db/` (async engine, sessions, models), `logging/` (structured logging via `skillcore`), `errors.py`
  (HTTP-agnostic error taxonomy), `config.py` (settings).

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

## CRM

The CRM is the **system of record** for who exists and how people relate
([ADR 0007](decisions/0007-crm-system-of-record.md)): `core` holds the intended state, the `bot`
schema mirrors what is true in Discord. The dependency is one-way - `app/services/bot` may import
`app/services/crm`, never the reverse - and a CRM write is validated against CRM rules only, never
against Discord state. The full design is in the [CRM API spec](specs/crm-api.md).

- **Route form.** A polymorphic read side (`GET /parties`, `GET /parties/{party_id}`, whose
  `PartyDetail` is a discriminated union of `PersonDetail` and `CompanyDetail`) and a typed write
  side (`/persons`, `/companies`) over one ID space. Roles are idempotent singletons
  (`PUT` / `DELETE /persons/{party_id}/student|tutor`), contact infos owned children with their own
  ID, relations associations addressed by their natural key
  (`/parties/{party_id}/relations/{type}/{to_party_id}`). Every route has exactly one target party.
- **Aggregate root.** `Party` is the root: every write inside the aggregate ends with `saved(...)`,
  which moves `party.updated_at` - the one change signal consumers get - and a relation touches
  both parties. A request that changes nothing (an empty `PATCH`, a repeated `PUT`) is not a write.
- **One loading path.** Async SQLAlchemy cannot lazy-load, so `PARTY_GRAPH` in `parties.py` names
  everything a representation may touch, `load_party` applies it with `populate_existing`, and every
  write returns through it. A `from_model` mapper touches only what `PARTY_GRAPH` loads.
- **The bot as a consumer.** The bot reads the CRM in two places. Its operational profile loads the
  party through `PARTY_GRAPH` plus the relationships only the profile touches
  (`load_parties_for_discord_ids` in `profile.py`). And `prepare_student_activation` checks the pair
  it is given against the intended state (`_require_tutor_of` in `transitions.py`): both users must
  be linked to a party through an active Discord account, and the tutor's party must be `tutor_of`
  the student's. The commit does not check again - a relation that moved on is divergence to
  reconcile, not a failed commit.
- **Uniqueness by constraint.** Subject titles (`uq_subject_title_lower`) and contact infos
  (`uq_contact_info`) are decided by the database: the change is made and flushed *inside* a
  SAVEPOINT and an `IntegrityError` becomes the domain error (`_unique_title`,
  `_unique_per_party`). `begin_nested()` flushes pending state first, so a change made before it
  would fail in the enclosing transaction.
- **Errors.** A missing thing named in the path is a 404, one referenced in the body a 422; what
  needs no database is checked by the request model. The catalog is closed at 15 classes in
  `app/services/crm/errors.py`, each pinned per route by `tests/api/test_crm_error_contract.py`.
- **Personal data stays out of logs.** Validation and domain messages never repeat a name or a
  contact value, text Postgres cannot store is rejected by the request models
  (`require_storable_text`), and the engine runs with `hide_parameters=True`.
- **Deleting a party** is refused (`party_in_use`) while a Discord account or an `ext` link exists:
  all foreign keys into `core.party` cascade, so the guard - taken under a row lock - is what keeps
  external systems from being orphaned. The CRM reads the `ext` tables there and nowhere else.
- **Commit before the response.** The CRM routes use `DBSession`, whose `scope="function"` ends the
  request transaction before the response is sent: a failing commit is a 500, not a 201.

## Database

One Postgres DB, six schemas by domain - details in
[`DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md):

| Schema | Contents |
|---|---|
| `core` | Central business domain: party/person/company, students, tutors, subjects |
| `geo`  | Geographic reference data (PLZ/Ort) |
| `ext`  | Links from external system ids (Discord, sevDesk, Clockodo, Microsoft) to a `core.party` |
| `bot`  | SkillBot operational state: Discord topology, workspaces, permissions, job queue, operations |
| `auth` | OAuth2 clients, secrets, scope grants, user accounts, roles, sessions, one-time tokens, audit |
| `system` | Runtime/operational state: background-worker liveness heartbeats |

- **Async SQLAlchemy 2** over `asyncpg`; models under `app/core/db/models/<schema>/`, one
  `*Base` class per schema with `{"schema": ...}`.
- **Migrations** via Alembic (`migrations/`). The app uses the pooled connection, migrations the
  direct one - see [ADR 0002](decisions/0002-pooled-vs-migration-url.md). Schema/baseline
  convention: [ADR 0005](decisions/0005-multi-schema-db.md).

## Auth

SkillForge is its own identity provider ([ADR 0008](decisions/0008-user-authentication-and-reach.md),
[spec](specs/user-authentication.md)); everything lives in `app/core/auth/` and the `auth` schema.

**Who logs in.** An application client authenticates at `POST /api/v1/auth/token` with `client_id` and an
Argon2-hashed secret (HTTP Basic or form) - for **every** grant:

| Grant | For | Result |
|---|---|---|
| `client_credentials` | the client itself | access token from its `application` grants |
| `password` | a person, through a login client | access token + refresh token; opens a `user_session` |
| `refresh_token` | the same person, same client | new access token; the refresh token rotates |

`password` and `refresh_token` need `auth:users:login` granted in `application` mode. Their services
(`issue_user_token`, `refresh_user_token` in `services/tokens.py`) *return* a `TokenDenial` instead of
raising, so the failed-login counter, a revoked session and the audit entry commit; `create_token` turns
it into the OAuth2 error. `POST /auth/revoke` logs out.

**One token model.** An access token is a stateless JWT (15 minutes) for an `ApplicationPrincipal` or a
`UserPrincipal` (`principal.py`); claims are declared once in `tokens.py`. A person's token names the
client (`azp`), the party, the session (`sid`), the roles (informational) and `amr: ["pwd"]`.

**Scopes.** Routes declare scopes; scopes are computed once, at issuance
(`resolve_token_scopes` in `services/scopes.py`). A client grant has a mode: `application` (what the
client may do for itself) or `delegated` (the most it may do for a person). A person's token gets
`delegated grants ∩ role scopes` (and, on refresh, `∩ the session's scope`).

| Scope | Purpose | Mode |
|---|---|---|
| `bot:read` / `bot:write` | Read / write the bot API | either |
| `crm:read` | Read every party, relation and subject | either |
| `crm:read:own` | Read the parties within the caller's reach | either (only a person has a reach) |
| `crm:write` | Create, change and delete CRM records | either |
| `auth:clients:manage` | Manage application clients | either |
| `auth:users:manage` | Create, disable and reset accounts; assign stored roles | either |
| `auth:users:login` | Log people in on their behalf; redeem, revoke | `application` only (`CLIENT_ONLY_SCOPES`) |
| `account:self` | Manage one's own account (routes from P1-2) | either |

**Roles** (`roles.py`) turn into scopes at issuance and are never checked by a route: every account
holds `BASE_USER_SCOPES` (`account:self`, `crm:read:own`); `student`, `tutor` and `guardian` are derived
from the CRM and add nothing yet; `admin` is stored and adds `ROLE_SCOPES[admin]`.

**Reach.** `x:own` restricts `x` to reachable records; the unqualified scope implies it (`expand`,
`canonical` in `scopes.py`). `require_scopes(...)` demands the unqualified form; a reach-aware route takes
an `Access` from `require_access(...)` (`dependencies.py`) - the own party plus the `PARENT_OF` /
`PAYS_FOR` children (`reach.py`). Today `GET /crm/parties` and `GET /crm/parties/{party_id}`.

**Accounts and sessions.** One `user_account` per person party, created by `auth:users:manage`; an
invitation or reset token (`sf_ua_`) sets the password. A login opens a `user_session`: its opaque
refresh token (`sf_rt_`, stored as SHA-256) rotates on every refresh, lives 30 days absolute, and a
rotated-out token ends the session unless it arrives within `REFRESH_REUSE_GRACE` (10 s,
`services/sessions.py`). Wrong passwords lock the login per account (from the 5th, 1 minute doubling to
15). Disabling an account or a reset revokes its sessions; access tokens die within 15 minutes.

**Never in a log or audit row:** a password, a refresh or action token, an e-mail address.
`just bootstrap-skillbot`, `just bootstrap-client` and `just bootstrap-admin` seed the first clients and
the first admin.

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
  `422`) and return `Page[Item]` - never a bare array.
- **Schemas** derive from `ApiModel`: a docstring under a field becomes its OpenAPI description.

## Release and deploy

One flow for the whole platform ([spec](specs/release-flow.md)): release-please maintains a release PR;
shipping it creates the tag and GitHub release, and the `Release` workflow builds the image, publishes
the Python client and calls `deploy.yml`, which hands over to the platform's shared deploy workflow
([`skill-platform-workflows`](https://github.com/Nachhilfe-Leon-Weimann/skill-platform-workflows), the same
for every repo). Its script triggers `compose.deploy` over the Dokploy API, waits for the deployment to
finish and then requires `GET /health` to answer `ok` with the released `version` - a failed migration or
a stale container is a red workflow. Dokploy runs the repo's
[`compose.yml`](../compose.yml), whose `image:` tags the release commit pins to `vX.Y.Z`: `main` records
what prod runs. There is no automatic rollback - an app rollback would not roll back an Alembic migration;
the manual procedure is in the [README](../README.md#rolling-back).

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
| How do people get into the system? | [`specs/crm-api.md`](specs/crm-api.md), [CRM](#crm) |
