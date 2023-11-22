#!/usr/bin/env python3
"""requirements.yaml - Unified Conda and Pip requirements management.

This module provides a command-line tool for managing conda environment.yaml files.
"""
from __future__ import annotations

import argparse
import platform
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Sequence

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

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


__version__ = "0.12.0"
__all__ = [
    "find_requirements_files",
    "extract_matching_platforms",
    "parse_yaml_requirements",
    "create_conda_env_specification",
    "write_conda_environment_file",
    "parse_requirements_deduplicate",
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


def extract_matching_platforms(content: str) -> list[Platform]:
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

    for line in content.splitlines(keepends=False):
        if multiple_brackets_pat.search(line):
            msg = f"Multiple bracketed selectors found in line: '{line}'"
            raise ValueError(msg)

        m = sel_pat.search(line)
        if m:
            conds = m.group(1).split()
            for cond in conds:
                for _platform in reverse_selector_map.get(cond, []):
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


class ParsedRequirements(NamedTuple):
    """Requirements with comments."""

    channels: set[str]
    conda: dict[str, str | None]  # values are comments
    pip: dict[str, str | None]  # values are comments


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
    conda: dict[str, str | None] = {}
    pip: dict[str, str | None] = {}
    channels: set[str] = set()

    yaml = YAML(typ="rt")
    for p in paths:
        if verbose:
            print(f"Parsing {p}")
        with p.open() as f:
            reqs = yaml.load(f)
            for channel in reqs.get("channels", []):
                channels.add(channel)
            dependencies = reqs.get("dependencies", [])
            for i, dep in enumerate(dependencies):
                if isinstance(dep, str):
                    comment = _extract_first_comment(dependencies, i)
                    conda[dep] = comment
                    pip[dep] = comment
                    continue
                if "conda" in dep:
                    conda[dep["conda"]] = _extract_first_comment(dep, "conda")
                if "pip" in dep:
                    pip[dep["pip"]] = _extract_first_comment(dep, "pip")
    return ParsedRequirements(channels, conda, pip)


# Conda environment file generation functions


class CondaEnvironmentSpec(NamedTuple):
    """A conda environment."""

    channels: list[str]
    conda: list[str | dict[str, str]]
    pip: list[str]


def create_conda_env_specification(
    requirements: ParsedRequirements,
) -> CondaEnvironmentSpec:
    """Create a conda environment specification from `ParsedRequirements`."""
    conda: list[str | dict[str, str]] = []
    pip: list[str] = []
    for dependency, comment in requirements.conda.items():
        platforms = extract_matching_platforms(comment) if comment is not None else []
        if platforms:
            unique_platforms = {p.split("-", 1)[0] for p in platforms}
            dependencies = [
                {f"sel({_platform})": dependency} for _platform in unique_platforms
            ]
            conda.extend(dependencies)
        else:
            conda.append(dependency)

    for dependency, comment in requirements.pip.items():
        platforms = extract_matching_platforms(comment) if comment is not None else []
        if platforms:
            for _platform in platforms:
                selector = _build_pep508_environment_marker([_platform])
                dep = f"{dependency}; {selector}"
                pip.append(dep)
        else:
            pip.append(dependency)
    # Filter out duplicate packages that are both in conda and pip
    pip = [p for p in pip if p not in conda]
    return CondaEnvironmentSpec(list(requirements.channels), conda, pip)


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
    else:
        yaml.dump(env_data, sys.stdout)


# Python setuptools integration functions


def _remove_unsupported_platform_dependencies(
    dependencies: dict[str, str | None],
    platform: Platform,
) -> dict[str, str | None]:
    return {
        dependency: comment
        for dependency, comment in dependencies.items()
        if comment is None
        or not extract_matching_platforms(comment)
        or platform in extract_matching_platforms(comment)
    }


def _segregate_pip_conda_dependencies(
    requirements_with_comments: ParsedRequirements,
    pip_or_conda: Literal["pip", "conda"] = "conda",
    platform: Platform | None = None,
) -> ParsedRequirements:
    r = requirements_with_comments
    conda = (
        _remove_unsupported_platform_dependencies(r.conda, platform)
        if platform
        else r.conda
    )
    pip = (
        _remove_unsupported_platform_dependencies(r.pip, platform)
        if platform
        else r.pip
    )
    if pip_or_conda == "pip":
        conda = {k: v for k, v in conda.items() if k not in pip}
    elif pip_or_conda == "conda":
        pip = {k: v for k, v in pip.items() if k not in conda}
    else:  # pragma: no cover
        msg = f"Invalid value for `pip_or_conda`: {pip_or_conda}"
        raise ValueError(msg)
    return ParsedRequirements(r.channels, conda, pip)


def _convert_to_commented_requirements(
    parsed_requirements: ParsedRequirements,
) -> Requirements:
    """Convert a `ParsedRequirements` to a `Requirements` with comments.

    Here we use `CommentedSeq` instead of `list` to preserve comments, but
    `CommentedSeq` behaves just like a `list`.

    Note that we're preserving the comments here, however, when writing the
    environment file, we're not preserving the comments.
    """
    conda = CommentedSeq()
    pip = CommentedSeq()
    channels = list(parsed_requirements.channels)

    for i, (dependency, comment) in enumerate(parsed_requirements.conda.items()):
        conda.append(dependency)
        if comment is not None:
            conda.yaml_add_eol_comment(comment, i)

    for i, (dependency, comment) in enumerate(parsed_requirements.pip.items()):
        pip.append(dependency)
        if comment is not None:
            pip.yaml_add_eol_comment(comment, i)

    return Requirements(channels, conda, pip)


def parse_requirements_deduplicate(
    paths: Sequence[Path],
    *,
    verbose: bool = False,
    pip_or_conda: Literal["pip", "conda"] = "conda",
    platform: Platform | None = None,
) -> Requirements:
    """Parse a list of requirements.yaml files including comments."""
    requirements_with_comments = parse_yaml_requirements(paths, verbose=verbose)
    deduplicated_requirements = _segregate_pip_conda_dependencies(
        requirements_with_comments,
        pip_or_conda,
        platform,
    )
    return _convert_to_commented_requirements(deduplicated_requirements)


def get_python_dependencies(
    filename: str = "requirements.yaml",
    *,
    verbose: bool = False,
    platform: Platform | None = None,
    raises_if_missing: bool = True,
) -> list[str]:
    """Extract Python (pip) requirements from requirements.yaml file."""
    p = Path(filename)
    if not p.exists():
        if raises_if_missing:
            msg = f"File {filename} not found."
            raise FileNotFoundError(msg)
        return []
    python_deps = parse_requirements_deduplicate(
        [p],
        pip_or_conda="pip",
        verbose=verbose,
        platform=platform,
    ).pip
    return list(python_deps)


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
            str(requirements_file),
            platform=_identify_current_platform(),
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
    env_spec = create_conda_env_specification(requirements)
    output_file = None if args.stdout else args.output
    write_conda_environment_file(env_spec, output_file, args.name, verbose=verbose)
    if output_file:
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


if __name__ == "__main__":
    main()
