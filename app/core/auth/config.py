from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict
from skillcore.config import CoreSettings


class AuthSettings(CoreSettings):
    """Settings for the authentication module."""

    issuer: str = "skillforge"
    audience: str = "skillforge-api"
    secret_key: SecretStr
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    invitation_expire_hours: int = 168
    password_reset_expire_hours: int = 24

    model_config = SettingsConfigDict(
        env_prefix="AUTH__",
    )
