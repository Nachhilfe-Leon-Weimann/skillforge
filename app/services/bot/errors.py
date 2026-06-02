class BotServiceError(Exception):
    """Base class for bot service-layer domain errors."""


class PrincipalNotFoundError(BotServiceError):
    """No Discord user is registered for the requested discord_id."""


class TutorContextNotFoundError(BotServiceError):
    """No tutor workspace exists for the requested guild/tutor."""


class StudentContextNotFoundError(BotServiceError):
    """No student workspace exists for the requested guild/student."""


class CommandEnvNotFoundError(BotServiceError):
    """No command env channel matches the requested coordinates."""


class JobNotFoundError(BotServiceError):
    """No job exists for the requested job_id."""


class JobNotClaimedError(BotServiceError):
    """The job is not in the claimed state required for completion/failure."""
