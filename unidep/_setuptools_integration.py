#!/usr/bin/env python3
"""unidep - Unified Conda and Pip requirements management.

This module provides setuptools integration for unidep.
"""

from __future__ import annotations

import ast
import configparser
import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from unidep._conflicts import resolve_conflicts
from unidep._dependencies_parsing import parse_local_dependencies, parse_requirements
from unidep.utils import (
    UnsupportedPlatformError,
    build_pep508_environment_marker,
    identify_current_platform,
    parse_folder_or_filename,
    warn,
)

try:  # pragma: no cover
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    HAS_TOML = True
except ImportError:  # pragma: no cover
    HAS_TOML = False


if TYPE_CHECKING:
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


class Dependencies(NamedTuple):
    dependencies: list[str]
    extras: dict[str, list[str]]


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
    resolved = resolve_conflicts(requirements.requirements, platforms)
    dependencies = filter_python_dependencies(resolved)
    # TODO[Bas]: This currently doesn't correctly handle  # noqa: TD004, TD003, FIX002
    # conflicts between sections in the extras and the main dependencies.
    extras = {
        section: filter_python_dependencies(resolve_conflicts(reqs, platforms))
        for section, reqs in requirements.optional_dependencies.items()
    }
    if include_local_dependencies:
        local_dependencies = parse_local_dependencies(
            p.path_with_extras,
            check_pip_installable=True,
            verbose=verbose,
            raise_if_missing=False,  # skip if local dep is not found
            # We don't need to warn about skipping the dependencies of
            # the local dependencies. That is only relevant for the
            # `unidep install` commands.
            warn_non_managed=False,
        )
        for paths in local_dependencies.values():
            for path in paths:
                name = _package_name_from_path(path)
                dependencies.append(f"{name} @ file://{path.as_posix()}")

    return Dependencies(dependencies=dependencies, extras=extras)


def _package_name_from_setup_cfg(file_path: Path) -> str:
    config = configparser.ConfigParser()
    config.read(file_path)
    name = config.get("metadata", "name", fallback=None)
    if name is None:
        msg = "Could not find the package name in the setup.cfg file."
        raise KeyError(msg)
    return name


def _package_name_from_setup_py(file_path: Path) -> str:
    with file_path.open() as f:
        file_content = f.read()

    tree = ast.parse(file_content)

    class SetupVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.package_name = None

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            if isinstance(node.func, ast.Name) and node.func.id == "setup":
                for keyword in node.keywords:
                    if keyword.arg == "name":
                        self.package_name = keyword.value.value  # type: ignore[attr-defined]

    visitor = SetupVisitor()
    visitor.visit(tree)
    if visitor.package_name is None:
        msg = "Could not find the package name in the setup.py file."
        raise KeyError(msg)
    assert isinstance(visitor.package_name, str)
    return visitor.package_name


def _package_name_from_pyproject_toml(file_path: Path) -> str:
    if not HAS_TOML:  # pragma: no cover
        msg = "toml is required to parse pyproject.toml files."
        raise ImportError(msg)
    with file_path.open("rb") as f:
        data = tomllib.load(f)
    with contextlib.suppress(KeyError):
        # PEP 621: setuptools, flit, hatch, pdm
        return data["project"]["name"]
    with contextlib.suppress(KeyError):
        # poetry doesn't follow any standard
        return data["tool"]["poetry"]["name"]
    msg = f"Could not find the package name in the pyproject.toml file: {data}."
    raise KeyError(msg)


def _package_name_from_path(path: Path) -> str:
    """Get the package name from a path."""
    pyproject_toml = path / "pyproject.toml"
    if pyproject_toml.exists():
        with contextlib.suppress(Exception):
            return _package_name_from_pyproject_toml(pyproject_toml)

    setup_cfg = path / "setup.cfg"
    if setup_cfg.exists():
        with contextlib.suppress(Exception):
            return _package_name_from_setup_cfg(setup_cfg)

    setup_py = path / "setup.py"
    if setup_py.exists():
        with contextlib.suppress(Exception):
            return _package_name_from_setup_py(setup_py)

    # Best guess for the package name is folder name.
    return path.name


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
    project_root = Path().resolve()
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
