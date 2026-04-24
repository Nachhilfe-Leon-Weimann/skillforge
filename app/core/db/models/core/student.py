from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import TimestampMixin
from .base import CoreBase

if TYPE_CHECKING:
    from .person import Person
    from .student_subject import StudentSubject


class PreferredMeetingTool(enum.StrEnum):
    DISCORD = "discord"
    IN_PERSON = "in_person"
    MICROSOFT_TEAMS = "microsoft_teams"
    PHONE = "phone"


class Student(TimestampMixin, CoreBase):
    __tablename__ = "student"

    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.person.party_id", ondelete="CASCADE"),
        primary_key=True,
    )

    preferred_meeting_tool: Mapped[PreferredMeetingTool] = mapped_column(
        Enum(PreferredMeetingTool, name="preferred_meeting_tool"),
        nullable=False,
    )

    person: Mapped[Person] = relationship("Person", back_populates="student")

    student_subjects: Mapped[list[StudentSubject]] = relationship(
        "StudentSubject",
        back_populates="student",
        cascade="all, delete-orphan",
    )
