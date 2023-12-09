"""requirements.yaml - Unified Conda and Pip requirements management.

This module provides a command-line tool for managing conda environment.yaml files.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections import defaultdict
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from ruamel.yaml import YAML

from unidep._yaml_parsing import find_requirements_files, parse_yaml_requirements
from unidep.utils import add_comment_to_file, remove_top_comments, warn

if TYPE_CHECKING:
    from unidep.platform_definitions import CondaPip, Platform

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:  # pragma: no cover
        from typing_extensions import Literal


def _run_conda_lock(
    tmp_env: Path,
    conda_lock_output: Path,
    *,
    check_input_hash: bool = False,
) -> None:  # pragma: no cover
    if shutil.which("conda-lock") is None:
        msg = (
            "Cannot find `conda-lock`."
            " Please install it with `pip install conda-lock`, or"
            " `pipx install conda-lock`, or"
            " `conda install -c conda-forge conda-lock`."
        )
        raise RuntimeError(msg)
    if not check_input_hash and conda_lock_output.exists():
        print(f"🗑️ Removing existing `{conda_lock_output}`")
        conda_lock_output.unlink()
    cmd = [
        "conda-lock",
        "lock",
        "--file",
        str(tmp_env),
        "--lockfile",
        str(conda_lock_output),
    ]
    if check_input_hash:
        cmd.append("--check-input-hash")
    print(f"🔒 Locking dependencies with `{' '.join(cmd)}`\n")
    try:
        subprocess.run(cmd, check=True, text=True, capture_output=True)  # noqa: S603
        remove_top_comments(conda_lock_output)
        add_comment_to_file(
            conda_lock_output,
            extra_lines=[
                "#",
                "# This environment can be installed with",
                "# `micromamba create -f conda-lock.yml -n myenv`",
                "# This file is a `conda-lock` file generated via `unidep`.",
                "# For details see https://conda.github.io/conda-lock/",
            ],
        )
    except subprocess.CalledProcessError as e:
        print("❌ Error occurred:\n", e)
        print("Return code:", e.returncode)
        print("Output:", e.output)
        print("Error Output:", e.stderr)
        sys.exit(1)


def _conda_lock_global(
    *,
    depth: int,
    directory: str | Path,
    platform: list[Platform],
    verbose: bool,
    check_input_hash: bool,
    ignore_pins: list[str],
    skip_dependencies: list[str],
    overwrite_pins: list[str],
    lockfile: str,
) -> Path:
    """Generate a conda-lock file for the global dependencies."""
    from unidep._cli import _merge_command

    directory = Path(directory)
    tmp_env = directory / "tmp.environment.yaml"
    conda_lock_output = directory / lockfile
    _merge_command(
        depth=depth,
        directory=directory,
        name="myenv",
        output=tmp_env,
        stdout=False,
        selector="comment",
        platforms=platform,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        verbose=verbose,
    )
    _run_conda_lock(tmp_env, conda_lock_output, check_input_hash=check_input_hash)
    print(f"✅ Global dependencies locked successfully in `{conda_lock_output}`.")
    return conda_lock_output


class LockSpec(NamedTuple):
    """A specification of the lock file."""

    packages: dict[tuple[CondaPip, Platform, str], dict[str, Any]]
    dependencies: dict[tuple[CondaPip, Platform, str], set[str]]


def _parse_conda_lock_packages(
    conda_lock_packages: list[dict[str, Any]],
) -> LockSpec:
    deps: dict[CondaPip, dict[Platform, dict[str, set[str]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(set)),
    )

    def _recurse(
        package_name: str,
        resolved: dict[str, set[str]],
        dependencies: dict[str, set[str]],
    ) -> set[str]:
        if package_name in resolved:
            return resolved[package_name]
        all_deps = set(dependencies[package_name])
        for dep in dependencies[package_name]:
            all_deps.update(_recurse(dep, resolved, dependencies))
        resolved[package_name] = all_deps
        return all_deps

    for p in conda_lock_packages:
        deps[p["manager"]][p["platform"]][p["name"]].update(p["dependencies"])

    resolved: dict[CondaPip, dict[Platform, dict[str, set[str]]]] = {}
    for manager, platforms in deps.items():
        resolved_manager = resolved.setdefault(manager, {})
        for _platform, pkgs in platforms.items():
            _resolved: dict[str, set[str]] = {}
            for package in list(pkgs):
                _recurse(package, _resolved, pkgs)
            resolved_manager[_platform] = _resolved

    packages: dict[tuple[CondaPip, Platform, str], dict[str, Any]] = {}
    for p in conda_lock_packages:
        key = (p["manager"], p["platform"], p["name"])
        assert key not in packages
        packages[key] = p

    # Flatten the `dependencies` dict to same format as `packages`
    dependencies = {
        (which, platform, name): deps
        for which, platforms in resolved.items()
        for platform, pkgs in platforms.items()
        for name, deps in pkgs.items()
    }
    return LockSpec(packages, dependencies)


def _add_package_to_lock(
    *,
    name: str,
    which: CondaPip,
    platform: Platform,
    packages: dict[tuple[CondaPip, Platform, str], dict[str, Any]],
    locked: list[dict[str, Any]],
    locked_keys: set[tuple[CondaPip, Platform, str]],
) -> tuple[CondaPip, Platform, str] | None:
    key = (which, platform, name)
    if key not in packages:
        return key
    if key not in locked_keys:
        locked.append(packages[key])
        locked_keys.add(key)  # Add identifier to the set
    return None


def _add_package_with_dependencies_to_lock(
    *,
    name: str,
    which: CondaPip,
    platform: Platform,
    lock_spec: LockSpec,
    locked: list[dict[str, Any]],
    locked_keys: set[tuple[CondaPip, Platform, str]],
    missing_keys: set[tuple[CondaPip, Platform, str]],
) -> None:
    missing_key = _add_package_to_lock(
        name=name,
        which=which,
        platform=platform,
        packages=lock_spec.packages,
        locked=locked,
        locked_keys=locked_keys,
    )
    if missing_key is not None:
        missing_keys.add(missing_key)
    for dep in lock_spec.dependencies.get((which, platform, name), set()):
        if dep.startswith("__"):
            continue  # Skip meta packages
        missing_key = _add_package_to_lock(
            name=dep,
            which=which,
            platform=platform,
            packages=lock_spec.packages,
            locked=locked,
            locked_keys=locked_keys,
        )
        if missing_key is not None:
            missing_keys.add(missing_key)


def _handle_missing_keys(
    lock_spec: LockSpec,
    locked_keys: set[tuple[CondaPip, Platform, str]],
    missing_keys: set[tuple[CondaPip, Platform, str]],
    locked: list[dict[str, Any]],
) -> None:
    add_pkg = partial(
        _add_package_with_dependencies_to_lock,
        lock_spec=lock_spec,
        locked=locked,
        locked_keys=locked_keys,
        missing_keys=missing_keys,
    )

    # Do not re-add packages that with pip that are
    # already added with conda
    for which, _platform, name in locked_keys:
        if which == "conda":
            key = ("pip", _platform, name)
            missing_keys.discard(key)  # type: ignore[arg-type]

    # Add missing pip packages using conda (if possible)
    for which, _platform, name in list(missing_keys):
        if which == "pip":
            missing_keys.discard((which, _platform, name))
            add_pkg(name=name, which="conda", platform=_platform)
            if ("conda", _platform, name) in missing_keys:
                # If the package wasn't added, restore the missing key
                missing_keys.discard(("conda", _platform, name))
                missing_keys.add(("pip", _platform, name))

    if not missing_keys:
        return

    # Finally there might be some pip packages that are missing
    # because in the lock file they are installed with conda, however,
    # on Conda the name might be different than on PyPI. For example,
    # `msgpack` (pip) and `msgpack-python` (conda).
    options = {
        (which, platform, name): pkg
        for which, platform, name in missing_keys
        for (_which, _platform, _name), pkg in lock_spec.packages.items()
        if which == "pip"
        and _which == "conda"
        and platform == _platform
        and name in _name
    }
    for (which, _platform, name), pkg in options.items():
        names = _download_and_get_package_names(pkg)
        if names is None:
            continue
        if name in names:
            add_pkg(name=pkg["name"], which=pkg["manager"], platform=pkg["platform"])
            missing_keys.discard((which, _platform, name))
    if missing_keys:
        print(f"❌ Missing keys {missing_keys}")


def _conda_lock_subpackage(
    *,
    file: Path,
    lock_spec: LockSpec,
    channels: list[str],
    platforms: list[Platform],
    yaml: YAML | None,  # Passing this to preserve order!
) -> Path:
    requirements = parse_yaml_requirements(file)
    locked: list[dict[str, Any]] = []
    locked_keys: set[tuple[CondaPip, Platform, str]] = set()
    missing_keys: set[tuple[CondaPip, Platform, str]] = set()

    add_pkg = partial(
        _add_package_with_dependencies_to_lock,
        lock_spec=lock_spec,
        locked=locked,
        locked_keys=locked_keys,
        missing_keys=missing_keys,
    )

    for name, metas in requirements.requirements.items():
        if name.startswith("__"):
            continue  # Skip meta packages
        for meta in metas:
            _platforms = meta.platforms()
            if _platforms is None:
                _platforms = platforms
            else:
                _platforms = [p for p in _platforms if p in platforms]

            for _platform in _platforms:
                if _platform not in platforms:
                    continue
                add_pkg(name=name, which=meta.which, platform=_platform)
    _handle_missing_keys(
        lock_spec=lock_spec,
        locked_keys=locked_keys,
        missing_keys=missing_keys,
        locked=locked,
    )

    locked = sorted(locked, key=lambda p: (p["manager"], p["name"], p["platform"]))

    if yaml is None:  # pragma: no cover
        # When passing the same YAML instance that is used to load the file,
        # we preserve the order of the keys.
        yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.width = 4096
    yaml.representer.ignore_aliases = lambda *_: True  # Disable anchors
    conda_lock_output = file.parent / "conda-lock.yml"
    metadata = {
        "content_hash": {p: "unidep-is-awesome" for p in platforms},
        "channels": [{"url": c, "used_env_vars": []} for c in channels],
        "platforms": platforms,
        "sources": [str(file)],
    }
    with conda_lock_output.open("w") as fp:
        yaml.dump({"version": 1, "metadata": metadata, "package": locked}, fp)
    add_comment_to_file(
        conda_lock_output,
        extra_lines=[
            "#",
            "# This environment can be installed with",
            "# `micromamba create -f conda-lock.yml -n myenv`",
            "# This file is a `conda-lock` file generated via `unidep`.",
            "# For details see https://conda.github.io/conda-lock/",
        ],
    )
    return conda_lock_output


def _download_and_get_package_names(
    package: dict[str, Any],
    component: Literal["info", "pkg"] | None = None,
) -> list[str] | None:
    try:
        import conda_package_handling.api
    except ImportError:  # pragma: no cover
        print(
            "❌ Could not import `conda-package-handling` module."
            " Please install it with `pip install conda-package-handling`.",
        )
        sys.exit(1)
    url = package["url"]
    if package["manager"] != "conda":  # pragma: no cover
        return None
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        file_path = temp_path / Path(url).name
        urllib.request.urlretrieve(url, str(file_path))  # noqa: S310
        conda_package_handling.api.extract(
            str(file_path),
            dest_dir=str(temp_path),
            components=component,
        )

        if (temp_path / "site-packages").exists():
            site_packages_path = temp_path / "site-packages"
        elif (temp_path / "lib").exists():
            lib_path = temp_path / "lib"
            python_dirs = [
                d
                for d in lib_path.iterdir()
                if d.is_dir() and d.name.startswith("python")
            ]
            if not python_dirs:
                return None
            site_packages_path = python_dirs[0] / "site-packages"
        else:
            return None

        if not site_packages_path.exists():
            return None

        return [
            d.name
            for d in site_packages_path.iterdir()
            if d.is_dir() and not d.name.endswith((".dist-info", ".egg-info"))
        ]


def _conda_lock_subpackages(
    directory: str | Path,
    depth: int,
    conda_lock_file: str | Path,
) -> list[Path]:
    directory = Path(directory)
    conda_lock_file = Path(conda_lock_file)
    with YAML(typ="rt") as yaml, conda_lock_file.open() as fp:
        data = yaml.load(fp)
    channels = [c["url"] for c in data["metadata"]["channels"]]
    platforms = data["metadata"]["platforms"]
    lock_spec = _parse_conda_lock_packages(data["package"])

    lock_files: list[Path] = []
    # Assumes that different platforms have the same versions
    found_files = find_requirements_files(directory, depth)
    for file in found_files:
        if file.parent == directory:
            # This is a `requirements.yaml` file in the root directory
            # for e.g., common packages, so skip it.
            continue
        sublock_file = _conda_lock_subpackage(
            file=file,
            lock_spec=lock_spec,
            channels=channels,
            platforms=platforms,
            yaml=yaml,
        )
        print(f"📝 Generated lock file for `{file}`: `{sublock_file}`")
        lock_files.append(sublock_file)
    return lock_files


def conda_lock_command(
    *,
    depth: int,
    directory: Path,
    platform: list[Platform],
    verbose: bool,
    only_global: bool,
    check_input_hash: bool,
    ignore_pins: list[str],
    skip_dependencies: list[str],
    overwrite_pins: list[str],
    lockfile: str = "conda-lock.yml",
) -> None:
    """Generate a conda-lock file a collection of requirements.yaml files."""
    conda_lock_output = _conda_lock_global(
        depth=depth,
        directory=directory,
        platform=platform,
        verbose=verbose,
        check_input_hash=check_input_hash,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
        lockfile=lockfile,
    )
    if only_global:
        return
    sub_lock_files = _conda_lock_subpackages(
        directory=directory,
        depth=depth,
        conda_lock_file=conda_lock_output,
    )
    mismatches = _check_consistent_lock_files(
        global_lock_file=conda_lock_output,
        sub_lock_files=sub_lock_files,
    )
    if not mismatches:
        print("✅ Analyzed all lock files and found no inconsistencies.")
    elif len(mismatches) > 1:  # pragma: no cover
        print("❌ Complete table of package version mismatches:")
        _mismatch_report(mismatches, raises=False)


class Mismatch(NamedTuple):
    """A mismatch between a global and subpackage lock file."""

    name: str
    version: str
    version_global: str
    platform: Platform
    lock_file: Path
    which: CondaPip


def _check_consistent_lock_files(
    global_lock_file: Path,
    sub_lock_files: list[Path],
) -> list[Mismatch]:
    yaml = YAML(typ="safe")
    with global_lock_file.open() as fp:
        global_data = yaml.load(fp)

    global_packages: dict[str, dict[Platform, dict[CondaPip, str]]] = defaultdict(
        lambda: defaultdict(dict),
    )
    for p in global_data["package"]:
        global_packages[p["name"]][p["platform"]][p["manager"]] = p["version"]

    mismatched_packages = []
    for lock_file in sub_lock_files:
        with lock_file.open() as fp:
            data = yaml.load(fp)

        for p in data["package"]:
            name = p["name"]
            platform = p["platform"]
            version = p["version"]
            which = p["manager"]
            if global_packages.get(name, {}).get(platform, {}).get(which) == version:
                continue

            global_version = global_packages[name][platform][which]
            if global_version != version:
                mismatched_packages.append(
                    Mismatch(
                        name=name,
                        version=version,
                        version_global=global_version,
                        platform=platform,
                        lock_file=lock_file,
                        which=which,
                    ),
                )
    return mismatched_packages


def _format_table_row(
    row: list[str],
    widths: list[int],
    seperator: str = " | ",
) -> str:  # pragma: no cover
    """Format a row of the table with specified column widths."""
    return seperator.join(f"{cell:<{widths[i]}}" for i, cell in enumerate(row))


def _mismatch_report(
    mismatched_packages: list[Mismatch],
    *,
    raises: bool = False,
) -> None:  # pragma: no cover
    if not mismatched_packages:
        return

    headers = [
        "Subpackage",
        "Manager",
        "Package",
        "Version (Sub)",
        "Version (Global)",
        "Platform",
    ]

    def _to_seq(m: Mismatch) -> list[str]:
        return [
            m.lock_file.parent.name,
            m.which,
            m.name,
            m.version,
            m.version_global,
            str(m.platform),
        ]

    column_widths = [len(header) for header in headers]
    for m in mismatched_packages:
        attrs = _to_seq(m)
        for i, attr in enumerate(attrs):
            column_widths[i] = max(column_widths[i], len(attr))

    # Create the table rows
    separator_line = [w * "-" for w in column_widths]
    table_rows = [
        _format_table_row(separator_line, column_widths, seperator="-+-"),
        _format_table_row(headers, column_widths),
        _format_table_row(["-" * width for width in column_widths], column_widths),
    ]
    for m in mismatched_packages:
        row = _to_seq(m)
        table_rows.append(_format_table_row(row, column_widths))
    table_rows.append(_format_table_row(separator_line, column_widths, seperator="-+-"))

    table = "\n".join(table_rows)

    full_error_message = (
        "Version mismatches found between global and subpackage lock files:\n"
        + table
        + "\n\n‼️ You might want to pin some versions stricter"
        " in your `requirements.yaml` files."
    )

    if raises:
        raise RuntimeError(full_error_message)
    warn(full_error_message, stacklevel=2)
