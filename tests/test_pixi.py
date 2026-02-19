"""Tests for simple Pixi.toml generation."""

from __future__ import annotations

import os
import textwrap
from typing import TYPE_CHECKING, Any

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib

from unidep._pixi import (
    _collect_transitive_nodes,
    _derive_feature_names,
    _discover_local_dependency_graph,
    _editable_dependency_path,
    _make_pip_version_spec,
    _merge_version_specs,
    _parse_direct_requirements_for_node,
    _parse_version_build,
    _resolve_conda_pip_conflict,
    _restore_demoted_universals,
    _unique_optional_feature_name,
    _version_spec_is_pinned,
    _with_unique_order_paths,
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


def test_pixi_reconcile_is_order_independent_for_universal_and_target_conflicts(
    tmp_path: Path,
) -> None:
    """Universal/target conflict reconciliation should not depend on declaration order."""
    req_target_then_universal = tmp_path / "target_then_universal.yaml"
    req_target_then_universal.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pip: click ==0.1 # [linux64]
              - conda: click >=8
            platforms:
              - linux-64
            """,
        ),
    )

    req_universal_then_target = tmp_path / "universal_then_target.yaml"
    req_universal_then_target.write_text(
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

    out1 = tmp_path / "pixi-target-then-universal.toml"
    out2 = tmp_path / "pixi-universal-then-target.toml"
    generate_pixi_toml(req_target_then_universal, output_file=out1, verbose=False)
    generate_pixi_toml(req_universal_then_target, output_file=out2, verbose=False)

    with out1.open("rb") as f:
        data1 = tomllib.load(f)
    with out2.open("rb") as f:
        data2 = tomllib.load(f)

    assert data1 == data2
    assert "click" not in data1.get("dependencies", {})
    assert data1["target"]["linux-64"]["pypi-dependencies"]["click"] == "==0.1"


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


def test_pixi_reconciles_universal_conda_and_target_pip_multiplatform(
    tmp_path: Path,
) -> None:
    """Universal conda should be promoted to non-overriding target platforms."""
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
              - osx-arm64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    # click must NOT be in universal deps (conflict on at least one platform)
    assert "click" not in data.get("dependencies", {})
    assert "click" not in data.get("pypi-dependencies", {})

    # linux-64 gets the target-specific pip pin
    linux_target = data["target"]["linux-64"]
    assert linux_target["pypi-dependencies"]["click"] == "==0.1"

    # osx-arm64 gets the original universal conda spec
    osx_target = data["target"]["osx-arm64"]
    assert osx_target["dependencies"]["click"] == ">=8"


def test_pixi_reconciles_universal_pip_and_target_conda_multiplatform(
    tmp_path: Path,
) -> None:
    """Universal pip should be promoted to non-overriding target platforms."""
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pip: click ==0.1
              - conda: click >=8 # [linux64]
            platforms:
              - linux-64
              - osx-arm64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    # click must NOT be in universal deps
    assert "click" not in data.get("dependencies", {})
    assert "click" not in data.get("pypi-dependencies", {})

    # linux-64 gets the target-specific conda pin
    linux_target = data["target"]["linux-64"]
    assert linux_target["dependencies"]["click"] == ">=8"

    # osx-arm64 gets the original universal pip spec
    osx_target = data["target"]["osx-arm64"]
    assert osx_target["pypi-dependencies"]["click"] == "==0.1"


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


def test_pixi_single_file_includes_local_dependency_package_as_editable(
    tmp_path: Path,
) -> None:
    """Single-file mode should install local dependency projects as editable packages."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    req_file = app_dir / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            local_dependencies:
              - ../lib
            """,
        ),
    )

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
              - pandas
            """,
        ),
    )
    (lib_dir / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]

            [project]
            name = "lib"
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert data["dependencies"]["numpy"] == "*"
    assert data["dependencies"]["pandas"] == "*"
    lib_editable = data["pypi-dependencies"]["lib"]
    assert lib_editable["editable"] is True
    assert lib_editable["path"] == "./lib"


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


def test_pixi_monorepo_keeps_unmanaged_local_dependency_as_editable(
    tmp_path: Path,
) -> None:
    """Monorepo mode should keep unmanaged but installable local packages."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    req_app = app_dir / "requirements.yaml"
    req_app.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            local_dependencies:
              - ../lib
            """,
        ),
    )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    req_other = other_dir / "requirements.yaml"
    req_other.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pandas
            """,
        ),
    )

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]

            [project]
            name = "lib-pkg"
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_app, req_other, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert "lib" not in data["feature"]
    app_editable = data["feature"]["app"]["pypi-dependencies"]["lib_pkg"]
    assert app_editable["editable"] is True
    assert app_editable["path"] == "./lib"


def test_pixi_monorepo_optional_unmanaged_deduped_against_base(
    tmp_path: Path,
) -> None:
    """Unmanaged local dep in both base and optional should only appear in base feature."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    req_app = app_dir / "requirements.yaml"
    req_app.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            local_dependencies:
              - ../lib
            optional_dependencies:
              dev:
                - ../lib
            """,
        ),
    )

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]

            [project]
            name = "lib-pkg"
            """,
        ),
    )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    req_other = other_dir / "requirements.yaml"
    req_other.write_text(
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
    generate_pixi_toml(req_app, req_other, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    # lib should be in the base feature
    assert "lib_pkg" in data["feature"]["app"]["pypi-dependencies"]
    # lib should NOT be duplicated in the optional sub-feature
    opt_feature_name = "app-dev"
    if opt_feature_name in data.get("feature", {}):
        opt_pypi = data["feature"][opt_feature_name].get("pypi-dependencies", {})
        assert "lib_pkg" not in opt_pypi, (
            "Unmanaged local dep should be deduped from optional feature"
        )


def test_pixi_monorepo_optional_unmanaged_only_group_creates_feature(
    tmp_path: Path,
) -> None:
    """An optional group with only unmanaged local deps should still create a feature."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            optional_dependencies:
              dev:
                - ../lib
            """,
        ),
    )

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]

            [project]
            name = "lib-pkg"
            """,
        ),
    )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / "requirements.yaml").write_text(
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
        app_dir / "requirements.yaml",
        other_dir / "requirements.yaml",
        output_file=output_file,
        verbose=False,
    )

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    # The optional sub-feature should exist with the editable
    opt_feature_name = "app-dev"
    assert opt_feature_name in data["feature"], (
        f"Expected feature '{opt_feature_name}' for unmanaged-only optional group"
    )
    opt_pypi = data["feature"][opt_feature_name].get("pypi-dependencies", {})
    assert "lib_pkg" in opt_pypi
    assert opt_pypi["lib_pkg"]["editable"] is True

    # An environment referencing the optional feature should exist
    env_name = opt_feature_name.replace("_", "-")
    assert env_name in data["environments"]
    assert opt_feature_name in data["environments"][env_name]


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


def test_pixi_monorepo_optional_group_with_only_local_deps_creates_env(
    tmp_path: Path,
) -> None:
    """Local-only optional groups should still create optional environments."""
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
    envs = data["environments"]

    assert "app" in features
    assert "lib" in features
    assert "other" in features
    # Local-only optional groups should not need their own dependency feature.
    assert "app-dev" not in features
    assert "app-dev" in envs
    assert "lib" not in envs["default"]
    assert "lib" in envs["app-dev"]


def test_pixi_monorepo_optional_feature_name_collision_does_not_overwrite_base_feature(
    tmp_path: Path,
) -> None:
    """Optional feature names must not overwrite existing base feature keys."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project_req = project_dir / "requirements.yaml"
    project_req.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            optional_dependencies:
              dev:
                - pytest
            """,
        ),
    )

    project_dev_dir = tmp_path / "project-dev"
    project_dev_dir.mkdir()
    project_dev_req = project_dev_dir / "requirements.yaml"
    project_dev_req.write_text(
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
        project_req,
        project_dev_req,
        output_file=output_file,
        verbose=False,
    )

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    features = data["feature"]
    assert features["project-dev"]["dependencies"]["pandas"] == "*"
    assert features["project-dev-opt"]["dependencies"]["pytest"] == "*"
    assert data["environments"]["project-dev-opt"] == ["project", "project-dev-opt"]


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


def test_resolve_conda_pip_conflict_prefers_pip_with_extras() -> None:
    """Pip extras cannot be represented via conda, so keep pip and drop conda."""
    conda_deps: dict[str, str | dict[str, object]] = {"foo": "*"}
    pip_deps: dict[str, str | dict[str, object]] = {
        "foo": {"version": "*", "extras": ["dev"]},
    }
    _resolve_conda_pip_conflict(conda_deps, pip_deps, "foo")
    assert "foo" not in conda_deps
    assert "foo" in pip_deps


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


def test_editable_dependency_path_cross_drive(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """On Windows, cross-drive paths should fall back to absolute instead of crashing."""
    project_dir = tmp_path / "pkg"
    project_dir.mkdir()
    output = tmp_path / "pixi.toml"

    # Simulate ValueError from os.path.relpath on cross-drive paths
    original_relpath = os.path.relpath

    def raising_relpath(_path: Any, _start: Any = None) -> str:
        msg = "path is on mount 'C:', start on mount 'D:'"
        raise ValueError(msg)

    monkeypatch.setattr(os.path, "relpath", raising_relpath)
    result = _editable_dependency_path(project_dir, output)
    # Should return an absolute posix path instead of raising
    assert project_dir.resolve().as_posix() == result

    # Restore and verify normal behavior still works
    monkeypatch.setattr(os.path, "relpath", original_relpath)
    assert _editable_dependency_path(project_dir, output) == "./pkg"


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

    result = _discover_local_dependency_graph([req])
    assert result.roots == result.discovered
    assert len(result.roots) == 1
    assert result.graph[result.roots[0]] == []
    assert result.optional_group_graph == {}
    assert result.unmanaged_local_graph[result.roots[0]] == []
    assert result.optional_group_unmanaged_graph == {}


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


def test_pixi_optional_local_dep_does_not_leak_base_local_deps(
    tmp_path: Path,
) -> None:
    """Base local deps must not appear in optional features.

    When a root file has both local_dependencies and an optional_dependencies
    group that adds another local dep, the subtraction of base requirements
    from the group parse must use stable Spec identity (not parse-time
    identifiers) to avoid leaking base deps into the optional feature.
    """
    lib1 = tmp_path / "lib1"
    lib1.mkdir()
    (lib1 / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
              - pandas
              - scipy
            """,
        ),
    )

    lib2 = tmp_path / "lib2"
    lib2.mkdir()
    (lib2 / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
              - requests
            """,
        ),
    )

    root_req = tmp_path / "requirements.yaml"
    root_req.write_text(
        textwrap.dedent(
            """\
            dependencies:
              - numpy
            local_dependencies:
              - ./lib1
            optional_dependencies:
              dev:
                - ./lib2
                - pytest
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(root_req, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    root_deps = set(data.get("dependencies", {}).keys())
    dev_deps = set(data["feature"]["dev"].get("dependencies", {}).keys())

    # pandas and scipy come from lib1 (base local dep) and must NOT leak
    assert "pandas" in root_deps
    assert "scipy" in root_deps
    assert "pandas" not in dev_deps, "base local dep leaked into optional feature"
    assert "scipy" not in dev_deps, "base local dep leaked into optional feature"
    # dev feature should only have the optional-specific deps
    assert "requests" in dev_deps
    assert "pytest" in dev_deps


def test_pixi_demoted_universal_replaces_weak_target_override(
    tmp_path: Path,
) -> None:
    """A pinned demoted universal must replace an unpinned target override.

    When universal conda click>=8 is demoted because linux-64 has a pinned pip
    override, platforms that only have an unpinned pip entry (osx-64: pip click)
    should get the demoted conda spec instead of keeping the weak pip entry.
    """
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - conda: click >=8
              - pip: click ==0.1  # [linux64]
              - pip: click        # [osx64]
            platforms:
              - linux-64
              - osx-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    # linux-64: pinned pip override wins
    linux = data["target"]["linux-64"]
    assert linux["pypi-dependencies"]["click"] == "==0.1"

    # osx-64: demoted conda >=8 must replace the unpinned pip entry
    osx = data["target"]["osx-64"]
    assert osx["dependencies"]["click"] == ">=8"
    assert "click" not in osx.get("pypi-dependencies", {})


def test_pixi_demoted_universal_reapplies_conflict_policy_for_unpinned_specs(
    tmp_path: Path,
) -> None:
    """Demoted universals should still use conda-vs-pip default precedence.

    With universal conda click and target pip overrides, linux-64 keeps the
    pinned pip override. For osx-64, where target pip is unpinned, restoration
    must re-run conflict preference so conda wins over unpinned pip.
    """
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - conda: click
              - pip: click ==0.1  # [linux64]
              - pip: click        # [osx64]
            platforms:
              - linux-64
              - osx-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    with output_file.open("rb") as f:
        data = tomllib.load(f)

    linux = data["target"]["linux-64"]
    assert linux["pypi-dependencies"]["click"] == "==0.1"

    osx = data["target"]["osx-64"]
    assert osx["dependencies"]["click"] == "*"
    assert "click" not in osx.get("pypi-dependencies", {})


def test_parse_version_build_whitespace_only() -> None:
    assert _parse_version_build("  ") == "*"


def test_make_pip_version_spec_dict_with_extras() -> None:
    result = _make_pip_version_spec({"version": ">=1.0", "build": "py3*"}, ["extra1"])
    assert result == {"version": ">=1.0", "build": "py3*", "extras": ["extra1"]}


def test_merge_version_specs_existing_star() -> None:
    assert _merge_version_specs("*", ">=1.0", "pkg") == ">=1.0"


def test_version_spec_is_pinned_dict_with_version() -> None:
    assert _version_spec_is_pinned({"version": ">=1.0"}) is True


def test_with_unique_order_paths_deduplicates(tmp_path: Path) -> None:
    d = tmp_path / "a"
    d.mkdir()
    result = _with_unique_order_paths([d, d, d])
    assert result == [d]


def test_unique_optional_feature_name_double_collision() -> None:
    taken: set[str] = {"feat-dev", "feat-dev-opt"}
    name = _unique_optional_feature_name(
        parent_feature="feat",
        group_name="dev",
        taken_names=taken,
    )
    assert name == "feat-dev-opt-2"
    assert name in taken


def test_pixi_single_file_optional_local_dep_transitive_dedup(
    tmp_path: Path,
) -> None:
    """Cover single-file optional local dep dedup and pip-installable path."""
    # Shared transitive dep referenced by both optional local deps
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    (shared_dir / "requirements.yaml").write_text(
        textwrap.dedent("""\
            dependencies:
              - scipy
        """),
    )
    (shared_dir / "setup.py").write_text(
        "from setuptools import setup; setup(name='shared')",
    )

    # Two optional local deps that both depend on shared
    opt_a_dir = tmp_path / "opt_a"
    opt_a_dir.mkdir()
    (opt_a_dir / "requirements.yaml").write_text(
        textwrap.dedent("""\
            dependencies:
              - pandas
            local_dependencies:
              - ../shared
        """),
    )
    (opt_a_dir / "setup.py").write_text(
        "from setuptools import setup; setup(name='opt-a')",
    )

    opt_b_dir = tmp_path / "opt_b"
    opt_b_dir.mkdir()
    (opt_b_dir / "requirements.yaml").write_text(
        textwrap.dedent("""\
            dependencies:
              - polars
            local_dependencies:
              - ../shared
        """),
    )
    (opt_b_dir / "setup.py").write_text(
        "from setuptools import setup; setup(name='opt-b')",
    )

    # Root project with optional deps pointing to opt_a and opt_b
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup; setup(name='root')",
    )
    req = tmp_path / "requirements.yaml"
    req.write_text(
        textwrap.dedent("""\
            channels:
              - conda-forge
            dependencies:
              - numpy
            optional_dependencies:
              extras:
                - ./opt_a
                - ./opt_b
            platforms:
              - linux-64
        """),
    )

    output = tmp_path / "pixi.toml"
    generate_pixi_toml(req, output_file=output, verbose=False)
    with output.open("rb") as f:
        data = tomllib.load(f)

    # The optional feature should exist with deps from the optional local subprojects
    assert "extras" in data["feature"]
    extras_deps = data["feature"]["extras"].get("dependencies", {})
    # pandas and polars come from opt_a and opt_b's requirements
    assert "pandas" in extras_deps or "polars" in extras_deps


def test_pixi_single_file_optional_group_demoted_universal(
    tmp_path: Path,
) -> None:
    """Cover line 856: optional group's own deps trigger demotion.

    When an optional group has both ``conda: click`` (universal) and
    ``pip: click >=2.0 # [linux64]``, the universal conda entry is demoted
    and restored as an explicit target for platforms that don't override it.
    """
    req = tmp_path / "requirements.yaml"
    req.write_text(
        textwrap.dedent("""\
            channels:
              - conda-forge
            dependencies:
              - numpy
            optional_dependencies:
              special:
                - conda: click
                - pip: click >=2.0  # [linux64]
            platforms:
              - linux-64
              - osx-arm64
        """),
    )
    output = tmp_path / "pixi.toml"
    generate_pixi_toml(req, output_file=output, verbose=False)
    with output.open("rb") as f:
        data = tomllib.load(f)

    special = data["feature"]["special"]
    # linux-64 should get pip click (the target-specific override)
    assert special["target"]["linux-64"]["pypi-dependencies"]["click"] == ">=2.0"
    # osx-arm64 should get conda click (restored from demotion)
    assert special["target"]["osx-arm64"]["dependencies"]["click"] == "*"
    # click should NOT appear in universal deps (it was demoted)
    assert "click" not in special.get("dependencies", {})
    assert "click" not in special.get("pypi-dependencies", {})


def test_pixi_monorepo_feature_demoted_universal(tmp_path: Path) -> None:
    """Cover lines 936 and 1074-1075: monorepo feature demotion + restore.

    When a monorepo feature has ``conda: requests`` (universal) and
    ``pip: requests >=2.0 # [linux64]``, the universal conda entry is demoted
    and restored as an explicit osx-arm64 target.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "requirements.yaml").write_text(
        textwrap.dedent("""\
            channels:
              - conda-forge
            dependencies:
              - conda: requests
              - pip: requests >=2.0  # [linux64]
            platforms:
              - linux-64
              - osx-arm64
        """),
    )
    proj2 = tmp_path / "proj2"
    proj2.mkdir()
    (proj2 / "requirements.yaml").write_text(
        textwrap.dedent("""\
            channels:
              - conda-forge
            dependencies:
              - pandas
            platforms:
              - linux-64
        """),
    )

    output = tmp_path / "pixi.toml"
    generate_pixi_toml(
        proj / "requirements.yaml",
        proj2 / "requirements.yaml",
        output_file=output,
        verbose=False,
    )
    with output.open("rb") as f:
        data = tomllib.load(f)

    proj_feature = data["feature"]["proj"]
    # linux-64 should get pip requests (the target-specific override)
    assert (
        proj_feature["target"]["linux-64"]["pypi-dependencies"]["requests"] == ">=2.0"
    )
    # osx-arm64 should get conda requests (restored from demotion)
    assert proj_feature["target"]["osx-arm64"]["dependencies"]["requests"] == "*"
    # requests should NOT appear in universal deps (it was demoted)
    assert "requests" not in proj_feature.get("dependencies", {})
    assert "requests" not in proj_feature.get("pypi-dependencies", {})
    # Second project should be present as a separate feature
    assert "pandas" in data["feature"]["proj2"]["dependencies"]


def test_pixi_monorepo_optional_group_demoted(tmp_path: Path) -> None:
    """Cover line 1002: monorepo optional group demotion.

    When a monorepo optional group has ``conda: click`` (universal) and
    ``pip: click >=2.0 # [linux64]``, the universal conda entry is demoted
    and restored as an explicit osx-arm64 target within the optional feature.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "requirements.yaml").write_text(
        textwrap.dedent("""\
            channels:
              - conda-forge
            dependencies:
              - numpy
            optional_dependencies:
              special:
                - conda: click
                - pip: click >=2.0  # [linux64]
            platforms:
              - linux-64
              - osx-arm64
        """),
    )
    proj2 = tmp_path / "proj2"
    proj2.mkdir()
    (proj2 / "requirements.yaml").write_text(
        textwrap.dedent("""\
            channels:
              - conda-forge
            dependencies:
              - pandas
            platforms:
              - linux-64
        """),
    )

    output = tmp_path / "pixi.toml"
    generate_pixi_toml(
        proj / "requirements.yaml",
        proj2 / "requirements.yaml",
        output_file=output,
        verbose=False,
    )
    with output.open("rb") as f:
        data = tomllib.load(f)

    opt_feature = data["feature"]["proj-special"]
    # linux-64 should get pip click (the target-specific override)
    assert opt_feature["target"]["linux-64"]["pypi-dependencies"]["click"] == ">=2.0"
    # osx-arm64 should get conda click (restored from demotion)
    assert opt_feature["target"]["osx-arm64"]["dependencies"]["click"] == "*"
    # click should NOT appear in universal deps (it was demoted)
    assert "click" not in opt_feature.get("dependencies", {})
    assert "click" not in opt_feature.get("pypi-dependencies", {})
    # The optional feature environment should compose correctly
    assert "proj-special" in data["environments"]
    assert "proj" in data["environments"]["proj-special"]
    assert "proj-special" in data["environments"]["proj-special"]


def test_pixi_single_file_env_name_collision(tmp_path: Path) -> None:
    """Optional groups whose names collide after underscore-to-hyphen normalization.

    Two groups ``foo_bar`` and ``foo-bar`` both normalize to ``foo-bar``.
    The second should get a disambiguated environment name instead of
    silently overwriting the first.
    """
    req = tmp_path / "requirements.yaml"
    req.write_text(
        textwrap.dedent("""\
            channels:
              - conda-forge
            dependencies:
              - numpy
            optional_dependencies:
              foo_bar:
                - pandas
              foo-bar:
                - polars
            platforms:
              - linux-64
        """),
    )
    output = tmp_path / "pixi.toml"
    generate_pixi_toml(req, output_file=output, verbose=False)
    with output.open("rb") as f:
        data = tomllib.load(f)

    envs = data["environments"]
    # Both features should be reachable via distinct environment names
    env_feature_lists = [v for k, v in envs.items() if k not in ("default", "all")]
    flat_features = [feat for lst in env_feature_lists for feat in lst]
    assert "foo_bar" in flat_features
    assert "foo-bar" in flat_features
    # There should be two separate environment entries (not one overwritten)
    assert len(env_feature_lists) == 2


def test_pixi_discover_graph_skips_non_list_optional_group(
    tmp_path: Path,
) -> None:
    """Cover line 469: optional group dep that is not a list."""
    req = tmp_path / "requirements.yaml"
    req.write_text(
        textwrap.dedent("""\
            dependencies:
              - numpy
            optional_dependencies:
              bad_group: "not a list"
            platforms:
              - linux-64
        """),
    )
    result = _discover_local_dependency_graph([req])
    assert len(result.roots) == 1
    # bad_group should be ignored
    assert not result.optional_group_graph


def test_pixi_discover_graph_skips_non_local_optional_dep(
    tmp_path: Path,
) -> None:
    """Cover line 481: optional dep with use != local via override side-effect."""
    proj = tmp_path / "proj"
    proj.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "setup.py").write_text(
        "from setuptools import setup; setup(name='other')",
    )
    (other / "requirements.yaml").write_text(
        textwrap.dedent("""\
            dependencies:
              - scipy
        """),
    )
    # local_dependencies with use:pypi populates the overrides dict via
    # _effective_local_dependencies, so the same dep in optional_dependencies
    # is resolved with use=pypi and skipped (line 481).
    (proj / "requirements.yaml").write_text(
        textwrap.dedent("""\
            dependencies:
              - numpy
            local_dependencies:
              - local: ../other
                use: pypi
                pypi: other-pkg
            optional_dependencies:
              extras:
                - ../other
            platforms:
              - linux-64
        """),
    )
    result = _discover_local_dependency_graph(
        [proj / "requirements.yaml"],
    )
    assert len(result.roots) == 1
    # other should NOT be in optional graph because use=pypi != local
    assert not result.optional_group_graph.get(result.roots[0], {}).get("extras", [])


def test_pixi_discover_graph_skips_non_installable_optional_unmanaged(
    tmp_path: Path,
) -> None:
    """Cover line 494: optional unmanaged dep that is not pip-installable."""
    proj = tmp_path / "proj"
    proj.mkdir()
    not_installable = tmp_path / "nosetup"
    not_installable.mkdir()
    # No setup.py or pyproject.toml → not pip-installable
    (proj / "requirements.yaml").write_text(
        textwrap.dedent("""\
            dependencies:
              - numpy
            optional_dependencies:
              extras:
                - ../nosetup
            platforms:
              - linux-64
        """),
    )
    result = _discover_local_dependency_graph(
        [proj / "requirements.yaml"],
    )
    assert len(result.roots) == 1
    # nosetup should not appear anywhere (not managed, not installable)
    assert not result.optional_group_graph.get(result.roots[0], {}).get("extras", [])
    assert not result.optional_group_unmanaged_graph.get(result.roots[0], {}).get(
        "extras",
        [],
    )


def test_restore_demoted_skips_when_still_in_universal(tmp_path: Path) -> None:
    """Cover restore skips when pkg is in universal deps or target."""
    req = tmp_path / "requirements.yaml"
    req.write_text(
        textwrap.dedent("""\
            channels:
              - conda-forge
            dependencies:
              - conda: numpy >=1.0
              - pip: numpy >=2.0  # [linux64]
              - conda: scipy
            platforms:
              - linux-64
              - osx-arm64
        """),
    )
    output = tmp_path / "pixi.toml"
    generate_pixi_toml(req, output_file=output, verbose=False)
    with output.open("rb") as f:
        data = tomllib.load(f)
    # scipy should remain universal (not demoted)
    assert "scipy" in data["dependencies"]


def test_pixi_monorepo_optional_local_feature_not_in_pixi_data(
    tmp_path: Path,
) -> None:
    """Cover line 1046: optional local dep feature not in pixi_data."""
    # Create a root project with an optional dep pointing to a local project
    # that has no dependencies at all (empty feature → not in pixi_data)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    (empty_dir / "requirements.yaml").write_text(
        textwrap.dedent("""\
            dependencies: []
        """),
    )

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "requirements.yaml").write_text(
        textwrap.dedent("""\
            channels:
              - conda-forge
            dependencies:
              - numpy
            optional_dependencies:
              extras:
                - ../empty
            platforms:
              - linux-64
        """),
    )

    proj2 = tmp_path / "proj2"
    proj2.mkdir()
    (proj2 / "requirements.yaml").write_text(
        textwrap.dedent("""\
            channels:
              - conda-forge
            dependencies:
              - pandas
            platforms:
              - linux-64
        """),
    )

    output = tmp_path / "pixi.toml"
    generate_pixi_toml(
        proj / "requirements.yaml",
        proj2 / "requirements.yaml",
        output_file=output,
        verbose=False,
    )
    with output.open("rb") as f:
        data = tomllib.load(f)
    # empty feature should NOT be in environments (feature was empty)
    for env_features in data.get("environments", {}).values():
        assert "empty" not in env_features


def test_restore_demoted_skips_pkg_still_in_conda_universal() -> None:
    """Line 1331: demoted pkg still present in universal dependencies."""
    section: dict[str, Any] = {"dependencies": {"click": "*"}}
    demoted: dict[str, tuple[str, str | dict[str, Any]]] = {
        "click": ("conda", ">=1.0"),
    }
    _restore_demoted_universals(section, demoted, ["linux-64"])
    # No target created because click is already in universal
    assert "target" not in section


def test_restore_demoted_skips_pkg_still_in_pip_universal() -> None:
    """Line 1333: demoted pkg still present in universal pypi-dependencies."""
    section: dict[str, Any] = {"pypi-dependencies": {"click": "*"}}
    demoted: dict[str, tuple[str, str | dict[str, Any]]] = {
        "click": ("pip", ">=1.0"),
    }
    _restore_demoted_universals(section, demoted, ["linux-64"])
    assert "target" not in section


def test_restore_demoted_skips_same_dep_type_in_target() -> None:
    """Line 1341: same dep type already in target for that platform."""
    section: dict[str, Any] = {
        "target": {"linux-64": {"dependencies": {"click": ">=2.0"}}},
    }
    demoted: dict[str, tuple[str, str | dict[str, Any]]] = {
        "click": ("conda", ">=1.0"),
    }
    _restore_demoted_universals(section, demoted, ["linux-64"])
    # Existing target conda dep should not be overwritten
    assert section["target"]["linux-64"]["dependencies"]["click"] == ">=2.0"
