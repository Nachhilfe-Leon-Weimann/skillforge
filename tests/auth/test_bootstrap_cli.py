"""The operator commands parse their arguments before they touch the database."""

from pathlib import Path

import pytest

from app.core.auth.bootstrap import build_parser

JUSTFILE = Path(__file__).resolve().parents[2] / "justfile"


def test_the_skillbot_command_takes_no_arguments():
    assert build_parser().parse_args(["skillbot"]).command == "skillbot"


def test_the_client_command_takes_a_client_id_and_a_scope_list_per_mode():
    arguments = build_parser().parse_args([
        "client",
        "operator",
        "--application",
        "auth:users:login crm:write",
        "--delegated",
        " account:self  crm:read ",
    ])

    assert arguments.command == "client"
    assert arguments.client_id == "operator"
    assert arguments.application == frozenset({"auth:users:login", "crm:write"})
    assert arguments.delegated == frozenset({"account:self", "crm:read"})


def test_an_empty_scope_list_grants_nothing_in_that_mode():
    arguments = build_parser().parse_args(["client", "operator", "--application", "", "--delegated", "crm:read"])

    assert arguments.application == frozenset()


@pytest.mark.parametrize(
    ("given", "missing"),
    [(["--delegated", "crm:read"], "--application"), (["--application", "crm:write"], "--delegated")],
)
def test_the_client_command_needs_both_scope_lists(given: list[str], missing: str, capsys):
    with pytest.raises(SystemExit) as exit_code:
        build_parser().parse_args(["client", "operator", *given])

    assert exit_code.value.code == 2
    assert missing in capsys.readouterr().err


def test_the_just_recipes_run_the_subcommands():
    """`bootstrap-client` hands its arguments on as they were quoted: `{{ args }}` would split a scope list."""
    justfile = JUSTFILE.read_text()

    assert "bootstrap-skillbot:\n    uv run python -m app.core.auth.bootstrap skillbot\n" in justfile
    assert (
        '[positional-arguments]\nbootstrap-client *args:\n    uv run python -m app.core.auth.bootstrap client "$@"\n'
        in justfile
    )
