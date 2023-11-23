#!/usr/bin/env python3
"""requirements.yaml - Unified Conda and Pip requirements management.

This module provides a command-line tool for managing conda environment.yaml files.
"""
from __future__ import annotations

import argparse
import platform
import re
import sys
import warnings
from collections import defaultdict
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Sequence

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

if TYPE_CHECKING:
    from setuptools import Distribution

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:
        from typing_extensions import Literal
    Platform = Literal[
        "linux-64",
        "linux-aarch64",
        "linux-ppc64le",
        "osx-64",
        "osx-arm64",
        "win-64",
    ]

with suppress(ImportError):  # not a hard dependency, but nice to have
    from rich import print


__version__ = "0.12.0"
__all__ = [
    "find_requirements_files",
    "extract_matching_platforms",
    "parse_yaml_requirements",
    "create_conda_env_specification",
    "write_conda_environment_file",
    "get_python_dependencies",
]

PEP508_MARKERS = {
    "linux-64": "sys_platform == 'linux' and platform_machine == 'x86_64'",
    "linux-aarch64": "sys_platform == 'linux' and platform_machine == 'aarch64'",
    "linux-ppc64le": "sys_platform == 'linux' and platform_machine == 'ppc64le'",
    "osx-64": "sys_platform == 'darwin' and platform_machine == 'x86_64'",
    "osx-arm64": "sys_platform == 'darwin' and platform_machine == 'arm64'",
    "win-64": "sys_platform == 'win32' and platform_machine == 'AMD64'",
}

# Functions for setuptools and conda


def find_requirements_files(
    base_dir: str | Path,
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
            print(f"Scanning in {path} at depth {current_depth}")
        if current_depth > depth:
            return
        for child in path.iterdir():
            if child.is_dir():
                _scan_dir(child, current_depth + 1)
            elif child.name == filename:
                found_files.append(child)
                if verbose:
                    print(f"Found {filename} at {child}")

    _scan_dir(base_path, 0)
    return found_files


def extract_matching_platforms(comment: str) -> list[Platform]:
    """Filter out lines from a requirements file that don't match the platform."""
    # we support a very limited set of selectors that adhere to platform only
    # refs:
    # https://docs.conda.io/projects/conda-build/en/latest/resources/define-metadata.html#preprocessing-selectors
    # https://github.com/conda/conda-lock/blob/3d2bf356e2cf3f7284407423f7032189677ba9be/conda_lock/src_parser/selectors.py

    platform_selector_map: dict[Platform, set[str]] = {
        "linux-64": {"linux64", "unix", "linux"},
        "linux-aarch64": {"aarch64", "unix", "linux"},
        "linux-ppc64le": {"ppc64le", "unix", "linux"},
        # "osx64" is a selector unique to conda-build referring to
        # platforms on macOS and the Python architecture is x86-64
        "osx-64": {"osx64", "osx", "macos", "unix"},
        "osx-arm64": {"arm64", "osx", "macos", "unix"},
        "win-64": {"win", "win64"},
    }

    # Reverse the platform_selector_map for easy lookup
    reverse_selector_map: dict[str, list[Platform]] = {}
    for key, values in platform_selector_map.items():
        for value in values:
            reverse_selector_map.setdefault(value, []).append(key)

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
                if cond not in reverse_selector_map:
                    msg = f"Unsupported platform specifier: '{comment}'"
                    raise ValueError(msg)
                for _platform in reverse_selector_map[cond]:
                    filtered_platforms.add(_platform)

    return list(filtered_platforms)


def _build_pep508_environment_marker(platforms: list[Platform]) -> str:
    """Generate a PEP 508 selector for a list of platforms."""
    environment_markers = [
        PEP508_MARKERS[platform] for platform in platforms if platform in PEP508_MARKERS
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
        return extract_matching_platforms(self.comment)

    def pprint(self) -> str:
        """Pretty print the dependency."""
        result = f"{self.name}"
        if self.pin is not None:
            result += f" {self.pin}"
        if self.comment is not None:
            result += f" {self.comment}"
        return result


def _all_platforms_identical(metas: list[Meta]) -> bool:
    """Check if all platforms are identical."""
    platforms = {meta.comment for meta in metas}
    if len(platforms) == 1:
        return True
    platform = metas[0].platforms()
    return all(meta.platforms == platform for meta in metas)


class ParsedRequirements(NamedTuple):
    """Requirements with comments."""

    channels: set[str]
    requirements: dict[str, list[Meta]]


class Requirements(NamedTuple):
    """Requirements as CommentedSeq."""

    # mypy doesn't support CommentedSeq[str], so we use list[str] instead.
    channels: list[str]  # actually a CommentedSeq[str]
    conda: list[str]  # actually a CommentedSeq[str]
    pip: list[str]  # actually a CommentedSeq[str]


def parse_yaml_requirements(
    paths: Sequence[Path],
    *,
    verbose: bool = False,
) -> ParsedRequirements:
    """Parse a list of requirements.yaml files including comments."""
    requirements: dict[str, list[Meta]] = defaultdict(list)
    channels: set[str] = set()

    yaml = YAML(typ="rt")
    for p in paths:
        if verbose:
            print(f"Parsing {p}")
        with p.open() as f:
            data = yaml.load(f)
            for channel in data.get("channels", []):
                channels.add(channel)
            if "dependencies" not in data:
                continue
            dependencies = data["dependencies"]
            for i, dep in enumerate(data["dependencies"]):
                if isinstance(dep, str):
                    metas = _parse_dependency(dep, dependencies, i, "both")
                else:
                    if "conda" in dep:
                        metas = _parse_dependency(dep["conda"], dep, "conda", "conda")
                    if "pip" in dep:
                        metas = _parse_dependency(dep["pip"], dep, "pip", "pip")
                for meta in metas:
                    requirements[meta.name].append(meta)
    return ParsedRequirements(channels, dict(requirements))


# Conflict resolution functions


def _prepare_metas_for_conflict_resolution(
    requirements: dict[str, list[Meta]],
) -> dict[str, dict[str, dict[str, list[Meta]]]]:
    """Prepare and group metadata for conflict resolution.

    This function groups metadata by platform and source for each package.

    :param requirements: Dictionary mapping package names to a list of Meta objects.
    :return: Dictionary mapping package names to grouped metadata.
    """
    prepared_data = {}
    for package, meta_list in requirements.items():
        grouped_metas: dict[str, dict[str, list[Meta]]] = defaultdict(
            lambda: defaultdict(list),
        )
        for meta in meta_list:
            platforms = meta.platforms()
            if platforms is None:
                platforms = [None]  # type: ignore[list-item]
            for _platform in platforms:
                grouped_metas[_platform][meta.which].append(meta)
        # Convert defaultdicts to dicts
        prepared_data[package] = {k: dict(v) for k, v in grouped_metas.items()}
    return prepared_data


def _select_preferred_version_within_platform(
    data: dict[str, dict[str, list[Meta]]],
) -> dict[str, dict[str, Meta]]:
    reduced_data: dict[str, dict[str, Meta]] = {}
    for _platform, packages in data.items():
        reduced_data[_platform] = {}
        for which, metas in packages.items():
            if len(metas) > 1:
                # Sort metas by presence of version pin and then by the pin itself
                metas.sort(key=lambda m: (m.pin is not None, m.pin), reverse=True)
                # Keep the first Meta, which has the highest priority
                selected_meta = metas[0]
                discarded_metas = [m for m in metas[1:] if m != selected_meta]
                if discarded_metas:
                    discarded_metas_str = ", ".join(
                        f"`{m.pprint()}` ({m.which})" for m in discarded_metas
                    )
                    on_platform = (
                        f" on platform `{_platform}`" if _platform is not None else ""
                    )
                    print(
                        f"⚠️ Conflict{on_platform}! Keeping `{selected_meta.pprint()}`"
                        f" ({which}), discarding {discarded_metas_str}",
                    )
                reduced_data[_platform][which] = selected_meta
            else:
                # Flatten the list
                reduced_data[_platform][which] = metas[0]
    return reduced_data


def _resolve_conda_pip_conflicts(sources: dict[str, Meta]) -> dict[str, Meta]:
    conda_meta = sources.get("conda")
    pip_meta = sources.get("pip")
    if not conda_meta or not pip_meta:  # If either is missing, there is no conflict
        return sources

    # Compare version pins to resolve conflicts
    if conda_meta.pin and not pip_meta.pin:
        return {"conda": conda_meta}  # Prefer conda if it has a pin
    if pip_meta.pin and not conda_meta.pin:
        return {"pip": pip_meta}  # Prefer pip if it has a pin
    if conda_meta.pin == pip_meta.pin:
        return {"conda": conda_meta, "pip": pip_meta}  # Keep both if pins are identical

    # Handle conflict where both conda and pip have different pins
    warnings.warn(
        f"Conflicting version pins for conda ({conda_meta.pin})"
        f" and pip ({pip_meta.pin}). Keeping both.",
        stacklevel=2,
    )
    return {"conda": conda_meta, "pip": pip_meta}


def resolve_conflicts(
    requirements: dict[str, list[Meta]],
) -> dict[str, dict[str, Meta]]:
    prepared = _prepare_metas_for_conflict_resolution(requirements)

    resolved = {
        pkg: _select_preferred_version_within_platform(data)
        for pkg, data in prepared.items()
    }
    for platforms in resolved.values():
        for _platform, sources in platforms.items():
            platforms[_platform] = _resolve_conda_pip_conflicts(sources)
    return resolved


# Conda environment file generation functions


class CondaEnvironmentSpec(NamedTuple):
    """A conda environment."""

    channels: list[str]
    conda: list[str | dict[str, str]]
    pip: list[str]


ALLOWED_CONDA = ["unix", "linux", "osx", "win"]


def _conda_sel(sel: str) -> str:
    """Return the allowed `sel(platform)` string."""
    _platform = sel.split("-", 1)[0]
    assert _platform in ALLOWED_CONDA, f"Invalid platform: {_platform}"
    return _platform


def _maybe_expand_none(platform_data: dict[str, dict[str, Meta]]) -> None:
    if len(platform_data) > 1 and None in platform_data:
        # Need to expand None to ALLOWED_CONDA
        sources = platform_data.pop(None)
        for _platform in ALLOWED_CONDA:
            platform_data[_platform] = sources


def create_conda_env_specification(
    resolved_requirements: dict[str, dict[str, dict[str, Meta]]],
    channels: set[str],
) -> CondaEnvironmentSpec:
    """Create a conda environment specification from resolved requirements."""
    conda_deps = []
    pip_deps = []
    for platform_data in resolved_requirements.values():
        _platform_data = deepcopy(platform_data)
        _maybe_expand_none(_platform_data)

        if len(_platform_data) == 1:
            _platform = next(iter(_platform_data))
            if _platform is not None:
                # swap e.g., linux-64 to linux
                sources = _platform_data.pop(_platform)
                _platform_data[_conda_sel(_platform)] = sources
        else:
            valid: dict[str, list[dict[str, Meta]]] = defaultdict(list)
            for _platform, sources in _platform_data.items():
                sel = _conda_sel(_platform)
                valid[sel].append(sources)

            # Now make sure that all values in valid are identical, if not
            # we have a conflict, and we select one of the values.
            _platform_data = {}
            for sel, sources in valid.items():
                first, *others = sources
                if not all(other == first for other in others):
                    # We have a conflict, select the first one.
                    msg = (
                        f"Conflicting dependencies for platform {sel}: {sources},"
                        f" keeping {first}, discarding {others}"
                    )
                    warnings.warn(msg, stacklevel=2)
                _platform_data[sel] = first

        for sel, sources in _platform_data.items():
            conda_meta = sources.get("conda")
            pip_meta = sources.get("pip")

            if conda_meta:
                dep_str = conda_meta.name
                if conda_meta.pin is not None:
                    dep_str += f" {conda_meta.pin}"
                if sel is not None:
                    dep_str = {f"sel({sel})": dep_str}
                conda_deps.append(dep_str)

            if not conda_meta and pip_meta:
                print(pip_meta)
                dep_str = pip_meta.name
                if pip_meta.pin is not None:
                    dep_str += f" {pip_meta.pin}"
                if sel is not None:
                    platforms = extract_matching_platforms(pip_meta.comment)
                    selector = _build_pep508_environment_marker(platforms)
                    dep_str = f"{dep_str}; {selector}"
                pip_deps.append(dep_str)

    return CondaEnvironmentSpec(list(channels), conda_deps, pip_deps)


def write_conda_environment_file(
    env_spec: CondaEnvironmentSpec,
    output_file: str | None = "environment.yaml",
    name: str = "myenv",
    *,
    verbose: bool = False,
) -> None:
    """Generate a conda environment.yaml file or print to stdout."""
    resolved_dependencies = deepcopy(env_spec.conda)
    resolved_dependencies.append({"pip": env_spec.pip})  # type: ignore[arg-type, dict-item]
    env_data = CommentedMap(
        {
            "name": name,
            "channels": env_spec.channels,
            "dependencies": resolved_dependencies,
        },
    )
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    if output_file:
        if verbose:
            print(f"Generating environment file at {output_file}")
        with open(output_file, "w") as f:  # noqa: PTH123
            yaml.dump(env_data, f)
        if verbose:
            print("Environment file generated successfully.")

        with open(output_file, "r+") as f:  # noqa: PTH123
            content = f.read()
            f.seek(0, 0)
            command_line_args = " ".join(sys.argv[1:])
            txt = [
                f"# This file is created and managed by `conda-join` {__version__}.",
                "# For details see https://github.com/basnijholt/conda-join",
                f"# File generated with: `conda-join {command_line_args}`",
            ]
            content = "\n".join(txt) + "\n\n" + content
            f.write(content)
    else:
        yaml.dump(env_data, sys.stdout)


# Python setuptools integration functions


def filter_python_dependencies(
    resolved_requirements,
    platforms: list[Platform] | None = None,
) -> dict[str, dict[str, Meta]]:
    pip_deps = []
    for platform_data in resolved_requirements.values():
        _maybe_expand_none(platform_data)
        for _platform, sources in platform_data.items():
            if platforms is not None and _platform not in platforms:
                continue
            pip_meta = sources.get("pip")
            if pip_meta:
                dep_str = pip_meta.name
                if pip_meta.pin is not None:
                    dep_str += f" {pip_meta.pin}"
                if _platform is not None:
                    selector = _build_pep508_environment_marker([_platform])
                    print(dep_str, _platform, selector)
                    dep_str = f"{dep_str}; {selector}"
                pip_deps.append(dep_str)
    return pip_deps


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

    requirements = parse_yaml_requirements([p], verbose=verbose)
    resolved_requirements = resolve_conflicts(requirements.requirements)
    pip_deps = filter_python_dependencies(resolved_requirements, platforms=platforms)
    return pip_deps


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
    """The entry point called by setuptools to retrieve the dependencies for a project."""
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
            platform=[_identify_current_platform()],
            raises_if_missing=False,
        ),
    )


def main() -> None:  # pragma: no cover
    """Main entry point for the command-line tool."""
    parser = argparse.ArgumentParser(
        description="Unified Conda and Pip requirements management.",
    )
    parser.add_argument(
        "-d",
        "--directory",
        type=str,
        default=".",
        help="Base directory to scan for requirements.yaml files, by default `.`",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default="environment.yaml",
        help="Output file for the conda environment, by default `environment.yaml`",
    )
    parser.add_argument(
        "-n",
        "--name",
        type=str,
        default="myenv",
        help="Name of the conda environment, by default `myenv`",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Depth to scan for requirements.yaml files, by default 1",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Output to stdout instead of a file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print verbose output",
    )

    args = parser.parse_args()

    # When using stdout, suppress verbose output
    verbose = args.verbose and not args.stdout

    found_files = find_requirements_files(
        args.directory,
        args.depth,
        verbose=verbose,
    )
    requirements = parse_yaml_requirements(found_files, verbose=verbose)
    resolved_requirements = resolve_conflicts(requirements.requirements)
    env_spec = create_conda_env_specification(
        resolved_requirements,
        requirements.channels,
    )
    output_file = None if args.stdout else args.output
    write_conda_environment_file(env_spec, output_file, args.name, verbose=verbose)
    if output_file:
        found_files_str = ", ".join(str(f) for f in found_files)
        print(f"✅ Generated environment file at `{output_file}` from {found_files_str}")


if __name__ == "__main__":
    main()
