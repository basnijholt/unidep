"""Pixi.toml generation with version constraint merging."""

from __future__ import annotations

import copy
import os
import re
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Literal,
    Optional,
    Sequence,
    Tuple,
    Union,
    cast,
)

from ruamel.yaml import YAML

from unidep._conflicts import VersionConflictError, combine_version_pinnings
from unidep._dependencies_parsing import (
    _apply_local_dependency_override,
    _load,
    _move_local_optional_dependencies_to_local_dependencies,
    _str_is_path_like,
    get_local_dependencies,
    parse_requirements,
)
from unidep.platform_definitions import Spec, platforms_from_selector
from unidep.utils import (
    LocalDependency,
    PathWithExtras,
    is_pip_installable,
    parse_folder_or_filename,
    resolve_platforms,
    split_path_and_extras,
)

if TYPE_CHECKING:
    if sys.version_info >= (3, 10):
        from typing import TypeAlias
    else:
        from typing_extensions import TypeAlias

    from unidep.platform_definitions import Platform

    # Version spec can be a string or dict with version/build/extras
    VersionSpec: TypeAlias = Union[str, Dict[str, Any]]

    # Type alias for the extracted dependencies structure
    # Maps platform (or None for universal) to (conda_deps, pip_deps)
    PlatformDeps: TypeAlias = Dict[
        Optional[str],
        Tuple[Dict[str, VersionSpec], Dict[str, VersionSpec]],
    ]

try:
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # pragma: no cover
    HAS_TOML = True
except ImportError:  # pragma: no cover
    HAS_TOML = False


def _parse_version_build(pin: str | None) -> str | dict[str, str]:
    """Parse a version pin that may contain a build string.

    Conda matchspecs can have format: ">=1.0 build_string*"
    where the build string comes after a space following the version.

    Returns:
        str: Simple version string like ">=1.0" or "*"
        dict: {"version": ">=1.0", "build": "build_string*"} when build present

    """
    if not pin:
        return "*"

    pin = pin.strip()
    if not pin:
        return "*"

    # Build strings come after the full version constraint, separated by whitespace.
    # We split on the last whitespace and only treat the last token as build when
    # the version part looks complete (has digits or a wildcard) and the last token
    # doesn't look like another constraint.
    if " " in pin:
        version_candidate, build_candidate = pin.rsplit(None, 1)
        if (
            re.search(r"\d", version_candidate) or "*" in version_candidate
        ) and not re.match(r"^[><=!~]", build_candidate):
            version = version_candidate.replace(" ", "")
            return {"version": version, "build": build_candidate}

    # No build string, just return the version without spaces
    return pin.replace(" ", "")


def _parse_package_extras(pkg_name: str) -> tuple[str, list[str]]:
    """Parse a package name that may contain extras.

    Pip packages can have format: "package[extra1,extra2]"

    Returns:
        tuple: (base_name, extras_list) where extras_list is empty if no extras

    """
    match = re.match(r"^([a-zA-Z0-9_.\-]+)\[([^\]]+)\]$", pkg_name)
    if match:
        base_name = match.group(1)
        extras = [e.strip() for e in match.group(2).split(",")]
        return base_name, extras
    return pkg_name, []


def _make_pip_version_spec(
    version: str | dict[str, str],
    extras: list[str],
) -> str | dict[str, Any]:
    """Create a pip version spec, handling extras if present.

    Pixi requires extras in table format:
        package = { version = "*", extras = ["extra1", "extra2"] }

    Returns:
        str: Simple version string if no extras
        dict: Table with version and extras if extras present

    """
    if not extras:
        return version

    # When we have extras, we need table format
    if isinstance(version, str):
        return {"version": version, "extras": extras}
    # version is already a dict (has build string), add extras
    return {**version, "extras": extras}


def _canonicalize_version_spec(version_spec: str) -> str:
    """Normalize comma-separated version constraints to a stable order."""
    if "," not in version_spec:
        return version_spec

    operator_order = {
        "==": 0,
        "===": 0,
        "~=": 1,
        ">=": 2,
        ">": 3,
        "<=": 4,
        "<": 5,
        "!=": 6,
        "=": 7,
    }

    def _constraint_key(constraint: str) -> tuple[int, str]:
        token = constraint.strip()
        op = next(
            (
                candidate
                for candidate in ("===", "==", "~=", ">=", "<=", "!=", ">", "<", "=")
                if token.startswith(candidate)
            ),
            "",
        )
        return (operator_order.get(op, 8), token)

    parts = [part.strip() for part in version_spec.split(",") if part.strip()]
    return ",".join(sorted(parts, key=_constraint_key))


def _merge_version_specs(
    existing: str | dict[str, Any] | None,
    new: str | dict[str, Any],
    pkg_name: str,
) -> str | dict[str, Any]:
    """Merge two version specs, combining version constraints.

    Uses combine_version_pinnings from _conflicts.py to properly merge
    constraints like ">=1.7,<2" + "<1.16" -> ">=1.7,<1.16".

    If either spec has a build string, we can't merge and prefer the new one
    if it has a pin, otherwise keep existing.

    """
    if existing is None:
        return new

    # If either is a dict with build string, we can't merge version constraints
    existing_has_build = isinstance(existing, dict) and "build" in existing
    new_has_build = isinstance(new, dict) and "build" in new

    if existing_has_build or new_has_build:
        # Can't merge build strings - prefer the one with build, or new if both have
        if new_has_build:
            return new
        return existing

    # Extract version strings
    existing_version = existing["version"] if isinstance(existing, dict) else existing
    new_version = new["version"] if isinstance(new, dict) else new

    # Handle "*" (no constraint)
    if existing_version == "*":
        merged_version = new_version
    elif new_version == "*":
        merged_version = existing_version
    else:
        # Merge constraints in a deterministic order.
        constraint_pair = sorted([existing_version, new_version])
        try:
            merged_version = combine_version_pinnings(constraint_pair, name=pkg_name)
        except VersionConflictError:
            # Keep both constraints (deterministically ordered) so the manifest
            # stays explicit and downstream solvers can report unsatisfiable specs.
            merged_version = ",".join(constraint_pair)
        merged_version = _canonicalize_version_spec(merged_version)

    # Handle extras (for pip packages)
    existing_extras = existing.get("extras", []) if isinstance(existing, dict) else []
    new_extras = new.get("extras", []) if isinstance(new, dict) else []
    merged_extras = sorted(set(existing_extras) | set(new_extras))

    if merged_extras:
        return {"version": merged_version, "extras": merged_extras}
    return merged_version


def _version_spec_is_pinned(version_spec: VersionSpec) -> bool:
    """Return True if the version spec has a concrete pin."""
    if isinstance(version_spec, dict):
        version = version_spec.get("version", "*")
        if version != "*":
            return True
        return "build" in version_spec
    return version_spec != "*"


def _resolve_conda_pip_conflict(
    conda_deps: dict[str, VersionSpec],
    pip_deps: dict[str, VersionSpec],
    base_name: str,
) -> None:
    """Resolve conflicts between conda and pip specs for the same package."""
    conda_spec = conda_deps.get(base_name)
    pip_spec = pip_deps.get(base_name)
    if conda_spec is None or pip_spec is None:
        return

    conda_pinned = _version_spec_is_pinned(conda_spec)
    pip_pinned = _version_spec_is_pinned(pip_spec)

    # Pip extras cannot be represented via conda dependencies, so prefer pip.
    if isinstance(pip_spec, dict) and pip_spec.get("extras"):
        conda_deps.pop(base_name, None)
        return

    if conda_pinned and not pip_pinned:
        pip_deps.pop(base_name, None)
        return
    if pip_pinned and not conda_pinned:
        conda_deps.pop(base_name, None)
        return

    # If both are pinned or both are unpinned, default to conda to avoid
    # duplicating the same dependency in both sections.
    pip_deps.pop(base_name, None)


def _get_package_name(project_dir: Path) -> str | None:
    """Get the package name from pyproject.toml or setup.py."""
    pyproject_path = project_dir / "pyproject.toml"
    if pyproject_path.exists() and HAS_TOML:
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
            if "project" in data and "name" in data["project"]:
                # Normalize package name for use in dependencies
                # Replace dots and hyphens with underscores
                name = data["project"]["name"]
                return name.replace("-", "_").replace(".", "_")
    # Fallback to directory name
    return project_dir.name


def _normalize_feature_name(name: str) -> str:
    """Normalize a feature name to a deterministic pixi-friendly key."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-_")


def _project_dir_from_requirement_file(req_file: Path) -> Path:
    """Get the installable project directory for a requirements path."""
    resolved = req_file.resolve()
    return resolved.parent if resolved.is_file() else resolved


def _derive_feature_names(requirements_files: Sequence[Path]) -> list[str]:
    """Derive unique, non-empty feature names for requirements files."""
    project_dirs = [
        _project_dir_from_requirement_file(req_file) for req_file in requirements_files
    ]
    resolved_paths = [req_file.resolve() for req_file in requirements_files]

    base_names = []
    for req_file, req_path, req_dir in zip(
        requirements_files,
        resolved_paths,
        project_dirs,
    ):
        # Prefer the file stem for non-standard requirement filenames
        # (e.g. dev-requirements.yaml) so shared files get meaningful feature names.
        if req_path.name not in {"requirements.yaml", "pyproject.toml"}:
            default_name = req_path.stem
        else:
            default_name = req_dir.name or req_path.stem or req_file.stem or "feature"
        normalized = _normalize_feature_name(default_name)
        base_names.append(normalized or "feature")

    try:
        common_dir = Path(os.path.commonpath([str(path) for path in project_dirs]))
    except ValueError:
        common_dir = Path.cwd().resolve()
    base_counts = Counter(base_names)
    used_names: set[str] = set()
    feature_names: list[str] = []

    for base_name, req_path, req_dir in zip(base_names, resolved_paths, project_dirs):
        if base_counts[base_name] == 1:
            candidate = base_name
        else:
            try:
                rel_parts = req_dir.relative_to(common_dir).parts
            except ValueError:
                rel_parts = req_dir.parts
            rel_name = _normalize_feature_name(
                "-".join(part for part in rel_parts if part),
            )
            candidate = rel_name or base_name or "feature"

            if candidate in used_names:
                stem_name = _normalize_feature_name(req_path.stem)
                if stem_name:
                    candidate = _normalize_feature_name(f"{candidate}-{stem_name}")

        unique_name = candidate
        suffix = 2
        while unique_name in used_names:
            unique_name = f"{candidate}-{suffix}"
            suffix += 1
        used_names.add(unique_name)
        feature_names.append(unique_name)

    return feature_names


def _editable_dependency_path(req_dir: Path, output_file: str | Path | None) -> str:
    """Build editable path relative to the generated pixi.toml location."""
    output_dir = (
        Path.cwd().resolve()
        if output_file is None
        else Path(output_file).resolve().parent
    )
    rel_path = Path(os.path.relpath(req_dir.resolve(), output_dir)).as_posix()
    if rel_path == ".":
        return "."
    if rel_path.startswith("."):
        return rel_path
    return f"./{rel_path}"


def _canonical_path_with_extras(path_with_extras: PathWithExtras) -> PathWithExtras:
    """Normalize a requirements path for deterministic graph keys."""
    extras = sorted(set(path_with_extras.extras))
    return PathWithExtras(path_with_extras.path.resolve(), extras)


def _discover_local_dependency_graph(  # noqa: C901, PLR0912, PLR0915
    requirements_files: Sequence[Path],
) -> tuple[
    list[PathWithExtras],
    list[PathWithExtras],
    dict[PathWithExtras, list[PathWithExtras]],
    dict[PathWithExtras, dict[str, list[PathWithExtras]]],
]:
    """Discover requirement files reachable via local_dependencies.

    Returns:
        - Root requirement files (the user-provided inputs).
        - All discovered requirement files (roots + reachable local deps).
        - A direct dependency graph between discovered requirement files.
        - Optional-group local dependency edges for root files.

    """
    yaml = YAML(typ="rt")
    local_dependency_overrides: dict[Path, LocalDependency] = {}

    roots = [
        _canonical_path_with_extras(parse_folder_or_filename(req_file))
        for req_file in requirements_files
    ]
    discovered: list[PathWithExtras] = []
    graph: dict[PathWithExtras, list[PathWithExtras]] = {}
    optional_group_graph: dict[PathWithExtras, dict[str, list[PathWithExtras]]] = {}
    seen: set[PathWithExtras] = set()
    roots_set = set(roots)
    queue = list(roots)

    while queue:
        node = queue.pop(0)
        if node in seen:
            continue
        seen.add(node)
        discovered.append(node)

        data = copy.deepcopy(_load(node.path, yaml))
        _move_local_optional_dependencies_to_local_dependencies(
            data=data,
            path_with_extras=node,
            verbose=False,
        )
        local_dependencies = get_local_dependencies(data)

        for local_dep_obj in local_dependencies:
            if local_dep_obj.use != "local":
                _apply_local_dependency_override(
                    local_dependency=local_dep_obj,
                    base_dir=node.path.parent,
                    overrides=local_dependency_overrides,
                )

        if node in roots_set:
            optional_groups = data.get("optional_dependencies", {})
            if isinstance(optional_groups, Mapping):
                for group_name, group_deps in optional_groups.items():
                    if not isinstance(group_deps, list):
                        continue
                    for dep in group_deps:
                        if isinstance(dep, Mapping) or not _str_is_path_like(dep):
                            continue
                        effective_local_dep = _apply_local_dependency_override(
                            local_dependency=LocalDependency(local=dep),
                            base_dir=node.path.parent,
                            overrides=local_dependency_overrides,
                        )
                        if effective_local_dep.use != "local":
                            continue
                        local_path, _ = split_path_and_extras(effective_local_dep.local)
                        abs_local = (node.path.parent / local_path).resolve()
                        if abs_local.suffix in (".whl", ".zip"):
                            continue
                        try:
                            requirements_dep_file = parse_folder_or_filename(
                                node.path.parent / effective_local_dep.local,
                            )
                        except FileNotFoundError:
                            continue
                        child = _canonical_path_with_extras(requirements_dep_file)
                        group_edges = optional_group_graph.setdefault(
                            node,
                            {},
                        ).setdefault(group_name, [])
                        if child not in group_edges:
                            group_edges.append(child)
                        if child not in seen:
                            queue.append(child)

        direct_nodes: list[PathWithExtras] = []
        for local_dep_obj in local_dependencies:
            effective_local_dep = _apply_local_dependency_override(
                local_dependency=local_dep_obj,
                base_dir=node.path.parent,
                overrides=local_dependency_overrides,
            )
            if effective_local_dep.use != "local":
                continue
            local_path, _ = split_path_and_extras(effective_local_dep.local)
            abs_local = (node.path.parent / local_path).resolve()
            if abs_local.suffix in (".whl", ".zip"):
                # Keep parity with parse_requirements(): wheel/zip entries are
                # installable artifacts, not requirement files to recurse into.
                continue
            try:
                requirements_dep_file = parse_folder_or_filename(
                    node.path.parent / effective_local_dep.local,
                )
            except FileNotFoundError:
                # Local dependency can be an unmanaged package; keep parity with
                # parse_requirements() behavior by skipping it here.
                continue
            child = _canonical_path_with_extras(requirements_dep_file)
            if child not in direct_nodes:
                direct_nodes.append(child)
            if child not in seen:
                queue.append(child)

        graph[node] = direct_nodes

    return roots, discovered, graph, optional_group_graph


def _parse_direct_requirements_for_node(
    node: PathWithExtras,
    *,
    verbose: bool,
    ignore_pins: list[str] | None,
    skip_dependencies: list[str] | None,
    overwrite_pins: list[str] | None,
    include_all_optional_groups: bool = False,
) -> Any:
    """Parse a requirements node without recursively flattening local deps."""
    extras: list[list[str]] | Literal["*"] | None
    if node.extras:
        extras = [node.extras]
    elif include_all_optional_groups:
        extras = "*"
    else:
        extras = None
    req = parse_requirements(
        node.path,
        verbose=verbose,
        extras=extras,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        include_local_dependencies=False,
    )

    if not node.extras:
        return req

    merged_requirements = {
        name: list(specs) for name, specs in req.requirements.items()
    }
    if "*" in node.extras:
        selected_groups = list(req.optional_dependencies.keys())
    else:
        selected_groups = [
            group_name
            for group_name in node.extras
            if group_name in req.optional_dependencies
        ]

    # Extras selected on local dependencies are required for the parent feature.
    for group_name in selected_groups:
        for dep_name, specs in req.optional_dependencies[group_name].items():
            merged_requirements.setdefault(dep_name, []).extend(specs)

    return req._replace(
        requirements=merged_requirements,
        optional_dependencies={},
    )


def _collect_transitive_nodes(
    node: PathWithExtras,
    graph: dict[PathWithExtras, list[PathWithExtras]],
) -> list[PathWithExtras]:
    """Collect transitive local dependency nodes in deterministic order."""
    collected: list[PathWithExtras] = []
    seen: set[PathWithExtras] = set()
    queue = list(graph.get(node, []))

    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        collected.append(current)
        queue.extend(graph.get(current, []))

    return collected


def _with_unique_order(items: list[str]) -> list[str]:
    """Return unique items while preserving order."""
    return list(dict.fromkeys(items))


def _subtract_requirements(
    full_requirements: dict[str, list[Spec]],
    base_requirements: dict[str, list[Spec]],
) -> dict[str, list[Spec]]:
    """Return specs present in full_requirements but not in base_requirements."""
    diff: dict[str, list[Spec]] = {}
    for package_name, specs in full_requirements.items():
        remaining = Counter(base_requirements.get(package_name, []))
        package_diff: list[Spec] = []
        for spec in specs:
            if remaining[spec] > 0:
                remaining[spec] -= 1
            else:
                package_diff.append(spec)
        if package_diff:
            diff[package_name] = package_diff
    return diff


def generate_pixi_toml(  # noqa: PLR0912, C901, PLR0915
    *requirements_files: Path,
    project_name: str | None = None,
    channels: list[str] | None = None,
    platforms: list[Platform] | None = None,
    output_file: str | Path | None = "pixi.toml",
    verbose: bool = False,
    ignore_pins: list[str] | None = None,
    skip_dependencies: list[str] | None = None,
    overwrite_pins: list[str] | None = None,
) -> None:
    """Generate a pixi.toml file from requirements files.

    This function creates a pixi.toml with features for each requirements file,
    letting Pixi handle all dependency resolution and conflict management.
    """
    if not requirements_files:
        requirements_files = (Path.cwd(),)
    if platforms is not None and not platforms:
        platforms = None
    use_platforms_override = platforms is not None

    # Initialize pixi structure
    pixi_data: dict[str, Any] = {}

    # Collect channels and platforms from all requirements files
    all_channels = set()
    all_platforms = set()
    discovered_target_platforms: set[str] = set()
    # Track demoted universal entries for post-resolution fixup
    root_demoted: dict[str, tuple[str, VersionSpec]] = {}
    feature_demoted_map: dict[str, dict[str, tuple[str, VersionSpec]]] = {}

    # If single file, put dependencies at root level
    if len(requirements_files) == 1:
        requirements_file = requirements_files[0]
        req_file = parse_folder_or_filename(requirements_file).path
        base_req = parse_requirements(
            requirements_files[0],
            verbose=verbose,
            ignore_pins=ignore_pins,
            overwrite_pins=overwrite_pins,
            skip_dependencies=skip_dependencies,
            include_local_dependencies=True,
        )
        platform_deps, root_demoted = _extract_dependencies(base_req.requirements)
        discovered_target_platforms.update(
            platform for platform in platform_deps if platform is not None
        )

        # Use channels and platforms from the requirements file
        if base_req.channels:
            all_channels.update(base_req.channels)
        if base_req.platforms and not use_platforms_override:
            all_platforms.update(base_req.platforms)

        # Get universal (non-platform-specific) dependencies
        conda_deps, pip_deps = platform_deps.get(None, ({}, {}))

        if conda_deps:
            pixi_data["dependencies"] = conda_deps
        if pip_deps:
            pixi_data["pypi-dependencies"] = pip_deps

        # Add platform-specific dependencies as target sections
        _add_target_sections(pixi_data, platform_deps)

        # Check if there's a local package in the same directory
        req_dir = _project_dir_from_requirement_file(req_file)
        if is_pip_installable(req_dir):
            # Add the local package as an editable dependency
            if "pypi-dependencies" not in pixi_data:
                pixi_data["pypi-dependencies"] = {}
            # Get the actual package name from pyproject.toml
            package_name = _get_package_name(req_dir) or req_dir.name
            pixi_data["pypi-dependencies"][package_name] = {
                "path": _editable_dependency_path(req_dir, output_file),
                "editable": True,
            }

        # Handle optional dependencies as features
        optional_data = _load(req_file, YAML(typ="rt")).get("optional_dependencies", {})
        optional_groups = (
            list(optional_data) if isinstance(optional_data, Mapping) else []
        )
        if optional_groups:
            pixi_data["feature"] = {}
            pixi_data["environments"] = {}
            opt_features = []

            for group_name in optional_groups:
                group_req = parse_requirements(
                    requirements_file,
                    verbose=verbose,
                    extras=[[group_name]],
                    ignore_pins=ignore_pins,
                    overwrite_pins=overwrite_pins,
                    skip_dependencies=skip_dependencies,
                    include_local_dependencies=True,
                )
                # A group parse contains the base requirements plus group-selected
                # optional local dependencies. Keep only the delta to preserve
                # optional semantics.
                group_feature_requirements = _subtract_requirements(
                    group_req.requirements,
                    base_req.requirements,
                )
                for dep_name, specs in group_req.optional_dependencies.get(
                    group_name,
                    {},
                ).items():
                    group_feature_requirements.setdefault(dep_name, []).extend(specs)
                opt_platform_deps, opt_demoted = _extract_dependencies(
                    group_feature_requirements,
                )
                discovered_target_platforms.update(
                    platform for platform in opt_platform_deps if platform is not None
                )
                feature = _build_feature_dict(opt_platform_deps)
                if feature:
                    pixi_data["feature"][group_name] = feature
                    opt_features.append(group_name)
                if opt_demoted:
                    feature_demoted_map[group_name] = opt_demoted

            # Create environments for optional dependencies
            if opt_features:
                # Default environment has no optional features
                pixi_data["environments"]["default"] = []
                for feat in opt_features:
                    # Environment names can't have underscores
                    env_name = feat.replace("_", "-")
                    pixi_data["environments"][env_name] = [feat]
                # "all" environment includes all optional features
                if len(opt_features) > 1:
                    pixi_data["environments"]["all"] = opt_features

    else:
        # Multiple files: create one feature per requirement file and compose
        # local-dependency relationships in environments instead of flattening.
        pixi_data["feature"] = {}
        pixi_data["environments"] = {}
        root_nodes, discovered_nodes, local_dependency_graph, optional_group_graph = (
            _discover_local_dependency_graph(requirements_files)
        )
        feature_names = _derive_feature_names([node.path for node in discovered_nodes])
        feature_name_by_node = dict(zip(discovered_nodes, feature_names))
        root_nodes_set = set(root_nodes)
        base_feature_nodes: dict[str, PathWithExtras] = {}
        optional_feature_parents: dict[str, str] = {}
        optional_feature_has_feature: dict[str, bool] = {}
        optional_feature_local_nodes: dict[str, list[PathWithExtras]] = {}

        for node in discovered_nodes:
            req = _parse_direct_requirements_for_node(
                node,
                verbose=verbose,
                ignore_pins=ignore_pins,
                skip_dependencies=skip_dependencies,
                overwrite_pins=overwrite_pins,
                include_all_optional_groups=node in root_nodes_set,
            )
            platform_deps, node_demoted = _extract_dependencies(req.requirements)
            discovered_target_platforms.update(
                platform for platform in platform_deps if platform is not None
            )
            feature_name = feature_name_by_node[node]

            # Collect channels and platforms
            if req.channels:
                all_channels.update(req.channels)
            if req.platforms and not use_platforms_override:
                all_platforms.update(req.platforms)

            # Build the feature dict from platform deps
            feature = _build_feature_dict(platform_deps)

            # Add editable dependency for standard project requirement files.
            req_dir = _project_dir_from_requirement_file(node.path)
            should_add_editable = node.path.name in {
                "requirements.yaml",
                "pyproject.toml",
            }
            if should_add_editable and is_pip_installable(req_dir):
                # Add the local package as an editable dependency
                if "pypi-dependencies" not in feature:
                    feature["pypi-dependencies"] = {}
                # Get the actual package name from pyproject.toml
                package_name = _get_package_name(req_dir) or feature_name
                # Use relative path from the output file location
                rel_path = _editable_dependency_path(req_dir, output_file)
                feature["pypi-dependencies"][package_name] = {
                    "path": rel_path,
                    "editable": True,
                }

            if feature:  # Only add non-empty features
                pixi_data["feature"][feature_name] = feature
                base_feature_nodes[feature_name] = node
            if node_demoted:
                feature_demoted_map[feature_name] = node_demoted

            if node not in root_nodes_set:
                continue

            # Handle optional dependencies as sub-features for root features.
            if feature_name not in pixi_data["feature"]:
                continue
            for group_name, group_specs in req.optional_dependencies.items():
                group_platform_deps, group_demoted = _extract_dependencies(group_specs)
                discovered_target_platforms.update(
                    platform for platform in group_platform_deps if platform is not None
                )
                opt_feature = _build_feature_dict(group_platform_deps)
                opt_feature_name = f"{feature_name}-{group_name}"
                optional_local_nodes = optional_group_graph.get(node, {}).get(
                    group_name,
                    [],
                )
                if not opt_feature and not optional_local_nodes:
                    continue
                if opt_feature:
                    pixi_data["feature"][opt_feature_name] = opt_feature
                    optional_feature_has_feature[opt_feature_name] = True
                else:
                    optional_feature_has_feature[opt_feature_name] = False
                optional_feature_parents[opt_feature_name] = feature_name
                optional_feature_local_nodes[opt_feature_name] = optional_local_nodes
                if group_demoted:
                    feature_demoted_map[opt_feature_name] = group_demoted

        # Create environments
        if pixi_data["feature"]:
            transitive_features: dict[str, list[str]] = {}
            for feature_name, node in base_feature_nodes.items():
                dep_features = [
                    feature_name_by_node[dep_node]
                    for dep_node in _collect_transitive_nodes(
                        node,
                        local_dependency_graph,
                    )
                    if feature_name_by_node.get(dep_node) in pixi_data["feature"]
                ]
                transitive_features[feature_name] = _with_unique_order(dep_features)

            root_base_features = [
                feature_name_by_node[node]
                for node in root_nodes
                if feature_name_by_node.get(node) in pixi_data["feature"]
            ]
            default_features: list[str] = []
            for feature_name in root_base_features:
                default_features.append(feature_name)
                default_features.extend(transitive_features.get(feature_name, []))
            pixi_data["environments"]["default"] = _with_unique_order(default_features)

            for opt_feature_name, parent_feature in optional_feature_parents.items():
                env_name = opt_feature_name.replace("_", "-")
                env_features = [
                    parent_feature,
                    *transitive_features.get(parent_feature, []),
                ]
                if optional_feature_has_feature.get(opt_feature_name, False):
                    env_features.append(opt_feature_name)
                for local_node in optional_feature_local_nodes.get(
                    opt_feature_name,
                    [],
                ):
                    local_feature = feature_name_by_node.get(local_node)
                    if (
                        local_feature is None
                        or local_feature not in pixi_data["feature"]
                    ):
                        continue
                    env_features.append(local_feature)
                    env_features.extend(transitive_features.get(local_feature, []))
                pixi_data["environments"][env_name] = _with_unique_order(env_features)

    # Set workspace metadata with collected channels and platforms
    # Sort for deterministic output
    final_platforms = resolve_platforms(
        requested_platforms=platforms,
        declared_platforms=cast("set[Platform]", all_platforms),
        selector_platforms=cast("set[Platform]", discovered_target_platforms),
    )
    final_channels = sorted(
        list(all_channels) if all_channels else (channels or ["conda-forge"]),
    )
    pixi_data["workspace"] = {
        "name": project_name or Path.cwd().name,
        "channels": final_channels,
        "platforms": final_platforms,
    }

    # Filter target sections to only include platforms in the project's platforms list
    _filter_targets_by_platforms(pixi_data, set(final_platforms))

    # Restore demoted universal entries as explicit targets for uncovered platforms
    if root_demoted:
        _restore_demoted_universals(pixi_data, root_demoted, final_platforms)
    for feat_name, feat_demoted in feature_demoted_map.items():
        if feat_name in pixi_data.get("feature", {}):
            _restore_demoted_universals(
                pixi_data["feature"][feat_name],
                feat_demoted,
                final_platforms,
            )

    # Write the pixi.toml file
    _write_pixi_toml(pixi_data, output_file, verbose=verbose)


def _add_dep(
    conda_deps: dict[str, VersionSpec],
    pip_deps: dict[str, VersionSpec],
    spec_which: str,
    pkg_name: str,
    base_name: str,
    version: VersionSpec,
    pip_version: VersionSpec,
) -> None:
    """Add a dependency to the appropriate dict, merging version constraints."""
    if spec_which == "conda":
        conda_deps[pkg_name] = _merge_version_specs(
            conda_deps.get(pkg_name),
            version,
            pkg_name,
        )
        _resolve_conda_pip_conflict(conda_deps, pip_deps, base_name)
    elif spec_which == "pip":
        pip_deps[base_name] = _merge_version_specs(
            pip_deps.get(base_name),
            pip_version,
            base_name,
        )
        _resolve_conda_pip_conflict(conda_deps, pip_deps, base_name)


def _reconcile_with_universal_deps(
    platform_deps: PlatformDeps,
    *,
    platform: Platform | None,
    base_name: str,
    demoted: dict[str, tuple[str, VersionSpec]] | None = None,
) -> None:
    """Resolve conflicts between a platform bucket and universal dependencies.

    When a universal entry is removed because of a target-specific conflict,
    the original spec is recorded in *demoted* so that callers can later
    promote it to explicit target entries for platforms that don't override it.
    """
    if platform is None:
        return

    universal_conda, universal_pip = platform_deps.get(None, ({}, {}))
    platform_conda, platform_pip = platform_deps.get(platform, ({}, {}))

    for conda_scope, pip_scope in (
        (universal_conda, platform_pip),
        (platform_conda, universal_pip),
    ):
        if base_name not in conda_scope or base_name not in pip_scope:
            continue
        # For universal-vs-target conflicts, preserve target-specific intent
        # when both sides are pinned.
        if (
            conda_scope is universal_conda
            and pip_scope is platform_pip
            and _version_spec_is_pinned(conda_scope[base_name])
            and _version_spec_is_pinned(pip_scope[base_name])
        ):
            if demoted is not None and base_name not in demoted:
                demoted[base_name] = ("conda", copy.deepcopy(conda_scope[base_name]))
            conda_scope.pop(base_name, None)
            continue
        conda_probe = {base_name: conda_scope[base_name]}
        pip_probe = {base_name: pip_scope[base_name]}
        _resolve_conda_pip_conflict(conda_probe, pip_probe, base_name)
        if base_name not in conda_probe:
            if (
                demoted is not None
                and conda_scope is universal_conda
                and base_name not in demoted
            ):
                demoted[base_name] = (
                    "conda",
                    copy.deepcopy(conda_scope[base_name]),
                )
            conda_scope.pop(base_name, None)
        if base_name not in pip_probe:
            if (
                demoted is not None
                and pip_scope is universal_pip
                and base_name not in demoted
            ):
                demoted[base_name] = (
                    "pip",
                    copy.deepcopy(pip_scope[base_name]),
                )
            pip_scope.pop(base_name, None)


def _extract_dependencies(
    specs_dict: dict[str, list[Spec]],
) -> tuple[PlatformDeps, dict[str, tuple[str, VersionSpec]]]:
    """Extract conda and pip dependencies from a dict of package specs.

    Returns a tuple of:
        - A dict mapping platform (or None for universal) to (conda_deps, pip_deps).
        - A dict of demoted universal entries (pkg_name -> (dep_type, spec)) that
          were removed from universal during cross-platform reconciliation and may
          need to be restored as explicit target entries for other platforms.

    Platform-specific dependencies are mapped to their respective platforms.
    Version constraints are merged using combine_version_pinnings to ensure
    consistency with pip package metadata generated by unidep's setuptools hook.

    """
    platform_deps: PlatformDeps = {None: ({}, {})}
    demoted: dict[str, tuple[str, VersionSpec]] = {}

    for pkg_name, specs in specs_dict.items():
        for spec in specs:
            normalized_pin = spec.pin
            if spec.which == "pip":
                # Reuse Spec pin-normalization logic (`=` -> `==`) used elsewhere.
                normalized = spec.name_with_pin(is_pip=True)
                normalized_pin = normalized[len(spec.name) :].strip() or None

            version = _parse_version_build(normalized_pin)

            # For pip packages, parse extras from package name
            if spec.which == "pip":
                base_name, extras = _parse_package_extras(pkg_name)
                pip_version = _make_pip_version_spec(version, extras)
            else:
                base_name = pkg_name
                pip_version = version

            # Get target platforms (list of one platform, or [None] for universal)
            targets: Sequence[Platform | None]
            if spec.selector:
                targets = platforms_from_selector(spec.selector)
            else:
                targets = [None]

            for platform in targets:
                if platform not in platform_deps:
                    platform_deps[platform] = ({}, {})
                conda_deps, pip_deps = platform_deps[platform]
                _add_dep(
                    conda_deps,
                    pip_deps,
                    spec.which,
                    pkg_name,
                    base_name,
                    version,
                    pip_version,
                )
                _reconcile_with_universal_deps(
                    platform_deps,
                    platform=platform,
                    base_name=base_name,
                    demoted=demoted,
                )

    return platform_deps, demoted


def _build_feature_dict(platform_deps: PlatformDeps) -> dict[str, Any]:
    """Build a pixi feature dict from platform dependencies."""
    feature: dict[str, Any] = {}

    # Get universal (non-platform-specific) dependencies
    conda_deps, pip_deps = platform_deps.get(None, ({}, {}))
    if conda_deps:
        feature["dependencies"] = conda_deps
    if pip_deps:
        feature["pypi-dependencies"] = pip_deps

    # Add platform-specific dependencies as target sections
    for platform, (plat_conda, plat_pip) in platform_deps.items():
        if platform is None:
            continue
        if "target" not in feature:
            feature["target"] = {}
        if platform not in feature["target"]:
            feature["target"][platform] = {}
        if plat_conda:
            feature["target"][platform]["dependencies"] = plat_conda
        if plat_pip:
            feature["target"][platform]["pypi-dependencies"] = plat_pip

    return feature


def _add_target_sections(
    pixi_data: dict[str, Any],
    platform_deps: PlatformDeps,
) -> None:
    """Add target.<platform>.dependencies sections for platform-specific deps."""
    for platform, (conda_deps, pip_deps) in platform_deps.items():
        if platform is None:
            # Universal deps are handled separately
            continue
        # Note: platforms only exist in platform_deps if they have deps,
        # so we don't need to check for empty conda_deps/pip_deps

        # Initialize target section if needed
        if "target" not in pixi_data:
            pixi_data["target"] = {}
        if platform not in pixi_data["target"]:
            pixi_data["target"][platform] = {}

        target = pixi_data["target"][platform]
        if conda_deps:
            target["dependencies"] = conda_deps
        if pip_deps:
            target["pypi-dependencies"] = pip_deps


def _filter_section_targets(
    section: dict[str, Any],
    valid_platforms: set[str],
) -> None:
    """Remove target entries for platforms not in *valid_platforms*."""
    if "target" not in section:
        return
    section["target"] = {
        platform: deps
        for platform, deps in section["target"].items()
        if platform in valid_platforms
    }
    if not section["target"]:
        del section["target"]


def _filter_targets_by_platforms(
    pixi_data: dict[str, Any],
    valid_platforms: set[str],
) -> None:
    """Filter target sections to only include platforms in valid_platforms.

    This removes targets for platforms that aren't in the project's platforms list,
    which would otherwise cause pixi to emit warnings.
    """
    _filter_section_targets(pixi_data, valid_platforms)
    for feature_data in pixi_data.get("feature", {}).values():
        _filter_section_targets(feature_data, valid_platforms)


def _restore_demoted_universals(
    section: dict[str, Any],
    demoted: dict[str, tuple[str, VersionSpec]],
    final_platforms: Sequence[str],
) -> None:
    """Add explicit target entries for platforms missing demoted universal deps.

    During conda/pip reconciliation, a universal entry may be removed when it
    conflicts with a target-specific entry on one platform.  Without this
    fixup, the dependency disappears for *all other* platforms.  This function
    promotes the original universal spec to an explicit target entry for every
    final platform that doesn't already carry it.
    """
    for pkg, (dep_type, spec) in demoted.items():
        dep_key = "dependencies" if dep_type == "conda" else "pypi-dependencies"
        # If the package is (still) in universal deps, all platforms are covered.
        if pkg in section.get("dependencies", {}):
            continue
        if pkg in section.get("pypi-dependencies", {}):
            continue
        for platform in final_platforms:
            target = section.get("target", {}).get(platform, {})
            if pkg in target.get("dependencies", {}):
                continue
            if pkg in target.get("pypi-dependencies", {}):
                continue
            # This platform is missing the package — add it.
            section.setdefault("target", {}).setdefault(
                platform,
                {},
            ).setdefault(dep_key, {})[pkg] = copy.deepcopy(spec)


def _write_pixi_toml(
    pixi_data: dict[str, Any],
    output_file: str | Path | None,
    *,
    verbose: bool = False,
) -> None:
    """Write the pixi data structure to a TOML file."""
    try:
        import tomli_w
    except ImportError:  # pragma: no cover
        msg = (
            "❌ `tomli_w` is required to write TOML files. "
            "Install it with `pip install tomli_w`."
        )
        raise ImportError(msg) from None

    if output_file is not None:
        output_path = Path(output_file)
        with output_path.open("wb") as f:
            tomli_w.dump(pixi_data, f)
        if verbose:
            print(f"✅ Generated pixi.toml at {output_path}")
    else:
        # Output to stdout
        tomli_w.dump(pixi_data, sys.stdout.buffer)
