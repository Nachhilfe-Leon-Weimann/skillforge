from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared import TimestampMixin
from .base import CoreBase

if TYPE_CHECKING:
    from .party import Party


class PartyRelationType(enum.StrEnum):
    PARENT_OF = "parent_of"
    TUTOR_OF = "tutor_of"
    PAYS_FOR = "pays_for"


class PartyRelation(TimestampMixin, CoreBase):
    __tablename__ = "party_relation"

    from_party_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.party.id", ondelete="CASCADE"),
        primary_key=True,
    )

    to_party_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.party.id", ondelete="CASCADE"),
        primary_key=True,
    )

    type: Mapped[PartyRelationType] = mapped_column(
        Enum(PartyRelationType, name="party_relation_type"),
        primary_key=True,
    )

    from_party: Mapped[Party] = relationship(
        "Party",
        back_populates="outgoing_relations",
        foreign_keys=[from_party_id],
    )

    to_party: Mapped[Party] = relationship(
        "Party",
        back_populates="incoming_relations",
        foreign_keys=[to_party_id],
    )
