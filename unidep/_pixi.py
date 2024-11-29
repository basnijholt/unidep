from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unidep._conda_env import _extract_conda_pip_dependencies
from unidep.utils import identify_current_platform

if TYPE_CHECKING:
    from unidep.platform_definitions import CondaPip, Platform, Spec

try:  # pragma: no cover
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    HAS_TOML = True
except ImportError:  # pragma: no cover
    HAS_TOML = False


def generate_pixi_toml(
    resolved_dependencies: dict[str, dict[Platform | None, dict[CondaPip, Spec]]],
    project_name: str | None,
    channels: list[str],
    platforms: list[Platform],
    output_file: str | Path | None = "pixi.toml",
    *,
    verbose: bool = False,
) -> None:
    pixi_data = _initialize_pixi_data(channels, platforms, project_name)
    _process_dependencies(pixi_data, resolved_dependencies)
    _write_pixi_toml(pixi_data, output_file, verbose=verbose)


def _initialize_pixi_data(
    channels: list[str],
    platforms: list[Platform],
    project_name: str | None,
) -> dict[str, dict[str, Any]]:
    pixi_data: dict[str, dict[str, Any]] = {}
    if not platforms:
        platforms = [identify_current_platform()]
    # Include extra configurations from pyproject.toml
    sections = _parse_pixi_sections_from_pyproject()
    pixi_data.update(sections)

    # Set 'project' section
    pixi_data.setdefault("project", {})
    pixi_data["project"].setdefault("name", project_name or Path.cwd().name)
    pixi_data["project"].setdefault("platforms", platforms)
    pixi_data["project"].setdefault("channels", channels)

    # Initialize dependencies sections
    pixi_data.setdefault("dependencies", {})
    pixi_data.setdefault("pypi-dependencies", {})
    pixi_data.setdefault("target", {})  # For platform-specific dependencies

    return pixi_data


def _process_dependencies(
    pixi_data: dict[str, dict[str, Any]],
    resolved_dependencies: dict[str, dict[Platform | None, dict[CondaPip, Spec]]],
) -> None:
    # Extract conda and pip dependencies
    conda_deps, pip_deps = _extract_conda_pip_dependencies(resolved_dependencies)

    # Process conda dependencies
    for pkg_name, platform_to_spec in conda_deps.items():
        for _platform, spec in platform_to_spec.items():
            pin = spec.pin or "*"
            if _platform is None:
                # Applies to all platforms
                pixi_data["dependencies"][pkg_name] = pin
            else:
                # Platform-specific dependency
                # Ensure target section exists
                target = pixi_data["target"].setdefault(_platform, {})
                deps = target.setdefault("dependencies", {})
                deps[pkg_name] = pin

    # Process pip dependencies
    for pkg_name, platform_to_spec in pip_deps.items():
        for _platform, spec in platform_to_spec.items():
            pin = spec.pin or "*"
            if _platform is None:
                # Applies to all platforms
                pixi_data["pypi-dependencies"][pkg_name] = pin
            else:
                # Platform-specific dependency
                # Ensure target section exists
                target = pixi_data["target"].setdefault(_platform, {})
                deps = target.setdefault("pypi-dependencies", {})
                deps[pkg_name] = pin

    # Remove empty sections if necessary
    if not pixi_data["dependencies"]:
        del pixi_data["dependencies"]
    if not pixi_data["pypi-dependencies"]:
        del pixi_data["pypi-dependencies"]
    if not pixi_data["target"]:
        del pixi_data["target"]


def _write_pixi_toml(
    pixi_data: dict[str, dict[str, Any]],
    output_file: str | Path | None,
    *,
    verbose: bool,
) -> None:
    try:
        import tomli_w
    except ImportError:  # pragma: no cover
        msg = (
            "❌ `tomli_w` is required to write TOML files."
            " Install it with `pip install tomli_w`."
        )
        raise ImportError(msg) from None

    # Write pixi.toml file
    if output_file is not None:
        with open(output_file, "wb") as f:  # noqa: PTH123
            tomli_w.dump(pixi_data, f)
    else:
        # to stdout
        tomli_w.dump(pixi_data, sys.stdout.buffer)
    if verbose:
        print(f"✅ Generated pixi.toml at {output_file}")


def _parse_pixi_sections_from_pyproject() -> dict[str, Any]:
    pyproject_path = Path("pyproject.toml")
    if not pyproject_path.exists():
        return {}
    with pyproject_path.open("rb") as f:
        pyproject_data = tomllib.load(f)
    return pyproject_data.get("tool", {}).get("unidep", {}).get("pixi", {})
