# Spec: User authentication (accounts, sessions, reach-qualified scopes)

> Status: Draft (2026-09) | Domain arc `auth`
> Tracking: [#85](https://github.com/Nachhilfe-Leon-Weimann/skillforge/issues/85)
> Builds on [`api-conventions.md`](api-conventions.md), on goal 4 of [`crm-api.md`](crm-api.md) ("exactly one target
> party per route") and on [ADR 0008](../decisions/0008-user-authentication-and-reach.md) (Accepted).
> Written to be executed by coding agents: every requirement names its symbols, files and checkable criteria.

## Problem statement

Forge authenticates machines only. `POST /auth/token` knows one grant (`client_credentials`),
`validate_access_token` in [`tokens.py`](../../app/core/auth/tokens.py) rejects every `principal_type` other than
`application`, and a scope such as `crm:read` means _every record_.

The customer portal on skillsite will be the second consumer of the CRM API
([ADR 0007](../decisions/0007-crm-system-of-record.md)). Its callers are humans - students, their parents and
payers, tutors, admins - and two things are missing for them:

- **Nobody can log in.** A person exists as a `Party`; there is no account, no credential, no session.
- **Nobody can be restricted to their own data.** Handing a student `crm:read` would show them every party. The
  only record-level check in the codebase, `check_authorization` in
  [`authz.py`](../../app/services/bot/authz.py), is keyed on Discord users and belongs to the bot domain.

The first consumer of this arc is, again, **the operator in Swagger UI**: inviting the first accounts, logging in
through the "Authorize" dialog, watching a restricted token get a filtered answer. The portal follows in its own
arc in the skillsite repository.

## Goals

1. **Users log in with e-mail and password** and get a token the existing guards understand.
2. **One token model.** A user token is issued to a client on behalf of a user; its scopes are computed once, at
   issuance. A request is validated exactly once, the same way for clients and users.
3. **One permission system.** Endpoints keep declaring scopes. "Everything" versus "only mine" is a qualifier on
   the scope, not a second mechanism next to it.
4. **Safe by default.** A restricted user reaches only endpoints that were deliberately made reach-aware.
   Everything else answers `403` without anybody having to remember a filter.
5. **Accounts follow the CRM.** An account belongs to exactly one person party, is created by invitation, and its
   roles are derived from CRM data wherever the CRM knows them.
6. **The mechanism is proven end to end** on the two party read routes; every further endpoint is a later,
   independent opt-in.

## Non-goals

- **The portal itself** and its backend-for-frontend. This spec only records what Forge expects from it
  ([BFF contract](#bff-contract)).
- **Sending e-mail.** In P0 the operator receives the one-time token in the API response and passes it on by hand.
  Mail delivery and self-service "forgot password" are P1-1.
- **Self-signup.** No account without a party, no unverified e-mail addresses, no abuse surface.
- **Reach-aware writes** and `crm:write:own`. Restricted users are read-only until P1-4.
- **Tutor reach.** `TUTOR_OF` does not extend reach: a tutor must not see everything about a student, so it needs
  field-level projections and arrives with the lessons arc (P2).
- **Federated login** (Discord, Microsoft) and **MFA**. P2.
- **Asymmetric signing, JWKS, token introspection.** Forge is the only party that validates its tokens.
- **Instant revocation of access tokens.** They stay stateless; see decision K.
- **Any change to the grant engine in the `bot` schema.** It answers a different question (which Discord user may
  run which bot workflow).

## Decisions

| Topic                                    | Decision                                                                                                                                                                      | Rationale                                                                                                                                     |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| **A - Identity provider**                | Forge itself. Accounts, hashes and sessions live in the `auth` schema.                                                                                                        | No second service to run, no second user store to sync with CRM invitations (ADR 0008).                                                       |
| **B - Credential**                       | E-mail + password, Argon2 via the existing `PasswordHash.recommended()` in [`secrets.py`](../../app/core/auth/secrets.py).                                                    | `pwdlib[argon2]` is already a dependency and already hashes client secrets.                                                                   |
| **C - Account origin**                   | Invitation only. An account always has a `party_id`, and the party is a `PERSON`.                                                                                             | Mirrors "account linking requires an existing party, no auto-creation" in [`principals-and-provisioning.md`](principals-and-provisioning.md). |
| **D - Login identifier**                 | `user_account.email`: its own column, stored lowercased, unique. Not a reference to `ContactInfo`, no sync.                                                                   | CRM e-mail addresses are unique per party only - siblings share a parent's address.                                                           |
| **E - Token endpoint**                   | `POST /auth/token` gains `password` and `refresh_token`. Both require client authentication.                                                                                  | One issuing path, one response model, one audit path; `azp` is naturally the client the user came through.                                    |
| **F - Who may log users in**             | A client needs the scope grant `auth:users:login`.                                                                                                                            | Reuses `ApplicationClientScopeGrant`; the `password` grant is off unless switched on per client.                                              |
| **G - Token scopes**                     | The meet of requested scopes, client grants and the user's role scopes, computed at issuance.                                                                                 | "Client on behalf of user" without a second check at request time.                                                                            |
| **H - Reach qualifier**                  | `<scope>:own` restricts a scope to reachable records; the unqualified scope implies it. `require_scopes` demands the unqualified form, `require_access` accepts either.       | Safe by default: a forgotten filter is a `403`, never a leak.                                                                                 |
| **I - Reach derivation**                 | Own party plus the `to_party` of outgoing `PARENT_OF` / `PAYS_FOR` relations, resolved per request. Not a token claim.                                                        | Same set as the bot's delegation check; always current; one indexed query, paid by restricted tokens only.                                    |
| **J - Roles**                            | `student`, `tutor`, `guardian` are derived from the CRM and never stored; `admin` is a row in `auth.user_account_role`. Role-to-scope mapping lives in code. Roles are a set. | No second role source that can drift from the CRM; a row instead of a flag leaves room for further stored roles.                              |
| **K - Sessions**                         | Access token: stateless JWT, 15 minutes. Refresh token: opaque, rotating, stored as SHA-256, reuse detection. Roles and scopes are re-derived at every refresh.               | Disabling an account or removing a role takes effect within 15 minutes without a lookup per request.                                          |
| **L - Out of reach**                     | `404` with the resource's own not-found error, never `403`.                                                                                                                   | A restricted caller cannot probe which parties exist.                                                                                         |
| **M - Deleting a party**                 | `user_account.party_id` is `ON DELETE CASCADE`; `delete_party` and its `EXTERNAL_LINKS` guard are not touched, `Party` gets no relationship to the account.                   | The CRM stays unaware of auth (ADR 0007). The audit log keeps the principal as a string and survives.                                         |
| **N - Denials are returned, not raised** | Every denial that writes state (audit entry, failed-login counter, session revocation) is returned as a response.                                                             | Raising rolls the session back and would undo the counter - the pattern `create_token` already follows.                                       |
| **O - First admin**                      | `just bootstrap-admin` creates the account with the `admin` role and prints the invitation token.                                                                             | Inviting needs an admin; the first one cannot be invited. It also recovers from a lockout.                                                    |

**Open:** whether the `admin` role also carries `bot:write`. It starts without it (humans then cannot drive
two-phase operations past the bot); adding it later is one line in `ROLE_SCOPES`.

## Scopes and reach

### New scopes

Declared on `Scope` in [`scopes.py`](../../app/core/auth/scopes.py) as `(value, description)` like the existing ones.

| Scope               | Held by      | Meaning                                                                                                            |
| ------------------- | ------------ | ------------------------------------------------------------------------------------------------------------------ |
| `crm:read:own`      | users        | Read parties within the caller's reach.                                                                            |
| `account:self`      | every user   | Manage the caller's own account. Also guarantees a user token never has an empty scope.                            |
| `auth:users:manage` | admins       | Invite, disable and reset user accounts; assign stored roles.                                                      |
| `auth:users:login`  | clients only | Log users in on their behalf (`password` / `refresh_token` grants, redeem, revoke). Never part of a user's scopes. |

### Implication

`OWN_VARIANT: dict[Scope, Scope]` in `scopes.py` maps an unqualified scope to its qualified form; in P0 it has one
entry, `CRM_READ -> CRM_READ_OWN`. Two pure functions build on it:

- `expand(scopes)` - the closure: every scope plus the `:own` variant of each unqualified one.
- `canonical(scopes)` - the inverse normal form: drops `x:own` wherever `x` is present.

A token always carries the canonical form. Every check expands first:
`get_current_principal` computes `missing = required - expand(principal.scopes)`.

### Token scope computation

```
available = expand(client_grants)                          # client_credentials
available = expand(client_grants) & expand(user_scopes)    # password, refresh_token

no scope requested  ->  token = canonical(available)
scope requested     ->  requested <= available, else invalid_scope;  token = canonical(requested)
empty result        ->  invalid_scope
```

**The client is the ceiling for every scope, `account:self` included.** A client that logs users in must be
granted each scope its users are supposed to end up with; a scope it lacks silently drops out of their tokens.
There is no exemption, so the rule has no special cases.

Requesting fewer scopes is always allowed and is how one account serves two views: an admin who is also a tutor
logs in once, and the portal obtains a token narrowed to `account:self crm:read:own` for the tutor view.
`resolve_token_scopes` in [`services/scopes.py`](../../app/core/auth/services/scopes.py) is reworked accordingly;
for `client_credentials` the observable behavior only widens (a client granted `crm:read` may now request
`crm:read:own`).

### Roles

`Role` (`StrEnum`) and the mapping live in a new `app/core/auth/roles.py`; the derivation that needs the database
lives in `app/core/auth/services/roles.py`.

| Role           | Source                                                       | Scopes                                                                          |
| -------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| _(every user)_ | -                                                            | `account:self`, `crm:read:own`                                                  |
| `student`      | the party's person has a `Student` row                       | -                                                                               |
| `tutor`        | the party's person has a `Tutor` row                         | -                                                                               |
| `guardian`     | the party has an outgoing `PARENT_OF` or `PAYS_FOR` relation | -                                                                               |
| `admin`        | row in `auth.user_account_role`                              | `crm:read`, `crm:write`, `auth:users:manage`, `auth:clients:manage`, `bot:read` |

The derived roles carry no scopes of their own yet. They exist so that `/auth/me` and the token's `roles` claim
tell the portal which views to offer, and so that the lessons arc can attach scopes to `tutor` without a schema
change. **Forge authorizes by scope only; it never branches on a role.**

### Access

`require_access(scope)` in [`dependencies.py`](../../app/core/auth/dependencies.py) is the reach-aware sibling of
`require_scopes`. It is used as a parameter, `Annotated[Access, require_access(Scope.CRM_READ)]`, and resolves:

| Token carries  | Principal                             | Result                                                  |
| -------------- | ------------------------------------- | ------------------------------------------------------- |
| `crm:read`     | any                                   | `Access.all()` - no query                               |
| `crm:read:own` | a `UserPrincipal`                     | `Access.of(reach)` - one query on `core.party_relation` |
| `crm:read:own` | an `ApplicationPrincipal`             | `403`                                                   |
| neither        | any                                   | `403`                                                   |

`Access` (frozen dataclass, `app/core/auth/reach.py`) exposes `party_ids: frozenset[UUID] | None` (`None` = all)
and `allows(party_id) -> bool`. It knows nothing about HTTP or about CRM errors. Calling `require_access` with a
scope that has no entry in `OWN_VARIANT` raises at import time.

`resolve_reach(session, party_id)` and the constant `DELEGATION_RELATION_TYPES` live in `reach.py` too.
[`authz.py`](../../app/services/bot/authz.py) imports the constant from there instead of defining it, so the bot's
delegation set and the API's reach cannot drift apart.

In the OpenAPI output a reach-aware operation lists **two alternative** security requirements,
`[{"OAuth2": ["crm:read"]}, {"OAuth2": ["crm:read:own"]}]`, and its derived `403` reads
"Missing required scope: crm:read or crm:read:own". `customize_openapi` in
[`openapi.py`](../../app/api/v1/common/openapi.py) derives both from the declaration, like it derives 401/403 today.

## Data model

Four new tables in the `auth` schema, models under `app/core/db/models/auth/`. One autogenerated Alembic
revision; the three new enum types are dropped explicitly on downgrade. `core` and `bot` do not change.

**`auth.user_account`** (`UserAccount`, `TimestampMixin`)

| Column               | Type                         | Notes                                                              |
| -------------------- | ---------------------------- | ------------------------------------------------------------------ |
| `id`                 | `uuid` PK                    | The token's `principal_id`; `sub` is `user:<id>`.                  |
| `party_id`           | `uuid` FK -> `core.party.id` | `NOT NULL`, `UNIQUE`, `ON DELETE CASCADE` (decision M).            |
| `email`              | `text`                       | `NOT NULL`, `UNIQUE`, `CHECK (email = lower(email))` (decision D). |
| `password_hash`      | `text`                       | `NULL` until the invitation is redeemed.                           |
| `status`             | enum `user_account_status`   | `invited` / `active` / `disabled`; server default `invited`.       |
| `failed_login_count` | `int`                        | `NOT NULL`, default `0`.                                           |
| `locked_until`       | `timestamptz`                | `NULL` = not locked.                                               |
| `last_login_at`      | `timestamptz`                | `NULL` until the first login.                                      |

**`auth.user_account_role`** (`UserAccountRole`, `CreatedAtMixin`) - primary key `(user_account_id, role)`;
`user_account_id` FK `ON DELETE CASCADE`; `role` enum `user_account_role_name` with the single value `admin`.

**`auth.user_session`** (`UserSession`, `CreatedAtMixin`) - one row per login.

| Column                        | Type                                      | Notes                                                                      |
| ----------------------------- | ----------------------------------------- | -------------------------------------------------------------------------- |
| `id`                          | `uuid` PK                                 | The token's `sid`.                                                         |
| `user_account_id`             | `uuid` FK                                 | `ON DELETE CASCADE`, indexed.                                              |
| `application_client_id`       | `uuid` FK -> `auth.application_client.id` | `ON DELETE CASCADE`. Only this client may refresh or revoke the session.   |
| `scope`                       | `text`                                    | Canonical scopes granted at login - the ceiling for every later refresh.   |
| `refresh_token_hash`          | `text`                                    | `NOT NULL`, `UNIQUE`. SHA-256 hex of the current refresh token.            |
| `previous_refresh_token_hash` | `text`                                    | `UNIQUE`, `NULL` before the first rotation.                                |
| `rotated_at`                  | `timestamptz`                             | When the current token replaced the previous one.                          |
| `expires_at`                  | `timestamptz`                             | Absolute; rotation does not extend it.                                     |
| `last_used_at`                | `timestamptz`                             |                                                                            |
| `revoked_at`                  | `timestamptz`                             | `NULL` = live.                                                             |
| `revoked_reason`              | `text`                                    | `logout`, `password_reset`, `account_disabled`, `admin`, `reuse_detected`. |

Refresh tokens are 32 random bytes (`secrets.token_urlsafe(32)`, prefix `sf_rt_`). They are hashed with SHA-256,
not Argon2: the token has full entropy, and the lookup needs a deterministic hash.

**`auth.user_action_token`** (`UserActionToken`, `CreatedAtMixin`) - one-time tokens for invitation and reset.

| Column            | Type                             | Notes                                                         |
| ----------------- | -------------------------------- | ------------------------------------------------------------- |
| `id`              | `uuid` PK                        |                                                               |
| `user_account_id` | `uuid` FK                        | `ON DELETE CASCADE`, indexed.                                 |
| `purpose`         | enum `user_action_token_purpose` | `invitation` / `password_reset`.                              |
| `token_hash`      | `text`                           | `NOT NULL`, `UNIQUE`. SHA-256 hex; plaintext prefix `sf_ua_`. |
| `expires_at`      | `timestamptz`                    |                                                               |
| `used_at`         | `timestamptz`                    | `NULL` = unused.                                              |
| `invalidated_at`  | `timestamptz`                    | Set when a newer token of the same purpose replaces this one. |
| `issued_by`       | `text`                           | `<principal_type>:<principal_id>` of the issuer, or `cli`.    |

**`AuthAuditLog`** is reused unchanged. `AuditEventType` in [`audit.py`](../../app/core/auth/audit.py) gains
`user_account.invited`, `user_account.activated`, `user_account.updated`, `user_account.disabled`,
`user_account.enabled`, `user_role.added`, `user_role.removed`, `invitation.issued`, `password_reset.issued`,
`password.set`, `session.revoked`, `session.reuse_detected`. `token.issued` / `token.denied` are written with
`principal_type="user"` for user grants. **An audit `detail` never contains an e-mail address the caller typed for
an account that does not exist.**

**Settings** (`AuthSettings` in [`config.py`](../../app/core/auth/config.py)): `refresh_token_expire_days = 30`,
`invitation_expire_hours = 168`, `password_reset_expire_hours = 24` (one hour once P1-1 sends mail),
`login_lockout_threshold = 5`, `login_lockout_max_minutes = 15`.

## Token endpoint

`POST /api/v1/auth/token`, form-encoded, OAuth2 error codes (RFC 6749, section 5.2). Client authentication (HTTP
Basic or `client_id` / `client_secret` in the form) is required for **every** grant.

| `grant_type`         | Additional form fields                        | Result                                                      |
| -------------------- | --------------------------------------------- | ----------------------------------------------------------- |
| `client_credentials` | `scope?`                                      | Unchanged.                                                  |
| `password`           | `username` (the e-mail), `password`, `scope?` | Creates a `user_session`; response carries `refresh_token`. |
| `refresh_token`      | `refresh_token`, `scope?`                     | Rotates the refresh token; response carries the new one.    |

`AccessTokenResponse` gains two optional fields, `refresh_token` and `refresh_expires_in` (seconds until the
session's `expires_at`); both are `null` for `client_credentials`.

**`password`, in this order:**

1. Authenticate the client -> `invalid_client`.
2. The client holds `auth:users:login` -> else `unauthorized_client`.
3. Normalize `username` (strip, lowercase) and look the account up. If it does not exist, is not `active`, or is
   locked (`locked_until > now`): verify the submitted password against a module-level dummy hash, then answer
   `invalid_grant`. A locked account's counter does not move.
4. Verify the password with `verify_and_update`. On failure: increment `failed_login_count`; from
   `login_lockout_threshold` on, set `locked_until = now + min(max, 1 min * 2^(count - threshold))`; answer
   `invalid_grant`. On success: reset counter and lock, set `last_login_at`, store an upgraded hash if `pwdlib`
   returned one.
5. Derive roles, compute the scopes (see above) -> `invalid_scope`.
6. Create the session, issue both tokens, write `token.issued`.

`invalid_grant` has **one** body for every case in steps 3 and 4.

**`refresh_token`:** authenticate the client and check `auth:users:login` as above, then look the hash up.

- It matches `refresh_token_hash` of a live, unexpired session of **this** client whose account is `active`:
  re-derive roles, compute scopes with `expand(parse_scopes(session.scope))` as an additional ceiling, rotate
  (`previous <- current`, `rotated_at = now`), answer with both tokens.
- The account is no longer `active`: revoke the session (`account_disabled`), answer `invalid_grant`.
- It matches `previous_refresh_token_hash`: within 10 seconds of `rotated_at` answer `invalid_grant` and leave the
  session alone (two requests raced); after that revoke the session (`reuse_detected`), write
  `session.reuse_detected`, answer `invalid_grant`.
- Anything else: `invalid_grant`.

**Claims of a user token** (the application token keeps its shape):

```
iss, aud, iat, exp, jti      as today
sub             "user:<user_account.id>"
principal_type  "user"
principal_id    "<user_account.id>"
azp             "<client_id of the client that logged the user in>"
scope           canonical, space-separated
party_id        "<core.party.id>"
sid             "<user_session.id>"
roles           ["admin", "tutor"]        sorted; informational
```

`Principal` in [`principal.py`](../../app/core/auth/principal.py) becomes the abstract base of two frozen
dataclasses: `ApplicationPrincipal` and `UserPrincipal`, which adds `party_id`, `session_id` and
`roles: frozenset[Role]`. `principal_type` (`PrincipalType`) is a class constant and `subject` is derived, so a
principal cannot contradict its own type. Code that needs a user narrows with `isinstance` or `match` - the type
then guarantees the `party_id`, where an optional field would have to be checked. A token is a signed principal:
`create_access_token(settings, principal)` in [`tokens.py`](../../app/core/auth/tokens.py) writes it,
`validate_access_token` reads it back, and both go through one pydantic declaration of the claims - one model per
principal type, told apart by `principal_type` as a discriminated union. A user token without `party_id` or `sid`,
with an unknown role, or whose `sub` is not the canonical `user:<principal_id>`, is invalid.

The security scheme in [`security.py`](../../app/core/auth/security.py) declares a `password` flow next to
`clientCredentials`, so Swagger UI's "Authorize" dialog logs a user in. The class `OAuth2ClientCredentialsBearer`
is renamed to `OAuth2Bearer` and gets an explicit `scheme_name="OAuth2"`. FastAPI derives the key in
`components.securitySchemes` from the class name otherwise, so today the contract carries
`OAuth2ClientCredentialsBearer` on all 66 secured operations - a name that would be wrong from here on. The rename
is a **one-time, contract-wide diff** in `openapi.json`, made deliberately in P0-4 while no consumer is live; with
the explicit name a later class rename can no longer leak into the contract.

## Route map

All under `/api/v1/auth`, tag `auth`. JSON routes follow the API conventions: `ApiModel` schemas, scopes on the
decorator, `error_responses(...)`, `Page[T]` for the list.

```
# Tokens - form-encoded, OAuth2 errors
POST   /token                              client auth               3 grants

# Users - admin surface                    auth:users:manage
POST   /users                              invite                    201  UserAccountDetail + invitation
GET    /users                              list, filter              200  Page[UserAccountListItem]
GET    /users/{user_id}                                              200  UserAccountDetail
PATCH  /users/{user_id}                    email, status             200  UserAccountDetail
PUT    /users/{user_id}/roles/{role}       idempotent                200  UserAccountDetail
DELETE /users/{user_id}/roles/{role}                                 204
POST   /users/{user_id}/invitation         re-issue                  201  ActionTokenResponse
POST   /users/{user_id}/password-reset                               201  ActionTokenResponse
DELETE /users/{user_id}/sessions           revoke all                204

# On behalf of a user - application principal with auth:users:login
POST   /password/redeem                    {token, new_password}     204
POST   /revoke                             {refresh_token}           204

# Caller
GET    /me                                 any token                 200  MeResponse
```

- **`POST /users`** takes `{party_id, email, roles?}`; `roles` may only name stored roles. The response embeds
  `invitation: {token, expires_at}` - the only time the plaintext exists outside the hash.
- **`GET /users`** takes a `PageParams` subclass with `status`, `party_id` and `email` (exact, normalized) filters,
  ordered by `email`.
- **`UserAccountDetail`** carries `id`, `party_id`, `email`, `status`, `roles` (stored **and** derived, sorted),
  `locked_until`, `last_login_at`, `created_at`, `updated_at`. No hash, no counter.
- **`PATCH /users/{user_id}`** uses the `MISSING`-based update model of the CRM. `status` accepts `active` and
  `disabled` only, and `active` only for an account that has a password. Disabling revokes all sessions.
- **`POST /users/{user_id}/invitation`** is refused for an account that already has a password;
  **`POST /users/{user_id}/password-reset`** is refused for one that has none. Issuing invalidates earlier unused
  tokens of the same purpose. A reset does not revoke anything until it is redeemed.
- **`POST /password/redeem`** serves both purposes; the purpose comes from the token row. It sets the hash,
  activates an `invited` account, clears counter and lock, marks the token used and - for `password_reset` -
  revokes all sessions. A `disabled` account stays disabled.
- **`POST /revoke`** revokes the session the refresh token belongs to if this client opened it. It answers `204`
  whether or not it found one (RFC 7009 semantics; `204` instead of `200` because there is no body).
- **`GET /me`**: `MeResponse` gains `user_id`, `party_id` and `roles`; all three are `null` / empty for an
  application principal. The handler still answers from the token alone.

## Error catalog

Taxonomy errors (`app/core/auth/services/errors.py`, mapped by `STATUS_BY_ERROR`):

| Class                           | Status | `code`                        | Raised when                                                                   |
| ------------------------------- | ------ | ----------------------------- | ----------------------------------------------------------------------------- |
| `UserAccountNotFoundError`      | 404    | `user_account_not_found`      | Unknown `user_id`.                                                            |
| `UserAccountAlreadyExistsError` | 409    | `user_account_already_exists` | The party already has an account.                                             |
| `UserEmailAlreadyInUseError`    | 409    | `user_email_already_in_use`   | Another account uses the e-mail (invite, patch).                              |
| `UserAccountStateError`         | 409    | `user_account_state`          | Invitation for an account with a password, reset or `active` for one without. |
| `AccountPartyNotFoundError`     | 404    | `account_party_not_found`     | `party_id` of an invitation does not exist.                                   |
| `AccountPartyNotAPersonError`   | 422    | `account_party_not_a_person`  | The party is a company.                                                       |
| `UserRoleNotFoundError`         | 404    | `user_role_not_found`         | Removing a stored role the account does not hold.                             |
| `InvalidActionTokenError`       | 422    | `invalid_action_token`        | Unknown, used, invalidated or expired token - one error for all four.         |
| `WeakPasswordError`             | 422    | `weak_password`               | Password shorter than 12 or longer than 128 characters.                       |

`role` in the path and in `roles` is validated by the enum; an unknown or derived role is a request validation
error.

OAuth2 errors (`ApiError` constants in [`errors.py`](../../app/api/v1/auth/errors.py)), new:
`INVALID_GRANT` (400, `invalid_grant`, "Invalid credentials or refresh token") and `UNAUTHORIZED_CLIENT` (400,
`unauthorized_client`, "Client may not use this grant"). `INVALID_REQUEST` is reworded to cover any missing grant
parameter.

The catalog is closed: a requirement that seems to need another class or `code` is a reason to stop and report.

## Security rules

- **Passwords:** 12 to 128 characters, no composition rules. Checked in the service (`WeakPasswordError`), not in
  the schema, so the rule has one home.
- **No enumeration:** `invalid_grant` and `invalid_action_token` have one body each. The dummy-hash verification
  keeps the timing of "unknown account" close to "wrong password".
- **Lockout is per account.** Forge sees only the portal's IP address; limiting by client IP is the job of the
  portal and the reverse proxy.
- **Plaintext exists once.** Refresh tokens and action tokens are returned in the response that creates them and
  stored as hashes only. They never appear in logs, audit details or error messages.
- **Denials are returned** (decision N). `create_token` stays the single place that turns the service's denial
  into an `ApiError` response, for all three grants.
- **`auth:users:login` never reaches a user token**: it is not in any role's scopes, and
  `test_no_role_carries_client_only_scopes` pins that.
- **Access tokens are not revocable.** Every revocation acts on the session; the access token dies within 15
  minutes. Documented, accepted (decision K).

## BFF contract

What Forge expects from the portal's backend-for-frontend. Recorded here, implemented in the skillsite arc.

- The browser talks to the portal only. Forge gets no CORS configuration and sets no cookies.
- The client secret and both tokens stay on the server; the browser holds an `HttpOnly`, `Secure`,
  `SameSite=Lax` session cookie.
- Refresh is **single-flight** per session. The 10-second grace only absorbs an accidental race; a client that
  refreshes in parallel as a habit logs its users out.
- The portal client is granted `auth:users:login`, `account:self` and the unqualified scopes its users may hold
  at most (`crm:read`, `crm:write`, ...). It is a ceiling, not what a student gets.
- For a narrowed view the portal refreshes with `scope=...` instead of filtering on its side.

## Operating without a portal

Until the portal exists, everything works from Swagger UI:

1. `just bootstrap-admin --party-id <uuid> --email <address>` prints the invitation token.
2. Create an operator client through the existing `/auth/clients` routes and grant it `auth:users:login`,
   `account:self` and the scopes an admin needs.
3. Authorize as that client (`clientCredentials`), redeem the token with `POST /auth/password/redeem`.
4. Authorize again with the `password` flow: the token now is the admin's.

## Requirements

### Must-have (P0)

**Standing criteria - they hold for every slice and are ticked with the last PR of wave 3.**

- [ ] Nothing under `app/core/auth` imports `app.services` or `app.api`; nothing under `app/services/crm` or
      `app/api/v1/crm` imports the bot domain (ADR 0007 still holds).
- [ ] No plaintext password, refresh token or action token is written to a log, an audit `detail` or an error
      `detail` (a test greps the captured log output of the lifecycle test).
- [ ] Every property of every new schema and every new path and query parameter has a description in
      `openapi.json`; operation IDs match `^auth_[a-z_]+$`.
- [ ] `just check-all` is green; `openapi.json` is regenerated with `just openapi`, never edited.
- [ ] **SkillBot keeps working without a user.** The `client_credentials` grant, the claims of an application
      token and every `/bot` route behave exactly as before for an application principal: the existing tests of
      the token endpoint and the bot domain pass **unmodified** in every slice. Contract changes SkillBot's
      generated client can see are additive only (`refresh_token` / `refresh_expires_in` on `AccessTokenResponse`,
      new fields on `MeResponse`), plus the one-time scheme rename of P0-4.

**P0-1 - Spec and ADR.** _Docs only._

- [ ] This spec and ADR 0008 are on `main`; the decisions index lists 0008; issue #85 links the spec.

**P0-2 - Scope model.** _Pure code, no database._

- _Technique:_ the four new `Scope` members; `OWN_VARIANT`, `expand`, `canonical` in `scopes.py`;
  `app/core/auth/roles.py` with `Role`, `STORED_ROLES`, `BASE_USER_SCOPES`, `ROLE_SCOPES`, `scopes_for(roles)`;
  `resolve_token_scopes` reworked to the computation above; `get_current_principal` expands before comparing.
- _Acceptance criteria:_
  - [x] `expand({crm:read}) == {crm:read, crm:read:own}`; `canonical` is its inverse on every subset of `Scope`
        (property-style test over all members).
  - [x] Given user scopes `{account:self, crm:read:own}`: client grants `{account:self, crm:read, crm:write}`
        yield `account:self crm:read:own`; client grants `{crm:read, crm:write}` yield `crm:read:own` (the client
        is the ceiling for `account:self` too); client grants `{bot:read}` yield `invalid_scope`.
  - [x] A client granted `crm:read` may request `crm:read:own`; requesting `crm:write` without the grant is
        `invalid_scope`, as today.
  - [x] A token carrying `crm:read:own` gets `403` from a route guarded with `require_scopes(Scope.CRM_READ)`; a
        token carrying `crm:read` passes a route that requires `crm:read:own`.
  - [x] `test_no_role_carries_client_only_scopes`; all new scopes appear with their description in
        `components.securitySchemes`.
- _Proven by:_ `test_expand_adds_the_own_variant_of_an_unqualified_scope` and
  `test_canonical_is_the_inverse_of_expand_on_every_subset_of_scope` in
  [`test_scopes.py`](../../tests/auth/test_scopes.py) (`expand`/`canonical`); `test_user_grant_the_client_is_the_ceiling_for_account_self_too`,
  `test_user_grant_intersects_client_grants_with_user_scopes_when_none_requested` and
  `test_user_grant_with_a_disjoint_client_grant_is_invalid_scope` in
  [`test_resolve_token_scopes.py`](../../tests/auth/test_resolve_token_scopes.py) (the user-scopes ceiling
  computation), alongside `test_client_credentials_may_request_the_own_variant_of_a_granted_scope` and
  `test_client_credentials_rejects_a_scope_without_a_grant` in the same file (the `client_credentials` case);
  `test_require_scopes_rejects_the_own_variant_for_a_route_that_requires_the_unqualified_scope` and
  `test_require_scopes_accepts_the_unqualified_scope_for_a_route_that_requires_the_own_variant` in
  [`test_dependencies.py`](../../tests/auth/test_dependencies.py) (`get_current_principal` expanding before
  comparing); `test_no_role_carries_client_only_scopes` in [`test_roles.py`](../../tests/auth/test_roles.py) and
  `test_security_scheme_lists_every_scope_with_its_description` in
  [`test_openapi_contract.py`](../../tests/api/test_openapi_contract.py) (generic over every `Scope` member, so it
  already covers the four new ones).

**P0-3 - Data model.**

- _Technique:_ the four models, exported from `app/core/db/models/auth/__init__.py`; one autogenerated revision;
  [`DATABASE_SCHEMA.md`](../DATABASE_SCHEMA.md) updated.
- _Acceptance criteria:_
  - [x] Upgrade and downgrade run clean against an empty and a seeded database; the downgrade drops the three enum
        types.
  - [x] Inserting an e-mail with an uppercase letter violates the check constraint; two accounts for one party
        violate the unique constraint.
  - [x] Deleting a party through `delete_party` removes its account, roles, sessions and action tokens and leaves
        the audit log untouched.
- _Proven by:_ `test_user_account_tables_migration_is_reversible_and_drops_its_enum_types` in
  [`test_migration_apply.py`](../../tests/db/test_migration_apply.py) - the seeded path: it seeds
  `core.party`/`auth.application_client` before running the 0011 upgrade, seeds the four new tables too, then
  downgrades and asserts via `pg_type` that the three enum types are gone while the pre-existing rows survive,
  then upgrades again; the empty-database path is covered by the same file's
  `test_migrations_apply_match_models_and_reverse` (`base -> head -> base -> head` on a fresh database).
  `test_uppercase_email_violates_the_lowercase_check_constraint` and
  `test_a_second_account_for_the_same_party_violates_the_unique_constraint` in
  [`test_auth_user_models.py`](../../tests/db/models/test_auth_user_models.py) assert the specific
  `asyncpg` error class and `constraint_name` (`ck_user_account_email_lowercase` /
  `user_account_party_id_key`), not just any `IntegrityError`. And
  `test_deleting_a_party_removes_its_account_roles_sessions_and_action_tokens_but_not_the_audit_log` in the same
  file, which calls the unchanged `delete_party`.

**P0-4 - User principal.**

- _Technique:_ `ApplicationPrincipal` / `UserPrincipal` under the abstract `Principal`; `create_access_token`
  for both types, with `create_application_access_token` as the application token's shorthand; the claims as a
  pydantic discriminated union on `principal_type`; one scope normalizer, `parse_scopes`, where scopes enter,
  and `expand` / `canonical` over sets; `MeResponse` extended; `OAuth2Bearer` with both flows and
  `scheme_name="OAuth2"`; `bind_request_log_context` receives `principal_type`, `user_id` and `party_id` for user
  principals.
- _Acceptance criteria:_
  - [x] `components.securitySchemes` has exactly one key, `OAuth2`, and every secured operation references it;
        apart from that key the `security` requirement of every existing operation is unchanged.
  - [x] A user token round-trips into a `UserPrincipal` with `party_id`, `session_id` and `roles`; an application
        token round-trips exactly as before (its claims are byte-compatible, asserted against a fixture).
  - [x] A user token missing `party_id` or `sid`, or whose `sub` is not `user:<principal_id>`, is a `401`.
  - [x] `GET /auth/me` answers both principal types without a database session (the existing
        "depends on the principal only" criterion of `crm-api.md` P1-1 still holds).
- _Proven by:_ `test_the_contract_declares_exactly_one_security_scheme`,
  `test_every_secured_operation_references_only_that_scheme`,
  `test_the_scheme_rename_left_every_security_requirement_unchanged` and
  `test_the_scheme_offers_the_client_credentials_and_the_password_flow` in
  [`test_openapi_security_scheme.py`](../../tests/api/test_openapi_security_scheme.py) - the third one pins
  `SECURITY_REQUIREMENTS_AT_THE_RENAME`, the scopes all 66 operations demanded before the rename, so only the key
  moved. `test_a_user_token_validates_back_into_the_user_principal_it_was_issued_for`,
  `test_user_access_token_claims_match_the_fixture` and
  `test_application_access_token_claims_match_the_fixture` in
  [`test_user_tokens.py`](../../tests/auth/test_user_tokens.py) (the round trip and both claim fixtures, the
  application one asserted claim by claim so a new claim on SkillBot's token breaks it), next to
  `test_an_application_token_validates_back_into_an_application_principal`,
  `test_user_access_token_carries_the_canonical_scope` and the rejections in the same file:
  `test_validate_access_token_rejects_a_user_token_without_its_reach_claims` (both `party_id` and `sid`),
  `test_validate_access_token_rejects_a_user_token_whose_subject_is_not_its_principal`,
  `..._whose_subject_is_not_canonically_spelled` (`sub` must be the one spelling Forge writes, whatever
  spelling `uuid.UUID` would also parse), `..._with_an_application_subject`, `..._with_an_unusable_party_id`,
  `..._with_malformed_roles` (including an unknown role), `..._without_a_usable_scope` and
  `test_validate_access_token_rejects_an_unknown_principal_type`. Over HTTP the same three denials are a `401` in
  the error envelope: `test_me_with_a_user_token_that_lost_its_party_is_the_401_envelope`,
  `..._that_lost_its_session_...` and `..._whose_subject_is_not_its_principal_...` in
  [`test_auth_me_endpoint.py`](../../tests/api/test_auth_me_endpoint.py), which also holds
  `test_me_reports_the_account_the_party_and_the_roles_of_a_user_token` and the unchanged
  `test_me_depends_on_the_principal_only` (the route's only parameter is the principal). The request log is
  covered by `test_request_logging_identifies_the_user_behind_a_request` in
  [`test_logging.py`](../../tests/test_logging.py) and the guard by
  `test_require_application_rejects_a_real_user_token` in
  [`test_dependencies.py`](../../tests/auth/test_dependencies.py).

**P0-5 - Accounts.**

- _Technique:_ the account services split by concern, like the client ones - `services/users.py` (invite, load,
  list, update, stored roles), `services/action_tokens.py` (issue, redeem), `services/sessions.py`
  (`SessionRevokedReason`, revoke) and `services/accounts.py` (get and lock an account row, the base the other
  three build on) - plus `services/roles.py` (`derive_roles`); `LoginEmail` and `normalize_email` in
  `app/core/auth/inputs.py`; `app/api/v1/auth/users.py` and the redeem route in `app/api/v1/auth/password.py`;
  `app/core/auth/passwords.py` for the policy and the dummy hash - hashing and token generation are
  `hash_secret`, `verify_secret`, `digest` and `generate_secret` in [`secrets.py`](../../app/core/auth/secrets.py),
  the one home of the secret primitives; `bootstrap_admin` in [`bootstrap.py`](../../app/core/auth/bootstrap.py)
  with a `just bootstrap-admin` recipe.
- _Acceptance criteria:_
  - [x] Inviting a person party answers `201` with an `invited` account and a token; the same party again is
        `user_account_already_exists`; a company is `account_party_not_a_person`; another account's e-mail is
        `user_email_already_in_use`. `Anna@Example.org` is stored as `anna@example.org`.
  - [x] Redeeming the invitation sets the password and activates the account; redeeming it again, redeeming an
        expired token and redeeming a token replaced by a newer one all answer the same `invalid_action_token`.
  - [x] Redeeming a `password_reset` revokes every session of the account; redeeming an invitation revokes none.
  - [x] Disabling an account revokes its sessions; enabling an account without a password is
        `user_account_state`.
  - [x] `roles` of `UserAccountDetail` lists `student` for a party with a `Student` row, `guardian` for a party
        with an outgoing `PAYS_FOR`, and `admin` + `tutor` for a tutor holding the stored role.
  - [x] Every route is `403` for an application token without `auth:users:manage`; the redeem route is `403` for a
        user token, whatever its scopes.
  - [x] `just bootstrap-admin` is idempotent: run twice it keeps the account and issues a fresh invitation only
        while the account has no password.
- _Proven by:_ `test_inviting_a_person_party_answers_201_with_an_invited_account_and_a_token`,
  `test_inviting_the_same_party_again_is_a_conflict`, `test_inviting_a_company_party_is_rejected`,
  `test_inviting_with_an_email_another_account_uses_is_a_conflict` and `test_inviting_stores_the_email_lowercased`
  in [`test_auth_users_api.py`](../../tests/db/auth/test_auth_users_api.py), which also covers disabling
  (`test_disabling_an_account_revokes_its_sessions`,
  `test_enabling_an_account_without_a_password_is_rejected`) and the stored roles.
  `test_redeeming_an_invitation_sets_the_password_and_activates_the_account` and
  `test_every_rejected_token_answers_the_same_body` (one body for unknown, used, expired and replaced) in
  [`test_auth_password_redeem_api.py`](../../tests/db/auth/test_auth_password_redeem_api.py), next to
  `test_redeeming_a_password_reset_revokes_every_session_of_the_account` and
  `test_redeeming_an_invitation_revokes_no_session`;
  `test_an_overlapping_issue_waits_and_leaves_exactly_one_live_token` in
  [`test_auth_action_token_concurrency.py`](../../tests/db/auth/test_auth_action_token_concurrency.py) pins that
  two issues arriving together leave one live token.
  The derivation table is checked by `test_the_detail_lists_student_for_a_party_with_a_student_row`,
  `test_the_detail_lists_guardian_for_a_party_with_an_outgoing_pays_for`,
  `test_the_detail_lists_admin_and_tutor_for_a_tutor_holding_the_stored_role` and
  `test_tutor_of_does_not_make_a_tutor_a_guardian` in
  [`test_auth_derived_roles.py`](../../tests/db/auth/test_auth_derived_roles.py).
  The guards run over every operation of the document:
  `test_a_user_route_answers_403_without_the_manage_scope` and
  `test_the_redeem_route_answers_403_for_a_user_principal_whatever_its_scopes` in
  [`test_auth_users_authz.py`](../../tests/api/test_auth_users_authz.py); the contract itself (route map,
  operation IDs, descriptions, the token living in one schema) in
  [`test_auth_users_openapi.py`](../../tests/api/test_auth_users_openapi.py).
  `test_bootstrapping_twice_keeps_the_account_and_issues_a_fresh_invitation` and
  `test_bootstrapping_an_account_that_has_a_password_issues_no_token` in
  [`test_auth_admin_bootstrap.py`](../../tests/db/auth/test_auth_admin_bootstrap.py) cover the operator command,
  whose `--email` is parsed by the API's own rule
  ([`test_bootstrap_cli.py`](../../tests/auth/test_bootstrap_cli.py)), and
  `test_the_invite_and_redeem_flow_logs_neither_the_token_nor_the_password` in
  [`test_auth_users_logging.py`](../../tests/db/auth/test_auth_users_logging.py) greps the captured log output of
  the whole flow.
  What holds when two requests interleave is pinned separately:
  `test_an_overlapping_redeem_of_the_same_token_is_refused` and
  `test_an_overlapping_removal_of_the_same_role_is_not_found` in
  [`test_auth_users_concurrency.py`](../../tests/db/auth/test_auth_users_concurrency.py) (two real transactions),
  and `test_issuing_reads_the_account_under_the_lock_not_what_the_session_held` plus the three
  check-then-insert cases in
  [`test_auth_users_service_guards.py`](../../tests/db/auth/test_auth_users_service_guards.py), which reach past
  the pre-checks to the constraint underneath. The audit trail of a change is pinned by
  [`test_auth_users_audit.py`](../../tests/db/auth/test_auth_users_audit.py) - a PATCH that changes nothing
  records nothing - and the cost of a redeem by
  `test_the_password_is_hashed_before_the_account_row_is_locked` in
  [`test_auth_redeem_hashing.py`](../../tests/db/auth/test_auth_redeem_hashing.py).

**P0-6 - Grants.**

- _Technique:_ `issue_user_token` and `refresh_user_token` in
  [`services/tokens.py`](../../app/core/auth/services/tokens.py); `ClientTokenForm` becomes `TokenForm`;
  `create_token` dispatches on the grant; `POST /auth/revoke`; sessions are opened, rotated and revoked in
  [`services/sessions.py`](../../app/core/auth/services/sessions.py), and their refresh tokens are generated and
  hashed with `generate_secret` and `digest` from [`secrets.py`](../../app/core/auth/secrets.py) - no second
  hashing module.
- _Acceptance criteria:_
  - [ ] The lifecycle test (database): invite -> redeem -> `password` -> call `/auth/me` -> `refresh_token` ->
        `revoke` -> the revoked refresh token is `invalid_grant`.
  - [ ] Unknown e-mail, wrong password, `invited`, `disabled` and locked accounts answer the identical
        `invalid_grant` body; a client without `auth:users:login` is `unauthorized_client`.
  - [ ] After five wrong passwords the correct one is refused until `locked_until`; **the counter is persisted
        although the request was denied** (the sibling of
        `test_auth_token_denial_commits_the_session_so_the_audit_entry_survives`).
  - [ ] Presenting a rotated-out refresh token after the grace revokes the session and writes
        `session.reuse_detected`; within the grace it only fails.
  - [ ] A refresh token is refused for another client than the one that opened the session.
  - [ ] Removing the `admin` role and refreshing yields a token without the admin scopes; refreshing with
        `scope=account:self crm:read:own` as an admin yields exactly that; requesting more than the session's
        `scope` is `invalid_scope`.
  - [ ] Swagger UI's "Authorize" dialog offers the `password` flow (asserted over `app.openapi()`).

**P0-7 - Reach.**

- _Technique:_ `app/core/auth/reach.py` (`Access`, `resolve_reach`, `DELEGATION_RELATION_TYPES`);
  `require_access` in `dependencies.py`; the alternative security requirement and the 403 wording in
  `customize_openapi`; in the CRM API two aliases in [`params.py`](../../app/api/v1/crm/params.py) -
  `CrmReadAccess` (`Annotated[Access, require_access(Scope.CRM_READ)]`) and `VisibleParty`, a path alias that
  depends on it and raises `PartyNotFoundError` when `access.allows(party_id)` is false, so the endpoint
  signatures keep passing `test_the_crm_endpoints_carry_no_boilerplate`; `list_parties` in [`parties.py`](../../app/services/crm/parties.py) gains a keyword-only
  `party_ids: frozenset[UUID] | None = None` filter; `GET /crm/parties` and `GET /crm/parties/{party_id}` switch
  to `require_access(Scope.CRM_READ)`; `authz.py` imports `DELEGATION_RELATION_TYPES`.
- _Acceptance criteria:_
  - [ ] A student reads their own party and gets `404 party_not_found` for any other - the same body an unknown
        UUID produces.
  - [ ] A mother with `PARENT_OF` to one child and `PAYS_FOR` to another reads both children and herself; the
        list returns exactly these three with `total == 3`; filters and paging still apply within the reach.
  - [ ] `TUTOR_OF` does not extend reach: a tutor does not read their student's party.
  - [ ] An application token with `crm:read` behaves exactly as before on both routes and issues no query on
        `core.party_relation` (asserted by counting statements).
  - [ ] A user token with `crm:read:own` is `403` on every CRM route other than the two (a test walks
        `app.routes`).
  - [ ] An application token that carries only `crm:read:own` is `403` on both routes.
  - [ ] Both operations list the two alternative security requirements in `openapi.json`.
  - [ ] `_allowed_target_parties` and `resolve_reach` return the same set for the same party (one test, both
        functions).
  - [ ] [`ARCHITECTURE.md`](../ARCHITECTURE.md) (Auth section), the `auth` entry of `OPENAPI_TAGS` and the
        conventions in `CLAUDE.md` (`require_access`) are updated.

### Nice-to-have (P1)

- **P1-1 - Mail delivery.** A `Mailer` port with an SMTP implementation and `MAIL__*` settings; invitation and
  reset are sent instead of returned; a public, client-guarded "forgot password" route that always answers `202`;
  `password_reset_expire_hours` drops to 1.
- **P1-2 - Self-service.** `require_user`; `POST /auth/me/password` (current + new password, revokes the other
  sessions); `GET /auth/me/sessions`, `DELETE /auth/me/sessions/{session_id}`. Guarded by `account:self`.
- **P1-3 - Housekeeping.** The reaper deletes sessions and action tokens that expired more than 30 days ago.
- **P1-4 - Reach-aware writes.** `crm:write:own` and the first routes that accept it (a person's own contact
  infos); decides which fields a guardian may change for a child.
- **P1-5 - Remaining CRM reads** become reach-aware where a restricted view makes sense (roles, contact infos,
  relations).

### Future considerations (P2)

- **Tutor reach with projections,** together with the lessons arc: a per-scope reach definition
  (`lessons:read:own` follows `TUTOR_OF`) and representations that differ by the basis of the access.
- **Federated login** through the existing `ext.discord_account` / `ext.microsoft_account` links.
- **MFA** for accounts holding a stored role.
- **Asymmetric signing + JWKS** once a second service validates Forge's tokens.

## Decided (formerly open questions)

- **Login method:** e-mail + password, not passwordless and not federated-only.
- **Roles:** derived from the CRM, `admin` stored; a tutor who is an admin holds both and picks the view by
  requesting scopes.
- **Session model:** backend-for-frontend; Forge stays a bearer-only API.
- **Mail:** none in P0; the operator forwards the token.
- **Scope of the arc:** identity, login and the reach mechanism with one reference rollout; the portal's data
  surface is a later arc.
- **Reach as a scope qualifier,** not a global claim and not the bot's grant engine (ADR 0008).

## Timeline / phasing

One PR per requirement, each branched from `main` - no stacked PRs. **P0-1** settles the contract; the six
implementation slices then run in three waves of two parallel PRs. A wave starts when both PRs of the previous
wave are merged.

| Wave | Slices                                     | Needs                                             |
| ---- | ------------------------------------------ | ------------------------------------------------- |
| 1    | **P0-2** scope model, **P0-3** data model  | nothing; the two touch disjoint files             |
| 2    | **P0-4** user principal, **P0-5** accounts | P0-4: P0-2. P0-5: P0-2 and P0-3                   |
| 3    | **P0-6** grants, **P0-7** reach            | P0-6: all of the above. P0-7: P0-2 and P0-4 only  |

- **P0-7 does not wait for the login flow.** Its tests mint user tokens with `create_access_token` and a
  `UserPrincipal` (P0-4), the way the CRM tests mint application tokens today.
- **`openapi.json` is generated, so it is never merged by hand.** Within a wave both PRs change it; the PR that
  merges second rebases onto `main`, takes either side of the file, reruns `just openapi` and `just check-all`.
  The other overlaps (`schemas.py`, the exports of `app/core/auth/__init__.py`) are appends.
- From P0-6 on the operator logs in through Swagger UI.
- The standing criteria are ticked with the last PR of wave 3.

**Dependency:** none open. P0-7 touches CRM code and therefore follows the CRM's rules (`crm-api.md`).

## Rules for implementing agents

- The rules of [`api-conventions.md`](api-conventions.md) and [`crm-api.md`](crm-api.md) apply unchanged (English
  only, symbol references in docs, never hand-edit `openapi.json`, `just check` green before every commit,
  conventional commits).
- **Never branch on a role** to authorize. Roles produce scopes at issuance; guards read scopes.
- **Never filter by hand.** A route that serves restricted users takes an `Access` from `require_access`; a route
  that does not must keep `require_scopes`. Do not add a `:own` scope to `OWN_VARIANT` without the routes that
  honor it.
- **Never raise after writing a denial.** Return the `ApiError` response (decision N).
- **Never log or persist a plaintext secret,** and never put a typed e-mail address of an unknown account into an
  audit `detail`.
- The error catalog is closed. If a requirement seems to need a new class or `code`, stop and report.
- Do not touch the `bot` schema, the grant engine or `delete_party`.
