from skillcore.logging import LoggingSettings as CoreLoggingSettings


class LoggingSettings(CoreLoggingSettings):
    """SkillForge logging settings."""

    app_name: str = "skillforge"
