import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import UUID, CheckConstraint, DateTime, Enum, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import TimestampMixin
from .base import AuthBase

if TYPE_CHECKING:
    from .user_account_role import UserAccountRole
    from .user_action_token import UserActionToken
    from .user_session import UserSession


class UserAccountStatus(enum.StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class UserAccount(TimestampMixin, AuthBase):
    """The account of exactly one person party, ``active`` from its creation; how the person logs in
    hangs off it (ADR 0008). ``id`` is the token's ``principal_id``. ``party_id`` cascades on delete
    and ``Party`` gets no relationship back: the CRM stays unaware of accounts (ADR 0007)."""

    __tablename__ = "user_account"
    __table_args__ = AuthBase.extend_table_args(
        CheckConstraint("email = lower(email)", name="ck_user_account_email_lowercase"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    party_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.party.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    email: Mapped[str | None] = mapped_column(nullable=True, unique=True)
    password_hash: Mapped[str | None] = mapped_column(nullable=True)

    status: Mapped[UserAccountStatus] = mapped_column(
        Enum(
            UserAccountStatus,
            name="user_account_status",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
        default=UserAccountStatus.ACTIVE,
        server_default=text("'active'"),
    )

    failed_login_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The foreign keys cascade in the database; ``passive_deletes`` leaves unloaded children to it.
    roles: Mapped[list[UserAccountRole]] = relationship(
        "UserAccountRole", back_populates="user_account", cascade="all, delete-orphan", passive_deletes=True
    )
    sessions: Mapped[list[UserSession]] = relationship(
        "UserSession", back_populates="user_account", cascade="all, delete-orphan", passive_deletes=True
    )
    action_tokens: Mapped[list[UserActionToken]] = relationship(
        "UserActionToken", back_populates="user_account", cascade="all, delete-orphan", passive_deletes=True
    )
