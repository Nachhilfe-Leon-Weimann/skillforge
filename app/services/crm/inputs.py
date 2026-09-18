"""Vocabulary shared by the CRM services and the CRM API.

The API imports from here, never the reverse: the services stay free of ``app.api``.
"""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import validate_email

from app.core.db.models import ContactInfoType


class PartyRole(StrEnum):
    """A role a person can hold; companies hold none."""

    STUDENT = "student"
    TUTOR = "tutor"


@dataclass(frozen=True)
class NewContactInfo:
    """A contact info to attach to a party; ``value`` may still be raw, the services normalize it."""

    type: ContactInfoType
    value: str
    label: str | None = None


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

    An e-mail is validated the way ``EmailStr`` does and lowercased; a phone number loses all
    whitespace and must not be empty. The messages never repeat the value: it is personal data.
    """
    require_storable_text(value)
    match type:
        case ContactInfoType.EMAIL:
            try:
                _, email = validate_email(value)
            except ValueError:
                raise ValueError("Value is not a valid e-mail address") from None
            return email.lower()
        case ContactInfoType.PHONE:
            phone = "".join(value.split())
            if not phone:
                raise ValueError("Value is not a valid phone number: it must not be empty")
            return phone
