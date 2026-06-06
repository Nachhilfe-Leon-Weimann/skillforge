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


class CommandEnvValidationError(BotServiceError):
    """The referenced guild/channel/owner for a command env does not exist or is inconsistent."""


class CommandEnvConflictError(BotServiceError):
    """The command env violates a uniqueness rule (e.g. owner already owns one of this kind)."""


class PartyNotFoundError(BotServiceError):
    """No party exists for the requested party_id when linking a Discord account."""


class AccountLinkConflictError(BotServiceError):
    """Linking would violate the one-primary-active-account-per-party invariant."""


class JobNotFoundError(BotServiceError):
    """No job exists for the requested job_id."""


class JobNotClaimedError(BotServiceError):
    """The job is not in the claimed state required for completion/failure."""


class JobNotFailedError(BotServiceError):
    """The job is not in the failed state required for an operator requeue."""


class OperationNotFoundError(BotServiceError):
    """No operation exists for the requested operation_id (and kind)."""


class OperationNotPendingError(BotServiceError):
    """The operation is not in the PREPARED state (already committed/failed, or expired)."""


class TransitionValidationError(BotServiceError):
    """A transition precondition failed (role, active flag, guild/channel context, missing entity)."""


class TransitionConflictError(BotServiceError):
    """A transition conflicts with current state (already exists, capacity reached, wrong channel state)."""
