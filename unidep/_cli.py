#!/usr/bin/env python3
"""requirements.yaml - Unified Conda and Pip requirements management.

This module provides a command-line tool for managing conda environment.yaml files.
"""
from __future__ import annotations

import argparse
import importlib.util
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
from unidep._setuptools_integration import (
    filter_python_dependencies,
    get_python_dependencies,
)
from unidep._version import __version__
from unidep._yaml_parsing import (
    find_requirements_files,
    parse_project_dependencies,
    parse_yaml_requirements,
)
from unidep.platform_definitions import Platform
from unidep.utils import (
    add_comment_to_file,
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

try:  # pragma: no cover
    from rich_argparse import RichHelpFormatter

    class _HelpFormatter(RichHelpFormatter):
        def _get_help_string(self, action: argparse.Action) -> str | None:
            # escapes "[" in text, otherwise e.g., [linux] is removed
            if action.help is not None:
                return action.help.replace("[", r"\[")
            return None
except ImportError:  # pragma: no cover
    from argparse import HelpFormatter as _HelpFormatter  # type: ignore[assignment]


def _add_common_args(  # noqa: PLR0912
    sub_parser: argparse.ArgumentParser,
    options: set[str],
) -> None:  # pragma: no cover
    if "directory" in options:
        sub_parser.add_argument(
            "-d",
            "--directory",
            type=Path,
            default=".",
            help="Base directory to scan for `requirements.yaml` file(s),"
            " by default `.`",
        )
    if "file" in options:
        sub_parser.add_argument(
            "-f",
            "--file",
            type=Path,
            default="requirements.yaml",
            help="The `requirements.yaml` file to parse or folder that contains"
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
            default=None,  # Default is a list with the current platform set in `main`
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
            help="Maximum depth to scan for `requirements.yaml` files, by default 1",
        )
    if "*files" in options:
        sub_parser.add_argument(
            "files",
            type=Path,
            nargs="+",
            help="The `requirements.yaml` file(s) to parse or folder(s) that contain"
            " those file(s), by default `.`",
            default=None,  # default is "." set in `main`
        )
    if "skip-local" in options:
        sub_parser.add_argument(
            "--skip-local",
            action="store_true",
            help="Skip installing local dependencies",
        )
    if "skip-pip" in options:
        sub_parser.add_argument(
            "--skip-pip",
            action="store_true",
            help="Skip installing pip dependencies from `requirements.yaml`",
        )
    if "skip-conda" in options:
        sub_parser.add_argument(
            "--skip-conda",
            action="store_true",
            help="Skip installing conda dependencies from `requirements.yaml`",
        )
    if "skip-dependency" in options:
        sub_parser.add_argument(
            "--skip-dependency",
            type=str,
            action="append",
            default=[],
            help="Skip installing a specific dependency that is in one of the"
            " `requirements.yaml` files. This option can be used multiple times, each"
            " time specifying a different package to skip."
            " For example, use `--skip-dependency pandas` to skip installing pandas.",
        )
    if "no-dependencies" in options:
        sub_parser.add_argument(
            "--no-dependencies",
            action="store_true",
            help="Skip installing dependencies from `requirements.yaml`"
            " file(s) and only install local package(s). This passes the"
            " `--no-dependencies` flag to `pip install`. Useful after"
            " installing a `conda-lock.yml` file because then all"
            " dependencies have already been installed.",
        )
    if "conda-executable" in options:
        sub_parser.add_argument(
            "--conda-executable",
            type=str,
            choices=("conda", "mamba", "micromamba"),
            help="The conda executable to use",
            default=None,
        )
    if "dry-run" in options:
        sub_parser.add_argument(
            "--dry-run",
            "--dry",
            action="store_true",
            help="Only print the commands that would be run",
        )
    if "ignore-pin" in options:
        sub_parser.add_argument(
            "--ignore-pin",
            type=str,
            action="append",
            default=[],
            help="Ignore the version pin for a specific package,"
            " e.g., `--ignore-pin numpy`. This option can be repeated"
            " to ignore multiple packages.",
        )
    if "overwrite-pin" in options:
        sub_parser.add_argument(
            "--overwrite-pin",
            type=str,
            action="append",
            default=[],
            help="Overwrite the version pin for a specific package,"
            " e.g., `--overwrite-pin 'numpy==1.19.2'`. This option can be repeated"
            " to overwrite the pins of multiple packages.",
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified Conda and Pip requirements management.",
        formatter_class=_HelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # Subparser for the 'merge' command
    merge_help = (
        "Combine multiple (or a single) `requirements.yaml` files into a"
        " single Conda installable `environment.yaml` file."
    )
    merge_example = (
        " Example usage: `unidep merge --directory . --depth 1 --output environment.yaml`"  # noqa: E501
        " to search for `requirements.yaml` files in the current directory and its"
        " subdirectories and create `environment.yaml`. These are the defaults, so you"
        " can also just run `unidep merge`."
    )
    parser_merge = subparsers.add_parser(
        "merge",
        help=merge_help,
        description=merge_help + merge_example,
        formatter_class=_HelpFormatter,
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
    _add_common_args(
        parser_merge,
        {
            "directory",
            "verbose",
            "platform",
            "depth",
            "ignore-pin",
            "skip-dependency",
            "overwrite-pin",
        },
    )

    # Subparser for the 'install' command
    install_help = (
        "Automatically install all dependencies from one or more `requirements.yaml`"
        " files. This command first installs dependencies with Conda, then with Pip."
        " Finally, it installs local packages (those containing the `requirements.yaml`"
        " files) using `pip install [-e] ./project`."
    )
    install_example = (
        " Example usage: `unidep install requirements.yaml` for a single file."
        " For multiple files or folders: `unidep install ./project1 ./project2`."
        " The command accepts both file paths and directories containing"
        " a `requirements.yaml` file. Use `--editable` or `-e` to install the"
        " local packages in editable mode. See `unidep install-all` to install"
        " all `requirements.yaml` in the current folder."
    )

    parser_install = subparsers.add_parser(
        "install",
        help=install_help,
        description=install_help + install_example,
        formatter_class=_HelpFormatter,
    )

    # Add positional argument for the file
    _add_common_args(
        parser_install,
        {
            "*files",
            "conda-executable",
            "dry-run",
            "editable",
            "skip-local",
            "skip-pip",
            "skip-conda",
            "no-dependencies",
            "ignore-pin",
            "skip-dependency",
            "overwrite-pin",
            "verbose",
        },
    )
    install_all_help = (
        "Install dependencies from all `requirements.yaml` files found in the current"
        " directory or specified directory. This command first installs dependencies"
        " using Conda, then Pip, and finally the local packages."
    )
    install_all_example = (
        " Example usage: `unidep install-all` to install dependencies from all"
        " `requirements.yaml` files in the current directory. Use"
        " `--directory ./path/to/dir` to specify a different directory. Use"
        " `--depth` to control the depth of directory search. Add `--editable`"
        " or `-e` for installing local packages in editable mode."
    )

    parser_install_all = subparsers.add_parser(
        "install-all",
        help=install_all_help,
        description=install_all_help + install_all_example,
        formatter_class=_HelpFormatter,
    )

    # Add positional argument for the file
    _add_common_args(
        parser_install_all,
        {
            "conda-executable",
            "dry-run",
            "editable",
            "depth",
            "directory",
            "skip-local",
            "skip-pip",
            "skip-conda",
            "no-dependencies",
            "ignore-pin",
            "skip-dependency",
            "overwrite-pin",
            "verbose",
        },
    )

    # Subparser for the 'conda-lock' command

    conda_lock_help = (
        "Generate a global `conda-lock.yml` file for a collection of"
        " `requirements.yaml` files. Additionally, create individual"
        " `conda-lock.yml` files for each `requirements.yaml` file"
        " consistent with the global lock file."
    )
    conda_lock_example = (
        " Example usage: `unidep conda-lock --directory ./projects` to generate"
        " conda-lock files for all `requirements.yaml` files in the `./projects`"
        " directory. Use `--only-global` to generate only the global lock file."
        " The `--check-input-hash` option can be used to avoid regenerating lock"
        " files if the input hasn't changed."
    )

    parser_lock = subparsers.add_parser(
        "conda-lock",
        help=conda_lock_help,
        description=conda_lock_help + conda_lock_example,
        formatter_class=_HelpFormatter,
    )

    parser_lock.add_argument(
        "--only-global",
        action="store_true",
        help="Only generate the global lock file",
    )
    parser_lock.add_argument(
        "--lockfile",
        type=Path,
        default="conda-lock.yml",
        help="Specify a path for the global lockfile (default: `conda-lock.yml`"
        " in current directory). Path should be relative, e.g.,"
        " `--lockfile ./locks/example.conda-lock.yml`.",
    )
    parser_lock.add_argument(
        "--check-input-hash",
        action="store_true",
        help="Check existing input hashes in lockfiles before regenerating lock files."
        " This flag is directly passed to `conda-lock`.",
    )
    _add_common_args(
        parser_lock,
        {
            "directory",
            "verbose",
            "platform",
            "depth",
            "ignore-pin",
            "skip-dependency",
            "overwrite-pin",
        },
    )

    # Subparser for the 'pip-compile' command
    pip_compile_help = (
        "Generate a fully pinned `requirements.txt` file from one or more"
        " `requirements.yaml` files using `pip-compile` from `pip-tools`. This"
        " command consolidates all pip dependencies defined in the `requirements.yaml`"
        " files and compiles them into a single `requirements.txt` file, taking"
        " into account the specific versions and dependencies of each package."
    )
    pip_compile_example = (
        " Example usage: `unidep pip-compile --directory ./projects` to generate"
        " a `requirements.txt` file for all `requirements.yaml` files in the"
        " `./projects` directory. Use `--output-file requirements.txt` to specify a"
        " different output file."
    )

    parser_pip_compile = subparsers.add_parser(
        "pip-compile",
        help=pip_compile_help,
        description=pip_compile_help + pip_compile_example,
        formatter_class=_HelpFormatter,
    )
    parser_pip_compile.add_argument(
        "-o",
        "--output-file",
        type=Path,
        default=None,
        help="Output file for the pip requirements, by default `requirements.txt`",
    )
    _add_common_args(
        parser_pip_compile,
        {
            "directory",
            "verbose",
            "platform",
            "depth",
            "ignore-pin",
            "skip-dependency",
            "overwrite-pin",
        },
    )
    parser_pip_compile.add_argument(
        "extra_flags",
        nargs=argparse.REMAINDER,
        help="Extra flags to pass to `pip-compile`. These flags are passed directly"
        " and should be provided in the format expected by `pip-compile`. For example,"
        " `unidep pip-compile -- --generate-hashes --allow-unsafe`. Note that the"
        " `--` is required to separate the flags for `unidep` from the flags for"
        " `pip-compile`.",
    )

    # Subparser for the 'pip' and 'conda' command
    help_str = "Get the {} requirements for the current platform only."
    help_example = (
        " Example usage: `unidep {which} --file folder1 --file"
        " folder2/requirements.yaml --seperator ' ' --platform linux-64` to"
        " extract all the {which} dependencies specific to the linux-64 platform. Note"
        " that the `--file` argument can be used multiple times to specify multiple"
        " `requirements.yaml` files and that --file can also be a folder that contains"
        " a `requirements.yaml` file."
    )
    parser_pip = subparsers.add_parser(
        "pip",
        help=help_str.format("pip"),
        description=help_str.format("pip") + help_example.format(which="pip"),
        formatter_class=_HelpFormatter,
    )
    parser_conda = subparsers.add_parser(
        "conda",
        help=help_str.format("conda"),
        description=help_str.format("conda") + help_example.format(which="conda"),
        formatter_class=_HelpFormatter,
    )
    for sub_parser in [parser_pip, parser_conda]:
        _add_common_args(
            sub_parser,
            {
                "verbose",
                "platform",
                "file",
                "ignore-pin",
                "skip-dependency",
                "overwrite-pin",
            },
        )
        sub_parser.add_argument(
            "--separator",
            type=str,
            default=" ",
            help="The separator between the dependencies, by default ` `",
        )

    # Subparser for the 'version' command
    parser_merge = subparsers.add_parser(
        "version",
        help="Print version information of unidep.",
        formatter_class=_HelpFormatter,
    )

    args = parser.parse_args()

    if args.command is None:  # pragma: no cover
        parser.print_help()
        sys.exit(1)

    if "file" in args and args.file.is_dir():  # pragma: no cover
        args.file = _to_requirements_file(args.file)
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
    *folders: str | Path,
    editable: bool,
    dry_run: bool,
    flags: list[str] | None = None,
) -> None:  # pragma: no cover
    pip_command = [sys.executable, "-m", "pip", "install"]
    if flags:
        pip_command.extend(flags)

    for folder in folders:
        if not os.path.isabs(folder):  # noqa: PTH117
            relative_prefix = ".\\" if os.name == "nt" else "./"
            folder = f"{relative_prefix}{folder}"  # noqa: PLW2901

        if editable:
            pip_command.extend(["-e", str(folder)])
        else:
            pip_command.append(str(folder))

    print(f"📦 Installing project with `{' '.join(pip_command)}`\n")
    if not dry_run:
        subprocess.run(pip_command, check=True)  # noqa: S603


def _to_requirements_file(path: Path) -> Path:
    return path if path.is_file() else path / "requirements.yaml"


def _install_command(
    *files: Path,
    conda_executable: str,
    dry_run: bool,
    editable: bool,
    skip_local: bool = False,
    skip_pip: bool = False,
    skip_conda: bool = False,
    no_dependencies: bool = False,
    ignore_pins: list[str] | None = None,
    overwrite_pins: list[str] | None = None,
    skip_dependencies: list[str] | None = None,
    verbose: bool = False,
) -> None:
    """Install the dependencies of a single `requirements.yaml` file."""
    if no_dependencies:
        skip_pip = True
        skip_conda = True
    files = tuple(_to_requirements_file(f) for f in files)
    requirements = parse_yaml_requirements(
        *files,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        verbose=verbose,
    )
    platforms = [identify_current_platform()]
    resolved = resolve_conflicts(
        requirements.requirements,
        platforms,
    )
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        platforms=platforms,
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
    pip_flags = ["--no-dependencies"] if no_dependencies else None
    installable = []
    if not skip_local:
        for file in files:
            if is_pip_installable(file.parent):
                installable.append(file.parent)
            else:  # pragma: no cover
                print(
                    f"⚠️  Project {file.parent} is not pip installable. "
                    "Could not find setup.py or [build-system] in pyproject.toml.",
                )

        # Install local dependencies (if any) included via `includes:`
        local_dependencies = parse_project_dependencies(
            *files,
            check_pip_installable=True,
            verbose=verbose,
        )
        names = {k.name: [dep.name for dep in v] for k, v in local_dependencies.items()}
        print(f"📝 Found local dependencies: {names}\n")
        installable_set = {p.resolve() for p in installable}
        installable += [
            dep
            for deps in local_dependencies.values()
            for dep in deps
            if dep.resolve() not in installable_set
        ]
        if installable:
            _pip_install_local(
                *sorted(installable),
                editable=editable,
                dry_run=dry_run,
                flags=pip_flags,
            )

    if not dry_run:  # pragma: no cover
        print("✅ All dependencies installed successfully.")


def _install_all_command(
    *,
    conda_executable: str,
    dry_run: bool,
    editable: bool,
    depth: int,
    directory: Path,
    skip_local: bool = False,
    skip_pip: bool = False,
    skip_conda: bool = False,
    no_dependencies: bool = False,
    ignore_pins: list[str] | None = None,
    overwrite_pins: list[str] | None = None,
    skip_dependencies: list[str] | None = None,
    verbose: bool = False,
) -> None:  # pragma: no cover
    found_files = find_requirements_files(
        directory,
        depth,
        verbose=verbose,
    )
    if not found_files:
        print(f"❌ No `requirements.yaml` files found in {directory}")
        sys.exit(1)
    _install_command(
        *found_files,
        conda_executable=conda_executable,
        dry_run=dry_run,
        editable=editable,
        skip_local=skip_local,
        skip_pip=skip_pip,
        skip_conda=skip_conda,
        no_dependencies=no_dependencies,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        verbose=verbose,
    )


def _merge_command(
    *,
    depth: int,
    directory: Path,
    name: str,
    output: Path,
    stdout: bool,
    selector: Literal["sel", "comment"],
    platforms: list[Platform],
    ignore_pins: list[str],
    skip_dependencies: list[str],
    overwrite_pins: list[str],
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
        print(f"❌ No `requirements.yaml` files found in {directory}")
        sys.exit(1)
    requirements = parse_yaml_requirements(
        *found_files,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        verbose=verbose,
    )

    platforms = requirements.platforms or platforms
    resolved = resolve_conflicts(
        requirements.requirements,
        platforms,
    )
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        platforms,
        selector=selector,
    )
    output_file = None if stdout else output
    write_conda_environment_file(env_spec, output_file, name, verbose=verbose)
    if output_file:
        found_files_str = ", ".join(f"`{f}`" for f in found_files)
        print(
            f"✅ Generated environment file at `{output_file}` from {found_files_str}",
        )


def _pip_compile_command(
    *,
    depth: int,
    directory: Path,
    platform: Platform,
    ignore_pins: list[str],
    skip_dependencies: list[str],
    overwrite_pins: list[str],
    verbose: bool,
    extra_flags: list[str],
    output_file: Path | None = None,
) -> None:
    if importlib.util.find_spec("piptools") is None:  # pragma: no cover
        print(
            "❌ Could not import `pip-tools` module."
            " Please install it with `pip install pip-tools`.",
        )
        sys.exit(1)

    found_files = find_requirements_files(
        directory,
        depth,
        verbose=verbose,
    )

    requirements = parse_yaml_requirements(
        *found_files,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        verbose=verbose,
    )
    resolved = resolve_conflicts(
        requirements.requirements,
        [platform],
    )
    python_deps = filter_python_dependencies(resolved)
    requirements_in = directory / "requirements.in"
    with requirements_in.open("w") as f:
        f.write("\n".join(python_deps))
    print("✅ Generated `requirements.in` file.")
    if extra_flags:
        assert extra_flags[0] == "--"
        extra_flags = extra_flags[1:]
        if verbose:
            print(f"📝 Extra flags: {extra_flags}")

    if output_file is None:
        output_file = directory / "requirements.txt"

    subprocess.run(
        [  # noqa: S603, S607
            "pip-compile",
            "--output-file",
            str(output_file),
            *extra_flags,
            str(requirements_in),
        ],
        check=True,
    )
    if output_file.exists():  # might not exist in tests
        add_comment_to_file(output_file)
    print(f"✅ Generated `{output_file}`.")


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

    if "files" in args and args.files is None:  # pragma: no cover
        args.platform = ["."]

    if args.command == "merge":  # pragma: no cover
        _merge_command(
            depth=args.depth,
            directory=args.directory,
            name=args.name,
            output=args.output,
            stdout=args.stdout,
            selector=args.selector,
            platforms=args.platform,
            ignore_pins=args.ignore_pin,
            skip_dependencies=args.skip_dependency,
            overwrite_pins=args.overwrite_pin,
            verbose=args.verbose,
        )
    elif args.command == "pip":  # pragma: no cover
        pip_dependencies = list(
            get_python_dependencies(
                args.file,
                platforms=[args.platform],
                verbose=args.verbose,
                ignore_pins=args.ignore_pin,
                skip_dependencies=args.skip_dependency,
                overwrite_pins=args.overwrite_pin,
            ),
        )
        print(escape_unicode(args.separator).join(pip_dependencies))
    elif args.command == "conda":  # pragma: no cover
        requirements = parse_yaml_requirements(
            args.file,
            ignore_pins=args.ignore_pin,
            skip_dependencies=args.skip_dependency,
            overwrite_pins=args.overwrite_pin,
            verbose=args.verbose,
        )
        platforms = [args.platform]
        resolved = resolve_conflicts(
            requirements.requirements,
            platforms,
        )
        env_spec = create_conda_env_specification(
            resolved,
            requirements.channels,
            platforms=platforms,
        )
        print(escape_unicode(args.separator).join(env_spec.conda))  # type: ignore[arg-type]
    elif args.command == "install":
        _check_conda_prefix()
        _install_command(
            *args.files,
            conda_executable=args.conda_executable,
            dry_run=args.dry_run,
            editable=args.editable,
            skip_local=args.skip_local,
            skip_pip=args.skip_pip,
            skip_conda=args.skip_conda,
            no_dependencies=args.no_dependencies,
            ignore_pins=args.ignore_pin,
            skip_dependencies=args.skip_dependency,
            overwrite_pins=args.overwrite_pin,
            verbose=args.verbose,
        )
    elif args.command == "install-all":
        _check_conda_prefix()
        _install_all_command(
            conda_executable=args.conda_executable,
            dry_run=args.dry_run,
            editable=args.editable,
            depth=args.depth,
            directory=args.directory,
            skip_local=args.skip_local,
            skip_pip=args.skip_pip,
            skip_conda=args.skip_conda,
            no_dependencies=args.no_dependencies,
            ignore_pins=args.ignore_pin,
            skip_dependencies=args.skip_dependency,
            overwrite_pins=args.overwrite_pin,
            verbose=args.verbose,
        )
    elif args.command == "conda-lock":  # pragma: no cover
        conda_lock_command(
            depth=args.depth,
            directory=args.directory,
            platform=args.platform,
            verbose=args.verbose,
            only_global=args.only_global,
            ignore_pins=args.ignore_pin,
            skip_dependencies=args.skip_dependency,
            overwrite_pins=args.overwrite_pin,
            check_input_hash=args.check_input_hash,
            lockfile=args.lockfile,
        )
    elif args.command == "pip-compile":  # pragma: no cover
        _pip_compile_command(
            depth=args.depth,
            directory=args.directory,
            platform=args.platform,
            verbose=args.verbose,
            ignore_pins=args.ignore_pin,
            skip_dependencies=args.skip_dependency,
            overwrite_pins=args.overwrite_pin,
            extra_flags=args.extra_flags,
            output_file=args.output_file,
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
