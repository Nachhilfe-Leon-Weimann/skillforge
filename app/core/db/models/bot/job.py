import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import TimestampMixin
from .base import BotBase


class JobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(TimestampMixin, BotBase):
    """Forge-first work queue. Forge enqueues jobs; SkillBot polls (claims), then reports
    completion or failure. Claiming is atomic via ``SELECT ... FOR UPDATE SKIP LOCKED``."""

    __tablename__ = "job"

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            name="job_status",
            schema="bot",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
        default=JobStatus.PENDING,
        server_default=text("'pending'"),
    )
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5, server_default=text("5"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = BotBase.extend_table_args(
        CheckConstraint("attempt >= 0", name="ck_job_attempt_non_negative"),
        CheckConstraint("max_attempts >= 1", name="ck_job_max_attempts_positive"),
        # Supports the claim query: pending jobs ordered by availability.
        Index(
            "ix_job_claimable",
            "available_at",
            postgresql_where=status == JobStatus.PENDING,
        ),
        Index("ix_job_kind_status", "kind", "status"),
    )
