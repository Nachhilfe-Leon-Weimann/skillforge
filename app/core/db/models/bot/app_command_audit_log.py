import uuid

from sqlalchemy import UUID, BigInteger, Boolean, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import CreatedAtMixin
from .base import BotBase


class AppCommandAuditLog(CreatedAtMixin, BotBase):
    __tablename__ = "app_command_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    discord_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    command_name: Mapped[str] = mapped_column(Text, nullable=False)
    permission_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    permission_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    permission_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    permission_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = BotBase.extend_table_args(
        Index("ix_app_command_audit_log_created_at", "created_at"),
        Index("ix_app_command_audit_log_guild_id_command_name_created_at", "guild_id", "command_name", "created_at"),
        Index("ix_app_command_audit_log_discord_id_created_at", "discord_id", "created_at"),
    )
