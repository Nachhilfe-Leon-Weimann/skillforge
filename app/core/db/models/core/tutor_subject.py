from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import CoreBase

if TYPE_CHECKING:
    from .subject import Subject
    from .tutor import Tutor


class TutorSubject(CoreBase):
    __tablename__ = "tutor_subject"

    tutor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.tutor.person_id", ondelete="CASCADE"),
        primary_key=True,
    )

    subject_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("core.subject.id", ondelete="CASCADE"),
        primary_key=True,
    )

    tutor: Mapped[Tutor] = relationship("Tutor", back_populates="tutor_subjects")
    subject: Mapped[Subject] = relationship("Subject", back_populates="tutor_subjects")
