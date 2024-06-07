#!/usr/bin/env python3
"""unidep - Unified Conda and Pip requirements management.

This module provides a command-line tool for managing conda environment.yaml files.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import itertools
import json
import os
import platform
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
from unidep._dependencies_parsing import (
    find_requirements_files,
    parse_local_dependencies,
    parse_requirements,
)
from unidep._setuptools_integration import (
    filter_python_dependencies,
    get_python_dependencies,
)
from unidep._version import __version__
from unidep.platform_definitions import Platform
from unidep.utils import (
    add_comment_to_file,
    escape_unicode,
    get_package_version,
    identify_current_platform,
    is_pip_installable,
    parse_folder_or_filename,
    parse_package_str,
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

_DEP_FILES = "`requirements.yaml` or `pyproject.toml`"
CondaExecutable = Literal["conda", "mamba", "micromamba"]


def _add_common_args(  # noqa: PLR0912, C901
    sub_parser: argparse.ArgumentParser,
    options: set[str],
) -> None:  # pragma: no cover
    if "directory" in options:
        sub_parser.add_argument(
            "-d",
            "--directory",
            type=Path,
            default=".",
            help=f"Base directory to scan for {_DEP_FILES} file(s), by default `.`",
        )
    if "depth" in options:
        sub_parser.add_argument(
            "--depth",
            type=int,
            default=1,
            help=f"Maximum depth to scan for {_DEP_FILES} files, by default 1",
        )
    if "file" in options or "file-alt" in options:
        if "file-alt" in options:
            help_msg = (
                f"A single {_DEP_FILES} file to use, or"
                " folder that contains that file. This is an alternative to using"
                f" `--directory` which searches for all {_DEP_FILES} files in the"
                " directory and its subdirectories."
            )
        else:
            help_msg = (
                f"The {_DEP_FILES} file to parse, or folder"
                " that contains that file, by default `.`"
            )
        sub_parser.add_argument(
            "-f",
            "--file",
            type=Path,
            default=[],
            action="append",
            help=help_msg,
        )
    if "*files" in options:
        sub_parser.add_argument(
            "files",
            type=Path,
            nargs="+",
            help=f"The {_DEP_FILES} file(s) to parse"
            " or folder(s) that contain those file(s), by default `.`",
            default=None,
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
            default=[],
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
            help=f"Skip installing pip dependencies from {_DEP_FILES}",
        )
    if "skip-conda" in options:
        sub_parser.add_argument(
            "--skip-conda",
            action="store_true",
            help=f"Skip installing conda dependencies from {_DEP_FILES}",
        )
    if "skip-dependency" in options:
        sub_parser.add_argument(
            "--skip-dependency",
            type=str,
            action="append",
            default=[],
            help="Skip installing a specific dependency that is in one of the"
            f" {_DEP_FILES}"
            " files. This option can be used multiple times, each"
            " time specifying a different package to skip."
            " For example, use `--skip-dependency pandas` to skip installing pandas.",
        )
    if "no-dependencies" in options:
        sub_parser.add_argument(
            "--no-dependencies",
            action="store_true",
            help=f"Skip installing dependencies from {_DEP_FILES}"
            " file(s) and only install local package(s). Useful after"
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
    if "conda-env" in options:
        grp = sub_parser.add_mutually_exclusive_group()
        grp.add_argument(
            "--conda-env-name",
            type=str,
            default=None,
            help="Name of the conda environment, if not provided, the currently"
            " active environment name is used, unless `--conda-env-prefix` is"
            " provided",
        )
        grp.add_argument(
            "--conda-env-prefix",
            type=Path,
            default=None,
            help="Path to the conda environment, if not provided, the currently"
            " active environment path is used, unless `--conda-env-name` is"
            " provided",
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
            " e.g., `--overwrite-pin 'numpy=1.19.2'`. This option can be repeated"
            " to overwrite the pins of multiple packages.",
        )


def _add_extra_flags(
    subparser: argparse.ArgumentParser,
    downstream_command: str,
    unidep_subcommand: str,
    example: str,
) -> None:
    subparser.add_argument(
        "extra_flags",
        nargs=argparse.REMAINDER,
        help=f"Extra flags to pass to `{downstream_command}`. These flags are passed"
        f" directly and should be provided in the format expected by"
        f" `{downstream_command}`. For example, `unidep {unidep_subcommand} -- {example}`."  # noqa: E501
        f" Note that the `--` is required to separate the flags for"
        f" `unidep {unidep_subcommand}` from the flags for `{downstream_command}`.",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified Conda and Pip requirements management.",
        formatter_class=_HelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # Subparser for the 'merge' command
    merge_help = (
        f"Combine multiple (or a single) {_DEP_FILES}"
        " files into a"
        " single Conda installable `environment.yaml` file."
    )
    merge_example = (
        " Example usage: `unidep merge --directory . --depth 1 --output environment.yaml`"  # noqa: E501
        f" to search for {_DEP_FILES}"
        " files in the current directory and its"
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
        f"Automatically install all dependencies from one or more {_DEP_FILES} files."
        " This command first installs dependencies"
        " with Conda, then with Pip. Finally, it installs local packages"
        f" (those containing the {_DEP_FILES} files)"
        " using `pip install [-e] ./project`."
    )
    install_example = (
        " Example usage: `unidep install .` for a single project."
        " For multiple projects: `unidep install ./project1 ./project2`."
        " The command accepts both file paths and directories containing"
        f" a {_DEP_FILES} file. Use `--editable` or"
        " `-e` to install the local packages in editable mode. See"
        f" `unidep install-all` to install all {_DEP_FILES} files in and below the"
        " current folder."
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
            "conda-env",
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
        f"Install dependencies from all {_DEP_FILES}"
        " files found in the current"
        " directory or specified directory. This command first installs dependencies"
        " using Conda, then Pip, and finally the local packages."
    )
    install_all_example = (
        " Example usage: `unidep install-all` to install dependencies from all"
        f" {_DEP_FILES}"
        " files in the current directory. Use"
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
            "conda-env",
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
        f" {_DEP_FILES}"
        " files. Additionally, create individual"
        f" `conda-lock.yml` files for each {_DEP_FILES} file"
        " consistent with the global lock file."
    )
    conda_lock_example = (
        " Example usage: `unidep conda-lock --directory ./projects` to generate"
        f" conda-lock files for all {_DEP_FILES}"
        " files in the `./projects`"
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
            "file-alt",
            "verbose",
            "platform",
            "depth",
            "ignore-pin",
            "skip-dependency",
            "overwrite-pin",
        },
    )
    _add_extra_flags(parser_lock, "conda-lock lock", "conda-lock", "--micromamba")

    # Subparser for the 'pip-compile' command
    pip_compile_help = (
        "Generate a fully pinned `requirements.txt` file from one or more"
        f" {_DEP_FILES}"
        " files using `pip-compile` from `pip-tools`. This"
        f" command consolidates all pip dependencies defined in the {_DEP_FILES}"
        " files and compiles them into a single `requirements.txt` file, taking"
        " into account the specific versions and dependencies of each package."
    )
    pip_compile_example = (
        " Example usage: `unidep pip-compile --directory ./projects` to generate"
        f" a `requirements.txt` file for all {_DEP_FILES}"
        " files in the"
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
    _add_extra_flags(
        parser_pip_compile,
        "pip-compile",
        "pip-compile",
        "--generate-hashes --allow-unsafe",
    )

    # Subparser for the 'pip' and 'conda' command
    help_str = "Get the {} requirements for the current platform only."
    help_example = (
        " Example usage: `unidep {which} --file folder1 --file"
        " folder2/requirements.yaml --separator ' ' --platform linux-64` to"
        " extract all the {which} dependencies specific to the linux-64 platform. Note"
        " that the `--file` argument can be used multiple times to specify multiple"
        f" {_DEP_FILES}"
        " files and that --file can also be a folder that contains"
        f" a {_DEP_FILES} file."
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

    if "file" in args:
        _ensure_files(args.file)

    return args


def _ensure_files(files: list[Path]) -> None:
    """Ensure that the files exist."""
    missing = []
    for i, f in enumerate(files):
        try:
            path_with_extras = parse_folder_or_filename(f)
        except FileNotFoundError:  # noqa: PERF203
            missing.append(f"`{f}`")
        else:
            files[i] = path_with_extras.path_with_extras
    if missing:
        print(f"❌ One or more files ({', '.join(missing)}) not found.")
        sys.exit(1)


def _identify_conda_executable() -> CondaExecutable:  # pragma: no cover
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
    pkg = parse_package_str(package)
    if pkg.pin is None:
        return pkg.name
    return f'{pkg.name}"{pkg.pin.strip()}"'


def _maybe_exe(conda_executable: CondaExecutable) -> str:
    """Add .exe on Windows."""
    if os.name == "nt":  # pragma: no cover
        if conda_executable in ("micromamba", "mamba") and os.environ.get("MAMBA_EXE"):
            return os.environ["MAMBA_EXE"]
        if os.environ.get("CONDA_EXE"):
            return os.environ["CONDA_EXE"]

        executables = [f"{conda_executable}.exe", conda_executable]
        for exe in executables:
            path = shutil.which(exe)
            if path is not None:
                return path

        print(
            "🔍 Going to search in different common paths"
            f" because `{conda_executable}` was not found in PATH.",
        )
        return _find_windows_path(conda_executable)
    return conda_executable


def _capitalize_dir(path: str, *, capitalize: bool = True, index: int = -1) -> str:
    """Capitalize or lowercase a directory in a path, on Windows only."""
    sep = "\\"
    parts = path.split(sep)
    if capitalize:
        parts[index] = parts[index].capitalize()
    else:
        parts[index] = parts[index].lower()
    return sep.join(parts)


@functools.lru_cache(1)
def _find_windows_path(conda_executable: CondaExecutable) -> str:
    """Find the path to the conda executable on Windows."""
    searched = []
    conda_roots = [
        r"%USERPROFILE%\Anaconda3",  # https://stackoverflow.com/a/58211115
        r"%USERPROFILE%\Miniconda3",  # https://stackoverflow.com/a/76545804
        r"C:\Anaconda3",  # https://stackoverflow.com/a/44597801
        r"C:\Miniconda3",  # https://stackoverflow.com/a/53685910
        r"C:\ProgramData\Anaconda3",  # https://stackoverflow.com/a/58211115
        r"C:\ProgramData\Miniconda3",  # https://stackoverflow.com/a/51003321
    ]
    if conda_executable == "mamba":
        conda_roots = [
            r"C:\ProgramData\mambaforge",  # https://github.com/mamba-org/mamba/issues/1756#issuecomment-1517284831
            r"%USERPROFILE%\AppData\Local\mambaforge",  # https://stackoverflow.com/a/75612393
            # First try native mamba locations, then Conda locations (in
            # case `conda install mamba` was used)
            *conda_roots,
        ]
    if conda_executable == "micromamba":
        conda_roots = [
            # Default installation directory based on the installation script
            # https://raw.githubusercontent.com/mamba-org/micromamba-releases/main/install.ps1
            r"%LOCALAPPDATA%\micromamba",
        ]

    extensions = (".exe", "", ".bat")
    subs = ("condabin\\", "Scripts\\", "")  # The "" is for micromamba
    for root, sub, ext, cap in itertools.product(
        conda_roots,
        subs,
        extensions,
        (True, False),
    ):
        # @sbalk reported that his `anaconda3` folder is lowercase
        path = rf"{_capitalize_dir(root, capitalize=cap)}\{sub}{conda_executable}{ext}"
        path = os.path.expandvars(path)
        searched.append(path)
        if os.path.exists(path):  # noqa: PTH110
            return path
    msg = f"Could not find {conda_executable}."
    searched_str = "\n👉 ".join(searched)
    msg = f"Could not find {conda_executable}. Searched in:\n👉 {searched_str}"
    raise FileNotFoundError(msg)


def _conda_cli_command_json(
    conda_executable: CondaExecutable,
    *args: str,
) -> dict[str, list[str]]:
    """Run a conda command and return the JSON output."""
    try:
        result = subprocess.run(
            [_maybe_exe(conda_executable), *args, "--json"],  # noqa: S603
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:  # pragma: no cover
        print(f"Error occurred: {e}")
        raise
    except json.JSONDecodeError as e:  # pragma: no cover
        print(f"Failed to parse JSON: {e}")
        raise


@functools.lru_cache(maxsize=None)
def _conda_env_list(conda_executable: CondaExecutable) -> list[str]:
    """Get a list of conda environments."""
    return _conda_cli_command_json(conda_executable, "env", "list")["envs"]


@functools.lru_cache(maxsize=None)
def _conda_info(conda_executable: CondaExecutable) -> dict:
    return _conda_cli_command_json(conda_executable, "info")


def _conda_root_prefix(conda_executable: CondaExecutable) -> Path:  # pragma: no cover
    """Get the root prefix of the conda installation."""
    if os.environ.get("MAMBA_ROOT_PREFIX"):
        return Path(os.environ["MAMBA_ROOT_PREFIX"])
    if os.environ.get("CONDA_ROOT"):
        return Path(os.environ["CONDA_ROOT"])
    info_dict = _conda_info(conda_executable)
    if conda_executable in ("conda", "mamba"):
        prefix = info_dict.get("root_prefix") or info_dict["conda_prefix"]
    else:
        assert conda_executable == "micromamba"
        prefix = info_dict["base environment"]
    return Path(prefix)


def _conda_env_dirs(
    conda_executable: CondaExecutable,
) -> list[Path]:  # pragma: no cover
    """Get a list of conda environment directories."""
    info_dict = _conda_info(conda_executable)
    if conda_executable in ("conda", "mamba"):
        envs_dirs = info_dict["envs_dirs"]
    else:
        assert conda_executable == "micromamba"
        envs_dirs = info_dict["envs directories"]
    return [Path(d) for d in envs_dirs]


def _conda_env_name_to_prefix(
    conda_executable: CondaExecutable,
    conda_env_name: str,
) -> Path:  # pragma: no cover
    """Get the prefix of a conda environment."""
    # Based on `conda.base.context.locate_prefix_by_name`
    # https://github.com/conda/conda/blob/72fe69dac8b2fef351c511c813493fef17f295e1/conda/base/context.py#L1976-L1977
    root_prefix = _conda_root_prefix(conda_executable)
    if conda_env_name in ("base", "root"):
        return root_prefix

    for envs_dir in _conda_env_dirs(conda_executable):
        prefix = envs_dir / conda_env_name
        if prefix.exists():
            return prefix

    envs = _conda_env_list(conda_executable)
    envs_str = "\n👉 ".join(envs)
    msg = (
        f"Could not find conda prefix with name `{conda_env_name}`."
        f" Available prefixes:\n👉 {envs_str}"
    )
    raise ValueError(msg)


def _python_executable(
    conda_executable: CondaExecutable,
    conda_env_name: str | None,
    conda_env_prefix: Path | None,
) -> str:
    """Get the Python executable to use for a conda environment."""
    if conda_env_name is None and conda_env_prefix is None:
        return sys.executable
    if conda_env_name:
        conda_env_prefix = _conda_env_name_to_prefix(conda_executable, conda_env_name)
    assert conda_env_prefix is not None
    if platform.system() == "Windows":  # pragma: no cover
        python_executable = conda_env_prefix / "python.exe"
    else:
        python_executable = conda_env_prefix / "bin" / "python"
    assert python_executable.exists()
    return str(python_executable)


def _pip_install_local(
    *folders: str | Path,
    editable: bool,
    dry_run: bool,
    python_executable: str,
    flags: list[str] | None = None,
) -> None:  # pragma: no cover
    pip_command = [python_executable, "-m", "pip", "install"]
    if flags:
        pip_command.extend(flags)

    for folder in sorted(folders):
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


def _install_command(  # noqa: PLR0912
    *files: Path,
    conda_executable: CondaExecutable,
    conda_env_name: str | None,
    conda_env_prefix: Path | None,
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
    """Install the dependencies of a single `requirements.yaml` or `pyproject.toml` file."""  # noqa: E501
    if no_dependencies:
        skip_pip = True
        skip_conda = True

    paths_with_extras = [parse_folder_or_filename(f) for f in files]
    requirements = parse_requirements(
        *[f.path for f in paths_with_extras],
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        verbose=verbose,
        extras=[f.extras for f in paths_with_extras],
    )
    platforms = [identify_current_platform()]
    resolved = resolve_conflicts(
        requirements.requirements,
        platforms,
        optional_dependencies=requirements.optional_dependencies,
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
        conda_env_args = []
        if conda_env_name:
            conda_env_args = ["--name", conda_env_name]
            # Check if the environment exists
            _conda_env_name_to_prefix(conda_executable, conda_env_name)
        elif conda_env_prefix:
            conda_env_args = ["--prefix", str(conda_env_prefix)]
        conda_command = [
            _maybe_exe(conda_executable),
            "install",
            "--yes",
            *channel_args,
            *conda_env_args,
        ]
        # When running the command in terminal, we need to wrap the pin in quotes
        # so what we print is what the user would type (copy-paste).
        to_print = [_format_inline_conda_package(pkg) for pkg in env_spec.conda]  # type: ignore[arg-type]
        conda_command_str = " ".join((*conda_command, *to_print))
        print(f"📦 Installing conda dependencies with `{conda_command_str}`\n")  # type: ignore[arg-type]
        if not dry_run:  # pragma: no cover
            subprocess.run((*conda_command, *env_spec.conda), check=True)  # type: ignore[arg-type]  # noqa: S603
    python_executable = _python_executable(
        conda_executable,
        conda_env_name,
        conda_env_prefix,
    )
    if env_spec.pip and not skip_pip:
        pip_command = [python_executable, "-m", "pip", "install", *env_spec.pip]
        print(f"📦 Installing pip dependencies with `{' '.join(pip_command)}`\n")
        if not dry_run:  # pragma: no cover
            subprocess.run(pip_command, check=True)  # noqa: S603

    installable = []
    if not skip_local:
        for file in paths_with_extras:
            if is_pip_installable(file.path.parent):
                installable.append(file.path.parent)
            else:  # pragma: no cover
                print(
                    f"⚠️  Project {file.path.parent} is not pip installable. "
                    "Could not find setup.py or [build-system] in pyproject.toml.",
                )

        # Install local dependencies (if any) included via `local_dependencies:`
        local_dependencies = parse_local_dependencies(
            *[p.path_with_extras for p in paths_with_extras],
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
            pip_flags = ["--no-dependencies"]  # we just ran pip/conda install, so skip
            if verbose:
                pip_flags.append("--verbose")

            _pip_install_local(
                *sorted(installable),
                editable=editable,
                dry_run=dry_run,
                python_executable=python_executable,
                flags=pip_flags,
            )

    if not dry_run:  # pragma: no cover
        print("✅ All dependencies installed successfully.")


def _install_all_command(
    *,
    conda_executable: CondaExecutable,
    conda_env_name: str | None,
    conda_env_prefix: Path | None,
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
        print(f"❌ No {_DEP_FILES} files found in {directory}")
        sys.exit(1)
    _install_command(
        *found_files,
        conda_executable=conda_executable,
        conda_env_name=conda_env_name,
        conda_env_prefix=conda_env_prefix,
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
    files: list[Path] | None,
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

    if files:  # ignores depth and directory!
        found_files = files
    else:
        found_files = find_requirements_files(
            directory,
            depth,
            verbose=verbose,
        )
        if not found_files:
            print(f"❌ No {_DEP_FILES} files found in {directory}")
            sys.exit(1)

    requirements = parse_requirements(
        *found_files,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        verbose=verbose,
    )
    if not platforms:
        platforms = requirements.platforms or [identify_current_platform()]
    resolved = resolve_conflicts(
        requirements.requirements,
        platforms,
        optional_dependencies=requirements.optional_dependencies,
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

    requirements = parse_requirements(
        *found_files,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        verbose=verbose,
    )
    resolved = resolve_conflicts(
        requirements.requirements,
        [platform],
        optional_dependencies=requirements.optional_dependencies,
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
            print(f"📝 Extra flags for `pip-compile`: {extra_flags}")

    if output_file is None:
        output_file = directory / "requirements.txt"

    cmd = [
        "pip-compile",
        "--output-file",
        str(output_file),
        *extra_flags,
        str(requirements_in),
    ]
    print(f"🔒 Locking dependencies with `{' '.join(cmd)}`\n")
    subprocess.run(cmd, check=True)  # noqa: S603
    if output_file.exists():  # pragma: no cover
        # might not exist in tests
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
        " Conda environment to avoid any issues, or provide the `--conda-env-name`"
        " or `--conda-env-prefix` option to specify the Conda environment to use."
    )
    warn(msg, stacklevel=2)
    sys.exit(1)


def _print_versions() -> None:  # pragma: no cover
    """Print version information."""
    path = Path(__file__).parent
    txt = [
        f"unidep version: {__version__}",
        f"unidep location: {path}",
        f"Python version: {sys.version}",
        f"Python executable: {sys.executable}",
    ]
    extra_packages = [
        "rich_argparse",
        "rich",
        "conda_lock",
        "pydantic",
        "pip_tools",
        "conda_package_handling",
        "ruamel.yaml",
        "packaging",
        "tomli",
    ]
    for package in extra_packages:
        version = get_package_version(package)
        if version is not None:
            txt.append(f"{package} version: {version}")

    if importlib.util.find_spec("rich") is not None:
        _print_with_rich(txt)
    else:
        print("\n".join(txt))


def _print_with_rich(data: list) -> None:
    """Print data as a table using rich, if it's installed."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(show_header=False)
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="magenta")
    for line in data:
        prop, value = line.split(":", 1)
        table.add_row(prop, value.strip())
    console.print(table)


def _pip_subcommand(
    *,
    file: list[Path],
    platforms: list[Platform],
    verbose: bool,
    ignore_pins: list[str] | None,
    skip_dependencies: list[str] | None,
    overwrite_pins: list[str] | None,
    separator: str,
) -> str:  # pragma: no cover
    platforms = platforms or [identify_current_platform()]
    assert len(file) <= 1
    path = file[0] if file else Path()
    deps = get_python_dependencies(
        path,
        platforms=platforms,
        verbose=verbose,
        ignore_pins=ignore_pins,
        skip_dependencies=skip_dependencies,
        overwrite_pins=overwrite_pins,
        include_local_dependencies=True,
    )
    pip_dependencies = deps.dependencies
    for extra in parse_folder_or_filename(path).extras:
        pip_dependencies.extend(deps.extras[extra])
    return escape_unicode(separator).join(pip_dependencies)


def main() -> None:
    """Main entry point for the command-line tool."""
    args = _parse_args()

    if args.command == "merge":  # pragma: no cover
        _merge_command(
            depth=args.depth,
            directory=args.directory,
            files=None,
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
        print(
            _pip_subcommand(
                file=args.file,
                platforms=args.platform,
                verbose=args.verbose,
                ignore_pins=args.ignore_pin,
                skip_dependencies=args.skip_dependency,
                overwrite_pins=args.overwrite_pin,
                separator=args.separator,
            ),
        )
    elif args.command == "conda":  # pragma: no cover
        platforms = args.platform or [identify_current_platform()]
        files = args.file or [Path()]
        requirements = parse_requirements(
            *files,
            ignore_pins=args.ignore_pin,
            skip_dependencies=args.skip_dependency,
            overwrite_pins=args.overwrite_pin,
            verbose=args.verbose,
        )
        resolved = resolve_conflicts(
            requirements.requirements,
            platforms,
            optional_dependencies=requirements.optional_dependencies,
        )
        env_spec = create_conda_env_specification(
            resolved,
            requirements.channels,
            platforms=platforms,
        )

        if any(parse_folder_or_filename(f).extras for f in files):
            msg = "🚧 The `conda` command currently does not support extras."
            print(msg)
            sys.exit(1)

        print(escape_unicode(args.separator).join(env_spec.conda))  # type: ignore[arg-type]
    elif args.command == "install":
        if args.conda_env_name is None and args.conda_env_prefix is None:
            _check_conda_prefix()
        _install_command(
            *(args.files or [Path()]),
            conda_executable=args.conda_executable,
            conda_env_name=args.conda_env_name,
            conda_env_prefix=args.conda_env_prefix,
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
        if args.conda_env_name is None and args.conda_env_prefix is None:
            _check_conda_prefix()
        _install_all_command(
            conda_executable=args.conda_executable,
            conda_env_name=args.conda_env_name,
            conda_env_prefix=args.conda_env_prefix,
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
            files=args.file or None,
            platforms=args.platform,
            verbose=args.verbose,
            only_global=args.only_global,
            ignore_pins=args.ignore_pin,
            skip_dependencies=args.skip_dependency,
            overwrite_pins=args.overwrite_pin,
            check_input_hash=args.check_input_hash,
            extra_flags=args.extra_flags,
            lockfile=args.lockfile,
        )
    elif args.command == "pip-compile":  # pragma: no cover
        if args.platform and len(args.platform) > 1:
            print(
                "❌ The `pip-compile` command does not support multiple platforms.",
            )
            sys.exit(1)
        platform = args.platform[0] if args.platform else identify_current_platform()
        _pip_compile_command(
            depth=args.depth,
            directory=args.directory,
            platform=platform,
            verbose=args.verbose,
            ignore_pins=args.ignore_pin,
            skip_dependencies=args.skip_dependency,
            overwrite_pins=args.overwrite_pin,
            extra_flags=args.extra_flags,
            output_file=args.output_file,
        )
    elif args.command == "version":  # pragma: no cover
        _print_versions()
