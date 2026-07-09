"""Two-phase state transitions (prepare/commit).

``prepare`` validates preconditions, reserves capacity (via a PREPARED ``Operation`` row), and
returns a plan for SkillBot to execute in Discord. ``commit`` accepts the bot's confirmed
Discord results and flips the persisted workspace state. Forge never touches Discord.

Capacity is enforced under concurrency by locking the relevant rows (the tutor workspace, or
the guild's archive categories) ``FOR UPDATE`` during prepare, and counting committed workspaces
plus outstanding (non-expired) PREPARED reservations.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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

from .errors import (
    OperationNotFoundError,
    OperationNotPendingError,
    TransitionConflictError,
    TransitionValidationError,
)

# How long a prepared operation stays valid before it is treated as expired.
OPERATION_TTL = timedelta(minutes=10)

# Insert attempts for a new operation. One collision is expected at most (a concurrent winner or a
# stale row holding the unique slot); the reclaim + retry then succeeds. The extra attempt is a
# guard, not an expectation.
_CREATE_MAX_ATTEMPTS = 3

# Operation kinds that add a channel to a tutor's category (count against tutor capacity).
_TUTOR_CATEGORY_INBOUND_KINDS = (OperationKind.STUDENT_ACTIVATE, OperationKind.STUDENT_POP)


# --- tutor activation -------------------------------------------------------


async def prepare_tutor_activation(session: AsyncSession, *, guild_id: int, tutor_discord_id: int) -> Operation:
    await _require_guild(session, guild_id)
    tutor = await _require_active_user(session, tutor_discord_id, MemberRole.TUTOR, "tutor")
    if await session.get(TutorWorkspace, {"guild_id": guild_id, "tutor_discord_id": tutor.discord_id}) is not None:
        raise TransitionConflictError("Tutor workspace already exists")

    existing = await _find_open_operation(session, guild_id, tutor_discord_id, OperationKind.TUTOR_ACTIVATE)
    if existing is not None:
        return existing

    plan = {"action": "create_tutor_workspace", "guild_id": guild_id, "tutor_discord_id": tutor_discord_id}
    return await _create_operation(
        session,
        kind=OperationKind.TUTOR_ACTIVATE,
        guild_id=guild_id,
        subject_discord_id=tutor_discord_id,
        plan=plan,
    )


async def commit_tutor_activation(
    session: AsyncSession,
    *,
    operation_id: uuid.UUID,
    category_channel_id: int,
    command_channel_id: int,
) -> Operation:
    operation = await _load_prepared_operation(session, operation_id, OperationKind.TUTOR_ACTIVATE)
    if (
        await session.get(
            TutorWorkspace, {"guild_id": operation.guild_id, "tutor_discord_id": operation.subject_discord_id}
        )
        is not None
    ):
        raise TransitionConflictError("Tutor workspace already exists")

    await _ensure_channel(session, operation.guild_id, category_channel_id, DiscordChannelType.CATEGORY)
    await _ensure_channel(
        session, operation.guild_id, command_channel_id, DiscordChannelType.TEXT, parent_channel_id=category_channel_id
    )
    session.add(
        TutorWorkspace(
            guild_id=operation.guild_id,
            tutor_discord_id=operation.subject_discord_id,
            category_channel_id=category_channel_id,
            command_channel_id=command_channel_id,
        )
    )
    return await _mark_committed(session, operation)


# --- student activation -----------------------------------------------------


async def prepare_student_activation(
    session: AsyncSession,
    *,
    guild_id: int,
    student_discord_id: int,
    tutor_discord_id: int,
) -> Operation:
    await _require_guild(session, guild_id)
    await _require_active_user(session, student_discord_id, MemberRole.STUDENT, "student")
    tutor_workspace = await _lock_tutor_workspace(session, guild_id, tutor_discord_id)
    if tutor_workspace is None:
        raise TransitionValidationError("Tutor has no workspace in this guild")

    existing = await _find_open_operation(session, guild_id, student_discord_id, OperationKind.STUDENT_ACTIVATE)
    if existing is not None:
        return _replayed_or_conflict(existing, tutor_discord_id)

    if (
        await session.get(StudentWorkspace, {"guild_id": guild_id, "student_discord_id": student_discord_id})
        is not None
    ):
        raise TransitionConflictError("Student workspace already exists")
    await _assert_tutor_capacity(session, tutor_workspace)

    plan = {
        "action": "create_student_channel",
        "guild_id": guild_id,
        "parent_channel_id": tutor_workspace.category_channel_id,
    }
    return await _create_operation(
        session,
        kind=OperationKind.STUDENT_ACTIVATE,
        guild_id=guild_id,
        subject_discord_id=student_discord_id,
        tutor_discord_id=tutor_discord_id,
        plan=plan,
    )


async def commit_student_activation(
    session: AsyncSession,
    *,
    operation_id: uuid.UUID,
    channel_id: int,
) -> Operation:
    operation = await _load_prepared_operation(session, operation_id, OperationKind.STUDENT_ACTIVATE)
    if (
        await session.get(
            StudentWorkspace, {"guild_id": operation.guild_id, "student_discord_id": operation.subject_discord_id}
        )
        is not None
    ):
        raise TransitionConflictError("Student workspace already exists")

    parent_channel_id = operation.plan.get("parent_channel_id")
    await _ensure_channel(
        session, operation.guild_id, channel_id, DiscordChannelType.TEXT, parent_channel_id=parent_channel_id
    )
    session.add(
        StudentWorkspace(
            guild_id=operation.guild_id,
            student_discord_id=operation.subject_discord_id,
            tutor_discord_id=operation.tutor_discord_id,
            channel_id=channel_id,
            channel_state=StudentChannelState.TUTOR_CATEGORY,
            current_parent_channel_id=parent_channel_id,
        )
    )
    return await _mark_committed(session, operation)


# --- stash / pop ------------------------------------------------------------


async def prepare_student_stash(session: AsyncSession, *, guild_id: int, student_discord_id: int) -> Operation:
    workspace = await _require_student_workspace(session, guild_id, student_discord_id, for_update=True)
    if workspace.channel_state is not StudentChannelState.TUTOR_CATEGORY:
        raise TransitionConflictError("Student is not currently in the tutor category")

    existing = await _find_open_operation(session, guild_id, student_discord_id, OperationKind.STUDENT_STASH)
    if existing is not None:
        return existing

    archive_category = await _reserve_archive_slot(session, guild_id)
    plan = {
        "action": "stash",
        "archive_no": archive_category.archive_no,
        "archive_category_channel_id": archive_category.category_channel_id,
    }
    return await _create_operation(
        session,
        kind=OperationKind.STUDENT_STASH,
        guild_id=guild_id,
        subject_discord_id=student_discord_id,
        tutor_discord_id=workspace.tutor_discord_id,
        reserved_archive_category_channel_id=archive_category.category_channel_id,
        plan=plan,
    )


async def commit_student_stash(session: AsyncSession, *, operation_id: uuid.UUID) -> Operation:
    operation = await _load_prepared_operation(session, operation_id, OperationKind.STUDENT_STASH)
    workspace = await _require_student_workspace(session, operation.guild_id, operation.subject_discord_id)
    if workspace.channel_state is not StudentChannelState.TUTOR_CATEGORY:
        raise TransitionConflictError("Student is not currently in the tutor category")

    workspace.channel_state = StudentChannelState.ARCHIVE_CATEGORY
    workspace.archive_category_channel_id = operation.reserved_archive_category_channel_id
    workspace.current_parent_channel_id = operation.reserved_archive_category_channel_id
    workspace.stashed_at = datetime.now(UTC)
    return await _mark_committed(session, operation)


async def prepare_student_pop(session: AsyncSession, *, guild_id: int, student_discord_id: int) -> Operation:
    workspace = await _require_student_workspace(session, guild_id, student_discord_id)
    if workspace.channel_state is not StudentChannelState.ARCHIVE_CATEGORY:
        raise TransitionConflictError("Student is not currently stashed")

    tutor_workspace = await _lock_tutor_workspace(session, guild_id, workspace.tutor_discord_id)
    if tutor_workspace is None:
        raise TransitionValidationError("Tutor has no workspace in this guild")

    existing = await _find_open_operation(session, guild_id, student_discord_id, OperationKind.STUDENT_POP)
    if existing is not None:
        return existing

    await _assert_tutor_capacity(session, tutor_workspace)

    plan = {
        "action": "pop",
        "guild_id": guild_id,
        "parent_channel_id": tutor_workspace.category_channel_id,
    }
    return await _create_operation(
        session,
        kind=OperationKind.STUDENT_POP,
        guild_id=guild_id,
        subject_discord_id=student_discord_id,
        tutor_discord_id=workspace.tutor_discord_id,
        plan=plan,
    )


async def commit_student_pop(session: AsyncSession, *, operation_id: uuid.UUID) -> Operation:
    operation = await _load_prepared_operation(session, operation_id, OperationKind.STUDENT_POP)
    workspace = await _require_student_workspace(session, operation.guild_id, operation.subject_discord_id)
    if workspace.channel_state is not StudentChannelState.ARCHIVE_CATEGORY:
        raise TransitionConflictError("Student is not currently stashed")
    tutor_workspace = await session.get(
        TutorWorkspace, {"guild_id": operation.guild_id, "tutor_discord_id": workspace.tutor_discord_id}
    )
    if tutor_workspace is None:
        raise TransitionConflictError("Tutor workspace no longer exists")

    workspace.channel_state = StudentChannelState.TUTOR_CATEGORY
    workspace.archive_category_channel_id = None
    workspace.current_parent_channel_id = tutor_workspace.category_channel_id
    workspace.popped_at = datetime.now(UTC)
    return await _mark_committed(session, operation)


# --- deactivation / off-boarding --------------------------------------------


async def prepare_student_deactivation(session: AsyncSession, *, guild_id: int, student_discord_id: int) -> Operation:
    # Deactivation works from either channel state; only the workspace's existence is required.
    workspace = await _require_student_workspace(session, guild_id, student_discord_id)

    existing = await _find_open_operation(session, guild_id, student_discord_id, OperationKind.STUDENT_DEACTIVATE)
    if existing is not None:
        return existing

    plan = {"action": "delete_student_channel", "guild_id": guild_id, "channel_id": workspace.channel_id}
    return await _create_operation(
        session,
        kind=OperationKind.STUDENT_DEACTIVATE,
        guild_id=guild_id,
        subject_discord_id=student_discord_id,
        tutor_discord_id=workspace.tutor_discord_id,
        plan=plan,
    )


async def commit_student_deactivation(session: AsyncSession, *, operation_id: uuid.UUID) -> Operation:
    operation = await _load_prepared_operation(session, operation_id, OperationKind.STUDENT_DEACTIVATE)
    workspace = await session.get(
        StudentWorkspace, {"guild_id": operation.guild_id, "student_discord_id": operation.subject_discord_id}
    )
    if workspace is None:
        raise TransitionConflictError("Student workspace no longer exists")

    channel_id = workspace.channel_id
    await session.delete(workspace)
    await session.flush()
    await _delete_channel(session, channel_id)
    await _deactivate_user(session, operation.subject_discord_id)
    return await _mark_committed(session, operation)


async def prepare_tutor_deactivation(session: AsyncSession, *, guild_id: int, tutor_discord_id: int) -> Operation:
    tutor_workspace = await _lock_tutor_workspace(session, guild_id, tutor_discord_id)
    if tutor_workspace is None:
        raise TransitionValidationError("Tutor has no workspace in this guild")

    existing = await _find_open_operation(session, guild_id, tutor_discord_id, OperationKind.TUTOR_DEACTIVATE)
    if existing is not None:
        return existing

    await _assert_tutor_has_no_students(session, guild_id, tutor_discord_id)

    plan = {
        "action": "delete_tutor_workspace",
        "guild_id": guild_id,
        "category_channel_id": tutor_workspace.category_channel_id,
        "command_channel_id": tutor_workspace.command_channel_id,
    }
    return await _create_operation(
        session,
        kind=OperationKind.TUTOR_DEACTIVATE,
        guild_id=guild_id,
        subject_discord_id=tutor_discord_id,
        plan=plan,
    )


async def commit_tutor_deactivation(session: AsyncSession, *, operation_id: uuid.UUID) -> Operation:
    operation = await _load_prepared_operation(session, operation_id, OperationKind.TUTOR_DEACTIVATE)
    # Lock the workspace FOR UPDATE (as every prepare path does) so the re-check below serializes
    # against a concurrent student_activate/pop prepare and the prepare->commit race is closed.
    tutor_workspace = await _lock_tutor_workspace(session, operation.guild_id, operation.subject_discord_id)
    if tutor_workspace is None:
        raise TransitionConflictError("Tutor workspace no longer exists")
    # A student (or an inbound reservation) may have appeared between prepare and commit.
    await _assert_tutor_has_no_students(session, operation.guild_id, operation.subject_discord_id)

    category_channel_id = tutor_workspace.category_channel_id
    command_channel_id = tutor_workspace.command_channel_id
    await session.delete(tutor_workspace)
    await session.flush()
    # Delete the command channel (child) before its category (parent).
    await _delete_channel(session, command_channel_id)
    await _delete_channel(session, category_channel_id)
    await _deactivate_user(session, operation.subject_discord_id)
    return await _mark_committed(session, operation)


# --- helpers ----------------------------------------------------------------


async def _require_guild(session: AsyncSession, guild_id: int) -> DiscordGuild:
    guild = await session.get(DiscordGuild, guild_id)
    if guild is None:
        raise TransitionValidationError("Guild not found")
    return guild


async def _require_active_user(
    session: AsyncSession,
    discord_id: int,
    role: MemberRole,
    role_label: str,
) -> DiscordUser:
    user = await session.get(DiscordUser, discord_id)
    if user is None:
        raise TransitionValidationError("Discord user not found")
    if user.role is not role or not user.active:
        raise TransitionValidationError(f"User is not an active {role_label}")
    return user


async def _require_student_workspace(
    session: AsyncSession,
    guild_id: int,
    student_discord_id: int,
    *,
    for_update: bool = False,
) -> StudentWorkspace:
    if for_update:
        # Row-lock the workspace so concurrent same-student prepares serialize here. stash needs
        # this because its only other lock (the archive categories) is taken inside
        # ``_reserve_archive_slot`` - after the idempotency look-up - so without it a retry would
        # count the winner's own reservation against capacity and wrongly report "archive full".
        result = await session.execute(
            select(StudentWorkspace)
            .where(
                StudentWorkspace.guild_id == guild_id,
                StudentWorkspace.student_discord_id == student_discord_id,
            )
            .with_for_update()
        )
        workspace = result.scalar_one_or_none()
    else:
        workspace = await session.get(
            StudentWorkspace, {"guild_id": guild_id, "student_discord_id": student_discord_id}
        )
    if workspace is None:
        raise TransitionValidationError("Student workspace not found")
    return workspace


async def _lock_tutor_workspace(
    session: AsyncSession,
    guild_id: int,
    tutor_discord_id: int,
) -> TutorWorkspace | None:
    """Load and row-lock the tutor workspace to serialize concurrent capacity checks."""
    result = await session.execute(
        select(TutorWorkspace)
        .where(TutorWorkspace.guild_id == guild_id, TutorWorkspace.tutor_discord_id == tutor_discord_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _assert_tutor_capacity(session: AsyncSession, tutor_workspace: TutorWorkspace) -> None:
    occupied = await session.scalar(
        select(func.count())
        .select_from(StudentWorkspace)
        .where(
            StudentWorkspace.guild_id == tutor_workspace.guild_id,
            StudentWorkspace.tutor_discord_id == tutor_workspace.tutor_discord_id,
            StudentWorkspace.channel_state == StudentChannelState.TUTOR_CATEGORY,
        )
    )
    reserved = await session.scalar(
        select(func.count())
        .select_from(Operation)
        .where(
            Operation.status == OperationStatus.PREPARED,
            Operation.kind.in_(_TUTOR_CATEGORY_INBOUND_KINDS),
            Operation.guild_id == tutor_workspace.guild_id,
            Operation.tutor_discord_id == tutor_workspace.tutor_discord_id,
            Operation.expires_at > datetime.now(UTC),
        )
    )
    if (occupied or 0) + (reserved or 0) >= tutor_workspace.student_channel_capacity:
        raise TransitionConflictError("Tutor student capacity reached")


async def _assert_tutor_has_no_students(session: AsyncSession, guild_id: int, tutor_discord_id: int) -> None:
    """Refuse a tutor teardown while it is still occupied: any student workspace referencing the
    tutor (in *either* channel state) or any outstanding inbound reservation blocks the teardown."""
    students = await session.scalar(
        select(func.count())
        .select_from(StudentWorkspace)
        .where(
            StudentWorkspace.guild_id == guild_id,
            StudentWorkspace.tutor_discord_id == tutor_discord_id,
        )
    )
    if students:
        raise TransitionConflictError("Tutor still has student workspaces")

    reserved = await session.scalar(
        select(func.count())
        .select_from(Operation)
        .where(
            Operation.status == OperationStatus.PREPARED,
            Operation.kind.in_(_TUTOR_CATEGORY_INBOUND_KINDS),
            Operation.guild_id == guild_id,
            Operation.tutor_discord_id == tutor_discord_id,
            Operation.expires_at > datetime.now(UTC),
        )
    )
    if reserved:
        raise TransitionConflictError("Tutor has outstanding inbound reservations")


async def _reserve_archive_slot(session: AsyncSession, guild_id: int) -> ArchiveCategory:
    categories = (
        (
            await session.execute(
                select(ArchiveCategory)
                .where(ArchiveCategory.guild_id == guild_id)
                .order_by(ArchiveCategory.archive_no)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if not categories:
        raise TransitionValidationError("No archive category configured for this guild")

    now = datetime.now(UTC)
    for category in categories:
        occupied = await session.scalar(
            select(func.count())
            .select_from(StudentWorkspace)
            .where(
                StudentWorkspace.guild_id == guild_id,
                StudentWorkspace.channel_state == StudentChannelState.ARCHIVE_CATEGORY,
                StudentWorkspace.archive_category_channel_id == category.category_channel_id,
            )
        )
        reserved = await session.scalar(
            select(func.count())
            .select_from(Operation)
            .where(
                Operation.status == OperationStatus.PREPARED,
                Operation.kind == OperationKind.STUDENT_STASH,
                Operation.guild_id == guild_id,
                Operation.reserved_archive_category_channel_id == category.category_channel_id,
                Operation.expires_at > now,
            )
        )
        if (occupied or 0) + (reserved or 0) < category.capacity:
            return category

    raise TransitionConflictError("All archive categories are full")


async def _ensure_channel(
    session: AsyncSession,
    guild_id: int,
    channel_id: int,
    channel_type: DiscordChannelType,
    *,
    parent_channel_id: int | None = None,
) -> None:
    """Record a Discord channel the bot has confirmed it created. Idempotent."""
    if await session.get(DiscordChannel, channel_id) is not None:
        return
    session.add(
        DiscordChannel(
            channel_id=channel_id,
            guild_id=guild_id,
            type=channel_type,
            parent_channel_id=parent_channel_id,
        )
    )
    await session.flush()


async def _delete_channel(session: AsyncSession, channel_id: int | None) -> None:
    """Remove a Discord channel row the bot has confirmed it deleted. Idempotent - the symmetric
    inverse of ``_ensure_channel`` (activation adds the row on commit, deactivation removes it)."""
    if channel_id is None:
        return
    channel = await session.get(DiscordChannel, channel_id)
    if channel is None:
        return
    await session.delete(channel)
    await session.flush()


async def _deactivate_user(session: AsyncSession, discord_id: int) -> None:
    """Flip the subject's identity off. Party/CRM data is never touched - only the ``active`` flag."""
    user = await session.get(DiscordUser, discord_id)
    if user is not None:
        user.active = False
        await session.flush()


async def _find_open_operation(
    session: AsyncSession,
    guild_id: int,
    subject_discord_id: int,
    kind: OperationKind,
) -> Operation | None:
    """Return the outstanding (PREPARED, not yet expired) operation for this natural key, if any.

    A retried ``prepare`` must resolve to the same reservation rather than book a second one, so
    ``(guild_id, subject_discord_id, kind)`` is the identity of a logical prepare. Expired rows are
    ignored - their capacity slot is already free (the capacity counts filter ``expires_at > now``)."""
    result = await session.execute(
        select(Operation)
        .where(
            Operation.guild_id == guild_id,
            Operation.subject_discord_id == subject_discord_id,
            Operation.kind == kind,
            Operation.status == OperationStatus.PREPARED,
            Operation.expires_at > datetime.now(UTC),
        )
        .limit(1)
    )
    return result.scalars().first()


def _replayed_or_conflict(operation: Operation, tutor_discord_id: int | None) -> Operation:
    """Return an existing reservation for an idempotent retry, unless it belongs to a different
    tutor than requested.

    For ``student_activate`` - the only kind with a caller-supplied tutor - a mismatch is a
    conflicting intent (the student has exactly one workspace), not a replay, so we must not
    silently hand back the other tutor's reservation. For every other kind ``tutor_discord_id`` is
    either ``None`` or derived from the subject's single workspace, so this never false-positives."""
    if operation.tutor_discord_id != tutor_discord_id:
        raise TransitionConflictError("Subject already has an operation prepared under a different tutor")
    return operation


async def _expire_stale_prepared(
    session: AsyncSession,
    guild_id: int,
    subject_discord_id: int,
    kind: OperationKind,
) -> None:
    """Materialize expired-but-unswept PREPARED rows for this natural key to EXPIRED.

    The partial unique index only filters ``status='prepared'`` (``now()`` is not IMMUTABLE), so an
    expired reservation still holds the slot until the sweeper clears it. When a fresh prepare
    collides with such a row we reclaim it here - the same transition the sweeper would apply -
    instead of over-blocking for up to a reaper interval."""
    result = await session.execute(
        select(Operation).where(
            Operation.guild_id == guild_id,
            Operation.subject_discord_id == subject_discord_id,
            Operation.kind == kind,
            Operation.status == OperationStatus.PREPARED,
            Operation.expires_at <= datetime.now(UTC),
        )
    )
    for stale in result.scalars():
        stale.status = OperationStatus.EXPIRED
    await session.flush()


async def _create_operation(
    session: AsyncSession,
    *,
    kind: OperationKind,
    guild_id: int,
    subject_discord_id: int,
    tutor_discord_id: int | None = None,
    reserved_archive_category_channel_id: int | None = None,
    plan: dict,
) -> Operation:
    # The early look-up in each prepare path already replayed any live reservation, so normally this
    # just inserts. The loop only matters under concurrency: the partial unique index
    # (guild, subject, kind) WHERE status='prepared' can still reject the insert if a concurrent
    # prepare won the race, or if an expired-but-unswept row still holds the slot.
    for _attempt in range(_CREATE_MAX_ATTEMPTS):
        operation = Operation(
            kind=kind,
            status=OperationStatus.PREPARED,
            guild_id=guild_id,
            subject_discord_id=subject_discord_id,
            tutor_discord_id=tutor_discord_id,
            reserved_archive_category_channel_id=reserved_archive_category_channel_id,
            plan=plan,
            expires_at=datetime.now(UTC) + OPERATION_TTL,
        )
        try:
            # Add + flush *inside* the SAVEPOINT so a unique violation rolls back only this insert
            # (and drops the pending row) while leaving the surrounding transaction usable.
            async with session.begin_nested():
                session.add(operation)
                await session.flush()
            return operation
        except IntegrityError:
            # The failed insert was rolled back with the savepoint, so `operation` is no longer in
            # the session. A live winner means a concurrent prepare already reserved this slot -
            # replay it (rejecting a different-tutor intent, exactly as the early look-up does).
            winner = await _find_open_operation(session, guild_id, subject_discord_id, kind)
            if winner is not None:
                return _replayed_or_conflict(winner, tutor_discord_id)
            # Otherwise the collider is a stale (expired) row: reclaim it and retry the insert.
            await _expire_stale_prepared(session, guild_id, subject_discord_id, kind)

    # Retries exhausted (practically unreachable): surface a live winner or fail loudly.
    winner = await _find_open_operation(session, guild_id, subject_discord_id, kind)
    if winner is not None:
        return _replayed_or_conflict(winner, tutor_discord_id)
    raise TransitionConflictError("Could not reserve an operation slot")


async def _load_prepared_operation(
    session: AsyncSession,
    operation_id: uuid.UUID,
    expected_kind: OperationKind,
) -> Operation:
    operation = await session.get(Operation, operation_id)
    if operation is None or operation.kind is not expected_kind:
        raise OperationNotFoundError("Operation not found")
    if operation.status is not OperationStatus.PREPARED:
        raise OperationNotPendingError("Operation is not in a prepared state")
    if operation.expires_at <= datetime.now(UTC):
        operation.status = OperationStatus.EXPIRED
        await session.flush()
        raise OperationNotPendingError("Operation has expired")
    return operation


async def _mark_committed(session: AsyncSession, operation: Operation) -> Operation:
    operation.status = OperationStatus.COMMITTED
    operation.committed_at = datetime.now(UTC)
    await session.flush()
    return operation
