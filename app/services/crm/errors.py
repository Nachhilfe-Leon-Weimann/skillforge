"""Domain errors of the CRM services.

Each error derives from a category of the taxonomy in ``app/core/errors.py``, which decides its
HTTP status in the API layer, and gets its ``code`` from the class name. ``message`` is the text a
client sees; the instance message a service raises with stays internal unless the class sets
``expose_message``.
"""

from app.core.errors import ConflictError, NotFoundError


class PartyNotFoundError(NotFoundError):
    """No party exists for the requested party_id."""

    message = "Party not found"


class PersonNotFoundError(NotFoundError):
    """No person exists for the requested party_id; a company's ID counts as missing here."""

    message = "Person not found"


class CompanyNotFoundError(NotFoundError):
    """No company exists for the requested party_id; a person's ID counts as missing here."""

    message = "Company not found"


class PartyInUseError(ConflictError):
    """The party is linked to an external system or a Discord account and must not be orphaned there."""

    message = "Party is linked to external systems"
    # Raised with a client-ready message only: it names the kinds of links, never their identifiers.
    expose_message = True


class SubjectNotFoundError(NotFoundError):
    """No subject exists for the requested subject_id."""

    message = "Subject not found"


class SubjectAlreadyExistsError(ConflictError):
    """Another subject already carries this title (compared case-insensitively)."""

    message = "Subject already exists"


class SubjectInUseError(ConflictError):
    """The subject is still referenced by a student or tutor role."""

    message = "Subject is still assigned to students or tutors"
