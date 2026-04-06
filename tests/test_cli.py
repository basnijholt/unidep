"""unidep CLI tests."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, Generator, cast
from unittest.mock import patch

import pytest

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib

from unidep._cli import (
    CondaExecutable,
    _capitalize_dir,
    _collect_selected_conda_like_platforms,
    _conda_env_list,
    _conda_info,
    _conda_root_prefix,
    _find_windows_path,
    _flatten_selected_dependency_entries,
    _identify_conda_executable,
    _install_all_command,
    _install_command,
    _maybe_conda_run,
    _maybe_create_conda_env_args,
    _merge_command,
    _pip_compile_command,
    _pip_subcommand,
    _print_versions,
    _print_with_rich,
)
from unidep._dependencies_parsing import (
    DependencyEntry,
    DependencyOrigin,
    parse_requirements,
)
from unidep.platform_definitions import Spec

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


@pytest.mark.parametrize(
    (
        "content",
        "current_platform",
        "expected_dependency",
        "expected_platforms",
        "excluded_platform",
    ),
    [
        (
            """\
            dependencies:
              - conda: click >=8
              - pip: click  # [osx]
            """,
            "linux-64",
            "  - click >=8",
            ["  - osx-64", "  - osx-arm64"],
            "  - linux-64",
        ),
        (
            """\
            dependencies:
              - pip: click ==0.1
              - conda: click  # [linux64]
            """,
            "osx-arm64",
            "    - click ==0.1",
            ["  - linux-64"],
            "  - osx-arm64",
        ),
    ],
)
def test_merge_uses_selector_platforms_even_for_losing_alternatives(
    tmp_path: Path,
    content: str,
    current_platform: str,
    expected_dependency: str,
    expected_platforms: list[str],
    excluded_platform: str,
) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(textwrap.dedent(content))
    output_file = tmp_path / "environment.yaml"

    with patch("unidep.utils.identify_current_platform", return_value=current_platform):
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

    merged = output_file.read_text()
    assert expected_dependency in merged
    assert "platforms:" in merged
    for expected_platform in expected_platforms:
        assert expected_platform in merged
    assert excluded_platform not in merged


def test_flatten_selected_dependency_entries_includes_optional_groups(
    tmp_path: Path,
) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
              - numpy
            optional_dependencies:
              dev:
                - pytest
            """,
        ),
    )

    requirements = parse_requirements(req_file, extras=[["*"]])
    entries = _flatten_selected_dependency_entries(
        requirements.dependency_entries,
        requirements.optional_dependency_entries,
    )

    def entry_name(entry: DependencyEntry) -> str:
        conda = entry.conda
        pip = entry.pip
        if conda is not None:
            return conda.name
        assert pip is not None
        return pip.name

    assert [entry_name(entry) for entry in entries] == [
        "numpy",
        "pytest",
    ]


def test_collect_selected_conda_like_platforms_uses_both_source_selectors() -> None:
    entry = DependencyEntry(
        identifier="selector-mismatch",
        selector="linux64",
        conda=Spec(name="click", which="conda", selector="linux64"),
        pip=Spec(name="click", which="pip", selector="osx"),
        origin=DependencyOrigin(
            source_file=Path("requirements.yaml"),
            dependency_index=0,
        ),
    )

    assert _collect_selected_conda_like_platforms([entry]) == [
        "linux-64",
        "osx-64",
        "osx-arm64",
    ]


def test_collect_selected_conda_like_platforms_preserves_selector_platforms(
    tmp_path: Path,
) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
              - conda: click >=8
              - pip: click  # [osx]
            """,
        ),
    )

    requirements = parse_requirements(req_file)
    entries = _flatten_selected_dependency_entries(
        requirements.dependency_entries,
        requirements.optional_dependency_entries,
    )

    assert _collect_selected_conda_like_platforms(entries) == [
        "osx-64",
        "osx-arm64",
    ]


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


def test_conda_info_uses_json_helper() -> None:
    _conda_info.cache_clear()
    try:
        with patch(
            "unidep._cli._conda_cli_command_json",
            return_value={"root_prefix": "/opt/conda"},
        ) as conda_cli_command_json:
            assert _conda_info("conda") == {"root_prefix": "/opt/conda"}
        conda_cli_command_json.assert_called_once_with("conda", "info")
    finally:
        _conda_info.cache_clear()


def test_version_uses_rich_when_installed() -> None:
    with (
        patch("importlib.util.find_spec", return_value=object()),
        patch("unidep._cli._print_with_rich") as print_with_rich,
    ):
        _print_versions()

    print_with_rich.assert_called_once()
    rendered = print_with_rich.call_args.args[0]
    assert any(line.startswith("unidep version: ") for line in rendered)
    assert any(line.startswith("packaging version: ") for line in rendered)


def test_print_with_rich_formats_table() -> None:
    fake_rich = ModuleType("rich")
    fake_rich.__path__ = []
    fake_console_module = ModuleType("rich.console")
    fake_table_module = ModuleType("rich.table")

    class FakeConsole:
        printed: ClassVar[list[object]] = []

        def print(self, table: object) -> None:
            self.printed.append(table)

    class FakeTable:
        instances: ClassVar[list[FakeTable]] = []

        def __init__(self, *, show_header: bool) -> None:
            self.show_header = show_header
            self.columns: list[tuple[str, str]] = []
            self.rows: list[tuple[str, str]] = []
            self.instances.append(self)

        def add_column(self, name: str, *, style: str) -> None:
            self.columns.append((name, style))

        def add_row(self, prop: str, value: str) -> None:
            self.rows.append((prop, value))

    fake_console_module_any = cast(Any, fake_console_module)
    fake_table_module_any = cast(Any, fake_table_module)
    fake_console_module_any.Console = FakeConsole
    fake_table_module_any.Table = FakeTable

    with patch.dict(
        sys.modules,
        {
            "rich": fake_rich,
            "rich.console": fake_console_module,
            "rich.table": fake_table_module,
        },
    ):
        _print_with_rich(["unidep version: 1.0", "packaging version: 2.0"])

    assert len(FakeTable.instances) == 1
    table = FakeTable.instances[0]
    assert table.show_header is False
    assert table.columns == [
        ("Property", "cyan"),
        ("Value", "magenta"),
    ]
    assert table.rows == [
        ("unidep version", "1.0"),
        ("packaging version", "2.0"),
    ]
    assert FakeConsole.printed == [table]


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


def test_find_windows_path_returns_existing_mamba_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "os.path.exists",
        lambda path: "mambaforge" in path and str(path).endswith("mamba.exe"),
    )
    found = _find_windows_path("mamba")
    assert found.endswith(r"mambaforge\condabin\mamba.exe")


def test_find_windows_path_returns_existing_micromamba_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "os.path.exists",
        lambda path: "micromamba" in path and str(path).endswith("micromamba.exe"),
    )
    found = _find_windows_path("micromamba")
    assert found.endswith(r"Micromamba\condabin\micromamba.exe")


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


def test_maybe_conda_run_without_executable_returns_empty() -> None:
    assert _maybe_conda_run(None, "my_env", None) == []


def test_maybe_conda_run_without_active_environment_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.delenv("MAMBA_ROOT_PREFIX", raising=False)
    assert _maybe_conda_run("conda", None, None) == []


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


def test_install_command_with_conda_lock_skips_dependency_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - numpy
            """,
        ),
    )
    created: list[tuple[Path, str]] = []

    def fake_create_env_from_lock(
        conda_lock_file: Path,
        conda_executable: str,
        **_: object,
    ) -> None:
        created.append((conda_lock_file, conda_executable))

    def fake_python_executable(*_args: object) -> str:
        return "python"

    monkeypatch.setattr("unidep._cli._create_env_from_lock", fake_create_env_from_lock)
    monkeypatch.setattr("unidep._cli.identify_current_platform", lambda: "linux-64")
    monkeypatch.setattr("unidep._cli._python_executable", fake_python_executable)

    _install_command(
        req_file,
        conda_executable="conda",
        conda_env_name="test-env",
        conda_env_prefix=None,
        conda_lock_file=Path("conda-lock.yml"),
        dry_run=True,
        editable=False,
        skip_local=True,
        verbose=False,
    )

    assert created == [(Path("conda-lock.yml"), "conda")]
    output = capsys.readouterr().out
    assert "Installing conda dependencies" not in output
    assert "Installing pip dependencies" not in output
