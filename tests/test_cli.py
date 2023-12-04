"""unidep tests."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from unidep._cli import _install_command

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).parent.parent


def test_install_command(capsys: pytest.CaptureFixture) -> None:
    _install_command(
        conda_executable="",
        dry_run=True,
        editable=False,
        file=REPO_ROOT / "example" / "project1" / "requirements.yaml",
        verbose=False,
    )
    captured = capsys.readouterr()
    assert "Installing conda dependencies" in captured.out
    assert "Installing pip dependencies" in captured.out
