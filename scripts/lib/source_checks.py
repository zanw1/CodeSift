"""Collect source facts and duplicate-block candidates without declaring semantic defects."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys

from dependencies import read_dependencies
from detect_lang import (
    BUILD_NAMES,
    IGNORED_DIRS,
    SOURCE_SUFFIXES,
    add_project_arguments,
    detect_languages,
    positive_int,
    print_report,
    scan_files,
)


def source_paths(files: list[Path], lang: str) -> list[Path]:
    """Select source suffixes without inferring reachability from file extensions."""
    suffixes = SOURCE_SUFFIXES.get(lang, set().union(*SOURCE_SUFFIXES.values()))
    return [path for path in files if path.suffix.lower() in suffixes]


def read_sources(
    paths: list[Path], root: Path, warnings: list[str]
) -> dict[str, list[str]]:
    """Read each source once and expose read or decoding failures as coverage gaps."""
    sources = {}
    for path in paths:
        try:
            sources[str(path.relative_to(root))] = path.read_text().splitlines()
        except (OSError, UnicodeError) as error:
            warnings.append(f"{path.relative_to(root)}: {type(error).__name__}")
    return sources


def collect_facts(
    root: Path, files: list[Path], lang: str, warnings: list[str]
) -> dict:
    """Collect bounded entry candidates and file counts without claiming complete framework coverage."""
    sources = read_sources(source_paths(files, lang), root, warnings)
    pattern = re.compile(
        r"\bfn\s+main\s*\(|\bint\s+main\s*\(|if\s+__name__\s*==\s*['\"]__main__['\"]"
    )
    entries = [
        {"file": file, "line": number, "kind": "source_entry_candidate"}
        for file, lines in sources.items()
        for number, line in enumerate(lines, 1)
        if pattern.search(line)
    ]
    package = root / "package.json"
    if package.is_file():
        try:
            data = json.loads(package.read_text())
            for key in ("main", "module", "bin", "exports", "scripts"):
                if key in data:
                    entries.append(
                        {
                            "file": "package.json",
                            "field": key,
                            "kind": "inspect_manifest_entry",
                        }
                    )
        except (OSError, ValueError):
            warnings.append("package.json could not be parsed for entry candidates")
    manifests = [str(p.relative_to(root)) for p in files if p.name in BUILD_NAMES]
    dependencies = read_dependencies(root, lang)
    return {
        "status": "completed" if not warnings else "incomplete",
        "complete": not warnings,
        "project": {
            "root": str(root),
            "language": lang,
            "detected_languages": detect_languages(root, files),
        },
        "codebase": {
            "scanned_files": len(files),
            "source_files": len(sources),
            "source_lines": sum(len(lines) for lines in sources.values()),
        },
        "build_files": manifests,
        "entry_point_candidates": entries,
        "dependency_inventory": dependencies,
        "warnings": warnings,
        "coverage": {
            "excluded_directories": sorted(IGNORED_DIRS),
            "follows_symlinks": False,
            "entry_points": "heuristic candidates; framework/config/export roots require inspection",
        },
    }


def meaningful_lines(lines: list[str], suffix: str) -> list[tuple[int, str]]:
    """Retain substantive lines and their locations, excluding punctuation-only and comment lines."""
    output = []
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if not text or not re.search(r"\w", text):
            continue
        if text.startswith(("//", "/*", "*", "--")) or (
            suffix == ".py" and text.startswith("#")
        ):
            continue
        output.append((number, text))
    return output


def duplicate_candidates(
    sources: dict[str, list[str]], threshold: int, max_results: int
) -> tuple[list[dict], bool]:
    """Index ordered substantive blocks once instead of repeatedly comparing every file pair."""
    blocks: dict[tuple[str, ...], list[tuple[str, int, int]]] = defaultdict(list)
    for file, lines in sources.items():
        filtered = meaningful_lines(lines, Path(file).suffix)
        for start in range(max(0, len(filtered) - threshold + 1)):
            window = filtered[start : start + threshold]
            block = tuple(value for _, value in window)
            if len(set(block)) < 2:
                continue
            blocks[block].append((file, window[0][0], window[-1][0]))
    results = []
    for locations in blocks.values():
        if len({file for file, _, _ in locations}) < 2:
            continue
        if len(results) >= max_results:
            return results, True
        results.append(
            {
                "matched_nontrivial_lines": threshold,
                "locations": [
                    {"file": f, "start": start, "end": end}
                    for f, start, end in locations
                ],
            }
        )
    return results, False


def main() -> int:
    """Run read-only scans and report truncation or read failures as incomplete coverage."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    facts = subparsers.add_parser("facts")
    duplicates = subparsers.add_parser("duplicates")
    for command in (facts, duplicates):
        add_project_arguments(command)
        command.add_argument("--max-files", type=positive_int, default=10000)
    facts.add_argument("--output", choices=("json", "md"), default="json")
    duplicates.add_argument("--threshold", type=positive_int, default=6)
    duplicates.add_argument("--max-results", type=positive_int, default=200)
    duplicates.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error("--root must be an existing directory")
    files, warnings = scan_files(root, args.max_files)
    languages = detect_languages(root, files)
    lang = (
        args.lang
        if args.lang != "auto"
        else (
            languages[0]
            if len(languages) == 1
            else ("mixed" if languages else "unknown")
        )
    )
    if args.mode == "facts":
        report = collect_facts(root, files, lang, warnings)
        print_report(report, args.output == "json")
    else:
        sources = read_sources(source_paths(files, lang), root, warnings)
        candidates, truncated = duplicate_candidates(
            sources, args.threshold, args.max_results
        )
        if truncated:
            warnings.append(
                "candidate output truncated; raise --max-results or narrow --root"
            )
        report = {
            "status": "completed" if not warnings else "incomplete",
            "complete": not warnings,
            "language": lang,
            "source_files": len(sources),
            "candidates": candidates,
            "warnings": warnings,
            "evidence": "ordered line-block matches, not semantic clones or a deletion recommendation",
            "coverage": {
                "excluded_directories": sorted(IGNORED_DIRS),
                "follows_symlinks": False,
            },
        }
        print_report(report, args.json)
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
