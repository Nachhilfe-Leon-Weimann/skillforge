"""Vocabulary shared by the CRM services and the CRM API.

The API imports from here, never the reverse: the services stay free of ``app.api``.
"""

from dataclasses import dataclass

from pydantic import validate_email

from app.core.db.models import ContactInfoType


@dataclass(frozen=True)
class NewContactInfo:
    """A contact info to attach to a party; ``value`` may still be raw, the services normalize it."""

    type: ContactInfoType
    value: str
    label: str | None = None


def normalize_contact_value(type: ContactInfoType, value: str) -> str:
    """Return the canonical form of a contact value, or raise ``ValueError`` if it is not valid.

    An e-mail is validated the way ``EmailStr`` does and lowercased; a phone number loses all
    whitespace and must not be empty. The messages never repeat the value: it is personal data.
    """
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
