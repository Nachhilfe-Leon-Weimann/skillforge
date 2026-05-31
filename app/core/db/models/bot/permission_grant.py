import uuid
from enum import StrEnum

from sqlalchemy import UUID, Boolean, Enum, Index, Text, UniqueConstraint, text, true
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import TimestampMixin
from .base import BotBase


class PermissionSubjectType(StrEnum):
    ROLE = "role"
    GROUP = "group"
    USER = "user"


class PermissionGrantEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class PermissionGrant(TimestampMixin, BotBase):
    __tablename__ = "permission_grant"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_type: Mapped[PermissionSubjectType] = mapped_column(
        Enum(
            PermissionSubjectType,
            name="permission_subject_type",
            schema="bot",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    subject_key: Mapped[str] = mapped_column(Text, nullable=False)
    action_key: Mapped[str] = mapped_column(Text, nullable=False)
    effect: Mapped[PermissionGrantEffect] = mapped_column(
        Enum(
            PermissionGrantEffect,
            name="permission_grant_effect",
            schema="bot",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    __table_args__ = BotBase.extend_table_args(
        UniqueConstraint("subject_type", "subject_key", "action_key", name="uq_permission_grant_subject_action"),
        Index("ix_permission_grant_subject_type_subject_key_active", "subject_type", "subject_key", "active"),
        Index("ix_permission_grant_action_key", "action_key"),
    )
