"""Tests for setuptools integration."""

import textwrap
from pathlib import Path

import pytest

from unidep._setuptools_integration import (
    _package_name_from_path,
    _package_name_from_pyproject_toml,
    _package_name_from_setup_cfg,
    _package_name_from_setup_py,
)

REPO_ROOT = Path(__file__).parent.parent


def test_package_name_from_path() -> None:
    example = REPO_ROOT / "example"
    # Could not find the package name, so it uses the folder name
    assert _package_name_from_path(example) == "example"
    # The following should read from the setup.py or pyproject.toml file
    assert _package_name_from_path(example / "hatch_project") == "hatch_project"
    assert (
        _package_name_from_pyproject_toml(example / "hatch_project" / "pyproject.toml")
        == "hatch_project"
    )
    assert _package_name_from_path(example / "hatch2_project") == "hatch2_project"
    assert (
        _package_name_from_pyproject_toml(example / "hatch2_project" / "pyproject.toml")
        == "hatch2_project"
    )
    assert (
        _package_name_from_path(example / "pyproject_toml_project")
        == "pyproject_toml_project"
    )
    assert (
        _package_name_from_pyproject_toml(
            example / "pyproject_toml_project" / "pyproject.toml",
        )
        == "pyproject_toml_project"
    )
    assert _package_name_from_path(example / "setup_py_project") == "setup_py_project"
    assert (
        _package_name_from_setup_py(example / "setup_py_project" / "setup.py")
        == "setup_py_project"
    )
    assert (
        _package_name_from_path(example / "setuptools_project") == "setuptools_project"
    )
    assert (
        _package_name_from_pyproject_toml(
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
    assert _package_name_from_path(tmp_path) == "setup_cfg_project"
    assert _package_name_from_setup_cfg(setup_cfg) == "setup_cfg_project"
    missing = tmp_path / "missing" / "setup.cfg"
    assert not missing.exists()
    with pytest.raises(KeyError):
        _package_name_from_setup_cfg(missing)

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
        _package_name_from_setup_cfg(setup_cfg2)
