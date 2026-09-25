"""The token endpoint's grants: ``client_credentials`` for a client itself, ``password`` and ``refresh_token``
for a person on whose behalf a client acts (user-authentication spec, "Tokens").

Every grant authenticates the client first. The person grants return a ``TokenDenial`` instead of raising
(decision O): the failed-login counter, a revoked session and the audit entry of the denial must commit.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import (
    ApplicationClient,
    ApplicationClientSecret,
    ApplicationClientStatus,
    GrantMode,
    UserAccount,
    UserAccountStatus,
    UserSession,
)

from ..audit import AuditEventType, write_auth_audit_log
from ..config import AuthSettings
from ..inputs import normalize_email
from ..passwords import verify_dummy_password
from ..principal import ApplicationPrincipal, AuthMethod, PrincipalType, UserPrincipal
from ..results import IssuedUserToken, TokenDenial, UserTokenResult
from ..roles import Role, scopes_for
from ..scopes import Scope, parse_scopes
from ..secrets import verify_and_update_async, verify_secret_async
from ..tokens import CreatedAccessToken, create_access_token, create_application_access_token
from .accounts import get_user_account, lock_user_account_by_email
from .clients import find_application_client
from .errors import InvalidClientCredentialsError, InvalidClientScopeError
from .roles import account_roles
from .scopes import granted_active_scope_keys, resolve_token_scopes
from .secrets import is_secret_usable, normalize_datetime
from .sessions import (
    RefreshTokenState,
    SessionRevokedReason,
    lock_session_by_refresh_token,
    open_session,
    refresh_token_state,
    revoke_session,
    rotate_session,
)

INVALID_CLIENT_CREDENTIALS = "Invalid client credentials"
MAX_CLIENT_CREDENTIAL_LENGTH = 255
"""The longest ``client_id`` or ``client_secret`` worth looking at. A secret is 51 characters; a longer value
is ``invalid_client`` at once - never hashed, never looked up, never written into the audit log."""
RACED_REFRESH_DETAIL = "refresh token reused within grace"
"""The fixed ``token.denied`` detail of a rotated-out refresh token presented within ``REFRESH_REUSE_GRACE``."""

PASSWORD_AUTH_METHODS = frozenset({AuthMethod.PASSWORD})
"""The ``amr`` of every token of this arc's sessions: each one comes from a password login (decision Q)."""


@dataclass(frozen=True)
class _AuthenticatedClient:
    """A client that proved who it is, and the secret it proved it with."""

    client: ApplicationClient
    secret: ApplicationClientSecret

    def granted(self, mode: GrantMode) -> frozenset[str]:
        return granted_active_scope_keys(self.client.scope_grants, mode=mode)

    @property
    def actor(self) -> ApplicationPrincipal:
        """The client as the actor of a revocation its request causes."""
        return ApplicationPrincipal(
            principal_id=self.client.id, client_id=self.client.client_id, scopes=self.granted(GrantMode.APPLICATION)
        )


async def issue_client_token(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    client_id: str,
    client_secret: str,
    requested_scopes: Iterable[str] | str | None = None,
    now: datetime | None = None,
) -> CreatedAccessToken:
    issued_at = normalize_datetime(now or datetime.now(UTC))
    authenticated = await _authenticate_client(session, client_id=client_id, client_secret=client_secret, now=issued_at)
    if authenticated is None:
        raise InvalidClientCredentialsError(INVALID_CLIENT_CREDENTIALS)

    client = authenticated.client
    try:
        token_scopes = resolve_token_scopes(
            requested=parse_scopes(requested_scopes), granted=authenticated.granted(GrantMode.APPLICATION)
        )
    except InvalidClientScopeError as exc:
        await _deny_client(session, client.id, str(exc))
        raise

    authenticated.secret.last_used_at = issued_at
    token = create_application_access_token(
        settings,
        principal_id=client.id,
        client_id=client.client_id,
        scopes=token_scopes,
        now=issued_at,
    )
    await write_auth_audit_log(
        session,
        principal_type=PrincipalType.APPLICATION,
        principal_id=client.id,
        event_type=AuditEventType.TOKEN_ISSUED,
        success=True,
        detail=f"Issued client credentials token for {client.client_id}.",
    )

    await session.flush()
    return token


async def issue_user_token(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    client_id: str,
    client_secret: str,
    username: str,
    password: str,
    requested_scopes: Iterable[str] | str | None = None,
    now: datetime | None = None,
) -> UserTokenResult:
    """The ``password`` grant: log a person in through a client and open a session.

    Unknown address, disabled account, no password, a lock and a wrong password are one ``INVALID_GRANT``
    that takes as long as a wrong password. A wrong password counts towards the lock, which is persisted
    although the login is denied.
    """
    issued_at = normalize_datetime(now or datetime.now(UTC))
    authenticated = await _authenticate_login_client(
        session, client_id=client_id, client_secret=client_secret, now=issued_at
    )
    if isinstance(authenticated, TokenDenial):
        return authenticated

    # The row lock is held until the transaction ends: two wrong passwords arriving together must both count,
    # so the second reads the counter the first wrote. The Argon2 work below runs in a worker thread, so
    # waiting for the lock or the hash stalls only this login, not the event loop.
    account = await _find_login_account(session, username)
    if account is None:
        return await _refuse_login(session, None, "unknown account", password=password)
    if (refusal := _login_refusal(account, now=issued_at)) is not None:
        return await _refuse_login(session, account.id, refusal, password=password)
    if account.password_hash is None:
        return await _refuse_login(session, account.id, "no password set", password=password)

    verified, upgraded_hash = await verify_and_update_async(password, account.password_hash)
    if not verified:
        _count_failed_login(account, settings, now=issued_at)
        return await _deny_user(session, account.id, "wrong password", TokenDenial.INVALID_GRANT)

    account.failed_login_count = 0
    account.locked_until = None
    if upgraded_hash is not None:
        account.password_hash = upgraded_hash

    roles = await account_roles(session, account)
    try:
        token_scopes = resolve_token_scopes(
            requested=parse_scopes(requested_scopes),
            granted=authenticated.granted(GrantMode.DELEGATED),
            ceilings=[scopes_for(roles)],
        )
    except InvalidClientScopeError as exc:
        return await _deny_user(session, account.id, str(exc), TokenDenial.INVALID_SCOPE)

    account.last_login_at = issued_at
    opened = await open_session(
        session,
        user_id=account.id,
        application_client_id=authenticated.client.id,
        scope=token_scopes,
        expires_at=issued_at + timedelta(days=settings.refresh_token_expire_days),
    )
    return await _issue(
        session,
        settings,
        authenticated,
        account=account,
        user_session=opened.user_session,
        refresh_token=opened.plaintext,
        roles=roles,
        scopes=token_scopes,
        now=issued_at,
        detail=f"Issued a password login token through client {authenticated.client.client_id}.",
    )


async def refresh_user_token(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    requested_scopes: Iterable[str] | str | None = None,
    now: datetime | None = None,
) -> UserTokenResult:
    """The ``refresh_token`` grant: rotate a session's refresh token and issue a fresh access token.

    The scopes are computed anew - roles re-derived, the session's ``scope`` an additional ceiling that is
    never rewritten. A rotated-out token is refused: within ``REFRESH_REUSE_GRACE`` as a race, later as a
    replay that ends the session. The login lock does not apply.
    """
    issued_at = normalize_datetime(now or datetime.now(UTC))
    authenticated = await _authenticate_login_client(
        session, client_id=client_id, client_secret=client_secret, now=issued_at
    )
    if isinstance(authenticated, TokenDenial):
        return authenticated

    user_session = await lock_session_by_refresh_token(
        session, refresh_token, application_client_id=authenticated.client.id
    )
    if user_session is None:
        return await _deny_user(session, None, "unknown refresh token", TokenDenial.INVALID_GRANT)

    state = refresh_token_state(user_session, refresh_token, now=issued_at)
    if state is RefreshTokenState.ENDED:
        return await _deny_user(
            session, user_session.user_account_id, "refresh token of an ended session", TokenDenial.INVALID_GRANT
        )

    # A disabled account ends the session whichever of its tokens arrives.
    account = await get_user_account(session, user_session.user_account_id)
    if account.status is UserAccountStatus.DISABLED:
        await revoke_session(
            session,
            user_session,
            reason=SessionRevokedReason.ACCOUNT_DISABLED,
            actor=authenticated.actor,
            now=issued_at,
        )
        return TokenDenial.INVALID_GRANT

    match state:
        case RefreshTokenState.RACED:
            return await _deny_user(session, account.id, RACED_REFRESH_DETAIL, TokenDenial.INVALID_GRANT)
        case RefreshTokenState.REUSED:
            await revoke_session(
                session,
                user_session,
                reason=SessionRevokedReason.REUSE_DETECTED,
                actor=authenticated.actor,
                now=issued_at,
            )
            return TokenDenial.INVALID_GRANT
        case RefreshTokenState.CURRENT:
            pass

    roles = await account_roles(session, account)
    try:
        token_scopes = resolve_token_scopes(
            requested=parse_scopes(requested_scopes),
            granted=authenticated.granted(GrantMode.DELEGATED),
            ceilings=[scopes_for(roles), parse_scopes(user_session.scope)],
        )
    except InvalidClientScopeError as exc:
        return await _deny_user(session, account.id, str(exc), TokenDenial.INVALID_SCOPE)

    return await _issue(
        session,
        settings,
        authenticated,
        account=account,
        user_session=user_session,
        refresh_token=rotate_session(user_session, now=issued_at),
        roles=roles,
        scopes=token_scopes,
        now=issued_at,
        detail=f"Refreshed session {user_session.id} through client {authenticated.client.client_id}.",
    )


async def _issue(
    session: AsyncSession,
    settings: AuthSettings,
    authenticated: _AuthenticatedClient,
    *,
    account: UserAccount,
    user_session: UserSession,
    refresh_token: str,
    roles: frozenset[Role],
    scopes: frozenset[str],
    now: datetime,
    detail: str,
) -> IssuedUserToken:
    """Mint the person's access token for ``user_session`` and record it."""
    authenticated.secret.last_used_at = now
    principal = UserPrincipal(
        principal_id=account.id,
        client_id=authenticated.client.client_id,
        scopes=scopes,
        party_id=account.party_id,
        session_id=user_session.id,
        roles=roles,
        auth_methods=PASSWORD_AUTH_METHODS,
    )
    token = create_access_token(settings, principal, now=now)
    await write_auth_audit_log(
        session,
        principal_type=PrincipalType.USER,
        principal_id=account.id,
        event_type=AuditEventType.TOKEN_ISSUED,
        success=True,
        detail=detail,
    )
    return IssuedUserToken(
        token=token,
        refresh_token=refresh_token,
        refresh_expires_in=int((user_session.expires_at - now).total_seconds()),
    )


async def _authenticate_client(
    session: AsyncSession, *, client_id: str, client_secret: str, now: datetime
) -> _AuthenticatedClient | None:
    """Return the active client ``client_id`` if one of its usable secrets is ``client_secret``.

    ``None`` is ``invalid_client``, and its ``token.denied`` entry is written here. It names the client, or for
    an unknown one the submitted ``client_id`` - unless that is longer than ``MAX_CLIENT_CREDENTIAL_LENGTH``.
    """
    if len(client_id) > MAX_CLIENT_CREDENTIAL_LENGTH or len(client_secret) > MAX_CLIENT_CREDENTIAL_LENGTH:
        await _deny_client(session, None, INVALID_CLIENT_CREDENTIALS)
        return None

    client = await find_application_client(session, client_id)
    if client is not None and client.status == ApplicationClientStatus.ACTIVE:
        for secret in client.secrets:
            if is_secret_usable(secret, now=now) and await verify_secret_async(client_secret, secret.secret_hash):
                return _AuthenticatedClient(client=client, secret=secret)

    await _deny_client(session, client.id if client is not None else client_id, INVALID_CLIENT_CREDENTIALS)
    return None


async def _authenticate_login_client(
    session: AsyncSession, *, client_id: str, client_secret: str, now: datetime
) -> _AuthenticatedClient | TokenDenial:
    """Steps 1 and 2 of both person grants: the client proves who it is and holds ``auth:users:login`` in
    ``application`` mode."""
    authenticated = await _authenticate_client(session, client_id=client_id, client_secret=client_secret, now=now)
    if authenticated is None:
        return TokenDenial.INVALID_CLIENT
    if Scope.AUTH_USERS_LOGIN not in authenticated.granted(GrantMode.APPLICATION):
        await _deny_client(session, authenticated.client.id, "Client may not log people in")
        return TokenDenial.UNAUTHORIZED_CLIENT

    return authenticated


async def _find_login_account(session: AsyncSession, username: str) -> UserAccount | None:
    """The account ``username`` logs in to, its row locked; a username that is no address matches none."""
    try:
        email = normalize_email(username)
    except ValueError:
        return None

    return await lock_user_account_by_email(session, email)


def _login_refusal(account: UserAccount, *, now: datetime) -> str | None:
    """Why ``account`` may not log in whatever the password, or ``None``: disabled or locked.

    A locked account's counter does not move - the password is not even checked.
    """
    if account.status is UserAccountStatus.DISABLED:
        return "account disabled"
    if account.locked_until is not None and account.locked_until > now:
        return "account locked"

    return None


async def _refuse_login(session: AsyncSession, user_id: uuid.UUID | None, detail: str, *, password: str) -> TokenDenial:
    """Refuse a login before its password is checked - as slowly as a wrong password (no enumeration)."""
    await verify_dummy_password(password)
    return await _deny_user(session, user_id, detail, TokenDenial.INVALID_GRANT)


def _count_failed_login(account: UserAccount, settings: AuthSettings, *, now: datetime) -> None:
    """Count a wrong password; from the threshold on, lock for 1 minute, doubling per failure up to the maximum."""
    account.failed_login_count += 1
    beyond = account.failed_login_count - settings.login_lockout_threshold
    if beyond >= 0:
        account.locked_until = now + timedelta(minutes=min(settings.login_lockout_max_minutes, 2**beyond))


async def _deny_client(session: AsyncSession, principal_id: uuid.UUID | str | None, detail: str) -> None:
    """Record a request the client itself was refused for (steps 1 and 2 of every grant)."""
    await write_auth_audit_log(
        session,
        principal_type=PrincipalType.APPLICATION,
        principal_id=principal_id,
        event_type=AuditEventType.TOKEN_DENIED,
        success=False,
        detail=detail,
    )


async def _deny_user(session: AsyncSession, user_id: uuid.UUID | None, detail: str, denial: TokenDenial) -> TokenDenial:
    """Record a refused person grant and return ``denial``.

    The entry names the account, or ``None`` when none matched - never the submitted username.
    """
    await write_auth_audit_log(
        session,
        principal_type=PrincipalType.USER,
        principal_id=user_id,
        event_type=AuditEventType.TOKEN_DENIED,
        success=False,
        detail=detail,
    )
    return denial
