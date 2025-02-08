from __future__ import annotations

import shutil
import subprocess
import sys
from typing import TYPE_CHECKING, Any, NamedTuple

from ruamel.yaml import YAML

from unidep._dependencies_parsing import find_requirements_files
from unidep.utils import add_comment_to_file, change_directory

if TYPE_CHECKING:
    from pathlib import Path

    from unidep.platform_definitions import CondaPip, Platform


def _run_pixi_lock(
    pixi_toml: Path,
    pixi_lock_output: Path,
    *,
    extra_flags: list[str],
) -> None:
    if shutil.which("pixi") is None:
        msg = (
            "Cannot find `pixi`."
            " Please install it, see the documentation"
            " at https://pixi.sh/latest/"
        )
        raise RuntimeError(msg)
    if pixi_lock_output.exists():
        print(f"🗑️ Removing existing `{pixi_lock_output}`")
        pixi_lock_output.unlink()

    cmd = ["pixi", "lock", "--manifest-path", str(pixi_toml), *extra_flags]
    print(f"🔒 Locking dependencies with `{' '.join(cmd)}`\n")
    try:
        with change_directory(pixi_toml.parent):
            subprocess.run(cmd, check=True, text=True)
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
        extra_flags=extra_flags,
    )
    print("✅ Global dependencies locked successfully in `pixi.lock`.")
    return pixi_toml.with_name("pixi.lock")


class PixiLockSpec(NamedTuple):
    """A specification of the pixi lock file."""

    packages: dict[tuple[CondaPip, Platform, str], list[dict[str, Any]]]
    dependencies: dict[tuple[CondaPip, Platform, str], set[str]]
    channels: list[dict[str, str]]
    indexes: list[str]


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
            for pkg_entry in packages_list:
                # pkg_entry is a dict like {'conda': 'url'}
                for url in pkg_entry.values():
                    global_packages.add(url)

    mismatches = []
    for lock_file in sub_lock_files:
        with lock_file.open() as fp:
            data = yaml.load(fp)

        sub_packages = set()
        environments = data.get("environments", {})
        for env_data in environments.values():
            for packages_list in env_data.get("packages", {}).values():
                for pkg_entry in packages_list:
                    for url in pkg_entry.values():
                        sub_packages.add(url)

        if not sub_packages.issubset(global_packages):
            missing = sub_packages - global_packages
            mismatches.append(
                f"Packages {missing} in {lock_file} not found in global lock file.",
            )

    return mismatches


def _generate_sub_lock_file(
    feature_name: str,
    global_lock_data: dict[str, Any],
    yaml_obj: YAML,
    output_dir: Path,
) -> Path:
    """Generate a sub-lock file for a given feature.

    Parameters
    ----------
    feature_name
        The name of the feature (derived from the parent folder's stem).
    global_lock_data
        The global lock file data as a dict.
    yaml_obj
        A ruamel.yaml YAML instance for dumping.
    output_dir
        The directory where the sublock file should be written.

    Returns
    -------
      - The Path to the newly written sub-lock file.

    The new lock file will contain a single environment ("default") whose contents
    are exactly the environment for the given feature in the global lock file. It
    also includes only the package entries from the global "packages" list that are
    used by that environment.

    """
    # Look up the environment for the given feature.
    envs = global_lock_data.get("environments", {})
    env_data = envs.get(feature_name)
    if env_data is None:
        msg = f"Feature '{feature_name}' not found in the global lock file."
        raise ValueError(msg)

    # Create a new lock dictionary with version and a single env renamed "default".
    new_lock = {
        "version": global_lock_data.get("version"),
        "environments": {"default": env_data},
    }

    # Collect all URLs from the environment's package list.
    used_urls = set()
    # The environment data is expected to have a "packages" key mapping each platform
    # to a list of package entry dicts.
    env_packages = env_data.get("packages", {})
    for pkg_list in env_packages.values():
        for pkg_entry in pkg_list:
            # Assume each pkg_entry is a dict with one key: either "conda" or "pypi"
            for url in pkg_entry.values():
                used_urls.add(url)

    # Filter the global packages list to include only those entries used in this env.
    global_packages = global_lock_data.get("packages", [])
    filtered_packages = [
        pkg
        for pkg in global_packages
        if (pkg.get("conda") in used_urls) or (pkg.get("pypi") in used_urls)
    ]
    new_lock["packages"] = filtered_packages

    # Write the new lock file into output_dir as "pixi.lock"
    output_file = output_dir / "pixi.lock"
    with output_file.open("w") as f:
        yaml_obj.dump(new_lock, f)
    return output_file


# Updated pixi_lock_command
def pixi_lock_command(
    *,
    depth: int,
    directory: Path,
    files: list[Path] | None,
    platforms: list[Platform],
    verbose: bool,
    only_global: bool,
    ignore_pins: list[str],
    skip_dependencies: list[str],
    overwrite_pins: list[str],
    extra_flags: list[str],
) -> None:
    """Generate a pixi.lock file for a collection of dependencies.

    This command first creates a global lock file (using _pixi_lock_global).
    Then, if neither only_global is True nor specific files were passed, it scans
    for requirements files in subdirectories. For each such file, it derives a
    feature name from the parent directory's stem and generates a sub-lock file
    that contains a single environment called "default" built from the corresponding
    environment in the global lock file.
    """
    # Process extra flags (assume they are prefixed with "--")
    if extra_flags:
        assert extra_flags[0] == "--"
        extra_flags = extra_flags[1:]
        if verbose:
            print(f"📝 Extra flags for `pixi lock`: {extra_flags}")

    # Step 1: Generate the global lock file.
    global_lock_file = _pixi_lock_global(
        depth=depth,
        directory=directory,
        files=files,
        platforms=platforms,
        verbose=verbose,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        extra_flags=extra_flags,
    )
    # If only_global or specific files were provided, do not generate sublock files.
    if only_global or files:
        return

    # Step 2: Load the global lock file.
    yaml_obj = YAML(typ="rt")
    with global_lock_file.open() as fp:
        global_lock_data = yaml_obj.load(fp)

    # Step 3: Find all requirements files in subdirectories.
    found_files = find_requirements_files(directory, depth)
    sub_lock_files = []
    for req_file in found_files:
        # Skip files in the root directory.
        if req_file.parent == directory:
            continue

        # Derive feature name from the parent directory's stem.
        feature_name = req_file.resolve().parent.stem
        if verbose:
            print(
                f"🔍 Processing sublock for feature '{feature_name}' from file: {req_file}",  # noqa: E501,
            )
        sublock_file = _generate_sub_lock_file(
            feature_name=feature_name,
            global_lock_data=global_lock_data,
            yaml_obj=yaml_obj,
            output_dir=req_file.parent,
        )

        print(f"📝 Generated sublock file for '{req_file}': {sublock_file}")
        sub_lock_files.append(sublock_file)

    # Step 3: Check consistency between the global and the sublock files.
    mismatches = _check_consistent_lock_files(
        global_lock_file=global_lock_file,
        sub_lock_files=sub_lock_files,
    )
    if not mismatches:
        print("✅ Analyzed all lock files and found no inconsistencies.")
    else:
        print("❌ Mismatches found:")
        for mismatch in mismatches:
            print(mismatch)
