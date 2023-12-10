"""unidep tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from ruamel.yaml import YAML

from unidep._conda_lock import conda_lock_command
from unidep.utils import remove_top_comments


def test_conda_lock_command() -> None:
    simple_monorepo = Path(__file__).parent / "simple_monorepo"
    with patch("unidep._conda_lock._run_conda_lock", return_value=None):
        conda_lock_command(
            depth=1,
            directory=simple_monorepo,
            platform=["linux-64", "osx-arm64"],
            verbose=True,
            only_global=False,
            check_input_hash=True,
            ignore_pins=[],
            overwrite_pins=[],
            skip_dependencies=[],
        )
    with YAML(typ="safe") as yaml:
        with (simple_monorepo / "project1" / "conda-lock.yml").open() as f:
            lock1 = yaml.load(f)
        with (simple_monorepo / "project2" / "conda-lock.yml").open() as f:
            lock2 = yaml.load(f)
    assert [p["name"] for p in lock1["package"]] == ["bzip2", "python_abi", "tzdata"]
    assert [p["name"] for p in lock2["package"]] == ["python_abi", "tzdata"]


def test_conda_lock_command_pip_package_with_conda_dependency() -> None:
    simple_monorepo = Path(__file__).parent / "test-pip-package-with-conda-dependency"
    with patch("unidep._conda_lock._run_conda_lock", return_value=None):
        conda_lock_command(
            depth=1,
            directory=simple_monorepo,
            platform=["linux-64"],
            verbose=True,
            only_global=False,
            check_input_hash=True,
            ignore_pins=[],
            overwrite_pins=[],
            skip_dependencies=[],
        )
    with YAML(typ="safe") as yaml:
        with (simple_monorepo / "project1" / "conda-lock.yml").open() as f:
            lock1 = yaml.load(f)
        with (simple_monorepo / "project2" / "conda-lock.yml").open() as f:
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
    capsys: pytest.CaptureFixture,
) -> None:
    simple_monorepo = Path(__file__).parent / "test-pip-and-conda-different-name"
    with patch("unidep._conda_lock._run_conda_lock", return_value=None):
        conda_lock_command(
            depth=1,
            directory=simple_monorepo,
            platform=["linux-64"],
            verbose=True,
            only_global=False,
            check_input_hash=True,
            ignore_pins=[],
            overwrite_pins=[],
            skip_dependencies=[],
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
