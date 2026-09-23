"""The operator commands parse their arguments before they touch the database."""

import uuid

import pytest

from app.core.auth.bootstrap import build_parser
from app.core.auth.inputs import normalize_email

PARTY_ID = "7d9f4f3e-1c2b-4a5d-9e8f-0a1b2c3d4e5f"


def test_the_admin_command_takes_a_party_and_an_e_mail_address():
    arguments = build_parser().parse_args(["admin", "--party-id", PARTY_ID, "--email", "Anna@Example.org"])

    assert arguments.command == "admin"
    assert arguments.party_id == uuid.UUID(PARTY_ID)
    # Accepted as typed; the service stores it trimmed and lowercased.
    assert normalize_email(arguments.email) == "anna@example.org"


@pytest.mark.parametrize("email", ["anna", "anna@", "@example.org", "anna example.org", "x" * 250 + "@example.org"])
def test_an_address_the_api_would_reject_is_refused_before_the_account_exists(email: str, capsys):
    """A typo on the *first* admin could only be fixed through the API it is needed for."""
    with pytest.raises(SystemExit) as exit_code:
        build_parser().parse_args(["admin", "--party-id", PARTY_ID, "--email", email])

    assert exit_code.value.code == 2
    assert "e-mail address" in capsys.readouterr().err


def test_an_unparseable_party_id_is_refused(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["admin", "--party-id", "not-a-uuid", "--email", "anna@example.org"])


def test_the_skillbot_command_takes_no_arguments():
    assert build_parser().parse_args(["skillbot"]).command == "skillbot"
