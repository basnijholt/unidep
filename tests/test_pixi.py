"""Tests for simple Pixi.toml generation."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from unidep._pixi import (
    _make_pip_version_spec,
    _merge_version_specs,
    _parse_package_extras,
    _parse_version_build,
    generate_pixi_toml,
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
    assert "[workspace]" in content
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
    assert "[workspace]" in content
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
    assert "[workspace]" in content
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
    assert "[workspace]" in captured.out


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


# Tests for build string parsing


def test_parse_version_build_simple() -> None:
    """Test _parse_version_build with simple version specs."""
    assert _parse_version_build(None) == "*"
    assert _parse_version_build("") == "*"
    assert _parse_version_build(">=1.0") == ">=1.0"
    assert _parse_version_build("=11") == "=11"
    assert _parse_version_build("1.2.3") == "1.2.3"


def test_parse_version_build_with_build_string() -> None:
    """Test _parse_version_build with build strings."""
    result = _parse_version_build(">=0.21.0 cuda*")
    assert result == {"version": ">=0.21.0", "build": "cuda*"}

    result = _parse_version_build("=11 h1234*")
    assert result == {"version": "=11", "build": "h1234*"}

    result = _parse_version_build("1.0 py310*")
    assert result == {"version": "1.0", "build": "py310*"}


def test_pixi_with_build_string(tmp_path: Path) -> None:
    """Test pixi.toml generation with build strings in version specs."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - conda: qsimcirq >=0.21.0 cuda*  # [linux64]
              - gcc =11
            platforms:
              - linux-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    content = output_file.read_text()

    # Check that build string is properly formatted (tomli_w uses nested sections)
    assert "[target.linux-64.dependencies.qsimcirq]" in content
    assert 'version = ">=0.21.0"' in content
    assert 'build = "cuda*"' in content
    # Simple version without build string should still work
    assert 'gcc = "=11"' in content


def test_parse_package_extras_simple() -> None:
    """Test _parse_package_extras with packages without extras."""
    assert _parse_package_extras("numpy") == ("numpy", [])
    assert _parse_package_extras("my-package") == ("my-package", [])
    assert _parse_package_extras("pkg.name") == ("pkg.name", [])


def test_parse_package_extras_with_extras() -> None:
    """Test _parse_package_extras with packages that have extras."""
    assert _parse_package_extras("pipefunc[extras]") == ("pipefunc", ["extras"])
    assert _parse_package_extras("package[dev,test]") == ("package", ["dev", "test"])
    assert _parse_package_extras("pkg[a, b, c]") == ("pkg", ["a", "b", "c"])


def test_make_pip_version_spec_no_extras() -> None:
    """Test _make_pip_version_spec without extras returns unchanged version."""
    assert _make_pip_version_spec("*", []) == "*"
    assert _make_pip_version_spec(">=1.0", []) == ">=1.0"
    assert _make_pip_version_spec({"version": ">=1.0", "build": "py*"}, []) == {
        "version": ">=1.0",
        "build": "py*",
    }


def test_make_pip_version_spec_with_extras() -> None:
    """Test _make_pip_version_spec with extras returns table format."""
    result = _make_pip_version_spec("*", ["dev"])
    assert result == {"version": "*", "extras": ["dev"]}

    result = _make_pip_version_spec(">=1.0", ["dev", "test"])
    assert result == {"version": ">=1.0", "extras": ["dev", "test"]}

    # Also works with dict version (with build string)
    result = _make_pip_version_spec({"version": ">=1.0", "build": "py*"}, ["extra"])
    assert result == {"version": ">=1.0", "build": "py*", "extras": ["extra"]}


def test_pixi_with_pip_extras(tmp_path: Path) -> None:
    """Test pixi.toml generation with pip extras."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pip: pipefunc[extras]
              - pip: package[dev,test] >=1.0
            platforms:
              - linux-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    content = output_file.read_text()

    # Check that extras are properly formatted as table sections
    # tomli_w formats lists on separate lines
    assert "[pypi-dependencies.pipefunc]" in content
    assert 'version = "*"' in content
    assert '"extras"' in content  # The extra name is in the extras list

    assert "[pypi-dependencies.package]" in content
    assert 'version = ">=1.0"' in content
    assert '"dev"' in content
    assert '"test"' in content


def test_merge_version_specs_none_existing() -> None:
    """Test _merge_version_specs when existing is None."""
    assert _merge_version_specs(None, ">=1.0", "pkg") == ">=1.0"
    assert _merge_version_specs(None, "*", "pkg") == "*"


def test_merge_version_specs_simple_merge() -> None:
    """Test _merge_version_specs merges compatible constraints."""
    # >=1.7,<2 + <1.16 -> >=1.7,<1.16
    result = _merge_version_specs(">=1.7,<2", "<1.16", "scipy")
    assert result == ">=1.7,<1.16"

    # >=1.0 + <2.0 -> >=1.0,<2.0
    result = _merge_version_specs(">=1.0", "<2.0", "pkg")
    assert result == ">=1.0,<2.0"


def test_merge_version_specs_star_handling() -> None:
    """Test _merge_version_specs handles * (no constraint) correctly."""
    # * + >=1.0 -> >=1.0
    assert _merge_version_specs("*", ">=1.0", "pkg") == ">=1.0"

    # >=1.0 + * -> >=1.0
    assert _merge_version_specs(">=1.0", "*", "pkg") == ">=1.0"

    # * + * -> *
    assert _merge_version_specs("*", "*", "pkg") == "*"


def test_merge_version_specs_with_build_string() -> None:
    """Test _merge_version_specs handles build strings correctly."""
    # Can't merge build strings - prefer the one with build
    existing_with_build = {"version": ">=1.0", "build": "cuda*"}
    new_str = ">=2.0"
    result = _merge_version_specs(existing_with_build, new_str, "pkg")
    assert result == existing_with_build  # Keep existing with build

    # If new has build, use new
    existing_str = ">=1.0"
    new_with_build = {"version": ">=2.0", "build": "py310*"}
    result = _merge_version_specs(existing_str, new_with_build, "pkg")
    assert result == new_with_build


def test_merge_version_specs_with_extras() -> None:
    """Test _merge_version_specs merges extras correctly."""
    existing = {"version": ">=1.0", "extras": ["dev"]}
    new = {"version": "<2.0", "extras": ["test"]}
    result = _merge_version_specs(existing, new, "pkg")
    assert isinstance(result, dict)
    assert result["version"] == ">=1.0,<2.0"
    assert set(result["extras"]) == {"dev", "test"}


def test_merge_version_specs_conflict() -> None:
    """Test _merge_version_specs handles conflicting constraints."""
    # >=2.0 and <1.0 conflict - should prefer new
    result = _merge_version_specs(">=2.0", "<1.0", "pkg")
    assert result == "<1.0"  # Prefers new when conflict

    # When new is *, keep existing
    result = _merge_version_specs(">=2.0", "*", "pkg")
    assert result == ">=2.0"


def test_pixi_with_merged_constraints(tmp_path: Path) -> None:
    """Test pixi.toml generation merges version constraints."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - scipy >=1.7,<2
              - scipy <1.16
              - numpy >=1.20
              - numpy <2.0
            platforms:
              - linux-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    content = output_file.read_text()

    # Check that constraints are merged
    assert 'scipy = ">=1.7,<1.16"' in content
    assert 'numpy = ">=1.20,<2.0"' in content
