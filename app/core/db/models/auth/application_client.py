import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import UUID, Enum, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import TimestampMixin
from .base import AuthBase

if TYPE_CHECKING:
    from .application_client_scope_grant import ApplicationClientScopeGrant
    from .application_client_secret import ApplicationClientSecret


class ApplicationClientStatus(enum.StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ApplicationClient(TimestampMixin, AuthBase):
    __tablename__ = "application_client"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    client_id: Mapped[str] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)

    status: Mapped[ApplicationClientStatus] = mapped_column(
        Enum(
            ApplicationClientStatus,
            name="application_client_status",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
        default=ApplicationClientStatus.ACTIVE,
        server_default=text("'active'"),
    )

    secrets: Mapped[list[ApplicationClientSecret]] = relationship(
        "ApplicationClientSecret",
        back_populates="application_client",
        cascade="all, delete-orphan",
    )

    scope_grants: Mapped[list[ApplicationClientScopeGrant]] = relationship(
        "ApplicationClientScopeGrant",
        back_populates="application_client",
        cascade="all, delete-orphan",
    )
