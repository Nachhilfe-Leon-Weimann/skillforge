import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _collapse_lines(lines: list[int]) -> str:
    if not lines:
        return ""

    ranges: list[str] = []
    start = previous = lines[0]

    for line in lines[1:]:
        if line == previous + 1:
            previous = line
            continue

        ranges.append(f"{start}-{previous}" if start != previous else str(start))
        start = previous = line

    ranges.append(f"{start}-{previous}" if start != previous else str(start))
    return ", ".join(ranges)


def _truncate(value: str, limit: int = 120) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def main() -> int:
    coverage_file = Path(sys.argv[1] if len(sys.argv) > 1 else "coverage.xml")
    if not coverage_file.exists():
        print(f"Coverage report not found: {coverage_file}", file=sys.stderr)
        return 1

    root = ET.parse(coverage_file).getroot()

    lines_valid = int(root.attrib.get("lines-valid", "0"))
    lines_covered = int(root.attrib.get("lines-covered", "0"))
    line_rate = float(root.attrib.get("line-rate", "0"))
    total_percent = line_rate * 100
    missing_total = lines_valid - lines_covered

    files: list[tuple[int, str, float, str]] = []
    for class_node in root.findall(".//class"):
        filename = class_node.attrib["filename"]
        file_rate = float(class_node.attrib.get("line-rate", "0")) * 100
        missing_lines = [
            int(line.attrib["number"])
            for line in class_node.findall("./lines/line")
            if int(line.attrib.get("hits", "0")) == 0
        ]

        if missing_lines:
            files.append((len(missing_lines), filename, file_rate, _collapse_lines(missing_lines)))

    files.sort(reverse=True)

    lines = [
        "## Test coverage",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Line coverage | {total_percent:.1f}% |",
        f"| Covered lines | {lines_covered}/{lines_valid} |",
        f"| Missing lines | {missing_total} |",
        "",
    ]

    if files:
        lines.extend([
            "### Files with missing coverage",
            "",
            "| File | Coverage | Missing lines |",
            "| --- | ---: | --- |",
        ])
        for missing_count, filename, file_rate, missing_lines in files:
            lines.append(f"| `{filename}` | {file_rate:.1f}% | {_truncate(missing_lines)} ({missing_count}) |")
    else:
        lines.append("All reported files are fully covered.")

    summary = "\n".join(lines) + "\n"
    print(summary)

    if github_step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(github_step_summary).write_text(summary, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
