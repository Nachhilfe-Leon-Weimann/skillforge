"""compose.yml pins the deployed image version and release-please keeps the pin current.

release-please's generic updater rewrites the first semver on every line annotated with
``x-release-please-version``. A line that lost the annotation, or a file missing from ``extra-files``, is a
silent no-op: the release would deploy the previous image. These tests turn that into a red ``check``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED_IMAGE_LINE = re.compile(
    r"^\s+image: ghcr\.io/nachhilfe-leon-weimann/skillforge:v(?P<version>\d+\.\d+\.\d+) # x-release-please-version$"
)


def _image_lines() -> list[str]:
    lines = (ROOT / "compose.yml").read_text().splitlines()
    return [line for line in lines if line.lstrip().startswith("image:")]


def test_every_image_line_is_pinned_and_annotated() -> None:
    image_lines = _image_lines()

    assert image_lines
    assert [line for line in image_lines if not PINNED_IMAGE_LINE.match(line)] == []


def test_every_service_runs_the_same_version() -> None:
    versions = {match["version"] for line in _image_lines() if (match := PINNED_IMAGE_LINE.match(line))}

    assert len(versions) == 1


def test_release_please_rewrites_compose_yml() -> None:
    config = json.loads((ROOT / "release-please-config.json").read_text())

    assert {"type": "generic", "path": "compose.yml"} in config["packages"]["."]["extra-files"]
