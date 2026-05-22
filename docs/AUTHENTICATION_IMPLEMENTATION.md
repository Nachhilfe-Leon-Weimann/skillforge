# Authentication Implementation Plan

This document describes how to implement the application-auth focused design
from `AUTHENTICATION.md` in FastAPI.

The implementation should stay small and explicit:

- routers are thin HTTP adapters
- services contain use-case logic
- dependencies enforce authentication and scopes
- SQLAlchemy models only describe persistence
- token and secret handling are isolated modules

## Implementation Decisions

These decisions are fixed for the first implementation slice.

### Scope

- Implement application authentication first.
- Do not implement human login, sessions, invitations, MFA, or browser cookies
  in this slice.
- Keep the code user-auth compatible by using the shared `Principal` runtime
  model from the beginning.

### Public API Shape

- Use `/api/v1` as the versioned API prefix.
- Keep `/health` outside `/api/v1` and unauthenticated.
- Use `/api/v1/auth/token` for OAuth2 Client Credentials token issuance.
- Use `/api/v1/auth/clients/...` for application client management.
- Use `/api/v1/bot/...` for the SkillBot API surface.
- Do not put application client IDs in functional routes. `skillbot` is an
  authenticated client, not part of the route path.

### Authorization Model

- Use API-surface scopes, not database/table scopes.
- Start with coarse scopes:
  - `bot:read`
  - `bot:write`
  - `users:read`
  - `users:write`
  - `auth:clients:manage`
- `/api/v1/bot/...` requires `bot:read` or `bot:write`.
- `/api/v1/auth/clients/...` requires `auth:clients:manage`.
- Internal reads/writes to `core.party`, `core.student`, `core.tutor`, or
  `ext.*` inside a `/bot/...` endpoint do not require extra client scopes.
- Object-level/domain authorization stays in services and is not encoded as
  scope names.

### Token Format

- Use signed JWT access tokens.
- Use `HS256` for v1 because it is simple and fits a single central backend.
- Keep the signing key in settings/env as a secret.
- Default token TTL: 15 minutes.
- Do not issue refresh tokens for application clients.
- Include a `jti` claim for audit/debug correlation, but do not persist every
  issued token as a DB row.
- Use `scope` as a space-separated string for OAuth2 compatibility.
- Use `sub = "app:<client_id>"`.
- Include `principal_type = "application"` and `azp = "<client_id>"`.

### Secret Handling

- Generate high-entropy random client secrets server-side.
- Prefix generated secrets with `sf_live_` for readability.
- Store only secret hashes in the database.
- Return plaintext secrets exactly once when they are created.
- Allow multiple active secrets per client to support rotation.
- A secret is usable only if `revoked_at IS NULL` and either `expires_at IS NULL`
  or `expires_at > now()`.
- Update `last_used_at` after successful authentication.

### Database

- Add an `auth` schema.
- Use SQLAlchemy models under `app/core/db/models/auth`.
- Keep auth persistence separate from `core`, `geo`, and `ext`.
- Add `auth` to test schema setup.
- Do not introduce Alembic as part of this slice because this repository does
  not currently have migration infrastructure. Add migrations before the first
  production schema rollout.

### Audit

- Persist security-relevant auth events in `auth.auth_audit_log`.
- Do not persist every normal API request as an audit row.
- Audit both successful and denied token requests.
- Audit client, secret, and scope management changes.
- Later delegated Discord admin commands must record both principals:
  application principal and actor/user principal.

### Bootstrap

- Seed default scopes in code or a small CLI/script.
- For local development, bootstrap the initial `skillbot` client with:
  - `client_id = "skillbot"`
  - scopes `bot:read`, `bot:write`
- Run `just bootstrap-skillbot` against the configured database.
- Print the generated plaintext secret once during bootstrap.
- Do not hard-code the SkillBot secret in source files.

### Error Semantics

- Missing Bearer token: `401`.
- Invalid or expired Bearer token: `401`.
- Valid token with missing scope: `403`.
- Invalid client credentials at `/auth/token`: `401`.
- Requested unknown or ungranted scope at `/auth/token`: `400`.
- Disabled client at `/auth/token`: `401`.

## Package Structure

Recommended structure:

```text
app/
  main.py

  api/
    __init__.py
    v1/
      __init__.py
      router.py
      auth.py
      bot.py

  core/
    config.py
    auth/
      __init__.py
      audit.py
      config.py
      constants.py
      dependencies.py
      principal.py
      schemas.py
      secrets.py
      service.py
      tokens.py
    db/
      models/
        auth/
          __init__.py
          application_client.py
          application_client_scope_grant.py
          application_client_secret.py
          auth_audit_log.py
          base.py
          permission_scope.py
```

Keep `app/core/auth` for auth infrastructure because auth is cross-cutting
platform behavior. Keep public HTTP routes under `app/api/v1`.

## Components

### SQLAlchemy Models

Auth models live under `app/core/db/models/auth`.

They should mirror the tables from `AUTHENTICATION.md`:

- `ApplicationClient`
- `ApplicationClientSecret`
- `PermissionScope`
- `ApplicationClientScopeGrant`
- `AuthAuditLog`

Model rules:

- no JWT generation in models
- no password or secret verification in models
- use relationships for convenient reads
- enforce DB uniqueness for `client_id` and scope grants
- store only secret hashes, never plaintext secrets

Add an `AuthBase` like the existing `CoreBase` and `ExtBase`:

```python
from ..base import Base


class AuthBase(Base):
    __abstract__ = True
    __table_args__ = {"schema": "auth"}
```

Tests that create schemas must include `auth` in addition to `core`, `geo`,
and `ext`.

### Settings

Add `AuthSettings` in `app/core/auth/config.py` and include it in the global
`Settings` object.

Settings should include:

- `issuer`, default `skillforge`
- `audience`, default `skillforge-api`
- `secret_key` or future signing key
- `algorithm`, default `HS256` for v1
- `access_token_expire_minutes`, default `15`

Use explicit settings rather than hard-coded values in token code.

### Constants

Define scope names once in `app/core/auth/constants.py`.

Example:

```python
SCOPE_BOT_READ = "bot:read"
SCOPE_BOT_WRITE = "bot:write"
SCOPE_USERS_READ = "users:read"
SCOPE_USERS_WRITE = "users:write"
SCOPE_AUTH_CLIENTS_MANAGE = "auth:clients:manage"

DEFAULT_SCOPES = {
    SCOPE_BOT_READ: "Read bot API surface.",
    SCOPE_BOT_WRITE: "Write bot API surface.",
    SCOPE_USERS_READ: "Read users API surface.",
    SCOPE_USERS_WRITE: "Write users API surface.",
    SCOPE_AUTH_CLIENTS_MANAGE: "Manage application clients.",
}
```

Route handlers should import constants instead of repeating scope strings.

### Principal

`Principal` is the runtime identity used by route handlers and services.

Use a small immutable type:

```python
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Principal:
    principal_type: str
    principal_id: UUID
    subject: str
    scopes: frozenset[str]
    client_id: str | None = None
```

In v1, `principal_type` is always `"application"` for authenticated API calls.
Later, user auth can add `"user"` without changing route-level scope checks.

### Secret Handling

`app/core/auth/secrets.py` owns application client secrets.

Responsibilities:

- generate cryptographically random plaintext secrets
- prefix secrets for readability, for example `sf_live_...`
- hash secrets before storage
- verify plaintext secret against stored hash
- never return stored hashes through API responses

Use a password-hashing library suitable for secret verification. Argon2 via
`pwdlib` or `argon2-cffi` is a good default.

The plaintext secret is returned exactly once when created.

### Token Handling

`app/core/auth/tokens.py` owns JWT creation and validation.

Responsibilities:

- create application access tokens
- validate signature, algorithm, issuer, audience, expiry
- parse and validate required claims
- convert valid claims into a `Principal`

Application token claims:

```json
{
  "iss": "skillforge",
  "aud": "skillforge-api",
  "sub": "app:skillbot",
  "principal_type": "application",
  "azp": "skillbot",
  "scope": "bot:read bot:write",
  "iat": 1710000000,
  "exp": 1710000900,
  "jti": "..."
}
```

Validation should reject:

- expired tokens
- wrong issuer
- wrong audience
- missing `sub`
- unsupported `principal_type`
- missing or invalid `scope`

### Service Layer

`app/core/auth/service.py` owns use cases.

Recommended service methods:

- `issue_client_token(client_id, client_secret, requested_scopes)`
- `create_application_client(client_id, name, description)`
- `create_client_secret(client_id, label, expires_at)`
- `revoke_client_secret(client_id, secret_id)`
- `grant_client_scopes(client_id, scopes)`
- `revoke_client_scopes(client_id, scopes)`
- `seed_default_scopes()`

Service rules:

- verify client exists and is active
- verify at least one active, non-expired, non-revoked secret matches
- requested scopes must be known, active, and granted to the client
- no requested scopes means issue all granted active scopes
- update `last_used_at` on the matching secret after successful auth
- write audit events for token issuance and failures

Routers should call services and not query auth tables directly except for
simple read endpoints if that remains clean.

### Audit

`app/core/auth/audit.py` owns DB audit event creation.

Audit events should be written for security-relevant auth events:

- `token.issued`
- `token.denied`
- `application_client.created`
- `application_client.disabled`
- `client_secret.created`
- `client_secret.revoked`
- `scope_grant.added`
- `scope_grant.removed`
- later: delegated actor admin commands

Do not write every normal API request to the DB audit log. Use normal
application logging for request timing, exceptions, and debugging.

### FastAPI Dependencies

`app/core/auth/dependencies.py` owns reusable dependencies.

Core dependencies:

```python
async def get_current_principal(...) -> Principal:
    ...


def require_scopes(*required_scopes: str):
    async def dependency(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> Principal:
        missing = set(required_scopes) - principal.scopes
        if missing:
            raise HTTPException(status_code=403, detail="Not enough permissions")
        return principal

    return dependency


async def require_application(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> Principal:
    if principal.principal_type != "application":
        raise HTTPException(status_code=403, detail="Application principal required")
    return principal
```

Behavior:

- missing token: `401`
- invalid token: `401`
- valid token with missing scope: `403`
- valid user token on app-only endpoint later: `403`

Prefer dependencies over manual permission checks inside route handlers.

## Routes

### API Router

Create `app/api/v1/router.py`:

```python
from fastapi import APIRouter

from . import auth, bot

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(bot.router)
```

Register it in `app/main.py`:

```python
app.include_router(v1_router)
```

Keep `/health` outside `/api/v1` and unauthenticated.

### Auth Routes

`app/api/v1/auth.py`:

```text
POST /api/v1/auth/token
```

Uses OAuth2 Client Credentials form data and returns:

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 900,
  "scope": "bot:read bot:write"
}
```

Management endpoints can come after token issuance works:

```text
GET    /api/v1/auth/clients
POST   /api/v1/auth/clients
GET    /api/v1/auth/clients/{client_id}
PATCH  /api/v1/auth/clients/{client_id}
POST   /api/v1/auth/clients/{client_id}/secrets
DELETE /api/v1/auth/clients/{client_id}/secrets/{secret_id}
POST   /api/v1/auth/clients/{client_id}/scopes
DELETE /api/v1/auth/clients/{client_id}/scopes/{scope_key}
```

All client management endpoints require `auth:clients:manage`.

For initial local development, a seed script or CLI can create `skillbot`
before management endpoints exist.

### Bot Routes

`app/api/v1/bot.py` owns the public SkillBot API surface:

```text
GET  /api/v1/bot/...
POST /api/v1/bot/...
```

Rules:

- read endpoints require `bot:read`
- write endpoints require `bot:write`
- route handlers may call services that read/write `core` or `ext` data
- SkillBot still does not need `users:*`, `parties:*`, or `students:*` scopes
  for internal work performed by `/bot/...` endpoints

## Request Flow: Client Credentials

1. SkillBot sends `POST /api/v1/auth/token` with `client_id`,
   `client_secret`, and requested scopes.
2. Auth router parses form data and calls `AuthService.issue_client_token`.
3. Service loads active client and active secrets.
4. Service verifies the secret hash.
5. Service resolves requested scopes against active DB grants.
6. Service writes `token.issued` or `token.denied` audit event.
7. Service asks `tokens.py` to mint a JWT.
8. Router returns the token response.
9. SkillBot calls `/api/v1/bot/...` with `Authorization: Bearer <token>`.
10. Route dependency validates token and checks `bot:read` or `bot:write`.

## Pythonic Implementation Guidelines

- Use `Annotated[...]` for FastAPI dependencies.
- Keep functions small and named after use cases.
- Prefer dataclasses for internal immutable runtime objects such as
  `Principal`.
- Prefer Pydantic models for HTTP request/response schemas.
- Do not pass raw dict claims around after token validation; convert them into
  typed data.
- Keep scope strings centralized in constants.
- Keep side effects obvious: DB writes happen in services, not token helpers.
- Return domain-specific results from services; let routers translate them to
  HTTP responses.
- Do not hide database sessions in global state.
- Do not couple route names to client IDs. `/api/v1/bot/...` is the API
  surface; `skillbot` is only the authenticated client.

## Testing Strategy

### Unit Tests

Secret handling:

- generated secrets are unique
- stored value is a hash, not plaintext
- correct secret verifies
- wrong secret fails

Token handling:

- valid claims produce a `Principal`
- expired token is rejected
- wrong issuer is rejected
- wrong audience is rejected
- unsupported principal type is rejected
- missing scope claim is rejected

Scope checks:

- required scope succeeds
- missing scope returns `403`
- missing token returns `401`
- invalid token returns `401`

### DB Tests

- `auth` schema tables create successfully
- `client_id` is unique
- scope grant uniqueness is enforced
- revoked secrets stay in DB
- audit rows can be written for success and failure

### API Tests

- `POST /api/v1/auth/token` succeeds for active client and valid secret
- token request rejects invalid secret
- token request rejects disabled client
- token request rejects revoked or expired secret
- token request rejects unknown or ungranted scope
- token request with no scope returns all granted active scopes
- `bot:write` token can call write `/api/v1/bot/...`
- `bot:write` token cannot call `/api/v1/auth/clients/...`

## Implementation Order

1. Add dependencies to `pyproject.toml` for JWT and secret hashing.
2. Add `AuthSettings` and wire it into `Settings`.
3. Add auth SQLAlchemy models and DB tests.
4. Add constants, schemas, `Principal`, secret helpers, and token helpers.
5. Add `AuthService.issue_client_token`.
6. Add `/api/v1/auth/token`.
7. Add auth dependencies and protect one example `/api/v1/bot/...` route.
8. Add local bootstrap for the `skillbot` client.
9. Add management endpoints for clients, secrets, and scope grants.
10. Add delegated actor support later for Discord admin commands.
