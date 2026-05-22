import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import UUID, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import CreatedAtMixin
from .base import AuthBase

if TYPE_CHECKING:
    from .application_client import ApplicationClient


class ApplicationClientSecret(CreatedAtMixin, AuthBase):
    __tablename__ = "application_client_secret"
    __table_args__ = AuthBase.extend_table_args(
        Index("ix_application_client_secret_application_client_id", "application_client_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth.application_client.id", ondelete="CASCADE"), nullable=False
    )
    secret_hash: Mapped[str] = mapped_column(nullable=False)
    label: Mapped[str | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    application_client: Mapped[ApplicationClient] = relationship("ApplicationClient", back_populates="secrets")
