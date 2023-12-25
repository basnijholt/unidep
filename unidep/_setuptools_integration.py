#!/usr/bin/env python3
"""unidep - Unified Conda and Pip requirements management.

This module provides setuptools integration for unidep.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from unidep._conflicts import resolve_conflicts
from unidep._dependencies_parsing import parse_requirements
from unidep.utils import (
    build_pep508_environment_marker,
    dependencies_filename,
    identify_current_platform,
)

if TYPE_CHECKING:
    import sys

    from setuptools import Distribution

    from unidep.platform_definitions import (
        CondaPip,
        Platform,
        Spec,
    )

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:
        from typing_extensions import Literal


def filter_python_dependencies(
    resolved: dict[str, dict[Platform | None, dict[CondaPip, Spec]]],
) -> list[str]:
    """Filter out conda dependencies and return only pip dependencies.

    Examples
    --------
    >>> requirements = parse_requirements("requirements.yaml")
    >>> resolved = resolve_conflicts(
    ...     requirements.requirements, requirements.platforms
    ... )
    >>> python_deps = filter_python_dependencies(resolved)
    """
    pip_deps = []
    for platform_data in resolved.values():
        to_process: dict[Platform | None, Spec] = {}  # platform -> Spec
        for _platform, sources in platform_data.items():
            pip_spec = sources.get("pip")
            if pip_spec:
                to_process[_platform] = pip_spec
        if not to_process:
            continue

        # Check if all Spec objects are identical
        first_spec = next(iter(to_process.values()))
        if all(spec == first_spec for spec in to_process.values()):
            # Build a single combined environment marker
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


def get_python_dependencies(
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
) -> list[str]:
    """Extract Python (pip) requirements from a `requirements.yaml` or `pyproject.toml` file."""  # noqa: E501
    p = Path(filename)
    if not p.exists():
        if raises_if_missing:
            msg = f"File {filename} not found."
            raise FileNotFoundError(msg)
        return []

    requirements = parse_requirements(
        p,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        verbose=verbose,
    )
    resolved = resolve_conflicts(
        requirements.requirements,
        platforms or list(requirements.platforms),
    )
    return filter_python_dependencies(resolved)


def _setuptools_finalizer(dist: Distribution) -> None:  # pragma: no cover
    """Entry point called by setuptools to get the dependencies for a project."""
    # PEP 517 says that "All hooks are run with working directory set to the
    # root of the source tree".
    project_root = Path().resolve()
    try:
        requirements_file = dependencies_filename(project_root)
    except FileNotFoundError:
        return
    if requirements_file.exists() and dist.install_requires:
        msg = (
            "You have a `requirements.yaml` file in your project root or"
            " configured unidep in `pyproject.toml` with `[tool.unidep]`,"
            " but you are also using setuptools' `install_requires`."
            " Remove the `install_requires` line from `setup.py`."
        )
        raise RuntimeError(msg)
    dist.install_requires = get_python_dependencies(
        requirements_file,
        platforms=[identify_current_platform()],
        raises_if_missing=False,
    )
