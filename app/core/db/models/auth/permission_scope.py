from typing import TYPE_CHECKING

from sqlalchemy import true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import TimestampMixin
from .base import AuthBase

if TYPE_CHECKING:
    from .application_client_scope_grant import ApplicationClientScopeGrant


class PermissionScope(TimestampMixin, AuthBase):
    __tablename__ = "permission_scope"

    key: Mapped[str] = mapped_column(primary_key=True)
    description: Mapped[str] = mapped_column(nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=true())

    client_scope_grants: Mapped[list[ApplicationClientScopeGrant]] = relationship(
        "ApplicationClientScopeGrant",
        back_populates="permission_scope",
    )
