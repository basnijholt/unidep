#!/usr/bin/env python3
"""Convert a pixi.lock file to a conda-lock.yml file using repodata.

This script reads a pixi.lock file and generates a conda-lock.yml file with the same
package information, using repodata to extract accurate package metadata.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml


def read_yaml_file(file_path: Path) -> dict[str, Any]:
    """Read a YAML file and return its contents as a dictionary."""
    with open(file_path) as f:  # noqa: PTH123
        return yaml.safe_load(f)


def write_yaml_file(file_path: Path, data: dict[str, Any]) -> None:
    """Write data to a YAML file."""
    with open(file_path, "w") as f:  # noqa: PTH123
        yaml.dump(data, f, sort_keys=False)


def find_repodata_cache_dir() -> Path | None:
    """Find the repodata cache directory based on common locations."""
    # Try to find the cache directory in common locations
    possible_paths = [
        Path.home() / "Library" / "Caches" / "rattler" / "cache" / "repodata",  # macOS
        Path.home() / ".cache" / "rattler" / "cache" / "repodata",  # Linux
        Path.home() / "AppData" / "Local" / "rattler" / "cache" / "repodata",  # Windows
    ]

    for path in possible_paths:
        if path.exists() and path.is_dir():
            return path

    return None


def load_json_file(file_path: Path) -> dict[str, Any]:
    """Load a JSON file and return its contents as a dictionary."""
    with open(file_path) as f:  # noqa: PTH123
        return json.load(f)


def load_repodata_files(repodata_dir: Path) -> dict[str, dict[str, Any]]:
    """Load all repodata files from the cache directory."""
    repodata = {}

    # Load all .json files (not .info.json)
    for file_path in repodata_dir.glob("*.json"):
        if not file_path.name.endswith(".info.json"):
            try:
                data = load_json_file(file_path)
                repodata[file_path.stem] = data
            except Exception as e:  # noqa: BLE001
                print(f"Warning: Failed to load {file_path}: {e}")

    return repodata


def extract_filename_from_url(url: str) -> str:
    """Extract the filename from a URL."""
    return url.split("/")[-1]


def find_package_in_repodata(
    repodata: dict[str, dict[str, Any]],
    package_url: str,
) -> dict[str, Any] | None:
    """Find a package in repodata based on its URL."""
    filename = extract_filename_from_url(package_url)

    # Check all repodata files
    for repo_data in repodata.values():
        # Check in packages
        if "packages" in repo_data and filename in repo_data["packages"]:
            return repo_data["packages"][filename]

        # Check in packages.conda (for newer conda formats)
        if "packages.conda" in repo_data and filename in repo_data["packages.conda"]:
            return repo_data["packages.conda"][filename]

    return None


def extract_platform_from_url(url: str) -> str:  # noqa: PLR0911
    """Extract platform information from a conda package URL."""
    if "/noarch/" in url:
        return "noarch"
    if "/osx-arm64/" in url:
        return "osx-arm64"
    if "/osx-64/" in url:
        return "osx-64"
    if "/linux-64/" in url:
        return "linux-64"
    if "/linux-aarch64/" in url:
        return "linux-aarch64"
    if "/win-64/" in url:
        return "win-64"
    # Default fallback
    return "unknown"


def extract_name_version_from_url(url: str) -> tuple[str, str]:
    """Extract package name and version from a conda package URL as a fallback."""
    filename = extract_filename_from_url(url)

    # Remove file extension (.conda or .tar.bz2)
    if filename.endswith(".conda"):
        filename_no_ext = filename[:-6]
    elif filename.endswith(".tar.bz2"):
        filename_no_ext = filename[:-8]
    else:
        filename_no_ext = filename

    # Split by hyphens to separate name, version, and build
    parts = filename_no_ext.split("-")

    # For simplicity in the fallback, assume the first part is the name
    # and the second part is the version
    name = parts[0]
    version = parts[1] if len(parts) > 1 else ""

    return name, version


def parse_dependencies_from_repodata(depends_list: list[str]) -> dict[str, str]:
    """Parse dependencies from repodata format to conda-lock format."""
    dependencies = {}
    for dep in depends_list:
        parts = dep.split()
        if len(parts) > 1:
            dependencies[parts[0]] = " ".join(parts[1:])
        else:
            dependencies[dep] = ""
    return dependencies


def create_conda_package_entry(
    url: str,
    repodata_info: dict[str, Any],
) -> dict[str, Any]:
    """Create a conda package entry for conda-lock.yml from repodata."""
    platform = extract_platform_from_url(url)

    package_entry = {
        "name": repodata_info["name"],
        "version": repodata_info["version"],
        "manager": "conda",
        "platform": platform,
        "dependencies": parse_dependencies_from_repodata(
            repodata_info.get("depends", []),
        ),
        "url": url,
        "hash": {
            "md5": repodata_info.get("md5", ""),
            "sha256": repodata_info.get("sha256", ""),
        },
        "category": "main",
        "optional": False,
    }

    # Add build information if available
    if "build" in repodata_info:
        package_entry["build"] = repodata_info["build"]

    # Add build number if available
    if "build_number" in repodata_info:
        package_entry["build_number"] = repodata_info["build_number"]

    return package_entry


def create_conda_package_entry_fallback(
    url: str,
    package_info: dict[str, Any],
) -> dict[str, Any]:
    """Create a conda package entry for conda-lock.yml using URL parsing as fallback."""
    platform = extract_platform_from_url(url)
    name, version = extract_name_version_from_url(url)

    return {
        "name": name,
        "version": version,
        "manager": "conda",
        "platform": platform,
        "dependencies": dict(package_info.get("depends", {}).items()),
        "url": url,
        "hash": {
            "md5": package_info.get("md5", ""),
            "sha256": package_info.get("sha256", ""),
        },
        "category": "main",
        "optional": False,
    }


def create_pypi_package_entry(
    platform: str,
    package_info: dict[str, Any],
) -> dict[str, Any]:
    """Create a PyPI package entry for conda-lock.yml."""
    url = package_info["pypi"]

    return {
        "name": package_info.get("name", ""),
        "version": package_info.get("version", ""),
        "manager": "pip",
        "platform": platform,
        "dependencies": {},  # PyPI dependencies are handled differently
        "url": url,
        "hash": {
            "sha256": package_info.get("sha256", ""),
        },
        "category": "main",
        "optional": False,
    }


def extract_platforms_from_pixi(pixi_data: Any) -> list[str]:
    """Extract platform information from pixi.lock data."""
    platforms = []
    for env_data in pixi_data.get("environments", {}).values():
        for platform in env_data.get("packages", {}):
            if platform not in platforms and platform != "noarch":
                platforms.append(platform)
    return platforms


def extract_channels_from_pixi(pixi_data: dict[str, Any]) -> list[dict[str, str]]:
    """Extract channel information from pixi.lock data."""
    return [
        {"url": channel["url"].replace("https://conda.anaconda.org/", "")}
        for channel in pixi_data.get("environments", {})
        .get("default", {})
        .get("channels", [])
    ]


def create_conda_lock_metadata(
    platforms: list[str],
    channels: list[dict[str, str]],
) -> dict[str, Any]:
    """Create metadata section for conda-lock.yml."""
    return {
        "content_hash": {
            platform: "generated-from-pixi-lock" for platform in platforms
        },
        "channels": channels,
        "platforms": platforms,
        "sources": ["converted-from-pixi.lock"],
    }


def process_conda_packages(
    pixi_data: dict[str, Any],
    repodata: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Process conda packages from pixi.lock and convert to conda-lock format."""
    package_entries = []

    for package_info in pixi_data.get("packages", []):
        if "conda:" in package_info:
            url = package_info["conda"]

            # Try to find package in repodata
            repodata_info = find_package_in_repodata(repodata, url)

            if repodata_info:
                # Use the information from repodata
                package_entry = create_conda_package_entry(
                    url,
                    repodata_info,
                )
            else:
                # Fallback to parsing the URL if repodata doesn't have the package
                package_entry = create_conda_package_entry_fallback(url, package_info)

            package_entries.append(package_entry)

    return package_entries


def process_pypi_packages(
    pixi_data: dict[str, Any],
    platforms: list[str],
) -> list[dict[str, Any]]:
    """Process PyPI packages from pixi.lock and convert to conda-lock format."""
    package_entries = []

    for package_info in pixi_data.get("packages", []):
        if "pypi:" in package_info:
            for platform in platforms:
                package_entry = create_pypi_package_entry(platform, package_info)
                package_entries.append(package_entry)

    return package_entries


def convert_pixi_to_conda_lock(
    pixi_data: dict[str, Any],
    repodata: dict[str, Any],
) -> dict[str, Any]:
    """Convert pixi.lock data structure to conda-lock.yml format using repodata."""
    # Extract platforms and channels
    platforms = extract_platforms_from_pixi(pixi_data)
    channels = extract_channels_from_pixi(pixi_data)

    # Create basic conda-lock structure
    conda_lock_data = {
        "version": 1,
        "metadata": create_conda_lock_metadata(platforms, channels),
        "package": [],
    }

    # Process conda packages
    conda_packages = process_conda_packages(pixi_data, repodata)
    conda_lock_data["package"].extend(conda_packages)  # type: ignore[attr-defined]

    # Process PyPI packages
    pypi_packages = process_pypi_packages(pixi_data, platforms)
    conda_lock_data["package"].extend(pypi_packages)  # type: ignore[attr-defined]

    return conda_lock_data


def main() -> int:
    """Main function to convert pixi.lock to conda-lock.yml."""
    parser = argparse.ArgumentParser(description="Convert pixi.lock to conda-lock.yml")
    parser.add_argument("pixi_lock", type=Path, help="Path to pixi.lock file")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("conda-lock.yml"),
        help="Output conda-lock.yml file path",
    )
    parser.add_argument(
        "--repodata-dir",
        type=Path,
        help="Path to repodata cache directory",
    )

    args = parser.parse_args()

    if not args.pixi_lock.exists():
        print(f"Error: {args.pixi_lock} does not exist")
        return 1

    # Find repodata cache directory
    repodata_dir = args.repodata_dir
    if repodata_dir is None:
        repodata_dir = find_repodata_cache_dir()
        if repodata_dir is None:
            print(
                "Warning: Could not find repodata cache directory. Using fallback URL parsing.",  # noqa: E501
            )
            repodata = {}
        else:
            print(f"Using repodata from: {repodata_dir}")
            repodata = load_repodata_files(repodata_dir)
    else:
        if not repodata_dir.exists():
            print(f"Error: Specified repodata directory {repodata_dir} does not exist")
            return 1
        repodata = load_repodata_files(repodata_dir)

    pixi_data = read_yaml_file(args.pixi_lock)
    conda_lock_data = convert_pixi_to_conda_lock(pixi_data, repodata)
    write_yaml_file(args.output, conda_lock_data)

    print(f"Successfully converted {args.pixi_lock} to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
