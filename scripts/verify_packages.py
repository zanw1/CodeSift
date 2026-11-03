#!/usr/bin/env python3
"""Check public registry names without claiming package safety, version validity, or API compatibility."""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from dependencies import canonical_name, read_dependencies
from detect_lang import (
    add_project_arguments,
    positive_int,
    print_report,
    select_language,
)

REGISTRIES = {
    "rust": "https://crates.io/api/v1/crates/{}",
    "python": "https://pypi.org/pypi/{}/json",
    "javascript": "https://registry.npmjs.org/{}",
}


def check_registry(name: str, lang: str, timeout: int = 10) -> tuple[str, str]:
    """Treat only registry HTTP 404 as absence; preserve other failures as unresolved."""
    url = REGISTRIES[lang].format(urllib.parse.quote(name, safe=""))
    request = urllib.request.Request(
        url, headers={"User-Agent": "plausible-code-review/2"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status == 200:
                return (
                    "registry_match",
                    "name exists; version, provenance and safety not checked",
                )
            return "error", f"unexpected HTTP status {response.status}"
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return (
                "not_found",
                "name absent from this public registry; not proof of hallucination",
            )
        return "error", f"HTTP {error.code}"
    except (OSError, ValueError) as error:
        return "error", type(error).__name__


def public_registry_uncertain(root: Path, lang: str) -> bool:
    """Detect registry overrides without reading or exposing authentication values."""
    if lang == "python":
        env = any(os.environ.get(k) for k in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"))
        configs = (
            root / "pip.conf",
            Path.home() / ".config/pip/pip.conf",
            Path.home() / ".pip/pip.conf",
        )
        return env or any(p.is_file() for p in configs)
    if lang == "rust":
        configs = (
            root / ".cargo/config",
            root / ".cargo/config.toml",
            Path(os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")))
            / "config.toml",
        )
        return any(p.is_file() for p in configs)
    if lang == "javascript":
        return bool(
            os.environ.get("NPM_CONFIG_REGISTRY")
            or os.environ.get("npm_config_registry")
            or (Path.home() / ".npmrc").is_file()
        )
    return False


def verify_inventory(
    inventory: dict,
    root: Path,
    offline: bool = False,
    public_registry: bool = False,
    max_checks: int = 100,
    timeout: int = 10,
) -> dict:
    """Deduplicate checks by source and expose offline, private, budget, and network limits."""
    lang = inventory["language"]
    results: list[dict] = []
    seen: set[tuple[str, str]] = set()
    checks = 0
    uncertain = public_registry_uncertain(root, lang) and not public_registry
    for item in inventory["direct"]:
        identity = (canonical_name(item["name"], lang), item["source"])
        if identity in seen:
            continue
        seen.add(identity)
        source = item["source"]
        if source in {"path", "git", "url", "git_or_url", "workspace"}:
            status, reason = (
                "not_applicable",
                f"{source} dependency; validate with its resolver/build",
            )
        elif source != "registry" or lang not in REGISTRIES or uncertain:
            status, reason = (
                "unresolved",
                "registry/source requires project-specific resolver metadata",
            )
        elif offline:
            status, reason = "not_checked", "offline mode"
        elif checks >= max_checks:
            status, reason = "not_checked", "request budget exhausted"
        else:
            checks += 1
            status, reason = check_registry(item["name"], lang, timeout)
        results.append(
            {"name": item["name"], "source": source, "status": status, "reason": reason}
        )
    incomplete = bool(
        inventory["errors"]
        or inventory["warnings"]
        or any(r["status"] in {"error", "not_checked", "unresolved"} for r in results)
    )
    missing = any(r["status"] == "not_found" for r in results)
    return {
        "language": lang,
        "status": "failed"
        if missing
        else ("incomplete" if incomplete else "completed"),
        "complete": not incomplete,
        "checks_performed": checks,
        "results": results,
        "warnings": inventory["warnings"],
        "errors": inventory["errors"],
        "evidence_scope": "public registry name existence only; not installed version, API or supply-chain safety",
    }


def main() -> int:
    """Run bounded registry checks without installing packages or executing project code."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_project_arguments(parser)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--public-registry",
        action="store_true",
        help="confirm public registry is authoritative despite local overrides",
    )
    parser.add_argument("--max-checks", type=positive_int, default=100)
    parser.add_argument("--timeout", type=positive_int, default=10)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error("--root must be an existing directory")
    inventory = read_dependencies(root, select_language(root, args.lang))
    report = verify_inventory(
        inventory,
        root,
        args.offline,
        args.public_registry,
        args.max_checks,
        args.timeout,
    )
    print_report(report, args.json)
    return 1 if report["status"] == "failed" else (0 if report["complete"] else 2)


if __name__ == "__main__":
    sys.exit(main())
