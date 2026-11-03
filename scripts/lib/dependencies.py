"""Parse dependency manifests centrally, preserving sources without executing build scripts."""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path


def load_manifest(path: Path) -> dict:
    """Read declarative manifests and propagate errors instead of reporting zero dependencies."""
    data = (
        json.loads(path.read_text())
        if path.suffix == ".json"
        else tomllib.loads(path.read_text())
    )
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected an object")
    return data


def dependency(
    name: str,
    spec: object,
    manifest: Path,
    root: Path,
    group: str = "runtime",
    source: str = "registry",
) -> dict:
    """Build a traceable declaration record without inferring package existence from its version."""
    return {
        "name": name,
        "specifier": spec,
        "source": source,
        "group": group,
        "manifest": str(manifest.relative_to(root)),
    }


def cargo_manifests(root: Path, data: dict, warnings: list[str]) -> list[Path]:
    """Locate workspace members within the root and expose external or unmatched members."""
    workspace = data.get("workspace", {})
    manifests = {root / "Cargo.toml"}
    for pattern in workspace.get("members", []):
        if Path(pattern).is_absolute():
            raise ValueError("workspace member patterns must be relative")
        members = list(root.glob(pattern))
        if not members:
            warnings.append(f"workspace member pattern matched no paths: {pattern}")
        for member in members:
            resolved = member.resolve()
            if resolved.is_relative_to(root) and (resolved / "Cargo.toml").is_file():
                manifests.add(resolved / "Cargo.toml")
            else:
                warnings.append("workspace member outside root or without Cargo.toml")
    if workspace.get("exclude"):
        warnings.append(
            "workspace excludes require Cargo metadata to confirm active members"
        )
    return sorted(manifests)


def cargo_dependency(
    alias: str,
    raw: object,
    inherited: dict,
    manifest: Path,
    root: Path,
    group: str,
    warnings: list[str],
) -> dict:
    """Resolve one Cargo declaration while preserving inheritance, aliases, and source identity."""
    spec = raw
    if isinstance(spec, dict) and spec.get("workspace"):
        spec = inherited.get(alias)
        if spec is None:
            warnings.append(f"unresolved workspace dependency: {alias}")
            return dependency(alias, raw, manifest, root, group, "workspace")
    detail = spec if isinstance(spec, dict) else {}
    source = next(
        (key for key in ("path", "git", "registry") if key in detail), "registry"
    )
    if source == "registry" and detail.get("registry"):
        source = "private_registry"
    return dependency(detail.get("package", alias), spec, manifest, root, group, source)


def rust_dependencies(root: Path, warnings: list[str]) -> tuple[list[dict], list[str]]:
    """Collect Cargo package, target, development, and build declarations with shared parsing."""
    path = root / "Cargo.toml"
    data = load_manifest(path)
    inherited = data.get("workspace", {}).get("dependencies", {})
    manifests = cargo_manifests(root, data, warnings)
    records: list[dict] = []
    for manifest in manifests:
        package = data if manifest == path else load_manifest(manifest)
        tables = [("", package)]
        tables.extend(
            (f"target:{target}/", table)
            for target, table in package.get("target", {}).items()
        )
        for prefix, table in tables:
            for key, group in (
                ("dependencies", "runtime"),
                ("dev-dependencies", "development"),
                ("build-dependencies", "build"),
            ):
                records.extend(
                    cargo_dependency(
                        alias, raw, inherited, manifest, root, prefix + group, warnings
                    )
                    for alias, raw in table.get(key, {}).items()
                )
        if package.get("patch") or package.get("replace"):
            warnings.append(
                f"{manifest.relative_to(root)} has overrides; confirm resolved sources with Cargo metadata"
            )
    if not records and inherited:
        warnings.append(
            "workspace dependency declarations alone do not prove member usage"
        )
    return records, [str(p.relative_to(root)) for p in manifests]


def python_requirement(raw: str, path: Path, root: Path, group: str) -> dict:
    """Extract PEP 508 names, retain direct URL sources, and reject unsupported requirement lines."""
    raw = raw.split(" #", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?\s*(.*)$", raw)
    if not match:
        raise ValueError(f"{path.name}: unsupported requirement")
    suffix = match.group(2)
    if suffix and not suffix.startswith(("@", ";", "<", ">", "=", "!", "~", "(")):
        raise ValueError(f"{path.name}: unsupported requirement syntax")
    source = "url" if suffix.startswith("@") else "registry"
    return dependency(match.group(1), raw, path, root, group, source)


def python_project_groups(
    data: dict, path: Path, root: Path, warnings: list[str]
) -> list[dict]:
    """Read declarative pyproject groups and expose unresolved dynamic or included declarations."""
    project = data.get("project", {})
    groups = {"runtime": project.get("dependencies", [])}
    groups.update(
        {
            f"optional:{k}": v
            for k, v in project.get("optional-dependencies", {}).items()
        }
    )
    groups.update(
        {f"development:{k}": v for k, v in data.get("dependency-groups", {}).items()}
    )
    records = []
    for group, values in groups.items():
        if not isinstance(values, list):
            raise ValueError(f"{path.name}: dependency group {group} must be an array")
        for raw in values:
            if isinstance(raw, str):
                records.append(python_requirement(raw, path, root, group))
            elif (
                group.startswith("development:")
                and isinstance(raw, dict)
                and "include-group" in raw
            ):
                warnings.append(f"unresolved dependency-group inclusion: {group}")
            else:
                raise ValueError(f"{path.name}: invalid requirement in {group}")
    if set(project.get("dynamic", [])) & {"dependencies", "optional-dependencies"}:
        warnings.append("dynamic Python dependencies require build-backend metadata")
    return records


def poetry_dependencies(poetry: dict, path: Path, root: Path) -> list[dict]:
    """Read Poetry groups without assuming public PyPI is authoritative for configured sources."""
    groups = {"runtime": poetry.get("dependencies", {})}
    groups.update(
        {
            f"development:{k}": v.get("dependencies", {})
            for k, v in poetry.get("group", {}).items()
        }
    )
    records = []
    for group, items in groups.items():
        for name, spec in items.items():
            if name.lower() == "python":
                continue
            source = "private_registry" if poetry.get("source") else "registry"
            if isinstance(spec, dict):
                source = next(
                    (k for k in ("path", "git", "url", "source") if k in spec), source
                )
                source = "private_registry" if source == "source" else source
            records.append(dependency(name, spec, path, root, group, source))
    return records


def requirements_file(path: Path, root: Path, warnings: list[str]) -> list[dict]:
    """Read requirement lines and report unresolved includes, indexes, and installation options."""
    records = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            warnings.append(
                "requirements option/include needs resolver metadata; not expanded"
            )
            continue
        records.append(python_requirement(line, path, root, "requirements"))
    return records


def setup_dependencies(path: Path, root: Path, warnings: list[str]) -> list[dict]:
    """Extract literal setup arguments without executing imports or dynamic expressions."""
    tree = ast.parse(path.read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "setup")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "setup")
        )
    ]
    records = []
    declared = False
    for node in (keyword for call in calls for keyword in call.keywords):
        if node.arg not in ("install_requires", "extras_require"):
            continue
        try:
            values = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            warnings.append("dynamic setup.py dependencies are not executed")
            continue
        groups = values if isinstance(values, dict) else {"runtime": values}
        for group, items in groups.items():
            if not isinstance(items, (list, tuple)):
                raise ValueError("setup.py requirements must be literal lists/tuples")
            records.extend(python_requirement(raw, path, root, group) for raw in items)
        declared = True
    if not declared:
        warnings.append("setup.py dependency declarations were not statically resolved")
    return records


def python_dependencies(
    root: Path, warnings: list[str]
) -> tuple[list[dict], list[str]]:
    """Combine Python manifest sources using a focused parser for each format."""
    records: list[dict] = []
    manifests: list[str] = []
    path = root / "pyproject.toml"
    if path.is_file():
        data = load_manifest(path)
        manifests.append(path.name)
        records.extend(python_project_groups(data, path, root, warnings))
        records.extend(
            poetry_dependencies(data.get("tool", {}).get("poetry", {}), path, root)
        )
    path = root / "requirements.txt"
    if path.is_file():
        manifests.append(path.name)
        records.extend(requirements_file(path, root, warnings))
    path = root / "setup.py"
    if path.is_file() and not records:
        manifests.append(path.name)
        records.extend(setup_dependencies(path, root, warnings))
    return records, manifests


def javascript_dependencies(
    root: Path, warnings: list[str]
) -> tuple[list[dict], list[str]]:
    """Resolve npm aliases and local sources without treating a workspace root as a complete graph."""
    path = root / "package.json"
    data = load_manifest(path)
    records: list[dict] = []
    custom_registry = False
    npmrc = root / ".npmrc"
    if npmrc.is_file():
        custom_registry = bool(
            re.search(r"(?:^|\n)\s*(?:@[^:\s]+:)?registry\s*=", npmrc.read_text())
        )
    for key, group in (
        ("dependencies", "runtime"),
        ("devDependencies", "development"),
        ("optionalDependencies", "optional"),
        ("peerDependencies", "peer"),
    ):
        for alias, spec in data.get(key, {}).items():
            if not isinstance(spec, str):
                raise ValueError(f"package.json: invalid specifier for {alias}")
            name, source = alias, "private_registry" if custom_registry else "registry"
            if spec.startswith("npm:"):
                name = spec[4:].rsplit("@", 1)[0] if "@" in spec[5:] else spec[4:]
                if not name:
                    raise ValueError("package.json: empty npm alias")
            elif spec.startswith(
                ("workspace:", "catalog:", "file:", "link:", ".", "/")
            ):
                source = (
                    "workspace"
                    if spec.startswith(("workspace:", "catalog:"))
                    else "path"
                )
            elif (
                re.match(r"(?:git|https?|ssh|github|gitlab|bitbucket)[+:]", spec)
                or "/" in spec
            ):
                source = "git_or_url"
            records.append(dependency(name, spec, path, root, group, source))
    if data.get("workspaces"):
        warnings.append(
            "workspace members need separate --root audits or package-manager metadata"
        )
    return records, [path.name]


def conan_requirements(path: Path, root: Path) -> list[dict]:
    """Read only the requires section of conanfile.txt as dependency declarations."""
    records = []
    active = False
    for line in path.read_text().splitlines():
        value = line.strip()
        if value.startswith("["):
            active = value == "[requires]"
        elif active and value and not value.startswith("#"):
            records.append(
                dependency(value.split("/", 1)[0], value, path, root, source="conan")
            )
    return records


def native_dependencies(
    root: Path, warnings: list[str]
) -> tuple[list[dict], list[str]]:
    """Read explicit native manifests without guessing public package names from CMake labels."""
    records: list[dict] = []
    manifests: list[str] = []
    vcpkg = root / "vcpkg.json"
    if vcpkg.is_file():
        data = load_manifest(vcpkg)
        manifests.append(vcpkg.name)
        for item in data.get("dependencies", []):
            name = item if isinstance(item, str) else item["name"]
            records.append(dependency(name, item, vcpkg, root, source="vcpkg"))
        if data.get("features"):
            warnings.append("vcpkg optional features require active-feature resolution")
    conan = root / "conanfile.txt"
    if conan.is_file():
        manifests.append(conan.name)
        records.extend(conan_requirements(conan, root))
    if (root / "conanfile.py").is_file():
        manifests.append("conanfile.py")
        warnings.append("executable conanfile.py requires Conan metadata; not executed")
    for name in ("CMakeLists.txt", "meson.build", "Makefile", "makefile"):
        if (root / name).is_file():
            manifests.append(name)
            warnings.append(
                f"{name} is executable build logic; dependency resolution not inferred by regex"
            )
    warnings.append(
        "native dependency inventory is best-effort; system/private registries are not resolved"
    )
    return records, manifests


def canonical_name(name: str, lang: str) -> str:
    """Normalize package identity consistently for inventory counts and registry deduplication."""
    return (re.sub(r"[-_.]+", "-", name) if lang == "python" else name).casefold()


def read_dependencies(root: Path, lang: str) -> dict:
    """Collect declarations and coverage limits without running package managers or writing locks."""
    root = root.resolve()
    result = {
        "language": lang,
        "direct": [],
        "count": 0,
        "manifests": [],
        "warnings": [],
        "errors": [],
        "scope": "declared dependencies, not usage or installed graph",
    }
    readers = {
        "rust": rust_dependencies,
        "python": python_dependencies,
        "javascript": javascript_dependencies,
        "c": native_dependencies,
        "cpp": native_dependencies,
    }
    if lang not in readers:
        result["errors"].append(
            "choose --lang and a package --root for unknown/mixed projects"
        )
        return result
    try:
        records, manifests = readers[lang](root, result["warnings"])
        result["direct"] = records
        result["manifests"] = manifests
        identities = {(canonical_name(r["name"], lang), r["source"]) for r in records}
        result["count"] = len(identities)
        if not manifests:
            result["errors"].append("no supported dependency manifest found")
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        SyntaxError,
    ) as error:
        result["errors"].append(
            f"manifest parsing failed: {type(error).__name__}: {error}"
        )
    return result
