from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.db.models import DiscordUser, JobStatus, Party, StudentWorkspace, TutorWorkspace


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


@dataclass(frozen=True)
class JobKindCountsView:
    """Per-``kind`` job counts, zero-filled across every :class:`JobStatus`."""

    kind: str
    counts: dict[JobStatus, int]


@dataclass(frozen=True)
class JobQueueSummaryView:
    """Funnel over the job queue: overall counts by status plus a per-kind breakdown.

    ``by_status`` and each kind's counts are zero-filled across every :class:`JobStatus`; derived
    figures (``total``, ``open = pending + claimed``) are computed at the API boundary.
    """

    by_status: dict[JobStatus, int]
    by_kind: list[JobKindCountsView]
