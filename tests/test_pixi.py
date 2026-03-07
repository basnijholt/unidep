"""Tests for simple Pixi.toml generation."""

from __future__ import annotations

import copy
import os
import textwrap
from itertools import permutations
from typing import TYPE_CHECKING, Any

import pytest

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
    _reconcile_with_universal_deps,
    _resolve_conda_pip_conflict,
    _restore_demoted_universals,
    _unique_env_name,
    _unique_optional_feature_name,
    _version_spec_is_pinned,
    _with_unique_order_paths,
    generate_pixi_toml,
)
from unidep.utils import PathWithExtras

if TYPE_CHECKING:
    from pathlib import Path


_UNSET = object()


def _write_file(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content))
    return path


def _generate_and_load(
    output_file: Path,
    *requirements_files: Path,
    **kwargs: Any,
) -> dict[str, Any]:
    if "verbose" not in kwargs:
        kwargs["verbose"] = False
    generate_pixi_toml(*requirements_files, output_file=output_file, **kwargs)
    with output_file.open("rb") as f:
        return tomllib.load(f)


def _setup_app_lib_other(
    tmp_path: Path,
    app_optional_deps: str,
) -> tuple[Path, Path]:
    """Create app/lib/other monorepo layout and return (app_req, other_req)."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    deps_block = textwrap.indent(textwrap.dedent(app_optional_deps), "    ")
    yaml_content = (
        "channels:\n"
        "  - conda-forge\n"
        "dependencies:\n"
        "  - pandas\n"
        "optional_dependencies:\n"
        "  dev:\n"
        f"{deps_block}"
    )
    app_req = app_dir / "requirements.yaml"
    app_req.write_text(yaml_content)

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    _write_file(
        lib_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        """,
    )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other_req = _write_file(
        other_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - scipy
        """,
    )

    return app_req, other_req


def test_simple_pixi_generation(tmp_path: Path) -> None:
    """Test basic pixi.toml generation from a single requirements.yaml."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
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


def test_pixi_applies_top_level_overlay(tmp_path: Path) -> None:
    """Top-level ``pixi`` config should merge into the generated manifest."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        platforms:
          - linux-64
          - osx-arm64
        pixi:
          tasks:
            test: pytest -q
          pypi-options:
            dependency-overrides:
              requests: ">=2.32"
          workspace:
            description: Custom workspace metadata
            platforms:
              - linux-64
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    assert data["dependencies"]["numpy"] == "*"
    assert data["tasks"]["test"] == "pytest -q"
    assert data["pypi-options"]["dependency-overrides"]["requests"] == ">=2.32"
    assert data["workspace"]["description"] == "Custom workspace metadata"
    assert data["workspace"]["platforms"] == ["linux-64"]


def test_pixi_overlay_requires_mapping(tmp_path: Path) -> None:
    """Top-level ``pixi`` config must be a mapping."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        dependencies:
          - numpy
        pixi: true
        """,
    )

    with pytest.raises(TypeError, match=r"`pixi` section .* must be a mapping"):
        generate_pixi_toml(req_file, output_file=tmp_path / "pixi.toml", verbose=False)


def test_pixi_workspace_overlay_requires_mapping(tmp_path: Path) -> None:
    """``pixi.workspace`` must be a mapping."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        dependencies:
          - numpy
        pixi:
          workspace: invalid
        """,
    )

    with pytest.raises(TypeError, match=r"`pixi\.workspace` must be a mapping"):
        generate_pixi_toml(req_file, output_file=tmp_path / "pixi.toml", verbose=False)


def test_pixi_overlay_merges_into_generated_feature(tmp_path: Path) -> None:
    """Overlay data should merge recursively with generated feature tables."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        optional_dependencies:
          dev:
            - pytest
        pixi:
          feature:
            dev:
              tasks:
                test: pytest -q
              pypi-options:
                dependency-overrides:
                  urllib3: "<3"
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    assert data["feature"]["dev"]["dependencies"]["pytest"] == "*"
    assert data["feature"]["dev"]["tasks"]["test"] == "pytest -q"
    assert (
        data["feature"]["dev"]["pypi-options"]["dependency-overrides"]["urllib3"]
        == "<3"
    )


def test_pixi_overlay_merges_across_multiple_root_files(tmp_path: Path) -> None:
    """Multi-file generation should merge ``pixi`` overlays from root inputs."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    req_app = _write_file(
        app_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        pixi:
          tasks:
            lint: ruff check .
        """,
    )

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    req_lib = _write_file(
        lib_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        pixi:
          tasks:
            test: pytest -q
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_app, req_lib)

    assert data["tasks"]["lint"] == "ruff check ."
    assert data["tasks"]["test"] == "pytest -q"


def test_pixi_workspace_overlay_preserves_explicit_arguments(tmp_path: Path) -> None:
    """Explicit API arguments should win over ``pixi.workspace`` overrides."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        platforms:
          - osx-arm64
        pixi:
          workspace:
            name: from-overlay
            channels:
              - defaults
            platforms:
              - osx-arm64
            description: Custom workspace metadata
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        req_file,
        project_name="from-cli",
        channels=["conda-forge", "bioconda"],
        platforms=["linux-64"],
    )

    assert data["workspace"]["name"] == "from-cli"
    assert data["workspace"]["channels"] == ["conda-forge", "bioconda"]
    assert data["workspace"]["platforms"] == ["linux-64"]
    assert data["workspace"]["description"] == "Custom workspace metadata"


def test_pixi_workspace_overlay_supplies_channels_without_cli_override(
    tmp_path: Path,
) -> None:
    """Workspace overlay channels should apply when no explicit override is given."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        platforms:
          - linux-64
        pixi:
          workspace:
            channels:
              - defaults
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    assert data["workspace"]["channels"] == ["defaults"]


def test_pixi_reads_overlay_from_pyproject_toml(tmp_path: Path) -> None:
    """Structured overlays should also load from ``[tool.unidep.pixi]``."""
    req_file = _write_file(
        tmp_path / "pyproject.toml",
        """\
        [project]
        name = "demo-project"

        [tool.unidep]
        channels = ["conda-forge"]
        dependencies = ["numpy"]
        platforms = ["linux-64"]

        [tool.unidep.pixi.tasks]
        test = "pytest -q"

        [tool.unidep.pixi.workspace]
        description = "Overlay from TOML"
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    assert data["dependencies"]["numpy"] == "*"
    assert data["tasks"]["test"] == "pytest -q"
    assert data["workspace"]["description"] == "Overlay from TOML"
    assert data["workspace"]["platforms"] == ["linux-64"]


def test_pixi_single_file_empty_optional_group_skips_optional_environments(
    tmp_path: Path,
) -> None:
    """Empty optional groups should not create single-file optional env entries."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        optional_dependencies:
          dev: []
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    assert data["feature"] == {}
    assert data["environments"] == {}


def test_channels_resolution_behaviors(tmp_path: Path) -> None:
    """Explicit channels override file/default channels, while None falls back."""
    cases: list[tuple[str, str, object, list[str]]] = [
        (
            "override",
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
            ["defaults", "bioconda"],
            ["defaults", "bioconda"],
        ),
        (
            "fallback",
            """\
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
            _UNSET,
            ["conda-forge"],
        ),
        (
            "empty-explicit",
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
            [],
            [],
        ),
    ]

    for case_name, req_content, channels_arg, expected in cases:
        case_dir = tmp_path / case_name
        case_dir.mkdir()
        req_file = _write_file(case_dir / "requirements.yaml", req_content)
        output_file = case_dir / "pixi.toml"
        kwargs: dict[str, Any] = {}
        if channels_arg is not _UNSET:
            kwargs["channels"] = channels_arg

        data = _generate_and_load(output_file, req_file, **kwargs)
        assert data["workspace"]["channels"] == expected


def test_monorepo_pixi_generation(tmp_path: Path) -> None:
    """Test pixi.toml generation with features for multiple requirements files."""
    project1_dir = tmp_path / "project1"
    project1_dir.mkdir()
    req1 = _write_file(
        project1_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
          - conda: scipy
        """,
    )

    project2_dir = tmp_path / "project2"
    project2_dir.mkdir()
    req2 = _write_file(
        project2_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
          - pip: requests
        """,
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
    apps_req = _write_file(
        apps_api_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        """,
    )

    libs_api_dir = tmp_path / "libs" / "api"
    libs_api_dir.mkdir(parents=True)
    libs_req = _write_file(
        libs_api_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", apps_req, libs_req)

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
    root_req = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        """,
    )

    sub_dir = tmp_path / "project"
    sub_dir.mkdir()
    sub_req = _write_file(
        sub_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )

    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]
    output_file = tmp_path / "pixi.toml"
    data = _generate_and_load(
        output_file,
        root_req.relative_to(tmp_path),
        sub_req.relative_to(tmp_path),
    )

    features = data["feature"]
    assert len(features) == 2
    assert "" not in features
    assert all(name for name in features)


def test_pixi_with_version_pins(tmp_path: Path) -> None:
    """Test that version pins are passed through without resolution."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy >=1.20,<2.0
          - conda: scipy =1.9.0
          - pip: requests >2.20
          - sympy >= 1.11
        """,
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
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pip: pygsti =0.9.13.3
        """,
    )
    data = _generate_and_load(
        tmp_path / "pixi.toml",
        req_file,
    )

    assert data["pypi-dependencies"]["pygsti"] == "==0.9.13.3"


def test_pixi_prefers_pip_pin_over_unpinned_conda(tmp_path: Path) -> None:
    """Pinned pip spec should override unpinned conda spec."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
          - pip: foo >=1.2
            conda: foo
        """,
    )
    data = _generate_and_load(
        tmp_path / "pixi.toml",
        req_file,
    )

    assert data["dependencies"].get("foo") is None
    assert data["pypi-dependencies"]["foo"] == ">=1.2"


def test_pixi_prefers_conda_for_unpinned_both_sources(tmp_path: Path) -> None:
    """Unpinned dependencies available in both sources should use conda only."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
          - pandas
        """,
    )
    data = _generate_and_load(
        tmp_path / "pixi.toml",
        req_file,
    )

    deps = data["dependencies"]
    assert deps["numpy"] == "*"
    assert deps["pandas"] == "*"
    assert "pypi-dependencies" not in data


def test_pixi_prefers_conda_for_equally_pinned_both_sources(tmp_path: Path) -> None:
    """When conda and pip have the same pin, use conda only."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - scipy >=1.10
        """,
    )
    data = _generate_and_load(
        tmp_path / "pixi.toml",
        req_file,
    )

    assert data["dependencies"]["scipy"] == ">=1.10"
    assert "pypi-dependencies" not in data


# --- Parametrized single-platform conflict resolution tests ---


@pytest.mark.parametrize(
    (
        "deps_yaml",
        "in_universal",
        "in_universal_pypi",
        "in_target_deps",
        "in_target_pypi",
    ),
    [
        pytest.param(
            """\
            - click
            - pip: click ==0.1 # [linux64]
            """,
            None,
            None,
            None,
            "==0.1",
            id="universal-conda-target-pip",
        ),
        pytest.param(
            """\
            - conda: click >=8
            - pip: click ==0.1 # [linux64]
            """,
            None,
            None,
            None,
            "==0.1",
            id="universal-pinned-conda-target-pinned-pip-prefers-target",
        ),
        pytest.param(
            """\
            - conda: click >=8
            - pip: click # [linux64]
            """,
            ">=8",
            None,
            None,
            None,
            id="universal-conda-target-unpinned-pip-prefers-conda",
        ),
        pytest.param(
            """\
            - pip: click
            - conda: click >=8 # [linux64]
            """,
            None,
            None,
            ">=8",
            None,
            id="universal-pip-target-conda-prefers-conda-when-pinned",
        ),
        pytest.param(
            """\
            - pip: click ==0.1
            - conda: click # [linux64]
            """,
            None,
            "==0.1",
            None,
            None,
            id="universal-pip-target-conda-prefers-pip-when-pinned",
        ),
    ],
)
def test_pixi_reconciles_single_platform_conflict(
    tmp_path: Path,
    deps_yaml: str,
    in_universal: str | None,
    in_universal_pypi: str | None,
    in_target_deps: str | None,
    in_target_pypi: str | None,
) -> None:
    """Reconciliation of universal/target conda/pip conflicts on a single platform."""
    deps_block = textwrap.indent(textwrap.dedent(deps_yaml), "  ")
    yaml_content = (
        "channels:\n"
        "  - conda-forge\n"
        "dependencies:\n"
        f"{deps_block}"
        "platforms:\n"
        "  - linux-64\n"
    )
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(yaml_content)
    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    if in_universal is not None:
        assert data["dependencies"]["click"] == in_universal
    else:
        assert "click" not in data.get("dependencies", {})

    if in_universal_pypi is not None:
        assert data["pypi-dependencies"]["click"] == in_universal_pypi
    else:
        assert "click" not in data.get("pypi-dependencies", {})

    linux_target = data.get("target", {}).get("linux-64", {})
    if in_target_deps is not None:
        assert linux_target["dependencies"]["click"] == in_target_deps
    else:
        assert "click" not in linux_target.get("dependencies", {})

    if in_target_pypi is not None:
        assert linux_target["pypi-dependencies"]["click"] == in_target_pypi
    else:
        assert "click" not in linux_target.get("pypi-dependencies", {})


def test_pixi_reconcile_is_order_independent_for_universal_and_target_conflicts(
    tmp_path: Path,
) -> None:
    """Universal/target conflict reconciliation should not depend on declaration order."""
    req_target_then_universal = _write_file(
        tmp_path / "target_then_universal.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pip: click ==0.1 # [linux64]
          - conda: click >=8
        platforms:
          - linux-64
        """,
    )

    req_universal_then_target = _write_file(
        tmp_path / "universal_then_target.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - conda: click >=8
          - pip: click ==0.1 # [linux64]
        platforms:
          - linux-64
        """,
    )

    out1 = tmp_path / "pixi-target-then-universal.toml"
    out2 = tmp_path / "pixi-universal-then-target.toml"
    data1 = _generate_and_load(out1, req_target_then_universal)
    data2 = _generate_and_load(out2, req_universal_then_target)

    assert data1 == data2
    assert "click" not in data1.get("dependencies", {})
    assert data1["target"]["linux-64"]["pypi-dependencies"]["click"] == "==0.1"


def test_pixi_demoted_reconciliation_is_order_independent_with_repeated_universals(
    tmp_path: Path,
) -> None:
    """All declaration orders should yield the same reconciled demoted result."""
    deps = [
        "- conda: click >=8",
        "- pip: click ==0.1 # [linux64]",
        "- conda: click >=9",
    ]

    results = []
    for i, dep_order in enumerate(permutations(deps)):
        deps_block = "\n".join(f"  {dep}" for dep in dep_order)
        req_file = _write_file(
            tmp_path / f"requirements-{i}.yaml",
            (
                "channels:\n"
                "  - conda-forge\n"
                "dependencies:\n"
                f"{deps_block}\n"
                "platforms:\n"
                "  - linux-64\n"
                "  - osx-64\n"
            ),
        )
        data = _generate_and_load(tmp_path / f"pixi-{i}.toml", req_file)

        assert data["target"]["linux-64"]["pypi-dependencies"]["click"] == "==0.1"
        assert data["target"]["osx-64"]["dependencies"]["click"] == ">=9"
        assert "click" not in data.get("dependencies", {})
        assert "click" not in data.get("pypi-dependencies", {})
        results.append(data)

    assert all(result == results[0] for result in results[1:])


# --- Parametrized multiplatform conflict resolution tests ---


@pytest.mark.parametrize(
    ("deps_yaml", "linux_section", "linux_val", "osx_section", "osx_val"),
    [
        pytest.param(
            """\
            - conda: click >=8
            - pip: click ==0.1 # [linux64]
            """,
            "pypi-dependencies",
            "==0.1",
            "dependencies",
            ">=8",
            id="universal-conda-target-pip-multiplatform",
        ),
        pytest.param(
            """\
            - pip: click ==0.1
            - conda: click >=8 # [linux64]
            """,
            "dependencies",
            ">=8",
            "pypi-dependencies",
            "==0.1",
            id="universal-pip-target-conda-multiplatform",
        ),
    ],
)
def test_pixi_reconciles_multiplatform_conflict(
    tmp_path: Path,
    deps_yaml: str,
    linux_section: str,
    linux_val: str,
    osx_section: str,
    osx_val: str,
) -> None:
    """Universal deps should be promoted to non-overriding target platforms."""
    deps_block = textwrap.indent(textwrap.dedent(deps_yaml), "  ")
    yaml_content = (
        "channels:\n"
        "  - conda-forge\n"
        "dependencies:\n"
        f"{deps_block}"
        "platforms:\n"
        "  - linux-64\n"
        "  - osx-arm64\n"
    )
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(yaml_content)
    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    assert "click" not in data.get("dependencies", {})
    assert "click" not in data.get("pypi-dependencies", {})
    assert data["target"]["linux-64"][linux_section]["click"] == linux_val
    assert data["target"]["osx-arm64"][osx_section]["click"] == osx_val


def test_pixi_with_local_package(tmp_path: Path) -> None:
    """Test that local packages are added as editable dependencies."""
    project_dir = tmp_path / "my_package"
    project_dir.mkdir()

    _write_file(
        project_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        """,
    )

    _write_file(
        project_dir / "pyproject.toml",
        """\
        [build-system]
        requires = ["setuptools"]

        [project]
        name = "my-package"
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", project_dir)

    assert data["dependencies"]["numpy"] == "*"
    assert data["pypi-dependencies"]["my_package"] == {
        "path": "./my_package",
        "editable": True,
    }
    assert data["pypi-options"]["dependency-overrides"]["my-package"] == {
        "path": "./my_package",
        "editable": True,
    }


def test_pixi_single_file_editable_path_relative_to_output(tmp_path: Path) -> None:
    """Single-file mode should use editable path relative to output location."""
    project_dir = tmp_path / "services" / "api"
    project_dir.mkdir(parents=True)

    _write_file(
        project_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        """,
    )

    _write_file(
        project_dir / "pyproject.toml",
        """\
        [build-system]
        requires = ["setuptools"]

        [project]
        name = "service-api"
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", project_dir / "requirements.yaml")

    editable_dep = data["pypi-dependencies"]["service_api"]
    assert editable_dep["editable"] is True
    assert editable_dep["path"] == "./services/api"


def test_pixi_single_file_includes_local_dependency_package_as_editable(
    tmp_path: Path,
) -> None:
    """Single-file mode should install local dependency projects as editable packages."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    req_file = _write_file(
        app_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        local_dependencies:
          - ../lib
        """,
    )

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    _write_file(
        lib_dir / "requirements.yaml",
        """\
        dependencies:
          - pandas
        """,
    )
    _write_file(
        lib_dir / "pyproject.toml",
        """\
        [build-system]
        requires = ["setuptools"]

        [project]
        name = "lib"
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    assert data["dependencies"]["numpy"] == "*"
    assert data["dependencies"]["pandas"] == "*"
    lib_editable = data["pypi-dependencies"]["lib"]
    assert lib_editable["editable"] is True
    assert lib_editable["path"] == "./lib"
    assert data["pypi-options"]["dependency-overrides"]["lib"] == {
        "path": "./lib",
        "editable": True,
    }


def test_pixi_empty_dependencies(tmp_path: Path) -> None:
    """Test handling of requirements file with no dependencies."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        platforms:
          - linux-64
        """,
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()

    assert "[workspace]" in content
    assert "[dependencies]" not in content
    assert "[pypi-dependencies]" not in content


def test_pixi_with_platform_selectors(tmp_path: Path) -> None:
    """Test that platform selectors are converted to target sections."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
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
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        req_file,
        project_name="test-selectors",
    )

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
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
          - cuda-toolkit  # [linux64]
          - pip: pyobjc  # [osx]
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    assert "linux-64" in data["workspace"]["platforms"]
    assert any(p in data["workspace"]["platforms"] for p in ("osx-64", "osx-arm64"))
    assert data["target"]["linux-64"]["dependencies"]["cuda-toolkit"] == "*"
    osx_target = data["target"].get("osx-arm64") or data["target"].get("osx-64")
    assert osx_target is not None
    assert osx_target["pypi-dependencies"]["pyobjc"] == "*"


def test_pixi_with_multiple_platform_selectors(tmp_path: Path) -> None:
    """Test that broad selectors like 'unix' expand to multiple platforms."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
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
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        req_file,
        project_name="test-multi-platform",
    )

    assert data["dependencies"]["numpy"] == "*"
    assert "readline" not in data["dependencies"]
    assert "pywin32" not in data["dependencies"]
    assert data["target"]["linux-64"]["dependencies"]["readline"] == "*"
    assert data["target"]["osx-arm64"]["dependencies"]["readline"] == "*"
    assert data["target"]["win-64"]["dependencies"]["pywin32"] == "*"


def test_pixi_monorepo_with_platform_selectors(tmp_path: Path) -> None:
    """Test platform selectors in monorepo mode (multiple files)."""
    project1_dir = tmp_path / "project1"
    project1_dir.mkdir()
    req1 = _write_file(
        project1_dir / "requirements.yaml",
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
    )

    project2_dir = tmp_path / "project2"
    project2_dir.mkdir()
    req2 = _write_file(
        project2_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
          - pip: pyobjc  # [arm64]
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        req1,
        req2,
        project_name="monorepo-selectors",
    )

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
    project1_dir = tmp_path / "project1"
    project1_dir.mkdir()
    req1 = _write_file(
        project1_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        """,
    )
    _write_file(
        project1_dir / "pyproject.toml",
        """\
        [build-system]
        requires = ["setuptools"]

        [project]
        name = "project-one"
        """,
    )

    project2_dir = tmp_path / "project2"
    project2_dir.mkdir()
    req2 = _write_file(
        project2_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )
    _write_file(
        project2_dir / "pyproject.toml",
        """\
        [build-system]
        requires = ["setuptools"]

        [project]
        name = "project-two"
        """,
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
    req_app = _write_file(
        app_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        local_dependencies:
          - ../lib
        """,
    )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    req_other = _write_file(
        other_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    _write_file(
        lib_dir / "pyproject.toml",
        """\
        [build-system]
        requires = ["setuptools"]

        [project]
        name = "lib-pkg"
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_app, req_other)

    assert "lib" not in data["feature"]
    app_editable = data["feature"]["app"]["pypi-dependencies"]["lib_pkg"]
    assert app_editable["editable"] is True
    assert app_editable["path"] == "./lib"
    assert data["feature"]["app"]["pypi-options"]["dependency-overrides"][
        "lib-pkg"
    ] == {
        "path": "./lib",
        "editable": True,
    }


def test_pixi_monorepo_optional_unmanaged_deduped_against_base(
    tmp_path: Path,
) -> None:
    """Unmanaged local dep in both base and optional should only appear in base feature."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    _write_file(
        app_dir / "requirements.yaml",
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
    )

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    _write_file(
        lib_dir / "pyproject.toml",
        """\
        [build-system]
        requires = ["setuptools"]

        [project]
        name = "lib-pkg"
        """,
    )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    _write_file(
        other_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        app_dir / "requirements.yaml",
        other_dir / "requirements.yaml",
    )

    assert "lib_pkg" in data["feature"]["app"]["pypi-dependencies"]
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
    _write_file(
        app_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        optional_dependencies:
          dev:
            - ../lib
        """,
    )

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    _write_file(
        lib_dir / "pyproject.toml",
        """\
        [build-system]
        requires = ["setuptools"]

        [project]
        name = "lib-pkg"
        """,
    )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    _write_file(
        other_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        app_dir / "requirements.yaml",
        other_dir / "requirements.yaml",
    )

    opt_feature_name = "app-dev"
    assert opt_feature_name in data["feature"], (
        f"Expected feature '{opt_feature_name}' for unmanaged-only optional group"
    )
    opt_pypi = data["feature"][opt_feature_name].get("pypi-dependencies", {})
    assert "lib_pkg" in opt_pypi
    assert opt_pypi["lib_pkg"]["editable"] is True

    env_name = opt_feature_name.replace("_", "-")
    assert env_name in data["environments"]
    assert opt_feature_name in data["environments"][env_name]


def test_pixi_monorepo_editable_paths_use_project_paths(tmp_path: Path) -> None:
    """Editable paths should point to project dirs, not derived feature names."""
    apps_api_dir = tmp_path / "apps" / "api"
    apps_api_dir.mkdir(parents=True)
    _write_file(
        apps_api_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        """,
    )
    _write_file(
        apps_api_dir / "pyproject.toml",
        """\
        [build-system]
        requires = ["setuptools"]

        [project]
        name = "apps-api"
        """,
    )

    libs_api_dir = tmp_path / "libs" / "api"
    libs_api_dir.mkdir(parents=True)
    _write_file(
        libs_api_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )
    _write_file(
        libs_api_dir / "pyproject.toml",
        """\
        [build-system]
        requires = ["setuptools"]

        [project]
        name = "libs-api"
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        apps_api_dir / "requirements.yaml",
        libs_api_dir / "requirements.yaml",
    )

    editable_paths = {
        dep_data["path"]
        for feature in data["feature"].values()
        for dep_data in feature.get("pypi-dependencies", {}).values()
        if isinstance(dep_data, dict) and dep_data.get("editable") is True
    }
    assert editable_paths == {"./apps/api", "./libs/api"}


def test_pixi_monorepo_shared_local_file_becomes_single_feature(tmp_path: Path) -> None:
    """Shared local requirements should be represented as a separate feature."""
    _write_file(
        tmp_path / "dev-requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pytest
        """,
    )

    project1_dir = tmp_path / "project1"
    project1_dir.mkdir()
    req1 = _write_file(
        project1_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        local_dependencies:
          - ../dev-requirements.yaml
        """,
    )

    project2_dir = tmp_path / "project2"
    project2_dir.mkdir()
    req2 = _write_file(
        project2_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        local_dependencies:
          - ../dev-requirements.yaml
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req1, req2)

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
    _write_file(
        project_c / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - sympy
        """,
    )

    project_b = tmp_path / "project_b"
    project_b.mkdir()
    _write_file(
        project_b / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        local_dependencies:
          - ../project_c
        """,
    )

    project_a = tmp_path / "project_a"
    project_a.mkdir()
    req_a = _write_file(
        project_a / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        local_dependencies:
          - ../project_b
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        req_a,
        project_c / "requirements.yaml",
    )

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
    req1 = _write_file(
        project1 / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        local_dependencies:
          - ../wheels/example-0.1.0-py3-none-any.whl
        """,
    )

    project2 = tmp_path / "project2"
    project2.mkdir()
    req2 = _write_file(
        project2 / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req1, req2)

    assert set(data["feature"]) == {"project1", "project2"}


def test_pixi_single_file_local_dependency_use_modes(tmp_path: Path) -> None:
    """`use: pypi` should add pip dep, while `use: skip` should add nothing."""
    pypi_local = tmp_path / "pypi_local"
    pypi_local.mkdir()
    _write_file(
        pypi_local / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )

    skipped_local = tmp_path / "skipped_local"
    skipped_local.mkdir()
    _write_file(
        skipped_local / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - scipy
        """,
    )

    req_file = _write_file(
        tmp_path / "requirements.yaml",
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
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    assert data["dependencies"]["numpy"] == "*"
    assert "pandas" not in data["dependencies"]
    assert "scipy" not in data["dependencies"]
    assert data["pypi-dependencies"]["pypi-local-package"] == ">=1.2"
    assert "skipped_local" not in data.get("pypi-dependencies", {})
    assert "target" not in data


def test_pixi_with_directory_input(tmp_path: Path) -> None:
    """Test passing a directory instead of a file."""
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    _write_file(
        project_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        platforms:
          - linux-64
        """,
    )

    output_file = tmp_path / "pixi.toml"
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
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        """,
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

    _write_file(
        project_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        """,
    )

    _write_file(
        project_dir / "pyproject.toml",
        """\
        [build-system]
        requires = ["setuptools"]
        """,
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        project_dir,
        output_file=output_file,
        verbose=False,
    )

    content = output_file.read_text()
    assert "my_fallback_pkg" in content


def test_pixi_filtering_removes_empty_targets(tmp_path: Path) -> None:
    """Test that filtering removes targets entirely when no platforms match."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
          - cuda-toolkit  # [linux64]
        platforms:
          - osx-arm64
        """,
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        output_file=output_file,
        verbose=False,
    )

    content = output_file.read_text()
    assert "cuda-toolkit" not in content
    assert "[target." not in content


def test_pixi_stdout_output(tmp_path: Path, capsys: object) -> None:
    """Test output to stdout when output_file is None."""
    _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        platforms:
          - linux-64
        """,
    )

    generate_pixi_toml(
        tmp_path / "requirements.yaml",
        output_file=None,
        verbose=False,
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert 'numpy = "*"' in captured.out
    assert "[workspace]" in captured.out


def test_pixi_monorepo_with_directory_input(tmp_path: Path) -> None:
    """Test monorepo mode passing directories instead of files."""
    project1_dir = tmp_path / "proj1"
    project1_dir.mkdir()
    _write_file(
        project1_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        """,
    )

    project2_dir = tmp_path / "proj2"
    project2_dir.mkdir()
    _write_file(
        project2_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        project1_dir,
        project2_dir,
        project_name="monorepo-dirs",
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()
    assert "[feature.proj1.dependencies]" in content
    assert "[feature.proj2.dependencies]" in content


def test_pixi_monorepo_filtering_removes_empty_feature_targets(tmp_path: Path) -> None:
    """Test that filtering removes empty feature targets in monorepo mode."""
    project1_dir = tmp_path / "project1"
    project1_dir.mkdir()
    req1 = _write_file(
        project1_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
          - cuda-toolkit  # [linux64]
        platforms:
          - osx-arm64
        """,
    )

    project2_dir = tmp_path / "project2"
    project2_dir.mkdir()
    req2 = _write_file(
        project2_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
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
    assert "cuda-toolkit" not in content
    assert "[feature.project1.dependencies]" in content
    assert "[feature.project1.target" not in content


def test_pixi_default_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that generate_pixi_toml uses cwd when no args provided."""
    _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        platforms:
          - linux-64
        """,
    )

    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        output_file=output_file,
        verbose=False,
    )

    assert output_file.exists()
    content = output_file.read_text()
    assert 'numpy = "*"' in content


def test_pixi_optional_dependencies_single_file(tmp_path: Path) -> None:
    """Test optional dependencies with realistic user scenario."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
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

    assert "[dependencies]" in content
    assert 'numpy = ">=1.20"' in content

    assert "[feature.dev.dependencies]" in content
    assert 'pytest = ">=7.0"' in content
    assert "[feature.dev.pypi-dependencies]" in content
    assert 'black = "*"' in content
    assert "[feature.dev.target.linux-64.dependencies]" in content
    assert "[feature.dev.target.win-64.dependencies]" in content

    assert "[feature.docs.dependencies]" in content
    assert 'sphinx = "*"' in content

    assert "[environments]" in content
    assert "default = []" in content
    assert "dev = [" in content
    assert "docs = [" in content
    assert "all = [" in content


def test_pixi_optional_dependencies_single_group(tmp_path: Path) -> None:
    """Test single optional group doesn't create 'all' environment."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
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
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        req_file,
        project_name="test-project",
        output_file=output_file,
        verbose=False,
    )

    content = output_file.read_text()

    assert "[feature.test.dependencies]" in content
    assert 'pytest = "*"' in content
    assert "all = [" not in content


def test_pixi_single_file_optional_group_named_all_keeps_unique_env(
    tmp_path: Path,
) -> None:
    """A user-defined optional group named 'all' should not be overwritten."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        optional_dependencies:
          all:
            - pandas
          dev:
            - pytest
        platforms:
          - linux-64
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    assert "all" in data["feature"]
    assert "dev" in data["feature"]

    envs = data["environments"]
    assert envs["all"] == ["all", "dev"]
    user_all_envs = [name for name, feats in envs.items() if feats == ["all"]]
    assert len(user_all_envs) == 1
    assert user_all_envs[0] != "all"


def test_pixi_single_file_optional_local_dependency_stays_optional(
    tmp_path: Path,
) -> None:
    """Optional local deps should appear in optional features, not root deps."""
    local_dep_dir = tmp_path / "localdep"
    local_dep_dir.mkdir()
    _write_file(
        local_dep_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )

    root_req = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        optional_dependencies:
          dev:
            - ./localdep
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", root_req)

    assert data["dependencies"]["numpy"] == "*"
    assert "pandas" not in data.get("dependencies", {})
    assert data["feature"]["dev"]["dependencies"]["pandas"] == "*"
    assert data["environments"]["default"] == []
    assert data["environments"]["dev"] == ["dev"]


def test_pixi_optional_dependencies_monorepo(tmp_path: Path) -> None:
    """Test optional dependencies in monorepo setup."""
    project1_dir = tmp_path / "project1"
    project1_dir.mkdir()
    req1 = _write_file(
        project1_dir / "requirements.yaml",
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
    )

    project2_dir = tmp_path / "project2"
    project2_dir.mkdir()
    req2 = _write_file(
        project2_dir / "requirements.yaml",
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

    assert "[feature.project1.dependencies]" in content
    assert 'numpy = "*"' in content
    assert "[feature.project2.dependencies]" in content
    assert 'pandas = "*"' in content

    assert "[feature.project1-test.dependencies]" in content
    assert 'pytest = "*"' in content
    assert "[feature.project2-lint.dependencies]" in content
    assert 'black = "*"' in content


def test_pixi_monorepo_optional_local_dependency_is_only_in_optional_env(
    tmp_path: Path,
) -> None:
    """Optional local projects should be included only in the optional env."""
    app_req, other_req = _setup_app_lib_other(
        tmp_path,
        """\
        - ../lib
        - pytest
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", app_req, other_req)

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
    app_req, other_req = _setup_app_lib_other(
        tmp_path,
        """\
        - ../lib
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", app_req, other_req)

    features = data["feature"]
    envs = data["environments"]

    assert "app" in features
    assert "lib" in features
    assert "other" in features
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
    _write_file(
        project_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        optional_dependencies:
          dev:
            - pytest
        """,
    )

    project_dev_dir = tmp_path / "project-dev"
    project_dev_dir.mkdir()
    _write_file(
        project_dev_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        project_dir / "requirements.yaml",
        project_dev_dir / "requirements.yaml",
    )

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
    req1 = _write_file(
        project1_dir / "requirements.yaml",
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
    )

    project2_dir = tmp_path / "project2"
    project2_dir.mkdir()
    req2 = _write_file(
        project2_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        platforms:
          - linux-64
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        req1,
        req2,
        project_name="monorepo",
    )

    envs = data["environments"]
    assert set(envs["default"]) == {"project1", "project2"}
    assert "project1-dev" not in envs["default"]
    assert set(envs["project1-dev"]) == {"project1", "project1-dev"}


def test_pixi_empty_platform_override_uses_file_platforms(tmp_path: Path) -> None:
    """Passing platforms=[] should fall back to platforms from requirements files."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        platforms:
          - linux-64
          - osx-arm64
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        req_file,
        platforms=[],
    )

    assert set(data["workspace"]["platforms"]) == {"linux-64", "osx-arm64"}


def test_pixi_monorepo_keeps_optional_groups_when_base_feature_empty(
    tmp_path: Path,
) -> None:
    """Optional sub-features should be preserved even when base feature is empty."""
    project1 = tmp_path / "project1"
    project1.mkdir()
    req1 = _write_file(
        project1 / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies: []
        optional_dependencies:
          docs:
            - sphinx
        """,
    )

    project2 = tmp_path / "project2"
    project2.mkdir()
    req2 = _write_file(
        project2 / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req1, req2)

    features = data["feature"]
    assert "project1" not in features
    assert features["project1-docs"]["dependencies"]["sphinx"] == "*"
    assert "project2" in features

    envs = data["environments"]
    assert envs["default"] == ["project2"]
    assert envs["project1-docs"] == ["project1-docs"]


def test_pixi_monorepo_skips_empty_optional_feature_group(tmp_path: Path) -> None:
    """Empty optional groups should not create empty sub-features."""
    project1 = tmp_path / "project1"
    project1.mkdir()
    req1 = _write_file(
        project1 / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        optional_dependencies:
          docs:
            - pytest
        """,
    )

    project2 = tmp_path / "project2"
    project2.mkdir()
    req2 = _write_file(
        project2 / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        req1,
        req2,
        skip_dependencies=["pytest"],
    )

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

    original_relpath = os.path.relpath

    def raising_relpath(_path: Any, _start: Any = None) -> str:
        msg = "path is on mount 'C:', start on mount 'D:'"
        raise ValueError(msg)

    monkeypatch.setattr(os.path, "relpath", raising_relpath)
    result = _editable_dependency_path(project_dir, output)
    assert project_dir.resolve().as_posix() == result

    monkeypatch.setattr(os.path, "relpath", original_relpath)
    assert _editable_dependency_path(project_dir, output) == "./pkg"


def test_discover_local_dependency_graph_skips_non_local_and_missing(
    tmp_path: Path,
) -> None:
    """Graph discovery should ignore skipped/pypi/missing local entries safely."""
    root = tmp_path / "root"
    root.mkdir()
    req = _write_file(
        root / "requirements.yaml",
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
    )

    result = _discover_local_dependency_graph([req])
    assert result.roots == result.discovered
    assert len(result.roots) == 1
    assert result.graph[result.roots[0]] == []
    assert result.optional_group_graph == {}
    assert result.unmanaged_local_graph[result.roots[0]] == []
    assert result.optional_group_unmanaged_graph == {}


# --- Parametrized _parse_direct_requirements_for_node tests ---


@pytest.mark.parametrize(
    ("extras", "req_content", "expected_in"),
    [
        pytest.param(
            ["dev"],
            """\
            dependencies:
              - numpy
            optional_dependencies:
              dev:
                - pytest
            """,
            ["numpy", "pytest"],
            id="selected-extras",
        ),
        pytest.param(
            ["*"],
            """\
            dependencies:
              - numpy
            optional_dependencies:
              dev:
                - pytest
              docs:
                - sphinx
            """,
            ["numpy", "pytest", "sphinx"],
            id="star-extra",
        ),
    ],
)
def test_parse_direct_requirements_for_node_extras(
    tmp_path: Path,
    extras: list[str],
    req_content: str,
    expected_in: list[str],
) -> None:
    """Extras on a local node should merge into required dependencies."""
    req = _write_file(tmp_path / "requirements.yaml", req_content)
    node = PathWithExtras(req, extras)
    parsed = _parse_direct_requirements_for_node(
        node,
        verbose=False,
        ignore_pins=None,
        skip_dependencies=None,
        overwrite_pins=None,
    )
    for name in expected_in:
        assert name in parsed.requirements
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
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - conda: qsimcirq >=0.21.0 cuda*  # [linux64]
          - gcc =11
        platforms:
          - linux-64
        """,
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    content = output_file.read_text()

    assert "[target.linux-64.dependencies.qsimcirq]" in content
    assert 'version = ">=0.21.0"' in content
    assert 'build = "cuda*"' in content
    assert 'gcc = "=11"' in content


def test_pixi_with_pip_extras(tmp_path: Path) -> None:
    """Test pixi.toml generation with pip extras."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pip: pipefunc[extras]
          - pip: package[dev,test] >=1.0
        platforms:
          - linux-64
        """,
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    content = output_file.read_text()

    assert "[pypi-dependencies.pipefunc]" in content
    assert 'version = "*"' in content
    assert '"extras"' in content

    assert "[pypi-dependencies.package]" in content
    assert 'version = ">=1.0"' in content
    assert '"dev"' in content
    assert '"test"' in content


# --- Parametrized _merge_version_specs tests ---


@pytest.mark.parametrize(
    ("existing", "new", "pkg", "expected"),
    [
        # Simple merges
        pytest.param(
            ">=1.7,<2",
            "<1.16",
            "scipy",
            ">=1.7,<1.16",
            id="merge-tighter-upper",
        ),
        pytest.param(">=1.0", "<2.0", "pkg", ">=1.0,<2.0", id="merge-lower-upper"),
        pytest.param(">=1.0", ">=2.0", "pkg", ">=2.0", id="merge-tighter-lower"),
        # Build strings
        pytest.param(
            {"version": ">=1.0", "build": "cuda*"},
            ">=2.0",
            "pkg",
            {"version": ">=1.0", "build": "cuda*"},
            id="keep-existing-with-build",
        ),
        pytest.param(
            ">=1.0",
            {"version": ">=2.0", "build": "py310*"},
            "pkg",
            {"version": ">=2.0", "build": "py310*"},
            id="use-new-with-build",
        ),
        # Extras
        pytest.param(
            {"version": ">=1.0", "extras": ["dev"]},
            {"version": "<2.0", "extras": ["test"]},
            "pkg",
            {"version": ">=1.0,<2.0", "extras": ["dev", "test"]},
            id="merge-extras",
        ),
        # Conflicts
        pytest.param(">=2.0", "<1.0", "pkg", ">=2.0,<1.0", id="conflict-fallback"),
        pytest.param("<1.0", ">=2.0", "pkg", ">=2.0,<1.0", id="conflict-reverse-order"),
        pytest.param("==1.0", ">=2.0", "pkg", "==1.0,>=2.0", id="exact-pin-conflict"),
        pytest.param(">=2.0", "*", "pkg", ">=2.0", id="new-is-star"),
        # Existing star
        pytest.param("*", ">=1.0", "pkg", ">=1.0", id="existing-star"),
    ],
)
def test_merge_version_specs(
    existing: str | dict[str, Any],
    new: str | dict[str, Any],
    pkg: str,
    expected: str | dict[str, Any],
) -> None:
    """Test _merge_version_specs handles various merge scenarios."""
    result = _merge_version_specs(existing, new, pkg)
    if isinstance(expected, dict):
        assert isinstance(result, dict)
        assert result["version"] == expected["version"]
        if "extras" in expected:
            assert result["extras"] == expected["extras"]
        if "build" in expected:
            assert result["build"] == expected["build"]
    else:
        assert result == expected


def test_pixi_with_merged_constraints(tmp_path: Path) -> None:
    """Test pixi.toml generation merges version constraints."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
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
    )

    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(req_file, output_file=output_file, verbose=False)

    content = output_file.read_text()

    assert 'scipy = ">=1.7,<1.16"' in content
    assert 'numpy = ">=1.20,<2.0"' in content


def test_pixi_optional_local_dep_does_not_leak_base_local_deps(
    tmp_path: Path,
) -> None:
    """Base local deps must not appear in optional features."""
    lib1 = tmp_path / "lib1"
    lib1.mkdir()
    _write_file(
        lib1 / "requirements.yaml",
        """\
        dependencies:
          - pandas
          - scipy
        """,
    )

    lib2 = tmp_path / "lib2"
    lib2.mkdir()
    _write_file(
        lib2 / "requirements.yaml",
        """\
        dependencies:
          - requests
        """,
    )

    root_req = _write_file(
        tmp_path / "requirements.yaml",
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
    )

    data = _generate_and_load(tmp_path / "pixi.toml", root_req)

    root_deps = set(data.get("dependencies", {}).keys())
    dev_deps = set(data["feature"]["dev"].get("dependencies", {}).keys())

    assert "pandas" in root_deps
    assert "scipy" in root_deps
    assert "pandas" not in dev_deps, "base local dep leaked into optional feature"
    assert "scipy" not in dev_deps, "base local dep leaked into optional feature"
    assert "requests" in dev_deps
    assert "pytest" in dev_deps


# --- Parametrized demotion weak-target tests ---


@pytest.mark.parametrize(
    ("deps_yaml", "osx_dep_section", "osx_click_val"),
    [
        pytest.param(
            """\
            - conda: click >=8
            - pip: click ==0.1  # [linux64]
            - pip: click        # [osx64]
            """,
            "dependencies",
            ">=8",
            id="pinned-demoted-replaces-weak-target",
        ),
        pytest.param(
            """\
            - conda: click
            - pip: click ==0.1  # [linux64]
            - pip: click        # [osx64]
            """,
            "dependencies",
            "*",
            id="unpinned-demoted-reapplies-conflict-policy",
        ),
    ],
)
def test_pixi_demoted_universal_weak_target(
    tmp_path: Path,
    deps_yaml: str,
    osx_dep_section: str,
    osx_click_val: str,
) -> None:
    """Demoted universals should replace weak target overrides correctly."""
    deps_block = textwrap.indent(textwrap.dedent(deps_yaml), "  ")
    yaml_content = (
        "channels:\n"
        "  - conda-forge\n"
        "dependencies:\n"
        f"{deps_block}"
        "platforms:\n"
        "  - linux-64\n"
        "  - osx-64\n"
    )
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(yaml_content)

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    linux = data["target"]["linux-64"]
    assert linux["pypi-dependencies"]["click"] == "==0.1"

    osx = data["target"]["osx-64"]
    assert osx[osx_dep_section]["click"] == osx_click_val
    assert "click" not in osx.get("pypi-dependencies", {})


def test_pixi_demoted_universal_uses_latest_merged_constraint(
    tmp_path: Path,
) -> None:
    """Repeated universal specs must not leave a stale weaker constraint in demoted."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - conda: click >=8
          - pip: click ==0.1  # [linux64]
          - conda: click >=9
        platforms:
          - linux-64
          - osx-64
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    # linux-64 should keep the target-specific pip override
    assert data["target"]["linux-64"]["pypi-dependencies"]["click"] == "==0.1"

    # osx-64 must get the final merged constraint (>=9), NOT the stale first (>=8)
    assert data["target"]["osx-64"]["dependencies"]["click"] == ">=9"
    assert "click" not in data["target"]["osx-64"].get("pypi-dependencies", {})

    # Universal should be empty (demoted to per-platform targets)
    assert "click" not in data.get("dependencies", {})
    assert "click" not in data.get("pypi-dependencies", {})


def test_pixi_demoted_universal_merges_constraints_across_demotions(
    tmp_path: Path,
) -> None:
    """Demoted universal constraints should keep cumulative merged bounds."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - conda: click >=8
          - pip: click ==0.1  # [linux64]
          - conda: click <=10
        platforms:
          - linux-64
          - osx-64
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    assert data["target"]["linux-64"]["pypi-dependencies"]["click"] == "==0.1"
    expected = _merge_version_specs(">=8", "<=10", "click")
    assert isinstance(expected, str)
    assert data["target"]["osx-64"]["dependencies"]["click"] == expected


def test_pixi_demoted_universal_switches_source_when_conflict_direction_flips(
    tmp_path: Path,
) -> None:
    """Later demotion of same package from the other source should replace source type."""
    req_file = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - conda: click >=8
          - pip: click ==0.1 # [linux64]
          - pip: click >=8
          - conda: click >=9 # [linux64]
        platforms:
          - linux-64
          - osx-64
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req_file)

    # linux keeps the stronger target-specific conda override
    assert data["target"]["linux-64"]["dependencies"]["click"] == ">=9"
    # osx restores the latest demoted universal pip spec
    assert data["target"]["osx-64"]["pypi-dependencies"]["click"] == ">=8"
    assert "click" not in data["target"]["osx-64"].get("dependencies", {})


def test_reconcile_with_universal_deps_without_demotion_tracking() -> None:
    """Reconciliation should work even when demotion tracking is disabled."""
    platform_deps: Any = {
        None: ({"click": ">=8"}, {}),
        "linux-64": ({}, {"click": "==0.1"}),
    }

    _reconcile_with_universal_deps(
        platform_deps,
        platform=None,
        base_name="click",
    )

    assert "click" not in platform_deps[None][0]
    assert platform_deps["linux-64"][1]["click"] == "==0.1"


def test_parse_version_build_whitespace_only() -> None:
    assert _parse_version_build("  ") == "*"


def test_make_pip_version_spec_dict_with_extras() -> None:
    result = _make_pip_version_spec({"version": ">=1.0", "build": "py3*"}, ["extra1"])
    assert result == {"version": ">=1.0", "build": "py3*", "extras": ["extra1"]}


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


def test_unique_env_name_triple_collision() -> None:
    taken: set[str] = {"foo-bar", "foo-bar-2"}
    assert _unique_env_name("foo_bar", taken) == "foo-bar-3"


def test_pixi_single_file_optional_local_dep_transitive_dedup(
    tmp_path: Path,
) -> None:
    """Cover single-file optional local dep dedup and pip-installable path."""
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    _write_file(
        shared_dir / "requirements.yaml",
        """\
        dependencies:
          - scipy
    """,
    )
    (shared_dir / "setup.py").write_text(
        "from setuptools import setup; setup(name='shared')",
    )

    opt_a_dir = tmp_path / "opt_a"
    opt_a_dir.mkdir()
    _write_file(
        opt_a_dir / "requirements.yaml",
        """\
        dependencies:
          - pandas
        local_dependencies:
          - ../shared
    """,
    )
    (opt_a_dir / "setup.py").write_text(
        "from setuptools import setup; setup(name='opt-a')",
    )

    opt_b_dir = tmp_path / "opt_b"
    opt_b_dir.mkdir()
    _write_file(
        opt_b_dir / "requirements.yaml",
        """\
        dependencies:
          - polars
        local_dependencies:
          - ../shared
    """,
    )
    (opt_b_dir / "setup.py").write_text(
        "from setuptools import setup; setup(name='opt-b')",
    )

    (tmp_path / "setup.py").write_text(
        "from setuptools import setup; setup(name='root')",
    )
    req = _write_file(
        tmp_path / "requirements.yaml",
        """\
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
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", req)

    assert "extras" in data["feature"]
    extras_deps = data["feature"]["extras"].get("dependencies", {})
    assert "pandas" in extras_deps or "polars" in extras_deps


def test_pixi_single_file_optional_group_demoted_universal(
    tmp_path: Path,
) -> None:
    """Cover optional group's own deps trigger demotion."""
    req = _write_file(
        tmp_path / "requirements.yaml",
        """\
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
        """,
    )
    data = _generate_and_load(tmp_path / "pixi.toml", req)

    special = data["feature"]["special"]
    assert special["target"]["linux-64"]["pypi-dependencies"]["click"] == ">=2.0"
    assert special["target"]["osx-arm64"]["dependencies"]["click"] == "*"
    assert "click" not in special.get("dependencies", {})
    assert "click" not in special.get("pypi-dependencies", {})


# --- Parametrized monorepo demotion tests ---


@pytest.mark.parametrize(
    (
        "proj_deps",
        "proj_feature_key",
        "universal_pkg",
        "linux_pip_val",
        "osx_conda_val",
    ),
    [
        pytest.param(
            """\
            channels:
              - conda-forge
            dependencies:
              - conda: requests
              - pip: requests >=2.0  # [linux64]
            platforms:
              - linux-64
              - osx-arm64
            """,
            "proj",
            "requests",
            ">=2.0",
            "*",
            id="feature-demoted-universal",
        ),
        pytest.param(
            """\
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
            """,
            "proj-special",
            "click",
            ">=2.0",
            "*",
            id="optional-group-demoted",
        ),
    ],
)
def test_pixi_monorepo_demotion(
    tmp_path: Path,
    proj_deps: str,
    proj_feature_key: str,
    universal_pkg: str,
    linux_pip_val: str,
    osx_conda_val: str,
) -> None:
    """Monorepo feature/optional-group demotion + restore."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_file(proj / "requirements.yaml", proj_deps)

    proj2 = tmp_path / "proj2"
    proj2.mkdir()
    _write_file(
        proj2 / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        platforms:
          - linux-64
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        proj / "requirements.yaml",
        proj2 / "requirements.yaml",
    )

    feature = data["feature"][proj_feature_key]
    assert (
        feature["target"]["linux-64"]["pypi-dependencies"][universal_pkg]
        == linux_pip_val
    )
    assert (
        feature["target"]["osx-arm64"]["dependencies"][universal_pkg] == osx_conda_val
    )
    assert universal_pkg not in feature.get("dependencies", {})
    assert universal_pkg not in feature.get("pypi-dependencies", {})
    assert "pandas" in data["feature"]["proj2"]["dependencies"]


def test_pixi_single_file_env_name_collision(tmp_path: Path) -> None:
    """Optional groups whose names collide after underscore-to-hyphen normalization."""
    req = _write_file(
        tmp_path / "requirements.yaml",
        """\
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
        """,
    )
    data = _generate_and_load(tmp_path / "pixi.toml", req)

    envs = data["environments"]
    env_feature_lists = [v for k, v in envs.items() if k not in ("default", "all")]
    flat_features = [feat for lst in env_feature_lists for feat in lst]
    assert "foo_bar" in flat_features
    assert "foo-bar" in flat_features
    assert len(env_feature_lists) == 2


def test_pixi_discover_graph_skips_non_list_optional_group(
    tmp_path: Path,
) -> None:
    """Cover optional group dep that is not a list."""
    req = _write_file(
        tmp_path / "requirements.yaml",
        """\
        dependencies:
          - numpy
        optional_dependencies:
          bad_group: "not a list"
        platforms:
          - linux-64
        """,
    )
    result = _discover_local_dependency_graph([req])
    assert len(result.roots) == 1
    assert not result.optional_group_graph


def test_pixi_discover_graph_skips_non_local_optional_dep(
    tmp_path: Path,
) -> None:
    """Cover optional dep with use != local via override side-effect."""
    proj = tmp_path / "proj"
    proj.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "setup.py").write_text(
        "from setuptools import setup; setup(name='other')",
    )
    _write_file(
        other / "requirements.yaml",
        """\
        dependencies:
          - scipy
        """,
    )
    _write_file(
        proj / "requirements.yaml",
        """\
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
        """,
    )
    result = _discover_local_dependency_graph(
        [proj / "requirements.yaml"],
    )
    assert len(result.roots) == 1
    assert not result.optional_group_graph.get(result.roots[0], {}).get("extras", [])


def test_pixi_discover_graph_skips_non_installable_optional_unmanaged(
    tmp_path: Path,
) -> None:
    """Cover optional unmanaged dep that is not pip-installable."""
    proj = tmp_path / "proj"
    proj.mkdir()
    not_installable = tmp_path / "nosetup"
    not_installable.mkdir()
    _write_file(
        proj / "requirements.yaml",
        """\
        dependencies:
          - numpy
        optional_dependencies:
          extras:
            - ../nosetup
        platforms:
          - linux-64
        """,
    )
    result = _discover_local_dependency_graph(
        [proj / "requirements.yaml"],
    )
    assert len(result.roots) == 1
    assert not result.optional_group_graph.get(result.roots[0], {}).get("extras", [])
    assert not result.optional_group_unmanaged_graph.get(result.roots[0], {}).get(
        "extras",
        [],
    )


def test_restore_demoted_skips_when_still_in_universal(tmp_path: Path) -> None:
    """Cover restore skips when pkg is in universal deps or target."""
    req = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - conda: numpy >=1.0
          - pip: numpy >=2.0  # [linux64]
          - conda: scipy
        platforms:
          - linux-64
          - osx-arm64
        """,
    )
    data = _generate_and_load(tmp_path / "pixi.toml", req)
    assert "scipy" in data["dependencies"]


def test_pixi_monorepo_optional_local_feature_not_in_pixi_data(
    tmp_path: Path,
) -> None:
    """Cover optional local dep feature not in pixi_data."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    _write_file(
        empty_dir / "requirements.yaml",
        """\
        dependencies: []
    """,
    )

    proj = tmp_path / "proj"
    proj.mkdir()
    _write_file(
        proj / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        optional_dependencies:
          extras:
            - ../empty
        platforms:
          - linux-64
        """,
    )

    proj2 = tmp_path / "proj2"
    proj2.mkdir()
    _write_file(
        proj2 / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        platforms:
          - linux-64
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        proj / "requirements.yaml",
        proj2 / "requirements.yaml",
    )
    for env_features in data.get("environments", {}).values():
        assert "empty" not in env_features


# --- Parametrized _restore_demoted_universals unit tests ---


@pytest.mark.parametrize(
    ("section", "demoted", "platforms", "expected_behavior"),
    [
        pytest.param(
            {"dependencies": {"click": "*"}},
            {"click": ("conda", ">=1.0")},
            ["linux-64"],
            "no-target",
            id="skip-still-in-conda-universal",
        ),
        pytest.param(
            {"pypi-dependencies": {"click": "*"}},
            {"click": ("pip", ">=1.0")},
            ["linux-64"],
            "no-target",
            id="skip-still-in-pip-universal",
        ),
        pytest.param(
            {"target": {"linux-64": {"dependencies": {"click": ">=2.0"}}}},
            {"click": ("conda", ">=1.0")},
            ["linux-64"],
            "not-overwritten",
            id="skip-same-dep-type-in-target",
        ),
    ],
)
def test_restore_demoted_universals(
    section: dict[str, Any],
    demoted: dict[str, tuple[str, str | dict[str, Any]]],
    platforms: list[str],
    expected_behavior: str,
) -> None:
    """Cover _restore_demoted_universals skip conditions."""
    original_section = copy.deepcopy(section)
    _restore_demoted_universals(section, demoted, platforms)
    if expected_behavior == "no-target" and "target" not in original_section:
        assert "target" not in section
    elif expected_behavior == "not-overwritten":
        # For the target case, verify click was not overwritten
        assert section["target"]["linux-64"]["dependencies"]["click"] == ">=2.0"


def test_pixi_single_file_installable_optional_local_dep_not_in_root(
    tmp_path: Path,
) -> None:
    """Pip-installable optional local deps must NOT leak into root pypi-dependencies."""
    localdep_dir = tmp_path / "localdep"
    localdep_dir.mkdir()
    _write_file(
        localdep_dir / "requirements.yaml",
        """\
        dependencies:
          - pandas
        """,
    )
    (localdep_dir / "setup.py").write_text(
        "from setuptools import setup; setup(name='localdep')",
    )

    root_req = _write_file(
        tmp_path / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        optional_dependencies:
          dev:
            - ./localdep
        platforms:
          - linux-64
        """,
    )

    data = _generate_and_load(tmp_path / "pixi.toml", root_req)

    # localdep must NOT appear in root pypi-dependencies
    root_pypi = data.get("pypi-dependencies", {})
    assert "localdep" not in root_pypi, (
        "pip-installable optional local dep leaked into root pypi-dependencies"
    )

    # localdep MUST appear in the dev feature
    dev_pypi = data["feature"]["dev"].get("pypi-dependencies", {})
    assert "localdep" in dev_pypi, "optional local dep missing from dev feature"
    assert dev_pypi["localdep"]["editable"] is True


def test_pixi_monorepo_optional_aggregator_transitive_deps_in_env(
    tmp_path: Path,
) -> None:
    """Empty aggregator in optional group must still pull transitive features into env."""
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    _write_file(
        lib_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - scipy
        """,
    )

    agg_dir = tmp_path / "agg"
    agg_dir.mkdir()
    _write_file(
        agg_dir / "requirements.yaml",
        """\
        dependencies: []
        local_dependencies:
          - ../lib
        """,
    )

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    _write_file(
        app_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - numpy
        optional_dependencies:
          extras:
            - ../agg
        platforms:
          - linux-64
        """,
    )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    _write_file(
        other_dir / "requirements.yaml",
        """\
        channels:
          - conda-forge
        dependencies:
          - pandas
        platforms:
          - linux-64
        """,
    )

    data = _generate_and_load(
        tmp_path / "pixi.toml",
        app_dir / "requirements.yaml",
        other_dir / "requirements.yaml",
    )

    app_extras_env = data["environments"].get("app-extras", [])
    assert "lib" in app_extras_env, (
        f"transitive dep 'lib' missing from app-extras env: {app_extras_env}"
    )
