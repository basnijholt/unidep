"""unidep CLI tests."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from unidep._cli import (
    _capitalize_dir,
    _conda_env_list,
    _conda_root_prefix,
    _find_windows_path,
    _identify_conda_executable,
    _install_all_command,
    _install_command,
    _pip_compile_command,
    _pip_subcommand,
    _print_versions,
)

REPO_ROOT = Path(__file__).parent.parent

EXAMPLE_PROJECTS = [
    "setup_py_project",
    "setuptools_project",
    "hatch_project",
    "pyproject_toml_project",
    "hatch2_project",
]


def current_env_and_prefix() -> tuple[str, Path]:
    """Get the current conda environment name and prefix."""
    try:
        prefix = _conda_root_prefix("conda")
    except (KeyError, FileNotFoundError):
        prefix = _conda_root_prefix("micromamba")
    folder, env_name = Path(os.environ["CONDA_PREFIX"]).parts[-2:]
    if folder != "envs":
        return "base", prefix
    return env_name, prefix / "envs" / env_name


@pytest.mark.parametrize(
    "project",
    EXAMPLE_PROJECTS,
)
def test_install_command(project: str, capsys: pytest.CaptureFixture) -> None:
    current_env, prefix = current_env_and_prefix()
    print(f"current_env: {current_env}, prefix: {prefix}")
    for kw in [
        {"conda_env_name": current_env, "conda_env_prefix": None},
        {"conda_env_name": None, "conda_env_prefix": prefix},
    ]:
        _install_command(
            REPO_ROOT / "example" / project,
            conda_executable="",  # type: ignore[arg-type]
            dry_run=True,
            editable=False,
            verbose=True,
            **kw,  # type: ignore[arg-type]
        )
        captured = capsys.readouterr()
        assert "Installing conda dependencies" in captured.out
        assert "Installing pip dependencies" in captured.out
        assert "Installing project with" in captured.out


@pytest.mark.parametrize(
    "project",
    EXAMPLE_PROJECTS,
)
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
        encoding="utf-8",
    )

    # Check the output
    assert result.returncode == 0, "Command failed to execute successfully"
    if project in ("setup_py_project", "setuptools_project"):
        assert "📦 Installing conda dependencies with" in result.stdout
    assert "📦 Installing pip dependencies with" in result.stdout
    assert "📦 Installing project with" in result.stdout


def test_install_all_command(capsys: pytest.CaptureFixture) -> None:
    _install_all_command(
        conda_executable="",  # type: ignore[arg-type]
        conda_env_name=None,
        conda_env_prefix=None,
        dry_run=True,
        editable=True,
        directory=REPO_ROOT / "example",
        depth=1,
        verbose=False,
    )
    captured = capsys.readouterr()
    assert "Installing conda dependencies" in captured.out
    assert "Installing pip dependencies" in captured.out
    projects = [REPO_ROOT / "example" / p for p in EXAMPLE_PROJECTS]
    pkgs = " ".join([f"-e {p}" for p in sorted(projects)])
    assert f"pip install --no-dependencies {pkgs}`" in captured.out


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
        encoding="utf-8",
    )

    # Check the output
    assert result.returncode == 0, "Command failed to execute successfully"
    assert "📦 Installing conda dependencies with `" in result.stdout

    assert r"📦 Installing pip dependencies with `" in result.stdout
    assert (
        "📝 Found local dependencies: {'pyproject_toml_project': ['hatch_project'], 'setup_py_project': ['hatch_project', 'setuptools_project'], 'setuptools_project': ['hatch_project']}"
        in result.stdout
    )
    projects = [REPO_ROOT / "example" / p for p in EXAMPLE_PROJECTS]
    pkgs = " ".join([f"-e {p}" for p in sorted(projects)])
    assert "📦 Installing project with `" in result.stdout
    assert f" -m pip install --no-dependencies {pkgs}" in result.stdout


def test_unidep_conda() -> None:
    # Path to the requirements file
    requirements_path = REPO_ROOT / "example" / "setup_py_project"

    assert requirements_path.exists(), "Requirements file does not exist"

    result = subprocess.run(
        [  # noqa: S607, S603
            "unidep",
            "conda",
            "--file",
            str(requirements_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    # Check the output
    assert result.returncode == 0, "Command failed to execute successfully"
    assert "pandas" in result.stdout


def test_unidep_file_not_found_error() -> None:
    # Path to the requirements file
    requirements_path = REPO_ROOT / "yolo"

    assert not requirements_path.exists()

    # Run the unidep install command
    result = subprocess.run(
        [  # noqa: S607, S603
            "unidep",
            "conda",
            "--file",
            str(requirements_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 1, "Command unexpectedly succeeded"
    assert "❌ One or more files" in result.stdout


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
    (project4 / "requirements.yaml").write_text(
        "local_dependencies: [../../setup_py_project]",
    )
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
            'setup(name="project4", version="0.1.0", description="yolo", py_modules=["setup_py_project"])',
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
        encoding="utf-8",
    )

    p1 = str(tmp_path / "example" / "hatch_project")
    p2 = str(tmp_path / "example" / "setup_py_project")
    p3 = str(tmp_path / "example" / "setuptools_project")
    p4 = str(tmp_path / "example" / "extra_projects" / "project4")
    pkgs = " ".join([f"-e {p}" for p in sorted((p1, p2, p3, p4))])
    assert f"pip install --no-dependencies {pkgs}`" in result.stdout

    p5 = str(tmp_path / "example" / "pyproject_toml_project")
    p6 = str(tmp_path / "example" / "hatch2_project")
    # Test depth 2
    result = subprocess.run(
        [  # noqa: S607, S603
            "unidep",
            "install-all",
            "--dry-run",
            "--editable",
            "--no-dependencies",
            "--directory",
            str(example_folder),
            "--depth",
            "2",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    pkgs = " ".join([f"-e {p}" for p in sorted((p1, p2, p3, p4, p5, p6))])
    assert f"pip install --no-dependencies {pkgs}`" in result.stdout

    # Test depth 1 (should not install project4)
    result = subprocess.run(
        [  # noqa: S607, S603
            "unidep",
            "install-all",
            "--dry-run",
            "--editable",
            "--no-dependencies",
            "--directory",
            str(example_folder),
            "--depth",
            "1",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    pkgs = " ".join([f"-e {p}" for p in sorted((p1, p2, p3, p5, p6))])
    assert f"pip install --no-dependencies {pkgs}`" in result.stdout


def test_pip_compile_command(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    folder = tmp_path / "example"
    shutil.copytree(REPO_ROOT / "example", folder)
    with patch("subprocess.run", return_value=None), patch(
        "importlib.util.find_spec",
        return_value=True,
    ):
        _pip_compile_command(
            depth=2,
            directory=folder,
            platform="linux-64",
            ignore_pins=[],
            skip_dependencies=[],
            overwrite_pins=[],
            verbose=True,
            extra_flags=["--", "--allow-unsafe"],
        )
    requirements_in = folder / "requirements.in"
    assert requirements_in.exists()
    with requirements_in.open() as f:
        assert "adaptive" in f.read()
    requirements_txt = folder / "requirements.txt"

    assert (
        f"Locking dependencies with `pip-compile --output-file {requirements_txt} --allow-unsafe {requirements_in}`"
        in capsys.readouterr().out
    )


def test_install_non_existing_file() -> None:
    with pytest.raises(FileNotFoundError, match="File `does_not_exist` not found."):
        _install_command(
            Path("does_not_exist"),
            conda_executable="",  # type: ignore[arg-type]
            conda_env_name=None,
            conda_env_prefix=None,
            dry_run=True,
            editable=True,
            verbose=True,
        )


def test_install_non_existing_folder(tmp_path: Path) -> None:
    requirements_file = tmp_path / "requirements.yaml"
    pyproject_file = tmp_path / "pyproject.toml"
    match = re.escape(
        f"File `{requirements_file}` or `{pyproject_file}`"
        f" (with unidep configuration) not found in `{tmp_path}`",
    )
    with pytest.raises(FileNotFoundError, match=match):
        _install_command(
            tmp_path,
            conda_executable="",  # type: ignore[arg-type]
            conda_env_name=None,
            conda_env_prefix=None,
            dry_run=True,
            editable=True,
            verbose=True,
        )


def test_version(capsys: pytest.CaptureFixture) -> None:
    _print_versions()
    captured = capsys.readouterr()
    assert "unidep location" in captured.out
    assert "unidep version" in captured.out
    assert "packaging" in captured.out


def test_conda_env_list() -> None:
    conda_executable = _identify_conda_executable()
    _conda_env_list(conda_executable)


def test_pip_optional(tmp_path: Path) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo
            optional_dependencies:
                test:
                    - bar
            """,
        ),
    )
    txt = _pip_subcommand(
        file=[p],
        platforms=[],
        verbose=True,
        ignore_pins=None,
        skip_dependencies=None,
        overwrite_pins=None,
        separator=" ",
    )
    assert txt == "foo"

    txt = _pip_subcommand(
        file=[f"{p}[test]"],  # type: ignore[list-item]
        platforms=[],
        verbose=True,
        ignore_pins=None,
        skip_dependencies=None,
        overwrite_pins=None,
        separator=" ",
    )
    assert txt == "foo bar"


def test_capitalize_last_dir() -> None:
    # Just needs to work for Windows paths
    assert _capitalize_dir(r"foo\bar\baz") == r"foo\bar\Baz"
    assert _capitalize_dir(r"foo\bar\baz", capitalize=False) == r"foo\bar\baz"
    assert _capitalize_dir(r"foo\bar\baz", capitalize=True) == r"foo\bar\Baz"


@pytest.mark.skipif(
    os.name == "nt",
    reason="Don't test on Windows to make sure that conda is not found.",
)
def test_find_conda_windows() -> None:
    """Tests whether the function searches the expected paths."""
    with pytest.raises(
        FileNotFoundError,
        match="Could not find conda.",
    ) as excinfo:
        _find_windows_path("conda")
    # This Windows hell... 🤦‍♂️
    paths = [
        r"👉 %USERPROFILE%\Anaconda3\condabin\conda.exe",
        r"👉 %USERPROFILE%\anaconda3\condabin\conda.exe",
        r"👉 %USERPROFILE%\Anaconda3\condabin\conda",
        r"👉 %USERPROFILE%\anaconda3\condabin\conda",
        r"👉 %USERPROFILE%\Anaconda3\condabin\conda.bat",
        r"👉 %USERPROFILE%\anaconda3\condabin\conda.bat",
        r"👉 %USERPROFILE%\Anaconda3\Scripts\conda.exe",
        r"👉 %USERPROFILE%\anaconda3\Scripts\conda.exe",
        r"👉 %USERPROFILE%\Anaconda3\Scripts\conda",
        r"👉 %USERPROFILE%\anaconda3\Scripts\conda",
        r"👉 %USERPROFILE%\Anaconda3\Scripts\conda.bat",
        r"👉 %USERPROFILE%\anaconda3\Scripts\conda.bat",
        r"👉 %USERPROFILE%\Anaconda3\conda.exe",
        r"👉 %USERPROFILE%\anaconda3\conda.exe",
        r"👉 %USERPROFILE%\Anaconda3\conda",
        r"👉 %USERPROFILE%\anaconda3\conda",
        r"👉 %USERPROFILE%\Anaconda3\conda.bat",
        r"👉 %USERPROFILE%\anaconda3\conda.bat",
        r"👉 %USERPROFILE%\Miniconda3\condabin\conda.exe",
        r"👉 %USERPROFILE%\miniconda3\condabin\conda.exe",
        r"👉 %USERPROFILE%\Miniconda3\condabin\conda",
        r"👉 %USERPROFILE%\miniconda3\condabin\conda",
        r"👉 %USERPROFILE%\Miniconda3\condabin\conda.bat",
        r"👉 %USERPROFILE%\miniconda3\condabin\conda.bat",
        r"👉 %USERPROFILE%\Miniconda3\Scripts\conda.exe",
        r"👉 %USERPROFILE%\miniconda3\Scripts\conda.exe",
        r"👉 %USERPROFILE%\Miniconda3\Scripts\conda",
        r"👉 %USERPROFILE%\miniconda3\Scripts\conda",
        r"👉 %USERPROFILE%\Miniconda3\Scripts\conda.bat",
        r"👉 %USERPROFILE%\miniconda3\Scripts\conda.bat",
        r"👉 %USERPROFILE%\Miniconda3\conda.exe",
        r"👉 %USERPROFILE%\miniconda3\conda.exe",
        r"👉 %USERPROFILE%\Miniconda3\conda",
        r"👉 %USERPROFILE%\miniconda3\conda",
        r"👉 %USERPROFILE%\Miniconda3\conda.bat",
        r"👉 %USERPROFILE%\miniconda3\conda.bat",
        r"👉 C:\Anaconda3\condabin\conda.exe",
        r"👉 C:\anaconda3\condabin\conda.exe",
        r"👉 C:\Anaconda3\condabin\conda",
        r"👉 C:\anaconda3\condabin\conda",
        r"👉 C:\Anaconda3\condabin\conda.bat",
        r"👉 C:\anaconda3\condabin\conda.bat",
        r"👉 C:\Anaconda3\Scripts\conda.exe",
        r"👉 C:\anaconda3\Scripts\conda.exe",
        r"👉 C:\Anaconda3\Scripts\conda",
        r"👉 C:\anaconda3\Scripts\conda",
        r"👉 C:\Anaconda3\Scripts\conda.bat",
        r"👉 C:\anaconda3\Scripts\conda.bat",
        r"👉 C:\Anaconda3\conda.exe",
        r"👉 C:\anaconda3\conda.exe",
        r"👉 C:\Anaconda3\conda",
        r"👉 C:\anaconda3\conda",
        r"👉 C:\Anaconda3\conda.bat",
        r"👉 C:\anaconda3\conda.bat",
        r"👉 C:\Miniconda3\condabin\conda.exe",
        r"👉 C:\miniconda3\condabin\conda.exe",
        r"👉 C:\Miniconda3\condabin\conda",
        r"👉 C:\miniconda3\condabin\conda",
        r"👉 C:\Miniconda3\condabin\conda.bat",
        r"👉 C:\miniconda3\condabin\conda.bat",
        r"👉 C:\Miniconda3\Scripts\conda.exe",
        r"👉 C:\miniconda3\Scripts\conda.exe",
        r"👉 C:\Miniconda3\Scripts\conda",
        r"👉 C:\miniconda3\Scripts\conda",
        r"👉 C:\Miniconda3\Scripts\conda.bat",
        r"👉 C:\miniconda3\Scripts\conda.bat",
        r"👉 C:\Miniconda3\conda.exe",
        r"👉 C:\miniconda3\conda.exe",
        r"👉 C:\Miniconda3\conda",
        r"👉 C:\miniconda3\conda",
        r"👉 C:\Miniconda3\conda.bat",
        r"👉 C:\miniconda3\conda.bat",
        r"👉 C:\ProgramData\Anaconda3\condabin\conda.exe",
        r"👉 C:\ProgramData\anaconda3\condabin\conda.exe",
        r"👉 C:\ProgramData\Anaconda3\condabin\conda",
        r"👉 C:\ProgramData\anaconda3\condabin\conda",
        r"👉 C:\ProgramData\Anaconda3\condabin\conda.bat",
        r"👉 C:\ProgramData\anaconda3\condabin\conda.bat",
        r"👉 C:\ProgramData\Anaconda3\Scripts\conda.exe",
        r"👉 C:\ProgramData\anaconda3\Scripts\conda.exe",
        r"👉 C:\ProgramData\Anaconda3\Scripts\conda",
        r"👉 C:\ProgramData\anaconda3\Scripts\conda",
        r"👉 C:\ProgramData\Anaconda3\Scripts\conda.bat",
        r"👉 C:\ProgramData\anaconda3\Scripts\conda.bat",
        r"👉 C:\ProgramData\Anaconda3\conda.exe",
        r"👉 C:\ProgramData\anaconda3\conda.exe",
        r"👉 C:\ProgramData\Anaconda3\conda",
        r"👉 C:\ProgramData\anaconda3\conda",
        r"👉 C:\ProgramData\Anaconda3\conda.bat",
        r"👉 C:\ProgramData\anaconda3\conda.bat",
        r"👉 C:\ProgramData\Miniconda3\condabin\conda.exe",
        r"👉 C:\ProgramData\miniconda3\condabin\conda.exe",
        r"👉 C:\ProgramData\Miniconda3\condabin\conda",
        r"👉 C:\ProgramData\miniconda3\condabin\conda",
        r"👉 C:\ProgramData\Miniconda3\condabin\conda.bat",
        r"👉 C:\ProgramData\miniconda3\condabin\conda.bat",
        r"👉 C:\ProgramData\Miniconda3\Scripts\conda.exe",
        r"👉 C:\ProgramData\miniconda3\Scripts\conda.exe",
        r"👉 C:\ProgramData\Miniconda3\Scripts\conda",
        r"👉 C:\ProgramData\miniconda3\Scripts\conda",
        r"👉 C:\ProgramData\Miniconda3\Scripts\conda.bat",
        r"👉 C:\ProgramData\miniconda3\Scripts\conda.bat",
        r"👉 C:\ProgramData\Miniconda3\conda.exe",
        r"👉 C:\ProgramData\miniconda3\conda.exe",
        r"👉 C:\ProgramData\Miniconda3\conda",
        r"👉 C:\ProgramData\miniconda3\conda",
        r"👉 C:\ProgramData\Miniconda3\conda.bat",
        r"👉 C:\ProgramData\miniconda3\conda.bat",
    ]
    for path in paths:
        assert path in excinfo.value.args[0]
