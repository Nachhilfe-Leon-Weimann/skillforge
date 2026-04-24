from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import CoreBase

if TYPE_CHECKING:
    from .student import Student
    from .subject import Subject


class StudentSubject(CoreBase):
    __tablename__ = "student_subject"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.student.person_id", ondelete="CASCADE"),
        primary_key=True,
    )

    subject_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("core.subject.id", ondelete="CASCADE"),
        primary_key=True,
    )

    student: Mapped[Student] = relationship("Student", back_populates="student_subjects")
    subject: Mapped[Subject] = relationship("Subject", back_populates="student_subjects")
