from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import UUID, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import TimestampMixin
from .base import CoreBase

if TYPE_CHECKING:
    from .party import Party


class ContactInfoType(enum.StrEnum):
    EMAIL = "email"
    PHONE = "phone"


class ContactInfo(TimestampMixin, CoreBase):
    __tablename__ = "contact_info"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    party_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.party.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    type: Mapped[ContactInfoType] = mapped_column(
        Enum(ContactInfoType, name="contact_info_type"),
        nullable=False,
    )

    value: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str | None] = mapped_column(String, nullable=True)

    party: Mapped[Party] = relationship("Party", back_populates="contact_infos")

    __table_args__ = CoreBase.extend_table_args(
        UniqueConstraint("party_id", "type", "value", name="uq_contact_info"),
    )
