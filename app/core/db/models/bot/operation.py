import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, Enum, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import TimestampMixin
from .base import BotBase


class OperationKind(StrEnum):
    TUTOR_ACTIVATE = "tutor_activate"
    STUDENT_ACTIVATE = "student_activate"
    STUDENT_STASH = "student_stash"
    STUDENT_POP = "student_pop"
    STUDENT_DEACTIVATE = "student_deactivate"
    TUTOR_DEACTIVATE = "tutor_deactivate"


class OperationStatus(StrEnum):
    PREPARED = "prepared"
    COMMITTED = "committed"
    EXPIRED = "expired"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Operation(TimestampMixin, BotBase):
    """A two-phase state transition. ``prepare`` validates and reserves (writing a PREPARED
    row with a ``plan`` and ``expires_at``); ``commit`` persists the bot's confirmed Discord
    results and flips the workspace state. Kept decoupled (no FKs) as a transient
    operation/reservation log; the prepare step validates referenced entities explicitly."""

    __tablename__ = "operation"

    operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[OperationKind] = mapped_column(
        Enum(
            OperationKind,
            name="operation_kind",
            schema="bot",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    status: Mapped[OperationStatus] = mapped_column(
        Enum(
            OperationStatus,
            name="operation_status",
            schema="bot",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
        default=OperationStatus.PREPARED,
        server_default=text("'prepared'"),
    )
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subject_discord_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tutor_discord_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # For student_stash: the archive category whose slot this prepared operation reserves.
    reserved_archive_category_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    plan: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = BotBase.extend_table_args(
        Index("ix_operation_status_expires_at", "status", "expires_at"),
        Index("ix_operation_subject", "guild_id", "subject_discord_id", "status"),
        # Supports counting outstanding stash reservations per archive category.
        Index(
            "ix_operation_reservation",
            "guild_id",
            "reserved_archive_category_channel_id",
            postgresql_where=(status == OperationStatus.PREPARED) & (reserved_archive_category_channel_id.is_not(None)),
        ),
        # At most one open (PREPARED) reservation per (guild, subject, kind): the DB backstop that
        # makes ``prepare`` idempotent under concurrency. The predicate can only be
        # ``status='prepared'`` (``now()`` is not IMMUTABLE, so ``expires_at`` cannot be indexed);
        # an expired-but-unswept row still holds the slot and is reclaimed in-app on collision.
        Index(
            "uq_operation_prepared_subject_kind",
            "guild_id",
            "subject_discord_id",
            "kind",
            unique=True,
            postgresql_where=(status == OperationStatus.PREPARED),
        ),
    )
