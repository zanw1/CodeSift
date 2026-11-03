"""Share project scanning, language detection, and reporting using Python 3.11+."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

VALID_LANGS = ("auto", "c", "cpp", "rust", "python", "javascript")
MANIFEST_MARKERS = {
    "rust": ("Cargo.toml",),
    "python": ("pyproject.toml", "setup.py", "requirements.txt"),
    "javascript": ("package.json",),
}
NATIVE_BUILD_NAMES = (
    "CMakeLists.txt",
    "meson.build",
    "Makefile",
    "makefile",
    "vcpkg.json",
    "conanfile.py",
    "conanfile.txt",
)
BUILD_NAMES = set(NATIVE_BUILD_NAMES).union(*MANIFEST_MARKERS.values())
SOURCE_SUFFIXES = {
    "c": {".c", ".h"},
    "cpp": {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"},
    "rust": {".rs"},
    "python": {".py"},
    "javascript": {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"},
}
IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
    "target",
    "build",
    "builddir",
    "out",
    "dist",
    ".next",
    ".cache",
    "_deps",
    "third_party",
    "vendor",
}


def positive_int(value: str) -> int:
    """Require a positive integer so invalid limits cannot silently bypass execution."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def scan_files(root: Path, limit: int = 10000) -> tuple[list[Path], list[str]]:
    """Scan regular files under explicit exclusions without following symbolic links."""
    files: list[Path] = []
    warnings: list[str] = []

    def on_error(error: OSError) -> None:
        """Expose traversal errors as coverage gaps instead of empty results."""
        warnings.append(f"scan error: {error}")

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=on_error):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in IGNORED_DIRS
            and not d.startswith("cmake-build-")
            and not (Path(directory) / d).is_symlink()
        )
        for name in sorted(names):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                warnings.append(
                    f"non-regular or symlink file excluded: {path.relative_to(root)}"
                )
                continue
            if len(files) >= limit:
                warnings.append(
                    f"file limit {limit} reached; narrow --root or raise --max-files"
                )
                return files, warnings
            files.append(path)
    return files, warnings


def native_language(root: Path, files: list[Path]) -> str | None:
    """Infer C or C++ from source and declarations without guessing unknown build logic."""
    suffixes = {p.suffix.lower() for p in files}
    if suffixes & (SOURCE_SUFFIXES["cpp"] - SOURCE_SUFFIXES["c"]):
        return "cpp"
    if ".c" in suffixes:
        return "c"
    cmake = root / "CMakeLists.txt"
    text = cmake.read_text(errors="replace") if cmake.is_file() else ""
    text = re.sub(r"(?m)^\s*#.*$", "", text)
    match = re.search(r"\bproject\s*\(([^)]*)\)", text, re.I | re.S)
    explicit = match.group(1) if match else ""
    if re.search(r"\bCXX\b", explicit, re.I):
        return "cpp"
    if re.search(r"\bC\b", explicit):
        return "c"
    if match and "LANGUAGES" not in explicit.upper():
        return "cpp"  # Bare project(NAME) enables both C and CXX.
    if any(
        (root / name).is_file()
        for name in ("vcpkg.json", "conanfile.py", "conanfile.txt")
    ):
        return "cpp"
    return None


def detect_languages(root: Path, files: list[Path] | None = None) -> list[str]:
    """Prefer root manifests and report mixed projects instead of choosing a language arbitrarily."""
    langs = {
        lang
        for lang, names in MANIFEST_MARKERS.items()
        if any((root / name).is_file() for name in names)
    }
    has_native = any((root / name).is_file() for name in NATIVE_BUILD_NAMES)
    if has_native or not langs:
        if files is None:
            files, _ = scan_files(root)
        native = native_language(root, files)
        if native:
            langs.add(native)
        if not langs:
            suffixes = {p.suffix.lower() for p in files}
            langs.update(
                lang
                for lang in ("python", "javascript", "rust")
                if suffixes & SOURCE_SUFFIXES[lang]
            )
    return sorted(langs)


def detect_lang(root: Path | None = None) -> str:
    """Return one detected language or an explicit mixed or unknown result."""
    langs = detect_languages(root or Path.cwd())
    return langs[0] if len(langs) == 1 else ("mixed" if langs else "unknown")


def select_language(root: Path, requested: str) -> str:
    """Validate the language option and detect the project language only when requested."""
    if requested not in VALID_LANGS:
        raise ValueError(f"unsupported language: {requested}")
    return detect_lang(root) if requested == "auto" else requested


def add_project_arguments(parser: argparse.ArgumentParser) -> None:
    """Provide consistent project-root and language arguments for command-line helpers."""
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--lang", choices=VALID_LANGS, default="auto")


def print_report(report: dict, as_json: bool = False) -> None:
    """Render the same report as JSON or readable text without changing its meaning."""
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    for key, value in report.items():
        print(
            f"{key}: {json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else value}"
        )
