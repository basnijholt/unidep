"""unidep conda-lock tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from ruamel.yaml import YAML

from unidep._conda_lock import (
    LockSpec,
    _handle_missing_keys,
    _parse_conda_lock_packages,
    conda_lock_command,
)
from unidep.utils import remove_top_comments

if TYPE_CHECKING:
    from unidep.platform_definitions import CondaPip, Platform


def test_conda_lock_command(tmp_path: Path) -> None:
    folder = tmp_path / "simple_monorepo"
    shutil.copytree(Path(__file__).parent / "simple_monorepo", folder)
    with patch("unidep._conda_lock._run_conda_lock", return_value=None):
        conda_lock_command(
            depth=1,
            directory=folder,
            files=None,
            platforms=["linux-64", "osx-arm64"],
            verbose=True,
            only_global=False,
            check_input_hash=True,
            ignore_pins=[],
            overwrite_pins=[],
            skip_dependencies=[],
            extra_flags=["--", "--micromamba"],
        )
    with YAML(typ="safe") as yaml:
        with (folder / "project1" / "conda-lock.yml").open() as f:
            lock1 = yaml.load(f)
        with (folder / "project2" / "conda-lock.yml").open() as f:
            lock2 = yaml.load(f)

    assert [p["name"] for p in lock1["package"] if p["platform"] == "osx-arm64"] == [
        "bzip2",
        "python_abi",
        "tzdata",
    ]
    assert [p["name"] for p in lock2["package"] if p["platform"] == "osx-arm64"] == [
        "python_abi",
        "tzdata",
    ]


def test_conda_lock_command_pip_package_with_conda_dependency(tmp_path: Path) -> None:
    folder = tmp_path / "test-pip-package-with-conda-dependency"
    shutil.copytree(
        Path(__file__).parent / "test-pip-package-with-conda-dependency",
        folder,
    )
    with patch("unidep._conda_lock._run_conda_lock", return_value=None):
        conda_lock_command(
            depth=1,
            directory=folder,
            files=None,
            platforms=["linux-64"],
            verbose=True,
            only_global=False,
            check_input_hash=True,
            ignore_pins=[],
            overwrite_pins=[],
            skip_dependencies=[],
            extra_flags=[],
        )
    with YAML(typ="safe") as yaml:
        with (folder / "project1" / "conda-lock.yml").open() as f:
            lock1 = yaml.load(f)
        with (folder / "project2" / "conda-lock.yml").open() as f:
            lock2 = yaml.load(f)
    assert [p["name"] for p in lock1["package"]] == [
        "_libgcc_mutex",
        "_openmp_mutex",
        "bzip2",
        "ca-certificates",
        "ld_impl_linux-64",
        "libexpat",
        "libffi",
        "libgcc-ng",
        "libgomp",
        "libnsl",
        "libsqlite",
        "libstdcxx-ng",
        "libuuid",
        "libzlib",
        "ncurses",
        "openssl",
        "pybind11",
        "pybind11-global",
        "python",
        "python_abi",
        "readline",
        "tk",
        "tzdata",
        "xz",
    ]
    assert [p["name"] for p in lock2["package"]] == [
        "_libgcc_mutex",
        "_openmp_mutex",
        "bzip2",
        "ca-certificates",
        "ld_impl_linux-64",
        "libexpat",
        "libffi",
        "libgcc-ng",
        "libgomp",
        "libnsl",
        "libsqlite",
        "libstdcxx-ng",
        "libuuid",
        "libzlib",
        "ncurses",
        "openssl",
        "pybind11",
        "pybind11-global",
        "python",
        "python_abi",
        "readline",
        "tk",
        "tzdata",
        "xz",
        "cutde",
        "mako",
        "markupsafe",
        "rsync-time-machine",
    ]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_conda_lock_command_pip_and_conda_different_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    folder = tmp_path / "test-pip-and-conda-different-name"
    shutil.copytree(Path(__file__).parent / "test-pip-and-conda-different-name", folder)
    files = [
        folder / "project1" / "requirements.yaml",
        folder / "project2" / "requirements.yaml",
    ]
    with patch("unidep._conda_lock._run_conda_lock", return_value=None):
        conda_lock_command(
            depth=1,
            directory=folder,  # ignored when using files
            files=files,
            platforms=["linux-64"],
            verbose=True,
            only_global=False,
            check_input_hash=True,
            ignore_pins=[],
            overwrite_pins=[],
            skip_dependencies=[],
            extra_flags=[],
        )
    assert "Missing keys" not in capsys.readouterr().out


def test_remove_top_comments(tmp_path: Path) -> None:
    test_file = tmp_path / "test_file.txt"
    test_file.write_text(
        "# Comment line 1\n# Comment line 2\nActual content line 1\nActual content line 2",
    )

    remove_top_comments(test_file)

    with test_file.open("r") as file:
        content = file.read()

    assert content == "Actual content line 1\nActual content line 2"


def test_handle_missing_keys(capsys: pytest.CaptureFixture) -> None:
    lock_spec = LockSpec(
        packages={
            ("conda", "linux-64", "python-nonexistent"): {
                "name": "python-nonexistent",
                "manager": "conda",
                "platform": "linux-64",
                "dependencies": [],
                "url": "https://example.com/nonexistent",
            },
        },
        dependencies={("conda", "linux-64", "nonexistent"): set()},
    )
    # Here the package name on pip contains the conda package name, so we will download
    # the conda package to verify that this is our package.

    locked: list[dict[str, Any]] = []
    locked_keys: set[tuple[CondaPip, Platform, str]] = {}  # type: ignore[assignment]
    missing_keys: set[tuple[CondaPip, Platform, str]] = {
        ("pip", "linux-64", "nonexistent"),
    }
    with patch(
        "unidep._conda_lock._download_and_get_package_names",
        return_value=None,
    ) as mock:
        _handle_missing_keys(
            lock_spec=lock_spec,
            locked_keys=locked_keys,
            missing_keys=missing_keys,
            locked=locked,
        )
        mock.assert_called_once()

    assert f"❌ Missing keys {missing_keys}" in capsys.readouterr().out
    assert ("pip", "linux-64", "nonexistent") in missing_keys


def test_circular_dependency() -> None:
    """Test that circular dependencies are handled correctly.

    This test is based on the following requirements.yml file:

    ```yaml
    channels:
        - conda-forge
    dependencies:
        - sphinx
    platforms:
        - linux-64
    ```

    The sphinx package has a circular dependency to itself, e.g., `sphinx` depends
    on `sphinxcontrib-applehelp` which depends on `sphinx`.

    Then we called `unidep conda-lock` on the above requirements.yml file. The
    bit to reproduce the error is in the `package` list below.
    """
    package = [
        {
            "name": "sphinx",
            "manager": "conda",
            "platform": "linux-64",
            "dependencies": {"sphinxcontrib-applehelp": ""},
        },
        {
            "name": "sphinxcontrib-applehelp",
            "version": "1.0.8",
            "manager": "conda",
            "platform": "linux-64",
            "dependencies": {"sphinx": ">=5"},
        },
    ]
    lock_spec = _parse_conda_lock_packages(package)
    assert lock_spec.packages == {
        ("conda", "linux-64", "sphinx"): {
            "name": "sphinx",
            "manager": "conda",
            "platform": "linux-64",
            "dependencies": {"sphinxcontrib-applehelp": ""},
        },
        ("conda", "linux-64", "sphinxcontrib-applehelp"): {
            "name": "sphinxcontrib-applehelp",
            "version": "1.0.8",
            "manager": "conda",
            "platform": "linux-64",
            "dependencies": {"sphinx": ">=5"},
        },
    }
