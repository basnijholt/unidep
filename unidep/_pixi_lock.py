"""unidep - Unified Conda and Pip requirements management.

This module provides the `unidep pixi-lock` CLI command.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

from unidep._dependencies_parsing import find_requirements_files
from unidep._pixi import generate_pixi_toml

if TYPE_CHECKING:
    from pathlib import Path

    from unidep.platform_definitions import Platform


def _check_pixi_installed() -> None:
    """Check if pixi CLI is installed and accessible."""
    if shutil.which("pixi") is None:
        print(
            "❌ pixi is not installed or not found in PATH.\n"
            "Please install it from https://pixi.sh\n"
            "  curl -fsSL https://pixi.sh/install.sh | bash",
        )
        sys.exit(1)


def _run_pixi_lock(
    pixi_toml: Path,
    *,
    verbose: bool = False,
) -> Path:
    """Run `pixi lock` to generate pixi.lock."""
    _check_pixi_installed()

    pixi_lock = pixi_toml.parent / "pixi.lock"
    cmd = ["pixi", "lock", "--manifest-path", str(pixi_toml)]
    if verbose:
        cmd.append("--verbose")

    print(f"🔒 Locking dependencies with `{' '.join(cmd)}`\n")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running pixi lock: {e}")
        sys.exit(1)

    if pixi_lock.exists():
        print(f"✅ Generated lock file at `{pixi_lock}`")
    return pixi_lock


def _convert_to_conda_lock(
    pixi_lock: Path,
    output: Path | None = None,
    *,
    verbose: bool = False,
) -> Path:
    """Convert pixi.lock to conda-lock.yml using pixi-to-conda-lock."""
    try:
        from pixi_to_conda_lock import convert
    except ImportError:
        print(
            "❌ pixi-to-conda-lock is not installed.\n"
            "Please install it with:\n"
            "  pip install pixi-to-conda-lock\n"
            "  # or\n"
            "  pip install unidep[pixi]",
        )
        sys.exit(1)

    if output is None:
        output = pixi_lock.parent / "conda-lock.yml"

    if verbose:
        print(f"🔄 Converting {pixi_lock} to {output}\n")

    try:
        convert(lock_file_path=pixi_lock, conda_lock_path=output)
    except Exception as e:  # noqa: BLE001
        print(f"❌ Error converting to conda-lock: {e}")
        sys.exit(1)

    print(f"✅ Generated conda-lock file at `{output}`")
    return output


def _needs_regeneration(
    pixi_toml: Path,
    requirements_files: list[Path],
) -> bool:
    """Check if pixi.toml needs regeneration based on file modification times."""
    if not pixi_toml.exists():
        return True

    pixi_mtime = pixi_toml.stat().st_mtime
    return any(req_file.stat().st_mtime > pixi_mtime for req_file in requirements_files)


def _needs_lock_regeneration(
    pixi_lock: Path,
    pixi_toml: Path,
) -> bool:
    """Check if pixi.lock needs regeneration."""
    if not pixi_lock.exists():
        return True
    if not pixi_toml.exists():
        return True
    return pixi_toml.stat().st_mtime > pixi_lock.stat().st_mtime


def _generate_pixi_toml_if_needed(
    *,
    pixi_toml: Path,
    found_files: list[Path],
    platforms: list[Platform] | None,
    only_pixi_lock: bool,
    regenerate: bool,
    check_input_hash: bool,
    verbose: bool,
) -> None:
    """Generate pixi.toml if needed."""
    if only_pixi_lock:
        return

    needs_regen = regenerate or _needs_regeneration(pixi_toml, found_files)
    if check_input_hash and not needs_regen:
        if verbose:
            print("⏭️  Skipping pixi.toml generation (up to date)")
    elif needs_regen:
        if verbose:
            n_files = len(found_files)
            print(f"📝 Generating pixi.toml from {n_files} requirements file(s)")
        generate_pixi_toml(
            *found_files,
            platforms=platforms,
            output_file=pixi_toml,
            verbose=verbose,
        )
    elif verbose:
        print(f"⏭️  Using existing pixi.toml at `{pixi_toml}`")


def pixi_lock_command(
    *,
    depth: int,
    directory: Path,
    files: list[Path] | None,
    platforms: list[Platform] | None,
    verbose: bool,
    only_pixi_lock: bool,
    conda_lock: bool,
    regenerate: bool,
    check_input_hash: bool,
    pixi_toml_output: Path | None = None,
    conda_lock_output: Path | None = None,
) -> None:
    """Generate pixi.lock from requirements files.

    Workflow:
    1. Generate pixi.toml (if needed or --regenerate)
    2. Run `pixi lock` to create pixi.lock
    3. Optionally convert to conda-lock.yml (--conda-lock)
    """
    # Find requirements files
    if files:
        found_files = files
        directory = files[0].parent if files[0].is_file() else files[0]
    else:
        found_files = find_requirements_files(directory, depth, verbose=verbose)
        if not found_files:
            print(
                f"❌ No requirements.yaml or pyproject.toml files found in {directory}",
            )
            sys.exit(1)

    # Determine output paths
    pixi_toml = pixi_toml_output or directory / "pixi.toml"
    pixi_lock = pixi_toml.parent / "pixi.lock"

    # Step 1: Generate pixi.toml (if needed)
    _generate_pixi_toml_if_needed(
        pixi_toml=pixi_toml,
        found_files=found_files,
        platforms=platforms,
        only_pixi_lock=only_pixi_lock,
        regenerate=regenerate,
        check_input_hash=check_input_hash,
        verbose=verbose,
    )

    if not pixi_toml.exists():
        print(f"❌ pixi.toml not found at `{pixi_toml}`")
        print("Run without --only-pixi-lock to generate it first.")
        sys.exit(1)

    # Step 2: Run pixi lock
    needs_lock = regenerate or _needs_lock_regeneration(pixi_lock, pixi_toml)
    if check_input_hash and not needs_lock:
        if verbose:
            print("⏭️  Skipping pixi lock (pixi.lock is up to date)")
    else:
        _run_pixi_lock(pixi_toml, verbose=verbose)

    # Step 3: Convert to conda-lock.yml (if requested)
    if conda_lock:
        _convert_to_conda_lock(
            pixi_lock,
            output=conda_lock_output,
            verbose=verbose,
        )

    print("✅ Done!")
