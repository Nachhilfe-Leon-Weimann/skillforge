import argparse
import re
import sys
import tomllib
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
STABLE_VERSION_PATTERN = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")
SUPPORTED_VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-z]+[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?")


def read_project_version() -> str:
    with PYPROJECT.open("rb") as file:
        return tomllib.load(file)["project"]["version"]


def validate_explicit_version(version: str) -> str:
    if not version:
        sys.exit("version is required when bump is explicit")
    if version.startswith("v"):
        sys.exit("Enter the explicit version without a leading v.")
    if not SUPPORTED_VERSION_PATTERN.fullmatch(version):
        sys.exit(f"Unsupported explicit version format: {version}")
    return version


def compute_next_version(current_version: str, bump: str, explicit_version: str) -> str:
    if bump == "explicit":
        next_version = validate_explicit_version(explicit_version)
        if next_version == current_version:
            sys.exit(f"Next version is unchanged: {next_version}")
        return next_version

    match = STABLE_VERSION_PATTERN.fullmatch(current_version)
    if not match:
        sys.exit(f"Cannot bump non-stable version automatically: {current_version}")

    major, minor, patch = map(int, match.groups())

    if bump == "major":
        next_version = f"{major + 1}.0.0"
    elif bump == "minor":
        next_version = f"{major}.{minor + 1}.0"
    elif bump == "patch":
        next_version = f"{major}.{minor}.{patch + 1}"
    else:
        sys.exit(f"Unsupported bump kind: {bump}")

    if next_version == current_version:
        sys.exit(f"Next version is unchanged: {next_version}")

    return next_version


def replace_project_version(next_version: str) -> None:
    validate_explicit_version(next_version)

    lines = PYPROJECT.read_text().splitlines(keepends=True)
    in_project = False

    for index, line in enumerate(lines):
        stripped = line.strip()

        if stripped == "[project]":
            in_project = True
            continue

        if in_project and stripped.startswith("[") and stripped.endswith("]"):
            break

        if in_project and line.startswith("version = "):
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f'version = "{next_version}"{newline}'
            PYPROJECT.write_text("".join(lines))
            return

    sys.exit("Could not find project.version in pyproject.toml")


def print_version_info(args: argparse.Namespace) -> None:
    current_version = read_project_version()
    next_version = compute_next_version(current_version, args.bump, args.version.strip())

    print(f"current={current_version}")
    print(f"next={next_version}")
    print(f"branch=release/v{next_version}")


def print_release_version(_args: argparse.Namespace) -> None:
    version = validate_explicit_version(read_project_version())
    print(f"version={version}")


def bump_version(args: argparse.Namespace) -> None:
    replace_project_version(args.version.strip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    info_parser = subparsers.add_parser("info")
    info_parser.add_argument("bump", choices=["patch", "minor", "major", "explicit"])
    info_parser.add_argument("version", nargs="?", default="")
    info_parser.set_defaults(func=print_version_info)

    release_parser = subparsers.add_parser("release")
    release_parser.set_defaults(func=print_release_version)

    bump_parser = subparsers.add_parser("bump")
    bump_parser.add_argument("version")
    bump_parser.set_defaults(func=bump_version)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
