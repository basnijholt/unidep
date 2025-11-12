"""Integration tests for PyPI alternatives in local dependencies."""

from __future__ import annotations

import shutil
import textwrap
from typing import TYPE_CHECKING

from unidep._setuptools_integration import get_python_dependencies

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_build_with_pypi_alternatives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that building a wheel uses PyPI alternatives when local paths don't exist."""
    # Create main project
    project = tmp_path / "main_project"
    project.mkdir(exist_ok=True)

    # Create local dependency
    local_dep = tmp_path / "local_dep"
    local_dep.mkdir(exist_ok=True)
    (local_dep / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools", "unidep"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "local-dep"
            version = "0.1.0"

            [tool.unidep]
            dependencies = ["requests"]
            """,
        ),
    )
    (local_dep / "local_dep.py").write_text("# Local dependency module")

    # Create main project with PyPI alternative
    (project / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools", "unidep"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "main-project"
            version = "0.1.0"
            dynamic = ["dependencies"]

            [tool.unidep]
            dependencies = ["numpy"]
            local_dependencies = [
                {local = "../local_dep", pypi = "company-local-dep==1.0.0"}
            ]
            """,
        ),
    )
    (project / "main_project.py").write_text("# Main project module")

    # Change to project directory
    monkeypatch.chdir(project)

    # Test 1: Normal development with local paths existing - should use file:// URLs

    deps = get_python_dependencies(
        project / "pyproject.toml",
        include_local_dependencies=True,
    )

    assert "numpy" in deps.dependencies
    # Should use file:// URL since local path exists
    assert any("local-dep @ file://" in dep for dep in deps.dependencies)
    assert not any("company-local-dep" in dep for dep in deps.dependencies)

    # Test 2: Simulate wheel build where local paths don't exist
    # Move the local dependency to simulate it not being available

    local_dep_backup = tmp_path / "local_dep_backup"
    shutil.move(str(local_dep), str(local_dep_backup))

    deps = get_python_dependencies(
        project / "pyproject.toml",
        include_local_dependencies=True,
    )

    assert "numpy" in deps.dependencies
    # Should use PyPI alternative since local path doesn't exist
    assert "company-local-dep==1.0.0" in deps.dependencies
    assert not any("file://" in dep for dep in deps.dependencies)


def test_mixed_local_deps_with_and_without_pypi(tmp_path: Path) -> None:
    """Test project with some local deps having PyPI alternatives and some not."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    # Create local dependencies
    for name in ["dep1", "dep2", "dep3"]:
        dep_dir = tmp_path / name
        dep_dir.mkdir(exist_ok=True)
        (dep_dir / "setup.py").write_text(
            f'from setuptools import setup; setup(name="{name}", version="0.1.0")',
        )

    # Create requirements.yaml with mixed format
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pandas
            local_dependencies:
                - ../dep1  # No PyPI alternative
                - local: ../dep2
                  pypi: company-dep2>=2.0
                - local: ../dep3
                  pypi: company-dep3~=3.0
            """,
        ),
    )

    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=True,
    )

    assert "pandas" in deps.dependencies
    # All should use file:// since local paths exist
    assert any("dep1 @ file://" in dep for dep in deps.dependencies)
    assert any("dep2 @ file://" in dep for dep in deps.dependencies)
    assert any("dep3 @ file://" in dep for dep in deps.dependencies)
    # Should NOT use PyPI alternatives when local exists
    assert not any("company-dep2" in dep for dep in deps.dependencies)
    assert not any("company-dep3" in dep for dep in deps.dependencies)


def test_setuptools_with_skip_local_deps_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that UNIDEP_SKIP_LOCAL_DEPS environment variable behavior."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    # Create local dependency
    dep = tmp_path / "dep"
    dep.mkdir(exist_ok=True)
    (dep / "setup.py").write_text(
        'from setuptools import setup; setup(name="my-dep", version="0.1.0")',
    )

    # Create project with local dependency (no PyPI alternative)
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - ../dep  # No PyPI alternative
            """,
        ),
    )

    # Test without UNIDEP_SKIP_LOCAL_DEPS

    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=True,
    )

    assert "numpy" in deps.dependencies
    assert any("my-dep @ file://" in dep for dep in deps.dependencies)

    # Test with UNIDEP_SKIP_LOCAL_DEPS=1
    monkeypatch.setenv("UNIDEP_SKIP_LOCAL_DEPS", "1")

    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=False,  # This would be set by _deps()
    )

    assert "numpy" in deps.dependencies
    # Should not include local dependency
    assert not any("my-dep" in dep for dep in deps.dependencies)
    assert not any("file://" in dep for dep in deps.dependencies)


def test_use_skip_entries_are_ignored(tmp_path: Path) -> None:
    """Entries marked `use: skip` should never contribute dependencies."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    skip_dep = tmp_path / "skip_dep"
    skip_dep.mkdir(exist_ok=True)
    (skip_dep / "setup.py").write_text(
        'from setuptools import setup; setup(name="skip-dep", version="0.1.0")',
    )
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../skip_dep
                  use: skip
            """,
        ),
    )

    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=True,
    )

    assert "numpy" in deps.dependencies
    assert not any("skip-dep" in dep for dep in deps.dependencies)
    assert not any("file://" in dep for dep in deps.dependencies)


def test_use_pypi_entries_not_readded(tmp_path: Path) -> None:
    """Entries marked `use: pypi` rely solely on their PyPI alternative."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    local_dep = tmp_path / "pypi_dep"
    local_dep.mkdir(exist_ok=True)
    (local_dep / "setup.py").write_text(
        'from setuptools import setup; setup(name="pypi-dep", version="0.1.0")',
    )
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../pypi_dep
                  use: pypi
                  pypi: company-pypi-dep==2.0
            """,
        ),
    )

    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=True,
    )

    assert "numpy" in deps.dependencies
    assert any(
        dep.replace(" ", "") == "company-pypi-dep==2.0" for dep in deps.dependencies
    )
    assert not any("pypi-dep @ file://" in dep for dep in deps.dependencies)
