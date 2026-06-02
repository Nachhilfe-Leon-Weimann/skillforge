from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.db.models import (
    CommandEnvKind,
    ContactInfoType,
    JobStatus,
    MemberRole,
    PartyRelationType,
    StudentChannelState,
)

if TYPE_CHECKING:
    from app.core.db.models import (
        DiscordUser,
        Job,
        Party,
        StudentWorkspace,
        TutorWorkspace,
    )


# --- Operational profile ----------------------------------------------------


class PersonProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    firstname: str
    lastname: str


class ContactInfoProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: ContactInfoType
    value: str
    label: str | None = None


class RelationProfile(BaseModel):
    type: PartyRelationType
    direction: Literal["outgoing", "incoming"]
    counterparty_party_id: UUID


class DiscordAccountProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    discord_id: int
    is_primary: bool
    active: bool


class MicrosoftAccountProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str


class ExternalAccountsProfile(BaseModel):
    discord: DiscordAccountProfile | None = None
    microsoft: MicrosoftAccountProfile | None = None


class OperationalProfile(BaseModel):
    """Operational view of a party. Intentionally excludes billing/finance data."""

    party_id: UUID
    person: PersonProfile | None = None
    contact_infos: list[ContactInfoProfile] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    relations: list[RelationProfile] = Field(default_factory=list)
    external_accounts: ExternalAccountsProfile = Field(default_factory=ExternalAccountsProfile)

    @classmethod
    def from_party(cls, party: Party) -> OperationalProfile:
        person = party.person

        subjects: set[str] = set()
        if person is not None:
            if person.tutor is not None:
                subjects.update(ts.subject.title for ts in person.tutor.tutor_subjects)
            if person.student is not None:
                subjects.update(ss.subject.title for ss in person.student.student_subjects)

        relations: list[RelationProfile] = []
        for relation in party.outgoing_relations:
            if relation.type is PartyRelationType.PAYS_FOR:
                continue
            relations.append(
                RelationProfile(
                    type=relation.type,
                    direction="outgoing",
                    counterparty_party_id=relation.to_party_id,
                )
            )
        for relation in party.incoming_relations:
            if relation.type is PartyRelationType.PAYS_FOR:
                continue
            relations.append(
                RelationProfile(
                    type=relation.type,
                    direction="incoming",
                    counterparty_party_id=relation.from_party_id,
                )
            )

        return cls(
            party_id=party.id,
            person=PersonProfile.model_validate(person) if person is not None else None,
            contact_infos=[ContactInfoProfile.model_validate(info) for info in party.contact_infos],
            subjects=sorted(subjects),
            relations=relations,
            external_accounts=ExternalAccountsProfile(
                discord=(
                    DiscordAccountProfile.model_validate(party.discord_account)
                    if party.discord_account is not None
                    else None
                ),
                microsoft=(
                    MicrosoftAccountProfile.model_validate(party.microsoft_account)
                    if party.microsoft_account is not None
                    else None
                ),
            ),
        )


# --- Principal & runtime contexts -------------------------------------------


class BotPrincipal(BaseModel):
    discord_id: int
    role: MemberRole
    display_name: str
    active: bool
    groups: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    profile: OperationalProfile | None = None

    @classmethod
    def from_parts(
        cls,
        *,
        user: DiscordUser,
        group_keys: list[str],
        permission_keys: list[str],
        party: Party | None,
    ) -> BotPrincipal:
        return cls(
            discord_id=user.discord_id,
            role=user.role,
            display_name=user.nick_name,
            active=user.active,
            groups=group_keys,
            permissions=permission_keys,
            profile=OperationalProfile.from_party(party) if party is not None else None,
        )


class TutorContext(BaseModel):
    principal: BotPrincipal
    guild_id: int
    category_channel_id: int
    command_channel_id: int
    student_channel_capacity: int

    @classmethod
    def from_parts(cls, *, principal: BotPrincipal, workspace: TutorWorkspace) -> TutorContext:
        return cls(
            principal=principal,
            guild_id=workspace.guild_id,
            category_channel_id=workspace.category_channel_id,
            command_channel_id=workspace.command_channel_id,
            student_channel_capacity=workspace.student_channel_capacity,
        )


class StudentContext(BaseModel):
    principal: BotPrincipal
    party_id: UUID | None
    tutor_discord_id: int
    channel_id: int | None
    channel_state: StudentChannelState
    current_parent_channel_id: int | None

    @classmethod
    def from_parts(
        cls,
        *,
        principal: BotPrincipal,
        workspace: StudentWorkspace,
        party_id: UUID | None,
    ) -> StudentContext:
        return cls(
            principal=principal,
            party_id=party_id,
            tutor_discord_id=workspace.tutor_discord_id,
            channel_id=workspace.channel_id,
            channel_state=workspace.channel_state,
            current_parent_channel_id=workspace.current_parent_channel_id,
        )


class CommandEnvChannelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    guild_id: int
    channel_id: int
    kind: CommandEnvKind
    owner_discord_id: int | None = None

    @classmethod
    def from_model(cls, command_env: object) -> CommandEnvChannelResponse:
        return cls.model_validate(command_env)


# --- Jobs -------------------------------------------------------------------


class JobClaimRequest(BaseModel):
    kinds: list[str] | None = None
    limit: int = Field(default=1, ge=1, le=50)
    worker: str | None = None


class JobFailRequest(BaseModel):
    error: str | None = None
    retry: bool = False


class BotJob(BaseModel):
    """Processing view returned when a job is claimed."""

    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    kind: str
    payload: dict[str, Any]
    claimed_at: datetime | None
    attempt: int

    @classmethod
    def from_model(cls, job: Job) -> BotJob:
        return cls.model_validate(job)


class JobResponse(BaseModel):
    """State view returned after completing or failing a job."""

    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    kind: str
    status: JobStatus
    attempt: int
    available_at: datetime
    claimed_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    last_error: str | None

    @classmethod
    def from_model(cls, job: Job) -> JobResponse:
        return cls.model_validate(job)
