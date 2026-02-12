"""Tests for simple Pixi.toml generation."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib

from unidep._dependencies_parsing import _normalize_local_dependency_use
from unidep._pixi import (
    _collect_transitive_nodes,
    _derive_feature_names,
    _discover_local_dependency_graph,
    _editable_dependency_path,
    _make_pip_version_spec,
    _merge_version_specs,
    _parse_direct_requirements_for_node,
    _parse_package_extras,
    _parse_version_build,
    _resolve_conda_pip_conflict,
    generate_pixi_toml,
)
from unidep.utils import PathWithExtras

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
    assert content.count('"project1"') >= 1
    assert content.count('"project2"') >= 1


def test_pixi_monorepo_feature_names_unique_for_same_leaf_dir(tmp_path: Path) -> None:
    """Feature names should not collide when leaf directory names are identical."""
    apps_api_dir = tmp_path / "apps" / "api"
    apps_api_dir.mkdir(parents=True)
    apps_req = apps_api_dir / "requirements.yaml"
    apps_req.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            """,
        ),
    )

    libs_api_dir = tmp_path / "libs" / "api"
    libs_api_dir.mkdir(parents=True)
    libs_req = libs_api_dir / "requirements.yaml"
    libs_req.write_text(
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
        apps_req,
        libs_req,
        output_file=output_file,
        verbose=False,
    )

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    features = data["feature"]
    assert len(features) == 2
    assert len(set(features)) == 2

    numpy_features = [
        name
        for name, feature in features.items()
        if feature.get("dependencies", {}).get("numpy") == "*"
    ]
    pandas_features = [
        name
        for name, feature in features.items()
        if feature.get("dependencies", {}).get("pandas") == "*"
    ]
    assert len(numpy_features) == 1
    assert len(pandas_features) == 1
    assert numpy_features[0] != pandas_features[0]
    assert set(data["environments"]["default"]) == {
        numpy_features[0],
        pandas_features[0],
    }


def test_pixi_monorepo_feature_name_not_empty_for_relative_root_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relative root-level requirements file should not produce an empty feature key."""
    root_req = tmp_path / "requirements.yaml"
    root_req.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            """,
        ),
    )

    sub_dir = tmp_path / "project"
    sub_dir.mkdir()
    sub_req = sub_dir / "requirements.yaml"
    sub_req.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pandas
            """,
        ),
    )

    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]
    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        root_req.relative_to(tmp_path),
        sub_req.relative_to(tmp_path),
        output_file=output_file,
        verbose=False,
    )

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    features = data["feature"]
    assert len(features) == 2
    assert "" not in features
    assert all(name for name in features)


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


def test_pixi_normalizes_single_equals_for_pip_pins(tmp_path: Path) -> None:
    """Pip pins with single '=' should be normalized to '=='."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pip: pygsti =0.9.13.3
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        output_file=output_file,
        verbose=False,
    )

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert data["pypi-dependencies"]["pygsti"] == "==0.9.13.3"


def test_pixi_prefers_pip_pin_over_unpinned_conda(tmp_path: Path) -> None:
    """Pinned pip spec should override unpinned conda spec."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
              - pip: foo >=1.2
                conda: foo
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        output_file=output_file,
        verbose=False,
    )

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert data["dependencies"].get("foo") is None
    assert data["pypi-dependencies"]["foo"] == ">=1.2"


def test_pixi_prefers_conda_for_unpinned_both_sources(tmp_path: Path) -> None:
    """Unpinned dependencies available in both sources should use conda only."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
              - pandas
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        output_file=output_file,
        verbose=False,
    )

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    deps = data["dependencies"]
    assert deps["numpy"] == "*"
    assert deps["pandas"] == "*"
    assert "pypi-dependencies" not in data


def test_pixi_prefers_conda_for_equally_pinned_both_sources(tmp_path: Path) -> None:
    """When conda and pip have the same pin, use conda only."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - scipy >=1.10
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        output_file=output_file,
        verbose=False,
    )

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert data["dependencies"]["scipy"] == ">=1.10"
    assert "pypi-dependencies" not in data


def test_pixi_reconciles_universal_conda_and_target_pip_conflict(
    tmp_path: Path,
) -> None:
    """Target pip pins should reconcile against universal conda entries."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - click
              - pip: click ==0.1 # [linux64]
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

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert "click" not in data.get("dependencies", {})
    assert "click" not in data.get("pypi-dependencies", {})
    linux_target = data["target"]["linux-64"]
    assert linux_target["pypi-dependencies"]["click"] == "==0.1"


def test_pixi_reconciles_universal_pinned_conda_and_target_pinned_pip_prefers_target(
    tmp_path: Path,
) -> None:
    """Target pinned pip should override universal pinned conda entries."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - conda: click >=8
              - pip: click ==0.1 # [linux64]
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

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert "click" not in data.get("dependencies", {})
    linux_target = data["target"]["linux-64"]
    assert linux_target["pypi-dependencies"]["click"] == "==0.1"


def test_pixi_reconciles_universal_conda_and_target_pip_prefers_conda_when_target_unpinned(
    tmp_path: Path,
) -> None:
    """Universal pinned conda should drop target-unpinned pip duplicates."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - conda: click >=8
              - pip: click # [linux64]
            platforms:
              - linux-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert data["dependencies"]["click"] == ">=8"
    linux_target = data["target"]["linux-64"]
    assert "pypi-dependencies" not in linux_target


def test_pixi_reconciles_universal_pip_and_target_conda_prefers_conda_when_pinned(
    tmp_path: Path,
) -> None:
    """Universal pip should be removed when target conda has a stronger pin."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pip: click
              - conda: click >=8 # [linux64]
            platforms:
              - linux-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert "pypi-dependencies" not in data
    assert data["target"]["linux-64"]["dependencies"]["click"] == ">=8"


def test_pixi_reconciles_universal_pip_and_target_conda_prefers_pip_when_pinned(
    tmp_path: Path,
) -> None:
    """Target conda should be removed when universal pip has the stronger pin."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pip: click ==0.1
              - conda: click # [linux64]
            platforms:
              - linux-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert data["pypi-dependencies"]["click"] == "==0.1"
    assert "dependencies" not in data["target"]["linux-64"]


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
    assert 'path = "./my_package"' in content
    assert "editable = true" in content
    assert 'numpy = "*"' in content


def test_pixi_single_file_editable_path_relative_to_output(tmp_path: Path) -> None:
    """Single-file mode should use editable path relative to output location."""
    project_dir = tmp_path / "services" / "api"
    project_dir.mkdir(parents=True)

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

    pyproject_file = project_dir / "pyproject.toml"
    pyproject_file.write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]

            [project]
            name = "service-api"
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    editable_dep = data["pypi-dependencies"]["service_api"]
    assert editable_dep["editable"] is True
    assert editable_dep["path"] == "./services/api"


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
    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert data["dependencies"]["numpy"] == "*"
    assert "cuda-toolkit" not in data["dependencies"]
    assert "pyobjc" not in data.get("pypi-dependencies", {})

    assert data["target"]["linux-64"]["dependencies"]["cuda-toolkit"] == "=11.8"
    osx_target = data["target"].get("osx-arm64") or data["target"].get("osx-64")
    assert osx_target is not None
    assert osx_target["pypi-dependencies"]["pyobjc"] == "*"


def test_pixi_selector_targets_preserved_without_explicit_platforms(
    tmp_path: Path,
) -> None:
    """Selector targets should not be dropped when input files omit platforms."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
              - cuda-toolkit  # [linux64]
              - pip: pyobjc  # [osx]
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert "linux-64" in data["workspace"]["platforms"]
    assert any(p in data["workspace"]["platforms"] for p in ("osx-64", "osx-arm64"))
    assert data["target"]["linux-64"]["dependencies"]["cuda-toolkit"] == "*"
    osx_target = data["target"].get("osx-arm64") or data["target"].get("osx-64")
    assert osx_target is not None
    assert osx_target["pypi-dependencies"]["pyobjc"] == "*"


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
    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert data["dependencies"]["numpy"] == "*"
    assert "readline" not in data["dependencies"]
    assert "pywin32" not in data["dependencies"]
    assert data["target"]["linux-64"]["dependencies"]["readline"] == "*"
    assert data["target"]["osx-arm64"]["dependencies"]["readline"] == "*"
    assert data["target"]["win-64"]["dependencies"]["pywin32"] == "*"


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
    with output_file.open("rb") as f:
        data = tomllib.load(f)

    project1 = data["feature"]["project1"]
    project2 = data["feature"]["project2"]

    assert project1["dependencies"]["numpy"] == "*"
    assert "cuda-toolkit" not in project1["dependencies"]
    assert project1["target"]["linux-64"]["dependencies"]["cuda-toolkit"] == "*"

    assert project2["dependencies"]["pandas"] == "*"
    assert "pyobjc" not in project2.get("pypi-dependencies", {})
    assert project2["target"]["osx-arm64"]["pypi-dependencies"]["pyobjc"] == "*"


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


def test_pixi_monorepo_editable_paths_use_project_paths(tmp_path: Path) -> None:
    """Editable paths should point to project dirs, not derived feature names."""
    apps_api_dir = tmp_path / "apps" / "api"
    apps_api_dir.mkdir(parents=True)
    req1 = apps_api_dir / "requirements.yaml"
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
    pyproject1 = apps_api_dir / "pyproject.toml"
    pyproject1.write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]

            [project]
            name = "apps-api"
            """,
        ),
    )

    libs_api_dir = tmp_path / "libs" / "api"
    libs_api_dir.mkdir(parents=True)
    req2 = libs_api_dir / "requirements.yaml"
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
    pyproject2 = libs_api_dir / "pyproject.toml"
    pyproject2.write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]

            [project]
            name = "libs-api"
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req1, req2, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    editable_paths = {
        dep_data["path"]
        for feature in data["feature"].values()
        for dep_data in feature.get("pypi-dependencies", {}).values()
        if isinstance(dep_data, dict) and dep_data.get("editable") is True
    }
    assert editable_paths == {"./apps/api", "./libs/api"}


def test_pixi_monorepo_shared_local_file_becomes_single_feature(tmp_path: Path) -> None:
    """Shared local requirements should be represented as a separate feature."""
    shared_req = tmp_path / "dev-requirements.yaml"
    shared_req.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pytest
            """,
        ),
    )

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
            local_dependencies:
              - ../dev-requirements.yaml
            """,
        ),
    )

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
            local_dependencies:
              - ../dev-requirements.yaml
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req1, req2, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    features = data["feature"]
    project1_feature = next(
        name
        for name, feature in features.items()
        if feature.get("dependencies", {}).get("numpy") == "*"
    )
    project2_feature = next(
        name
        for name, feature in features.items()
        if feature.get("dependencies", {}).get("pandas") == "*"
    )
    shared_feature = next(
        name
        for name, feature in features.items()
        if feature.get("dependencies", {}).get("pytest") == "*"
    )

    assert project1_feature != shared_feature
    assert project2_feature != shared_feature
    assert shared_feature.startswith("dev-requirements")
    assert "pytest" not in features[project1_feature].get("dependencies", {})
    assert "pytest" not in features[project2_feature].get("dependencies", {})

    assert set(data["environments"]["default"]) == {
        project1_feature,
        project2_feature,
        shared_feature,
    }


def test_pixi_monorepo_transitive_local_dependencies_are_composed_in_envs(
    tmp_path: Path,
) -> None:
    """Features should stay local while envs include transitive local dependencies."""
    project_c = tmp_path / "project_c"
    project_c.mkdir()
    req_c = project_c / "requirements.yaml"
    req_c.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - sympy
            """,
        ),
    )

    project_b = tmp_path / "project_b"
    project_b.mkdir()
    req_b = project_b / "requirements.yaml"
    req_b.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pandas
            local_dependencies:
              - ../project_c
            """,
        ),
    )

    project_a = tmp_path / "project_a"
    project_a.mkdir()
    req_a = project_a / "requirements.yaml"
    req_a.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            local_dependencies:
              - ../project_b
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_a, req_c, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    features = data["feature"]
    feature_a = next(
        name
        for name, feature in features.items()
        if feature.get("dependencies", {}).get("numpy") == "*"
    )
    feature_b = next(
        name
        for name, feature in features.items()
        if feature.get("dependencies", {}).get("pandas") == "*"
    )
    feature_c = next(
        name
        for name, feature in features.items()
        if feature.get("dependencies", {}).get("sympy") == "*"
    )

    assert "pandas" not in features[feature_a].get("dependencies", {})
    assert "sympy" not in features[feature_a].get("dependencies", {})
    assert "sympy" not in features[feature_b].get("dependencies", {})

    assert set(data["environments"]["default"]) == {feature_a, feature_b, feature_c}


def test_pixi_monorepo_ignores_wheel_local_dependencies_in_graph(
    tmp_path: Path,
) -> None:
    """Multi-file mode should skip wheel/zip locals while discovering features."""
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    wheel_file = wheels_dir / "example-0.1.0-py3-none-any.whl"
    wheel_file.write_text("not-a-real-wheel")

    project1 = tmp_path / "project1"
    project1.mkdir()
    req1 = project1 / "requirements.yaml"
    req1.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            local_dependencies:
              - ../wheels/example-0.1.0-py3-none-any.whl
            """,
        ),
    )

    project2 = tmp_path / "project2"
    project2.mkdir()
    req2 = project2 / "requirements.yaml"
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
    generate_pixi_toml(req1, req2, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert set(data["feature"]) == {"project1", "project2"}


def test_pixi_single_file_local_dependency_use_modes(tmp_path: Path) -> None:
    """`use: pypi` should add pip dep, while `use: skip` should add nothing."""
    pypi_local = tmp_path / "pypi_local"
    pypi_local.mkdir()
    (pypi_local / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pandas
            """,
        ),
    )

    skipped_local = tmp_path / "skipped_local"
    skipped_local.mkdir()
    (skipped_local / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - scipy
            """,
        ),
    )

    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            local_dependencies:
              - local: ./pypi_local
                use: pypi
                pypi: pypi-local-package >=1.2
              - local: ./skipped_local
                use: skip
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert data["dependencies"]["numpy"] == "*"
    assert "pandas" not in data["dependencies"]
    assert "scipy" not in data["dependencies"]
    assert data["pypi-dependencies"]["pypi-local-package"] == ">=1.2"
    assert "skipped_local" not in data.get("pypi-dependencies", {})
    assert "target" not in data


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


def test_pixi_single_file_optional_local_dependency_stays_optional(
    tmp_path: Path,
) -> None:
    """Optional local deps should appear in optional features, not root deps."""
    local_dep_dir = tmp_path / "localdep"
    local_dep_dir.mkdir()
    local_req = local_dep_dir / "requirements.yaml"
    local_req.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pandas
            """,
        ),
    )

    root_req = tmp_path / "requirements.yaml"
    root_req.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            optional_dependencies:
              dev:
                - ./localdep
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(root_req, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert data["dependencies"]["numpy"] == "*"
    assert "pandas" not in data.get("dependencies", {})
    assert data["feature"]["dev"]["dependencies"]["pandas"] == "*"
    assert data["environments"]["default"] == []
    assert data["environments"]["dev"] == ["dev"]


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


def test_pixi_monorepo_optional_local_dependency_is_only_in_optional_env(
    tmp_path: Path,
) -> None:
    """Optional local projects should be included only in the optional env."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    app_req = app_dir / "requirements.yaml"
    app_req.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pandas
            optional_dependencies:
              dev:
                - ../lib
                - pytest
            """,
        ),
    )

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    lib_req = lib_dir / "requirements.yaml"
    lib_req.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            """,
        ),
    )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other_req = other_dir / "requirements.yaml"
    other_req.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - scipy
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(app_req, other_req, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    features = data["feature"]
    assert "app" in features
    assert "app-dev" in features
    assert "lib" in features
    assert "other" in features

    envs = data["environments"]
    assert "lib" not in envs["default"]
    assert "lib" in envs["app-dev"]


def test_pixi_monorepo_default_env_excludes_optional_features(
    tmp_path: Path,
) -> None:
    """Ensure monorepo default env only includes base features."""
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
              dev:
                - pytest
            platforms:
              - linux-64
            """,
        ),
    )

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

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    envs = data["environments"]
    assert set(envs["default"]) == {"project1", "project2"}
    assert "project1-dev" not in envs["default"]
    assert set(envs["project1-dev"]) == {"project1", "project1-dev"}


def test_pixi_empty_platform_override_uses_file_platforms(tmp_path: Path) -> None:
    """Passing platforms=[] should fall back to platforms from requirements files."""
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
              - osx-arm64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        output_file=output_file,
        verbose=False,
        platforms=[],
    )

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert set(data["workspace"]["platforms"]) == {"linux-64", "osx-arm64"}


def test_pixi_monorepo_skips_optional_groups_when_base_feature_empty(
    tmp_path: Path,
) -> None:
    """Optional sub-features should be skipped when a root has no base feature."""
    project1 = tmp_path / "project1"
    project1.mkdir()
    req1 = project1 / "requirements.yaml"
    req1.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies: []
            optional_dependencies:
              docs:
                - sphinx
            """,
        ),
    )

    project2 = tmp_path / "project2"
    project2.mkdir()
    req2 = project2 / "requirements.yaml"
    req2.write_text(
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
    generate_pixi_toml(req1, req2, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    features = data["feature"]
    assert "project1" not in features
    assert "project1-docs" not in features
    assert "project2" in features


def test_pixi_monorepo_skips_empty_optional_feature_group(tmp_path: Path) -> None:
    """Empty optional groups should not create empty sub-features."""
    project1 = tmp_path / "project1"
    project1.mkdir()
    req1 = project1 / "requirements.yaml"
    req1.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            optional_dependencies:
              docs:
                - pytest
            """,
        ),
    )

    project2 = tmp_path / "project2"
    project2.mkdir()
    req2 = project2 / "requirements.yaml"
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
        output_file=output_file,
        verbose=False,
        skip_dependencies=["pytest"],
    )

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert "project1-docs" not in data["feature"]


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


def test_parse_version_build_empty_string() -> None:
    """Whitespace-only pins should normalize to wildcard."""
    assert _parse_version_build("   ") == "*"


def test_resolve_conda_pip_conflict_prefers_pip_with_extras() -> None:
    """Pip extras cannot be represented via conda, so keep pip and drop conda."""
    conda_deps: dict[str, str | dict[str, object]] = {"foo": "*"}
    pip_deps: dict[str, str | dict[str, object]] = {
        "foo": {"version": "*", "extras": ["dev"]},
    }
    _resolve_conda_pip_conflict(conda_deps, pip_deps, "foo")
    assert "foo" not in conda_deps
    assert "foo" in pip_deps


def test_resolve_conda_pip_conflict_drops_unpinned_pip_when_conda_pinned() -> None:
    """Pinned conda should win over unpinned pip for the same package."""
    conda_deps: dict[str, str | dict[str, object]] = {"foo": ">=1.0"}
    pip_deps: dict[str, str | dict[str, object]] = {"foo": "*"}
    _resolve_conda_pip_conflict(conda_deps, pip_deps, "foo")
    assert "foo" in conda_deps
    assert "foo" not in pip_deps


def test_resolve_conda_pip_conflict_with_pinned_dict_spec() -> None:
    """Dict specs with non-wildcard versions should be treated as pinned."""
    conda_deps: dict[str, str | dict[str, object]] = {"foo": {"version": ">=1.0"}}
    pip_deps: dict[str, str | dict[str, object]] = {"foo": "*"}
    _resolve_conda_pip_conflict(conda_deps, pip_deps, "foo")
    assert "foo" in conda_deps
    assert "foo" not in pip_deps


def test_normalize_local_dependency_use_returns_valid_mode() -> None:
    """Valid explicit local dependency use values should pass through."""
    assert _normalize_local_dependency_use("skip") == "skip"


def test_derive_feature_names_handles_commonpath_valueerror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feature naming should fall back when commonpath raises ValueError."""
    first = tmp_path / "a" / "api"
    second = tmp_path / "b" / "api"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    req1 = first / "requirements.yaml"
    req2 = second / "requirements.yaml"
    req1.write_text("dependencies: [numpy]\n")
    req2.write_text("dependencies: [pandas]\n")

    def _raise_commonpath(_: list[str]) -> str:
        msg = "boom"
        raise ValueError(msg)

    monkeypatch.setattr("unidep._pixi.os.path.commonpath", _raise_commonpath)
    names = _derive_feature_names([req1, req2])
    assert len(names) == 2
    assert len(set(names)) == 2


def test_derive_feature_names_handles_relative_to_valueerror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feature naming should still be unique if relative_to raises ValueError."""
    root1 = tmp_path / "a+b" / "api"
    root2 = tmp_path / "a b" / "api"
    root3 = tmp_path / "a@b" / "api"
    root1.mkdir(parents=True)
    root2.mkdir(parents=True)
    root3.mkdir(parents=True)
    req1 = root1 / "requirements.yaml"
    req2 = root2 / "requirements.yaml"
    req3 = root3 / "requirements.yaml"
    req1.write_text("dependencies: [numpy]\n")
    req2.write_text("dependencies: [pandas]\n")
    req3.write_text("dependencies: [scipy]\n")

    path_type = type(tmp_path)

    def _raise_relative_to(_self: Path, *_args: object, **_kwargs: object) -> Path:
        msg = "boom"
        raise ValueError(msg)

    monkeypatch.setattr(path_type, "relative_to", _raise_relative_to)
    names = _derive_feature_names([req1, req2, req3])
    assert len(names) == 3
    assert len(set(names)) == 3
    assert any(name.endswith("-2") for name in names)


def test_editable_dependency_path_relative_forms(tmp_path: Path) -> None:
    """Editable path helper should preserve '.' and '../' relative forms."""
    project_dir = tmp_path / "pkg"
    project_dir.mkdir()
    same_dir_output = project_dir / "pixi.toml"
    assert _editable_dependency_path(project_dir, same_dir_output) == "."

    nested_output = tmp_path / "nested" / "pixi.toml"
    nested_output.parent.mkdir()
    assert _editable_dependency_path(project_dir, nested_output) == "../pkg"


def test_discover_local_dependency_graph_skips_non_local_and_missing(
    tmp_path: Path,
) -> None:
    """Graph discovery should ignore skipped/pypi/missing local entries safely."""
    root = tmp_path / "root"
    root.mkdir()
    req = root / "requirements.yaml"
    req.write_text(
        textwrap.dedent(
            """\
            dependencies:
              - numpy
            local_dependencies:
              - local: ../missing
                use: local
              - local: ../skipme
                use: skip
              - local: ../pypi-alt
                use: pypi
                pypi: foo>=1
            """,
        ),
    )

    roots, discovered, graph, optional_graph = _discover_local_dependency_graph([req])
    assert roots == discovered
    assert len(roots) == 1
    assert graph[roots[0]] == []
    assert optional_graph == {}


def test_parse_direct_requirements_for_node_with_extras(tmp_path: Path) -> None:
    """Selected extras on a local node should merge into required dependencies."""
    req = tmp_path / "requirements.yaml"
    req.write_text(
        textwrap.dedent(
            """\
            dependencies:
              - numpy
            optional_dependencies:
              dev:
                - pytest
            """,
        ),
    )
    node = PathWithExtras(req, ["dev"])
    parsed = _parse_direct_requirements_for_node(
        node,
        verbose=False,
        ignore_pins=None,
        skip_dependencies=None,
        overwrite_pins=None,
    )
    assert "numpy" in parsed.requirements
    assert "pytest" in parsed.requirements
    assert parsed.optional_dependencies == {}


def test_parse_direct_requirements_for_node_with_star_extra(tmp_path: Path) -> None:
    """A '*' extra should include all optional dependency groups."""
    req = tmp_path / "requirements.yaml"
    req.write_text(
        textwrap.dedent(
            """\
            dependencies:
              - numpy
            optional_dependencies:
              dev:
                - pytest
              docs:
                - sphinx
            """,
        ),
    )
    node = PathWithExtras(req, ["*"])
    parsed = _parse_direct_requirements_for_node(
        node,
        verbose=False,
        ignore_pins=None,
        skip_dependencies=None,
        overwrite_pins=None,
    )
    assert "pytest" in parsed.requirements
    assert "sphinx" in parsed.requirements
    assert parsed.optional_dependencies == {}


def test_collect_transitive_nodes_deduplicates_seen_nodes(tmp_path: Path) -> None:
    """Transitive collection should skip already-seen nodes in cyclic graphs."""
    req_a = PathWithExtras(tmp_path / "a" / "requirements.yaml", [])
    req_b = PathWithExtras(tmp_path / "b" / "requirements.yaml", [])
    graph = {req_a: [req_b, req_b], req_b: [req_a]}
    collected = _collect_transitive_nodes(req_a, graph)
    assert collected == [req_b, req_a]


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

    # A simplified merge should still keep a single constraint format.
    result = _merge_version_specs(">=1.0", ">=2.0", "pkg")
    assert result == ">=2.0"


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
    assert result["extras"] == ["dev", "test"]


def test_merge_version_specs_conflict() -> None:
    """Test _merge_version_specs handles conflicting constraints."""
    # Conflict fallback should be deterministic and order-independent.
    result = _merge_version_specs(">=2.0", "<1.0", "pkg")
    assert result == ">=2.0,<1.0"

    # Reverse order should produce the same result.
    reverse_result = _merge_version_specs("<1.0", ">=2.0", "pkg")
    assert reverse_result == ">=2.0,<1.0"

    # Conflict path that still triggers the explicit fallback branch.
    exact_pin_conflict = _merge_version_specs("==1.0", ">=2.0", "pkg")
    assert exact_pin_conflict == "==1.0,>=2.0"

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
