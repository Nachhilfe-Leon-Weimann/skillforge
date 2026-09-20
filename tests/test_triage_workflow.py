"""triage.yml runs with the org App's key for anyone who opens an issue or a PR.

``issues`` and ``pull_request_target`` execute the workflow from ``main`` with access to secrets, even for an
outsider. That is only safe while the workflow never checks out code and never lets event data reach a shell
(``docs/specs/project-intake.md``, platform contract). These tests turn a slip into a red ``check``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "triage.yml"
PINNED_ACTION = re.compile(r"^\s+uses: [\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
RUN_BLOCK = re.compile(r"^(?P<indent>\s*)run: \|$")


def _lines() -> list[str]:
    return WORKFLOW.read_text().splitlines()


def _run_block_lines() -> list[str]:
    script: list[str] = []
    block_indent: int | None = None
    for line in _lines():
        if block_indent is not None and (not line.strip() or len(line) - len(line.lstrip()) > block_indent):
            script.append(line)
            continue
        match = RUN_BLOCK.match(line)
        block_indent = len(match["indent"]) if match else None
    return script


def test_the_workflow_never_checks_out_code() -> None:
    assert [line for line in _lines() if "actions/checkout" in line] == []


def test_no_expression_reaches_a_shell() -> None:
    script = _run_block_lines()

    assert script
    assert [line for line in script if "${{" in line] == []


def test_the_default_token_has_no_permissions() -> None:
    assert "permissions: {}" in _lines()


def test_the_app_token_is_narrowed_to_the_board() -> None:
    requested = {line.strip() for line in _lines() if line.lstrip().startswith("permission-")}

    assert requested == {
        "permission-organization-projects: write",
        "permission-issues: read",
        "permission-pull-requests: read",
    }


def test_every_action_is_pinned_by_sha() -> None:
    uses = [line for line in _lines() if line.lstrip().startswith("uses:")]

    assert uses
    assert [line for line in uses if not PINNED_ACTION.match(line)] == []


def test_the_module_is_configured_in_one_line() -> None:
    assert len([line for line in _lines() if re.match(r"^  MODULE: \w+$", line)]) == 1
