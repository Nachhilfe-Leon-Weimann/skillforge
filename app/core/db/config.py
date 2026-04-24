from pydantic import PostgresDsn
from pydantic_settings import SettingsConfigDict
from skillcore.config import CoreSettings


class DatabaseSettings(CoreSettings):
    """
    Database settings loaded from environment/.env.

    Expected keys:
        - DB__URL=postgresql+asyncpg://...
    """

    url: PostgresDsn

    model_config = SettingsConfigDict(
        env_prefix="DB__",
    )
