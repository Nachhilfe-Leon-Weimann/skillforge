"""Guard against documentation drift.

Two checks over every Markdown file in the repo (outside fenced code blocks):

1. **No line-number anchors into source** (``file.py:123`` or ``file.py#L123``). Line numbers
   rot on the next edit; reference code by symbol name instead (see CLAUDE.md).
2. **No dead relative links.** Every ``[text](path)`` pointing at a repo-relative path must
   resolve to an existing file or directory.

Run via ``just docs-check`` (part of ``just check``). Exits non-zero on any violation, listing
each with a ``path:line`` locator.
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directory names we never descend into.
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".ruff_cache", ".mypy_cache"}

# ``file.py:123`` or ``file.py#L123`` - a line anchor into source.
LINE_ANCHOR = re.compile(r"[\w./-]+\.py(?::\d+|#L\d+)")

# Markdown inline link: capture the target inside [text](target).
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# Opening/closing fence of a code block.
FENCE = re.compile(r"^\s*```")

# Link targets that are not repo-relative paths.
EXTERNAL_PREFIXES = ("http://", "https://", "#", "mailto:", "tel:")


def markdown_files() -> list[Path]:
    files = [
        path
        for path in REPO_ROOT.rglob("*.md")
        if not any(part in SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts)
    ]
    return sorted(files)


def non_fenced_lines(text: str):
    """Yield (line_number, line) for lines outside ``` fenced code blocks."""
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield lineno, line


def check_file(path: Path) -> list[str]:
    rel = path.relative_to(REPO_ROOT).as_posix()
    problems: list[str] = []

    for lineno, line in non_fenced_lines(path.read_text(encoding="utf-8")):
        for match in LINE_ANCHOR.finditer(line):
            problems.append(
                f"{rel}:{lineno}: line-number anchor `{match.group()}` - reference the symbol instead (see CLAUDE.md)"
            )

        for match in LINK.finditer(line):
            target = match.group(1).strip()
            if target.startswith(EXTERNAL_PREFIXES):
                continue
            # Drop a trailing #fragment or ?query before resolving.
            target = re.split(r"[#?]", target, maxsplit=1)[0]
            if not target:
                continue
            if not (path.parent / target).resolve().exists():
                problems.append(f"{rel}:{lineno}: dead link -> {target}")

    return problems


def main() -> None:
    files = markdown_files()
    problems = [problem for path in files for problem in check_file(path)]

    if problems:
        print("docs-check failed:\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            f"\n{len(problems)} issue(s) across {len(files)} Markdown file(s). "
            "Reference code by symbol (not line number) and fix dead links; see CLAUDE.md.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"docs-check: {len(files)} Markdown files OK (no line anchors, no dead links).")


if __name__ == "__main__":
    main()
