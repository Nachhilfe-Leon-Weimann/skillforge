from functools import lru_cache

from skillcore.config import CoreSettings

from .auth.config import AuthSettings
from .db.config import DatabaseSettings


class Settings(CoreSettings):
    auth: AuthSettings
    db: DatabaseSettings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
