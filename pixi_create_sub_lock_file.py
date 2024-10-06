"""Create a subset of a lock file with a subset of packages."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections import defaultdict

from rattler import (
    Environment,
    GenericVirtualPackage,
    LockFile,
    Platform,
    Version,
    solve_with_sparse_repodata,
)
from rattler.channel import Channel, ChannelConfig
from rattler.match_spec import MatchSpec
from rattler.repo_data import SparseRepoData


def create_repodata_from_pixi_lock(lock_file_path: str) -> dict[str, dict]:
    """Create repodata from a pixi lock file."""
    lock_file = LockFile.from_path(lock_file_path)
    env = lock_file.default_environment()
    repodata = {}
    for platform in env.platforms():
        subdir = str(platform)
        packages = env.conda_repodata_records_for_platform(platform)
        if not packages:
            continue

        repodata[subdir] = {
            "info": {
                "subdir": subdir,
                "base_url": f"https://conda.anaconda.org/conda-forge/{subdir}",
            },
            "packages": {
                f"{pkg.name.normalized}-{pkg.version}-{pkg.build}.conda": {
                    "build": pkg.build,
                    "build_number": pkg.build_number,
                    "depends": pkg.depends,
                    "constrains": pkg.constrains,
                    "license": pkg.license,
                    "license_family": pkg.license_family,
                    "md5": pkg.md5.hex() if pkg.md5 else None,
                    "name": pkg.name.normalized,
                    "sha256": pkg.sha256.hex() if pkg.sha256 else None,
                    "size": pkg.size,
                    "subdir": pkg.subdir,
                    "timestamp": int(pkg.timestamp.timestamp() * 1000)
                    if pkg.timestamp
                    else None,
                    "version": str(pkg.version),
                }
                for pkg in packages
            },
            "repodata_version": 2,
        }
    return repodata


def _version_requirement_to_lowest_version(version: str | None) -> str | None:
    if version is None:
        return None
    if version.startswith(">="):
        version = version[2:]
    if version.startswith("=="):
        version = version[2:]
    version = version.split(",")[0]
    return version  # noqa: RET504


def all_virtual_packages(env: Environment) -> dict[Platform, set[str]]:
    """Get all virtual packages from an environment."""
    virtual_packages = defaultdict(set)
    for platform, packages in env.packages_by_platform().items():
        for package in packages:
            if not package.is_conda:
                continue
            repo_record = package.as_conda()
            for dep in repo_record.depends:
                spec = MatchSpec(dep)
                if spec.name.normalized.startswith("__"):
                    version = _version_requirement_to_lowest_version(spec.version)
                    virtual_package = GenericVirtualPackage(
                        spec.name,
                        version=Version(version or "0"),
                        build_string=spec.build or "*",
                    )
                    virtual_packages[platform].add(virtual_package)
    return virtual_packages


async def create_subset_lock_file(
    original_lock_file_path: str,
    required_packages: list[str],
    platform: Platform,
) -> LockFile:
    """Create a new lock file with a subset of packages from original lock file."""
    original_lock_file = LockFile.from_path(original_lock_file_path)
    env = original_lock_file.default_environment()
    conda_records = env.conda_repodata_records_for_platform(platform)
    if conda_records is None:
        msg = f"No conda records found for platform {platform}"
        raise ValueError(msg)
    repodata = create_repodata_from_pixi_lock(original_lock_file_path)
    platform_repodata = repodata.get(str(platform))
    if platform_repodata is None:
        msg = f"No repodata found for platform {platform}"
        raise ValueError(msg)

    with tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        suffix=".json",
    ) as temp_file:
        json.dump(platform_repodata, temp_file)
        temp_file_path = temp_file.name
    print(f"Temporary repodata file: {temp_file_path}")
    dummy_channel = Channel("dummy", ChannelConfig())
    sparse_repo_data = SparseRepoData(dummy_channel, str(platform), temp_file_path)
    specs = [MatchSpec(pkg) for pkg in required_packages]
    virtual_packages = all_virtual_packages(env)[platform]

    solved_records = await solve_with_sparse_repodata(
        specs=specs,
        sparse_repodata=[sparse_repo_data],
        locked_packages=conda_records,
        virtual_packages=virtual_packages,
    )
    new_env = Environment("new_env", {platform: solved_records})
    new_lock_file = LockFile({"new_env": new_env})
    os.unlink(temp_file_path)  # noqa: PTH108
    return new_lock_file


async def main() -> None:
    """Example usage of create_subset_lock_file."""
    original_lock_file_path = "pixi.lock"
    required_packages = ["tornado", "scipy", "ipykernel", "adaptive"]
    platform = Platform("linux-64")
    new_lock_file = await create_subset_lock_file(
        original_lock_file_path,
        required_packages,
        platform,
    )
    new_lock_file.to_path("new_lock_file.lock")


if __name__ == "__main__":
    asyncio.run(main())
