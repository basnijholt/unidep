#!/usr/bin/env python3
"""requirements.yaml - Unified Conda and Pip requirements management.

This module provides a command-line tool for managing conda environment.yaml files.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from unidep._conflicts import resolve_conflicts
from unidep._yaml_parsing import parse_yaml_requirements
from unidep.utils import (
    _maybe_expand_none_to_all_platforms,
    build_pep508_environment_marker,
    identify_current_platform,
)

if TYPE_CHECKING:
    from setuptools import Distribution

    from unidep.platform_definitions import (
        CondaPip,
        Meta,
        Platform,
    )


def filter_python_dependencies(
    resolved_requirements: dict[str, dict[Platform | None, dict[CondaPip, Meta]]],
    platforms: list[Platform] | None = None,
) -> list[str]:
    """Filter out conda dependencies and return only pip dependencies.

    Examples
    --------
    >>> requirements = parse_yaml_requirements("requirements.yaml")
    >>> resolved_requirements = resolve_conflicts(requirements.requirements)
    >>> python_dependencies = filter_python_dependencies(resolved_requirements)
    """
    pip_deps = []
    for platform_data in resolved_requirements.values():
        _maybe_expand_none_to_all_platforms(platform_data)
        to_process: dict[Platform | None, Meta] = {}  # platform -> Meta
        for _platform, sources in platform_data.items():
            if (
                _platform is not None
                and platforms is not None
                and _platform not in platforms
            ):
                continue
            pip_meta = sources.get("pip")
            if pip_meta:
                to_process[_platform] = pip_meta
        if not to_process:
            continue

        # Check if all Meta objects are identical
        first_meta = next(iter(to_process.values()))
        if all(meta == first_meta for meta in to_process.values()):
            # Build a single combined environment marker
            dep_str = first_meta.name
            if first_meta.pin is not None:
                dep_str += f" {first_meta.pin}"
            if _platform is not None:
                selector = build_pep508_environment_marker(list(to_process.keys()))  # type: ignore[arg-type]
                dep_str = f"{dep_str}; {selector}"
            pip_deps.append(dep_str)
            continue

        for _platform, pip_meta in to_process.items():
            dep_str = pip_meta.name
            if pip_meta.pin is not None:
                dep_str += f" {pip_meta.pin}"
            if _platform is not None:
                selector = build_pep508_environment_marker([_platform])
                dep_str = f"{dep_str}; {selector}"
            pip_deps.append(dep_str)
    return sorted(pip_deps)


def get_python_dependencies(
    filename: str | Path = "requirements.yaml",
    *,
    verbose: bool = False,
    platforms: list[Platform] | None = None,
    raises_if_missing: bool = True,
) -> list[str]:
    """Extract Python (pip) requirements from requirements.yaml file."""
    p = Path(filename)
    if not p.exists():
        if raises_if_missing:
            msg = f"File {filename} not found."
            raise FileNotFoundError(msg)
        return []

    requirements = parse_yaml_requirements(p, verbose=verbose)
    resolved_requirements = resolve_conflicts(requirements.requirements)
    return filter_python_dependencies(
        resolved_requirements,
        platforms=platforms or list(requirements.platforms),
    )


def _setuptools_finalizer(dist: Distribution) -> None:  # pragma: no cover
    """Entry point called by setuptools to get the dependencies for a project."""
    # PEP 517 says that "All hooks are run with working directory set to the
    # root of the source tree".
    project_root = Path().resolve()
    requirements_file = project_root / "requirements.yaml"
    if requirements_file.exists() and dist.install_requires:
        msg = (
            "You have a requirements.yaml file in your project root, "
            "but you are also using setuptools' install_requires. "
            "Please use one or the other, but not both."
        )
        raise RuntimeError(msg)
    dist.install_requires = list(
        get_python_dependencies(
            requirements_file,
            platforms=[identify_current_platform()],
            raises_if_missing=False,
        ),
    )
