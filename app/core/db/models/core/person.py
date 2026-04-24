from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import TimestampMixin
from .base import CoreBase

if TYPE_CHECKING:
    from .party import Party
    from .student import Student
    from .tutor import Tutor


class Person(TimestampMixin, CoreBase):
    __tablename__ = "person"

    party_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.party.id", ondelete="CASCADE"),
        primary_key=True,
    )

    firstname: Mapped[str] = mapped_column(String, nullable=False)
    lastname: Mapped[str] = mapped_column(String, nullable=False)

    party: Mapped[Party] = relationship("Party", back_populates="person")

    student: Mapped[Student | None] = relationship(
        "Student",
        back_populates="person",
        uselist=False,
        cascade="all, delete-orphan",
        single_parent=True,
    )

    tutor: Mapped[Tutor | None] = relationship(
        "Tutor",
        back_populates="person",
        uselist=False,
        cascade="all, delete-orphan",
        single_parent=True,
    )
