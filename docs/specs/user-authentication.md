# Spec: User authentication (accounts, grant modes, reach-qualified scopes)

> Status: Draft (2026-09) | Domain arc `auth`
> Tracking: [#85](https://github.com/Nachhilfe-Leon-Weimann/skillforge/issues/85)
> Builds on the [project sketch](../PROJECT.md), [`api-conventions.md`](api-conventions.md), goal 4 of
> [`crm-api.md`](crm-api.md) ("exactly one target party per route") and
> [ADR 0008](../decisions/0008-user-authentication-and-reach.md).
> Written to be executed by coding agents: every requirement names its symbols, files and checkable criteria.

## Problem statement

SkillForge authenticates applications only. `POST /auth/token` knows one grant (`client_credentials`), a client
has one list of scope grants, and a scope such as `crm:read` means _every record_.

The project sketch asks for more. People use SkillForge through frontends - the portal acts for the person who
logged in, the bot (in its own arc) for the person who typed a command - and the same person gets the same rights
through either. The frontend is the ceiling, some frontends also work for themselves, and a person may be
restricted to their own data. None of that can be expressed today:

- **Nobody can log in.** A person exists as a `Party`; there is no account, no credential, no session.
- **A client's grants mean one thing.** They are what the client may do for itself; there is no way to say "the
  most this client may do for a person".
- **Nobody can be restricted to their own data.** Handing a student `crm:read` would show them every party.

The first consumer of this arc is the operator in Swagger UI: creating accounts, logging in through the
"Authorize" dialog, watching a restricted token get a filtered answer. The portal follows in its own arc in
skillsite; the bot's use of person tokens follows in the bot arc (see
[Designed for the bot arc](#designed-for-the-bot-arc)).

## Goals

1. **People log in with e-mail and password** through a client and get a token the existing guards understand.
2. **Client grants have a mode**: what a client may do for itself (`application`) and the most it may do for a
   person (`delegated`).
3. **One token model.** A token for a person is issued to a client on behalf of that person; its scopes are
   computed once, at issuance. A request is validated exactly once, the same way for both kinds of token.
4. **One permission system.** Routes keep declaring scopes. "Everything" versus "only mine" is a qualifier on the
   scope, not a second mechanism.
5. **Safe by default.** A restricted person reaches only routes that were deliberately made reach-aware.
6. **The account is the door.** An account belongs to exactly one person party, only admins create it, and it is
   separate from the ways a person logs in.
7. **Ready for the bot.** The model takes the token exchange, Discord as a way to log in and tutor reach without
   changing shape.

## Non-goals

- **The portal itself** and its backend-for-frontend. This spec only records what SkillForge expects from it
  ([BFF contract](#bff-contract)).
- **Building the bot's side:** token exchange, Discord as a login method, tutor reach, account creation by tutors,
  retiring the bot's `PermissionGrant` engine. Designed here, built in the bot arc.
- **Sending e-mail.** The operator receives one-time tokens in the API response and passes them on by hand (P1-1).
- **Self-signup** and self-service for people (P1-2).
- **Reach-aware writes** (`crm:write:own`). Restricted people are read-only until P1-4.
- **Federated login beyond Discord, MFA, asymmetric signing, JWKS, token introspection.**
- **Instant revocation of access tokens.** They stay stateless (decision L).
- **Any change to the `bot` schema or the grant engine.**

## Decisions

| Topic                                    | Decision                                                                                                                                                                             | Rationale                                                                                                                           |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| **A - Identity provider**                | SkillForge itself. Accounts, hashes and sessions live in the `auth` schema.                                                                                                          | No second service, no second user store (ADR 0008).                                                                                 |
| **B - Password**                         | Argon2 via `hash_secret` in [`secrets.py`](../../app/core/auth/secrets.py); 8 to 128 characters, no composition rules, no list of forbidden passwords.                               | One hashing home; a length rule people can live with.                                                                               |
| **C - Account origin**                   | Only admins create accounts (`auth:users:manage`), always for an existing person party. No self-signup.                                                                              | The account is the door (sketch, principle 8).                                                                                      |
| **D - Login identifier**                 | `user_account.email`: optional, stored lowercased, unique when set. Not a reference to `ContactInfo`.                                                                                | CRM addresses are unique per party only, and must not be changeable through `crm:write`. People who only use Discord may have none. |
| **E - Account vs. login**                | `status` is `active` or `disabled`; an account is `active` from its creation. Logging in is a separate matter: a password (this arc) or Discord (bot arc).                           | A person who never uses the portal still needs an account; an `active` account without a way to log in is "not set up yet".         |
| **F - Grant modes**                      | `application_client_scope_grant.mode`: `application` (for the client itself) or `delegated` (ceiling for people). Client-only scopes are grantable in `application` mode only.       | The client is the ceiling without its ceiling becoming its own rights.                                                              |
| **G - Token endpoint**                   | `POST /auth/token` gains `password` and `refresh_token`. Client authentication is required for every grant; both new grants require `auth:users:login` in `application` mode.        | One issuing path, one response model, one audit path.                                                                               |
| **H - Token scopes**                     | `client_credentials`: from `application` grants. A person's token: `delegated` grants ∩ the person's role scopes. A request only narrows. Computed at issuance and at every refresh. | "Client on behalf of person" without a second check at request time.                                                                |
| **I - Reach qualifier**                  | `<scope>:own` restricts a scope to reachable records; the unqualified scope implies it. `require_scopes` demands the unqualified form, `require_access` accepts either.              | A forgotten filter is a `403`, never a leak.                                                                                        |
| **J - Reach derivation**                 | Own party plus the `to_party` of outgoing `PARENT_OF` / `PAYS_FOR` relations, resolved per request, each with its basis (`self`, `guardian`).                                        | Same set as the bot's delegation check; always current; the basis leaves room for tutor projections.                                |
| **K - Roles**                            | `student`, `tutor`, `guardian` derived from the CRM, never stored; `admin` is a row in `auth.user_account_role`. Role-to-scope mapping in code. Roles are a set.                     | No second role source that can drift from the CRM; `admin` is an access right, not a CRM fact.                                      |
| **L - Sessions**                         | Access token: stateless JWT, 15 minutes. Refresh token: opaque, rotating, stored as SHA-256, 30 days absolute, reuse detection with a 10-second grace.                               | Disabling an account or removing a role takes effect within 15 minutes without a lookup per request.                                |
| **M - Out of reach**                     | `404` with the resource's own not-found error, never `403`.                                                                                                                          | A restricted caller cannot probe which parties exist.                                                                               |
| **N - Deleting a party**                 | `user_account.party_id` is `ON DELETE CASCADE`; `delete_party` and its `EXTERNAL_LINKS` guard are not touched.                                                                       | The CRM stays unaware of accounts (ADR 0007).                                                                                       |
| **O - Denials are returned, not raised** | Every denial that writes state (audit entry, failed-login counter, session revocation) is returned as a response.                                                                    | Raising rolls the session back and would undo the counter - the pattern `create_token` already follows.                             |
| **P - First admin**                      | `just bootstrap-admin --party-id <uuid> --email <address>` creates the account with the `admin` role and prints an invitation token.                                                 | Creating accounts needs an admin; the first one cannot be created through the API. It also recovers from a lockout.                 |
| **Q - How a token was obtained**         | A person's token carries `amr` (RFC 8176): `["pwd"]` in this arc, `["discord"]` from the bot arc.                                                                                    | The two ways differ in strength; password-only actions (P1-2) can demand `pwd`.                                                     |

**Open:** whether `admin` also carries `bot:write`. It starts without it; adding it is one line in `ROLE_SCOPES`.

## Client grants

`ApplicationClientScopeGrant` gains `mode: GrantMode` (`StrEnum`: `APPLICATION = "application"`,
`DELEGATED = "delegated"`); the primary key becomes `(application_client_id, scope_key, mode)`, so a scope can be
granted in both modes. The migration turns every existing grant into an `application` grant - SkillBot keeps
exactly the rights it has.

`CLIENT_ONLY_SCOPES: frozenset[Scope]` in [`scopes.py`](../../app/core/auth/scopes.py) holds the scopes that only
make sense for a client: `auth:users:login` in this arc (the bot arc adds `auth:users:exchange`). Granting one of
them in `delegated` mode is refused with `invalid_scope` and grants nothing; no role's scopes contain one.

Client API (all `auth:clients:manage`, as today):

- `POST /auth/clients/{client_id}/scopes` takes `{scopes, mode}`; `mode` is required.
- `DELETE /auth/clients/{client_id}/scopes/{mode}/{scope_key}` revokes one grant.
- `ApplicationClientResponse` replaces `scopes` with `application_scopes` and `delegated_scopes`, each sorted.
- Audit entries of grants and revocations name the mode.

The service functions (`grant_application_client_scopes`, `bootstrap_application_client`, ...) take `mode` as a
keyword that defaults to `application`, so `just bootstrap-skillbot` and every existing caller behave as before.

## Scopes and roles

### New scopes

Declared on `Scope` in [`scopes.py`](../../app/core/auth/scopes.py) as `(value, description)`.

| Scope               | Granted as                 | Meaning                                                                                        |
| ------------------- | -------------------------- | ---------------------------------------------------------------------------------------------- |
| `crm:read:own`      | `delegated`, `application` | Read parties within the caller's reach. Useless on an application token (no person, no reach). |
| `account:self`      | `delegated`                | Manage the caller's own account. Also guarantees a person's token never has an empty scope.    |
| `auth:users:manage` | either                     | Create, disable and reset accounts; assign stored roles.                                       |
| `auth:users:login`  | `application` only         | Log people in on their behalf (`password` / `refresh_token` grants, redeem, revoke).           |

### Implication

`OWN_VARIANT: dict[Scope, Scope]` maps an unqualified scope to its qualified form; in this arc it has one entry,
`CRM_READ -> CRM_READ_OWN`. `expand(scopes)` adds the `:own` variant of every unqualified scope present;
`canonical(scopes)` drops `x:own` wherever `x` is present. Both take a `Set[str]`; `parse_scopes` and
`format_scopes` are the only places a scope string is split or joined. A token always carries the canonical form;
every check expands first (`missing = required - expand(principal.scopes)` in `get_current_principal`).

### Token scope computation

```
client_credentials:   available = expand(application grants)
password / refresh:   available = expand(delegated grants) & expand(role scopes)
                                  (refresh: & expand(parse_scopes(session.scope)) as well)

no scope requested  ->  token = canonical(available)
scope requested     ->  requested <= available, else invalid_scope;  token = canonical(requested)
empty result        ->  invalid_scope
```

`resolve_token_scopes` in [`services/scopes.py`](../../app/core/auth/services/scopes.py) implements it; the caller
passes the grants of the mode that applies. For `client_credentials` the observable behavior only widens (a client
granted `crm:read` may now request `crm:read:own`).

### Roles

`Role` (`StrEnum`), `STORED_ROLES`, `BASE_USER_SCOPES`, `ROLE_SCOPES` and `scopes_for(roles)` live in
`app/core/auth/roles.py`; the derivation that reads the CRM tables lives in `app/core/auth/services/roles.py`.

| Role           | Source                                                       | Scopes                                                                          |
| -------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| _(every user)_ | -                                                            | `account:self`, `crm:read:own`                                                  |
| `student`      | the party's person has a `Student` row                       | -                                                                               |
| `tutor`        | the party's person has a `Tutor` row                         | -                                                                               |
| `guardian`     | the party has an outgoing `PARENT_OF` or `PAYS_FOR` relation | -                                                                               |
| `admin`        | row in `auth.user_account_role`                              | `crm:read`, `crm:write`, `auth:users:manage`, `auth:clients:manage`, `bot:read` |

The derived roles carry no scopes of their own yet; they tell `/auth/me` and the token's `roles` claim which views
to offer. **SkillForge authorizes by scope only; it never branches on a role.**

`DELEGATION_RELATION_TYPES` (`PARENT_OF`, `PAYS_FOR`) lives in `app/core/auth/reach.py`. The guardian derivation,
the reach and [`authz.py`](../../app/services/bot/authz.py) all import it, so the bot's delegation set and the
API's reach cannot drift apart.

## Accounts

**`auth.user_account`** (`UserAccount`, `TimestampMixin`)

| Column               | Type                         | Notes                                                                  |
| -------------------- | ---------------------------- | ---------------------------------------------------------------------- |
| `id`                 | `uuid` PK                    | The token's `principal_id`; `sub` is `user:<id>`.                      |
| `party_id`           | `uuid` FK -> `core.party.id` | `NOT NULL`, `UNIQUE`, `ON DELETE CASCADE` (decision N).                |
| `email`              | `text`                       | `NULL` allowed, `UNIQUE`, `CHECK (email = lower(email))` (decision D). |
| `password_hash`      | `text`                       | `NULL` until a password is set.                                        |
| `status`             | enum `user_account_status`   | `active` / `disabled`; server default `active`.                        |
| `failed_login_count` | `int`                        | `NOT NULL`, default `0`.                                               |
| `locked_until`       | `timestamptz`                | `NULL` = not locked.                                                   |
| `last_login_at`      | `timestamptz`                | `NULL` until the first login.                                          |

**Life cycle** (routes in the [route map](#route-map)):

- **Create.** `POST /auth/users` with `{party_id, email?, roles?}`: the party must exist and be a `PERSON`, it may
  not have an account yet, the e-mail may not be in use. `roles` names stored roles only. The account is `active`;
  the response carries no token.
- **Invite** - set up the password login. Needs an e-mail address and no password yet, else `user_account_state`.
  Returns the plaintext token once; earlier unused invitations stop working.
- **Reset.** Needs a password, else `user_account_state`. Nothing changes until the token is redeemed.
- **Redeem** (`POST /auth/password/redeem`, a login client on the person's behalf) serves both purposes: sets the
  hash, clears counter and lock, marks the token used and - for a reset - revokes every session. The status does
  not change: an `active` account stays active, a `disabled` one stays disabled.
- **Update.** `PATCH` with the `MISSING`-based model of the CRM: `email` (a new address, or `null` to remove it)
  and `status`. Disabling revokes all sessions; enabling is always allowed. Changing or removing the e-mail
  invalidates the account's unused action tokens; removing the e-mail of an account that has a password is
  `user_account_state` (the password login needs it). A request that changes nothing records nothing.
- **Stored roles.** `PUT` / `DELETE /auth/users/{user_id}/roles/{role}`, idempotent `PUT`, row-locked like v1.
- **Sessions.** `DELETE /auth/users/{user_id}/sessions` revokes every live session.

**`auth.user_account_role`** (`UserAccountRole`, `CreatedAtMixin`) - primary key `(user_account_id, role)`,
`ON DELETE CASCADE`; `role` enum `user_account_role_name` with the single value `admin`.

**`auth.user_action_token`** (`UserActionToken`, `CreatedAtMixin`) - one-time tokens for invitation and reset:
`purpose` (enum `user_action_token_purpose`: `invitation` / `password_reset`), `token_hash` (`UNIQUE`, SHA-256
hex, plaintext prefix `sf_ua_`), `expires_at`, `used_at`, `invalidated_at`, `issued_by`
(`<principal_type>:<principal_id>` or `cli`); `user_account_id` indexed, `ON DELETE CASCADE`.

**`auth.user_session`** (`UserSession`, `CreatedAtMixin`) - one row per login: `user_account_id` (indexed),
`application_client_id` (indexed; only this client may refresh or revoke the session), `scope` (canonical scopes
granted at login - the ceiling of every refresh), `refresh_token_hash` (`UNIQUE`), `previous_refresh_token_hash`
(`UNIQUE`), `rotated_at`, `expires_at` (absolute), `last_used_at`, `revoked_at`, `revoked_reason` (`logout`,
`password_reset`, `account_disabled`, `admin`, `reuse_detected`). Both foreign keys `ON DELETE CASCADE`.

Refresh and action tokens are `generate_secret(prefix)` values (32 random bytes) stored as `digest` (SHA-256):
they carry full entropy, and the lookup needs a deterministic hash. Passwords use `hash_secret` / `verify_secret`
(Argon2); `verify_and_update` is added for the login.

**`AuthAuditLog`** is reused. `AuditEventType` gains `user_account.created`, `user_account.updated`,
`user_account.disabled`, `user_account.enabled`, `user_role.added`, `user_role.removed`, `invitation.issued`,
`password_reset.issued`, `password.set`, `session.revoked`, `session.reuse_detected`; `token.issued` /
`token.denied` are written with `principal_type="user"` for a person's grants. An audit `detail` never contains a
secret, and never an e-mail address.

**Settings** (`AuthSettings` in [`config.py`](../../app/core/auth/config.py)): `invitation_expire_hours = 168`,
`password_reset_expire_hours = 24` (one hour once P1-1 sends mail), `refresh_token_expire_days = 30`,
`login_lockout_threshold = 5`, `login_lockout_max_minutes = 15`.

## Tokens

`POST /api/v1/auth/token`, form-encoded, OAuth2 error codes (RFC 6749, section 5.2). Client authentication (HTTP
Basic or `client_id` / `client_secret` in the form) is required for **every** grant.

| `grant_type`         | Additional form fields                        | Result                                                        |
| -------------------- | --------------------------------------------- | ------------------------------------------------------------- |
| `client_credentials` | `scope?`                                      | Unchanged, from `application` grants.                         |
| `password`           | `username` (the e-mail), `password`, `scope?` | Opens a `user_session`; the response carries `refresh_token`. |
| `refresh_token`      | `refresh_token`, `scope?`                     | Rotates the refresh token; the response carries the new one.  |

`AccessTokenResponse` gains `refresh_token` and `refresh_expires_in` (seconds until the session's `expires_at`),
both `null` for `client_credentials`.

**`password`, in this order:**

1. Authenticate the client -> `invalid_client`.
2. The client holds `auth:users:login` in `application` mode -> else `unauthorized_client`.
3. Normalize `username` (`normalize_email`) and look the account up. If it does not exist, is `disabled`, has no
   password or is locked (`locked_until > now`): verify the submitted password against `dummy_password_hash()`,
   then answer `invalid_grant`. A locked account's counter does not move.
4. `verify_and_update` the password. On failure: increment `failed_login_count`; from `login_lockout_threshold`
   on, set `locked_until = now + min(max, 1 min * 2^(count - threshold))`; answer `invalid_grant`. On success:
   reset counter and lock, set `last_login_at`, store an upgraded hash if one came back.
5. Derive the roles, compute the scopes from the client's `delegated` grants -> `invalid_scope`.
6. Open the session, issue both tokens with `amr: ["pwd"]`, write `token.issued`.

`invalid_grant` has **one** body for every case in steps 3 and 4.

**`refresh_token`:** authenticate the client and check `auth:users:login` as above, then look the digest up.

- It matches `refresh_token_hash` of a live, unexpired session of **this** client whose account is `active`:
  re-derive the roles, compute the scopes with the session's `scope` as an additional ceiling, rotate
  (`previous <- current`, `rotated_at = now`), answer with both tokens. The token carries `amr: ["pwd"]` - in this
  arc every session comes from a password login.
- The account is `disabled`: revoke the session (`account_disabled`), answer `invalid_grant`.
- It matches `previous_refresh_token_hash`: within 10 seconds of `rotated_at` answer `invalid_grant` and leave
  the session alone (two requests raced); after that revoke the session (`reuse_detected`), write
  `session.reuse_detected`, answer `invalid_grant`.
- Anything else: `invalid_grant`.

**`POST /auth/revoke`** (`{refresh_token}`, a login client) revokes the session the token belongs to if this client
opened it (`logout`), and answers `204` whether or not it found one (RFC 7009 semantics).

**Claims of a person's token** (the application token keeps its shape, byte for byte):

```
iss, aud, iat, exp, jti      as today
sub             "user:<user_account.id>"
principal_type  "user"
principal_id    "<user_account.id>"
azp             "<client_id of the client that logged the person in>"
scope           canonical, space-separated
party_id        "<core.party.id>"
sid             "<user_session.id>"
roles           ["admin", "tutor"]        sorted; informational
amr             ["pwd"]                   how the person was authenticated (decision Q)
```

`Principal` in [`principal.py`](../../app/core/auth/principal.py) is the abstract base of two frozen dataclasses,
`ApplicationPrincipal` and `UserPrincipal`; the latter adds `party_id`, `session_id`, `roles: frozenset[Role]`
and `auth_methods: frozenset[AuthMethod]` (`StrEnum`, `PASSWORD = "pwd"`). `principal_type` (`PrincipalType`) is
a class constant and `subject` is derived. `create_access_token(settings, principal)` writes a principal and
`validate_access_token` reads it back, both through one pydantic declaration of the claims - a discriminated union
on `principal_type`. `create_application_access_token` stays as the application token's shorthand. A person's
token without `party_id`, `sid` or `amr`, with an unknown role or method, or whose `sub` is not the canonical
`user:<principal_id>`, is invalid.

The security scheme in [`security.py`](../../app/core/auth/security.py) declares a `password` flow next to
`clientCredentials`, so Swagger UI's "Authorize" dialog logs a person in. The class is renamed to `OAuth2Bearer`
with an explicit `scheme_name="OAuth2"` - a one-time, contract-wide rename of the key in
`components.securitySchemes`, made while no consumer is live.

`GET /auth/me` gains `user_id`, `party_id` and `roles` (`null` / empty for an application principal) and still
answers from the token alone. `bind_request_log_context` receives `principal_type`, `user_id` and `party_id` for a
person, never the session id.

## Reach

`require_access(scope)` in [`dependencies.py`](../../app/core/auth/dependencies.py) is the reach-aware sibling of
`require_scopes`, used as a parameter: `Annotated[Access, require_access(Scope.CRM_READ)]`.

| Token carries  | Principal                 | Result                                                  |
| -------------- | ------------------------- | ------------------------------------------------------- |
| `crm:read`     | any                       | `Access.all()` - no query                               |
| `crm:read:own` | a `UserPrincipal`         | `Access.of(reach)` - one query on `core.party_relation` |
| `crm:read:own` | an `ApplicationPrincipal` | `403`                                                   |
| neither        | any                       | `403`                                                   |

`app/core/auth/reach.py` holds:

- `ReachBasis` (`StrEnum`): `SELF`, `GUARDIAN` (the bot arc adds `TUTOR`).
- `Access` (frozen dataclass): `Access.all()` or `Access.of(reach: Mapping[UUID, ReachBasis])`;
  `party_ids: frozenset[UUID] | None` (`None` = all), `allows(party_id) -> bool`,
  `basis(party_id) -> ReachBasis | None`. It knows nothing about HTTP or CRM errors.
- `resolve_reach(session, party_id) -> dict[UUID, ReachBasis]`: the own party as `SELF`, every `to_party` of an
  outgoing relation in `DELEGATION_RELATION_TYPES` as `GUARDIAN`.
- `DELEGATION_RELATION_TYPES`.

Calling `require_access` with a scope that has no entry in `OWN_VARIANT` raises at import time. In the OpenAPI
output a reach-aware operation lists **two alternative** security requirements,
`[{"OAuth2": ["crm:read"]}, {"OAuth2": ["crm:read:own"]}]`, and its derived `403` reads "Missing required scope:
crm:read or crm:read:own"; `customize_openapi` in [`openapi.py`](../../app/api/v1/common/openapi.py) derives both.

In the CRM API, two aliases in [`params.py`](../../app/api/v1/crm/params.py): `CrmReadAccess`
(`Annotated[Access, require_access(Scope.CRM_READ)]`) and `VisibleParty`, a path alias that depends on it and raises
`PartyNotFoundError` when `access.allows(party_id)` is false. `list_parties` in
[`parties.py`](../../app/services/crm/parties.py) gains a keyword-only `party_ids: frozenset[UUID] | None = None`
filter. `GET /crm/parties` and `GET /crm/parties/{party_id}` switch to `require_access(Scope.CRM_READ)`; no other
route changes.

## Route map

All under `/api/v1/auth`, tag `auth`. JSON routes follow the API conventions: `ApiModel` schemas, scopes on the
decorator, `error_responses(...)`, `Page[T]` for the list, operation IDs `auth_<function>`.

```
# Tokens - form-encoded, OAuth2 errors
POST   /token                              client auth               3 grants

# Accounts - admin surface                 auth:users:manage
POST   /users                              create                    201  UserAccountDetail
GET    /users                              list, filter              200  Page[UserAccountListItem]
GET    /users/{user_id}                                              200  UserAccountDetail
PATCH  /users/{user_id}                    email, status             200  UserAccountDetail
PUT    /users/{user_id}/roles/{role}       idempotent                200  UserAccountDetail
DELETE /users/{user_id}/roles/{role}                                 204
POST   /users/{user_id}/invitation                                   201  ActionTokenResponse
POST   /users/{user_id}/password-reset                               201  ActionTokenResponse
DELETE /users/{user_id}/sessions           revoke all                204

# On a person's behalf - application principal with auth:users:login (application mode)
POST   /password/redeem                    {token, new_password}     204
POST   /revoke                             {refresh_token}           204

# Client grants                            auth:clients:manage
POST   /clients/{client_id}/scopes         {scopes, mode}            200  ApplicationClientResponse
DELETE /clients/{client_id}/scopes/{mode}/{scope_key}                204

# Caller
GET    /me                                 any token                 200  MeResponse
```

- **`GET /users`** takes a `PageParams` subclass with `status`, `party_id` and `email` (exact, normalized) filters,
  ordered by `created_at` then `id` (the e-mail is optional).
- **`UserAccountDetail`** carries `id`, `party_id`, `email`, `status`, `has_password`, `roles` (stored **and**
  derived, sorted), `locked_until`, `last_login_at`, `created_at`, `updated_at`. No hash, no counter.
- **`ActionTokenResponse`** carries `token` and `expires_at` - the only time the plaintext exists outside the hash.
- The redeem and revoke routes use `Security(require_application, scopes=[Scope.AUTH_USERS_LOGIN])`: a person's
  token is `403` whatever it carries.

## Error catalog

Taxonomy errors (`app/core/auth/services/errors.py`, mapped by `STATUS_BY_ERROR`):

| Class                           | Status | `code`                        | Raised when                                                                                                                |
| ------------------------------- | ------ | ----------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `UserAccountNotFoundError`      | 404    | `user_account_not_found`      | Unknown `user_id`.                                                                                                         |
| `UserAccountAlreadyExistsError` | 409    | `user_account_already_exists` | The party already has an account.                                                                                          |
| `UserEmailAlreadyInUseError`    | 409    | `user_email_already_in_use`   | Another account uses the e-mail (create, patch).                                                                           |
| `UserAccountStateError`         | 409    | `user_account_state`          | Invitation without e-mail or with a password; reset without a password; removing the e-mail of an account with a password. |
| `AccountPartyNotFoundError`     | 404    | `account_party_not_found`     | `party_id` of a new account does not exist.                                                                                |
| `AccountPartyNotAPersonError`   | 422    | `account_party_not_a_person`  | The party is a company.                                                                                                    |
| `UserRoleNotFoundError`         | 404    | `user_role_not_found`         | Removing a stored role the account does not hold.                                                                          |
| `InvalidActionTokenError`       | 422    | `invalid_action_token`        | Unknown, used, invalidated or expired token - one error for all four.                                                      |
| `WeakPasswordError`             | 422    | `weak_password`               | Password shorter than 8 or longer than 128 characters.                                                                     |

`role` in the path and in `roles`, and `mode` in the path and body, are validated by their enums; an unknown or
derived role, or an unknown mode, is a request validation error.

OAuth2 errors (`ApiError` constants in [`errors.py`](../../app/api/v1/auth/errors.py)), new: `INVALID_GRANT` (400,
`invalid_grant`, "Invalid credentials or refresh token") and `UNAUTHORIZED_CLIENT` (400, `unauthorized_client`,
"Client may not use this grant"). `INVALID_REQUEST` is reworded to cover any missing grant parameter.
`INVALID_SCOPE` also answers a client-only scope granted in `delegated` mode.

The catalog is closed: a requirement that seems to need another class or `code` is a reason to stop and report.

## Security rules

- **Passwords:** 8 to 128 characters, nothing else. Checked in the service (`WeakPasswordError`), not in the schema.
- **No enumeration:** `invalid_grant` and `invalid_action_token` have one body each; the dummy-hash verification
  keeps "unknown account" as slow as "wrong password".
- **Lockout is per account.** SkillForge sees only the portal's address; limiting by client IP is the job of the
  portal and the reverse proxy.
- **Plaintext exists once.** Refresh and action tokens appear in the response that creates them and are stored as
  digests only; never in logs, audit details or error messages.
- **Denials are returned** (decision O). `create_token` is the single place that turns a denial into an
  `ApiError` response, for all three grants.
- **Client-only scopes never reach a person's token:** they are grantable in `application` mode only and are in no
  role's scopes; `test_no_role_carries_client_only_scopes` pins the second half.
- **Access tokens are not revocable.** Every revocation acts on the session; the access token dies within 15
  minutes (decision L).

## BFF contract

What SkillForge expects from the portal's backend-for-frontend; implemented in the skillsite arc.

- The browser talks to the portal only. SkillForge gets no CORS configuration and sets no cookies.
- The client secret and both tokens stay on the server; the browser holds an `HttpOnly`, `Secure`, `SameSite=Lax`
  session cookie.
- Refresh is **single-flight** per session. The 10-second grace only absorbs an accidental race.
- The portal client holds `auth:users:login` as an `application` grant and, as `delegated` grants, `account:self`
  and the unqualified scopes its users may hold at most (`crm:read`, `crm:write`, ...). The ceiling is not what a
  student gets.
- For a narrowed view the portal refreshes with `scope=...` instead of filtering on its side.

## Operating without a portal

1. `just bootstrap-admin --party-id <uuid> --email <address>` prints the invitation token.
2. Through the `/auth/clients` routes, create an operator client and grant it `auth:users:login` as `application`
   and, as `delegated`, `account:self` plus the scopes an admin needs.
3. Authorize as that client (`clientCredentials`) and redeem the invitation with `POST /auth/password/redeem`.
4. Authorize again with the `password` flow: the token is now the admin's.

## Designed for the bot arc

Not built here; recorded so that this arc's shapes take it without change.

- **Token exchange.** An extension grant (RFC 6749, section 4.5) on `POST /auth/token`: the bot authenticates as a
  client holding `auth:users:exchange` (an `application`, client-only scope) and names the Discord user who sent a
  command, optionally with the scopes the command needs. SkillForge follows active `ext.discord_account` -> party
  -> account -> `active`, computes the scopes from the bot's `delegated` grants and the person's roles, and issues a
  token with `amr: ["discord"]` and no refresh token; `sid` becomes optional for such tokens. Any broken link is
  the same `invalid_grant`; the bot then offers only what needs no identity.
- **Tutor reach.** `ReachBasis.TUTOR` via `TUTOR_OF`, with representations that differ by basis (a tutor does not
  see who pays for a student).
- **Accounts by tutors.** A reach-limited scope lets a tutor create accounts for people in their reach, without
  stored roles.
- **The bot's grant engine retires.** What `PermissionGrant` decides today becomes scopes and reach of the person.
- **Change signals.** The bot pulls changes (`updated_since`, plus a periodic comparison for deletions) and runs its
  own workflows in its own database; a new ADR supersedes ADRs 0003 and 0004.

## Requirements

### Must-have (P0)

**Standing criteria - they hold for every slice and are ticked with P0-8.**

- [ ] Nothing under `app/core/auth` imports `app.services` or `app.api` (it may read CRM _models_); nothing under
      `app/services/crm` or `app/api/v1/crm` imports the bot domain (ADR 0007).
- [ ] No plaintext password, refresh token or action token is written to a log, an audit `detail` or an error
      `detail` (the lifecycle tests grep their captured log output).
- [ ] Every property of every new schema and every new path and query parameter has a description in
      `openapi.json`; the operation IDs of the auth routes match `^auth_[a-z_]+$`.
- [ ] `just check-all` is green; `openapi.json` is regenerated with `just openapi`, never edited.
- [ ] **SkillBot keeps working.** `client_credentials`, the claims of an application token and every `/bot` route
      behave exactly as before for an application principal: the existing tests of the token endpoint and the bot
      domain pass **unmodified**. SkillBot does not call the client routes, so their new grant shape does not
      reach it; other contract changes it can see are additive, plus the one-time scheme rename of P0-5.

**P0-1 - Spec and ADR.** _Docs only._

- [ ] This spec and ADR 0008 are on `main`; the decisions index lists 0008; issue #85 links the spec.

**P0-2 - Scope model.** _Pure code, no database._

- _Technique:_ the four new `Scope` members, `CLIENT_ONLY_SCOPES`, `OWN_VARIANT`, `expand`, `canonical`,
  `parse_scopes`, `format_scopes` in `scopes.py`; `app/core/auth/roles.py`; `resolve_token_scopes` as specified;
  `get_current_principal` expands before comparing; `DELEGATION_RELATION_TYPES` in `app/core/auth/reach.py`,
  imported by [`authz.py`](../../app/services/bot/authz.py).
- _Acceptance criteria:_
  - [ ] `expand({crm:read}) == {crm:read, crm:read:own}`; `canonical` is its inverse on every subset of `Scope`.
  - [ ] Given role scopes `{account:self, crm:read:own}`: delegated grants `{account:self, crm:read, crm:write}`
        yield `account:self crm:read:own`; `{crm:read, crm:write}` yield `crm:read:own`; `{bot:read}` yield
        `invalid_scope`.
  - [ ] A client granted `crm:read` may request `crm:read:own`; requesting an ungranted scope is `invalid_scope`.
  - [ ] A token carrying `crm:read:own` gets `403` from a route guarded with `require_scopes(Scope.CRM_READ)`; a
        token carrying `crm:read` passes a route that requires `crm:read:own`.
  - [ ] `test_no_role_carries_client_only_scopes`; every scope appears with its description in
        `components.securitySchemes`.
  - [ ] The bot's delegation check answers exactly as before (its tests unmodified).

**P0-3 - Data model.**

- _Technique:_ `GrantMode` and the `mode` column; the four account models exported from
  `app/core/db/models/auth/__init__.py`; **one** autogenerated revision; [`DATABASE_SCHEMA.md`](../DATABASE_SCHEMA.md).
- _Acceptance criteria:_
  - [ ] Upgrade and downgrade run clean against an empty and a seeded database; every existing grant comes out as
        `application`; the downgrade restores the old primary key and drops the four enum types
        (`grant_mode`, `user_account_status`, `user_account_role_name`, `user_action_token_purpose`).
  - [ ] One scope can be granted to one client in both modes.
  - [ ] An uppercase e-mail violates the check constraint; two accounts for one party, or two with the same
        e-mail, violate their unique constraints; two accounts without an e-mail do not.
  - [ ] A new account defaults to `active`.
  - [ ] Deleting a party through `delete_party` removes its account, roles, sessions and action tokens and leaves
        the audit log untouched.

**P0-4 - Grant modes.**

- _Technique:_ the client services and routes take the mode as specified in [Client grants](#client-grants);
  `issue_client_token` reads `application` grants only.
- _Acceptance criteria:_
  - [ ] Granting with `mode` in the body, revoking by `/{mode}/{scope_key}`, and the client detail listing
        `application_scopes` and `delegated_scopes` work as specified; the audit entries name the mode.
  - [ ] Granting `auth:users:login` as `delegated` answers `invalid_scope` and grants nothing of the request.
  - [ ] A client holding a scope only as `delegated` does not get it through `client_credentials`.
  - [ ] `just bootstrap-skillbot` and every existing caller of the grant services behave as before.

**P0-5 - Person tokens.**

- _Technique:_ `ApplicationPrincipal` / `UserPrincipal`, `AuthMethod`; claims as a pydantic discriminated union;
  `create_access_token` for both types; `MeResponse` extended; `OAuth2Bearer` with both flows and
  `scheme_name="OAuth2"`; the request log context.
- _Acceptance criteria:_
  - [ ] `components.securitySchemes` has exactly one key, `OAuth2`; apart from the key, the `security` requirement
        of every existing operation is unchanged (pinned against the list of operations at the rename).
  - [ ] A person's token round-trips into a `UserPrincipal` with `party_id`, `session_id`, `roles` and
        `auth_methods`; an application token round-trips exactly as before (claims asserted against a fixture).
  - [ ] A person's token missing `party_id`, `sid` or `amr`, with an unknown role or method, or whose `sub` is not
        `user:<principal_id>`, is a `401` in the error envelope.
  - [ ] `GET /auth/me` answers both principal types without a database session.
  - [ ] `require_application` rejects a person's token; the request log identifies the person behind a request.

**P0-6 - Accounts.**

- _Technique:_ `services/accounts.py` (get and lock an account row), `services/users.py` (create, load, list,
  update, stored roles), `services/action_tokens.py` (issue, redeem), `services/sessions.py`
  (`SessionRevokedReason`, revoke), `services/roles.py` (`derive_roles`); `inputs.py` (`LoginEmail`,
  `normalize_email`); `passwords.py` (policy, `dummy_password_hash`); `app/api/v1/auth/users.py`, the redeem route
  in `app/api/v1/auth/password.py`; `bootstrap_admin` with a `just bootstrap-admin` recipe.
- _Acceptance criteria:_
  - [ ] Creating an account for a person party answers `201` with an `active` account and no token, with or
        without an e-mail; the same party again is `user_account_already_exists`; a company is
        `account_party_not_a_person`; another account's e-mail is `user_email_already_in_use`;
        `Anna@Example.org` is stored as `anna@example.org`.
  - [ ] An invitation for an account without an e-mail, or with a password, is `user_account_state`; a reset for
        an account without a password is `user_account_state`; issuing invalidates earlier unused tokens of the
        same purpose, and two issues arriving together leave one live token.
  - [ ] Redeeming an invitation sets the password and leaves the status as it was; redeeming it again, an expired
        token and a replaced token all answer the same `invalid_action_token` body; two overlapping redeems of one
        token set one password.
  - [ ] Redeeming a reset revokes every session of the account; redeeming an invitation revokes none.
  - [ ] A password of 7 or 129 characters is `weak_password`; 8 and 128 are accepted.
  - [ ] Disabling revokes the sessions; enabling an account without a password succeeds; changing or removing the
        e-mail invalidates unused action tokens; removing the e-mail of an account with a password is
        `user_account_state`; a `PATCH` that changes nothing records nothing.
  - [ ] `roles` lists `student` for a party with a `Student` row, `guardian` for an outgoing `PAYS_FOR`, and
        `admin` + `tutor` for a tutor holding the stored role; `TUTOR_OF` does not make a tutor a guardian.
  - [ ] Every account route is `403` for a token without `auth:users:manage`; the redeem route is `403` for a
        person's token, whatever its scopes, and for a client without `auth:users:login`.
  - [ ] `just bootstrap-admin` is idempotent: run twice it keeps the account and issues a fresh invitation only
        while the account has no password; `--email` is validated by the API's rule.
  - [ ] The create-invite-redeem flow logs neither the token nor the password.

**P0-7 - Own data (reach).**

- _Technique:_ `reach.py` (`ReachBasis`, `Access`, `resolve_reach`); `require_access` in `dependencies.py`; the
  alternative security requirement and the 403 wording in `customize_openapi`; `CrmReadAccess` and `VisibleParty`
  in the CRM params; the `party_ids` filter of `list_parties`; the two party read routes.
- _Acceptance criteria:_
  - [ ] A student reads their own party and gets `404 party_not_found` for any other - the same body an unknown
        UUID produces.
  - [ ] A mother with `PARENT_OF` to one child and `PAYS_FOR` to another reads both children and herself; the list
        returns exactly these three with `total == 3`; filters and paging apply within the reach.
  - [ ] `Access.basis` reports `self` for the own party and `guardian` for both children.
  - [ ] `TUTOR_OF` does not extend reach: a tutor does not read their student's party.
  - [ ] An application token with `crm:read` behaves exactly as before on both routes and issues no query on
        `core.party_relation` (asserted by counting statements).
  - [ ] A person's token with `crm:read:own` is `403` on every other CRM route (a test walks `app.routes`); an
        application token with only `crm:read:own` is `403` on both routes.
  - [ ] Both operations list the two alternative security requirements; the CRM endpoints still pass
        `test_the_crm_endpoints_carry_no_boilerplate`.
  - [ ] `_allowed_target_parties` in `authz.py` and `resolve_reach` return the same set for the same party.
  - [ ] The conventions in `CLAUDE.md` name `require_access`.

**P0-8 - Login.**

- _Technique:_ `issue_user_token` and `refresh_user_token` in
  [`services/tokens.py`](../../app/core/auth/services/tokens.py); sessions opened, rotated and revoked in
  `services/sessions.py`; `ClientTokenForm` becomes `TokenForm`; `create_token` dispatches on the grant;
  `POST /auth/revoke`; `verify_and_update` in `secrets.py`.
- _Acceptance criteria:_
  - [ ] The lifecycle test (database): create -> invite -> redeem -> `password` -> `/auth/me` -> `refresh_token` ->
        `revoke` -> the revoked refresh token is `invalid_grant`.
  - [ ] Unknown e-mail, wrong password, a `disabled` account, an account without a password and a locked account
        answer the identical `invalid_grant` body; a client without `auth:users:login` is `unauthorized_client`.
  - [ ] After five wrong passwords the correct one is refused until `locked_until`; **the counter is persisted
        although the request was denied**.
  - [ ] Presenting a rotated-out refresh token after the grace revokes the session and writes
        `session.reuse_detected`; within the grace it only fails.
  - [ ] A refresh token is refused for another client than the one that opened the session.
  - [ ] A person's scopes come from the client's `delegated` grants: an admin logging in through a client whose
        only `delegated` grant is `account:self` gets exactly `account:self`.
  - [ ] Removing the `admin` role and refreshing yields a token without the admin scopes; refreshing with
        `scope=account:self crm:read:own` as an admin yields exactly that; requesting more than the session's
        `scope` is `invalid_scope`.
  - [ ] Both the login's and the refresh's token carry `amr: ["pwd"]`.
  - [ ] Swagger UI's "Authorize" dialog offers the `password` flow (asserted over `app.openapi()`).
  - [ ] [`ARCHITECTURE.md`](../ARCHITECTURE.md) (Auth section) and the "Where we are" table of the
        [project sketch](../PROJECT.md) describe the result; the standing criteria are ticked; the status line
        reads Implemented; the PR carries `Closes #85`.

### Nice-to-have (P1)

- **P1-1 - Mail delivery.** A `Mailer` port with an SMTP implementation and `MAIL__*` settings; invitations and
  resets are sent instead of returned; a public, client-guarded "forgot password" route that always answers `202`;
  `password_reset_expire_hours` drops to 1.
- **P1-2 - Self-service.** `require_user`; `POST /auth/me/password` (current + new password, demands `amr`
  `pwd`, revokes the other sessions); `GET /auth/me/sessions`, `DELETE /auth/me/sessions/{session_id}`. Guarded by
  `account:self`.
- **P1-3 - Housekeeping.** The reaper deletes sessions and action tokens that expired more than 30 days ago.
- **P1-4 - Reach-aware writes.** `crm:write:own` and the first routes that accept it.
- **P1-5 - Remaining CRM reads** become reach-aware where a restricted view makes sense.

### Future considerations (P2)

- The bot arc items of [Designed for the bot arc](#designed-for-the-bot-arc).
- **MFA** for accounts holding a stored role.
- **Asymmetric signing + JWKS** once a second service validates SkillForge's tokens.

## Timeline / phasing

One PR per requirement, each branched from `main` - no stacked PRs. A wave starts when every PR of the previous
wave is merged.

| Wave | Slices                                       | Needs                                       |
| ---- | -------------------------------------------- | ------------------------------------------- |
| 0    | **P0-1** spec and ADR                        | nothing                                     |
| 1    | **P0-2** scope model, **P0-3** data model    | nothing; disjoint files                     |
| 2    | **P0-4** grant modes, **P0-5** person tokens | P0-4: P0-2 and P0-3. P0-5: P0-2             |
| 3    | **P0-6** accounts, **P0-7** own data         | P0-6: P0-2, P0-3, P0-5. P0-7: P0-2 and P0-5 |
| 4    | **P0-8** login                               | everything above                            |

- All schema changes live in P0-3's single revision, so no two slices add competing Alembic heads.
- P0-7 does not wait for the login: its tests mint person tokens with `create_access_token` and a
  `UserPrincipal`, the way the CRM tests mint application tokens today.
- **`openapi.json` is never merged by hand.** Within a wave the PR that merges second rebases onto `main`, takes
  either side of the file, reruns `just openapi` and `just check-all`.

## Rules for implementing agents

- The rules of [`api-conventions.md`](api-conventions.md) and [`crm-api.md`](crm-api.md) apply unchanged (English
  only, symbol references in docs, never hand-edit `openapi.json`, `just check` green before every commit,
  conventional commits).
- **Code from the withdrawn first version** is on `archive/user-auth-v1`. Take it deliberately, file by file
  (`git show archive/user-auth-v1:<path>`), and adapt it: that version had an `invited` status, a required e-mail,
  one list of client grants and no `amr`. Never cherry-pick its commits.
- **Never branch on a role** to authorize. Roles produce scopes at issuance; guards read scopes.
- **Never filter by hand.** A route that serves restricted people takes an `Access` from `require_access`; a route
  that does not keeps `require_scopes`. Do not add a `:own` scope to `OWN_VARIANT` without the routes that honor it.
- **Never raise after writing a denial.** Return the `ApiError` response (decision O).
- **Never log or persist a plaintext secret,** and never put an e-mail address into an audit `detail`.
- The error catalog is closed. If a requirement seems to need a new class or `code`, stop and report.
- Do not touch the `bot` schema, the grant engine or `delete_party`.
