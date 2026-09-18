"""Vocabulary shared by the CRM services and the CRM API.

The API imports from here, never the reverse: the services stay free of ``app.api``.
"""

from collections.abc import Set
from dataclasses import dataclass, field
from enum import Enum, StrEnum

from pydantic import validate_email

from app.core.db.models import ContactInfoType, PreferredMeetingTool


class PartyRole(StrEnum):
    """A role a person can hold; companies hold none."""

    STUDENT = "student"
    TUTOR = "tutor"


class RelationDirection(StrEnum):
    """A relation seen from one of its two parties: that party is the ``from`` side, or the ``to`` side."""

    OUTGOING = "outgoing"
    INCOMING = "incoming"


class Unset(Enum):
    """Type of ``UNSET``: "leave this nullable field as it is", where ``None`` already means "clear it"."""

    UNSET = "unset"


UNSET = Unset.UNSET


@dataclass(frozen=True)
class NewContactInfo:
    """A contact info to attach to a party; ``value`` may still be raw, the services normalize it."""

    type: ContactInfoType
    value: str
    label: str | None = None


@dataclass(frozen=True)
class StudentRoleData:
    """The student role to give a person; ``subject_ids`` is the whole set, not a delta."""

    preferred_meeting_tool: PreferredMeetingTool
    subject_ids: Set[int] = field(default_factory=frozenset)


@dataclass(frozen=True)
class TutorRoleData:
    """The tutor role to give a person; ``subject_ids`` is the whole set, not a delta."""

    subject_ids: Set[int] = field(default_factory=frozenset)


# The longest e-mail address there is (RFC 5321) and far beyond any phone number. The value sits in
# the index of uq_contact_info, whose rows Postgres limits to about 2700 bytes.
MAX_CONTACT_VALUE_LENGTH = 254


def require_storable_text(value: str) -> str:
    """Return ``value`` if Postgres can store it as ``text``, or raise ``ValueError``.

    Postgres rejects U+0000, and the driver cannot encode a lone UTF-16 surrogate (which JSON can
    carry as an escape). Left to the database both surface as a 500 - and the driver's message
    repeats the value. The message here never does.
    """
    if "\x00" in value:
        raise ValueError("Value must not contain the NUL character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Value contains characters that are not valid Unicode") from None
    return value


def normalize_contact_value(type: ContactInfoType, value: str) -> str:
    """Return the canonical form of a contact value, or raise ``ValueError`` if it is not valid.

    An e-mail is lowercased and validated the way ``EmailStr`` does; a phone number loses all
    whitespace and must not be empty. The result is a fixed point - normalizing it again changes
    nothing - because the create routes normalize in the request model and again in the service.
    The messages never repeat the value: it is personal data.
    """
    require_storable_text(value)
    match type:
        case ContactInfoType.EMAIL:
            try:
                # Lowercase first: the validator then checks - and NFC-normalizes - the form that is
                # stored. Lowercasing its result instead can lengthen it or break its normalization.
                _, normalized = validate_email(value.lower())
            except ValueError:
                raise ValueError("Value is not a valid e-mail address") from None
        case ContactInfoType.PHONE:
            normalized = "".join(value.split())
            if not normalized:
                raise ValueError("Value is not a valid phone number: it must not be empty")

    if len(normalized) > MAX_CONTACT_VALUE_LENGTH:
        raise ValueError(f"Value must not be longer than {MAX_CONTACT_VALUE_LENGTH} characters")
    return normalized
