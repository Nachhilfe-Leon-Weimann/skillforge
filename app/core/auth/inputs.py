"""Vocabulary shared by the auth services, the auth API and the operator commands.

The API and ``just bootstrap-admin`` import from here, never the reverse: this module stays free of
``app.api`` and of the services.
"""

from typing import Annotated

from pydantic import EmailStr, StringConstraints

# The longest e-mail address there is (RFC 5321); the column is `text`, the limit documents the rule.
MAX_EMAIL_LENGTH = 254

LoginEmail = Annotated[EmailStr, StringConstraints(max_length=MAX_EMAIL_LENGTH)]
"""What counts as a login e-mail address, for the API schema and for `just bootstrap-admin` alike.

A plain assignment rather than a PEP 695 alias: the API inlines the constraints at its field, where
an alias would become a schema of its own.
"""


def normalize_email(email: str) -> str:
    """The stored form of a login e-mail address: trimmed and lowercased (decision D).

    A fixed point, and the form the ``ck_user_account_email_lowercase`` check constraint accepts.
    """
    return email.strip().lower()
