"""Pixi.toml generation with version constraint merging."""

from __future__ import annotations

import copy
import os
import re
import sys
from collections import Counter, deque
from collections.abc import Mapping
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    NamedTuple,
    Sequence,
    cast,
)

from ruamel.yaml import YAML

try:
    import tomli_w
except ImportError:  # pragma: no cover
    tomli_w = None

from unidep._dependencies_parsing import (
    DependencyEntry,
    _apply_local_dependency_override,
    _effective_local_dependencies,
    _load,
    _move_local_optional_dependencies_to_local_dependencies,
    _str_is_path_like,
    _try_parse_local_dependency_requirement_file,
    parse_requirements,
)
from unidep._dependency_selection import select_conda_like_requirements
from unidep.platform_definitions import Platform
from unidep.utils import (
    LocalDependency,
    PathWithExtras,
    is_pip_installable,
    package_name_from_path,
    parse_folder_or_filename,
    resolve_platforms,
    split_path_and_extras,
)

if TYPE_CHECKING:
    from typing import Dict, Optional, Tuple, Union

    from unidep._dependencies_parsing import ParsedRequirements
    from unidep.platform_definitions import Spec

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


def _get_package_name(project_dir: Path) -> str:
    """Get a pixi dependency key for an editable local package."""
    name = package_name_from_path(project_dir)
    return name.replace("-", "_").replace(".", "_")


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
    try:
        rel_path = Path(os.path.relpath(req_dir.resolve(), output_dir)).as_posix()
    except ValueError:
        # On Windows, os.path.relpath raises ValueError when paths are on
        # different drives (e.g. C:\ vs D:\).  Fall back to an absolute path.
        return req_dir.resolve().as_posix()
    if rel_path == ".":
        return "."
    if rel_path.startswith("."):
        return rel_path
    return f"./{rel_path}"


def _with_unique_order_paths(items: Sequence[Path]) -> list[Path]:
    """Return unique paths while preserving order."""
    unique_items: list[Path] = []
    seen: set[Path] = set()
    for item in items:
        resolved = item.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_items.append(item)
    return unique_items


def _add_editable_local_dependencies(
    section: dict[str, Any],
    local_projects: Sequence[Path],
    *,
    output_file: str | Path | None,
    exclude: set[Path] | None = None,
) -> None:
    """Add local projects to a pixi section as editable pip dependencies.

    Parameters
    ----------
    section
        The pixi data dict to add ``pypi-dependencies`` entries to.
    local_projects
        Directories of installable Python projects.
    output_file
        Path to the generated pixi.toml (used to compute relative paths).
    exclude
        Resolved paths to skip (used to avoid duplicating editables that
        already appear in a parent/base section).

    """
    unique_projects = _with_unique_order_paths(list(local_projects))
    if not unique_projects:
        return
    for project_dir in unique_projects:
        if exclude and project_dir.resolve() in exclude:
            continue
        package_name = _get_package_name(project_dir)
        section.setdefault("pypi-dependencies", {})[package_name] = {
            "path": _editable_dependency_path(project_dir, output_file),
            "editable": True,
        }


def _unmanaged_installable_local_project_dir(
    *,
    base_dir: Path,
    local_dependency: str,
) -> Path | None:
    """Resolve an unmanaged local dependency to an installable project directory."""
    local_path, _extras = split_path_and_extras(local_dependency)
    abs_local = (base_dir / local_path).resolve()
    if abs_local.suffix in (".whl", ".zip"):
        return None
    if is_pip_installable(abs_local):
        return abs_local
    return None


class LocalDependencyGraph(NamedTuple):
    """Result of discovering local dependency relationships."""

    roots: list[PathWithExtras]
    discovered: list[PathWithExtras]
    graph: dict[PathWithExtras, list[PathWithExtras]]
    optional_group_graph: dict[PathWithExtras, dict[str, list[PathWithExtras]]]
    unmanaged_local_graph: dict[PathWithExtras, list[Path]]
    optional_group_unmanaged_graph: dict[PathWithExtras, dict[str, list[Path]]]


def _discover_local_dependency_graph(  # noqa: PLR0912, C901, PLR0915
    requirements_files: Sequence[Path],
) -> LocalDependencyGraph:
    """Discover requirement files reachable via local_dependencies.

    Returns:
        - Root requirement files (the user-provided inputs).
        - All discovered requirement files (roots + reachable local deps).
        - A direct dependency graph between discovered requirement files.
        - Optional-group local dependency edges for root files.
        - Direct unmanaged installable local dependencies for each node.
        - Optional-group unmanaged installable local dependencies for root files.

    """
    yaml = YAML(typ="rt")
    local_dependency_overrides: dict[Path, LocalDependency] = {}

    roots = [
        parse_folder_or_filename(req_file).canonicalized()
        for req_file in requirements_files
    ]
    discovered: list[PathWithExtras] = []
    graph: dict[PathWithExtras, list[PathWithExtras]] = {}
    optional_group_graph: dict[PathWithExtras, dict[str, list[PathWithExtras]]] = {}
    unmanaged_local_graph: dict[PathWithExtras, list[Path]] = {}
    optional_group_unmanaged_graph: dict[PathWithExtras, dict[str, list[Path]]] = {}
    seen: set[PathWithExtras] = set()
    roots_set = set(roots)
    queue = deque(roots)

    while queue:
        node = queue.popleft()
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
        effective_local_dependencies = _effective_local_dependencies(
            data=data,
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
                        requirements_dep_file = (
                            _try_parse_local_dependency_requirement_file(
                                base_dir=node.path.parent,
                                local_dependency=effective_local_dep.local,
                            )
                        )
                        if requirements_dep_file is None:
                            unmanaged_local_dir = (
                                _unmanaged_installable_local_project_dir(
                                    base_dir=node.path.parent,
                                    local_dependency=effective_local_dep.local,
                                )
                            )
                            if unmanaged_local_dir is None:
                                continue
                            unmanaged_group_edges = (
                                optional_group_unmanaged_graph.setdefault(
                                    node,
                                    {},
                                ).setdefault(group_name, [])
                            )
                            if unmanaged_local_dir not in unmanaged_group_edges:
                                unmanaged_group_edges.append(unmanaged_local_dir)
                            continue
                        child = requirements_dep_file.canonicalized()
                        group_edges = optional_group_graph.setdefault(
                            node,
                            {},
                        ).setdefault(group_name, [])
                        if child not in group_edges:
                            group_edges.append(child)
                        if child not in seen:
                            queue.append(child)

        direct_nodes: list[PathWithExtras] = []
        direct_unmanaged_nodes: list[Path] = []
        for effective_local_dep in effective_local_dependencies:
            if effective_local_dep.use != "local":
                continue
            requirements_dep_file = _try_parse_local_dependency_requirement_file(
                base_dir=node.path.parent,
                local_dependency=effective_local_dep.local,
            )
            if requirements_dep_file is None:
                unmanaged_local_dir = _unmanaged_installable_local_project_dir(
                    base_dir=node.path.parent,
                    local_dependency=effective_local_dep.local,
                )
                if (
                    unmanaged_local_dir is not None
                    and unmanaged_local_dir not in direct_unmanaged_nodes
                ):
                    direct_unmanaged_nodes.append(unmanaged_local_dir)
                continue
            child = requirements_dep_file.canonicalized()
            if child not in direct_nodes:
                direct_nodes.append(child)
            if child not in seen:
                queue.append(child)

        graph[node] = direct_nodes
        unmanaged_local_graph[node] = direct_unmanaged_nodes

    return LocalDependencyGraph(
        roots=roots,
        discovered=discovered,
        graph=graph,
        optional_group_graph=optional_group_graph,
        unmanaged_local_graph=unmanaged_local_graph,
        optional_group_unmanaged_graph=optional_group_unmanaged_graph,
    )


def _parse_direct_requirements_for_node(
    node: PathWithExtras,
    *,
    verbose: bool,
    ignore_pins: list[str] | None,
    skip_dependencies: list[str] | None,
    overwrite_pins: list[str] | None,
    include_all_optional_groups: bool = False,
) -> ParsedRequirements:
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
    merged_entries = list(req.dependency_entries)
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
        merged_entries.extend(req.optional_dependency_entries.get(group_name, []))

    return req._replace(
        requirements=merged_requirements,
        optional_dependencies={},
        dependency_entries=merged_entries,
        optional_dependency_entries={},
    )


def _collect_transitive_nodes(
    node: PathWithExtras,
    graph: dict[PathWithExtras, list[PathWithExtras]],
) -> list[PathWithExtras]:
    """Collect transitive local dependency nodes in deterministic order."""
    collected: list[PathWithExtras] = []
    seen: set[PathWithExtras] = set()
    queue = deque(graph.get(node, []))

    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        collected.append(current)
        queue.extend(graph.get(current, []))

    return collected


def _with_unique_order(items: list[str]) -> list[str]:
    """Return unique items while preserving order."""
    return list(dict.fromkeys(items))


def _unique_optional_feature_name(
    *,
    parent_feature: str,
    group_name: str,
    taken_names: set[str],
) -> str:
    """Generate a non-colliding optional sub-feature name."""
    candidate = f"{parent_feature}-{group_name}"
    if candidate not in taken_names:
        taken_names.add(candidate)
        return candidate

    suffix_base = f"{candidate}-opt"
    unique_candidate = suffix_base
    suffix = 2
    while unique_candidate in taken_names:
        unique_candidate = f"{suffix_base}-{suffix}"
        suffix += 1
    taken_names.add(unique_candidate)
    return unique_candidate


def _unique_env_name(
    feature_name: str,
    taken_env_names: set[str],
) -> str:
    """Generate a non-colliding pixi environment name from a feature name.

    Pixi environment names cannot contain underscores, so we replace them
    with hyphens.  When this normalization causes a collision (e.g. both
    ``foo_bar`` and ``foo-bar`` exist), a numeric suffix is appended.
    """
    candidate = feature_name.replace("_", "-")
    if candidate not in taken_env_names:
        taken_env_names.add(candidate)
        return candidate

    suffix = 2
    while f"{candidate}-{suffix}" in taken_env_names:
        suffix += 1
    result = f"{candidate}-{suffix}"
    taken_env_names.add(result)
    return result


def _add_single_file_optional_environments(
    pixi_data: dict[str, Any],
    opt_features: list[str],
) -> None:
    """Add single-file optional environments, avoiding `all` name collisions."""
    if not opt_features:
        return

    pixi_data["environments"]["default"] = []
    create_aggregate_all_env = len(opt_features) > 1
    taken_env_names: set[str] = {"default"} | (
        {"all"} if create_aggregate_all_env else set()
    )

    for feat in opt_features:
        env_name = _unique_env_name(feat, taken_env_names)
        pixi_data["environments"][env_name] = [feat]

    if create_aggregate_all_env:
        pixi_data["environments"]["all"] = opt_features


def _spec_key(spec: Spec) -> tuple[str, str, str | None, str | None]:
    """Return the stable identity of a Spec (excludes parse-time identifier)."""
    return (spec.name, spec.which, spec.pin, spec.selector)


def _entry_key(
    entry: DependencyEntry,
) -> tuple[
    tuple[str, str, str | None, str | None] | None,
    tuple[str, str, str | None, str | None] | None,
]:
    """Return the stable identity of a dependency entry."""
    conda = _spec_key(entry.conda) if entry.conda is not None else None
    pip = _spec_key(entry.pip) if entry.pip is not None else None
    return (conda, pip)


def _subtract_entries(
    full_entries: list[DependencyEntry],
    base_entries: list[DependencyEntry],
) -> list[DependencyEntry]:
    """Return entries present in full_entries but not in base_entries."""
    remaining = Counter(_entry_key(entry) for entry in base_entries)
    diff: list[DependencyEntry] = []
    for entry in full_entries:
        key = _entry_key(entry)
        if remaining[key] > 0:
            remaining[key] -= 1
        else:
            diff.append(entry)
    return diff


class _PixiGenerationResult(NamedTuple):
    """Intermediate result from single-file or multi-file pixi generation."""

    pixi_data: dict[str, Any]
    all_channels: set[str]
    all_platforms: set[str]
    discovered_target_platforms: set[str]


def _process_single_file_optional_groups(
    pixi_data: dict[str, Any],
    *,
    req_file: Path,
    base_req: ParsedRequirements,
    dep_graph: LocalDependencyGraph,
    root_node: PathWithExtras,
    base_local_editable_set: set[Path],
    platforms_override: list[Platform] | None,
    output_file: str | Path | None,
    verbose: bool,
    ignore_pins: list[str] | None,
    skip_dependencies: list[str] | None,
    overwrite_pins: list[str] | None,
) -> set[str]:
    """Process optional dependency groups for single-file pixi generation.

    Returns discovered target platforms.
    """
    discovered_target_platforms: set[str] = set()

    optional_data = _load(req_file, YAML(typ="rt")).get("optional_dependencies", {})
    optional_groups = list(optional_data) if isinstance(optional_data, Mapping) else []
    if not optional_groups:
        return discovered_target_platforms

    pixi_data["feature"] = {}
    pixi_data["environments"] = {}
    opt_features = []

    for group_name in optional_groups:
        group_req = parse_requirements(
            req_file,
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
        group_feature_entries = _subtract_entries(
            group_req.dependency_entries,
            base_req.dependency_entries,
        )
        group_feature_entries.extend(
            group_req.optional_dependency_entries.get(group_name, []),
        )
        opt_platform_deps = _extract_dependencies(
            group_feature_entries,
            platforms=platforms_override or list(group_req.platforms) or None,
            allow_hoist_without_universal_origin=True,
        )
        discovered_target_platforms.update(
            platform for platform in opt_platform_deps if platform is not None
        )
        feature = _build_feature_dict(opt_platform_deps)
        optional_group_projects: list[Path] = list(
            dep_graph.optional_group_unmanaged_graph.get(root_node, {}).get(
                group_name,
                [],
            ),
        )
        optional_local_nodes = dep_graph.optional_group_graph.get(
            root_node,
            {},
        ).get(
            group_name,
            [],
        )
        seen_optional_nodes: set[PathWithExtras] = set()
        for optional_local_node in optional_local_nodes:
            for candidate_node in [
                optional_local_node,
                *(
                    _collect_transitive_nodes(
                        optional_local_node,
                        dep_graph.graph,
                    )
                ),
            ]:
                if candidate_node in seen_optional_nodes:
                    continue
                seen_optional_nodes.add(candidate_node)
                optional_project_dir = _project_dir_from_requirement_file(
                    candidate_node.path,
                )
                if is_pip_installable(optional_project_dir):
                    optional_group_projects.append(optional_project_dir)
                optional_group_projects.extend(
                    dep_graph.unmanaged_local_graph.get(candidate_node, []),
                )
        _add_editable_local_dependencies(
            feature,
            optional_group_projects,
            output_file=output_file,
            exclude=base_local_editable_set,
        )
        if feature:
            pixi_data["feature"][group_name] = feature
            opt_features.append(group_name)

    # Create environments for optional dependencies
    _add_single_file_optional_environments(pixi_data, opt_features)

    return discovered_target_platforms


def _generate_single_file_pixi(
    requirements_file: Path,
    *,
    platforms_override: list[Platform] | None,
    output_file: str | Path | None,
    verbose: bool,
    ignore_pins: list[str] | None,
    skip_dependencies: list[str] | None,
    overwrite_pins: list[str] | None,
) -> _PixiGenerationResult:
    """Generate pixi data for a single requirements file."""
    pixi_data: dict[str, Any] = {}
    all_channels: set[str] = set()
    all_platforms: set[str] = set()
    discovered_target_platforms: set[str] = set()

    req_file = parse_folder_or_filename(requirements_file).path
    base_req = parse_requirements(
        requirements_file,
        verbose=verbose,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        include_local_dependencies=True,
    )
    platform_deps = _extract_dependencies(
        base_req.dependency_entries,
        platforms=platforms_override or list(base_req.platforms) or None,
        allow_hoist_without_universal_origin=True,
    )
    discovered_target_platforms.update(
        platform for platform in platform_deps if platform is not None
    )

    # Use channels and platforms from the requirements file
    if base_req.channels:
        all_channels.update(base_req.channels)
    if base_req.platforms and not platforms_override:
        all_platforms.update(base_req.platforms)

    pixi_data.update(_build_feature_dict(platform_deps))

    dep_graph = _discover_local_dependency_graph([requirements_file])
    root_node = dep_graph.roots[0]

    # Collect editable packages from the root project and required local deps
    # only (NOT optional-only local deps, which belong in optional features).
    required_nodes = set(_collect_transitive_nodes(root_node, dep_graph.graph))
    req_dir = _project_dir_from_requirement_file(req_file)
    local_editable_projects: list[Path] = []
    if is_pip_installable(req_dir):
        local_editable_projects.append(req_dir)
    for node in dep_graph.discovered:
        if node == root_node or node not in required_nodes:
            continue
        node_project_dir = _project_dir_from_requirement_file(node.path)
        should_add_editable = node.path.name in {
            "requirements.yaml",
            "pyproject.toml",
        }
        if should_add_editable and is_pip_installable(node_project_dir):
            local_editable_projects.append(node_project_dir)
        local_editable_projects.extend(dep_graph.unmanaged_local_graph.get(node, []))
    local_editable_projects.extend(dep_graph.unmanaged_local_graph.get(root_node, []))
    _add_editable_local_dependencies(
        pixi_data,
        local_editable_projects,
        output_file=output_file,
    )
    base_local_editable_set = {
        path.resolve() for path in _with_unique_order_paths(local_editable_projects)
    }

    # Handle optional dependencies as features
    opt_target_platforms = _process_single_file_optional_groups(
        pixi_data,
        req_file=req_file,
        base_req=base_req,
        dep_graph=dep_graph,
        root_node=root_node,
        base_local_editable_set=base_local_editable_set,
        platforms_override=platforms_override,
        output_file=output_file,
        verbose=verbose,
        ignore_pins=ignore_pins,
        skip_dependencies=skip_dependencies,
        overwrite_pins=overwrite_pins,
    )
    discovered_target_platforms.update(opt_target_platforms)

    return _PixiGenerationResult(
        pixi_data=pixi_data,
        all_channels=all_channels,
        all_platforms=all_platforms,
        discovered_target_platforms=discovered_target_platforms,
    )


def _generate_multi_file_pixi(  # noqa: PLR0912, C901, PLR0915
    requirements_files: Sequence[Path],
    *,
    platforms_override: list[Platform] | None,
    output_file: str | Path | None,
    verbose: bool,
    ignore_pins: list[str] | None,
    skip_dependencies: list[str] | None,
    overwrite_pins: list[str] | None,
) -> _PixiGenerationResult:
    """Generate pixi data for multiple requirements files."""
    pixi_data: dict[str, Any] = {"feature": {}, "environments": {}}
    all_channels: set[str] = set()
    all_platforms: set[str] = set()
    discovered_target_platforms: set[str] = set()
    dep_graph = _discover_local_dependency_graph(requirements_files)
    feature_names = _derive_feature_names(
        [node.path for node in dep_graph.discovered],
    )
    feature_name_by_node = dict(zip(dep_graph.discovered, feature_names))
    taken_optional_feature_names: set[str] = set(feature_names)
    root_nodes_set = set(dep_graph.roots)
    parsed_by_node: dict[PathWithExtras, ParsedRequirements] = {}
    global_declared_platforms: set[Platform] = set()
    base_feature_nodes: dict[str, PathWithExtras] = {}
    optional_feature_parents: dict[str, str] = {}
    optional_feature_has_feature: dict[str, bool] = {}
    optional_feature_local_nodes: dict[str, list[PathWithExtras]] = {}

    for node in dep_graph.discovered:
        req = _parse_direct_requirements_for_node(
            node,
            verbose=verbose,
            ignore_pins=ignore_pins,
            skip_dependencies=skip_dependencies,
            overwrite_pins=overwrite_pins,
            include_all_optional_groups=node in root_nodes_set,
        )
        parsed_by_node[node] = req
        if req.platforms and not platforms_override:
            global_declared_platforms.update(req.platforms)

    for node in dep_graph.discovered:
        req = parsed_by_node[node]
        feature_platforms = _feature_platforms_for_entries(
            entries=req.dependency_entries,
            declared_platforms=req.platforms,
            global_declared_platforms=global_declared_platforms,
            platforms_override=platforms_override,
        )
        platform_deps = _extract_dependencies(
            req.dependency_entries,
            platforms=feature_platforms,
            allow_hoist_without_universal_origin=platforms_override is not None
            or not req.platforms,
        )
        discovered_target_platforms.update(
            platform for platform in platform_deps if platform is not None
        )
        feature_name = feature_name_by_node[node]

        # Collect channels and platforms
        if req.channels:
            all_channels.update(req.channels)
        if not platforms_override and feature_platforms:
            all_platforms.update(feature_platforms)

        # Build the feature dict from platform deps
        feature = _build_feature_dict(platform_deps)

        # Add editable dependency for standard project requirement files.
        req_dir = _project_dir_from_requirement_file(node.path)
        should_add_editable = node.path.name in {
            "requirements.yaml",
            "pyproject.toml",
        }
        node_editable_projects: list[Path] = []
        if should_add_editable and is_pip_installable(req_dir):
            node_editable_projects.append(req_dir)
        node_editable_projects.extend(dep_graph.unmanaged_local_graph.get(node, []))
        _add_editable_local_dependencies(
            feature,
            node_editable_projects,
            output_file=output_file,
        )

        if feature:  # Only add non-empty features
            pixi_data["feature"][feature_name] = feature
        # Always track the node so transitive deps are computed even when
        # the root itself has no direct dependencies (aggregator pattern).
        base_feature_nodes[feature_name] = node

        if node not in root_nodes_set:
            continue

        # Build set of editables already in the base feature so optional
        # sub-features don't duplicate them (mirrors single-file behavior).
        base_editable_set = {
            p.resolve() for p in _with_unique_order_paths(node_editable_projects)
        }

        # Handle optional dependencies as sub-features for root features.
        # Even when a root has no direct deps/editables (so no base feature),
        # its optional groups may still carry real dependencies and must be kept.
        parsed_group_names = list(req.optional_dependencies)
        local_only_group_names = set(
            dep_graph.optional_group_graph.get(node, {}),
        ) | set(
            dep_graph.optional_group_unmanaged_graph.get(node, {}),
        )
        all_group_names = parsed_group_names + [
            group_name
            for group_name in sorted(local_only_group_names)
            if group_name not in req.optional_dependencies
        ]
        for group_name in all_group_names:
            group_entries = req.optional_dependency_entries.get(group_name, [])
            group_platforms = _feature_platforms_for_entries(
                entries=group_entries,
                declared_platforms=req.platforms,
                global_declared_platforms=global_declared_platforms,
                platforms_override=platforms_override,
            )
            group_platform_deps = _extract_dependencies(
                group_entries,
                platforms=group_platforms,
                allow_hoist_without_universal_origin=platforms_override is not None
                or not req.platforms,
            )
            discovered_target_platforms.update(
                platform for platform in group_platform_deps if platform is not None
            )
            if not platforms_override and group_platforms:
                all_platforms.update(group_platforms)
            opt_feature = _build_feature_dict(group_platform_deps)
            opt_feature_name = _unique_optional_feature_name(
                parent_feature=feature_name,
                group_name=group_name,
                taken_names=taken_optional_feature_names,
            )
            optional_local_nodes = dep_graph.optional_group_graph.get(
                node,
                {},
            ).get(
                group_name,
                [],
            )
            optional_unmanaged_local_projects = (
                dep_graph.optional_group_unmanaged_graph.get(
                    node,
                    {},
                ).get(
                    group_name,
                    [],
                )
            )
            _add_editable_local_dependencies(
                opt_feature,
                optional_unmanaged_local_projects,
                output_file=output_file,
                exclude=base_editable_set,
            )
            if (
                not opt_feature
                and not optional_local_nodes
                and not optional_unmanaged_local_projects
            ):
                continue
            if opt_feature:
                pixi_data["feature"][opt_feature_name] = opt_feature
                optional_feature_has_feature[opt_feature_name] = True
            else:
                optional_feature_has_feature[opt_feature_name] = False
            optional_feature_parents[opt_feature_name] = feature_name
            optional_feature_local_nodes[opt_feature_name] = optional_local_nodes

    # Create environments
    if pixi_data["feature"]:
        transitive_features: dict[str, list[str]] = {}
        for feature_name, node in base_feature_nodes.items():
            dep_features = [
                feature_name_by_node[dep_node]
                for dep_node in _collect_transitive_nodes(
                    node,
                    dep_graph.graph,
                )
                if feature_name_by_node.get(dep_node) in pixi_data["feature"]
            ]
            transitive_features[feature_name] = _with_unique_order(dep_features)

        default_features: list[str] = []
        for root_node in dep_graph.roots:
            root_feature = feature_name_by_node[root_node]
            # Include the root's own feature only if it's non-empty.
            if root_feature in pixi_data["feature"]:
                default_features.append(root_feature)
            # Always include transitive deps (supports aggregator roots
            # that have no direct deps but pull in local_dependencies).
            default_features.extend(transitive_features.get(root_feature, []))
        pixi_data["environments"]["default"] = _with_unique_order(default_features)

        taken_env_names: set[str] = {"default"}
        for opt_feature_name, parent_feature in optional_feature_parents.items():
            env_name = _unique_env_name(opt_feature_name, taken_env_names)
            env_features = []
            if parent_feature in pixi_data["feature"]:
                env_features.append(parent_feature)
            env_features.extend(transitive_features.get(parent_feature, []))
            if optional_feature_has_feature.get(opt_feature_name, False):
                env_features.append(opt_feature_name)
            for local_node in optional_feature_local_nodes.get(
                opt_feature_name,
                [],
            ):
                local_feature = feature_name_by_node[local_node]
                # Include the local node's own feature if it's non-empty.
                if local_feature in pixi_data["feature"]:
                    env_features.append(local_feature)
                # Always traverse transitive deps even when the local node
                # itself is empty (aggregator pattern).
                env_features.extend(transitive_features.get(local_feature, []))
            pixi_data["environments"][env_name] = _with_unique_order(env_features)

    return _PixiGenerationResult(
        pixi_data=pixi_data,
        all_channels=all_channels,
        all_platforms=all_platforms,
        discovered_target_platforms=discovered_target_platforms,
    )


def _selector_platforms_from_entries(
    entries: Sequence[DependencyEntry],
) -> list[Platform]:
    selector_platforms: set[Platform] = set()
    for entry in entries:
        for spec in (entry.conda, entry.pip):
            if spec is None or spec.selector is None:
                continue
            entry_platforms = spec.platforms()
            if entry_platforms is not None:
                selector_platforms.update(entry_platforms)
    return sorted(selector_platforms)


def _feature_platforms_for_entries(
    *,
    entries: Sequence[DependencyEntry],
    declared_platforms: Sequence[Platform],
    global_declared_platforms: set[Platform],
    platforms_override: list[Platform] | None,
) -> list[Platform] | None:
    if platforms_override:
        return list(platforms_override)
    if declared_platforms:
        return list(declared_platforms)
    inferred_platforms = set(global_declared_platforms)
    inferred_platforms.update(_selector_platforms_from_entries(entries))
    return sorted(inferred_platforms) or None


def generate_pixi_toml(
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

    Parameters
    ----------
    requirements_files
        One or more requirement file paths to process.
    project_name
        Name for the ``[workspace]`` section. Defaults to the current
        directory name.
    channels
        Conda channels for the workspace.  When provided, these **override**
        any channels declared in the requirement files (consistent with how
        *platforms* behaves).  When ``None``, channels are read from the
        requirement files, falling back to ``["conda-forge"]``.
    platforms
        Target platforms.  When provided, overrides file-declared platforms.
    output_file
        Path to write the generated TOML.  ``None`` writes to stdout.
    verbose
        Print progress information.
    ignore_pins
        Package names whose version pins should be stripped.
    skip_dependencies
        Package names to omit entirely.
    overwrite_pins
        Pin overrides in ``"pkg>=version"`` format.

    """
    if not requirements_files:
        requirements_files = (Path.cwd(),)
    if platforms is not None and not platforms:
        platforms = None
    if len(requirements_files) == 1:
        result = _generate_single_file_pixi(
            requirements_files[0],
            platforms_override=platforms,
            output_file=output_file,
            verbose=verbose,
            ignore_pins=ignore_pins,
            skip_dependencies=skip_dependencies,
            overwrite_pins=overwrite_pins,
        )
    else:
        result = _generate_multi_file_pixi(
            requirements_files,
            platforms_override=platforms,
            output_file=output_file,
            verbose=verbose,
            ignore_pins=ignore_pins,
            skip_dependencies=skip_dependencies,
            overwrite_pins=overwrite_pins,
        )

    pixi_data = result.pixi_data

    # Set workspace metadata with collected channels and platforms
    # Sort for deterministic output
    final_platforms = resolve_platforms(
        requested_platforms=platforms,
        declared_platforms=cast("set[Platform]", result.all_platforms),
        selector_platforms=cast("set[Platform]", result.discovered_target_platforms),
    )
    if channels is not None:
        final_channels = list(channels)
    elif result.all_channels:
        final_channels = sorted(result.all_channels)
    else:
        final_channels = ["conda-forge"]
    pixi_data["workspace"] = {
        "name": project_name or Path.cwd().name,
        "channels": final_channels,
        "platforms": final_platforms,
    }

    # Filter target sections to only include platforms in the project's platforms list
    _filter_targets_by_platforms(pixi_data, set(final_platforms))

    # Write the pixi.toml file
    _write_pixi_toml(pixi_data, output_file, verbose=verbose)


def _extract_dependencies(  # noqa: PLR0912
    entries: list[DependencyEntry],
    *,
    platforms: list[Platform] | None = None,
    allow_hoist_without_universal_origin: bool = False,
) -> PlatformDeps:
    """Extract conda and pip dependencies from dependency entries.

    Returns a dict mapping platform (or None for universal) to
    ``(conda_deps, pip_deps)``.
    """
    platform_deps: PlatformDeps = {None: ({}, {})}
    selected = select_conda_like_requirements(entries, platforms)
    target_platforms = platforms or sorted(
        platform for platform in selected if platform is not None
    )

    if target_platforms:
        per_platform: dict[
            Platform,
            tuple[
                dict[str, tuple[VersionSpec, bool]],
                dict[str, tuple[VersionSpec, bool]],
            ],
        ] = {platform: ({}, {}) for platform in target_platforms}
        for platform, candidates in selected.items():
            if platform is None:
                continue
            conda_deps, pip_deps = per_platform[platform]
            for candidate in candidates:
                has_universal_origin = any(
                    scope is None for scope in candidate.declared_scopes
                )
                if candidate.source == "conda":
                    conda_deps[candidate.spec.name] = (
                        _parse_version_build(candidate.spec.pin),
                        has_universal_origin,
                    )
                else:
                    base_name, extras = _parse_package_extras(candidate.spec.name)
                    normalized = candidate.spec.name_with_pin(is_pip=True)
                    normalized_pin = (
                        normalized[len(candidate.spec.name) :].strip() or None
                    )
                    version = _parse_version_build(normalized_pin)
                    pip_deps[base_name] = (
                        _make_pip_version_spec(version, extras),
                        has_universal_origin,
                    )

        universal_conda, universal_pip = platform_deps[None]
        conda_names = {
            name
            for conda_deps, _pip_deps in per_platform.values()
            for name in conda_deps
        }
        pip_names = {
            name for _conda_deps, pip_deps in per_platform.values() for name in pip_deps
        }

        for name in sorted(conda_names):
            present = {
                platform: deps[0][name]
                for platform, deps in per_platform.items()
                if name in deps[0]
            }
            if len(present) == len(target_platforms):
                first_spec, _first_universal = next(iter(present.values()))
                specs_match = all(
                    spec == first_spec for spec, _is_universal in present.values()
                )
                hoist_is_safe = allow_hoist_without_universal_origin
                if specs_match and hoist_is_safe:
                    universal_conda[name] = first_spec
                    continue
            for platform, (spec, _is_universal) in present.items():
                platform_deps.setdefault(platform, ({}, {}))[0][name] = spec

        for name in sorted(pip_names):
            present = {
                platform: deps[1][name]
                for platform, deps in per_platform.items()
                if name in deps[1]
            }
            if len(present) == len(target_platforms):
                first_spec, _first_universal = next(iter(present.values()))
                specs_match = all(
                    spec == first_spec for spec, _is_universal in present.values()
                )
                hoist_is_safe = allow_hoist_without_universal_origin
                if specs_match and hoist_is_safe:
                    universal_pip[name] = first_spec
                    continue
            for platform, (spec, _is_universal) in present.items():
                platform_deps.setdefault(platform, ({}, {}))[1][name] = spec
    else:
        universal_conda_deps, universal_pip_deps = platform_deps[None]
        for candidate in selected.get(None, []):
            if candidate.source == "conda":
                universal_conda_deps[candidate.spec.name] = _parse_version_build(
                    candidate.spec.pin,
                )
            else:
                base_name, extras = _parse_package_extras(candidate.spec.name)
                normalized = candidate.spec.name_with_pin(is_pip=True)
                normalized_pin = normalized[len(candidate.spec.name) :].strip() or None
                version = _parse_version_build(normalized_pin)
                universal_pip_deps[base_name] = _make_pip_version_spec(version, extras)

    return platform_deps


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


def _write_pixi_toml(
    pixi_data: dict[str, Any],
    output_file: str | Path | None,
    *,
    verbose: bool = False,
) -> None:
    """Write the pixi data structure to a TOML file."""
    if tomli_w is None:  # pragma: no cover
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
