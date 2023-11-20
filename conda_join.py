"""requirements.yaml - Unified Conda and Pip requirements management.

This module provides a command-line tool for managing conda environment.yaml files.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import yaml

if TYPE_CHECKING:
    from setuptools import Distribution

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:
        from typing_extensions import Literal

__version__ = "0.7.0"


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


def parse_requirements(
    paths: Sequence[Path],
    *,
    verbose: bool = False,
    pip_or_conda: Literal["pip", "conda"] = "conda",
) -> dict[str, set[str]]:
    """Parse a list of requirements.yaml files."""
    combined_deps: dict[str, set[str]] = {
        "conda": set(),
        "pip": set(),
        "channels": set(),
    }
    for p in paths:
        if verbose:
            print(f"Parsing {p}")
        with p.open() as f:
            reqs = yaml.safe_load(f)
            for channel in reqs.get("channels", []):
                combined_deps["channels"].add(channel)
            for dep in reqs.get("dependencies", []):
                if pip_or_conda == "conda":
                    if isinstance(dep, str):  # Prefer conda
                        combined_deps["conda"].add(dep)
                    elif "conda" in dep:
                        combined_deps["conda"].add(dep["conda"])
                    elif "pip" in dep:
                        combined_deps["pip"].add(dep["pip"])
                elif pip_or_conda == "pip":
                    if isinstance(dep, str):  # Prefer pip
                        combined_deps["pip"].add(dep)
                    elif "pip" in dep:
                        combined_deps["pip"].add(dep["pip"])
                    elif "conda" in dep:
                        combined_deps["conda"].add(dep["conda"])
                else:  # pragma: no cover
                    msg = f"Invalid value for `pip_or_conda`: {pip_or_conda}"
                    raise ValueError(msg)
    return combined_deps


def generate_conda_env_file(
    dependencies: dict[str, set[str]],
    output_file: str | None = "environment.yaml",
    name: str = "myenv",
    *,
    verbose: bool = False,
) -> None:
    """Generate a conda environment.yaml file or print to stdout."""
    env_data = {
        "name": name,
        "channels": ["conda-forge"],
        "dependencies": [
            *list(dependencies["conda"]),
            {"pip": list(dependencies["pip"])},
        ],
    }
    if output_file:
        if verbose:
            print(f"Generating environment file at {output_file}")
        with open(output_file, "w") as f:  # noqa: PTH123
            yaml.dump(env_data, f, sort_keys=False)
        if verbose:
            print("Environment file generated successfully.")
    else:
        yaml.dump(env_data, sys.stdout, sort_keys=False)


def extract_python_requires(
    filename: str = "requirements.yaml",
    *,
    verbose: bool = False,
) -> set[str]:
    """Extract Python (pip) requirements from requirements.yaml file."""
    p = Path(filename)
    if not p.exists():
        return set()
    deps = parse_requirements([p], pip_or_conda="pip", verbose=verbose)
    return deps["pip"]


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
