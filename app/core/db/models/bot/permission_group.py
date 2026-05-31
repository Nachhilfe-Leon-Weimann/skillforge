from sqlalchemy import Boolean, Text, true
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import TimestampMixin
from .base import BotBase


class PermissionGroup(TimestampMixin, BotBase):
    __tablename__ = "permission_group"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
