from collections.abc import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import (
    DiscordUser,
    DiscordUserPermissionGroup,
    PermissionGrant,
    PermissionGrantEffect,
    PermissionGroup,
    PermissionSubjectType,
)

from .errors import PrincipalNotFoundError
from .profile import load_parties_for_discord_ids
from .views import PrincipalView


async def get_principal_view(session: AsyncSession, discord_id: int) -> PrincipalView:
    views = await get_principal_views(session, [discord_id])
    if not views:
        raise PrincipalNotFoundError("Discord principal not found")
    return views[0]


async def get_principal_views(session: AsyncSession, discord_ids: Iterable[int]) -> list[PrincipalView]:
    """Resolve principals for many discord ids in a fixed, small set of id-set queries.

    Returns a view per *found* id, in first-occurrence order of ``discord_ids``; unknown ids are simply
    absent (the API boundary reports them as ``missing``). De-duplicates the input.
    """

    unique_ids = list(dict.fromkeys(discord_ids))
    if not unique_ids:
        return []

    users = await _load_users(session, unique_ids)
    if not users:
        return []

    found_ids = [discord_id for discord_id in unique_ids if discord_id in users]
    group_keys_by_id = await _load_group_keys(session, found_ids)
    permission_keys_by_id = await _resolve_permission_keys(
        session, discord_ids=found_ids, group_keys_by_id=group_keys_by_id
    )
    parties_by_id = await load_parties_for_discord_ids(session, found_ids)

    return [
        PrincipalView(
            user=users[discord_id],
            group_keys=group_keys_by_id.get(discord_id, []),
            permission_keys=permission_keys_by_id.get(discord_id, []),
            party=parties_by_id.get(discord_id),
        )
        for discord_id in found_ids
    ]


async def _load_users(session: AsyncSession, discord_ids: list[int]) -> dict[int, DiscordUser]:
    result = await session.execute(select(DiscordUser).where(DiscordUser.discord_id.in_(discord_ids)))
    return {user.discord_id: user for user in result.scalars().all()}


async def _load_group_keys(session: AsyncSession, discord_ids: list[int]) -> dict[int, list[str]]:
    result = await session.execute(
        select(DiscordUserPermissionGroup.discord_id, PermissionGroup.key)
        .join(DiscordUserPermissionGroup, DiscordUserPermissionGroup.group_key == PermissionGroup.key)
        .where(
            DiscordUserPermissionGroup.discord_id.in_(discord_ids),
            PermissionGroup.active.is_(True),
        )
        .order_by(DiscordUserPermissionGroup.discord_id, PermissionGroup.key)
    )
    group_keys_by_id: dict[int, list[str]] = {}
    for discord_id, key in result.all():
        group_keys_by_id.setdefault(discord_id, []).append(key)
    return group_keys_by_id


async def _resolve_permission_keys(
    session: AsyncSession,
    *,
    discord_ids: list[int],
    group_keys_by_id: dict[int, list[str]],
) -> dict[int, list[str]]:
    """Resolve guild-agnostic effective permissions per principal.

    Considers user-scoped grants (subject_key == str(discord_id)) and group-scoped grants for each
    principal's active groups. Role-scoped grants are guild-specific (resolved via role bindings) and
    are intentionally out of scope for the guild-agnostic principal lookup. Within a single action the
    highest-priority grant wins; ties resolve to DENY.
    """

    if not discord_ids:
        return {}

    user_subject_keys = {str(discord_id) for discord_id in discord_ids}
    group_subject_keys = {key for keys in group_keys_by_id.values() for key in keys}

    conditions = [
        and_(
            PermissionGrant.subject_type == PermissionSubjectType.USER,
            PermissionGrant.subject_key.in_(user_subject_keys),
        )
    ]
    if group_subject_keys:
        conditions.append(
            and_(
                PermissionGrant.subject_type == PermissionSubjectType.GROUP,
                PermissionGrant.subject_key.in_(group_subject_keys),
            )
        )

    result = await session.execute(select(PermissionGrant).where(PermissionGrant.active.is_(True), or_(*conditions)))

    user_grants: dict[str, list[PermissionGrant]] = {}
    group_grants: dict[str, list[PermissionGrant]] = {}
    for grant in result.scalars().all():
        if grant.subject_type is PermissionSubjectType.USER:
            user_grants.setdefault(grant.subject_key, []).append(grant)
        else:
            group_grants.setdefault(grant.subject_key, []).append(grant)

    permission_keys_by_id: dict[int, list[str]] = {}
    for discord_id in discord_ids:
        grants: list[PermissionGrant] = list(user_grants.get(str(discord_id), []))
        for key in group_keys_by_id.get(discord_id, []):
            grants.extend(group_grants.get(key, []))
        permission_keys_by_id[discord_id] = _resolve_grant_winners(grants)
    return permission_keys_by_id


def _resolve_grant_winners(grants: Iterable[PermissionGrant]) -> list[str]:
    """Highest-priority grant per action wins; ties resolve to DENY. Returns sorted ALLOW action keys."""

    winners: dict[str, PermissionGrant] = {}
    for grant in grants:
        current = winners.get(grant.action_key)
        if (
            current is None
            or grant.priority > current.priority
            or (grant.priority == current.priority and grant.effect is PermissionGrantEffect.DENY)
        ):
            winners[grant.action_key] = grant
    return sorted(action_key for action_key, grant in winners.items() if grant.effect is PermissionGrantEffect.ALLOW)
