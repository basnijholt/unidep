#!/usr/bin/env python3
"""unidep - Unified Conda and Pip requirements management.

This module provides setuptools integration for unidep.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, NamedTuple

from ruamel.yaml import YAML

from unidep._conflicts import _reconcile_conda_pip_pair
from unidep._dependencies_parsing import (
    DependencyEntry,
    _load,
    get_local_dependencies,
    parse_requirements,
)
from unidep._dependency_selection import (
    MergedSourceCandidate,
    _build_platform_candidates,
    _candidate_has_pip_extras,
    _resolve_final_collisions,
    collapse_selected_universals,
)
from unidep.utils import (
    UnsupportedPlatformError,
    build_pep508_environment_marker,
    identify_current_platform,
    is_pip_installable,
    package_name_from_path,
    parse_folder_or_filename,
    split_path_and_extras,
    warn,
)

if TYPE_CHECKING:
    import sys

    from setuptools import Distribution

    from unidep.platform_definitions import CondaPip, Platform, Spec

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:
        from typing_extensions import Literal


def filter_python_dependencies(
    entries: list[DependencyEntry]
    | dict[str, dict[Platform | None, dict[CondaPip, Spec]]],
    platforms: list[Platform] | None = None,
) -> list[str]:
    """Filter out conda dependencies and return only pip dependencies.

    Examples
    --------
    >>> requirements = parse_requirements("requirements.yaml")
    >>> python_deps = filter_python_dependencies(
    ...     requirements.dependency_entries, requirements.platforms
    ... )

    """
    if isinstance(entries, dict):
        warnings.warn(
            "`filter_python_dependencies()` accepting the dict returned by "
            "`resolve_conflicts()` is deprecated; pass "
            "`parse_requirements(...).dependency_entries` instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return _filter_python_dependencies_from_resolved(entries)

    selected = collapse_selected_universals(
        _select_pip_requirements_with_conda_suppression(list(entries), platforms),
        platforms,
    )
    pip_deps: list[str] = []
    by_spec: dict[Spec, list[Platform | None]] = {}
    for _platform, candidates in selected.items():
        for candidate in candidates:
            by_spec.setdefault(candidate.spec, []).append(_platform)

    for spec, _platforms in by_spec.items():
        dep_str = spec.name_with_pin(is_pip=True)
        if _platforms != [None] and all(
            platform is not None for platform in _platforms
        ):
            selector = build_pep508_environment_marker(_platforms)  # type: ignore[arg-type]
            dep_str = f"{dep_str}; {selector}"
        pip_deps.append(dep_str)
    return sorted(pip_deps)


def _select_pip_requirements_with_conda_suppression(
    entries: list[DependencyEntry],
    platforms: list[Platform] | None,
) -> dict[Platform | None, list[MergedSourceCandidate]]:
    selected: dict[Platform | None, list[MergedSourceCandidate]] = {}
    for platform_candidates in _build_platform_candidates(entries, platforms):
        conda_candidate = platform_candidates.conda
        pip_candidate = platform_candidates.pip
        _conda_kept, pip_kept = _reconcile_conda_pip_pair(
            conda=conda_candidate,
            pip=pip_candidate,
            conda_pinned=(
                conda_candidate is not None and conda_candidate.spec.pin is not None
            ),
            pip_pinned=pip_candidate is not None and pip_candidate.spec.pin is not None,
            pip_has_extras=(
                pip_candidate is not None and _candidate_has_pip_extras(pip_candidate)
            ),
            on_tie="both",
        )
        if pip_kept is None:
            continue
        selected.setdefault(platform_candidates.platform, []).append(pip_kept)
    return _resolve_final_collisions(selected)


def _filter_python_dependencies_from_resolved(
    resolved: dict[str, dict[Platform | None, dict[CondaPip, Spec]]],
) -> list[str]:
    """Filter out conda dependencies and return only pip dependencies."""
    pip_deps = []
    for platform_data in resolved.values():
        to_process: dict[Platform | None, Spec] = {}
        for _platform, sources in platform_data.items():
            pip_spec = sources.get("pip")
            if pip_spec:
                to_process[_platform] = pip_spec
        if not to_process:
            continue

        first_spec = next(iter(to_process.values()))
        if all(spec == first_spec for spec in to_process.values()):
            dep_str = first_spec.name_with_pin(is_pip=True)
            if _platform is not None:
                selector = build_pep508_environment_marker(list(to_process.keys()))  # type: ignore[arg-type]
                dep_str = f"{dep_str}; {selector}"
            pip_deps.append(dep_str)
            continue

        for _platform, pip_spec in to_process.items():
            dep_str = pip_spec.name_with_pin(is_pip=True)
            if _platform is not None:
                selector = build_pep508_environment_marker([_platform])
                dep_str = f"{dep_str}; {selector}"
            pip_deps.append(dep_str)
    return sorted(pip_deps)


class Dependencies(NamedTuple):
    dependencies: list[str]
    extras: dict[str, list[str]]


def _path_to_file_uri(path: PurePath) -> str:
    """Return a RFC 8089 compliant file URI for an absolute path."""
    # Keep in sync with CI helper and discussion in
    # https://github.com/basnijholt/unidep/pull/214#issuecomment-2568663364
    if isinstance(path, Path):
        target = path if path.is_absolute() else path.resolve()
        return target.as_uri()

    uri_path = path.as_posix().lstrip("/")
    return f"file:///{uri_path.replace(' ', '%20')}"


def get_python_dependencies(  # noqa: PLR0912
    filename: str
    | Path
    | Literal["requirements.yaml", "pyproject.toml"] = "requirements.yaml",  # noqa: PYI051
    *,
    verbose: bool = False,
    ignore_pins: list[str] | None = None,
    overwrite_pins: list[str] | None = None,
    skip_dependencies: list[str] | None = None,
    platforms: list[Platform] | None = None,
    raises_if_missing: bool = True,
    include_local_dependencies: bool = False,
) -> Dependencies:
    """Extract Python (pip) requirements from a `requirements.yaml` or `pyproject.toml` file."""  # noqa: E501
    try:
        p = parse_folder_or_filename(filename)
    except FileNotFoundError:
        if raises_if_missing:
            raise
        return Dependencies(dependencies=[], extras={})

    requirements = parse_requirements(
        p.path,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        verbose=verbose,
        extras="*",
    )
    if not platforms:
        platforms = list(requirements.platforms)
    dependencies = filter_python_dependencies(
        requirements.dependency_entries,
        platforms,
    )
    # TODO[Bas]: This currently doesn't correctly handle  # noqa: TD004, TD003, FIX002
    # conflicts between sections in the extras and the main dependencies.
    extras = {
        section: filter_python_dependencies(entries, platforms)
        for section, entries in requirements.optional_dependency_entries.items()
    }
    # Always process local dependencies to handle PyPI alternatives
    yaml = YAML(typ="rt")
    data = _load(p.path, yaml)

    # Process each local dependency
    for local_dep_obj in get_local_dependencies(data):
        if local_dep_obj.use == "skip":
            continue
        if local_dep_obj.use == "pypi":
            # Already added to pip dependencies when parsing requirements.
            continue
        local_path, extras_list = split_path_and_extras(local_dep_obj.local)
        abs_local = (p.path.parent / local_path).resolve()

        # If include_local_dependencies is False (UNIDEP_SKIP_LOCAL_DEPS=1),
        # always use PyPI alternative if available, skip otherwise
        if not include_local_dependencies:
            if local_dep_obj.pypi:
                dependencies.append(local_dep_obj.pypi)
            continue

        # Original behavior when include_local_dependencies is True
        # Handle wheel and zip files
        if abs_local.suffix in (".whl", ".zip"):
            if abs_local.exists():
                # Local wheel exists - use it
                uri = _path_to_file_uri(abs_local)
                dependencies.append(f"{abs_local.name} @ {uri}")
            elif local_dep_obj.pypi:
                # Wheel doesn't exist - use PyPI alternative
                dependencies.append(local_dep_obj.pypi)
            continue

        # Check if local path exists
        if abs_local.exists() and is_pip_installable(abs_local):
            # Local development - use file:// URL
            name = package_name_from_path(abs_local)
            uri = _path_to_file_uri(abs_local)
            dep_str = f"{name} @ {uri}"
            if extras_list:
                dep_str = f"{name}[{','.join(extras_list)}] @ {uri}"
            dependencies.append(dep_str)
        elif local_dep_obj.pypi:
            # Built wheel - local path doesn't exist, use PyPI alternative
            dependencies.append(local_dep_obj.pypi)
        # else: path doesn't exist and no PyPI alternative - skip

    return Dependencies(dependencies=dependencies, extras=extras)


def _deps(requirements_file: Path) -> Dependencies:  # pragma: no cover
    try:
        platforms = [identify_current_platform()]
    except UnsupportedPlatformError:
        warn(
            "Could not identify the current platform."
            " This may result in selecting all platforms."
            " Please report this issue at"
            " https://github.com/basnijholt/unidep/issues",
        )
        # We don't know the current platform, so we can't filter out.
        # This will result in selecting all platforms. But this is better
        # than failing.
        platforms = None

    skip_local_dependencies = bool(os.getenv("UNIDEP_SKIP_LOCAL_DEPS"))
    verbose = bool(os.getenv("UNIDEP_VERBOSE"))
    return get_python_dependencies(
        requirements_file,
        platforms=platforms,
        raises_if_missing=False,
        verbose=verbose,
        include_local_dependencies=not skip_local_dependencies,
    )


def _setuptools_finalizer(dist: Distribution) -> None:  # pragma: no cover
    """Entry point called by setuptools to get the dependencies for a project."""
    # PEP 517 says that "All hooks are run with working directory set to the
    # root of the source tree".
    project_root = Path.cwd()
    try:
        requirements_file = parse_folder_or_filename(project_root).path
    except FileNotFoundError:
        return
    if requirements_file.exists() and dist.install_requires:  # type: ignore[attr-defined]
        msg = (
            "You have a `requirements.yaml` file in your project root or"
            " configured unidep in `pyproject.toml` with `[tool.unidep]`,"
            " but you are also using setuptools' `install_requires`."
            " Remove the `install_requires` line from `setup.py`."
        )
        raise RuntimeError(msg)

    deps = _deps(requirements_file)
    dist.install_requires = deps.dependencies  # type: ignore[attr-defined]

    if deps.extras:
        dist.extras_require = deps.extras  # type: ignore[attr-defined]
