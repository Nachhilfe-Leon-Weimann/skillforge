# Application Auth & Permissions Plan

## Summary

SkillForge will first provide application authentication for internal Skill
Platform services such as SkillBot and the Skillsite backend. Human user
authentication is intentionally out of scope for v1, but the authorization
layer should already be shaped so user principals can be added later without
rewriting endpoint permission checks.

The v1 design uses OAuth2 Client Credentials for confidential application
clients and database-managed API scopes.

## Authorization Layers

The auth design separates three concerns:

1. Authentication: who is calling SkillForge?
   Example: `principal_type = "application"` and `client_id = "skillbot"`.
2. API authorization: which public API surface may this principal use?
   Example: `bot:write` allows write operations on `/bot/...`, but not on
   `/users/...`.
3. Domain policy: what may happen inside the endpoint for a concrete object or
   workflow?
   Example: `/bot/students/activate` may internally read or update party and
   student data, but only through SkillForge service rules.

Scopes control the second layer only. They should not mirror internal database
tables or every domain entity touched by an endpoint.

## Permission Model

Every authenticated caller is represented as a `Principal`.

In v1, only application principals are implemented:

- `principal_type = "application"`
- `principal_id = auth.application_client.id`
- `scopes = [...]`

Later, user authentication can add user principals without changing the
permission checks used by route handlers:

- `principal_type = "user"`
- `principal_id = auth.user_account.id`
- `roles = ["admin", "tutor", "student"]`
- effective scopes derived from roles and explicit grants

Applications must not receive human roles such as `admin`, `tutor`, or
`student`. Those roles belong to users. Applications receive API scopes that
describe which API surface they may use.

Scopes are route/API permissions, not direct permissions on database tables or
domain objects. A scope should answer: "May this principal call this group of
endpoints in this mode?"

Scope naming convention:

- use `<api_namespace>:<operation>`
- `api_namespace` maps to a route prefix or public API surface
- `operation` starts coarse, usually `read`, `write`, or `manage`
- avoid modeling internal data access as a separate scope unless it is exposed
  as its own API surface

Good scope names are API-boundary names:

- `bot:read`
- `bot:write`
- `users:read`
- `users:write`
- `auth:clients:manage`
- later examples: `admin:read`, `admin:write`, `billing:read`,
  `billing:write`, `integrations:manage`

Avoid data-model scopes for internal implementation details:

- avoid `parties:read` just because `/bot/...` internally loads `core.party`
- avoid `students:write` just because `/bot/...` activates a student workflow
- add such scopes only if `/parties/...` or `/students/...` become public API
  surfaces that clients may call directly

Initial v1 scopes:

- `bot:read`
- `bot:write`
- `users:read`
- `users:write`
- `auth:clients:manage`

Expected initial grants:

- SkillBot: `bot:read`, `bot:write`
- SkillBot should not receive `users:read` or `users:write` just because a
  `/bot/...` handler internally resolves a party, student, or tutor.
- Skillsite backend later: scopes for the route groups it exposes to the
  frontend, for example `users:read` or `users:write` if it calls `/users/...`.

Internal data access is an implementation detail of the endpoint. For example,
`POST /bot/students/activate` can read or update central party/student data
inside SkillForge while still requiring only `bot:write` from SkillBot.

Object-level authorization is separate from scopes. A scope answers whether a
principal may call an API capability at all. Domain policies answer whether the
principal may access a specific object.

Example: a later tutor user may have `users:read`, but a policy check still
decides whether this tutor may read this specific user's student-related data.

## Data Model

Add a new `auth` schema.

```sql
-- auth.application_client
id UUID PRIMARY KEY DEFAULT uuid_generate_v4()
client_id TEXT NOT NULL UNIQUE
name TEXT NOT NULL
description TEXT NULL
status auth.application_client_status NOT NULL DEFAULT 'active'
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- auth.application_client_secret
id UUID PRIMARY KEY DEFAULT uuid_generate_v4()
application_client_id UUID NOT NULL REFERENCES auth.application_client(id) ON DELETE CASCADE
secret_hash TEXT NOT NULL
label TEXT NULL
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
expires_at TIMESTAMPTZ NULL
last_used_at TIMESTAMPTZ NULL
revoked_at TIMESTAMPTZ NULL
INDEX (application_client_id)

-- auth.permission_scope
key TEXT PRIMARY KEY
description TEXT NOT NULL
active BOOLEAN NOT NULL DEFAULT true
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- auth.application_client_scope_grant
application_client_id UUID NOT NULL REFERENCES auth.application_client(id) ON DELETE CASCADE
scope_key TEXT NOT NULL REFERENCES auth.permission_scope(key) ON DELETE RESTRICT
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
PRIMARY KEY (application_client_id, scope_key)

-- auth.auth_audit_log
id UUID PRIMARY KEY DEFAULT uuid_generate_v4()
principal_type TEXT NULL
principal_id TEXT NULL
event_type TEXT NOT NULL
success BOOLEAN NOT NULL
detail TEXT NULL
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
INDEX (created_at)
INDEX (principal_type, principal_id, created_at)
```

Recommended enum:

```sql
auth.application_client_status = ('active', 'disabled')
```

User-auth tables are not implemented in v1. Later additions should feed the
same runtime `Principal` model:

- `auth.user_account`
- `auth.user_role`
- `auth.role_scope_grant`
- optional explicit user grants or denies

## Token Contract

Applications authenticate with OAuth2 Client Credentials.

Endpoint:

```http
POST /auth/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
client_id=...
client_secret=...
scope=bot:read bot:write
```

Rules:

- `grant_type` must be `client_credentials`.
- Requested scopes must be known, active, and granted to the client.
- If no scope is requested, issue all scopes granted to the client.
- Disabled clients cannot receive tokens.
- Revoked or expired secrets cannot receive tokens.
- Application clients do not receive refresh tokens.
- Client secrets are stored only as hashes.
- Access tokens should be short-lived, for example 10-15 minutes.

JWT claims:

```json
{
  "iss": "skillforge",
  "aud": "skillforge-api",
  "sub": "app:<client_id>",
  "principal_type": "application",
  "azp": "<client_id>",
  "scope": "bot:read bot:write",
  "iat": 1710000000,
  "exp": 1710000900,
  "jti": "<token-id>"
}
```

## FastAPI Enforcement

Add central dependencies instead of checking tokens inside route handlers.

Core dependencies:

- `get_current_principal`
  - reads the Bearer token
  - validates signature, issuer, audience, expiry, and token shape
  - returns a typed `Principal`
- `require_application`
  - requires `principal_type == "application"`
  - useful for service-only endpoints
- `require_scopes([...])`
  - verifies that all required scopes are present
  - returns `401` for missing or invalid authentication
  - returns `403` for authenticated principals without enough scope

Endpoint examples:

- `GET /bot/...` requires `bot:read`
- `POST /bot/...` requires `bot:write`
- `GET /users/...` requires `users:read`
- `POST /users/...` requires `users:write`
- application client management endpoints require `auth:clients:manage`

This means a SkillBot token with `bot:read bot:write` may call `/bot/...`
endpoints only. If a `/bot/...` endpoint internally needs party or student data,
that is handled by SkillForge's service layer and does not require the token to
have a separate `parties:*` or `students:*` scope.

Use FastAPI's `Security(..., scopes=[...])` style where practical, so required
scopes appear in OpenAPI and stay close to the route definitions.

## Implementation Plan

1. Add `auth` SQLAlchemy base/model package following the existing `core`,
   `geo`, and `ext` schema pattern.
2. Add settings for auth:
   - JWT issuer
   - JWT audience
   - signing key or key pair
   - access token TTL
3. Add dependencies:
   - password/secret hashing library suitable for secret verification
   - JWT library with explicit allowed algorithms
4. Implement application client services:
   - create client
   - add/rotate/revoke secret
   - grant/revoke scopes
   - verify client credentials
5. Implement `/auth/token`.
6. Implement the shared `Principal` type and FastAPI auth dependencies.
7. Protect the first SkillBot-facing endpoints with `bot:read` and
   `bot:write`.
8. Add audit events for successful token issuance, failed authentication,
   secret rotation, revoked secrets, and scope changes.

## Test Plan

Client Credentials:

- valid client and secret returns a Bearer access token
- invalid secret is rejected
- disabled client is rejected
- revoked secret is rejected
- expired secret is rejected
- unknown requested scope is rejected
- ungranted requested scope is rejected
- no requested scope returns all granted scopes

JWT validation:

- expired token is rejected
- wrong issuer is rejected
- wrong audience is rejected
- missing scope is rejected on a protected endpoint
- application token with the required scope succeeds
- SkillBot token with `bot:read bot:write` is rejected from `/users/...`
- SkillBot token with `bot:read bot:write` succeeds on protected `/bot/...`
  endpoints

Database behavior:

- `client_id` is unique
- scope grants are unique per client
- revoked secrets are not deleted
- audit history remains after secret revocation

Future compatibility:

- endpoint permission checks depend on `Principal` and scopes, not directly on
  application client models
- adding `principal_type = "user"` later does not require changing existing
  route-level scope declarations

## Assumptions

- v1 does not implement human login, sessions, invitations, MFA, or browser
  cookies.
- SkillBot and the Skillsite backend are confidential clients and can safely
  store client secrets.
- Permissions are API route/API-surface scopes, not database table permissions
  and not business roles.
- Human roles will be added later and mapped to effective scopes.
- SkillForge is the central backend for Skill Platform services, not a public
  multi-tenant identity provider.
