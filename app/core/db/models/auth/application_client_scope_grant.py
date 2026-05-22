import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import CreatedAtMixin
from .base import AuthBase

if TYPE_CHECKING:
    from .application_client import ApplicationClient
    from .permission_scope import PermissionScope


class ApplicationClientScopeGrant(CreatedAtMixin, AuthBase):
    __tablename__ = "application_client_scope_grant"

    application_client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth.application_client.id", ondelete="CASCADE"), primary_key=True
    )

    scope_key: Mapped[str] = mapped_column(
        ForeignKey("auth.permission_scope.key", ondelete="RESTRICT"), primary_key=True
    )

    application_client: Mapped[ApplicationClient] = relationship("ApplicationClient", back_populates="scope_grants")
    permission_scope: Mapped[PermissionScope] = relationship("PermissionScope", back_populates="client_scope_grants")
