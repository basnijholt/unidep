"""Simple Pixi.toml generation without conflict resolution."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unidep._dependencies_parsing import parse_requirements
from unidep.utils import identify_current_platform, is_pip_installable

if TYPE_CHECKING:
    from unidep._dependencies_parsing import ParsedRequirements
    from unidep.platform_definitions import Platform

try:
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    HAS_TOML = True
except ImportError:
    HAS_TOML = False


def _get_package_name(project_dir: Path) -> str | None:
    """Get the package name from pyproject.toml or setup.py."""
    pyproject_path = project_dir / "pyproject.toml"
    if pyproject_path.exists() and HAS_TOML:
        try:
            with pyproject_path.open("rb") as f:
                data = tomllib.load(f)
                if "project" in data and "name" in data["project"]:
                    # Normalize package name for use in dependencies
                    # Replace dots and hyphens with underscores
                    name = data["project"]["name"]
                    return name.replace("-", "_").replace(".", "_")
        except Exception:  # noqa: S110, BLE001
            pass
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
        conda_deps, pip_deps = _extract_dependencies(req)

        # Use channels and platforms from the requirements file
        if req.channels:
            all_channels.update(req.channels)
        if req.platforms:
            all_platforms.update(req.platforms)

        if conda_deps:
            pixi_data["dependencies"] = conda_deps
        if pip_deps:
            pixi_data["pypi-dependencies"] = pip_deps

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
            conda_deps, pip_deps = _extract_dependencies(req)

            # Collect channels and platforms
            if req.channels:
                all_channels.update(req.channels)
            if req.platforms:
                all_platforms.update(req.platforms)

            feature: dict[str, Any] = {}
            if conda_deps:
                feature["dependencies"] = conda_deps
            if pip_deps:
                feature["pypi-dependencies"] = pip_deps

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
    pixi_data["project"] = {
        "name": project_name or Path.cwd().name,
        "channels": (
            list(all_channels) if all_channels else (channels or ["conda-forge"])
        ),
        "platforms": (
            list(all_platforms)
            if all_platforms
            else (platforms or [identify_current_platform()])
        ),
    }

    # Write the pixi.toml file
    _write_pixi_toml(pixi_data, output_file, verbose=verbose)


def _extract_dependencies(
    requirements: ParsedRequirements,
) -> tuple[dict[str, str], dict[str, str]]:
    """Extract conda and pip dependencies from parsed requirements.

    Returns a tuple of (conda_deps, pip_deps) as simple name->version dicts.
    No conflict resolution - just pass through what's specified.
    """
    conda_deps = {}
    pip_deps = {}

    # Process each package's specifications
    for pkg_name, specs in requirements.requirements.items():
        conda_spec = None
        pip_spec = None

        for spec in specs:
            # Format the version pin or use "*" if no pin
            version = spec.pin.replace(" ", "") if spec.pin else "*"

            # Add platform selector if present
            if spec.selector:
                # In pixi.toml, platform selectors go in target section
                # For now, we'll skip platform-specific deps for simplicity
                # This can be enhanced later if needed
                continue

            if spec.which == "conda":
                # Keep the conda spec, prefer pinned versions
                if conda_spec is None or spec.pin:
                    conda_spec = version
            elif spec.which == "pip" and (pip_spec is None or spec.pin):
                # Keep the pip spec, prefer pinned versions
                pip_spec = version

        # Add to appropriate section
        if conda_spec:
            conda_deps[pkg_name] = conda_spec
        if pip_spec and pkg_name not in conda_deps:  # Only add to pip if not in conda
            pip_deps[pkg_name] = pip_spec

    return conda_deps, pip_deps


def _write_pixi_toml(
    pixi_data: dict[str, Any],
    output_file: str | Path | None,
    *,
    verbose: bool = False,
) -> None:
    """Write the pixi data structure to a TOML file."""
    try:
        import tomli_w
    except ImportError:
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
