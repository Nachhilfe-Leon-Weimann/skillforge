import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import CreatedAtMixin
from .base import AuthBase

if TYPE_CHECKING:
    from .user_account import UserAccount


class UserAccountRoleName(enum.StrEnum):
    ADMIN = "admin"


class UserAccountRole(CreatedAtMixin, AuthBase):
    """A stored role held by a user account (decision J). Every other role is derived from the CRM
    and never stored here."""

    __tablename__ = "user_account_role"

    user_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth.user_account.id", ondelete="CASCADE"), primary_key=True
    )

    role: Mapped[UserAccountRoleName] = mapped_column(
        Enum(
            UserAccountRoleName,
            name="user_account_role_name",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        primary_key=True,
    )

    user_account: Mapped[UserAccount] = relationship("UserAccount", back_populates="roles")
