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

# Operation kinds that add a channel to a tutor's category (count against tutor capacity).
_TUTOR_CATEGORY_INBOUND_KINDS = (OperationKind.STUDENT_ACTIVATE, OperationKind.STUDENT_POP)


# --- tutor activation -------------------------------------------------------


async def prepare_tutor_activation(session: AsyncSession, *, guild_id: int, tutor_discord_id: int) -> Operation:
    await _require_guild(session, guild_id)
    tutor = await _require_active_user(session, tutor_discord_id, MemberRole.TUTOR, "tutor")
    if await session.get(TutorWorkspace, {"guild_id": guild_id, "tutor_discord_id": tutor.discord_id}) is not None:
        raise TransitionConflictError("Tutor workspace already exists")

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
    workspace = await _require_student_workspace(session, guild_id, student_discord_id)
    if workspace.channel_state is not StudentChannelState.TUTOR_CATEGORY:
        raise TransitionConflictError("Student is not currently in the tutor category")

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
) -> StudentWorkspace:
    workspace = await session.get(StudentWorkspace, {"guild_id": guild_id, "student_discord_id": student_discord_id})
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
    session.add(operation)
    await session.flush()
    return operation


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
