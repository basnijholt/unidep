"""unidep tests."""
from __future__ import annotations

import shutil
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
        f"pip install -e {REPO_ROOT}/example/project1 -e {REPO_ROOT}/example/project2 -e {REPO_ROOT}/example/project3`"
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


def test_doubly_nested_project_folder_installable(
    tmp_path: Path,
) -> None:
    example_folder = tmp_path / "example"
    shutil.copytree(REPO_ROOT / "example", example_folder)

    # Add an extra project
    extra_projects = example_folder / "extra_projects"
    extra_projects.mkdir(exist_ok=True, parents=True)
    project4 = extra_projects / "project4"
    project4.mkdir(exist_ok=True, parents=True)
    (project4 / "requirements.yaml").write_text("includes: [../../project1]")
    pyproject_toml = "\n".join(  # noqa: FLY002
        (
            "[build-system]",
            'requires = ["setuptools", "unidep"]',
            'build-backend = "setuptools.build_meta"',
        ),
    )

    (project4 / "pyproject.toml").write_text(pyproject_toml)
    setup = "\n".join(  # noqa: FLY002
        (
            "from setuptools import setup",
            'setup(name="project4", version="0.1.0", description="yolo", py_modules=["project1"])',
        ),
    )
    (project4 / "setup.py").write_text(setup)
    (project4 / "project4.py").write_text("print('hello')")

    # Run the unidep install command
    result = subprocess.run(
        [  # noqa: S607, S603
            "unidep",
            "install",
            "--dry-run",
            "--editable",
            "--no-dependencies",
            str(project4 / "requirements.yaml"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert (
        f"pip install --no-dependencies -e {tmp_path}/example/project1 -e {tmp_path}/example/project2 -e {tmp_path}/example/project3`"
        in result.stdout
    )
    assert (
        f"pip install --no-dependencies -e {tmp_path}/example/extra_projects/project4`"
        in result.stdout
    )
