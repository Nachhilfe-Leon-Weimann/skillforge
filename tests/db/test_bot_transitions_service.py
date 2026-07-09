import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

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
    # The workspace disappears between prepare and commit (e.g. a concurrent teardown); the commit
    # must find it gone and refuse with a TransitionConflictError, distinct from the double-commit
    # OperationNotPendingError. (A repeated prepare is now idempotent, so it cannot mint the second
    # racing operation this once did - the workspace is removed directly instead.)
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)
    operation = await prepare_student_deactivation(session, guild_id=1, student_discord_id=20)

    workspace = await session.get(StudentWorkspace, {"guild_id": 1, "student_discord_id": 20})
    await session.delete(workspace)
    await session.flush()

    with pytest.raises(TransitionConflictError):
        await commit_student_deactivation(session, operation_id=operation.operation_id)


@pytest.mark.db
async def test_tutor_deactivation_commit_conflicts_when_workspace_already_gone(session):
    # As above, but for the tutor teardown: the workspace vanishes between prepare and commit.
    await _setup_guild_with_tutor(session)
    operation = await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)

    tutor_workspace = await session.get(TutorWorkspace, {"guild_id": 1, "tutor_discord_id": 10})
    await session.delete(tutor_workspace)
    await session.flush()

    with pytest.raises(TransitionConflictError):
        await commit_tutor_deactivation(session, operation_id=operation.operation_id)


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


# --- idempotent prepare -----------------------------------------------------


@pytest.mark.db
async def test_repeated_tutor_activation_prepare_returns_same_operation(session):
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")

    first = await prepare_tutor_activation(session, guild_id=1, tutor_discord_id=10)
    second = await prepare_tutor_activation(session, guild_id=1, tutor_discord_id=10)

    assert second.operation_id == first.operation_id
    assert second.status is OperationStatus.PREPARED
    assert await _count_prepared(session, subject_discord_id=10, kind=OperationKind.TUTOR_ACTIVATE) == 1


@pytest.mark.db
async def test_repeated_student_activation_prepare_returns_same_operation(session):
    await _setup_guild_with_tutor(session)
    await _add_user(session, 20, MemberRole.STUDENT, "Student")

    first = await prepare_student_activation(session, guild_id=1, student_discord_id=20, tutor_discord_id=10)
    second = await prepare_student_activation(session, guild_id=1, student_discord_id=20, tutor_discord_id=10)

    assert second.operation_id == first.operation_id
    assert await _count_prepared(session, subject_discord_id=20, kind=OperationKind.STUDENT_ACTIVATE) == 1


@pytest.mark.db
async def test_repeated_stash_prepare_returns_same_operation(session):
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)
    await _add_archive_category(session, archive_no=1, category_channel_id=200, capacity=50)

    first = await prepare_student_stash(session, guild_id=1, student_discord_id=20)
    second = await prepare_student_stash(session, guild_id=1, student_discord_id=20)

    assert second.operation_id == first.operation_id
    # The reserved archive slot must be stable on replay so the bot can commit the same plan.
    assert second.reserved_archive_category_channel_id == first.reserved_archive_category_channel_id
    assert await _count_prepared(session, subject_discord_id=20, kind=OperationKind.STUDENT_STASH) == 1


@pytest.mark.db
async def test_repeated_pop_prepare_returns_same_operation(session):
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)
    await _add_archive_category(session, archive_no=1, category_channel_id=200, capacity=50)
    await _stash_student_workspace(session, student_id=20, archive_category_channel_id=200)

    first = await prepare_student_pop(session, guild_id=1, student_discord_id=20)
    second = await prepare_student_pop(session, guild_id=1, student_discord_id=20)

    assert second.operation_id == first.operation_id
    assert await _count_prepared(session, subject_discord_id=20, kind=OperationKind.STUDENT_POP) == 1


@pytest.mark.db
async def test_repeated_student_deactivation_prepare_returns_same_operation(session):
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)

    first = await prepare_student_deactivation(session, guild_id=1, student_discord_id=20)
    second = await prepare_student_deactivation(session, guild_id=1, student_discord_id=20)

    assert second.operation_id == first.operation_id
    assert await _count_prepared(session, subject_discord_id=20, kind=OperationKind.STUDENT_DEACTIVATE) == 1


@pytest.mark.db
async def test_repeated_tutor_deactivation_prepare_returns_same_operation(session):
    await _setup_guild_with_tutor(session)

    first = await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)
    second = await prepare_tutor_deactivation(session, guild_id=1, tutor_discord_id=10)

    assert second.operation_id == first.operation_id
    assert await _count_prepared(session, subject_discord_id=10, kind=OperationKind.TUTOR_DEACTIVATE) == 1


@pytest.mark.db
async def test_student_activation_prepare_conflicts_on_different_tutor(session):
    # A second prepare naming a DIFFERENT tutor for the same student is a conflicting intent, not a
    # retry: the student has one workspace. It must not silently replay the first tutor's reservation.
    await _setup_guild_with_tutor(session, tutor_id=10, category_id=100, command_id=101)
    await _add_user(session, 11, MemberRole.TUTOR, "Tutor B")
    await _add_channel(session, 110, DiscordChannelType.CATEGORY)
    await _add_channel(session, 111, DiscordChannelType.TEXT, parent_channel_id=110)
    session.add(TutorWorkspace(guild_id=1, tutor_discord_id=11, category_channel_id=110, command_channel_id=111))
    await session.flush()
    await _add_user(session, 20, MemberRole.STUDENT, "Student")

    await prepare_student_activation(session, guild_id=1, student_discord_id=20, tutor_discord_id=10)

    with pytest.raises(TransitionConflictError):
        await prepare_student_activation(session, guild_id=1, student_discord_id=20, tutor_discord_id=11)


@pytest.mark.db
async def test_prepare_after_expiry_creates_new_operation(session):
    # An expired reservation is not a live one: its slot is already free, so a fresh prepare must
    # mint a new operation rather than replay the dead one.
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")

    first = await prepare_tutor_activation(session, guild_id=1, tutor_discord_id=10)
    first.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    second = await prepare_tutor_activation(session, guild_id=1, tutor_discord_id=10)
    assert second.operation_id != first.operation_id
    assert second.status is OperationStatus.PREPARED


@pytest.mark.db
async def test_retried_activation_near_capacity_replays_without_capacity_error(session):
    # Capacity 1: `first` reserves the single slot. A retry of the SAME student must replay `first`
    # instead of counting first's own reservation and spuriously tripping "capacity reached".
    await _setup_guild_with_tutor(session, capacity=1)
    await _add_user(session, 20, MemberRole.STUDENT, "Student")

    first = await prepare_student_activation(session, guild_id=1, student_discord_id=20, tutor_discord_id=10)
    second = await prepare_student_activation(session, guild_id=1, student_discord_id=20, tutor_discord_id=10)

    assert second.operation_id == first.operation_id


@pytest.mark.db
async def test_retried_stash_when_archive_full_replays_without_conflict(session):
    # The only archive slot (capacity 1) is reserved by `first`; a retry of the same student must
    # replay it, not report "all archive categories are full".
    await _setup_guild_with_tutor(session)
    await _add_student_workspace(session, student_id=20, channel_id=300)
    await _add_archive_category(session, archive_no=1, category_channel_id=200, capacity=1)

    first = await prepare_student_stash(session, guild_id=1, student_discord_id=20)
    second = await prepare_student_stash(session, guild_id=1, student_discord_id=20)

    assert second.operation_id == first.operation_id


@pytest.mark.db
async def test_prepare_reclaims_expired_row_holding_the_slot(session):
    # An expired-but-unswept PREPARED row still occupies the unique (guild, subject, kind) slot
    # (the index predicate can only be `status='prepared'`). A fresh prepare must reclaim it (flip
    # to EXPIRED) and create a new reservation - not fail on the unique index, not leave two
    # PREPARED rows behind.
    await _add_guild(session, 1)
    await _add_user(session, 10, MemberRole.TUTOR, "Tutor")

    stale = await prepare_tutor_activation(session, guild_id=1, tutor_discord_id=10)
    stale_id = stale.operation_id
    stale.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    fresh = await prepare_tutor_activation(session, guild_id=1, tutor_discord_id=10)

    assert fresh.operation_id != stale_id
    assert fresh.status is OperationStatus.PREPARED
    assert await _count_prepared(session, subject_discord_id=10, kind=OperationKind.TUTOR_ACTIVATE) == 1
    reclaimed = await session.get(Operation, stale_id)
    assert reclaimed.status is OperationStatus.EXPIRED


@pytest.mark.db
async def test_concurrent_prepare_dedupes_to_a_single_operation(db):
    # Under real concurrency the early look-up cannot see another transaction's uncommitted insert
    # (READ COMMITTED), so the partial unique index is the backstop. Session A holds a PREPARED row
    # uncommitted; B's look-up sees nothing, its insert blocks on the unique index until A commits,
    # then hits the violation and replays A's winning reservation. Exactly one row must survive.
    async with db.session() as setup:
        await _add_guild(setup, 1)
        await _add_user(setup, 10, MemberRole.TUTOR, "Tutor")

    a_prepared = asyncio.Event()
    release_a = asyncio.Event()

    async def session_a():
        async with db.session() as sess:
            operation = await prepare_tutor_activation(sess, guild_id=1, tutor_discord_id=10)
            a_prepared.set()
            # Hold the transaction open (its PREPARED row is invisible to B) until signalled.
            await release_a.wait()
            return operation.operation_id

    async def session_b():
        await a_prepared.wait()
        async with db.session() as sess:
            insert = asyncio.create_task(prepare_tutor_activation(sess, guild_id=1, tutor_discord_id=10))
            # Let B reach its insert and block on the unique index behind A's uncommitted row.
            await asyncio.sleep(0.5)
            release_a.set()  # A commits, unblocking B's insert into a unique violation.
            operation = await insert
            return operation.operation_id

    try:
        a_id, b_id = await asyncio.gather(session_a(), session_b())

        assert b_id == a_id  # B replayed A's winning reservation rather than double-booking.
        async with db.session() as check:
            assert await _count_prepared(check, subject_discord_id=10, kind=OperationKind.TUTOR_ACTIVATE) == 1
    finally:
        release_a.set()
        async with db.session() as cleanup:
            await cleanup.execute(delete(Operation))
            await cleanup.execute(delete(DiscordUser))
            await cleanup.execute(delete(DiscordGuild))


@pytest.mark.db
async def test_concurrent_activation_under_different_tutor_conflicts(db):
    # Two truly-concurrent activations of the SAME student under DIFFERENT tutors lock different
    # tutor-workspace rows, so they do not serialize; B's early look-up cannot see A's uncommitted
    # row. B's insert blocks on the unique index, and when A commits B hits the violation and finds
    # A's winning reservation - which belongs to a different tutor. That is a conflicting intent,
    # not a replay, so B must get a 409 rather than silently receiving A's tutor-10 operation.
    async with db.session() as setup:
        await _setup_guild_with_tutor(setup, tutor_id=10, category_id=100, command_id=101)
        await _add_user(setup, 11, MemberRole.TUTOR, "Tutor B")
        await _add_channel(setup, 110, DiscordChannelType.CATEGORY)
        await _add_channel(setup, 111, DiscordChannelType.TEXT, parent_channel_id=110)
        setup.add(TutorWorkspace(guild_id=1, tutor_discord_id=11, category_channel_id=110, command_channel_id=111))
        await setup.flush()
        await _add_user(setup, 20, MemberRole.STUDENT, "Student")

    a_prepared = asyncio.Event()
    release_a = asyncio.Event()

    async def session_a():
        async with db.session() as sess:
            operation = await prepare_student_activation(sess, guild_id=1, student_discord_id=20, tutor_discord_id=10)
            a_prepared.set()
            await release_a.wait()
            return operation.operation_id

    async def session_b():
        await a_prepared.wait()
        async with db.session() as sess:
            insert = asyncio.create_task(
                prepare_student_activation(sess, guild_id=1, student_discord_id=20, tutor_discord_id=11)
            )
            await asyncio.sleep(0.5)
            release_a.set()
            return await insert

    try:
        with pytest.raises(TransitionConflictError):
            await asyncio.gather(session_a(), session_b())
    finally:
        release_a.set()
        async with db.session() as cleanup:
            await cleanup.execute(delete(Operation))
            await cleanup.execute(delete(StudentWorkspace))
            await cleanup.execute(delete(TutorWorkspace))
            await cleanup.execute(delete(DiscordChannel))
            await cleanup.execute(delete(DiscordUser))
            await cleanup.execute(delete(DiscordGuild))


@pytest.mark.db
async def test_concurrent_stash_same_student_replays_without_capacity_error(db):
    # The stash look-up runs before the archive-slot lock, so under concurrency B cannot see A's
    # uncommitted reservation and would otherwise re-reserve. With capacity 1, B must still replay
    # A's reservation rather than count A's slot against capacity and raise "archive full".
    async with db.session() as setup:
        await _setup_guild_with_tutor(setup)
        await _add_student_workspace(setup, student_id=20, channel_id=300)
        await _add_archive_category(setup, archive_no=1, category_channel_id=200, capacity=1)

    a_prepared = asyncio.Event()
    release_a = asyncio.Event()

    async def session_a():
        async with db.session() as sess:
            operation = await prepare_student_stash(sess, guild_id=1, student_discord_id=20)
            a_prepared.set()
            await release_a.wait()
            return operation.operation_id

    async def session_b():
        await a_prepared.wait()
        async with db.session() as sess:
            stash = asyncio.create_task(prepare_student_stash(sess, guild_id=1, student_discord_id=20))
            await asyncio.sleep(0.5)
            release_a.set()
            operation = await stash
            return operation.operation_id

    try:
        a_id, b_id = await asyncio.gather(session_a(), session_b())
        assert b_id == a_id  # B replayed A's reservation rather than tripping "archive full".
        async with db.session() as check:
            assert await _count_prepared(check, subject_discord_id=20, kind=OperationKind.STUDENT_STASH) == 1
    finally:
        release_a.set()
        async with db.session() as cleanup:
            await cleanup.execute(delete(Operation))
            await cleanup.execute(delete(StudentWorkspace))
            await cleanup.execute(delete(ArchiveCategory))
            await cleanup.execute(delete(TutorWorkspace))
            await cleanup.execute(delete(DiscordChannel))
            await cleanup.execute(delete(DiscordUser))
            await cleanup.execute(delete(DiscordGuild))


@pytest.mark.db
async def test_retried_pop_near_capacity_replays_without_capacity_error(session):
    # Capacity 1: `first` pop reserves the single slot. A retry of the same student must replay it
    # instead of counting first's own reservation and tripping "capacity reached".
    await _setup_guild_with_tutor(session, capacity=1)
    await _add_student_workspace(session, student_id=20, channel_id=300)
    await _add_archive_category(session, archive_no=1, category_channel_id=200, capacity=50)
    await _stash_student_workspace(session, student_id=20, archive_category_channel_id=200)

    first = await prepare_student_pop(session, guild_id=1, student_discord_id=20)
    second = await prepare_student_pop(session, guild_id=1, student_discord_id=20)

    assert second.operation_id == first.operation_id


# --- seed helpers -----------------------------------------------------------


async def _count_prepared(session, *, subject_discord_id: int, kind: OperationKind, guild_id: int = 1) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(Operation)
        .where(
            Operation.guild_id == guild_id,
            Operation.subject_discord_id == subject_discord_id,
            Operation.kind == kind,
            Operation.status == OperationStatus.PREPARED,
        )
    )
    return count or 0


async def _stash_student_workspace(session, *, student_id: int, archive_category_channel_id: int) -> None:
    """Flip an existing student workspace into the archived state (for pop tests)."""
    workspace = await session.get(StudentWorkspace, {"guild_id": 1, "student_discord_id": student_id})
    workspace.channel_state = StudentChannelState.ARCHIVE_CATEGORY
    workspace.archive_category_channel_id = archive_category_channel_id
    workspace.current_parent_channel_id = archive_category_channel_id
    await session.flush()


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
