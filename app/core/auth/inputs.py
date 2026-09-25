"""Vocabulary shared by the auth services, the auth API and the operator commands.

The API and ``just bootstrap-admin`` import from here, never the reverse: this module stays free of
``app.api`` and of the services.
"""

from typing import Annotated

from pydantic import AfterValidator, EmailStr, StringConstraints, validate_email

MAX_EMAIL_LENGTH = 254
"""The longest e-mail address there is (RFC 5321); the column is ``text``, the limit documents the rule."""


def normalize_email(email: str) -> str:
    """Return the one canonical form of a login e-mail address (user-authentication spec, decision D).

    Parsed the way ``LoginEmail`` parses it - a display name (``Anna <anna@example.org>``) is dropped, the
    domain is read as Unicode (``xn--bcher-kva.de`` is ``bücher.de``), the text is NFC-normalized - then
    lowercased. Every spelling of one address yields the same value, the one that is stored and looked up;
    the form ``ck_user_account_email_lowercase`` accepts. Raises ``ValueError`` for what is no address.
    """
    _, address = validate_email(email)
    return address.lower()


LoginEmail = Annotated[EmailStr, StringConstraints(max_length=MAX_EMAIL_LENGTH), AfterValidator(normalize_email)]
"""A login e-mail address, parsed into its canonical form: for the API schemas and ``just bootstrap-admin`` alike.

A plain assignment rather than a PEP 695 alias: the API inlines the constraints at its field, where an
alias would become a schema of its own.
"""
