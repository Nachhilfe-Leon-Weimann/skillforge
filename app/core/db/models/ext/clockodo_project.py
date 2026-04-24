from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import TimestampMixin
from .base import ExtBase

if TYPE_CHECKING:
    from ..core.party import Party


class ClockodoProject(TimestampMixin, ExtBase):
    __tablename__ = "clockodo_project"

    clockodo_project_id: Mapped[str] = mapped_column(String, primary_key=True)

    party_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.party.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    party: Mapped[Party] = relationship("Party", back_populates="clockodo_project")
