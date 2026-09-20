# ADR 0008 - Forge authenticates users itself; one scope model with a reach qualifier

Status: Accepted, 2026-09

## Context

Authentication is machine-to-machine only. `POST /auth/token` knows the `client_credentials` grant,
`validate_access_token` in [`tokens.py`](../../app/core/auth/tokens.py) rejects every
`principal_type` other than `application`, and scopes such as `crm:read` mean _every record_. That
fits the one consumer that exists, SkillBot.

The customer portal on skillsite will be the second consumer
([ADR 0007](0007-crm-system-of-record.md)), and its callers are humans: students, their parents and
payers, tutors, admins. Two things are missing:

- **A login subject.** Forge knows people as a `Party`. There is no account, no credential and no
  session. `DiscordAccount` and `MicrosoftAccount` link identities to a party but authenticate
  nobody towards Forge.
- **A notion of "only mine".** A student holding `crm:read` would see every party. The only
  record-level authorization in the codebase is the delegation check of `/bot/authz/check`
  (`check_authorization` in [`authz.py`](../../app/services/bot/authz.py)), which is keyed on Discord
  users and lives in the bot domain.

Context for the timing: neither Forge's user-facing surface nor the portal exists yet, and no
consumer depends on the token shape beyond SkillBot's `client_credentials` flow. Reshaping the auth
core costs nothing now.

## Decision

**1. Forge is the identity provider for its users.** Accounts, password hashes and sessions live in
the `auth` schema next to the application clients. An account always belongs to exactly one person
party and is created by invitation only.

**2. One token endpoint, one token model.** `POST /auth/token` gains the `password` and
`refresh_token` grants. Both require client authentication, so a user token is always issued _to a
client on behalf of a user_: `sub` names the user, `azp` names the client. Its scopes are computed
once, at issuance, as the meet of what was requested, what the client is granted and what the
user's roles allow. A request is then validated exactly once, the same way for both principal types.

**3. Scopes carry a reach qualifier.** `crm:read` means all records; `crm:read:own` means the
records reachable from the caller's party. The unqualified scope implies the qualified one. An
endpoint guarded with `require_scopes(Scope.CRM_READ)` keeps demanding the unqualified scope; an
endpoint that can filter opts in with `require_access(Scope.CRM_READ)`, accepts either form and
receives an `Access` that says "all" or "these parties".

**4. Reach is derived from `PartyRelation`, per request.** It is the caller's own party plus the
`to_party` of its outgoing `PARENT_OF` and `PAYS_FOR` relations - the delegation set the bot domain
already uses. It is not stored in the token.

**5. Roles are derived, not assigned - except `admin`.** `student`, `tutor` and `guardian` follow
from the CRM; only roles the CRM cannot know are rows in `auth.user_account_role`. A role maps to
scopes in code, next to the `Scope` enum. Roles are a set: a tutor who is also an admin holds both,
and a client narrows a token to one view by requesting fewer scopes.

Deliberately _not_:

- **No external identity provider** (Keycloak, Authentik, Zitadel). It would be a second stateful
  service to run and a second user store to keep in sync with CRM invitations, for a few hundred
  accounts.
- **No identity in skillsite** (an auth library with its own user table). Identity would live
  outside the system of record and Forge would have to believe the portal about who is calling.
- **No impersonation header.** A client never says "treat me as user X". It can only present a token
  that a user's credentials produced, so a compromised portal cannot become an arbitrary user.
- **No second permission language for the API.** The grant engine in the `bot` schema
  (`PermissionGrant`, ALLOW/DENY with priorities) keeps answering which Discord user may run which
  bot workflow. API endpoints are guarded by scopes only.
- **No global `reach` claim.** One claim for the whole token would be simpler, but an endpoint that
  forgets to filter would then show a student everything. With the qualifier, such an endpoint
  answers `403`.
- **No asymmetric signing or JWKS.** Forge is the only party that validates its tokens.

## Consequences

- One guard per endpoint, one declaration site, one token validation path. A new user-facing
  capability is a new scope pair plus an opt-in on the endpoints that serve it.
- **Safe by default.** A restricted user can only reach endpoints that were deliberately made
  reach-aware. Everything else keeps answering `403`, so the rollout can go endpoint by endpoint.
- **The `password` grant is a conscious compromise.** OAuth 2.1 drops it because it hands user
  credentials to the client. Here the only such client is a first-party backend-for-frontend that
  renders the login form anyway; an authorization-code flow would add a hosted login page and
  redirects without changing who sees the password. Which clients may use the grant is itself a scope
  grant (`auth:users:login`), so it is off unless switched on.
- Forge now owns security-sensitive code it did not have before: password hashing, lockout, refresh
  rotation with reuse detection, one-time tokens. The spec pins each of these down.
- **Access tokens stay stateless** (15 minutes). Disabling an account or removing a role takes
  effect at the next refresh at the latest, not instantly.
- Reach costs one indexed query per request of a restricted token; unrestricted tokens - every
  application client - pay nothing.
- A user's e-mail address for login is stored on the account, not taken from `ContactInfo`: CRM
  e-mail addresses are unique per party only (siblings share a parent's address), a login identifier
  has to be unique globally.
- Out-of-reach records answer `404`, not `403`: a restricted caller cannot probe which parties exist.
- `TUTOR_OF` does not extend reach yet. A tutor must not see everything about a student (who pays,
  for example), so tutor reach needs field-level projections and arrives with the lessons arc.

Implementation is specified in [`user-authentication.md`](../specs/user-authentication.md).
