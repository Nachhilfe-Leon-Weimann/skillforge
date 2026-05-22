import uuid

from sqlalchemy import UUID, Index
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import CreatedAtMixin
from .base import AuthBase


class AuthAuditLog(CreatedAtMixin, AuthBase):
    __tablename__ = "auth_audit_log"
    __table_args__ = AuthBase.extend_table_args(
        Index("ix_auth_audit_log_created_at", "created_at"),
        Index("ix_auth_audit_log_principal_type_id_created_at", "principal_type", "principal_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_type: Mapped[str | None] = mapped_column(nullable=True)
    principal_id: Mapped[str | None] = mapped_column(nullable=True)
    event_type: Mapped[str] = mapped_column(nullable=False)
    success: Mapped[bool] = mapped_column(nullable=False)
    detail: Mapped[str | None] = mapped_column(nullable=True)
