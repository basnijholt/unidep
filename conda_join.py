"""requirements.yaml - Unified Conda and Pip requirements management.

This module provides a command-line tool for managing conda environment.yaml files.
"""
from __future__ import annotations

import argparse
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

__version__ = "0.9.0"


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


def _comment(commented_map: CommentedMap, index_or_key: int | str) -> str | None:
    comments = commented_map.ca.items.get(index_or_key, None)
    if comments is None:
        return None
    comment_strings = [c.value.rstrip().lstrip() for c in comments if c is not None]
    return " ".join(comment_strings)


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


def _parse_requirements(
    paths: Sequence[Path],
    *,
    verbose: bool = False,
    pip_or_conda: Literal["pip", "conda"] = "conda",
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
                if pip_or_conda == "conda":
                    if isinstance(dep, str):  # Prefer conda
                        conda[dep] = _comment(dependencies, i)
                    elif "conda" in dep:
                        conda[dep["conda"]] = _comment(dep, "conda")
                    elif "pip" in dep:
                        pip[dep["pip"]] = _comment(dep, "pip")
                elif pip_or_conda == "pip":
                    if isinstance(dep, str):  # Prefer pip
                        pip[dep] = _comment(dependencies, i)
                    elif "pip" in dep:
                        pip[dep["pip"]] = _comment(dep, "pip")
                    elif "conda" in dep:
                        conda[dep["conda"]] = _comment(dep, "conda")
                else:  # pragma: no cover
                    msg = f"Invalid value for `pip_or_conda`: {pip_or_conda}"
                    raise ValueError(msg)
    return RequirementsWithComments(channels, conda, pip)


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


def parse_requirements(
    paths: Sequence[Path],
    *,
    verbose: bool = False,
    pip_or_conda: Literal["pip", "conda"] = "conda",
) -> Requirements:
    """Parse a list of requirements.yaml files including comments."""
    combined_deps = _parse_requirements(
        paths,
        verbose=verbose,
        pip_or_conda=pip_or_conda,
    )
    return _to_requirements(combined_deps)


def generate_conda_env_file(
    dependencies: Requirements,  # actually a CommentedMap with CommentedSeq
    output_file: str | None = "environment.yaml",
    name: str = "myenv",
    *,
    verbose: bool = False,
) -> None:
    """Generate a conda environment.yaml file or print to stdout."""
    _dependencies = deepcopy(dependencies.conda)
    _dependencies.append({"pip": dependencies.pip})  # type: ignore[arg-type]
    env_data = CommentedMap(
        {
            "name": name,
            "channels": dependencies.channels,
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
) -> list[str]:
    """Extract Python (pip) requirements from requirements.yaml file."""
    p = Path(filename)
    if not p.exists():
        return []
    deps = parse_requirements([p], pip_or_conda="pip", verbose=verbose)
    return list(deps.pip)


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
        extract_python_requires(str(requirements_file)),
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
    combined_deps = parse_requirements(requirements_files, verbose=verbose)

    output_file = None if args.stdout else args.output
    generate_conda_env_file(combined_deps, output_file, args.name, verbose=verbose)
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
