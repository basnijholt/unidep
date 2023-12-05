"""unidep tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from unidep._cli import _install_all_command, _install_command

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


@pytest.mark.parametrize("project", ["project1", "project2", "project3"])
def test_unidep_install_dry_run(project: str) -> None:
    # Path to the requirements file
    requirements_path = REPO_ROOT / "example" / project

    # Ensure the requirements file exists
    assert requirements_path.exists(), "Requirements file does not exist"

    # Run the unidep install command
    result = subprocess.run(
        [  # noqa: S607, S603
            "unidep",
            "install",
            "--dry-run",
            str(requirements_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    # Check the output
    assert result.returncode == 0, "Command failed to execute successfully"
    if project in ("project1", "project2"):
        assert "📦 Installing conda dependencies with" in result.stdout
    assert "📦 Installing pip dependencies with" in result.stdout
    assert "📦 Installing project with" in result.stdout


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


def test_unidep_install_all_dry_run() -> None:
    # Path to the requirements file
    requirements_path = REPO_ROOT / "example"

    # Ensure the requirements file exists
    assert requirements_path.exists(), "Requirements file does not exist"

    # Run the unidep install command
    result = subprocess.run(
        [  # noqa: S607, S603
            "unidep",
            "install-all",
            "--dry-run",
            "--editable",
            "--directory",
            str(requirements_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    # Check the output
    assert result.returncode == 0, "Command failed to execute successfully"
    assert "📦 Installing pip dependencies with" in result.stdout
    assert "📦 Installing project with" in result.stdout
    assert (
        f"-m pip install -e {REPO_ROOT}/example/project1 -e {REPO_ROOT}/example/project2 -e {REPO_ROOT}/example/project3`"
        in result.stdout
    )
