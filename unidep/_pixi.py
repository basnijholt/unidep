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


def _format_pin(pin: str) -> Any:
    parts = pin.split()
    if len(parts) == 2:  # noqa: PLR2004
        return {"version": parts[0], "build": parts[1]}
    return pin


def _group_by_origin(
    resolved_deps: dict[str, dict[Platform | None, dict[CondaPip, Spec]]],
) -> dict[Path, dict[str, dict[Platform | None, dict[CondaPip, Spec]]]]:
    groups: dict[Path, dict[str, dict[Platform | None, dict[CondaPip, Spec]]]] = {}
    for pkg_name, platform_map in resolved_deps.items():
        for plat, manager_map in platform_map.items():
            for manager, spec in manager_map.items():
                for origin in spec.origin:
                    # Normalize origin to a Path object
                    origin_path = Path(origin)
                    groups.setdefault(origin_path, {})
                    groups[origin_path].setdefault(pkg_name, {})
                    groups[origin_path][pkg_name].setdefault(plat, {})
                    groups[origin_path][pkg_name][plat][manager] = spec
    return groups


def _process_dependencies(  # noqa: PLR0912
    pixi_data: dict[str, dict[str, Any]],
    resolved_dependencies: dict[str, dict[Platform | None, dict[CondaPip, Spec]]],
) -> None:
    """Process the resolved dependencies and update the pixi manifest data.

    This function first groups the resolved dependencies by origin (using
    _group_by_origin) and then creates a separate feature (under the "feature"
    key in pixi_data) for each origin. The feature name is derived using the
    parent directory's stem of the origin file.

    After creating the per-origin features, if the manifest does not yet have an
    "environments" table, we automatically add one with:
      - a "default" environment that includes all features, and
      - one environment per feature (with the feature name as the sole member).
    """
    # --- Step 1: Group by origin and create per-origin features ---
    origin_groups = _group_by_origin(resolved_dependencies)
    features = pixi_data.setdefault("feature", {})

    for origin_path, group_deps in origin_groups.items():
        # Derive a feature name from the parent folder of the origin file.
        feature_name = origin_path.resolve().parent.stem

        # Initialize the feature entry.
        feature_entry: dict[str, Any] = {
            "dependencies": {},
            "pypi-dependencies": {},
            "target": {},
        }

        # Extract conda and pip dependencies from the grouped data.
        group_conda, group_pip = _extract_conda_pip_dependencies(group_deps)

        # Process conda dependencies for this feature.
        for pkg_name, platform_to_spec in group_conda.items():
            for _platform, spec in platform_to_spec.items():
                pin = spec.pin or "*"
                pin = _format_pin(pin)
                if _platform is None:
                    feature_entry["dependencies"][pkg_name] = pin
                else:
                    target = feature_entry["target"].setdefault(_platform, {})
                    deps = target.setdefault("dependencies", {})
                    deps[pkg_name] = pin

        # Process pip dependencies for this feature.
        for pkg_name, platform_to_spec in group_pip.items():
            for _platform, spec in platform_to_spec.items():
                pin = spec.pin or "*"
                if _platform is None:
                    feature_entry["pypi-dependencies"][pkg_name] = pin
                else:
                    target = feature_entry["target"].setdefault(_platform, {})
                    deps = target.setdefault("pypi-dependencies", {})
                    deps[pkg_name] = pin

        # Remove empty sections.
        if not feature_entry["dependencies"]:
            del feature_entry["dependencies"]
        if not feature_entry["pypi-dependencies"]:
            del feature_entry["pypi-dependencies"]
        if not feature_entry["target"]:
            del feature_entry["target"]

        # Save this feature entry.
        features[feature_name] = feature_entry

    # --- Step 2: Automatically add the environments table if not already defined ---
    if "environments" not in pixi_data:
        all_features = list(features.keys())
        pixi_data["environments"] = {}
        # The "default" environment will include all features.
        pixi_data["environments"]["default"] = all_features
        # Also create one environment per feature.
        for feat in all_features:
            # Environment names cannot use _, only lowercase letters, digits, and -
            name = feat.replace("_", "-")
            pixi_data["environments"][name] = [feat]


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
