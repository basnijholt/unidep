"""unidep CLI tests."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import textwrap
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib

from unidep._cli import (
    CondaExecutable,
    _capitalize_dir,
    _check_conda_prefix,
    _conda_env_list,
    _conda_root_prefix,
    _find_windows_path,
    _identify_conda_executable,
    _install_all_command,
    _install_command,
    _maybe_conda_run,
    _maybe_create_conda_env_args,
    _merge_command,
    _pip_compile_command,
    _pip_subcommand,
    _print_versions,
    _python_executable,
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
    conda_prefix = Path(os.environ.get("CONDA_PREFIX", prefix))
    if len(conda_prefix.parts) < 2:
        return "base", prefix
    folder, env_name = conda_prefix.parts[-2:]
    if folder != "envs":
        return "base", prefix
    return env_name, conda_prefix


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
            conda_lock_file=None,
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
        [  # noqa: S607
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
        conda_lock_file=None,
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
    assert f"pip install --no-deps {pkgs}`" in captured.out


def test_install_command_detects_duplicate_local_package_names(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools", "wheel"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "shared-lib"
            version = "0.1.0"

            [tool.unidep]
            local_dependencies = ["../vendored"]
            """,
        ),
    )

    vendored = tmp_path / "vendored"
    vendored.mkdir()
    (vendored / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools", "wheel"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "shared-lib"
            version = "0.1.0"

            [tool.unidep]
            dependencies = []
            """,
        ),
    )

    with pytest.raises(RuntimeError, match="Multiple local packages resolve"):
        _install_command(
            project,
            conda_executable="",  # type: ignore[arg-type]
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=True,
            editable=True,
        )


def test_install_command_raises_for_unknown_selected_extra(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.yaml").write_text(
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

    with pytest.raises(ValueError, match="extras that are not defined"):
        _install_command(
            Path(f"{project}[missing]"),
            conda_executable="",  # type: ignore[arg-type]
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=True,
            editable=False,
        )


def test_install_command_detects_conflicting_selected_extra_direct_refs(
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
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
        _install_command(
            Path(f"{p}[test]"),
            conda_executable="",  # type: ignore[arg-type]
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=True,
            editable=False,
        )


def test_install_command_dry_run_allows_missing_target_python_for_pip_deps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - conda: python=3.11
                - pip: requests
            """,
        ),
    )

    conda_prefix = tmp_path / "envs" / "example"
    conda_prefix.mkdir(parents=True)
    monkeypatch.setenv("CONDA_PREFIX", str(conda_prefix))

    _install_command(
        project,
        conda_executable="conda",
        conda_env_name=None,
        conda_env_prefix=None,
        conda_lock_file=None,
        dry_run=True,
        editable=False,
    )

    captured = capsys.readouterr()
    assert "Installing conda dependencies" in captured.out
    assert "Installing pip dependencies" in captured.out


def test_install_command_dry_run_allows_missing_target_python_for_local_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - conda: python=3.11
            """,
        ),
    )
    (project / "setup.py").write_text(
        "from setuptools import setup\nsetup(name='project', version='0.1.0')\n",
    )

    conda_prefix = tmp_path / "envs" / "example"
    conda_prefix.mkdir(parents=True)
    monkeypatch.setenv("CONDA_PREFIX", str(conda_prefix))

    _install_command(
        project,
        conda_executable="conda",
        conda_env_name=None,
        conda_env_prefix=None,
        conda_lock_file=None,
        dry_run=True,
        editable=False,
    )

    captured = capsys.readouterr()
    assert "Installing conda dependencies" in captured.out
    assert "Installing project with" in captured.out


def mock_uv_env(tmp_path: Path) -> dict[str, str]:
    """Create a mock uv executable and return env with it in the PATH."""
    mock_uv_path = tmp_path / ("uv.bat" if platform.system() == "Windows" else "uv")
    if platform.system() == "Windows":
        mock_uv_path.write_text("@echo off\necho Mock uv called %*")
    else:
        mock_uv_path.write_text("#!/bin/sh\necho 'Mock uv called' \"$@\"")
    mock_uv_path.chmod(0o755)  # Make it executable

    # Add tmp_path to the PATH environment variable
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    return env


@pytest.mark.parametrize("with_uv", [True, False])
def test_unidep_install_all_dry_run(tmp_path: Path, with_uv: bool) -> None:  # noqa: FBT001
    # Path to the requirements file
    requirements_path = REPO_ROOT / "example"

    # Ensure the requirements file exists
    assert requirements_path.exists(), "Requirements file does not exist"

    # Run the unidep install command
    result = subprocess.run(
        [  # noqa: S607
            "unidep",
            "install-all",
            "--dry-run",
            "--editable",
            "--directory",
            str(requirements_path),
            *(["--no-uv"] if not with_uv else []),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=mock_uv_env(tmp_path) if with_uv else None,
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
    if with_uv:
        assert "uv pip install --python" in result.stdout
    else:
        assert f" -m pip install --no-deps {pkgs}" in result.stdout


def test_unidep_conda() -> None:
    # Path to the requirements file
    requirements_path = REPO_ROOT / "example" / "setup_py_project"

    assert requirements_path.exists(), "Requirements file does not exist"

    result = subprocess.run(
        [  # noqa: S607
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


def test_unidep_pixi_cli_respects_overrides(tmp_path: Path) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy >=1.20
              - pandas >=2.0
              - scipy <1.10
              - pyobjc  # [osx]
            platforms:
              - linux-64
              - osx-arm64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    result = subprocess.run(
        [  # noqa: S607
            "unidep",
            "pixi",
            "--file",
            str(req_file),
            "--output",
            str(output_file),
            "--name",
            "test-project",
            "--platform",
            "linux-64",
            "--ignore-pin",
            "numpy",
            "--skip-dependency",
            "pandas",
            "--overwrite-pin",
            "scipy>=1.11",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, "Command failed to execute successfully"
    with output_file.open("rb") as f:
        data = tomllib.load(f)

    deps = data["dependencies"]
    assert deps["numpy"] == "*"
    assert "pandas" not in deps
    assert deps["scipy"] == ">=1.11"
    assert data["workspace"]["platforms"] == ["linux-64"]
    assert "target" not in data or "osx-arm64" not in data["target"]


def test_unidep_pixi_cli_channel_override(tmp_path: Path) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            platforms:
              - linux-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    result = subprocess.run(
        [  # noqa: S607
            "unidep",
            "pixi",
            "--file",
            str(req_file),
            "--output",
            str(output_file),
            "--channel",
            "defaults",
            "--channel",
            "bioconda",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    with output_file.open("rb") as f:
        data = tomllib.load(f)

    assert data["workspace"]["channels"] == ["defaults", "bioconda"]


def test_unidep_pixi_cli_ranged_build_string(tmp_path: Path) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - conda: numpy >=1.20,<1.21 py310*
            platforms:
              - linux-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    result = subprocess.run(
        [  # noqa: S607
            "unidep",
            "pixi",
            "--file",
            str(req_file),
            "--output",
            str(output_file),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, "Command failed to execute successfully"
    with output_file.open("rb") as f:
        data = tomllib.load(f)

    numpy_spec = data["dependencies"]["numpy"]
    assert numpy_spec["version"] == ">=1.20,<1.21"
    assert numpy_spec["build"] == "py310*"


def test_merge_uses_selector_platforms_when_no_platforms_declared(
    tmp_path: Path,
) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - cuda-toolkit  # [linux64]
            """,
        ),
    )
    output_file = tmp_path / "environment.yaml"

    with patch("unidep.utils.identify_current_platform", return_value="osx-arm64"):
        _merge_command(
            depth=1,
            directory=tmp_path,
            files=[req_file],
            name="myenv",
            output=output_file,
            stdout=False,
            selector="comment",
            platforms=[],
            ignore_pins=[],
            skip_dependencies=[],
            overwrite_pins=[],
            verbose=False,
        )

    content = output_file.read_text()
    assert "platforms:" in content
    assert "  - linux-64" in content
    assert "  - osx-arm64" not in content


def test_unidep_pixi_cli_optional_monorepo_env_includes_base(
    tmp_path: Path,
) -> None:
    project1_dir = tmp_path / "project1"
    project1_dir.mkdir()
    req1 = project1_dir / "requirements.yaml"
    req1.write_text(
        textwrap.dedent(
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
        ),
    )

    project2_dir = tmp_path / "project2"
    project2_dir.mkdir()
    req2 = project2_dir / "requirements.yaml"
    req2.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pandas
            platforms:
              - linux-64
            """,
        ),
    )

    output_file = tmp_path / "pixi.toml"
    result = subprocess.run(
        [  # noqa: S607
            "unidep",
            "pixi",
            "--file",
            str(project1_dir),
            "--file",
            str(project2_dir),
            "--output",
            str(output_file),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, "Command failed to execute successfully"
    with output_file.open("rb") as f:
        data = tomllib.load(f)

    envs = data["environments"]
    assert set(envs["project1-dev"]) == {"project1", "project1-dev"}


def test_unidep_file_not_found_error() -> None:
    # Path to the requirements file
    requirements_path = REPO_ROOT / "yolo"

    assert not requirements_path.exists()

    # Run the unidep install command
    result = subprocess.run(
        [  # noqa: S607
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
        [  # noqa: S607
            "unidep",
            "install",
            "--dry-run",
            "--editable",
            "--no-dependencies",
            "--no-uv",
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
    assert f"pip install --no-deps {pkgs}`" in result.stdout

    p5 = str(tmp_path / "example" / "pyproject_toml_project")
    p6 = str(tmp_path / "example" / "hatch2_project")
    # Test depth 2
    result = subprocess.run(
        [  # noqa: S607
            "unidep",
            "install-all",
            "--dry-run",
            "--editable",
            "--no-dependencies",
            "--no-uv",
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
    assert f"pip install --no-deps {pkgs}`" in result.stdout

    # Test depth 1 (should not install project4)
    result = subprocess.run(
        [  # noqa: S607
            "unidep",
            "install-all",
            "--dry-run",
            "--editable",
            "--no-dependencies",
            "--no-uv",
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
    assert f"pip install --no-deps {pkgs}`" in result.stdout


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
    with pytest.raises(FileNotFoundError, match=r"File `does_not_exist` not found\."):
        _install_command(
            Path("does_not_exist"),
            conda_executable="",  # type: ignore[arg-type]
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
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
            conda_lock_file=None,
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


def test_pip_subcommand_detects_conflicting_selected_extra_direct_refs(
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
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
        _pip_subcommand(
            file=[f"{p}[test]"],  # type: ignore[list-item]
            platforms=[],
            verbose=True,
            ignore_pins=None,
            skip_dependencies=None,
            overwrite_pins=None,
            separator=" ",
        )


def test_pip_subcommand_ignores_conflicting_unselected_extra_direct_refs(
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
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

    txt = _pip_subcommand(
        file=[p],
        platforms=[],
        verbose=True,
        ignore_pins=None,
        skip_dependencies=None,
        overwrite_pins=None,
        separator="\n",
    )

    assert txt.splitlines() == ["shared-lib @ file:///tmp/dep-a"]


def test_pip_subcommand_raises_for_unknown_selected_extra(tmp_path: Path) -> None:
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

    with pytest.raises(ValueError, match="extras that are not defined"):
        _pip_subcommand(
            file=[f"{p}[missing]"],  # type: ignore[list-item]
            platforms=[],
            verbose=True,
            ignore_pins=None,
            skip_dependencies=None,
            overwrite_pins=None,
            separator=" ",
        )


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
        match=r"Could not find conda\.",
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


@contextmanager
def set_env_var(key: str, value: str) -> Generator[None, None, None]:
    original_value = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if original_value is None:
            del os.environ[key]
        else:
            os.environ[key] = original_value


def test_check_conda_prefix_warning_is_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conda_prefix = tmp_path / "envs" / "example"
    conda_python = conda_prefix / ("python.exe" if os.name == "nt" else "bin/python")
    conda_python.parent.mkdir(parents=True)
    conda_python.write_text("")

    monkeypatch.setenv("CONDA_PREFIX", str(conda_prefix))
    monkeypatch.setattr(
        "unidep._cli.sys.executable",
        str(tmp_path / "outside/bin/python"),
    )

    with pytest.warns(
        UserWarning,
        match="different Python than the active Conda environment",
    ) as record:
        _check_conda_prefix(None)

    msg = str(record[0].message)
    assert "Detected:" in msg
    assert "Do this:" in msg
    assert "Tip:" in msg
    assert str(conda_python) in msg
    assert "uv run" in msg


def test_install_command_without_conda_executable_raises_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - python
            """,
        ),
    )

    conda_prefix = tmp_path / "envs" / "example"
    conda_prefix.mkdir(parents=True)
    monkeypatch.setenv("CONDA_PREFIX", str(conda_prefix))
    monkeypatch.setattr(
        "unidep._cli.sys.executable",
        str(tmp_path / "outside/bin/python"),
    )
    monkeypatch.setattr("unidep._cli._maybe_conda_executable", lambda: None)

    with pytest.warns(
        UserWarning,
        match="different Python than the active Conda environment",
    ):
        _check_conda_prefix(None)

    with pytest.raises(RuntimeError) as excinfo:
        _install_command(
            project,
            conda_executable=None,
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=True,
            editable=False,
        )

    msg = str(excinfo.value)
    assert "Conda executable" in msg
    assert "Do this:" in msg
    assert "--skip-conda" in msg


def test_install_command_with_conda_lock_without_conda_executable_raises_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - python
            """,
        ),
    )
    lock = tmp_path / "conda-lock.yml"
    lock.write_text("version: 1\n")

    monkeypatch.setattr("unidep._cli._maybe_conda_executable", lambda: None)

    with pytest.raises(RuntimeError) as excinfo:
        _install_command(
            project,
            conda_executable=None,
            conda_env_name="test-env",
            conda_env_prefix=None,
            conda_lock_file=lock,
            dry_run=True,
            editable=False,
        )

    msg = str(excinfo.value)
    assert "Conda executable" in msg
    assert "Do this:" in msg
    assert "--skip-conda" in msg


@pytest.mark.skipif(
    os.name == "nt",
    reason="On Windows it will search for Conda because of `_maybe_exe`.",
)
def test_maybe_conda_run() -> None:
    with set_env_var("CONDA_EXE", "conda"):
        result = _maybe_conda_run("conda", "my_env", None)
        assert result == ["conda", "run", "--name", "my_env"]

    p = Path("/path/to/env")
    with set_env_var("CONDA_EXE", "conda"):
        result = _maybe_conda_run("conda", None, p)
        assert result == ["conda", "run", "--prefix", str(p)]

    with set_env_var("MAMBA_EXE", "mamba"):
        result = _maybe_conda_run("mamba", "my_env", None)
        assert result == ["mamba", "run", "--name", "my_env"]


@pytest.mark.skipif(
    os.name == "nt",
    reason="Uses POSIX-style conda prefixes in assertions.",
)
def test_active_conda_prefix_is_used_when_python_runs_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prefix = tmp_path / "arch-env"
    external_python = tmp_path / "uv-tool" / "bin" / "python"
    python_executable = prefix / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text("")
    external_python.parent.mkdir(parents=True)
    external_python.write_text("")

    monkeypatch.setenv("CONDA_PREFIX", str(prefix))
    monkeypatch.setenv("CONDA_EXE", "conda")
    monkeypatch.setattr("unidep._cli.sys.executable", str(external_python))

    assert _python_executable("conda", None, None) == str(python_executable)
    assert _maybe_conda_run("conda", None, None) == [
        "conda",
        "run",
        "--prefix",
        str(prefix),
    ]


def test_python_executable_prefers_active_prefix_when_python_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prefix = tmp_path / "arch-env"
    external_python = tmp_path / "uv-tool" / "bin" / "python"
    expected_python = prefix / ("python.exe" if os.name == "nt" else "bin/python")
    prefix.mkdir()
    external_python.parent.mkdir(parents=True)
    external_python.write_text("")

    monkeypatch.setenv("CONDA_PREFIX", str(prefix))
    monkeypatch.setattr("unidep._cli.sys.executable", str(external_python))

    assert _python_executable("conda", None, None) == str(expected_python)


@pytest.mark.skipif(
    os.name == "nt",
    reason="Uses POSIX-style conda prefixes in assertions.",
)
def test_active_conda_prefix_reuses_env_name_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prefix = tmp_path / "named-env"
    python_executable = prefix / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text("")

    def fake_env_name_to_prefix(
        conda_executable: CondaExecutable,  # noqa: ARG001
        conda_env_name: str,  # noqa: ARG001
        *,
        raise_if_not_found: bool = True,  # noqa: ARG001
    ) -> Path:
        return prefix

    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "named-env")
    monkeypatch.setattr(
        "unidep._cli._conda_env_name_to_prefix",
        fake_env_name_to_prefix,
    )

    assert _python_executable("conda", None, None) == str(python_executable)
    assert _maybe_conda_run("conda", None, None) == [
        "conda",
        "run",
        "--prefix",
        str(prefix),
    ]


def test_maybe_create_conda_env_args_creates_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Test that _maybe_create_conda_env_args creates the environment if it doesn't exist.

    This simulates running:
      unidep install --conda-env-name non-existing-env .
    and checks that the function to create a conda environment is called.
    """
    # Create a flag to record if _create_conda_environment is called
    created = []

    # Define a fake _create_conda_environment that records its call
    def fake_create(
        conda_executable: CondaExecutable,  # noqa: ARG001
        *args: str,
    ) -> None:
        created.append(args)
        print("Fake create called with", args)

    # Patch the _create_conda_environment function
    monkeypatch.setattr(
        "unidep._cli._create_conda_environment",
        fake_create,
    )

    # Patch _conda_env_name_to_prefix to simulate that the environment is missing.
    def fake_env_name_to_prefix(
        conda_executable: CondaExecutable,  # noqa: ARG001
        env_name: str,  # noqa: ARG001
        *,
        raise_if_not_found: bool = True,  # noqa: ARG001
    ) -> Path | None:
        # Simulate that for "non-existing-env" no environment exists.
        return None

    monkeypatch.setattr(
        "unidep._cli._conda_env_name_to_prefix",
        fake_env_name_to_prefix,
    )

    # Now call _maybe_create_conda_env_args with a non-existing environment name.

    args = _maybe_create_conda_env_args("conda", "non-existing-env", None)

    # Check that our fake_create was called (i.e. the environment creation was triggered)
    assert created, (
        "Expected environment creation to be triggered for non-existing env."
    )
    # Also, the returned arguments should be the standard ones for a named env.
    assert args == ["--name", "non-existing-env"]

    # Optionally, verify that our fake function printed the expected message.
    output = capsys.readouterr().out
    assert "Fake create called with" in output

    # Now with a prefix
    prefix = Path("/home/user/micromamba/envs/non-existing-env")
    args = _maybe_create_conda_env_args("conda", None, prefix)

    # Check that our fake_create was called (i.e. the environment creation was triggered)
    assert created, (
        "Expected environment creation to be triggered for non-existing env."
    )
    # Also, the returned arguments should be the standard ones for a named env.
    assert args == ["--prefix", str(prefix)]

    # Optionally, verify that our fake function printed the expected message.
    output = capsys.readouterr().out
    assert "Fake create called with" in output
