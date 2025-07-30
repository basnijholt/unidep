"""Test error cases and special scenarios for PyPI alternatives."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from unidep._dependencies_parsing import (
    parse_requirements,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_local_dependency_wheel_with_pypi_alternative(tmp_path: Path) -> None:
    """Test that wheel files work with PyPI alternatives."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    # Create a dummy wheel file
    wheel_file = tmp_path / "some_package.whl"
    wheel_file.write_text("dummy wheel content")

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../some_package.whl
                  pypi: company-package==1.0.0
            """,
        ),
    )

    # This should work without errors
    requirements = parse_requirements(req_file)
    assert "numpy" in requirements.requirements

    # The wheel should be handled in parse_local_dependencies
    from unidep import parse_local_dependencies

    deps = parse_local_dependencies(req_file, verbose=True)
    assert len(deps) == 1
    assert str(wheel_file) in str(next(iter(deps.values())))


def test_missing_local_dependency_with_pypi_alternative(tmp_path: Path) -> None:
    """Test behavior when local dependency doesn't exist but has PyPI alternative."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../missing_dep
                  pypi: company-missing
            """,
        ),
    )

    # Should not raise when raise_if_missing=False
    from unidep import parse_local_dependencies

    deps = parse_local_dependencies(req_file, raise_if_missing=False)
    assert len(deps) == 0

    # Should raise when raise_if_missing=True
    with pytest.raises(FileNotFoundError):
        parse_local_dependencies(req_file, raise_if_missing=True)


def test_empty_folder_with_pypi_alternative(tmp_path: Path) -> None:
    """Test error when local dependency is an empty folder."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    # Create empty folder
    empty_dep = tmp_path / "empty_dep"
    empty_dep.mkdir(exist_ok=True)

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../empty_dep
                  pypi: company-empty
            """,
        ),
    )

    # Should raise RuntimeError for empty folder
    from unidep import parse_local_dependencies

    with pytest.raises(
        RuntimeError,
        match="is not pip installable because it is an empty folder",
    ):
        parse_local_dependencies(req_file)


def test_empty_git_submodule_with_pypi_alternative(tmp_path: Path) -> None:
    """Test error when local dependency is an empty git submodule."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    # Create a directory that looks like an empty git submodule
    git_submodule = tmp_path / "git_submodule"
    git_submodule.mkdir(exist_ok=True)
    (git_submodule / ".git").write_text("gitdir: ../.git/modules/git_submodule")

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../git_submodule
                  pypi: company-submodule
            """,
        ),
    )

    # Should raise RuntimeError for empty git submodule
    from unidep import parse_local_dependencies

    with pytest.raises(
        RuntimeError,
        match="is not installable by pip because it is an empty Git submodule",
    ):
        parse_local_dependencies(req_file)


def test_non_pip_installable_with_pypi_alternative(tmp_path: Path) -> None:
    """Test error when local dependency is not pip installable."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    # Create a non-pip-installable directory (no setup.py, pyproject.toml, etc.)
    non_pip = tmp_path / "non_pip"
    non_pip.mkdir(exist_ok=True)
    (non_pip / "some_file.txt").write_text("not a python package")

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../non_pip
                  pypi: company-non-pip
            """,
        ),
    )

    # Should raise RuntimeError
    from unidep import parse_local_dependencies

    with pytest.raises(
        RuntimeError,
        match="is not pip installable nor is it managed by unidep",
    ):
        parse_local_dependencies(req_file)


def test_circular_dependencies_with_pypi_alternatives(tmp_path: Path) -> None:
    """Test circular dependencies with PyPI alternatives."""
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True)

    project2 = tmp_path / "project2"
    project2.mkdir(exist_ok=True)

    # project1 depends on project2
    (project1 / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pandas
            local_dependencies:
                - local: ../project2
                  pypi: company-project2
            """,
        ),
    )

    # project2 depends on project1 (circular)
    (project2 / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../project1
                  pypi: company-project1
            """,
        ),
    )

    # Should handle circular dependencies gracefully
    requirements = parse_requirements(
        project1 / "requirements.yaml",
        project2 / "requirements.yaml",
    )
    assert "pandas" in requirements.requirements
    assert "numpy" in requirements.requirements
