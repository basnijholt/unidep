"""Test error cases and special scenarios for PyPI alternatives."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from unidep import parse_local_dependencies
from unidep._dependencies_parsing import parse_requirements
from unidep._setuptools_integration import get_python_dependencies

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

    deps = parse_local_dependencies(req_file, verbose=True)
    assert len(deps) == 1
    # Get the first (and only) list of paths
    paths = next(iter(deps.values()))
    assert len(paths) == 1
    # Compare resolved paths to handle Windows path differences
    assert paths[0].resolve() == wheel_file.resolve()


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


def test_very_long_pypi_alternative_names(tmp_path: Path) -> None:
    """Test handling of very long PyPI package names in alternatives."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    # Create a local dependency
    dep = tmp_path / "dep"
    dep.mkdir(exist_ok=True)
    (dep / "setup.py").write_text(
        'from setuptools import setup; setup(name="dep", version="1.0")',
    )
    (dep / "dep").mkdir(exist_ok=True)
    (dep / "dep" / "__init__.py").write_text("")

    # Very long PyPI alternative name
    long_name = "company-" + "x" * 200 + "-package>=1.0.0"

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            f"""\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../dep
                  pypi: {long_name}
            """,
        ),
    )

    # Should handle long names without issues

    # Test with local path existing - should use file:// URL
    deps = get_python_dependencies(
        req_file,
        include_local_dependencies=True,
    )
    assert "numpy" in deps.dependencies
    assert any("dep @ file://" in d for d in deps.dependencies)

    # Test with local path missing - should use PyPI alternative
    import shutil

    shutil.rmtree(dep)

    deps = get_python_dependencies(
        req_file,
        include_local_dependencies=True,
    )
    assert "numpy" in deps.dependencies
    assert long_name in deps.dependencies


def test_special_characters_in_paths(tmp_path: Path) -> None:
    """Test handling of special characters in local dependency paths."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    # Create a dependency with special characters in name
    special_dir = tmp_path / "dep with spaces & special-chars"
    special_dir.mkdir(exist_ok=True)
    (special_dir / "setup.py").write_text(
        'from setuptools import setup; setup(name="special-dep", version="1.0")',
    )
    (special_dir / "special_dep").mkdir(exist_ok=True)
    (special_dir / "special_dep" / "__init__.py").write_text("")

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: "../dep with spaces & special-chars"
                  pypi: company-special-dep
            """,
        ),
    )

    # Should handle special characters correctly

    # With local path existing - should use file:// URL
    deps = get_python_dependencies(
        req_file,
        include_local_dependencies=True,
    )
    assert "numpy" in deps.dependencies
    assert any("special-dep @ file://" in d for d in deps.dependencies)
    assert not any("company-special-dep" in d for d in deps.dependencies)


def test_symlink_local_dependencies(tmp_path: Path) -> None:
    """Test handling of symlinked local dependencies."""
    import os

    # Skip on Windows where symlinks require admin privileges
    if os.name == "nt":
        pytest.skip("Symlink test skipped on Windows")

    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    # Create actual dependency
    actual_dep = tmp_path / "actual_dep"
    actual_dep.mkdir(exist_ok=True)
    (actual_dep / "setup.py").write_text(
        'from setuptools import setup; setup(name="actual", version="1.0")',
    )
    (actual_dep / "actual").mkdir(exist_ok=True)
    (actual_dep / "actual" / "__init__.py").write_text("")

    # Create symlink
    symlink_dep = tmp_path / "symlink_dep"
    symlink_dep.symlink_to(actual_dep)

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../symlink_dep
                  pypi: company-symlink-dep
            """,
        ),
    )

    # Should resolve symlinks correctly

    # With symlink existing - should use file:// URL
    deps = get_python_dependencies(
        req_file,
        include_local_dependencies=True,
    )
    assert "numpy" in deps.dependencies
    assert any("actual @ file://" in d for d in deps.dependencies)
    assert not any("company-symlink-dep" in d for d in deps.dependencies)
