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
from .profile import load_party_for_discord_id
from .views import PrincipalView


async def get_principal_view(session: AsyncSession, discord_id: int) -> PrincipalView:
    user = await session.get(DiscordUser, discord_id)
    if user is None:
        raise PrincipalNotFoundError("Discord principal not found")

    group_keys = await _load_group_keys(session, discord_id)
    permission_keys = await _resolve_permission_keys(session, discord_id=discord_id, group_keys=group_keys)
    party = await load_party_for_discord_id(session, discord_id)

    return PrincipalView(
        user=user,
        group_keys=group_keys,
        permission_keys=permission_keys,
        party=party,
    )


async def _load_group_keys(session: AsyncSession, discord_id: int) -> list[str]:
    result = await session.execute(
        select(PermissionGroup.key)
        .join(DiscordUserPermissionGroup, DiscordUserPermissionGroup.group_key == PermissionGroup.key)
        .where(
            DiscordUserPermissionGroup.discord_id == discord_id,
            PermissionGroup.active.is_(True),
        )
        .order_by(PermissionGroup.key)
    )
    return list(result.scalars().all())


async def _resolve_permission_keys(
    session: AsyncSession,
    *,
    discord_id: int,
    group_keys: list[str],
) -> list[str]:
    """Resolve guild-agnostic effective permissions for a principal.

    Considers user-scoped grants (subject_key == str(discord_id)) and group-scoped grants for
    the principal's active groups. Role-scoped grants are guild-specific (resolved via role
    bindings) and are intentionally out of scope for the guild-agnostic principal lookup.
    Within a single action, the highest-priority grant wins; ties resolve to DENY.
    """

    subjects: list[tuple[PermissionSubjectType, str]] = [(PermissionSubjectType.USER, str(discord_id))]
    subjects.extend((PermissionSubjectType.GROUP, key) for key in group_keys)

    conditions = [
        and_(PermissionGrant.subject_type == subject_type, PermissionGrant.subject_key == subject_key)
        for subject_type, subject_key in subjects
    ]
    result = await session.execute(select(PermissionGrant).where(PermissionGrant.active.is_(True), or_(*conditions)))

    winners: dict[str, PermissionGrant] = {}
    for grant in result.scalars().all():
        current = winners.get(grant.action_key)
        if (
            current is None
            or grant.priority > current.priority
            or (grant.priority == current.priority and grant.effect is PermissionGrantEffect.DENY)
        ):
            winners[grant.action_key] = grant

    return sorted(action_key for action_key, grant in winners.items() if grant.effect is PermissionGrantEffect.ALLOW)
