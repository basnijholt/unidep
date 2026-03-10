"""Tests for setuptools integration."""

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from unidep._setuptools_integration import get_python_dependencies
from unidep.utils import (
    package_name_from_path,
    package_name_from_pyproject_toml,
    package_name_from_setup_cfg,
    package_name_from_setup_py,
)

REPO_ROOT = Path(__file__).parent.parent


def test_package_name_from_path() -> None:
    example = REPO_ROOT / "example"
    # Could not find the package name, so it uses the folder name
    assert package_name_from_path(example) == "example"
    # The following should read from the setup.py or pyproject.toml file
    assert package_name_from_path(example / "hatch_project") == "hatch_project"
    assert (
        package_name_from_pyproject_toml(example / "hatch_project" / "pyproject.toml")
        == "hatch_project"
    )
    assert package_name_from_path(example / "hatch2_project") == "hatch2_project"
    assert (
        package_name_from_pyproject_toml(example / "hatch2_project" / "pyproject.toml")
        == "hatch2_project"
    )
    assert (
        package_name_from_path(example / "pyproject_toml_project")
        == "pyproject_toml_project"
    )
    assert (
        package_name_from_pyproject_toml(
            example / "pyproject_toml_project" / "pyproject.toml",
        )
        == "pyproject_toml_project"
    )
    assert package_name_from_path(example / "setup_py_project") == "setup_py_project"
    assert (
        package_name_from_setup_py(example / "setup_py_project" / "setup.py")
        == "setup_py_project"
    )
    assert (
        package_name_from_path(example / "setuptools_project") == "setuptools_project"
    )
    assert (
        package_name_from_pyproject_toml(
            example / "setuptools_project" / "pyproject.toml",
        )
        == "setuptools_project"
    )


def test_package_name_from_cfg(tmp_path: Path) -> None:
    setup_cfg = tmp_path / "setup.cfg"
    setup_cfg.write_text(
        textwrap.dedent(
            """\
            [metadata]
            name = setup_cfg_project
            """,
        ),
    )
    assert package_name_from_path(tmp_path) == "setup_cfg_project"
    assert package_name_from_setup_cfg(setup_cfg) == "setup_cfg_project"
    missing = tmp_path / "missing" / "setup.cfg"
    assert not missing.exists()
    with pytest.raises(KeyError):
        package_name_from_setup_cfg(missing)

    setup_cfg2 = tmp_path / "setup.cfg"
    setup_cfg2.write_text(
        textwrap.dedent(
            """\
            [metadata]
            yolo = missing
            """,
        ),
    )
    with pytest.raises(KeyError):
        package_name_from_setup_cfg(setup_cfg2)


def test_package_name_from_setup_py_requires_literal_name(tmp_path: Path) -> None:
    setup_py = tmp_path / "setup.py"
    setup_py.write_text(
        textwrap.dedent(
            """\
            from setuptools import setup
            NAME = "dynamic_name"
            setup(name=NAME)
            """,
        ),
    )

    with pytest.raises(
        KeyError,
        match=r"Could not find the package name in the setup\.py",
    ):
        package_name_from_setup_py(setup_py)


def test_package_name_from_path_falls_back_on_invalid_pyproject(tmp_path: Path) -> None:
    pyproject_toml = tmp_path / "pyproject.toml"
    pyproject_toml.write_text("this is not valid toml = [")

    assert package_name_from_path(tmp_path) == tmp_path.name


def test_package_name_from_path_falls_back_on_invalid_setup_py(tmp_path: Path) -> None:
    setup_py = tmp_path / "setup.py"
    setup_py.write_text("from setuptools import setup\nsetup(name='missing'")

    assert package_name_from_path(tmp_path) == tmp_path.name


def test_package_name_from_path_does_not_suppress_unexpected_errors(
    tmp_path: Path,
) -> None:
    setup_py = tmp_path / "setup.py"
    setup_py.write_text("from setuptools import setup\nsetup(name='pkg')")

    with patch(
        "unidep.utils.package_name_from_setup_py",
        side_effect=RuntimeError("boom"),
    ), pytest.raises(RuntimeError, match="boom"):
        package_name_from_path(tmp_path)


def test_get_python_dependencies_detects_conflicting_local_sources(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    dep_a = tmp_path / "dep_a"
    dep_b = tmp_path / "dep_b"
    for dep in (dep_a, dep_b):
        dep.mkdir()
        (dep / "setup.py").write_text(
            "from setuptools import setup\nsetup(name='shared-lib', version='0.1.0')\n",
        )

    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
              - numpy
            local_dependencies:
              - ../dep_a
              - ../dep_b
            """,
        ),
    )

    with pytest.raises(RuntimeError, match="multiple sources for the same package"):
        get_python_dependencies(
            project / "requirements.yaml",
            include_local_dependencies=True,
        )


def test_get_python_dependencies_allows_same_local_source_with_different_extras(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    dep = tmp_path / "dep"
    dep.mkdir()
    (dep / "setup.py").write_text(
        "from setuptools import setup\nsetup(name='shared-lib', version='0.1.0')\n",
    )

    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
              - numpy
            local_dependencies:
              - ../dep[test]
              - ../dep[dev]
            """,
        ),
    )

    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=True,
    )

    assert "numpy" in deps.dependencies
    assert any("shared-lib[test] @ file://" in dep for dep in deps.dependencies)
    assert any("shared-lib[dev] @ file://" in dep for dep in deps.dependencies)


def test_get_python_dependencies_ignores_unselected_conflicting_optional_direct_refs(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
              - pip: shared-lib @ file:///tmp/dep-a
            optional_dependencies:
              test:
                - pip: shared-lib @ file:///tmp/dep-b
            """,
        ),
    )

    deps = get_python_dependencies(project / "requirements.yaml")

    assert deps.dependencies == ["shared-lib @ file:///tmp/dep-a"]
    assert deps.extras == {"test": ["shared-lib @ file:///tmp/dep-b"]}


def test_get_python_dependencies_detects_conflicting_selected_optional_direct_refs(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
              - pip: shared-lib @ file:///tmp/dep-a
            optional_dependencies:
              test:
                - pip: shared-lib @ file:///tmp/dep-b
            """,
        ),
    )

    with pytest.raises(RuntimeError, match="multiple sources for the same package"):
        get_python_dependencies(f"{project / 'requirements.yaml'}[test]")
