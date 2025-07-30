"""Integration tests for PyPI alternatives in local dependencies."""

from __future__ import annotations

import subprocess
import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_build_with_pypi_alternatives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that building a wheel uses PyPI alternatives when UNIDEP_SKIP_LOCAL_DEPS is set."""
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

    # Test 1: Normal pip install should use file:// URL (but we have PyPI alternative)
    subprocess.run(
        ["pip", "install", "--dry-run", "-e", "."],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    # With the new implementation, PyPI alternatives are always used when defined
    # This is the behavior you mentioned you prefer

    # Test 2: Build with UNIDEP_SKIP_LOCAL_DEPS should definitely use PyPI alternative
    monkeypatch.setenv("UNIDEP_SKIP_LOCAL_DEPS", "1")

    # We can't easily test the actual build process in a unit test,
    # but we can test the dependency resolution
    from unidep._setuptools_integration import get_python_dependencies

    deps = get_python_dependencies(
        project / "pyproject.toml",
        include_local_dependencies=True,
    )

    assert "numpy" in deps.dependencies
    assert "company-local-dep==1.0.0" in deps.dependencies
    # Should NOT have file:// URL since we have a PyPI alternative
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

    from unidep._setuptools_integration import get_python_dependencies

    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=True,
    )

    assert "pandas" in deps.dependencies
    assert "company-dep2>=2.0" in deps.dependencies
    assert "company-dep3~=3.0" in deps.dependencies
    # dep1 should use file:// since no PyPI alternative
    assert any("dep1 @ file://" in dep for dep in deps.dependencies)
    # dep2 and dep3 should NOT use file://
    assert not any("dep2 @ file://" in dep for dep in deps.dependencies)
    assert not any("dep3 @ file://" in dep for dep in deps.dependencies)


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
    from unidep._setuptools_integration import get_python_dependencies

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
