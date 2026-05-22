import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import UUID, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import TimestampMixin
from .base import AuthBase

if TYPE_CHECKING:
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
        Enum(ApplicationClientStatus, name="application_client_status"),
        nullable=False,
        default=ApplicationClientStatus.ACTIVE,
    )

    secrets: Mapped[list[ApplicationClientSecret]] = relationship(
        "ApplicationClientSecret",
        back_populates="application_client",
        cascade="all, delete-orphan",
    )
