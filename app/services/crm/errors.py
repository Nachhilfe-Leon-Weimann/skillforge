"""Domain errors of the CRM services.

Each error derives from a category of the taxonomy in ``app/core/errors.py``, which decides its
HTTP status in the API layer, and gets its ``code`` from the class name. ``message`` is the text a
client sees; the instance message a service raises with stays internal unless the class sets
``expose_message``.
"""

from app.core.errors import ConflictError, NotFoundError


class SubjectNotFoundError(NotFoundError):
    """No subject exists for the requested subject_id."""

    message = "Subject not found"


class SubjectAlreadyExistsError(ConflictError):
    """Another subject already carries this title (compared case-insensitively)."""

    message = "Subject already exists"


class SubjectInUseError(ConflictError):
    """The subject is still referenced by a student or tutor role."""

    message = "Subject is still assigned to students or tutors"
