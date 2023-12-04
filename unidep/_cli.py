#!/usr/bin/env python3
"""requirements.yaml - Unified Conda and Pip requirements management.

This module provides a command-line tool for managing conda environment.yaml files.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from unidep._conda_env import (
    create_conda_env_specification,
    write_conda_environment_file,
)
from unidep._conda_lock import conda_lock_command
from unidep._conflicts import resolve_conflicts
from unidep._setuptools_integration import get_python_dependencies
from unidep._version import __version__
from unidep._yaml_parsing import (
    find_requirements_files,
    parse_project_dependencies,
    parse_yaml_requirements,
)
from unidep.platform_definitions import Platform
from unidep.utils import (
    escape_unicode,
    extract_name_and_pin,
    identify_current_platform,
    is_pip_installable,
    warn,
)

if sys.version_info >= (3, 8):
    from typing import Literal, get_args
else:  # pragma: no cover
    from typing_extensions import Literal, get_args


def _add_common_args(
    sub_parser: argparse.ArgumentParser,
    options: set[str],
) -> None:  # pragma: no cover
    if "directory" in options:
        sub_parser.add_argument(
            "-d",
            "--directory",
            type=Path,
            default=".",
            help="Base directory to scan for requirements.yaml file(s), by default `.`",
        )
    if "file" in options:
        sub_parser.add_argument(
            "-f",
            "--file",
            type=Path,
            default="requirements.yaml",
            help="The requirements.yaml file to parse or folder that contains"
            " that file, by default `requirements.yaml`",
        )
    if "verbose" in options:
        sub_parser.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            help="Print verbose output",
        )
    if "platform" in options:
        current_platform = identify_current_platform()
        sub_parser.add_argument(
            "--platform",
            "-p",
            type=str,
            action="append",  # Allow multiple instances of -p
            default=None,  # Default is a list with the current platform
            choices=get_args(Platform),
            help="The platform(s) to get the requirements for. "
            "Multiple platforms can be specified. "
            f"By default, the current platform (`{current_platform}`) is used.",
        )
    if "editable" in options:
        sub_parser.add_argument(
            "-e",
            "--editable",
            action="store_true",
            help="Install the project in editable mode",
        )
    if "depth" in options:
        sub_parser.add_argument(
            "--depth",
            type=int,
            default=1,
            help="Maximum depth to scan for requirements.yaml files, by default 1",
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified Conda and Pip requirements management.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # Subparser for the 'merge' command
    parser_merge = subparsers.add_parser(
        "merge",
        help="Merge requirements to conda installable environment.yaml",
    )
    parser_merge.add_argument(
        "-o",
        "--output",
        type=Path,
        default="environment.yaml",
        help="Output file for the conda environment, by default `environment.yaml`",
    )
    parser_merge.add_argument(
        "-n",
        "--name",
        type=str,
        default="myenv",
        help="Name of the conda environment, by default `myenv`",
    )
    parser_merge.add_argument(
        "--stdout",
        action="store_true",
        help="Output to stdout instead of a file",
    )
    parser_merge.add_argument(
        "--selector",
        type=str,
        choices=("sel", "comment"),
        default="sel",
        help="The selector to use for the environment markers, if `sel` then"
        " `- numpy # [linux]` becomes `sel(linux): numpy`, if `comment` then"
        " it remains `- numpy # [linux]`, by default `sel`",
    )
    _add_common_args(parser_merge, {"directory", "verbose", "platform", "depth"})

    # Subparser for the 'pip' and 'conda' command
    help_str = "Get the {} requirements for the current platform only."
    parser_pip = subparsers.add_parser("pip", help=help_str.format("pip"))
    parser_conda = subparsers.add_parser("conda", help=help_str.format("conda"))
    for sub_parser in [parser_pip, parser_conda]:
        _add_common_args(sub_parser, {"verbose", "platform", "file"})
        sub_parser.add_argument(
            "--separator",
            type=str,
            default=" ",
            help="The separator between the dependencies, by default ` `",
        )

    # Subparser for the 'install' command
    parser_install = subparsers.add_parser(
        "install",
        help="Install the dependencies of a single `requirements.yaml` file in the"
        " currently activated conda environment with conda, then install the remaining"
        " dependencies with pip, and finally install the current package"
        " with `pip install [-e] .`.",
    )
    # Add positional argument for the file
    parser_install.add_argument(
        "file",
        type=Path,
        help="The requirements.yaml file to parse or folder that contains that"
        " file, by default `.`",
        default=".",
    )
    parser_install.add_argument(
        "--skip-local",
        action="store_true",
        help="Skip installing local dependencies",
    )
    parser_install.add_argument(
        "--skip-pip",
        action="store_true",
        help="Skip installing pip dependencies from `requirements.yaml`",
    )
    parser_install.add_argument(
        "--skip-conda",
        action="store_true",
        help="Skip installing conda dependencies from `requirements.yaml`",
    )
    _add_common_args(parser_install, {"verbose", "editable"})
    parser_install.add_argument(
        "--conda-executable",
        type=str,
        choices=("conda", "mamba", "micromamba"),
        help="The conda executable to use",
        default=None,
    )
    parser_install.add_argument(
        "--dry-run",
        "--dry",
        action="store_true",
        help="Only print the commands that would be run",
    )

    # Subparser for the 'conda-lock' command
    parser_lock = subparsers.add_parser(
        "conda-lock",
        help="Generate a global conda-lock file of a collection of `requirements.yaml`"
        " files. Additionally, generate a conda-lock file for each separate"
        " `requirements.yaml` file based on the global lock file.",
    )
    parser_lock.add_argument(
        "--only-global",
        action="store_true",
        help="Only generate the global lock file",
    )
    parser_lock.add_argument(
        "--check-input-hash",
        action="store_true",
        help="Check existing input hashes in lockfiles before regenerating lock files."
        " This flag is directly passed to `conda-lock`.",
    )
    _add_common_args(parser_lock, {"directory", "verbose", "platform", "depth"})

    # Subparser for the 'version' command
    parser_merge = subparsers.add_parser(
        "version",
        help="Print version information of unidep.",
    )

    args = parser.parse_args()

    if args.command is None:  # pragma: no cover
        parser.print_help()
        sys.exit(1)

    if "file" in args and args.file.is_dir():
        args.file = args.file / "requirements.yaml"
    return args


def _identify_conda_executable() -> str:  # pragma: no cover
    """Identify the conda executable to use.

    This function checks for micromamba, mamba, and conda in that order.
    """
    if shutil.which("micromamba"):
        return "micromamba"
    if shutil.which("mamba"):
        return "mamba"
    if shutil.which("conda"):
        return "conda"
    msg = "Could not identify conda executable."
    raise RuntimeError(msg)


def _format_inline_conda_package(package: str) -> str:
    name, pin = extract_name_and_pin(package)
    if pin is None:
        return name
    return f'{name}"{pin.strip()}"'


def _pip_install_local(
    folder: str | Path,
    *,
    editable: bool,
    dry_run: bool,
) -> None:  # pragma: no cover
    if not os.path.isabs(folder):  # noqa: PTH117
        relative_prefix = ".\\" if os.name == "nt" else "./"
        folder = f"{relative_prefix}{folder}"
    pip_command = [sys.executable, "-m", "pip", "install", str(folder)]
    if editable:
        pip_command.insert(-1, "-e")
    print(f"📦 Installing project with `{' '.join(pip_command)}`\n")
    if not dry_run:
        subprocess.run(pip_command, check=True)  # noqa: S603


def _install_command(
    *,
    conda_executable: str,
    dry_run: bool,
    editable: bool,
    file: Path,
    skip_local: bool = False,
    skip_pip: bool = False,
    skip_conda: bool = False,
    verbose: bool = False,
) -> None:
    """Install the dependencies of a single `requirements.yaml` file."""
    requirements = parse_yaml_requirements(file, verbose=verbose)
    resolved_requirements = resolve_conflicts(requirements.requirements)
    env_spec = create_conda_env_specification(
        resolved_requirements,
        requirements.channels,
        platforms=[identify_current_platform()],
    )
    if env_spec.conda and not skip_conda:
        conda_executable = conda_executable or _identify_conda_executable()
        channel_args = ["--override-channels"] if env_spec.channels else []
        for channel in env_spec.channels:
            channel_args.extend(["--channel", channel])

        conda_command = [
            conda_executable,
            "install",
            "--yes",
            *channel_args,
        ]
        # When running the command in terminal, we need to wrap the pin in quotes
        # so what we print is what the user would type (copy-paste).
        to_print = [_format_inline_conda_package(pkg) for pkg in env_spec.conda]  # type: ignore[arg-type]
        conda_command_str = " ".join((*conda_command, *to_print))
        print(f"📦 Installing conda dependencies with `{conda_command_str}`\n")  # type: ignore[arg-type]
        if not dry_run:  # pragma: no cover
            subprocess.run((*conda_command, *env_spec.conda), check=True)  # type: ignore[arg-type]  # noqa: S603
    if env_spec.pip and not skip_pip:
        pip_command = [sys.executable, "-m", "pip", "install", *env_spec.pip]
        print(f"📦 Installing pip dependencies with `{' '.join(pip_command)}`\n")
        if not dry_run:  # pragma: no cover
            subprocess.run(pip_command, check=True)  # noqa: S603

    if not skip_local:
        if is_pip_installable(file.parent):  # pragma: no cover
            folder = file.parent
            _pip_install_local(folder, editable=editable, dry_run=dry_run)
        else:  # pragma: no cover
            print(
                f"⚠️  Project {file.parent} is not pip installable. "
                "Could not find setup.py or [build-system] in pyproject.toml.",
            )

    if not skip_local:
        # Install local dependencies (if any) included via `includes:`
        local_dependencies = parse_project_dependencies(
            file,
            check_pip_installable=True,
            verbose=verbose,
        )
        assert len(local_dependencies) <= 1
        names = {k.name: [dep.name for dep in v] for k, v in local_dependencies.items()}
        print(f"📝 Found local dependencies: {names}\n")
        for deps in sorted(local_dependencies.values()):
            for dep in sorted(deps):
                _pip_install_local(dep, editable=editable, dry_run=dry_run)

    if not dry_run:  # pragma: no cover
        print("✅ All dependencies installed successfully.")


def _merge_command(
    *,
    depth: int,
    directory: Path,
    name: str,
    output: Path,
    stdout: bool,
    selector: Literal["sel", "comment"],
    platforms: list[Platform],
    verbose: bool,
) -> None:  # pragma: no cover
    # When using stdout, suppress verbose output
    verbose = verbose and not stdout

    found_files = find_requirements_files(
        directory,
        depth,
        verbose=verbose,
    )
    if not found_files:
        print(f"❌ No requirements.yaml files found in {directory}")
        sys.exit(1)
    requirements = parse_yaml_requirements(*found_files, verbose=verbose)
    resolved_requirements = resolve_conflicts(requirements.requirements)
    env_spec = create_conda_env_specification(
        resolved_requirements,
        requirements.channels,
        requirements.platforms or platforms,
        selector=selector,
    )
    output_file = None if stdout else output
    write_conda_environment_file(env_spec, output_file, name, verbose=verbose)
    if output_file:
        found_files_str = ", ".join(f"`{f}`" for f in found_files)
        print(
            f"✅ Generated environment file at `{output_file}` from {found_files_str}",
        )


def _check_conda_prefix() -> None:  # pragma: no cover
    """Check if sys.executable is in the $CONDA_PREFIX."""
    if "CONDA_PREFIX" not in os.environ:
        return
    conda_prefix = os.environ["CONDA_PREFIX"]
    if sys.executable.startswith(str(conda_prefix)):
        return
    msg = (
        "UniDep should be run from the current Conda environment for correct"
        " operation. However, it's currently running with the Python interpreter"
        f" at `{sys.executable}`, which is not in the active Conda environment"
        f" (`{conda_prefix}`). Please install and run UniDep in the current"
        " Conda environment to avoid any issues."
    )
    warn(msg, stacklevel=2)
    sys.exit(1)


def main() -> None:
    """Main entry point for the command-line tool."""
    args = _parse_args()
    if "file" in args and not args.file.exists():  # pragma: no cover
        print(f"❌ File {args.file} not found.")
        sys.exit(1)

    if "platform" in args and args.platform is None:  # pragma: no cover
        args.platform = [identify_current_platform()]

    if args.command == "merge":  # pragma: no cover
        _merge_command(
            depth=args.depth,
            directory=args.directory,
            name=args.name,
            output=args.output,
            stdout=args.stdout,
            selector=args.selector,
            platforms=args.platform,
            verbose=args.verbose,
        )
    elif args.command == "pip":  # pragma: no cover
        pip_dependencies = list(
            get_python_dependencies(
                args.file,
                platforms=[args.platform],
                verbose=args.verbose,
            ),
        )
        print(escape_unicode(args.separator).join(pip_dependencies))
    elif args.command == "conda":  # pragma: no cover
        requirements = parse_yaml_requirements(args.file, verbose=args.verbose)
        resolved_requirements = resolve_conflicts(requirements.requirements)
        env_spec = create_conda_env_specification(
            resolved_requirements,
            requirements.channels,
            platforms=[args.platform],
        )
        print(escape_unicode(args.separator).join(env_spec.conda))  # type: ignore[arg-type]
    elif args.command == "install":
        _check_conda_prefix()
        _install_command(
            conda_executable=args.conda_executable,
            dry_run=args.dry_run,
            editable=args.editable,
            file=args.file,
            skip_local=args.skip_local,
            skip_pip=args.skip_pip,
            skip_conda=args.skip_conda,
            verbose=args.verbose,
        )
    elif args.command == "conda-lock":  # pragma: no cover
        conda_lock_command(
            depth=args.depth,
            directory=args.directory,
            platform=args.platform,
            verbose=args.verbose,
            only_global=args.only_global,
            check_input_hash=args.check_input_hash,
        )
    elif args.command == "version":  # pragma: no cover
        path = Path(__file__).parent
        txt = (
            f"unidep version: {__version__}",
            f"unidep location: {path}",
            f"Python version: {sys.version}",
            f"Python executable: {sys.executable}",
        )
        print("\n".join(txt))
