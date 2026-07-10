import uuid

import pytest

from app.api.v1.bot.schemas import OperationalProfile
from app.core.db.models import (
    CommandEnvChannel,
    CommandEnvKind,
    ContactInfo,
    ContactInfoType,
    DiscordAccount,
    DiscordChannel,
    DiscordChannelType,
    DiscordGuild,
    DiscordUser,
    DiscordUserPermissionGroup,
    MemberRole,
    MicrosoftAccount,
    Party,
    PartyRelation,
    PartyRelationType,
    PartyType,
    PermissionGrant,
    PermissionGrantEffect,
    PermissionGroup,
    PermissionSubjectType,
    Person,
    PreferredMeetingTool,
    Student,
    StudentSubject,
    StudentWorkspace,
    Subject,
    TutorWorkspace,
)
from app.services.bot import (
    CommandEnvConflictError,
    CommandEnvNotFoundError,
    CommandEnvValidationError,
    PrincipalNotFoundError,
    StudentContextNotFoundError,
    TutorContextNotFoundError,
    delete_command_env,
    get_principal_view,
    get_principal_views,
    get_student_context_view,
    get_student_context_views,
    get_tutor_context_view,
    get_tutor_context_views,
    resolve_command_env,
    upsert_command_env,
)

# Snowflake-sized id (> 2**32) so the test fails if discord_id is not a BIGINT.
DISCORD_ID = 123456789012345678


# --- principal --------------------------------------------------------------


@pytest.mark.db
async def test_get_principal_view_resolves_groups_permissions_and_profile(session):
    party_id, parent_id = await _seed_rich_principal(session)

    view = await get_principal_view(session, DISCORD_ID)

    assert view.user.discord_id == DISCORD_ID
    assert view.group_keys == ["support"]
    # x: user ALLOW(0) vs group DENY(10) -> denied. w: tie DENY wins. inactive grant + inactive group excluded.
    assert view.permission_keys == ["y", "z"]
    assert view.party is not None
    assert view.party.id == party_id

    profile = OperationalProfile.from_party(view.party)
    assert profile.party_id == party_id
    assert profile.person is not None
    assert (profile.person.firstname, profile.person.lastname) == ("Max", "Muster")
    assert profile.subjects == ["Mathe", "Physik"]
    assert {(info.type, info.value) for info in profile.contact_infos} == {
        (ContactInfoType.EMAIL, "max@example.com"),
        (ContactInfoType.PHONE, "+4915112345678"),
    }
    assert [(rel.type, rel.direction, rel.counterparty_party_id) for rel in profile.relations] == [
        (PartyRelationType.PARENT_OF, "incoming", parent_id)
    ]
    assert profile.external_accounts.discord is not None
    assert profile.external_accounts.discord.discord_id == DISCORD_ID
    assert profile.external_accounts.discord.is_primary is True
    assert profile.external_accounts.microsoft is not None
    assert profile.external_accounts.microsoft.user_id == "MS-GRAPH-ID"


@pytest.mark.db
async def test_get_principal_view_without_party_has_no_profile(session):
    session.add(DiscordUser(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor"))
    await session.flush()

    view = await get_principal_view(session, 10)

    assert view.group_keys == []
    assert view.permission_keys == []
    assert view.party is None


@pytest.mark.db
async def test_get_principal_view_unknown_raises(session):
    with pytest.raises(PrincipalNotFoundError):
        await get_principal_view(session, 999)


@pytest.mark.db
async def test_get_principal_views_resolves_multiple_and_omits_unknown(session):
    await _seed_rich_principal(session)  # DISCORD_ID: groups ["support"], permissions ["y", "z"]
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")  # no groups, no grants, no party

    views = await get_principal_views(session, [DISCORD_ID, 10, 999])

    assert [view.user.discord_id for view in views] == [DISCORD_ID, 10]  # request order, 999 absent
    rich, plain = views
    assert rich.group_keys == ["support"]
    assert rich.permission_keys == ["y", "z"]
    assert rich.party is not None
    assert plain.group_keys == []
    assert plain.permission_keys == []
    assert plain.party is None


@pytest.mark.db
async def test_get_principal_views_dedups_and_returns_empty_for_all_unknown(session):
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")

    assert await get_principal_views(session, [777, 888]) == []

    deduped = await get_principal_views(session, [10, 10])
    assert [view.user.discord_id for view in deduped] == [10]


@pytest.mark.db
async def test_get_principal_views_matches_single_id_for_rich_principal(session):
    await _seed_rich_principal(session)

    (batched,) = await get_principal_views(session, [DISCORD_ID])
    single = await get_principal_view(session, DISCORD_ID)

    assert batched.group_keys == single.group_keys
    assert batched.permission_keys == single.permission_keys
    assert batched.party is not None and single.party is not None
    assert batched.party.id == single.party.id


# --- tutor / student contexts ----------------------------------------------


@pytest.mark.db
async def test_get_tutor_context_view_returns_workspace_and_principal(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")
    await _add_channel(session, 1, 100, DiscordChannelType.CATEGORY)
    await _add_channel(session, 1, 101, DiscordChannelType.TEXT, parent_id=100)
    session.add(TutorWorkspace(guild_id=1, tutor_discord_id=10, category_channel_id=100, command_channel_id=101))
    await session.flush()

    view = await get_tutor_context_view(session, guild_id=1, tutor_discord_id=10)

    assert view.workspace.category_channel_id == 100
    assert view.workspace.command_channel_id == 101
    assert view.workspace.student_channel_capacity == 49
    assert view.principal.user.discord_id == 10


@pytest.mark.db
async def test_get_tutor_context_view_unknown_raises(session):
    with pytest.raises(TutorContextNotFoundError):
        await get_tutor_context_view(session, guild_id=1, tutor_discord_id=10)


@pytest.mark.db
async def test_get_student_context_view_includes_party_id(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")
    await _add_user(session, 20, MemberRole.STUDENT, "Student")
    await _add_channel(session, 1, 100, DiscordChannelType.CATEGORY)
    await _add_channel(session, 1, 300, DiscordChannelType.TEXT, parent_id=100)

    party = Party(type=PartyType.PERSON)
    session.add(party)
    await session.flush()
    session.add(DiscordAccount(discord_id=20, party_id=party.id))
    session.add(
        StudentWorkspace(
            guild_id=1,
            student_discord_id=20,
            tutor_discord_id=10,
            channel_id=300,
            current_parent_channel_id=100,
        )
    )
    await session.flush()

    view = await get_student_context_view(session, guild_id=1, student_discord_id=20)

    assert view.workspace.channel_id == 300
    assert view.workspace.tutor_discord_id == 10
    assert view.party_id == party.id
    assert view.principal.user.discord_id == 20


@pytest.mark.db
async def test_get_student_context_view_unknown_raises(session):
    with pytest.raises(StudentContextNotFoundError):
        await get_student_context_view(session, guild_id=1, student_discord_id=20)


@pytest.mark.db
async def test_get_tutor_context_views_batch_omits_missing_workspace(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor A")
    await _add_user(session, 11, MemberRole.TUTOR, "Tutor B")
    await _add_channel(session, 1, 100, DiscordChannelType.CATEGORY)
    await _add_channel(session, 1, 101, DiscordChannelType.TEXT, parent_id=100)
    session.add(TutorWorkspace(guild_id=1, tutor_discord_id=10, category_channel_id=100, command_channel_id=101))
    await session.flush()

    views = await get_tutor_context_views(session, guild_id=1, tutor_discord_ids=[10, 11])

    assert [view.workspace.tutor_discord_id for view in views] == [10]  # 11 has no workspace
    assert views[0].principal.user.discord_id == 10


@pytest.mark.db
async def test_get_student_context_views_batch_includes_party_id(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")
    await _add_user(session, 20, MemberRole.STUDENT, "Student A")
    await _add_user(session, 21, MemberRole.STUDENT, "Student B")
    await _add_channel(session, 1, 100, DiscordChannelType.CATEGORY)
    await _add_channel(session, 1, 300, DiscordChannelType.TEXT, parent_id=100)
    await _add_channel(session, 1, 301, DiscordChannelType.TEXT, parent_id=100)

    party = Party(type=PartyType.PERSON)
    session.add(party)
    await session.flush()
    session.add(DiscordAccount(discord_id=20, party_id=party.id))
    session.add(
        StudentWorkspace(
            guild_id=1, student_discord_id=20, tutor_discord_id=10, channel_id=300, current_parent_channel_id=100
        )
    )
    session.add(
        StudentWorkspace(
            guild_id=1, student_discord_id=21, tutor_discord_id=10, channel_id=301, current_parent_channel_id=100
        )
    )
    await session.flush()

    views = await get_student_context_views(session, guild_id=1, student_discord_ids=[20, 21, 99])

    assert [view.workspace.student_discord_id for view in views] == [20, 21]  # 99 absent
    by_id = {view.workspace.student_discord_id: view for view in views}
    assert by_id[20].party_id == party.id
    assert by_id[21].party_id is None  # student 21 has no linked party


# --- command env resolve ----------------------------------------------------


@pytest.mark.db
async def test_resolve_command_env_matches_with_and_without_owner(session):
    await _seed_command_env(session)

    by_owner = await resolve_command_env(
        session, guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD, owner_discord_id=10
    )
    without_owner = await resolve_command_env(session, guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD)

    assert by_owner.channel_id == 100
    assert without_owner.owner_discord_id == 10


@pytest.mark.db
async def test_resolve_command_env_wrong_owner_raises(session):
    await _seed_command_env(session)

    with pytest.raises(CommandEnvNotFoundError):
        await resolve_command_env(
            session, guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD, owner_discord_id=999
        )


@pytest.mark.db
async def test_resolve_command_env_missing_raises(session):
    await _add_guild(session, 1)

    with pytest.raises(CommandEnvNotFoundError):
        await resolve_command_env(session, guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD)


@pytest.mark.db
async def test_upsert_command_env_creates_then_updates_owner(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor A")
    await _add_user(session, 11, MemberRole.TUTOR, "Tutor B")
    await _add_channel(session, 1, 100, DiscordChannelType.TEXT)

    created = await upsert_command_env(
        session, guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD, owner_discord_id=10
    )
    assert created.owner_discord_id == 10

    updated = await upsert_command_env(
        session, guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD, owner_discord_id=11
    )
    assert updated.owner_discord_id == 11

    resolved = await resolve_command_env(session, guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD)
    assert resolved.owner_discord_id == 11


@pytest.mark.db
async def test_upsert_command_env_unknown_channel_raises(session):
    await _add_guild(session, 1)

    with pytest.raises(CommandEnvValidationError):
        await upsert_command_env(session, guild_id=1, channel_id=999, kind=CommandEnvKind.TUTOR_CMD)


@pytest.mark.db
async def test_upsert_command_env_unknown_owner_raises(session):
    await _add_guild(session, 1)
    await _add_channel(session, 1, 100, DiscordChannelType.TEXT)

    with pytest.raises(CommandEnvValidationError):
        await upsert_command_env(
            session, guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD, owner_discord_id=999
        )


@pytest.mark.db
async def test_upsert_command_env_owner_conflict_raises(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")
    await _add_channel(session, 1, 100, DiscordChannelType.TEXT)
    await _add_channel(session, 1, 101, DiscordChannelType.TEXT)
    await upsert_command_env(session, guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD, owner_discord_id=10)

    with pytest.raises(CommandEnvConflictError):
        await upsert_command_env(
            session, guild_id=1, channel_id=101, kind=CommandEnvKind.TUTOR_CMD, owner_discord_id=10
        )


@pytest.mark.db
async def test_delete_command_env_removes_row(session):
    await _seed_command_env(session)

    await delete_command_env(session, guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD)

    with pytest.raises(CommandEnvNotFoundError):
        await resolve_command_env(session, guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD)


@pytest.mark.db
async def test_delete_command_env_unknown_raises(session):
    await _add_guild(session, 1)

    with pytest.raises(CommandEnvNotFoundError):
        await delete_command_env(session, guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD)


# --- seed helpers -----------------------------------------------------------


async def _seed_rich_principal(session) -> tuple[uuid.UUID, uuid.UUID]:
    # Build the whole party graph transient and flush once: associating a child to an
    # already-persistent parent via a relationship would lazy-load the backref, which is
    # illegal under an async session.
    party = Party(type=PartyType.PERSON)
    parent = Party(type=PartyType.PERSON)
    person = Person(firstname="Max", lastname="Muster", party=party)
    student = Student(preferred_meeting_tool=PreferredMeetingTool.DISCORD, person=person)
    subject_m = Subject(title="Mathe")
    subject_p = Subject(title="Physik")
    session.add_all([
        party,
        parent,
        person,
        student,
        subject_m,
        subject_p,
        StudentSubject(student=student, subject=subject_m),
        StudentSubject(student=student, subject=subject_p),
        ContactInfo(party=party, type=ContactInfoType.EMAIL, value="max@example.com"),
        ContactInfo(party=party, type=ContactInfoType.PHONE, value="+4915112345678"),
        DiscordAccount(discord_id=DISCORD_ID, party=party, is_primary=True, active=True),
        MicrosoftAccount(user_id="MS-GRAPH-ID", party=party),
        PartyRelation(from_party=parent, to_party=party, type=PartyRelationType.PARENT_OF),
        PartyRelation(from_party=parent, to_party=party, type=PartyRelationType.PAYS_FOR),
    ])
    await session.flush()

    session.add_all([
        DiscordUser(discord_id=DISCORD_ID, role=MemberRole.STUDENT, nick_name="Student"),
        PermissionGroup(key="support", name="Support", active=True),
        PermissionGroup(key="legacy", name="Legacy", active=False),
    ])
    await session.flush()

    session.add_all([
        DiscordUserPermissionGroup(discord_id=DISCORD_ID, group_key="support"),
        DiscordUserPermissionGroup(discord_id=DISCORD_ID, group_key="legacy"),
        _grant(PermissionSubjectType.USER, str(DISCORD_ID), "y", PermissionGrantEffect.ALLOW),
        _grant(PermissionSubjectType.USER, str(DISCORD_ID), "x", PermissionGrantEffect.ALLOW),
        _grant(PermissionSubjectType.GROUP, "support", "x", PermissionGrantEffect.DENY, priority=10),
        _grant(PermissionSubjectType.GROUP, "support", "z", PermissionGrantEffect.ALLOW),
        _grant(PermissionSubjectType.USER, str(DISCORD_ID), "w", PermissionGrantEffect.DENY, priority=5),
        _grant(PermissionSubjectType.GROUP, "support", "w", PermissionGrantEffect.ALLOW, priority=5),
        _grant(PermissionSubjectType.USER, str(DISCORD_ID), "inactive", PermissionGrantEffect.ALLOW, active=False),
        _grant(PermissionSubjectType.GROUP, "legacy", "legacy_only", PermissionGrantEffect.ALLOW),
    ])
    await session.flush()

    return party.id, parent.id


def _grant(
    subject_type: PermissionSubjectType,
    subject_key: str,
    action_key: str,
    effect: PermissionGrantEffect,
    *,
    priority: int = 0,
    active: bool = True,
) -> PermissionGrant:
    return PermissionGrant(
        subject_type=subject_type,
        subject_key=subject_key,
        action_key=action_key,
        effect=effect,
        priority=priority,
        active=active,
    )


async def _seed_command_env(session) -> None:
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")
    await _add_channel(session, 1, 100, DiscordChannelType.TEXT)
    session.add(CommandEnvChannel(guild_id=1, channel_id=100, kind=CommandEnvKind.TUTOR_CMD, owner_discord_id=10))
    await session.flush()


async def _add_guild(session, guild_id: int) -> None:
    session.add(DiscordGuild(guild_id=guild_id))
    await session.flush()


async def _add_user(session, discord_id: int, role: MemberRole, nick_name: str) -> None:
    session.add(DiscordUser(discord_id=discord_id, role=role, nick_name=nick_name))
    await session.flush()


async def _add_channel(
    session,
    guild_id: int,
    channel_id: int,
    channel_type: DiscordChannelType,
    *,
    parent_id: int | None = None,
) -> None:
    session.add(
        DiscordChannel(channel_id=channel_id, guild_id=guild_id, parent_channel_id=parent_id, type=channel_type)
    )
    await session.flush()
