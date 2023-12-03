#!/usr/bin/env python3
"""requirements.yaml - Unified Conda and Pip requirements management.

This module provides a command-line tool for managing conda environment.yaml files.
"""
from __future__ import annotations

import platform
import re
import sys
import warnings
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, cast

from ruamel.yaml import YAML

from unidep._conflicts import resolve_conflicts as _resolve_conflicts
from unidep._version import __version__

if TYPE_CHECKING:
    from ruamel.yaml.comments import CommentedMap
    from setuptools import Distribution


if sys.version_info >= (3, 8):
    from typing import Literal, get_args
else:  # pragma: no cover
    from typing_extensions import Literal, get_args


# Definitions

Platform = Literal[
    "linux-64",
    "linux-aarch64",
    "linux-ppc64le",
    "osx-64",
    "osx-arm64",
    "win-64",
]
Selector = Literal[
    "linux64",
    "aarch64",
    "ppc64le",
    "osx64",
    "arm64",
    "win64",
    "win",
    "unix",
    "linux",
    "osx",
    "macos",
]
CondaPip = Literal["conda", "pip"]

PEP508_MARKERS = {
    "linux-64": "sys_platform == 'linux' and platform_machine == 'x86_64'",
    "linux-aarch64": "sys_platform == 'linux' and platform_machine == 'aarch64'",
    "linux-ppc64le": "sys_platform == 'linux' and platform_machine == 'ppc64le'",
    "osx-64": "sys_platform == 'darwin' and platform_machine == 'x86_64'",
    "osx-arm64": "sys_platform == 'darwin' and platform_machine == 'arm64'",
    "win-64": "sys_platform == 'win32' and platform_machine == 'AMD64'",
    ("linux-64", "linux-aarch64", "linux-ppc64le"): "sys_platform == 'linux'",
    ("osx-64", "osx-arm64"): "sys_platform == 'darwin'",
    (
        "linux-64",
        "linux-aarch64",
        "linux-ppc64le",
        "osx-64",
        "osx-arm64",
    ): "sys_platform == 'linux' or sys_platform == 'darwin'",
}


# The first element of each tuple is the only unique selector
PLATFORM_SELECTOR_MAP: dict[Platform, list[Selector]] = {
    "linux-64": ["linux64", "unix", "linux"],
    "linux-aarch64": ["aarch64", "unix", "linux"],
    "linux-ppc64le": ["ppc64le", "unix", "linux"],
    # "osx64" is a selector unique to conda-build referring to
    # platforms on macOS and the Python architecture is x86-64
    "osx-64": ["osx64", "osx", "macos", "unix"],
    "osx-arm64": ["arm64", "osx", "macos", "unix"],
    "win-64": ["win64", "win"],
}
PLATFORM_SELECTOR_MAP_REVERSE: dict[Selector, set[Platform]] = {}
for _platform, _selectors in PLATFORM_SELECTOR_MAP.items():
    for _selector in _selectors:
        PLATFORM_SELECTOR_MAP_REVERSE.setdefault(_selector, set()).add(_platform)


def _simple_warning_format(
    message: Warning | str,
    category: type[Warning],  # noqa: ARG001
    filename: str,
    lineno: int,
    line: str | None = None,  # noqa: ARG001
) -> str:
    """Format warnings without code context."""
    return (
        f"---------------------\n"
        f"⚠️  *** WARNING *** ⚠️\n"
        f"{message}\n"
        f"Location: {filename}:{lineno}\n"
        f"---------------------\n"
    )


warnings.formatwarning = _simple_warning_format

# Functions for setuptools and conda


def find_requirements_files(
    base_dir: str | Path = ".",
    depth: int = 1,
    filename: str = "requirements.yaml",
    *,
    verbose: bool = False,
) -> list[Path]:
    """Scan a directory for requirements.yaml files."""
    base_path = Path(base_dir)
    found_files = []

    # Define a helper function to recursively scan directories
    def _scan_dir(path: Path, current_depth: int) -> None:
        if verbose:
            print(f"🔍 Scanning in `{path}` at depth {current_depth}")
        if current_depth > depth:
            return
        for child in path.iterdir():
            if child.is_dir():
                _scan_dir(child, current_depth + 1)
            elif child.name == filename:
                found_files.append(child)
                if verbose:
                    print(f"🔍 Found `{filename}` at `{child}`")

    _scan_dir(base_path, 0)
    return sorted(found_files)


def extract_matching_platforms(comment: str) -> list[Platform]:
    """Filter out lines from a requirements file that don't match the platform."""
    # we support a very limited set of selectors that adhere to platform only
    # refs:
    # https://docs.conda.io/projects/conda-build/en/latest/resources/define-metadata.html#preprocessing-selectors
    # https://github.com/conda/conda-lock/blob/3d2bf356e2cf3f7284407423f7032189677ba9be/conda_lock/src_parser/selectors.py

    sel_pat = re.compile(r"#\s*\[([^\[\]]+)\]")
    multiple_brackets_pat = re.compile(r"#.*\].*\[")  # Detects multiple brackets

    filtered_platforms = set()

    for line in comment.splitlines(keepends=False):
        if multiple_brackets_pat.search(line):
            msg = f"Multiple bracketed selectors found in line: '{line}'"
            raise ValueError(msg)

        m = sel_pat.search(line)
        if m:
            conds = m.group(1).split()
            for cond in conds:
                if cond not in PLATFORM_SELECTOR_MAP_REVERSE:
                    valid = list(PLATFORM_SELECTOR_MAP_REVERSE.keys())
                    msg = f"Unsupported platform specifier: '{comment}' use one of {valid}"  # noqa: E501
                    raise ValueError(msg)
                cond = cast(Selector, cond)
                for _platform in PLATFORM_SELECTOR_MAP_REVERSE[cond]:
                    filtered_platforms.add(_platform)

    return list(filtered_platforms)


def _build_pep508_environment_marker(
    platforms: list[Platform | tuple[Platform, ...]],
) -> str:
    """Generate a PEP 508 selector for a list of platforms."""
    sorted_platforms = tuple(sorted(platforms))
    if sorted_platforms in PEP508_MARKERS:
        return PEP508_MARKERS[sorted_platforms]  # type: ignore[index]
    environment_markers = [
        PEP508_MARKERS[platform]
        for platform in sorted(sorted_platforms)
        if platform in PEP508_MARKERS
    ]
    return " or ".join(environment_markers)


def _extract_first_comment(
    commented_map: CommentedMap,
    index_or_key: int | str,
) -> str | None:
    comments = commented_map.ca.items.get(index_or_key, None)
    if comments is None:
        return None
    comment_strings = next(
        c.value.split("\n")[0].rstrip().lstrip() for c in comments if c is not None
    )
    if not comment_strings:
        # empty string
        return None
    return "".join(comment_strings)


def _extract_name_and_pin(package_str: str) -> tuple[str, str | None]:
    """Splits a string into package name and version pinning."""
    # Regular expression to match package name and version pinning
    match = re.match(r"([a-zA-Z0-9_-]+)\s*(.*)", package_str)
    if match:
        package_name = match.group(1).strip()
        version_pin = match.group(2).strip()

        # Return None if version pinning is missing or empty
        if not version_pin:
            return package_name, None
        return package_name, version_pin

    msg = f"Invalid package string: '{package_str}'"
    raise ValueError(msg)


def _parse_dependency(
    dependency: str,
    dependencies: CommentedMap,
    index_or_key: int | str,
    which: Literal["conda", "pip", "both"],
) -> list[Meta]:
    comment = _extract_first_comment(dependencies, index_or_key)
    name, pin = _extract_name_and_pin(dependency)
    if which == "both":
        return [Meta(name, "conda", comment, pin), Meta(name, "pip", comment, pin)]
    return [Meta(name, which, comment, pin)]


class Meta(NamedTuple):
    """Metadata for a dependency."""

    name: str
    which: Literal["conda", "pip"]
    comment: str | None = None
    pin: str | None = None

    def platforms(self) -> list[Platform] | None:
        """Return the platforms for this dependency."""
        if self.comment is None:
            return None
        return extract_matching_platforms(self.comment) or None

    def pprint(self) -> str:
        """Pretty print the dependency."""
        result = f"{self.name}"
        if self.pin is not None:
            result += f" {self.pin}"
        if self.comment is not None:
            result += f" {self.comment}"
        return result


class ParsedRequirements(NamedTuple):
    """Requirements with comments."""

    channels: list[str]
    platforms: list[Platform]
    requirements: dict[str, list[Meta]]


class Requirements(NamedTuple):
    """Requirements as CommentedSeq."""

    # mypy doesn't support CommentedSeq[str], so we use list[str] instead.
    channels: list[str]  # actually a CommentedSeq[str]
    conda: list[str]  # actually a CommentedSeq[str]
    pip: list[str]  # actually a CommentedSeq[str]


def _include_path(include: str) -> Path:
    """Return the path to an included file."""
    path = Path(include)
    if path.is_dir():
        path /= "requirements.yaml"
    return path.resolve()


def parse_yaml_requirements(  # noqa: PLR0912
    *paths: Path,
    verbose: bool = False,
) -> ParsedRequirements:
    """Parse a list of `requirements.yaml` files including comments."""
    requirements: dict[str, list[Meta]] = defaultdict(list)
    channels: set[str] = set()
    platforms: set[Platform] = set()
    datas = []
    seen: set[Path] = set()
    yaml = YAML(typ="rt")
    for p in paths:
        if verbose:
            print(f"📄 Parsing `{p}`")
        with p.open() as f:
            data = yaml.load(f)
        datas.append(data)
        seen.add(p.resolve())

        # Deal with includes
        for include in data.get("includes", []):
            include_path = _include_path(p.parent / include)
            if include_path in seen:
                continue  # Avoids circular includes
            if verbose:
                print(f"📄 Parsing include `{include}`")
            with include_path.open() as f:
                datas.append(yaml.load(f))
            seen.add(include_path)

    for data in datas:
        for channel in data.get("channels", []):
            channels.add(channel)
        for _platform in data.get("platforms", []):
            platforms.add(_platform)
        if "dependencies" not in data:
            continue
        dependencies = data["dependencies"]
        for i, dep in enumerate(data["dependencies"]):
            if isinstance(dep, str):
                metas = _parse_dependency(dep, dependencies, i, "both")
                for meta in metas:
                    requirements[meta.name].append(meta)
                continue
            for which in ["conda", "pip"]:
                if which in dep:
                    metas = _parse_dependency(dep[which], dep, which, which)  # type: ignore[arg-type]
                    for meta in metas:
                        requirements[meta.name].append(meta)

    return ParsedRequirements(sorted(channels), sorted(platforms), dict(requirements))


def _extract_project_dependencies(
    path: Path,
    base_path: Path,
    processed: set,
    dependencies: dict[str, set[str]],
    *,
    check_pip_installable: bool = True,
    verbose: bool = False,
) -> None:
    if path in processed:
        return
    processed.add(path)
    yaml = YAML(typ="safe")
    with path.open() as f:
        data = yaml.load(f)
    for include in data.get("includes", []):
        include_path = _include_path(path.parent / include)
        if not include_path.exists():
            msg = f"Include file `{include_path}` does not exist."
            raise FileNotFoundError(msg)
        include_base_path = str(include_path.parent)
        if include_base_path == str(base_path):
            continue
        if not check_pip_installable or (
            _is_pip_installable(base_path) and _is_pip_installable(include_path.parent)
        ):
            dependencies[str(base_path)].add(include_base_path)
        if verbose:
            print(f"🔗 Adding include `{include_path}`")
        _extract_project_dependencies(
            include_path,
            base_path,
            processed,
            dependencies,
            check_pip_installable=check_pip_installable,
        )


def parse_project_dependencies(
    *paths: Path,
    check_pip_installable: bool = True,
    verbose: bool = False,
) -> dict[Path, list[Path]]:
    """Extract local project dependencies from a list of `requirements.yaml` files.

    Works by scanning for `includes` in the `requirements.yaml` files.
    """
    dependencies: dict[str, set[str]] = defaultdict(set)

    for p in paths:
        if verbose:
            print(f"🔗 Analyzing dependencies in `{p}`")
        base_path = p.resolve().parent
        _extract_project_dependencies(
            path=p,
            base_path=base_path,
            processed=set(),
            dependencies=dependencies,
            check_pip_installable=check_pip_installable,
            verbose=verbose,
        )

    return {
        Path(k): sorted({Path(v) for v in v_set})
        for k, v_set in sorted(dependencies.items())
    }


def _maybe_expand_none(
    platform_data: dict[Platform | None, dict[CondaPip, Meta]],
) -> None:
    if len(platform_data) > 1 and None in platform_data:
        sources = platform_data.pop(None)
        for _platform in get_args(Platform):
            if _platform not in platform_data:
                # Only add if there is not yet a specific platform
                platform_data[_platform] = sources


def _add_comment_to_file(
    filename: str | Path,
    extra_lines: list[str] | None = None,
) -> None:
    """Add a comment to the top of a file."""
    if extra_lines is None:
        extra_lines = []
    with open(filename, "r+") as f:  # noqa: PTH123
        content = f.read()
        f.seek(0, 0)
        command_line_args = " ".join(sys.argv[1:])
        txt = [
            f"# This file is created and managed by `unidep` {__version__}.",
            "# For details see https://github.com/basnijholt/unidep",
            f"# File generated with: `unidep {command_line_args}`",
            *extra_lines,
        ]
        content = "\n".join(txt) + "\n\n" + content
        f.write(content)


# Python setuptools integration functions


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
        _maybe_expand_none(platform_data)
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
                selector = _build_pep508_environment_marker(list(to_process.keys()))  # type: ignore[arg-type]
                dep_str = f"{dep_str}; {selector}"
            pip_deps.append(dep_str)
            continue

        for _platform, pip_meta in to_process.items():
            dep_str = pip_meta.name
            if pip_meta.pin is not None:
                dep_str += f" {pip_meta.pin}"
            if _platform is not None:
                selector = _build_pep508_environment_marker([_platform])
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
    resolved_requirements = _resolve_conflicts(requirements.requirements)
    return filter_python_dependencies(
        resolved_requirements,
        platforms=platforms or list(requirements.platforms),
    )


def _identify_current_platform() -> Platform:
    """Detect the current platform."""
    system = platform.system().lower()
    architecture = platform.machine().lower()

    if system == "linux":
        if architecture == "x86_64":
            return "linux-64"
        if architecture == "aarch64":
            return "linux-aarch64"
        if architecture == "ppc64le":
            return "linux-ppc64le"
        msg = "Unsupported Linux architecture"
        raise ValueError(msg)
    if system == "darwin":
        if architecture == "x86_64":
            return "osx-64"
        if architecture == "arm64":
            return "osx-arm64"
        msg = "Unsupported macOS architecture"
        raise ValueError(msg)
    if system == "windows":
        if "64" in architecture:
            return "win-64"
        msg = "Unsupported Windows architecture"
        raise ValueError(msg)
    msg = "Unsupported operating system"
    raise ValueError(msg)


def setuptools_finalizer(dist: Distribution) -> None:  # pragma: no cover
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
            platforms=[_identify_current_platform()],
            raises_if_missing=False,
        ),
    )


def _is_pip_installable(folder: str | Path) -> bool:  # pragma: no cover
    """Determine if the project is pip installable.

    Checks for existence of setup.py or [build-system] in pyproject.toml.
    """
    path = Path(folder)
    if (path / "setup.py").exists():
        return True

    # When toml makes it into the standard library, we can use that instead
    # For now this is good enough, except it doesn't handle the case where
    # [build-system] is inside of a multi-line literal string.
    pyproject_path = path / "pyproject.toml"
    if pyproject_path.exists():
        with pyproject_path.open("r") as file:
            for line in file:
                if line.strip().startswith("[build-system]"):
                    return True
    return False
