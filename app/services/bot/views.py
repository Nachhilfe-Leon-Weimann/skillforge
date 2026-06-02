from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.db.models import DiscordUser, Party, StudentWorkspace, TutorWorkspace


@dataclass(frozen=True)
class PrincipalView:
    """Aggregated principal state assembled by the bot service layer."""

    user: DiscordUser
    group_keys: list[str]
    permission_keys: list[str]
    party: Party | None


@dataclass(frozen=True)
class TutorContextView:
    workspace: TutorWorkspace
    principal: PrincipalView


@dataclass(frozen=True)
class StudentContextView:
    workspace: StudentWorkspace
    principal: PrincipalView
    party_id: UUID | None
