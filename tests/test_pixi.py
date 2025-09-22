"""Tests for simple Pixi.toml generation."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from unidep._pixi import generate_pixi_toml

if TYPE_CHECKING:
    from pathlib import Path


def test_simple_pixi_generation(tmp_path: Path) -> None:
    """Test basic pixi.toml generation from a single requirements.yaml."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy >=1.20
              - pandas
              - pip: requests
            platforms:
              - linux-64
              - osx-arm64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        project_name="test-project",
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()

    # Check basic structure
    assert "[project]" in content
    assert 'name = "test-project"' in content
    assert "conda-forge" in content
    assert "linux-64" in content
    assert "osx-arm64" in content

    # Check dependencies
    assert "[dependencies]" in content
    assert 'numpy = ">=1.20"' in content
    assert 'pandas = "*"' in content

    assert "[pypi-dependencies]" in content
    assert 'requests = "*"' in content


def test_monorepo_pixi_generation(tmp_path: Path) -> None:
    """Test pixi.toml generation with features for multiple requirements files."""
    # Create project1
    project1_dir = tmp_path / "project1"
    project1_dir.mkdir()
    req1 = project1_dir / "requirements.yaml"
    req1.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
              - conda: scipy
            """,
        ),
    )

    # Create project2
    project2_dir = tmp_path / "project2"
    project2_dir.mkdir()
    req2 = project2_dir / "requirements.yaml"
    req2.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pandas
              - pip: requests
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req1,
        req2,
        project_name="monorepo",
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()

    # Check project section
    assert "[project]" in content
    assert 'name = "monorepo"' in content

    # Check feature dependencies (TOML writes them directly without parent section)
    assert "[feature.project1.dependencies]" in content
    assert 'numpy = "*"' in content
    assert 'scipy = "*"' in content

    assert "[feature.project2.dependencies]" in content
    assert 'pandas = "*"' in content

    assert "[feature.project2.pypi-dependencies]" in content
    assert 'requests = "*"' in content

    # Check environments (be flexible with TOML formatting)
    assert "[environments]" in content
    assert "default =" in content
    assert "project1" in content
    assert "project2" in content
    # Verify that default includes both projects
    assert content.count('"project1"') >= 2  # In default and individual env
    assert content.count('"project2"') >= 2  # In default and individual env


def test_pixi_with_version_pins(tmp_path: Path) -> None:
    """Test that version pins are passed through without resolution."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy >=1.20,<2.0
              - conda: scipy =1.9.0
              - pip: requests >2.20
              - sympy >= 1.11
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        output_file=output_file,
        verbose=False,
    )

    content = output_file.read_text()

    # Check that pins are preserved exactly (spaces removed)
    assert 'numpy = ">=1.20,<2.0"' in content
    assert 'scipy = "=1.9.0"' in content
    assert 'requests = ">2.20"' in content
    assert 'sympy = ">=1.11"' in content  # Space should be removed


def test_pixi_with_local_package(tmp_path: Path) -> None:
    """Test that local packages are added as editable dependencies."""
    # Create a directory with requirements.yaml and pyproject.toml
    project_dir = tmp_path / "my_package"
    project_dir.mkdir()

    req_file = project_dir / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            """,
        ),
    )

    # Create a pyproject.toml with build-system to simulate a local package
    pyproject_file = project_dir / "pyproject.toml"
    pyproject_file.write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]

            [project]
            name = "my-package"
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        project_dir,
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()

    # Check that the local package is added as an editable dependency
    # TOML can format this as either inline or table format
    assert "pypi-dependencies" in content
    assert "my_package" in content
    assert 'path = "."' in content
    assert "editable = true" in content
    assert 'numpy = "*"' in content


def test_pixi_empty_dependencies(tmp_path: Path) -> None:
    """Test handling of requirements file with no dependencies."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            platforms:
              - linux-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()

    # Should have project section but no dependencies sections
    assert "[project]" in content
    assert "[dependencies]" not in content
    assert "[pypi-dependencies]" not in content
