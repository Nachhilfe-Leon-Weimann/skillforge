from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import StudentWorkspace, TutorWorkspace

from .errors import StudentContextNotFoundError, TutorContextNotFoundError
from .principals import get_principal_views
from .views import StudentContextView, TutorContextView


async def get_tutor_context_view(session: AsyncSession, *, guild_id: int, tutor_discord_id: int) -> TutorContextView:
    views = await get_tutor_context_views(session, guild_id=guild_id, tutor_discord_ids=[tutor_discord_id])
    if not views:
        raise TutorContextNotFoundError("Tutor workspace not found")
    return views[0]


async def get_tutor_context_views(
    session: AsyncSession,
    *,
    guild_id: int,
    tutor_discord_ids: Iterable[int],
) -> list[TutorContextView]:
    """Resolve tutor contexts for many tutor ids within one guild. Ids without a workspace are absent."""

    unique_ids = list(dict.fromkeys(tutor_discord_ids))
    if not unique_ids:
        return []

    result = await session.execute(
        select(TutorWorkspace).where(
            TutorWorkspace.guild_id == guild_id,
            TutorWorkspace.tutor_discord_id.in_(unique_ids),
        )
    )
    workspaces = {workspace.tutor_discord_id: workspace for workspace in result.scalars().all()}
    if not workspaces:
        return []

    principals = {view.user.discord_id: view for view in await get_principal_views(session, list(workspaces))}
    return [
        TutorContextView(workspace=workspaces[discord_id], principal=principals[discord_id])
        for discord_id in unique_ids
        if discord_id in workspaces and discord_id in principals
    ]


async def get_student_context_view(
    session: AsyncSession,
    *,
    guild_id: int,
    student_discord_id: int,
) -> StudentContextView:
    views = await get_student_context_views(session, guild_id=guild_id, student_discord_ids=[student_discord_id])
    if not views:
        raise StudentContextNotFoundError("Student workspace not found")
    return views[0]


async def get_student_context_views(
    session: AsyncSession,
    *,
    guild_id: int,
    student_discord_ids: Iterable[int],
) -> list[StudentContextView]:
    """Resolve student contexts for many student ids within one guild. Ids without a workspace are absent."""

    unique_ids = list(dict.fromkeys(student_discord_ids))
    if not unique_ids:
        return []

    result = await session.execute(
        select(StudentWorkspace).where(
            StudentWorkspace.guild_id == guild_id,
            StudentWorkspace.student_discord_id.in_(unique_ids),
        )
    )
    workspaces = {workspace.student_discord_id: workspace for workspace in result.scalars().all()}
    if not workspaces:
        return []

    principals = {view.user.discord_id: view for view in await get_principal_views(session, list(workspaces))}
    views: list[StudentContextView] = []
    for discord_id in unique_ids:
        workspace = workspaces.get(discord_id)
        principal = principals.get(discord_id)
        if workspace is None or principal is None:
            continue
        party_id = principal.party.id if principal.party is not None else None
        views.append(StudentContextView(workspace=workspace, principal=principal, party_id=party_id))
    return views
