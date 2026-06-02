from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import TimestampMixin
from .base import ExtBase

if TYPE_CHECKING:
    from ..core.party import Party


class DiscordAccount(TimestampMixin, ExtBase):
    __tablename__ = "discord_account"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)

    party_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.party.id", ondelete="CASCADE"),
        nullable=False,
    )

    is_primary: Mapped[bool] = mapped_column(nullable=False, default=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)

    __table_args__ = ExtBase.extend_table_args(
        Index("ix_discord_account_party_id_active", "party_id", "active"),
        Index(
            "uq_discord_account_party_id_primary_active",
            "party_id",
            unique=True,
            postgresql_where=is_primary.is_(true()) & active.is_(true()),
        ),
    )

    party: Mapped[Party] = relationship("Party", back_populates="discord_account")
