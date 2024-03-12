"""unidep - Unified Conda and Pip requirements management.

Pytest plugin for running only tests of changed files.

WARNING: Still experimental and not documented.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from unidep._dependencies_parsing import (
    find_requirements_files,
    parse_local_dependencies,
)

if TYPE_CHECKING:
    import pytest

LOGGER = logging.getLogger(__name__)


def pytest_addoption(parser: pytest.Parser) -> None:  # pragma: no cover
    """Add options to the pytest command line."""
    parser.addoption(
        "--run-affected",
        action="store_true",
        default=False,
        help="Run only tests from affected packages (via `unidep`)",
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
    repo = Repo(repo_root, search_parent_directories=True)
    repo_root = Path(repo.working_tree_dir)  # In case we searched parent directories
    found_files = find_requirements_files(repo_root)
    local_dependencies = parse_local_dependencies(*found_files)
    staged_diffs = repo.head.commit.diff(compare_branch)
    unstaged_diffs = repo.index.diff(None)
    diffs = staged_diffs + unstaged_diffs
    changed_files = [Path(diff.a_path) for diff in diffs]
    affected_packages = _affected_packages(repo_root, changed_files, local_dependencies)
    test_files = [config.cwd_relative_nodeid(i.nodeid).split("::", 1)[0] for i in items]
    run_from_dir = config.invocation_params.dir
    assert all((run_from_dir / item).exists() for item in test_files)
    affected_tests = {
        item
        for item, f in zip(items, test_files)
        if any(f.startswith(str(pkg)) for pkg in affected_packages)
    }
    # Run `pytest -o log_cli=true -o log_cli_level=INFO --run-affected`
    # to see the logging output.
    logging.info(
        "Running affected_tests: %s, changed_files: %s, affected_packages: %s",
        affected_tests,
        changed_files,
        affected_packages,
    )
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
