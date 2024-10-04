from __future__ import annotations

import shutil
import subprocess
import sys
from collections import defaultdict
from typing import TYPE_CHECKING, Any, NamedTuple

from ruamel.yaml import YAML

from unidep._dependencies_parsing import find_requirements_files, parse_requirements
from unidep.utils import add_comment_to_file, change_directory

if TYPE_CHECKING:
    from pathlib import Path

    from unidep.platform_definitions import Platform

    if sys.version_info >= (3, 8):
        pass
    else:
        pass


def _run_pixi_lock(
    pixi_toml: Path,
    pixi_lock_output: Path,
    *,
    check_input_hash: bool = False,
    extra_flags: list[str],
) -> None:
    if shutil.which("pixi") is None:
        msg = (
            "Cannot find `pixi`."
            " Please install it with `mamba install -c conda-forge pixi`."
        )
        raise RuntimeError(msg)
    if not check_input_hash and pixi_lock_output.exists():
        print(f"🗑️ Removing existing `{pixi_lock_output}`")
        pixi_lock_output.unlink()

    cmd = [
        "pixi",
        "list",
        *extra_flags,
    ]
    if check_input_hash:
        cmd.append("--check-input-hash")
    print(f"🔒 Locking dependencies with `{' '.join(cmd)}`\n")
    try:
        with change_directory(pixi_toml.parent):
            subprocess.run(cmd, check=True, text=True, capture_output=True)
        # Optionally process the lock file if needed
        add_comment_to_file(
            pixi_lock_output,
            extra_lines=[
                "#",
                "# This environment can be installed with",
                "# `pixi install`",
                "# This file is a `pixi.lock` file generated via `unidep`.",
                "# For details see https://pixi.sh/",
            ],
        )
    except subprocess.CalledProcessError as e:
        print("❌ Error occurred:\n", e)
        print("Return code:", e.returncode)
        print("Output:", e.output)
        print("Error Output:", e.stderr)
        sys.exit(1)


def _pixi_lock_global(
    *,
    depth: int,
    directory: Path,
    files: list[Path] | None,
    platforms: list[Platform],
    verbose: bool,
    check_input_hash: bool,
    ignore_pins: list[str],
    skip_dependencies: list[str],
    overwrite_pins: list[str],
    extra_flags: list[str],
) -> Path:
    """Generate a pixi.lock file for the global dependencies."""
    from unidep._cli import _merge_command

    if files:
        directory = files[0].parent

    pixi_toml = directory / "pixi.toml"
    pixi_lock_output = directory / "pixi.lock"
    _merge_command(
        depth=depth,
        directory=directory,
        files=files,
        name="myenv",
        output=pixi_toml,
        stdout=False,
        selector="comment",
        platforms=platforms,
        ignore_pins=ignore_pins,
        skip_dependencies=skip_dependencies,
        overwrite_pins=overwrite_pins,
        pixi=True,
        verbose=verbose,
    )
    _run_pixi_lock(
        pixi_toml,
        pixi_lock_output,
        check_input_hash=check_input_hash,
        extra_flags=extra_flags,
    )
    print("✅ Global dependencies locked successfully in `pixi.lock`.")
    return pixi_toml.with_name("pixi.lock")


class PixiLockSpec(NamedTuple):
    """A specification of the pixi lock file."""

    packages: dict[tuple[Platform, str], dict[str, Any]]


def _parse_pixi_lock_packages(
    pixi_lock_data: dict[str, Any],
) -> PixiLockSpec:
    packages = {}
    environments = pixi_lock_data.get("environments", {})
    for env_name, env_data in environments.items():
        channels = env_data.get("channels", [])
        for platform, packages_list in env_data.get("packages", {}).items():
            for pkg_entry in packages_list:
                for manager, url in pkg_entry.items():
                    # Extract package name from URL
                    package_filename = url.split("/")[-1]
                    # Remove the extension
                    if package_filename.endswith((".conda", ".tar.bz2")):
                        package_filename = package_filename.rsplit(".", 1)[0]
                    # For conda packages, format is name-version-build
                    parts = package_filename.split("-")
                    if len(parts) >= 3:
                        package_name = "-".join(parts[:-2])
                        package_version = parts[-2]
                    else:
                        package_name = parts[0]
                        package_version = parts[1] if len(parts) > 1 else ""
                    key = (platform, package_name)
                    packages[key] = {
                        "environment": env_name,
                        "channels": channels,
                        "package": pkg_entry,
                        "manager": manager,
                        "url": url,
                        "version": package_version,
                    }
    return PixiLockSpec(packages=packages)


def _pixi_lock_subpackage(
    *,
    file: Path,
    lock_spec: PixiLockSpec,
    platforms: list[Platform],
    yaml: YAML | None,  # Passing this to preserve order!
) -> Path:
    requirements = parse_requirements(file)
    locked_entries: dict[Platform, list[dict]] = defaultdict(list)

    for name, specs in requirements.requirements.items():
        if name.startswith("__"):
            continue
        for spec in specs:
            _platforms = spec.platforms()
            if _platforms is None:
                _platforms = platforms
            else:
                _platforms = [p for p in _platforms if p in platforms]

            for _platform in _platforms:
                key = (_platform, name)
                if key in lock_spec.packages:
                    pkg_entry = lock_spec.packages[key]["package"]
                    locked_entries[_platform].append(pkg_entry)
                else:
                    print(
                        f"⚠️  Package {name} for platform {_platform} not found"
                        " in global lock file.",
                    )

    # Generate subproject pixi.lock
    pixi_lock_output = file.parent / "pixi.lock"
    sub_lock_data = {
        "version": 5,
        "environments": {
            "default": {
                "channels": lock_spec.packages[next(iter(lock_spec.packages))][
                    "channels"
                ],
                "packages": dict(locked_entries),
            },
        },
    }

    if yaml is None:
        yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.width = 4096
    yaml.representer.ignore_aliases = lambda *_: True  # Disable anchors

    with pixi_lock_output.open("w") as fp:
        yaml.dump(sub_lock_data, fp)

    add_comment_to_file(
        pixi_lock_output,
        extra_lines=[
            "#",
            "# This environment can be installed with",
            "# `pixi install`",
            "# This file is a `pixi.lock` file generated via `unidep`.",
            "# For details see https://github.com/pyx/conda-pix",
        ],
    )
    return pixi_lock_output


def _check_consistent_lock_files(
    global_lock_file: Path,
    sub_lock_files: list[Path],
) -> list[str]:
    yaml = YAML(typ="safe")
    with global_lock_file.open() as fp:
        global_data = yaml.load(fp)

    global_packages = set()
    environments = global_data.get("environments", {})
    for env_data in environments.values():
        for packages_list in env_data.get("packages", {}).values():
            print(f"{packages_list=}")
            global_packages.update(packages_list)

    mismatches = []
    for lock_file in sub_lock_files:
        with lock_file.open() as fp:
            data = yaml.load(fp)

        sub_packages = set()
        environments = data.get("environments", {})
        for env_data in environments.values():
            for packages_list in env_data.get("packages", {}).values():
                sub_packages.update(packages_list)

        if not sub_packages.issubset(global_packages):
            missing = sub_packages - global_packages
            mismatches.append(
                f"Packages {missing} in {lock_file} not found in global lock file.",
            )

    return mismatches


def pixi_lock_command(
    *,
    depth: int,
    directory: Path,
    files: list[Path] | None,
    platforms: list[Platform],
    verbose: bool,
    only_global: bool,
    check_input_hash: bool,
    ignore_pins: list[str],
    skip_dependencies: list[str],
    overwrite_pins: list[str],
    extra_flags: list[str],
) -> None:
    """Generate a pixi.lock file for a collection of dependencies."""
    if extra_flags:
        assert extra_flags[0] == "--"
        extra_flags = extra_flags[1:]
        if verbose:
            print(f"📝 Extra flags for `pixi lock`: {extra_flags}")

    pixi_lock_output = _pixi_lock_global(
        depth=depth,
        directory=directory,
        files=files,
        platforms=platforms,
        verbose=verbose,
        check_input_hash=check_input_hash,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        extra_flags=extra_flags,
    )
    if only_global or files:
        return

    with YAML(typ="safe") as yaml, pixi_lock_output.open() as fp:
        global_lock_data = yaml.load(fp)

    lock_spec = _parse_pixi_lock_packages(global_lock_data)

    sub_lock_files = []
    found_files = find_requirements_files(directory, depth)
    for file in found_files:
        if file.parent == directory:
            continue
        sublock_file = _pixi_lock_subpackage(
            file=file,
            lock_spec=lock_spec,
            platforms=platforms,
            yaml=yaml,
        )
        print(f"📝 Generated lock file for `{file}`: `{sublock_file}`")
        sub_lock_files.append(sublock_file)

    mismatches = _check_consistent_lock_files(
        global_lock_file=pixi_lock_output,
        sub_lock_files=sub_lock_files,
    )
    if not mismatches:
        print("✅ Analyzed all lock files and found no inconsistencies.")
    else:
        print("❌ Mismatches found:")
        for mismatch in mismatches:
            print(mismatch)
