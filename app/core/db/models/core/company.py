from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import TimestampMixin
from .base import CoreBase

if TYPE_CHECKING:
    from .party import Party


class Company(TimestampMixin, CoreBase):
    __tablename__ = "company"

    party_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.party.id", ondelete="CASCADE"),
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(String, nullable=False)

    party: Mapped[Party] = relationship("Party", back_populates="company")
