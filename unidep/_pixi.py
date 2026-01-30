"""Simple Pixi.toml generation without conflict resolution."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from unidep._dependencies_parsing import parse_requirements
from unidep.platform_definitions import platforms_from_selector
from unidep.utils import identify_current_platform, is_pip_installable

if TYPE_CHECKING:
    if sys.version_info >= (3, 10):
        from typing import TypeAlias
    else:
        from typing_extensions import TypeAlias

    from unidep._dependencies_parsing import ParsedRequirements
    from unidep.platform_definitions import Platform

    # Type alias for the extracted dependencies structure
    # Maps platform (or None for universal) to (conda_deps, pip_deps)
    PlatformDeps: TypeAlias = Dict[
        Optional[str],
        Tuple[Dict[str, str], Dict[str, str]],
    ]

try:
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # pragma: no cover
    HAS_TOML = True
except ImportError:  # pragma: no cover
    HAS_TOML = False


def _get_package_name(project_dir: Path) -> str | None:
    """Get the package name from pyproject.toml or setup.py."""
    pyproject_path = project_dir / "pyproject.toml"
    if pyproject_path.exists() and HAS_TOML:
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
            if "project" in data and "name" in data["project"]:
                # Normalize package name for use in dependencies
                # Replace dots and hyphens with underscores
                name = data["project"]["name"]
                return name.replace("-", "_").replace(".", "_")
    # Fallback to directory name
    return project_dir.name


def generate_pixi_toml(  # noqa: PLR0912, C901, PLR0915
    *requirements_files: Path,
    project_name: str | None = None,
    channels: list[str] | None = None,
    platforms: list[Platform] | None = None,
    output_file: str | Path | None = "pixi.toml",
    verbose: bool = False,
) -> None:
    """Generate a pixi.toml file from requirements files.

    This function creates a pixi.toml with features for each requirements file,
    letting Pixi handle all dependency resolution and conflict management.
    """
    if not requirements_files:
        requirements_files = (Path.cwd(),)

    # Initialize pixi structure
    pixi_data: dict[str, Any] = {}

    # Collect channels and platforms from all requirements files
    all_channels = set()
    all_platforms = set()

    # If single file, put dependencies at root level
    if len(requirements_files) == 1:
        req = parse_requirements(requirements_files[0], verbose=verbose)
        platform_deps = _extract_dependencies(req)

        # Use channels and platforms from the requirements file
        if req.channels:
            all_channels.update(req.channels)
        if req.platforms:
            all_platforms.update(req.platforms)

        # Get universal (non-platform-specific) dependencies
        conda_deps, pip_deps = platform_deps.get(None, ({}, {}))

        if conda_deps:
            pixi_data["dependencies"] = conda_deps
        if pip_deps:
            pixi_data["pypi-dependencies"] = pip_deps

        # Add platform-specific dependencies as target sections
        _add_target_sections(pixi_data, platform_deps)

        # Check if there's a local package in the same directory
        req_file = requirements_files[0]
        req_dir = req_file.parent if req_file.is_file() else req_file
        if is_pip_installable(req_dir):
            # Add the local package as an editable dependency
            if "pypi-dependencies" not in pixi_data:
                pixi_data["pypi-dependencies"] = {}
            # Get the actual package name from pyproject.toml
            package_name = _get_package_name(req_dir) or req_dir.name
            pixi_data["pypi-dependencies"][package_name] = {
                "path": ".",
                "editable": True,
            }
    else:
        # Multiple files: create features
        pixi_data["feature"] = {}
        pixi_data["environments"] = {}
        all_features = []

        for req_file in requirements_files:
            feature_name = req_file.parent.stem if req_file.is_file() else req_file.stem
            req = parse_requirements(req_file, verbose=verbose)
            platform_deps = _extract_dependencies(req)

            # Collect channels and platforms
            if req.channels:
                all_channels.update(req.channels)
            if req.platforms:
                all_platforms.update(req.platforms)

            # Get universal (non-platform-specific) dependencies
            conda_deps, pip_deps = platform_deps.get(None, ({}, {}))

            feature: dict[str, Any] = {}
            if conda_deps:
                feature["dependencies"] = conda_deps
            if pip_deps:
                feature["pypi-dependencies"] = pip_deps

            # Add platform-specific dependencies as target sections within the feature
            for platform, (plat_conda, plat_pip) in platform_deps.items():
                if platform is None:
                    continue
                # Note: platforms only exist in platform_deps if they have deps,
                # so we don't need to check for empty plat_conda/plat_pip
                if "target" not in feature:
                    feature["target"] = {}
                if platform not in feature["target"]:
                    feature["target"][platform] = {}
                if plat_conda:
                    feature["target"][platform]["dependencies"] = plat_conda
                if plat_pip:
                    feature["target"][platform]["pypi-dependencies"] = plat_pip

            # Check if there's a local package in the same directory
            req_dir = req_file.parent if req_file.is_file() else req_file
            if is_pip_installable(req_dir):
                # Add the local package as an editable dependency
                if "pypi-dependencies" not in feature:
                    feature["pypi-dependencies"] = {}
                # Get the actual package name from pyproject.toml
                package_name = _get_package_name(req_dir) or feature_name
                # Use relative path from the output file location
                rel_path = f"./{feature_name}"
                feature["pypi-dependencies"][package_name] = {
                    "path": rel_path,
                    "editable": True,
                }

            if feature:  # Only add non-empty features
                pixi_data["feature"][feature_name] = feature
                all_features.append(feature_name)

        # Create environments
        if all_features:
            pixi_data["environments"]["default"] = all_features
            for feat in all_features:
                # Environment names can't have underscores
                env_name = feat.replace("_", "-")
                pixi_data["environments"][env_name] = [feat]

    # Set project metadata with collected channels and platforms
    final_platforms = (
        list(all_platforms)
        if all_platforms
        else (platforms or [identify_current_platform()])
    )
    pixi_data["project"] = {
        "name": project_name or Path.cwd().name,
        "channels": (
            list(all_channels) if all_channels else (channels or ["conda-forge"])
        ),
        "platforms": final_platforms,
    }

    # Filter target sections to only include platforms in the project's platforms list
    _filter_targets_by_platforms(pixi_data, set(final_platforms))

    # Write the pixi.toml file
    _write_pixi_toml(pixi_data, output_file, verbose=verbose)


def _extract_dependencies(  # noqa: PLR0912
    requirements: ParsedRequirements,
) -> PlatformDeps:
    """Extract conda and pip dependencies from parsed requirements.

    Returns a dict mapping platform (or None for universal) to (conda_deps, pip_deps).
    Platform-specific dependencies are mapped to their respective platforms.
    No conflict resolution - just pass through what's specified.
    """
    # Initialize with universal deps (None key)
    platform_deps: PlatformDeps = {None: ({}, {})}

    # Process each package's specifications
    for pkg_name, specs in requirements.requirements.items():
        for spec in specs:
            # Format the version pin or use "*" if no pin
            version = spec.pin.replace(" ", "") if spec.pin else "*"

            if spec.selector:
                # Platform-specific dependency
                # Get list of platforms this selector maps to
                target_platforms = platforms_from_selector(spec.selector)
                for platform in target_platforms:
                    if platform not in platform_deps:
                        platform_deps[platform] = ({}, {})
                    conda_deps, pip_deps = platform_deps[platform]
                    if spec.which == "conda":
                        # Prefer pinned versions
                        if pkg_name not in conda_deps or spec.pin:
                            conda_deps[pkg_name] = version
                    elif spec.which == "pip":  # noqa: SIM102
                        # Only add to pip if not already in conda for this platform
                        if pkg_name not in conda_deps and (
                            pkg_name not in pip_deps or spec.pin
                        ):
                            pip_deps[pkg_name] = version
            else:
                # Universal dependency (no platform selector)
                conda_deps, pip_deps = platform_deps[None]
                if spec.which == "conda":
                    # Prefer pinned versions
                    if pkg_name not in conda_deps or spec.pin:
                        conda_deps[pkg_name] = version
                elif spec.which == "pip":  # noqa: SIM102
                    # Only add to pip if not already in conda
                    if pkg_name not in conda_deps and (
                        pkg_name not in pip_deps or spec.pin
                    ):
                        pip_deps[pkg_name] = version

    return platform_deps


def _add_target_sections(
    pixi_data: dict[str, Any],
    platform_deps: PlatformDeps,
) -> None:
    """Add target.<platform>.dependencies sections for platform-specific deps."""
    for platform, (conda_deps, pip_deps) in platform_deps.items():
        if platform is None:
            # Universal deps are handled separately
            continue
        # Note: platforms only exist in platform_deps if they have deps,
        # so we don't need to check for empty conda_deps/pip_deps

        # Initialize target section if needed
        if "target" not in pixi_data:
            pixi_data["target"] = {}
        if platform not in pixi_data["target"]:
            pixi_data["target"][platform] = {}

        target = pixi_data["target"][platform]
        if conda_deps:
            target["dependencies"] = conda_deps
        if pip_deps:
            target["pypi-dependencies"] = pip_deps


def _filter_targets_by_platforms(
    pixi_data: dict[str, Any],
    valid_platforms: set[str],
) -> None:
    """Filter target sections to only include platforms in valid_platforms.

    This removes targets for platforms that aren't in the project's platforms list,
    which would otherwise cause pixi to emit warnings.
    """
    # Filter root-level targets
    if "target" in pixi_data:
        pixi_data["target"] = {
            platform: deps
            for platform, deps in pixi_data["target"].items()
            if platform in valid_platforms
        }
        # Remove empty target section
        if not pixi_data["target"]:
            del pixi_data["target"]

    # Filter feature-level targets
    if "feature" in pixi_data:
        for feature_data in pixi_data["feature"].values():
            if "target" in feature_data:
                feature_data["target"] = {
                    platform: deps
                    for platform, deps in feature_data["target"].items()
                    if platform in valid_platforms
                }
                # Remove empty target section
                if not feature_data["target"]:
                    del feature_data["target"]


def _write_pixi_toml(
    pixi_data: dict[str, Any],
    output_file: str | Path | None,
    *,
    verbose: bool = False,
) -> None:
    """Write the pixi data structure to a TOML file."""
    try:
        import tomli_w
    except ImportError:  # pragma: no cover
        msg = (
            "❌ `tomli_w` is required to write TOML files. "
            "Install it with `pip install tomli_w`."
        )
        raise ImportError(msg) from None

    if output_file is not None:
        output_path = Path(output_file)
        with output_path.open("wb") as f:
            tomli_w.dump(pixi_data, f)
        if verbose:
            print(f"✅ Generated pixi.toml at {output_path}")
    else:
        # Output to stdout
        tomli_w.dump(pixi_data, sys.stdout.buffer)
