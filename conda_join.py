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
    Platforms = Literal[
        "linux-64",
        "linux-aarch64",
        "linux-ppc64le",
        "osx-64",
        "osx-arm64",
        "win-64",
    ]


__version__ = "0.12.0"


PEP508_MARKERS = {
    "linux-64": "sys_platform == 'linux' and platform_machine == 'x86_64'",
    "linux-aarch64": "sys_platform == 'linux' and platform_machine == 'aarch64'",
    "linux-ppc64le": "sys_platform == 'linux' and platform_machine == 'ppc64le'",
    "osx-64": "sys_platform == 'darwin' and platform_machine == 'x86_64'",
    "osx-arm64": "sys_platform == 'darwin' and platform_machine == 'arm64'",
    "win-64": "sys_platform == 'win32' and platform_machine == 'AMD64'",
}


def scan_requirements(
    base_dir: str | Path,
    depth: int = 1,
    filename: str = "requirements.yaml",
    *,
    verbose: bool = False,
) -> list[Path]:
    """Scan a directory for requirements.yaml files."""
    base_path = Path(base_dir)
    requirements_files = []

    # Define a helper function to recursively scan directories
    def scan_dir(path: Path, current_depth: int) -> None:
        if verbose:
            print(f"Scanning in {path} at depth {current_depth}")
        if current_depth > depth:
            return
        for child in path.iterdir():
            if child.is_dir():
                scan_dir(child, current_depth + 1)
            elif child.name == filename:
                requirements_files.append(child)
                if verbose:
                    print(f"Found {filename} at {child}")

    scan_dir(base_path, 0)
    return requirements_files


def filter_platform_selectors(content: str) -> list[Platforms]:
    """Filter out lines from a requirements file that don't match the platform."""
    # we support a very limited set of selectors that adhere to platform only
    # refs:
    # https://docs.conda.io/projects/conda-build/en/latest/resources/define-metadata.html#preprocessing-selectors
    # https://github.com/conda/conda-lock/blob/3d2bf356e2cf3f7284407423f7032189677ba9be/conda_lock/src_parser/selectors.py

    platform_sel: dict[Platforms, set[str]] = {
        "linux-64": {"linux64", "unix", "linux"},
        "linux-aarch64": {"aarch64", "unix", "linux"},
        "linux-ppc64le": {"ppc64le", "unix", "linux"},
        # "osx64" is a selector unique to conda-build referring to
        # platforms on macOS and the Python architecture is x86-64
        "osx-64": {"osx64", "osx", "macos", "unix"},
        "osx-arm64": {"arm64", "osx", "macos", "unix"},
        "win-64": {"win", "win64"},
    }

    # Reverse the platform_sel for easy lookup
    reverse_platform_sel: dict[str, list[Platforms]] = {}
    for key, values in platform_sel.items():
        for value in values:
            reverse_platform_sel.setdefault(value, []).append(key)

    sel_pat = re.compile(r"#\s*\[([^\[\]]+)\]")
    multiple_brackets_pat = re.compile(r"#.*\].*\[")  # Detects multiple brackets

    matched_platforms = set()

    for line in content.splitlines(keepends=False):
        if multiple_brackets_pat.search(line):
            msg = f"Multiple bracketed selectors found in line: '{line}'"
            raise ValueError(msg)

        m = sel_pat.search(line)
        if m:
            conds = m.group(1).split()
            for cond in conds:
                for _platform in reverse_platform_sel.get(cond, []):
                    matched_platforms.add(_platform)

    return list(matched_platforms)


def pep508_selector(platforms: list[Platforms]) -> str:
    """Generate a PEP 508 selector for a list of platforms."""
    selectors = [
        PEP508_MARKERS[platform] for platform in platforms if platform in PEP508_MARKERS
    ]
    return " or ".join(selectors)


def _comment(commented_map: CommentedMap, index_or_key: int | str) -> str | None:
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


class RequirementsWithComments(NamedTuple):
    """Requirements with comments."""

    channels: set[str]
    conda: dict[str, str | None]
    pip: dict[str, str | None]


class Requirements(NamedTuple):
    """Requirements as CommentedSeq."""

    # mypy doesn't support CommentedSeq[str], so we use list[str] instead.
    channels: list[str]  # actually a CommentedSeq[str]
    conda: list[str]  # actually a CommentedSeq[str]
    pip: list[str]  # actually a CommentedSeq[str]


def _initial_parse_requirements(
    paths: Sequence[Path],
    *,
    verbose: bool = False,
) -> RequirementsWithComments:
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
                    comment = _comment(dependencies, i)
                    conda[dep] = comment
                    pip[dep] = comment
                    continue
                if "conda" in dep:
                    conda[dep["conda"]] = _comment(dep, "conda")
                if "pip" in dep:
                    pip[dep["pip"]] = _comment(dep, "pip")
    return RequirementsWithComments(channels, conda, pip)


def _filter_unsupported_platforms(
    requirements: dict[str, str | None],
    platform: Platforms,
) -> dict[str, str | None]:
    return {
        dependency: comment
        for dependency, comment in requirements.items()
        if comment is None
        or not filter_platform_selectors(comment)
        or platform in filter_platform_selectors(comment)
    }


def _filter_pip_and_conda(
    requirements_with_comments: RequirementsWithComments,
    pip_or_conda: Literal["pip", "conda"],
    platform: Platforms | None = None,
) -> RequirementsWithComments:
    r = requirements_with_comments
    conda = _filter_unsupported_platforms(r.conda, platform) if platform else r.conda
    pip = _filter_unsupported_platforms(r.pip, platform) if platform else r.pip
    if pip_or_conda == "pip":
        conda = {k: v for k, v in conda.items() if k not in pip}
    elif pip_or_conda == "conda":
        pip = {k: v for k, v in pip.items() if k not in conda}
    else:  # pragma: no cover
        msg = f"Invalid value for `pip_or_conda`: {pip_or_conda}"
        raise ValueError(msg)
    return RequirementsWithComments(r.channels, conda, pip)


def _parse_requirements_and_filter_duplicates(
    paths: Sequence[Path],
    *,
    verbose: bool = False,
    pip_or_conda: Literal["pip", "conda"] = "conda",
    platform: Platforms | None = None,
) -> RequirementsWithComments:
    """Parse a list of requirements.yaml files including comments."""
    requirements_with_comments = _initial_parse_requirements(paths, verbose=verbose)
    return _filter_pip_and_conda(requirements_with_comments, pip_or_conda, platform)


class EnvSpec(NamedTuple):
    """A conda environment."""

    channels: list[str]
    conda: list[str | dict[str, str]]
    pip: list[str]


def _prepare_for_conda_environment(
    requirements_with_comments: RequirementsWithComments,
) -> EnvSpec:
    r = requirements_with_comments
    conda: list[str | dict[str, str]] = []
    pip: list[str] = []
    for dependency, comment in r.conda.items():
        platforms = filter_platform_selectors(comment) if comment is not None else []
        if platforms:
            unique_platforms = {p.split("-", 1)[0] for p in platforms}
            dependencies = [
                {f"sel({_platform})": dependency} for _platform in unique_platforms
            ]
            conda.extend(dependencies)
        else:
            conda.append(dependency)

    for dependency, comment in r.pip.items():
        platforms = filter_platform_selectors(comment) if comment is not None else []
        if platforms:
            for _platform in platforms:
                selector = pep508_selector([_platform])
                dep = f"{dependency}; {selector}"
                pip.append(dep)
        else:
            pip.append(dependency)
    # Filter out duplicate packages that are both in conda and pip
    pip = [p for p in pip if p not in conda]
    return EnvSpec(list(r.channels), conda, pip)


def _to_requirements(
    combined_deps: RequirementsWithComments,
) -> Requirements:
    conda = CommentedSeq()
    pip = CommentedSeq()
    channels = list(combined_deps.channels)

    for i, (dependency, comment) in enumerate(combined_deps.conda.items()):
        conda.append(dependency)
        if comment is not None:
            conda.yaml_add_eol_comment(comment, i)

    for i, (dependency, comment) in enumerate(combined_deps.pip.items()):
        pip.append(dependency)
        if comment is not None:
            pip.yaml_add_eol_comment(comment, i)

    return Requirements(channels, conda, pip)


def parse_requirements_and_filter_duplicates(
    paths: Sequence[Path],
    *,
    verbose: bool = False,
    pip_or_conda: Literal["pip", "conda"] = "conda",
    platform: Platforms | None = None,
) -> Requirements:
    """Parse a list of requirements.yaml files including comments."""
    combined_deps = _parse_requirements_and_filter_duplicates(
        paths,
        verbose=verbose,
        pip_or_conda=pip_or_conda,
        platform=platform,
    )
    return _to_requirements(combined_deps)


def generate_conda_env_file(
    env_spec: EnvSpec,
    output_file: str | None = "environment.yaml",
    name: str = "myenv",
    *,
    verbose: bool = False,
) -> None:
    """Generate a conda environment.yaml file or print to stdout."""
    _dependencies = deepcopy(env_spec.conda)
    _dependencies.append({"pip": env_spec.pip})  # type: ignore[arg-type, dict-item]
    env_data = CommentedMap(
        {
            "name": name,
            "channels": env_spec.channels,
            "dependencies": _dependencies,
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


def extract_python_requires(
    filename: str = "requirements.yaml",
    *,
    verbose: bool = False,
    platform: Platforms | None = None,
    raises_if_missing: bool = True,
) -> list[str]:
    """Extract Python (pip) requirements from requirements.yaml file."""
    p = Path(filename)
    if not p.exists():
        if raises_if_missing:
            msg = f"File {filename} not found."
            raise FileNotFoundError(msg)
        return []
    deps = parse_requirements_and_filter_duplicates(
        [p],
        pip_or_conda="pip",
        verbose=verbose,
        platform=platform,
    )
    return list(deps.pip)


def detect_platform() -> Platforms:
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
        extract_python_requires(
            str(requirements_file),
            platform=detect_platform(),
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

    requirements_files = scan_requirements(
        args.directory,
        args.depth,
        verbose=verbose,
    )
    combined_deps = _initial_parse_requirements(requirements_files, verbose=verbose)
    env_spec = _prepare_for_conda_environment(combined_deps)
    output_file = None if args.stdout else args.output
    generate_conda_env_file(env_spec, output_file, args.name, verbose=verbose)
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
