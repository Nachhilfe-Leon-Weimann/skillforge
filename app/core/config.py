from functools import lru_cache

from skillcore.config import CoreSettings

from .db.config import DatabaseSettings


class Settings(CoreSettings):
    db: DatabaseSettings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
