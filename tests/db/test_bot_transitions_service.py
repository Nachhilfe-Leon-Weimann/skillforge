import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.core.db.models import (
    ArchiveCategory,
    DiscordChannel,
    DiscordChannelType,
    DiscordGuild,
    DiscordUser,
    MemberRole,
    Operation,
    OperationKind,
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
    commit_student_deactivation,
    commit_student_pop,
    commit_student_stash,
    commit_tutor_activation,
    commit_tutor_deactivation,
    prepare_student_activation,
    prepare_student_deactivation,
    prepare_student_pop,
    prepare_student_stash,
    prepare_tutor_activation,
    prepare_tutor_deactivation,
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


# --- student deactivation ---------------------------------------------------


@pytest.mark.db
async def test_student_deactivation_prepare_and_commit_from_tutor_category(session):
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)

    operation = await prepare_student_deactivation(session, guild_id=1, student_discord_id=20)
    assert operation.status is OperationStatus.PREPARED
    assert operation.kind is OperationKind.STUDENT_DEACTIVATE
    assert operation.plan == {"action": "delete_student_channel", "guild_id": 1, "channel_id": 300}

    committed = await commit_student_deactivation(session, operation_id=operation.operation_id)
    assert committed.status is OperationStatus.COMMITTED

    assert await session.get(StudentWorkspace, {"guild_id": 1, "student_discord_id": 20}) is None
    assert await session.get(DiscordChannel, 300) is None
    student = await session.get(DiscordUser, 20)
    assert student.active is False


@pytest.mark.db
async def test_student_deactivation_works_from_archive_state(session):
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)
    await _add_archive_category(session, archive_no=1, category_channel_id=200, capacity=50)

    stash_op = await prepare_student_stash(session, guild_id=1, student_discord_id=20)
    await commit_student_stash(session, operation_id=stash_op.operation_id)
    workspace = await session.get(StudentWorkspace, {"guild_id": 1, "student_discord_id": 20})
    assert workspace.channel_state is StudentChannelState.ARCHIVE_CATEGORY

    operation = await prepare_student_deactivation(session, guild_id=1, student_discord_id=20)
    assert operation.plan["channel_id"] == 300
    await commit_student_deactivation(session, operation_id=operation.operation_id)

    assert await session.get(StudentWorkspace, {"guild_id": 1, "student_discord_id": 20}) is None
    assert await session.get(DiscordChannel, 300) is None
    student = await session.get(DiscordUser, 20)
    assert student.active is False


@pytest.mark.db
async def test_student_deactivation_prepare_missing_workspace_raises(session):
    await _setup_guild_with_tutor(session)

    with pytest.raises(TransitionValidationError):
        await prepare_student_deactivation(session, guild_id=1, student_discord_id=20)


@pytest.mark.db
async def test_student_deactivation_double_commit_is_rejected(session):
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)

    operation = await prepare_student_deactivation(session, guild_id=1, student_discord_id=20)
    await commit_student_deactivation(session, operation_id=operation.operation_id)

    with pytest.raises(OperationNotPendingError):
        await commit_student_deactivation(session, operation_id=operation.operation_id)


@pytest.mark.db
async def test_student_deactivation_frees_tutor_capacity(session):
    await _setup_guild_with_tutor(session, capacity=1)
    await _add_student_workspace(session, student_id=20, channel_id=300)
    await _add_user(session, 21, MemberRole.STUDENT, "Student B")

    # The single slot is occupied, so a new activation is refused.
    with pytest.raises(TransitionConflictError):
        await prepare_student_activation(session, guild_id=1, student_discord_id=21, tutor_discord_id=10)

    deactivate_op = await prepare_student_deactivation(session, guild_id=1, student_discord_id=20)
    await commit_student_deactivation(session, operation_id=deactivate_op.operation_id)

    # The freed slot is now available again.
    operation = await prepare_student_activation(session, guild_id=1, student_discord_id=21, tutor_discord_id=10)
    assert operation.status is OperationStatus.PREPARED


# --- tutor deactivation -----------------------------------------------------


@pytest.mark.db
async def test_tutor_deactivation_prepare_and_commit_with_no_students(session):
    await _setup_guild_with_tutor(session)

    operation = await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)
    assert operation.status is OperationStatus.PREPARED
    assert operation.kind is OperationKind.TUTOR_DEACTIVATE
    assert operation.plan == {
        "action": "delete_tutor_workspace",
        "guild_id": 1,
        "category_channel_id": 100,
        "command_channel_id": 101,
    }

    committed = await commit_tutor_deactivation(session, operation_id=operation.operation_id)
    assert committed.status is OperationStatus.COMMITTED

    assert await session.get(TutorWorkspace, {"guild_id": 1, "tutor_discord_id": 10}) is None
    assert await session.get(DiscordChannel, 100) is None
    assert await session.get(DiscordChannel, 101) is None
    tutor = await session.get(DiscordUser, 10)
    assert tutor.active is False


@pytest.mark.db
async def test_tutor_deactivation_missing_workspace_raises(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")  # tutor exists but has no workspace

    with pytest.raises(TransitionValidationError):
        await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)


@pytest.mark.db
async def test_tutor_deactivation_refuses_with_student_in_tutor_category(session):
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)

    with pytest.raises(TransitionConflictError):
        await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)


@pytest.mark.db
async def test_tutor_deactivation_refuses_with_stashed_student(session):
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)
    await _add_archive_category(session, archive_no=1, category_channel_id=200, capacity=50)
    stash_op = await prepare_student_stash(session, guild_id=1, student_discord_id=20)
    await commit_student_stash(session, operation_id=stash_op.operation_id)

    with pytest.raises(TransitionConflictError):
        await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)


@pytest.mark.db
async def test_tutor_deactivation_refuses_with_outstanding_inbound_reservation(session):
    await _setup_guild_with_tutor(session)
    await _add_user(session, 20, MemberRole.STUDENT, "Student")
    # A PREPARED (uncommitted) student activation reserves a slot under the tutor.
    await prepare_student_activation(session, guild_id=1, student_discord_id=20, tutor_discord_id=10)

    with pytest.raises(TransitionConflictError):
        await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)


@pytest.mark.db
async def test_tutor_deactivation_commit_rechecks_no_students(session):
    await _setup_guild_with_tutor(session)
    operation = await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)

    # A student appears under the tutor after prepare but before commit.
    await _add_student_workspace(session, student_id=20, channel_id=300)

    with pytest.raises(TransitionConflictError):
        await commit_tutor_deactivation(session, operation_id=operation.operation_id)

    # The refusal leaves the workspace and channels intact.
    assert await session.get(TutorWorkspace, {"guild_id": 1, "tutor_discord_id": 10}) is not None
    assert await session.get(DiscordChannel, 100) is not None
    assert await session.get(DiscordChannel, 101) is not None


@pytest.mark.db
async def test_tutor_deactivation_commit_rechecks_reservations(session):
    # The commit re-check must also refuse on an inbound reservation (not only a student workspace)
    # that appears between prepare and commit.
    await _setup_guild_with_tutor(session)
    operation = await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)

    await _add_user(session, 20, MemberRole.STUDENT, "Student")
    await prepare_student_activation(session, guild_id=1, student_discord_id=20, tutor_discord_id=10)

    with pytest.raises(TransitionConflictError):
        await commit_tutor_deactivation(session, operation_id=operation.operation_id)

    assert await session.get(TutorWorkspace, {"guild_id": 1, "tutor_discord_id": 10}) is not None


@pytest.mark.db
async def test_tutor_deactivation_is_scoped_to_the_target_tutor(session):
    # A second tutor's students must not block (nor be torn down by) this tutor's teardown.
    await _setup_guild_with_tutor(session, tutor_id=10, category_id=100, command_id=101)
    await _add_user(session, 11, MemberRole.TUTOR, "Tutor B")
    await _add_channel(session, 110, DiscordChannelType.CATEGORY)
    await _add_channel(session, 111, DiscordChannelType.TEXT, parent_channel_id=110)
    session.add(TutorWorkspace(guild_id=1, tutor_discord_id=11, category_channel_id=110, command_channel_id=111))
    await session.flush()
    await _add_student_workspace(session, student_id=21, channel_id=311, tutor_id=11, parent_channel_id=110)

    # Tutor A (id 10) has no students, so its teardown succeeds.
    operation = await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)
    await commit_tutor_deactivation(session, operation_id=operation.operation_id)

    assert await session.get(TutorWorkspace, {"guild_id": 1, "tutor_discord_id": 10}) is None
    # Tutor B and its student/channels are untouched.
    assert await session.get(TutorWorkspace, {"guild_id": 1, "tutor_discord_id": 11}) is not None
    assert await session.get(DiscordChannel, 110) is not None
    assert await session.get(DiscordChannel, 111) is not None
    assert await session.get(StudentWorkspace, {"guild_id": 1, "student_discord_id": 21}) is not None


@pytest.mark.db
async def test_student_deactivation_commit_conflicts_when_workspace_already_gone(session):
    # Two PREPARED deactivations race; the second commit must find the workspace gone and refuse
    # (a TransitionConflictError, distinct from the double-commit OperationNotPendingError).
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)
    first = await prepare_student_deactivation(session, guild_id=1, student_discord_id=20)
    second = await prepare_student_deactivation(session, guild_id=1, student_discord_id=20)

    await commit_student_deactivation(session, operation_id=first.operation_id)

    with pytest.raises(TransitionConflictError):
        await commit_student_deactivation(session, operation_id=second.operation_id)


@pytest.mark.db
async def test_tutor_deactivation_commit_conflicts_when_workspace_already_gone(session):
    await _setup_guild_with_tutor(session)
    first = await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)
    second = await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)

    await commit_tutor_deactivation(session, operation_id=first.operation_id)

    with pytest.raises(TransitionConflictError):
        await commit_tutor_deactivation(session, operation_id=second.operation_id)


@pytest.mark.db
async def test_tutor_deactivation_commit_serializes_against_concurrent_reservation(db):
    # A tutor teardown that clears its commit-time re-check must not race a concurrent inbound
    # reservation: commit locks the tutor workspace FOR UPDATE (like every prepare path), so a
    # student activation preparing in parallel is serialized and the teardown then refuses.
    async with db.session() as setup:
        await _add_guild(setup, 1)
        await _add_user(setup, 10, MemberRole.TUTOR, "Tutor")
        await _add_channel(setup, 100, DiscordChannelType.CATEGORY)
        await _add_channel(setup, 101, DiscordChannelType.TEXT, parent_channel_id=100)
        setup.add(
            TutorWorkspace(
                guild_id=1,
                tutor_discord_id=10,
                category_channel_id=100,
                command_channel_id=101,
                student_channel_capacity=1,
            )
        )
        await _add_user(setup, 20, MemberRole.STUDENT, "Student")
        deactivate_op = await prepare_tutor_deactivation(setup, guild_id=1, tutor_discord_id=10)
        op_id = deactivate_op.operation_id

    lock_acquired = asyncio.Event()
    release_lock = asyncio.Event()

    async def hold_reservation_lock() -> None:
        # Session A: lock the tutor workspace FOR UPDATE and stage a PREPARED student activation,
        # then hold the transaction open (lock + still-invisible reservation) until signalled.
        async with db.session() as sess:
            await prepare_student_activation(sess, guild_id=1, student_discord_id=20, tutor_discord_id=10)
            lock_acquired.set()
            await release_lock.wait()

    async def commit_teardown():
        async with db.session() as sess:
            return await commit_tutor_deactivation(sess, operation_id=op_id)

    holder = asyncio.create_task(hold_reservation_lock())
    try:
        await lock_acquired.wait()
        teardown = asyncio.create_task(commit_teardown())
        # Let the teardown reach its FOR UPDATE lock and block behind session A.
        await asyncio.sleep(0.5)
        # Release session A: its reservation commits and becomes visible, freeing the lock.
        release_lock.set()

        # The teardown refused (rolling back without deleting anything), rather than tearing the
        # workspace down while an inbound reservation was outstanding.
        with pytest.raises(TransitionConflictError):
            await teardown
    finally:
        release_lock.set()
        await holder
        async with db.session() as cleanup:
            await cleanup.execute(delete(Operation))
            await cleanup.execute(delete(StudentWorkspace))
            await cleanup.execute(delete(TutorWorkspace))
            await cleanup.execute(delete(DiscordChannel))
            await cleanup.execute(delete(DiscordUser))
            await cleanup.execute(delete(DiscordGuild))


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
