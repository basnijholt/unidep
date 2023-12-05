"""unidep tests."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from unidep._cli import _install_all_command, _install_command

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).parent.parent


def test_install_command(capsys: pytest.CaptureFixture) -> None:
    _install_command(
        REPO_ROOT / "example" / "project1" / "requirements.yaml",
        conda_executable="",
        dry_run=True,
        editable=False,
        verbose=False,
    )
    captured = capsys.readouterr()
    assert "Installing conda dependencies" in captured.out
    assert "Installing pip dependencies" in captured.out


def test_install_all_command(capsys: pytest.CaptureFixture) -> None:
    _install_all_command(
        conda_executable="",
        dry_run=True,
        editable=True,
        directory=REPO_ROOT / "example",
        depth=1,
        verbose=False,
    )
    captured = capsys.readouterr()
    assert "Installing conda dependencies" in captured.out
    assert "Installing pip dependencies" in captured.out
    assert (
        f"-m pip install -e {REPO_ROOT}/example/project1 -e {REPO_ROOT}/example/project2 -e {REPO_ROOT}/example/project3`"
        in captured.out
    )
