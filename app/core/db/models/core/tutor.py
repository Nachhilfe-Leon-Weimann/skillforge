from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import TimestampMixin
from .base import CoreBase

if TYPE_CHECKING:
    from .person import Person
    from .tutor_subject import TutorSubject


class Tutor(TimestampMixin, CoreBase):
    __tablename__ = "tutor"

    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.person.party_id", ondelete="CASCADE"),
        primary_key=True,
    )

    person: Mapped[Person] = relationship("Person", back_populates="tutor")

    tutor_subjects: Mapped[list[TutorSubject]] = relationship(
        "TutorSubject",
        back_populates="tutor",
        cascade="all, delete-orphan",
    )
