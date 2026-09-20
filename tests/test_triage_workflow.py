"""triage.yml runs with the org App's key for anyone who opens an issue or a PR.

``issues`` and ``pull_request_target`` execute the workflow from ``main`` with access to secrets, even for an
outsider. The work - and the rules that make it safe: no checkout, no event data in a shell - lives in the
platform's shared workflow, which guards them with its own tests (``skill-platform-workflows``,
``tests/test_workflows.py``). That only holds while this file stays a caller
(skillforge, ``docs/specs/project-intake.md``, platform contract). These tests turn a slip into a red ``check``.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "triage.yml"
SHARED_WORKFLOW = "Nachhilfe-Leon-Weimann/skill-platform-workflows/.github/workflows/triage.yml@v1"


def _lines() -> list[str]:
    return [line for line in WORKFLOW.read_text().splitlines() if not line.lstrip().startswith("#")]


def test_the_workflow_only_calls_the_shared_workflow() -> None:
    assert [line.strip() for line in _lines() if line.lstrip().startswith("uses:")] == [f"uses: {SHARED_WORKFLOW}"]


def test_the_workflow_has_no_steps_of_its_own() -> None:
    # A job with steps would run them from `main` with the App key, on anybody's issue or PR.
    keys = [line.strip().removeprefix("- ") for line in _lines()]

    assert [key for key in keys if key.startswith(("steps:", "runs-on:", "run:"))] == []


def test_the_default_token_has_no_permissions() -> None:
    assert "permissions: {}" in _lines()


def test_the_call_grants_only_what_the_assignment_and_the_reminder_need() -> None:
    lines = _lines()
    start = lines.index("    permissions:") + 1
    granted = []
    for line in lines[start:]:
        if not line.startswith("      "):
            break
        granted.append(line.strip())

    assert granted == ["issues: write", "pull-requests: write"]
