from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.db.models import DiscordUser, MemberRole


class DiscordUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    discord_id: int
    role: MemberRole
    nick_name: str
    active: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, user: DiscordUser) -> DiscordUserResponse:
        return cls.model_validate(user)
