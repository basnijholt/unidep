from __future__ import annotations

import re
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

    cmd = ["pixi", "list", *extra_flags]
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


def _filter_clean_deps(dependencies: list[str]) -> list[str]:
    package_names = []
    for dep in dependencies:
        # Split the dependency and the environment marker
        if ";" in dep:
            dep_part, marker_part = dep.split(";", 1)
            marker_part = marker_part.strip()
        else:
            dep_part = dep
            marker_part = ""

        # Skip if 'extra ==' is in the environment marker
        if "extra ==" in marker_part:
            continue

        # Extract the package name
        dep_part = dep_part.strip()
        package_name = re.split(r"[<>=!~\s]", dep_part)[0]
        package_names.append(package_name)

    return package_names


def _parse_pixi_lock_packages(
    pixi_lock_data: dict[str, Any],
) -> dict[str, PixiLockSpec]:
    # Build a mapping from URL to package metadata
    url_to_package = {pkg["url"]: pkg for pkg in pixi_lock_data.get("packages", [])}
    lock_specs: dict[str, PixiLockSpec] = {}
    environments = pixi_lock_data.get("environments", {})
    for env_name, env_data in environments.items():
        deps: dict[CondaPip, dict[Platform, dict[str, set[str]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(set)),
        )
        for platform, packages_dict in env_data.get("packages", {}).items():
            for manager_url in packages_dict:
                for manager, url in manager_url.items():
                    dep = url_to_package[url]
                    name = dep["name"]
                    depends = dep.get(
                        "depends" if manager == "conda" else "requires_dict",
                        [],
                    )
                    deps[manager][platform][name].update(_filter_clean_deps(depends))

        resolved: dict[CondaPip, dict[Platform, dict[str, set[str]]]] = {}
        for manager, platforms in deps.items():
            resolved_manager = resolved.setdefault(manager, {})
            for _platform, pkgs in platforms.items():
                _resolved: dict[str, set[str]] = {}
                for package in list(pkgs):
                    _recurse_pixi(package, _resolved, pkgs, set())
                resolved_manager[_platform] = _resolved

        packages: dict[tuple[CondaPip, Platform, str], list[dict[str, Any]]] = (
            defaultdict(list)
        )
        for p in pixi_lock_data.get("packages", []):
            # TODO: subdir is missing for pypi! This will cause issues
            # later in the code.
            key = (p["kind"], p.get("subdir"), p["name"])
            # Could be multiple entries for the same package,
            # e.g., different wheels for different OS versions
            packages[key].append(p)

        # Flatten the `dependencies` dict to same format as `packages`
        dependencies = {
            (which, platform, name): deps
            for which, platforms in resolved.items()
            for platform, pkgs in platforms.items()
            for name, deps in pkgs.items()
        }
        lock_specs[env_name] = PixiLockSpec(
            packages,
            dependencies,
            env_data.get("channels", []),
            env_data.get("indexes", []),
        )

    return lock_specs


def _recurse_pixi(
    package_name: str,
    resolved: dict[str, set[str]],
    dependencies: dict[str, set[str]],
    seen: set[str],
) -> set[str]:
    if package_name in resolved:
        return resolved[package_name]
    if package_name in seen:  # Circular dependency detected
        return set()
    seen.add(package_name)

    all_deps = set(dependencies.get(package_name, []))
    for dep in dependencies.get(package_name, []):
        all_deps.update(_recurse_pixi(dep, resolved, dependencies, seen))

    resolved[package_name] = all_deps
    seen.remove(package_name)
    return all_deps


def _pixi_lock_subpackage(
    *,
    file: Path,
    lock_spec: PixiLockSpec,
    platforms: list[Platform],
    yaml: YAML | None,
) -> Path:
    requirements = parse_requirements(file)
    locked_entries: dict[Platform, list[dict]] = defaultdict(list)
    locked_packages: list[dict] = []
    locked_keys: set[tuple[CondaPip, Platform, str]] = set()
    missing_keys: set[tuple[CondaPip, Platform, str]] = set()

    def add_package_with_dependencies(
        which: CondaPip,
        platform: Platform,
        name: str,
    ) -> None:
        key: tuple[CondaPip, Platform, str] = (which, platform, name)
        if key in locked_keys:
            return
        if key not in lock_spec.packages:
            missing_keys.add(key)
            return
        pkg_infos = lock_spec.packages[key]
        for pkg_info in pkg_infos:
            # Add to locked_entries
            locked_entries[platform].append({pkg_info["kind"]: pkg_info["url"]})
            # Add to locked_packages
            locked_packages.append(pkg_info)
        locked_keys.add(key)
        # Recursively add dependencies
        dependencies = lock_spec.dependencies.get(key, set())
        for dep_name in dependencies:
            add_package_with_dependencies(which, platform, dep_name)

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
                add_package_with_dependencies(spec.which, _platform, name)

    if missing_keys:
        print(f"⚠️  Missing packages: {missing_keys}")

    # Generate subproject pixi.lock
    pixi_lock_output = file.parent / "pixi.lock"
    sub_lock_data = {
        "version": 5,
        "environments": {
            "default": {
                "channels": lock_spec.channels,
                "indexes": lock_spec.indexes,
                "packages": dict(locked_entries),
            },
        },
        "packages": locked_packages,
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
            "# For details see https://pixi.sh/",
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
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        extra_flags=extra_flags,
    )
    if only_global or files:
        return

    with YAML(typ="safe") as yaml, pixi_lock_output.open() as fp:
        global_lock_data = yaml.load(fp)

    lock_specs = _parse_pixi_lock_packages(global_lock_data)["default"]
    sub_lock_files = []
    found_files = find_requirements_files(directory, depth)
    for file in found_files:
        if file.parent == directory:
            continue
        sublock_file = _pixi_lock_subpackage(
            file=file,
            lock_spec=lock_specs,
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
