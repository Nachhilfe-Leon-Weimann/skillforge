"""Vocabulary shared by the CRM services and the CRM API.

The API imports from here, never the reverse: the services stay free of ``app.api``.
"""

from collections.abc import Set
from dataclasses import dataclass, field
from enum import StrEnum

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat, ValidationResult
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


# The region a phone number written in its national form ("0171 ...") is read with.
DEFAULT_PHONE_REGION = "DE"


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

    An e-mail is lowercased and validated the way ``EmailStr`` does; a phone number becomes its
    E.164 form (``+491711234567``). The result is a fixed point - normalizing it again changes
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
            normalized = _e164(value)

    if len(normalized) > MAX_CONTACT_VALUE_LENGTH:
        raise ValueError(f"Value must not be longer than {MAX_CONTACT_VALUE_LENGTH} characters")
    return normalized


def _e164(value: str) -> str:
    """Return the E.164 form of a phone number; a national form is read with ``DEFAULT_PHONE_REGION``.

    The number must be *possible* for its region, deliberately not *valid*: number ranges are opened
    faster than metadata ships, and a real number that cannot be stored is worse than a typo that can.
    What E.164 cannot carry is rejected instead of dropped: the parser would turn letters into digits
    or skip them, and formatting loses an extension without a word.
    """
    invalid = ValueError("Value is not a valid phone number")
    try:
        # The parser knows the space but no tab or line break inside a number.
        number = phonenumbers.parse(" ".join(value.split()), DEFAULT_PHONE_REGION)
    except NumberParseException:
        raise invalid from None

    if number.extension:
        raise ValueError("Value is not a valid phone number: an extension is not supported, put it into the label")
    if any(char.isalpha() for char in value):
        raise invalid
    # IS_POSSIBLE_LOCAL_ONLY is not enough: a number without its area code has no E.164 form.
    if phonenumbers.is_possible_number_with_reason(number) != ValidationResult.IS_POSSIBLE:
        raise invalid

    return phonenumbers.format_number(number, PhoneNumberFormat.E164)
