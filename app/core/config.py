from functools import lru_cache

from pydantic import Field
from skillcore.config import CoreSettings

from .auth.config import AuthSettings
from .db.config import DatabaseSettings
from .logging import LoggingSettings


class Settings(CoreSettings):
    auth: AuthSettings
    db: DatabaseSettings
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
