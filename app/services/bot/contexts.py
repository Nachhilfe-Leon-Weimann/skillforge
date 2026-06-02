from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import StudentWorkspace, TutorWorkspace

from .errors import StudentContextNotFoundError, TutorContextNotFoundError
from .principals import get_principal_view
from .views import StudentContextView, TutorContextView


async def get_tutor_context_view(
    session: AsyncSession,
    *,
    guild_id: int,
    tutor_discord_id: int,
) -> TutorContextView:
    workspace = await session.get(TutorWorkspace, {"guild_id": guild_id, "tutor_discord_id": tutor_discord_id})
    if workspace is None:
        raise TutorContextNotFoundError("Tutor workspace not found")

    principal = await get_principal_view(session, tutor_discord_id)
    return TutorContextView(workspace=workspace, principal=principal)


async def get_student_context_view(
    session: AsyncSession,
    *,
    guild_id: int,
    student_discord_id: int,
) -> StudentContextView:
    workspace = await session.get(StudentWorkspace, {"guild_id": guild_id, "student_discord_id": student_discord_id})
    if workspace is None:
        raise StudentContextNotFoundError("Student workspace not found")

    principal = await get_principal_view(session, student_discord_id)
    party_id = principal.party.id if principal.party is not None else None
    return StudentContextView(workspace=workspace, principal=principal, party_id=party_id)
