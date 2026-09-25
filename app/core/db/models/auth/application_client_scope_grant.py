import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import CreatedAtMixin
from .base import AuthBase

if TYPE_CHECKING:
    from .application_client import ApplicationClient
    from .permission_scope import PermissionScope


class GrantMode(enum.StrEnum):
    APPLICATION = "application"
    DELEGATED = "delegated"


class ApplicationClientScopeGrant(CreatedAtMixin, AuthBase):
    """A scope granted to a client in one mode (ADR 0008): ``application`` for the client itself,
    ``delegated`` as the most it may do for a person. The mode is part of the key, so one scope can
    be granted in both."""

    __tablename__ = "application_client_scope_grant"

    application_client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth.application_client.id", ondelete="CASCADE"), primary_key=True
    )

    scope_key: Mapped[str] = mapped_column(
        ForeignKey("auth.permission_scope.key", ondelete="RESTRICT"), primary_key=True
    )

    mode: Mapped[GrantMode] = mapped_column(
        Enum(
            GrantMode,
            name="grant_mode",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        primary_key=True,
        default=GrantMode.APPLICATION,
        server_default=text("'application'"),
    )

    application_client: Mapped[ApplicationClient] = relationship("ApplicationClient", back_populates="scope_grants")
    permission_scope: Mapped[PermissionScope] = relationship("PermissionScope", back_populates="client_scope_grants")
