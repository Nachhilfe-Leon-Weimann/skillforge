from pydantic_settings import BaseSettings


class CoreSettings(BaseSettings):
    @classmethod
    def from_env[S](cls: type[S]) -> S:
        return cls()  # pyright: ignore


class Settings(CoreSettings):
    pass
