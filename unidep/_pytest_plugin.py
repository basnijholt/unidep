"""requirements.yaml - Unified Conda and Pip requirements management.

This module provides a command-line tool for managing conda environment.yaml files.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from unidep._yaml_parsing import find_requirements_files, parse_project_dependencies

if TYPE_CHECKING:
    import pytest


def pytest_addoption(parser: pytest.Parser) -> None:  # pragma: no cover
    """Add options to the pytest command line."""
    parser.addoption(
        "--run-affected",
        action="store_true",
        default=False,
        help="Run only tests from affected packages",
    )
    parser.addoption(
        "--branch",
        action="store",
        default="origin/main",
        help="Branch to compare with for finding affected tests",
    )
    parser.addoption(
        "--repo-root",
        action="store",
        default=".",
        type=Path,
        help="Root of the repository",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:  # pragma: no cover
    """Filter tests based on the --run-affected option."""
    if not config.getoption("--run-affected"):
        return
    try:
        from git import Repo
    except ImportError:
        print(
            "🛑 You need to install `gitpython` to use the `--run-affected` option."
            "run `pip install gitpython` to install it.",
        )
        sys.exit(1)

    compare_branch = config.getoption("--branch")
    repo_root = Path(config.getoption("--repo-root")).absolute()
    repo = Repo(repo_root)
    found_files = find_requirements_files(repo_root)
    local_dependencies = parse_project_dependencies(*found_files)
    diffs = repo.head.commit.diff(compare_branch)
    changed_files = [Path(diff.a_path) for diff in diffs]
    affected_packages = _affected_packages(repo_root, changed_files, local_dependencies)
    affected_tests = {
        item
        for item in items
        if any(item.nodeid.startswith(str(pkg)) for pkg in affected_packages)
    }
    items[:] = list(affected_tests)


def _file_in_folder(file: Path, folder: Path) -> bool:  # pragma: no cover
    file = file.absolute()
    folder = folder.absolute()
    common = os.path.commonpath([folder, file])
    return os.path.commonpath([folder]) == common


def _affected_packages(
    repo_root: Path,
    changed_files: list[Path],
    dependencies: dict[Path, list[Path]],
    *,
    verbose: bool = False,
) -> set[Path]:  # pragma: no cover
    affected_packages = set()
    for file in changed_files:
        for package, deps in dependencies.items():
            if _file_in_folder(repo_root / file, package):
                if verbose:
                    print(f"File {file} affects package {package}")
                affected_packages.add(package)
                affected_packages.update(deps)
    return {pkg.relative_to(repo_root) for pkg in affected_packages}
