"""Tests for simple Pixi.toml generation."""

from __future__ import annotations

import builtins
import shutil
import subprocess
import sys
import textwrap
import time
import types
from typing import TYPE_CHECKING

import pytest

from unidep._pixi import generate_pixi_toml
from unidep._pixi_lock import (
    _check_pixi_installed,
    _convert_to_conda_lock,
    _needs_lock_regeneration,
    _needs_regeneration,
    _run_pixi_lock,
    pixi_lock_command,
)

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


def test_pixi_with_platform_selectors(tmp_path: Path) -> None:
    """Test that platform selectors are converted to target sections."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
              - cuda-toolkit =11.8  # [linux64]
              - pip: pyobjc  # [osx]
            platforms:
              - linux-64
              - osx-arm64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        project_name="test-selectors",
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()

    # Check universal dependencies
    assert "[dependencies]" in content
    assert 'numpy = "*"' in content

    # Check platform-specific conda dependency
    assert "[target.linux-64.dependencies]" in content
    assert 'cuda-toolkit = "=11.8"' in content

    # Check platform-specific pip dependency (osx maps to osx-64 and osx-arm64)
    assert "pypi-dependencies" in content
    assert "pyobjc" in content
    # Should be in at least one osx target
    assert "osx-64" in content or "osx-arm64" in content


def test_pixi_with_multiple_platform_selectors(tmp_path: Path) -> None:
    """Test that broad selectors like 'unix' expand to multiple platforms."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
              - readline  # [unix]
              - pywin32  # [win64]
            platforms:
              - linux-64
              - osx-arm64
              - win-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        project_name="test-multi-platform",
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()

    # Universal dep
    assert 'numpy = "*"' in content

    # unix selector should expand to linux and osx platforms
    assert "linux-64" in content
    assert "osx-arm64" in content or "osx-64" in content
    assert "readline" in content

    # win64 selector
    assert "win-64" in content
    assert "pywin32" in content


def test_pixi_monorepo_with_platform_selectors(tmp_path: Path) -> None:
    """Test platform selectors in monorepo mode (multiple files)."""
    # Create project1 with linux-specific dep
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
              - cuda-toolkit  # [linux64]
            platforms:
              - linux-64
              - osx-arm64
            """,
        ),
    )

    # Create project2 with osx-specific dep
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
              - pip: pyobjc  # [arm64]
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req1,
        req2,
        project_name="monorepo-selectors",
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()

    # Check that features have target sections
    assert "[feature.project1.dependencies]" in content
    assert 'numpy = "*"' in content

    # Platform-specific in feature should use target within feature
    assert "cuda-toolkit" in content
    assert "linux-64" in content

    assert "[feature.project2.dependencies]" in content
    assert 'pandas = "*"' in content
    assert "pyobjc" in content
    assert "osx-arm64" in content


def test_pixi_monorepo_with_local_packages(tmp_path: Path) -> None:
    """Test that local packages in monorepo are added as editable dependencies."""
    # Create project1 with pyproject.toml
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
            """,
        ),
    )
    pyproject1 = project1_dir / "pyproject.toml"
    pyproject1.write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]

            [project]
            name = "project-one"
            """,
        ),
    )

    # Create project2 with pyproject.toml
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
            """,
        ),
    )
    pyproject2 = project2_dir / "pyproject.toml"
    pyproject2.write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]

            [project]
            name = "project-two"
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req1,
        req2,
        project_name="monorepo-local",
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()

    # Check that local packages are added as editable dependencies
    assert "[feature.project1.pypi-dependencies.project_one]" in content
    assert "[feature.project2.pypi-dependencies.project_two]" in content
    assert 'path = "./project1"' in content
    assert 'path = "./project2"' in content
    assert "editable = true" in content


def test_pixi_with_directory_input(tmp_path: Path) -> None:
    """Test passing a directory instead of a file."""
    # Create a directory with requirements.yaml
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    req_file = project_dir / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    # Pass directory instead of file
    generate_pixi_toml(
        project_dir,
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()
    assert 'numpy = "*"' in content


def test_pixi_verbose_output(tmp_path: Path, capsys: object) -> None:
    """Test verbose output mode."""
    req_file = tmp_path / "requirements.yaml"
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

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        output_file=output_file,
        verbose=True,
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "Generated pixi.toml" in captured.out


def test_pixi_fallback_package_name(tmp_path: Path) -> None:
    """Test fallback to directory name when pyproject.toml has no project.name."""
    project_dir = tmp_path / "my_fallback_pkg"
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

    # Create a pyproject.toml WITHOUT project.name
    pyproject_file = project_dir / "pyproject.toml"
    pyproject_file.write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        project_dir,
        output_file=output_file,
        verbose=False,
    )

    content = output_file.read_text()
    # Should fallback to directory name
    assert "my_fallback_pkg" in content


def test_pixi_filtering_removes_empty_targets(tmp_path: Path) -> None:
    """Test that filtering removes targets entirely when no platforms match."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
              - cuda-toolkit  # [linux64]
            platforms:
              - osx-arm64
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
    # cuda-toolkit should be filtered out since linux-64 is not in platforms
    assert "cuda-toolkit" not in content
    # target section should not exist
    assert "[target." not in content


def test_pixi_stdout_output(tmp_path: Path, capsys: object) -> None:
    """Test output to stdout when output_file is None."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
        ),
    )

    generate_pixi_toml(
        req_file,
        output_file=None,
        verbose=False,
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert 'numpy = "*"' in captured.out
    assert "[project]" in captured.out


def test_pixi_monorepo_with_directory_input(tmp_path: Path) -> None:
    """Test monorepo mode passing directories instead of files."""
    # Create project1 directory
    project1_dir = tmp_path / "proj1"
    project1_dir.mkdir()
    req1 = project1_dir / "requirements.yaml"
    req1.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            """,
        ),
    )

    # Create project2 directory
    project2_dir = tmp_path / "proj2"
    project2_dir.mkdir()
    req2 = project2_dir / "requirements.yaml"
    req2.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pandas
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    # Pass directories instead of files
    generate_pixi_toml(
        project1_dir,
        project2_dir,
        project_name="monorepo-dirs",
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()
    # Feature names should be derived from directory names
    assert "[feature.proj1.dependencies]" in content
    assert "[feature.proj2.dependencies]" in content


def test_pixi_monorepo_filtering_removes_empty_feature_targets(tmp_path: Path) -> None:
    """Test that filtering removes empty feature targets in monorepo mode."""
    # Create project1 with platform-specific dep that won't match
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
              - cuda-toolkit  # [linux64]
            platforms:
              - osx-arm64
            """,
        ),
    )

    # Create project2 with no platform deps
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
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req1,
        req2,
        project_name="monorepo-filter",
        output_file=output_file,
        verbose=False,
    )

    content = output_file.read_text()
    # cuda-toolkit should be filtered out
    assert "cuda-toolkit" not in content
    # Feature should exist but without target section
    assert "[feature.project1.dependencies]" in content
    assert "[feature.project1.target" not in content


def test_pixi_default_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that generate_pixi_toml uses cwd when no args provided."""
    # Create requirements.yaml in tmp_path
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
        ),
    )

    # Change to tmp_path directory
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]

    output_file = tmp_path / "pixi.toml"
    # Call with no requirements_files argument
    generate_pixi_toml(
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()
    assert 'numpy = "*"' in content


def test_pixi_optional_dependencies_single_file(tmp_path: Path) -> None:
    """Test optional dependencies with realistic user scenario.

    A typical user would have a requirements.yaml with:
    - Main dependencies
    - Multiple optional dependency groups (dev, docs)
    - Version pins, pip packages, and platform selectors in optional deps
    """
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy >=1.20
            optional_dependencies:
              dev:
                - pytest >=7.0
                - pip: black
                - pexpect  # [unix]
                - wexpect  # [win64]
              docs:
                - sphinx
                - sphinx-rtd-theme
            platforms:
              - linux-64
              - win-64
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

    # Check main dependencies are at root level
    assert "[dependencies]" in content
    assert 'numpy = ">=1.20"' in content

    # Check dev feature with conda deps, pip deps, and platform-specific
    assert "[feature.dev.dependencies]" in content
    assert 'pytest = ">=7.0"' in content
    assert "[feature.dev.pypi-dependencies]" in content
    assert 'black = "*"' in content
    assert "[feature.dev.target.linux-64.dependencies]" in content
    assert "[feature.dev.target.win-64.dependencies]" in content

    # Check docs feature
    assert "[feature.docs.dependencies]" in content
    assert 'sphinx = "*"' in content

    # Check environments are created
    assert "[environments]" in content
    assert "default = []" in content
    assert "dev = [" in content
    assert "docs = [" in content
    # "all" environment includes both features
    assert "all = [" in content


def test_pixi_optional_dependencies_single_group(tmp_path: Path) -> None:
    """Test single optional group doesn't create 'all' environment."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            optional_dependencies:
              test:
                - pytest
            platforms:
              - linux-64
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

    content = output_file.read_text()

    # Check feature is created
    assert "[feature.test.dependencies]" in content
    assert 'pytest = "*"' in content

    # With only one group, there should be no "all" environment
    assert "all = [" not in content


def test_pixi_optional_dependencies_monorepo(tmp_path: Path) -> None:
    """Test optional dependencies in monorepo setup."""
    # Create project1 with optional deps
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
            optional_dependencies:
              test:
                - pytest
            platforms:
              - linux-64
            """,
        ),
    )

    # Create project2 with different optional deps
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
            optional_dependencies:
              lint:
                - black
            platforms:
              - linux-64
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

    content = output_file.read_text()

    # Check main features
    assert "[feature.project1.dependencies]" in content
    assert 'numpy = "*"' in content
    assert "[feature.project2.dependencies]" in content
    assert 'pandas = "*"' in content

    # Check optional dependencies become sub-features
    assert "[feature.project1-test.dependencies]" in content
    assert 'pytest = "*"' in content
    assert "[feature.project2-lint.dependencies]" in content
    assert 'black = "*"' in content


# Tests for pixi-lock command


def test_pixi_lock_needs_regeneration_no_pixi_toml(tmp_path: Path) -> None:
    """Test _needs_regeneration returns True when pixi.toml doesn't exist."""
    pixi_toml = tmp_path / "pixi.toml"
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text("dependencies:\n  - numpy\n")

    assert _needs_regeneration(pixi_toml, [req_file]) is True


def test_pixi_lock_needs_regeneration_stale_pixi_toml(tmp_path: Path) -> None:
    """Test _needs_regeneration returns True when requirements are newer."""
    # Create pixi.toml first
    pixi_toml = tmp_path / "pixi.toml"
    pixi_toml.write_text("[project]\n")

    # Wait a bit and create requirements file (newer)
    time.sleep(0.05)
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text("dependencies:\n  - numpy\n")

    assert _needs_regeneration(pixi_toml, [req_file]) is True


def test_pixi_lock_needs_regeneration_up_to_date(tmp_path: Path) -> None:
    """Test _needs_regeneration returns False when pixi.toml is newer."""
    # Create requirements file first
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text("dependencies:\n  - numpy\n")

    # Wait a bit and create pixi.toml (newer)
    time.sleep(0.05)
    pixi_toml = tmp_path / "pixi.toml"
    pixi_toml.write_text("[project]\n")

    assert _needs_regeneration(pixi_toml, [req_file]) is False


def test_pixi_lock_needs_lock_regeneration_no_lock(tmp_path: Path) -> None:
    """Test _needs_lock_regeneration returns True when pixi.lock doesn't exist."""
    pixi_lock = tmp_path / "pixi.lock"
    pixi_toml = tmp_path / "pixi.toml"
    pixi_toml.write_text("[project]\n")

    assert _needs_lock_regeneration(pixi_lock, pixi_toml) is True


def test_pixi_lock_needs_lock_regeneration_stale_lock(tmp_path: Path) -> None:
    """Test _needs_lock_regeneration returns True when pixi.toml is newer."""
    # Create lock first
    pixi_lock = tmp_path / "pixi.lock"
    pixi_lock.write_text("version: 5\n")

    # Wait a bit and update pixi.toml
    time.sleep(0.05)
    pixi_toml = tmp_path / "pixi.toml"
    pixi_toml.write_text("[project]\n")

    assert _needs_lock_regeneration(pixi_lock, pixi_toml) is True


def test_pixi_lock_needs_lock_regeneration_up_to_date(tmp_path: Path) -> None:
    """Test _needs_lock_regeneration returns False when lock is newer."""
    # Create pixi.toml first
    pixi_toml = tmp_path / "pixi.toml"
    pixi_toml.write_text("[project]\n")

    # Wait a bit and create lock (newer)
    time.sleep(0.05)
    pixi_lock = tmp_path / "pixi.lock"
    pixi_lock.write_text("version: 5\n")

    assert _needs_lock_regeneration(pixi_lock, pixi_toml) is False


def test_pixi_lock_check_pixi_installed_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _check_pixi_installed exits when pixi is not found."""
    # Make shutil.which return None for pixi
    monkeypatch.setattr(shutil, "which", lambda _: None)  # type: ignore[attr-defined]

    with pytest.raises(SystemExit) as exc_info:
        _check_pixi_installed()
    assert exc_info.value.code == 1


def test_pixi_lock_command_generates_pixi_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test full pixi-lock workflow with mocked pixi CLI."""
    # Create requirements file
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
        ),
    )

    # Mock subprocess.run to avoid actually calling pixi
    def mock_subprocess_run(cmd: list, **kwargs: object) -> object:  # noqa: ARG001
        if cmd[0] == "pixi":
            # Create a fake pixi.lock file
            (tmp_path / "pixi.lock").write_text("version: 5\n")
            return subprocess.CompletedProcess(cmd, 0)
        msg = f"Unexpected command: {cmd}"
        raise ValueError(msg)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)  # type: ignore[attr-defined]

    # Mock shutil.which to find pixi

    original_which = shutil.which

    def mock_which(cmd: str) -> str | None:
        if cmd == "pixi":
            return "/usr/bin/pixi"
        return original_which(cmd)

    monkeypatch.setattr(shutil, "which", mock_which)  # type: ignore[attr-defined]

    # Run the command
    pixi_lock_command(
        depth=1,
        directory=tmp_path,
        files=None,
        platforms=None,
        verbose=False,
        only_pixi_lock=False,
        conda_lock=False,
        regenerate=False,
        check_input_hash=False,
    )

    # Check pixi.toml was generated
    pixi_toml = tmp_path / "pixi.toml"
    assert pixi_toml.exists()
    content = pixi_toml.read_text()
    assert 'numpy = "*"' in content

    # Check pixi.lock was "created" by our mock
    assert (tmp_path / "pixi.lock").exists()


def test_pixi_lock_command_with_conda_lock_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test pixi-lock with --conda-lock flag."""
    # Create requirements file
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
        ),
    )

    pixi_called = False
    convert_called = False

    def mock_subprocess_run(cmd: list, **kwargs: object) -> object:  # noqa: ARG001
        nonlocal pixi_called
        if cmd[0] == "pixi":
            pixi_called = True
            (tmp_path / "pixi.lock").write_text("version: 5\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)  # type: ignore[attr-defined]

    # Mock shutil.which to find pixi

    def mock_which(cmd: str) -> str | None:
        if cmd == "pixi":
            return "/usr/bin/pixi"
        return None

    monkeypatch.setattr(shutil, "which", mock_which)  # type: ignore[attr-defined]

    # Mock _convert_to_conda_lock to avoid needing a real pixi.lock
    import unidep._pixi_lock

    def mock_convert_to_conda_lock(
        pixi_lock: Path,
        output: Path | None = None,
        *,
        verbose: bool = False,  # noqa: ARG001
    ) -> Path:
        nonlocal convert_called
        convert_called = True
        output_path = output or pixi_lock.parent / "conda-lock.yml"
        output_path.write_text("version: 1\n")
        return output_path

    monkeypatch.setattr(
        unidep._pixi_lock,
        "_convert_to_conda_lock",
        mock_convert_to_conda_lock,
    )

    pixi_lock_command(
        depth=1,
        directory=tmp_path,
        files=None,
        platforms=None,
        verbose=False,
        only_pixi_lock=False,
        conda_lock=True,  # Enable conda-lock conversion
        regenerate=False,
        check_input_hash=False,
    )

    assert pixi_called
    assert convert_called
    assert (tmp_path / "conda-lock.yml").exists()


def test_pixi_lock_only_pixi_lock_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test --only-pixi-lock flag skips pixi.toml generation."""
    # Pre-create pixi.toml (required when using --only-pixi-lock)
    pixi_toml = tmp_path / "pixi.toml"
    pixi_toml.write_text(
        textwrap.dedent(
            """\
            [project]
            name = "test"
            channels = ["conda-forge"]
            platforms = ["linux-64"]

            [dependencies]
            numpy = "*"
            """,
        ),
    )

    # Create requirements file (but it shouldn't be read)
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text("dependencies:\n  - pandas\n")

    def mock_subprocess_run(cmd: list, **kwargs: object) -> object:  # noqa: ARG001
        if cmd[0] == "pixi":
            (tmp_path / "pixi.lock").write_text("version: 5\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)  # type: ignore[attr-defined]

    monkeypatch.setattr(  # type: ignore[attr-defined]
        shutil,
        "which",
        lambda cmd: "/usr/bin/pixi" if cmd == "pixi" else None,
    )

    pixi_lock_command(
        depth=1,
        directory=tmp_path,
        files=None,
        platforms=None,
        verbose=False,
        only_pixi_lock=True,  # Skip pixi.toml generation
        conda_lock=False,
        regenerate=False,
        check_input_hash=False,
    )

    # pixi.toml should still have numpy (not regenerated with pandas)
    content = pixi_toml.read_text()
    assert "numpy" in content
    assert "pandas" not in content


def test_pixi_lock_check_input_hash_skips_when_up_to_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test --check-input-hash skips regeneration when files are up to date."""
    # Create requirements file first
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
        ),
    )

    # Wait and create pixi.toml (newer than requirements)
    time.sleep(0.05)
    pixi_toml = tmp_path / "pixi.toml"
    pixi_toml.write_text("[project]\nname = 'test'\n")

    # Wait and create pixi.lock (newer than pixi.toml)
    time.sleep(0.05)
    pixi_lock = tmp_path / "pixi.lock"
    pixi_lock.write_text("version: 5\n")

    commands_called = []

    def mock_subprocess_run(cmd: list, **kwargs: object) -> object:  # noqa: ARG001
        commands_called.append(cmd[0])
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)  # type: ignore[attr-defined]

    monkeypatch.setattr(  # type: ignore[attr-defined]
        shutil,
        "which",
        lambda cmd: "/usr/bin/pixi" if cmd == "pixi" else None,
    )

    pixi_lock_command(
        depth=1,
        directory=tmp_path,
        files=None,
        platforms=None,
        verbose=True,  # Enable verbose to hit those code paths
        only_pixi_lock=False,
        conda_lock=False,
        regenerate=False,
        check_input_hash=True,  # Enable input hash check
    )

    # pixi lock should NOT be called since everything is up to date
    assert "pixi" not in commands_called


def test_pixi_lock_no_requirements_files_found(
    tmp_path: Path,
) -> None:
    """Test error when no requirements files are found."""
    with pytest.raises(SystemExit) as exc_info:
        pixi_lock_command(
            depth=1,
            directory=tmp_path,
            files=None,
            platforms=None,
            verbose=False,
            only_pixi_lock=False,
            conda_lock=False,
            regenerate=False,
            check_input_hash=False,
        )
    assert exc_info.value.code == 1


def test_pixi_lock_missing_pixi_toml_with_only_lock_flag(
    tmp_path: Path,
) -> None:
    """Test error when --only-pixi-lock but pixi.toml doesn't exist."""
    # Create a requirements file so we pass the first check
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text("dependencies:\n  - numpy\n")

    with pytest.raises(SystemExit) as exc_info:
        pixi_lock_command(
            depth=1,
            directory=tmp_path,
            files=None,
            platforms=None,
            verbose=False,
            only_pixi_lock=True,  # Skip generation, but pixi.toml doesn't exist
            conda_lock=False,
            regenerate=False,
            check_input_hash=False,
        )
    assert exc_info.value.code == 1


def test_pixi_lock_convert_to_conda_lock_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _convert_to_conda_lock calls convert and handles output."""
    pixi_lock = tmp_path / "pixi.lock"
    pixi_lock.write_text("version: 5\n")

    convert_called = False

    def mock_convert(lock_file_path: Path, conda_lock_path: Path) -> None:  # noqa: ARG001
        nonlocal convert_called
        convert_called = True
        conda_lock_path.write_text("version: 1\n")

    # Create a fake module to be imported

    fake_module = types.ModuleType("pixi_to_conda_lock")
    fake_module.convert = mock_convert  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pixi_to_conda_lock", fake_module)

    result = _convert_to_conda_lock(pixi_lock, verbose=True)
    assert convert_called
    assert result == tmp_path / "conda-lock.yml"
    assert result.exists()


def test_pixi_lock_convert_to_conda_lock_import_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _convert_to_conda_lock handles ImportError."""
    pixi_lock = tmp_path / "pixi.lock"
    pixi_lock.write_text("version: 5\n")

    # Remove the module from sys.modules to force ImportError
    monkeypatch.delitem(sys.modules, "pixi_to_conda_lock", raising=False)

    # Make import fail
    original_import = builtins.__import__

    def mock_import(
        name: str,
        globals_: dict | None = None,
        locals_: dict | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "pixi_to_conda_lock":
            raise ImportError(name)
        return original_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    with pytest.raises(SystemExit) as exc_info:
        _convert_to_conda_lock(pixi_lock)
    assert exc_info.value.code == 1


def test_pixi_lock_convert_to_conda_lock_convert_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _convert_to_conda_lock handles convert exception."""
    pixi_lock = tmp_path / "pixi.lock"
    pixi_lock.write_text("version: 5\n")

    def mock_convert(lock_file_path: Path, conda_lock_path: Path) -> None:  # noqa: ARG001
        msg = "Invalid lock file format"
        raise ValueError(msg)

    fake_module = types.ModuleType("pixi_to_conda_lock")
    fake_module.convert = mock_convert  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pixi_to_conda_lock", fake_module)

    with pytest.raises(SystemExit) as exc_info:
        _convert_to_conda_lock(pixi_lock)
    assert exc_info.value.code == 1


def test_pixi_lock_run_pixi_lock_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _run_pixi_lock handles CalledProcessError."""
    pixi_toml = tmp_path / "pixi.toml"
    pixi_toml.write_text("[project]\nname = 'test'\n")

    def mock_subprocess_run(cmd: list, **kwargs: object) -> None:  # noqa: ARG001
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)  # type: ignore[attr-defined]

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/pixi")  # type: ignore[attr-defined]

    with pytest.raises(SystemExit) as exc_info:
        _run_pixi_lock(pixi_toml)
    assert exc_info.value.code == 1


def test_pixi_lock_run_pixi_lock_verbose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _run_pixi_lock with verbose flag."""
    pixi_toml = tmp_path / "pixi.toml"
    pixi_toml.write_text("[project]\nname = 'test'\n")

    commands_called = []

    def mock_subprocess_run(cmd: list, **kwargs: object) -> object:  # noqa: ARG001
        commands_called.append(cmd)
        (tmp_path / "pixi.lock").write_text("version: 5\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)  # type: ignore[attr-defined]

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/pixi")  # type: ignore[attr-defined]

    _run_pixi_lock(pixi_toml, verbose=True)

    assert len(commands_called) == 1
    assert "--verbose" in commands_called[0]


def test_pixi_lock_command_with_explicit_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test pixi_lock_command with explicit files argument."""
    # Create requirements file
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
        ),
    )

    def mock_subprocess_run(cmd: list, **kwargs: object) -> object:  # noqa: ARG001
        if cmd[0] == "pixi":
            (tmp_path / "pixi.lock").write_text("version: 5\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)  # type: ignore[attr-defined]

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/pixi")  # type: ignore[attr-defined]

    pixi_lock_command(
        depth=1,
        directory=tmp_path,
        files=[req_file],  # Explicit file
        platforms=None,
        verbose=False,
        only_pixi_lock=False,
        conda_lock=False,
        regenerate=False,
        check_input_hash=False,
    )

    assert (tmp_path / "pixi.toml").exists()
    assert (tmp_path / "pixi.lock").exists()


def test_pixi_lock_needs_lock_regeneration_missing_pixi_toml(tmp_path: Path) -> None:
    """Test _needs_lock_regeneration returns True when pixi.toml doesn't exist."""
    pixi_lock = tmp_path / "pixi.lock"
    pixi_lock.write_text("version: 5\n")
    pixi_toml = tmp_path / "pixi.toml"  # Does not exist

    assert _needs_lock_regeneration(pixi_lock, pixi_toml) is True


def test_pixi_lock_command_verbose_regenerate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    """Test verbose output when regenerating pixi.toml."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
        ),
    )

    def mock_subprocess_run(cmd: list, **kwargs: object) -> object:  # noqa: ARG001
        if cmd[0] == "pixi":
            (tmp_path / "pixi.lock").write_text("version: 5\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)  # type: ignore[attr-defined]

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/pixi")  # type: ignore[attr-defined]

    pixi_lock_command(
        depth=1,
        directory=tmp_path,
        files=None,
        platforms=None,
        verbose=True,  # Enable verbose
        only_pixi_lock=False,
        conda_lock=False,
        regenerate=True,  # Force regeneration
        check_input_hash=False,
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "Generating pixi.toml" in captured.out


def test_pixi_lock_command_verbose_existing_pixi_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    """Test verbose output when pixi.toml already exists and is up to date."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
        ),
    )

    # Create pixi.toml AFTER requirements.yaml so it's "up to date"
    time.sleep(0.01)
    pixi_toml = tmp_path / "pixi.toml"
    pixi_toml.write_text("[project]\nname = 'test'\n")

    def mock_subprocess_run(cmd: list, **kwargs: object) -> object:  # noqa: ARG001
        if cmd[0] == "pixi":
            (tmp_path / "pixi.lock").write_text("version: 5\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)  # type: ignore[attr-defined]

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/pixi")  # type: ignore[attr-defined]

    pixi_lock_command(
        depth=1,
        directory=tmp_path,
        files=None,
        platforms=None,
        verbose=True,  # Enable verbose
        only_pixi_lock=False,
        conda_lock=False,
        regenerate=False,
        check_input_hash=False,  # Not using check_input_hash
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "Using existing pixi.toml" in captured.out
