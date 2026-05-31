import pytest
from sqlalchemy.exc import IntegrityError


def test_bot_model_contracts():
    import app.core.db.models  # noqa: F401
    from app.core.db.models import CommandEnvKind, MemberRole, StudentChannelState
    from app.core.db.models.base import Base

    bot_tables = {table.name for table in Base.metadata.tables.values() if table.schema == "bot"}

    assert bot_tables == {
        "app_command_audit_log",
        "archive_category",
        "command_env_channel",
        "discord_channel",
        "discord_guild",
        "discord_role_binding",
        "discord_user",
        "discord_user_permission_group",
        "permission_grant",
        "permission_group",
        "student_workspace",
        "tutor_workspace",
    }
    assert [item.value for item in MemberRole] == ["admin", "tutor", "student"]
    assert [item.value for item in CommandEnvKind] == ["admin_cmd", "tutor_cmd"]
    assert [item.value for item in StudentChannelState] == ["tutor_category", "archive_category"]


@pytest.mark.db
async def test_bot_identity_and_channel_defaults(session):
    from app.core.db.models import DiscordChannel, DiscordChannelType, DiscordGuild, DiscordUser, MemberRole

    guild = DiscordGuild(guild_id=1, name="Main")
    user = DiscordUser(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor")

    session.add_all([guild, user])
    await session.flush()

    channel = DiscordChannel(
        channel_id=100,
        guild_id=1,
        type=DiscordChannelType.CATEGORY,
        name="Tutor",
    )
    session.add(channel)
    await session.flush()

    await session.refresh(guild)
    await session.refresh(user)
    await session.refresh(channel)

    assert guild.is_primary is False
    assert guild.active is True
    assert user.active is True
    assert channel.managed_by_bot is True
    assert channel.deleted_at is None


@pytest.mark.db
async def test_only_one_active_primary_guild_is_allowed(session):
    from app.core.db.models import DiscordGuild

    session.add(DiscordGuild(guild_id=1, is_primary=True, active=True))
    await session.flush()

    await _assert_integrity_error(session, DiscordGuild(guild_id=2, is_primary=True, active=True))


@pytest.mark.db
async def test_inactive_primary_guilds_can_coexist(session):
    from app.core.db.models import DiscordGuild

    session.add_all([
        DiscordGuild(guild_id=1, is_primary=True, active=False),
        DiscordGuild(guild_id=2, is_primary=True, active=False),
    ])

    await session.flush()


@pytest.mark.db
async def test_discord_user_requires_non_empty_nick_name(session):
    from app.core.db.models import DiscordUser, MemberRole

    await _assert_integrity_error(session, DiscordUser(discord_id=10, role=MemberRole.STUDENT, nick_name=""))


@pytest.mark.db
async def test_discord_channel_parent_must_belong_to_same_guild(session):
    from app.core.db.models import DiscordChannel, DiscordChannelType, DiscordGuild

    session.add_all([DiscordGuild(guild_id=1), DiscordGuild(guild_id=2)])
    await session.flush()

    session.add(DiscordChannel(channel_id=100, guild_id=1, type=DiscordChannelType.CATEGORY))
    await session.flush()

    await _assert_integrity_error(
        session,
        DiscordChannel(
            channel_id=200,
            guild_id=2,
            parent_channel_id=100,
            type=DiscordChannelType.TEXT,
        ),
    )


@pytest.mark.db
async def test_tutor_workspace_accepts_valid_channels_and_sets_capacity_default(session):
    from app.core.db.models import TutorWorkspace

    await _create_guild_user_and_channels(
        session,
        guild_id=1,
        user_id=10,
        category_channel_id=100,
        text_channel_id=101,
    )

    session.add(
        TutorWorkspace(
            guild_id=1,
            tutor_discord_id=10,
            category_channel_id=100,
            command_channel_id=101,
        )
    )
    await session.flush()

    workspace = await session.get(TutorWorkspace, {"guild_id": 1, "tutor_discord_id": 10})
    await session.refresh(workspace)

    assert workspace.student_channel_capacity == 49


@pytest.mark.db
async def test_tutor_workspace_rejects_capacity_outside_discord_category_limit(session):
    from app.core.db.models import TutorWorkspace

    await _create_guild_user_and_channels(
        session,
        guild_id=1,
        user_id=10,
        category_channel_id=100,
        text_channel_id=101,
    )

    await _assert_integrity_error(
        session,
        TutorWorkspace(
            guild_id=1,
            tutor_discord_id=10,
            category_channel_id=100,
            command_channel_id=101,
            student_channel_capacity=50,
        ),
    )


@pytest.mark.db
async def test_archive_category_defaults_and_constraints(session):
    from app.core.db.models import ArchiveCategory, DiscordChannel, DiscordChannelType, DiscordGuild

    session.add(DiscordGuild(guild_id=1))
    await session.flush()

    session.add_all([
        DiscordChannel(channel_id=100, guild_id=1, type=DiscordChannelType.CATEGORY),
        DiscordChannel(channel_id=101, guild_id=1, type=DiscordChannelType.CATEGORY),
    ])
    await session.flush()

    session.add(ArchiveCategory(guild_id=1, archive_no=1, category_channel_id=100))
    await session.flush()

    archive = await session.get(ArchiveCategory, {"guild_id": 1, "archive_no": 1})
    await session.refresh(archive)

    assert archive.capacity == 50

    await _assert_integrity_error(session, ArchiveCategory(guild_id=1, archive_no=0, category_channel_id=101))


@pytest.mark.db
async def test_student_workspace_accepts_tutor_category_and_archive_states(session):
    from app.core.db.models import (
        ArchiveCategory,
        DiscordChannel,
        DiscordChannelType,
        DiscordGuild,
        DiscordUser,
        MemberRole,
        StudentChannelState,
        StudentWorkspace,
    )

    session.add(DiscordGuild(guild_id=1))
    await session.flush()

    session.add_all([
        DiscordUser(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor"),
        DiscordUser(discord_id=20, role=MemberRole.STUDENT, nick_name="Student A"),
        DiscordUser(discord_id=21, role=MemberRole.STUDENT, nick_name="Student B"),
    ])
    await session.flush()

    session.add_all([
        DiscordChannel(channel_id=100, guild_id=1, type=DiscordChannelType.CATEGORY),
        DiscordChannel(channel_id=200, guild_id=1, type=DiscordChannelType.CATEGORY),
    ])
    await session.flush()

    session.add_all([
        DiscordChannel(channel_id=300, guild_id=1, parent_channel_id=100, type=DiscordChannelType.TEXT),
        DiscordChannel(channel_id=301, guild_id=1, parent_channel_id=200, type=DiscordChannelType.TEXT),
    ])
    await session.flush()

    session.add(ArchiveCategory(guild_id=1, archive_no=1, category_channel_id=200))
    await session.flush()

    session.add_all([
        StudentWorkspace(
            guild_id=1,
            student_discord_id=20,
            tutor_discord_id=10,
            channel_id=300,
            current_parent_channel_id=100,
        ),
        StudentWorkspace(
            guild_id=1,
            student_discord_id=21,
            tutor_discord_id=10,
            channel_id=301,
            channel_state=StudentChannelState.ARCHIVE_CATEGORY,
            current_parent_channel_id=200,
            archive_category_channel_id=200,
        ),
    ])

    await session.flush()


@pytest.mark.db
async def test_student_workspace_rejects_archive_state_without_archive_category(session):
    from app.core.db.models import (
        DiscordChannel,
        DiscordChannelType,
        DiscordGuild,
        DiscordUser,
        MemberRole,
        StudentChannelState,
        StudentWorkspace,
    )

    session.add(DiscordGuild(guild_id=1))
    await session.flush()

    session.add_all([
        DiscordUser(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor"),
        DiscordUser(discord_id=20, role=MemberRole.STUDENT, nick_name="Student"),
    ])
    await session.flush()

    session.add(DiscordChannel(channel_id=100, guild_id=1, type=DiscordChannelType.CATEGORY))
    await session.flush()

    session.add(DiscordChannel(channel_id=300, guild_id=1, parent_channel_id=100, type=DiscordChannelType.TEXT))
    await session.flush()

    await _assert_integrity_error(
        session,
        StudentWorkspace(
            guild_id=1,
            student_discord_id=20,
            tutor_discord_id=10,
            channel_id=300,
            channel_state=StudentChannelState.ARCHIVE_CATEGORY,
            current_parent_channel_id=100,
        ),
    )


@pytest.mark.db
async def test_command_env_channel_owner_is_unique_per_guild_and_kind(session):
    from app.core.db.models import (
        CommandEnvChannel,
        CommandEnvKind,
        DiscordChannel,
        DiscordChannelType,
        DiscordGuild,
        DiscordUser,
        MemberRole,
    )

    session.add(DiscordGuild(guild_id=1))
    await session.flush()

    session.add(DiscordUser(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor"))
    await session.flush()

    session.add_all([
        DiscordChannel(channel_id=100, guild_id=1, type=DiscordChannelType.TEXT),
        DiscordChannel(channel_id=101, guild_id=1, type=DiscordChannelType.TEXT),
    ])
    await session.flush()

    session.add(
        CommandEnvChannel(
            guild_id=1,
            channel_id=100,
            kind=CommandEnvKind.TUTOR_CMD,
            owner_discord_id=10,
        )
    )
    await session.flush()

    await _assert_integrity_error(
        session,
        CommandEnvChannel(
            guild_id=1,
            channel_id=101,
            kind=CommandEnvKind.TUTOR_CMD,
            owner_discord_id=10,
        ),
    )


@pytest.mark.db
async def test_permission_grant_rejects_duplicate_subject_action(session):
    from app.core.db.models import PermissionGrant, PermissionGrantEffect, PermissionSubjectType

    session.add(
        PermissionGrant(
            subject_type=PermissionSubjectType.ROLE,
            subject_key="tutor",
            action_key="students.enable",
            effect=PermissionGrantEffect.ALLOW,
        )
    )
    await session.flush()

    await _assert_integrity_error(
        session,
        PermissionGrant(
            subject_type=PermissionSubjectType.ROLE,
            subject_key="tutor",
            action_key="students.enable",
            effect=PermissionGrantEffect.DENY,
            priority=10,
        ),
    )


@pytest.mark.db
async def test_permission_group_membership_requires_existing_user_and_group(session):
    from app.core.db.models import DiscordUser, DiscordUserPermissionGroup, MemberRole, PermissionGroup

    session.add_all([
        DiscordUser(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor"),
        PermissionGroup(key="support", name="Support"),
    ])
    await session.flush()

    session.add(DiscordUserPermissionGroup(discord_id=10, group_key="support"))
    await session.flush()

    await _assert_integrity_error(session, DiscordUserPermissionGroup(discord_id=999, group_key="support"))


async def _create_guild_user_and_channels(
    session,
    *,
    guild_id: int,
    user_id: int,
    category_channel_id: int,
    text_channel_id: int,
) -> None:
    from app.core.db.models import DiscordChannel, DiscordChannelType, DiscordGuild, DiscordUser, MemberRole

    session.add(DiscordGuild(guild_id=guild_id))
    await session.flush()

    session.add(DiscordUser(discord_id=user_id, role=MemberRole.TUTOR, nick_name="Tutor"))
    await session.flush()

    session.add(DiscordChannel(channel_id=category_channel_id, guild_id=guild_id, type=DiscordChannelType.CATEGORY))
    await session.flush()

    session.add(
        DiscordChannel(
            channel_id=text_channel_id,
            guild_id=guild_id,
            parent_channel_id=category_channel_id,
            type=DiscordChannelType.TEXT,
        )
    )
    await session.flush()


async def _assert_integrity_error(session, *objects) -> None:
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add_all(objects)
            await session.flush()
