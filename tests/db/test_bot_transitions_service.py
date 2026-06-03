from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.db.models import (
    ArchiveCategory,
    DiscordChannel,
    DiscordChannelType,
    DiscordGuild,
    DiscordUser,
    MemberRole,
    OperationStatus,
    StudentChannelState,
    StudentWorkspace,
    TutorWorkspace,
)
from app.services.bot import (
    OperationNotFoundError,
    OperationNotPendingError,
    TransitionConflictError,
    TransitionValidationError,
    commit_student_activation,
    commit_student_pop,
    commit_student_stash,
    commit_tutor_activation,
    prepare_student_activation,
    prepare_student_pop,
    prepare_student_stash,
    prepare_tutor_activation,
)

# --- tutor activation -------------------------------------------------------


@pytest.mark.db
async def test_tutor_activation_prepare_and_commit(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")

    operation = await prepare_tutor_activation(session, guild_id=1, tutor_discord_id=10)
    assert operation.status is OperationStatus.PREPARED

    committed = await commit_tutor_activation(
        session, operation_id=operation.operation_id, category_channel_id=100, command_channel_id=101
    )
    assert committed.status is OperationStatus.COMMITTED

    workspace = await session.get(TutorWorkspace, {"guild_id": 1, "tutor_discord_id": 10})
    assert workspace is not None
    assert workspace.category_channel_id == 100
    assert workspace.command_channel_id == 101


@pytest.mark.db
async def test_tutor_activation_prepare_rejects_non_tutor(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.STUDENT, "Student")

    with pytest.raises(TransitionValidationError):
        await prepare_tutor_activation(session, guild_id=1, tutor_discord_id=10)


@pytest.mark.db
async def test_tutor_activation_prepare_conflicts_when_workspace_exists(session):
    await _setup_guild_with_tutor(session)

    with pytest.raises(TransitionConflictError):
        await prepare_tutor_activation(session, guild_id=1, tutor_discord_id=10)


# --- student activation -----------------------------------------------------


@pytest.mark.db
async def test_student_activation_prepare_and_commit(session):
    await _setup_guild_with_tutor(session)
    await _add_user(session, 20, MemberRole.STUDENT, "Student")

    operation = await prepare_student_activation(session, guild_id=1, student_discord_id=20, tutor_discord_id=10)
    committed = await commit_student_activation(session, operation_id=operation.operation_id, channel_id=300)
    assert committed.status is OperationStatus.COMMITTED

    workspace = await session.get(StudentWorkspace, {"guild_id": 1, "student_discord_id": 20})
    assert workspace is not None
    assert workspace.channel_id == 300
    assert workspace.channel_state is StudentChannelState.TUTOR_CATEGORY
    assert workspace.current_parent_channel_id == 100


@pytest.mark.db
async def test_student_activation_capacity_counts_reservations(session):
    # Capacity 1: a single outstanding PREPARED reservation already fills the tutor.
    await _setup_guild_with_tutor(session, capacity=1)
    await _add_user(session, 20, MemberRole.STUDENT, "Student A")
    await _add_user(session, 21, MemberRole.STUDENT, "Student B")

    await prepare_student_activation(session, guild_id=1, student_discord_id=20, tutor_discord_id=10)

    with pytest.raises(TransitionConflictError):
        await prepare_student_activation(session, guild_id=1, student_discord_id=21, tutor_discord_id=10)


@pytest.mark.db
async def test_student_activation_unknown_tutor_workspace(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")  # tutor exists but no workspace
    await _add_user(session, 20, MemberRole.STUDENT, "Student")

    with pytest.raises(TransitionValidationError):
        await prepare_student_activation(session, guild_id=1, student_discord_id=20, tutor_discord_id=10)


# --- stash / pop ------------------------------------------------------------


@pytest.mark.db
async def test_stash_then_pop_round_trip(session):
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)
    await _add_archive_category(session, archive_no=1, category_channel_id=200, capacity=50)

    stash_op = await prepare_student_stash(session, guild_id=1, student_discord_id=20)
    assert stash_op.reserved_archive_category_channel_id == 200
    await commit_student_stash(session, operation_id=stash_op.operation_id)

    workspace = await session.get(StudentWorkspace, {"guild_id": 1, "student_discord_id": 20})
    assert workspace.channel_state is StudentChannelState.ARCHIVE_CATEGORY
    assert workspace.archive_category_channel_id == 200
    assert workspace.stashed_at is not None

    pop_op = await prepare_student_pop(session, guild_id=1, student_discord_id=20)
    await commit_student_pop(session, operation_id=pop_op.operation_id)

    await session.refresh(workspace)
    assert workspace.channel_state is StudentChannelState.TUTOR_CATEGORY
    assert workspace.archive_category_channel_id is None
    assert workspace.current_parent_channel_id == 100
    assert workspace.popped_at is not None


@pytest.mark.db
async def test_stash_rejects_already_stashed_student(session):
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)
    await _add_archive_category(session, archive_no=1, category_channel_id=200)

    op = await prepare_student_stash(session, guild_id=1, student_discord_id=20)
    await commit_student_stash(session, operation_id=op.operation_id)

    with pytest.raises(TransitionConflictError):
        await prepare_student_stash(session, guild_id=1, student_discord_id=20)


@pytest.mark.db
async def test_stash_capacity_full_across_archives(session):
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)
    await _add_student_workspace(session, student_id=21, channel_id=301)
    await _add_archive_category(session, archive_no=1, category_channel_id=200, capacity=1)

    first = await prepare_student_stash(session, guild_id=1, student_discord_id=20)
    await commit_student_stash(session, operation_id=first.operation_id)

    # The only archive category (capacity 1) is now full.
    with pytest.raises(TransitionConflictError):
        await prepare_student_stash(session, guild_id=1, student_discord_id=21)


@pytest.mark.db
async def test_stash_requires_archive_category(session):
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)

    with pytest.raises(TransitionValidationError):
        await prepare_student_stash(session, guild_id=1, student_discord_id=20)


# --- operation lifecycle ----------------------------------------------------


@pytest.mark.db
async def test_commit_unknown_operation_raises(session):
    with pytest.raises(OperationNotFoundError):
        await commit_tutor_activation(session, operation_id=uuid4(), category_channel_id=100, command_channel_id=101)


@pytest.mark.db
async def test_double_commit_is_rejected(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")
    operation = await prepare_tutor_activation(session, guild_id=1, tutor_discord_id=10)
    await commit_tutor_activation(
        session, operation_id=operation.operation_id, category_channel_id=100, command_channel_id=101
    )

    with pytest.raises(OperationNotPendingError):
        await commit_tutor_activation(
            session, operation_id=operation.operation_id, category_channel_id=100, command_channel_id=101
        )


@pytest.mark.db
async def test_expired_operation_cannot_commit(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")
    operation = await prepare_tutor_activation(session, guild_id=1, tutor_discord_id=10)

    operation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    with pytest.raises(OperationNotPendingError):
        await commit_tutor_activation(
            session, operation_id=operation.operation_id, category_channel_id=100, command_channel_id=101
        )

    await session.refresh(operation)
    assert operation.status is OperationStatus.EXPIRED


# --- seed helpers -----------------------------------------------------------


async def _add_guild(session, guild_id: int) -> None:
    session.add(DiscordGuild(guild_id=guild_id))
    await session.flush()


async def _add_user(session, discord_id: int, role: MemberRole, nick_name: str) -> None:
    session.add(DiscordUser(discord_id=discord_id, role=role, nick_name=nick_name))
    await session.flush()


async def _add_channel(
    session,
    channel_id: int,
    channel_type: DiscordChannelType,
    *,
    guild_id: int = 1,
    parent_channel_id: int | None = None,
) -> None:
    session.add(
        DiscordChannel(channel_id=channel_id, guild_id=guild_id, parent_channel_id=parent_channel_id, type=channel_type)
    )
    await session.flush()


async def _setup_guild_with_tutor(
    session,
    *,
    guild_id: int = 1,
    tutor_id: int = 10,
    category_id: int = 100,
    command_id: int = 101,
    capacity: int = 49,
) -> None:
    await _add_guild(session, guild_id)
    await _add_user(session, tutor_id, MemberRole.TUTOR, "Tutor")
    await _add_channel(session, category_id, DiscordChannelType.CATEGORY, guild_id=guild_id)
    await _add_channel(session, command_id, DiscordChannelType.TEXT, guild_id=guild_id, parent_channel_id=category_id)
    session.add(
        TutorWorkspace(
            guild_id=guild_id,
            tutor_discord_id=tutor_id,
            category_channel_id=category_id,
            command_channel_id=command_id,
            student_channel_capacity=capacity,
        )
    )
    await session.flush()


async def _add_student_workspace(
    session,
    *,
    student_id: int,
    channel_id: int,
    guild_id: int = 1,
    tutor_id: int = 10,
    parent_channel_id: int = 100,
) -> None:
    await _add_user(session, student_id, MemberRole.STUDENT, "Student")
    await _add_channel(
        session, channel_id, DiscordChannelType.TEXT, guild_id=guild_id, parent_channel_id=parent_channel_id
    )
    session.add(
        StudentWorkspace(
            guild_id=guild_id,
            student_discord_id=student_id,
            tutor_discord_id=tutor_id,
            channel_id=channel_id,
            channel_state=StudentChannelState.TUTOR_CATEGORY,
            current_parent_channel_id=parent_channel_id,
        )
    )
    await session.flush()


async def _add_archive_category(
    session,
    *,
    archive_no: int,
    category_channel_id: int,
    guild_id: int = 1,
    capacity: int = 50,
) -> None:
    await _add_channel(session, category_channel_id, DiscordChannelType.CATEGORY, guild_id=guild_id)
    session.add(
        ArchiveCategory(
            guild_id=guild_id,
            archive_no=archive_no,
            category_channel_id=category_channel_id,
            capacity=capacity,
        )
    )
    await session.flush()
