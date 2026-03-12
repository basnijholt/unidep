#!/usr/bin/env python3
"""unidep - Unified Conda and Pip requirements management.

This module provides setuptools integration for unidep.
"""

from __future__ import annotations

from pathlib import Path, PurePath
from typing import TYPE_CHECKING, NamedTuple

from ruamel.yaml import YAML

from unidep._conflicts import resolve_conflicts
from unidep._dependencies_parsing import (
    _load,
    available_optional_dependencies,
    get_local_dependencies,
    parse_requirements,
)
from unidep.utils import (
    UnsupportedPlatformError,
    build_pep508_environment_marker,
    detect_conflicting_direct_reference_groups,
    detect_conflicting_direct_references,
    identify_current_platform,
    is_pip_installable,
    package_name_from_path,
    parse_folder_or_filename,
    pip_requirement_strings,
    selected_extra_names,
    split_path_and_extras,
    warn,
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


def _validated_setuptools_dependencies(deps: Dependencies) -> Dependencies:
    """Reject dependency metadata that conflicts across installable extras."""
    extra_group_names = {
        section: f"optional dependency `{section}`" for section in deps.extras
    }
    requirement_groups = {"dependencies": deps.dependencies}
    requirement_groups.update(
        {
            extra_group_names[section]: section_dependencies
            for section, section_dependencies in deps.extras.items()
        },
    )
    deduplicated_groups = detect_conflicting_direct_reference_groups(
        requirement_groups,
        context="preparing setuptools metadata",
    )
    return Dependencies(
        dependencies=list(deduplicated_groups["dependencies"]),
        extras={
            section: deduplicated_groups[extra_group_names[section]]
            for section in deps.extras
        },
    )


def get_python_dependencies(  # noqa: PLR0912, PLR0915
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

    optional_sections = available_optional_dependencies(p.path)
    selected_extras = selected_extra_names(
        p.extras,
        optional_sections,
        dependency_file=p.path,
    )
    requirements = parse_requirements(
        p.path,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        verbose=verbose,
        extras=[selected_extras],
        include_local_dependencies=include_local_dependencies,
    )
    all_optional_requirements = parse_requirements(
        p.path,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        verbose=verbose,
        extras="*",
        include_local_dependencies=False,
    )
    if not platforms:
        platforms = list(requirements.platforms)
    raw_requirement_groups = {
        "dependencies": pip_requirement_strings(
            requirements.requirements,
            platforms=platforms,
        ),
    }
    raw_requirement_groups.update(
        {
            f"optional dependency `{section}`": pip_requirement_strings(
                requirements.optional_dependencies[section],
                platforms=platforms,
            )
            for section in selected_extras
            if section in requirements.optional_dependencies
        },
    )
    detect_conflicting_direct_reference_groups(
        raw_requirement_groups,
        context="collecting Python dependencies",
    )
    resolved = resolve_conflicts(requirements.requirements, platforms)
    dependencies = filter_python_dependencies(resolved)
    # Resolve optional dependency groups separately; direct-reference conflicts
    # across the groups are validated below.
    # Portable metadata should keep declared extras visible even when every
    # local-only entry in a section gets dropped.
    extras: dict[str, list[str]] = (
        {section: [] for section in optional_sections}
        if not include_local_dependencies
        else {}
    )
    extras.update(
        {
            section: filter_python_dependencies(resolve_conflicts(reqs, platforms))
            for section, reqs in all_optional_requirements.optional_dependencies.items()
        },
    )
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

        # Portable mode uses PyPI alternatives when available and otherwise
        # omits local path entries from published requirements.
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
                dependencies.append(f"{package_name_from_path(abs_local)} @ {uri}")
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

    dependencies = detect_conflicting_direct_references(
        dependencies,
        context="collecting Python dependencies",
    )
    extras = {
        section: detect_conflicting_direct_references(
            deps,
            context=f"collecting optional dependency `{section}`",
        )
        for section, deps in extras.items()
    }
    if selected_extras:
        extra_group_names = {
            section: f"optional dependency `{section}`"
            for section in selected_extras
            if section in extras
        }
        requirement_groups = {"dependencies": dependencies}
        requirement_groups.update(
            {
                extra_group_names[section]: extras[section]
                for section in selected_extras
                if section in extras
            },
        )
        deduplicated_groups = detect_conflicting_direct_reference_groups(
            requirement_groups,
            context="collecting Python dependencies",
        )
        dependencies = deduplicated_groups["dependencies"]
        extras = {
            section: deduplicated_groups[extra_group_names[section]]
            if section in selected_extras
            else deps
            for section, deps in extras.items()
        }
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

    # Build metadata must stay portable: never publish local file URLs.
    return get_python_dependencies(
        requirements_file,
        platforms=platforms,
        raises_if_missing=False,
        include_local_dependencies=False,
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

    deps = _validated_setuptools_dependencies(_deps(requirements_file))
    dist.install_requires = deps.dependencies  # type: ignore[attr-defined]

    if deps.extras:
        dist.extras_require = deps.extras  # type: ignore[attr-defined]
