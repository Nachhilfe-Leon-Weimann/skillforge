from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import CoreBase

if TYPE_CHECKING:
    from .student_subject import StudentSubject
    from .tutor_subject import TutorSubject


class Subject(CoreBase):
    __tablename__ = "subject"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String, nullable=False)

    tutor_subjects: Mapped[list[TutorSubject]] = relationship(
        "TutorSubject",
        back_populates="subject",
        cascade="all, delete-orphan",
    )

    student_subjects: Mapped[list[StudentSubject]] = relationship(
        "StudentSubject",
        back_populates="subject",
        cascade="all, delete-orphan",
    )
