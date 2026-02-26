"""Tests for setuptools integration."""

from __future__ import annotations

import json
import textwrap
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from unidep._index_install import InstallRuntime, install_package_specs_command
from unidep._setuptools_integration import _write_unidep_metadata_egg_info
from unidep.utils import (
    package_name_from_path,
    package_name_from_pyproject_toml,
    package_name_from_setup_cfg,
    package_name_from_setup_py,
)

REPO_ROOT = Path(__file__).parent.parent


def test_package_name_from_path() -> None:
    example = REPO_ROOT / "example"
    # Could not find the package name, so it uses the folder name
    assert package_name_from_path(example) == "example"
    # The following should read from the setup.py or pyproject.toml file
    assert package_name_from_path(example / "hatch_project") == "hatch_project"
    assert (
        package_name_from_pyproject_toml(example / "hatch_project" / "pyproject.toml")
        == "hatch_project"
    )
    assert package_name_from_path(example / "hatch2_project") == "hatch2_project"
    assert (
        package_name_from_pyproject_toml(example / "hatch2_project" / "pyproject.toml")
        == "hatch2_project"
    )
    assert (
        package_name_from_path(example / "pyproject_toml_project")
        == "pyproject_toml_project"
    )
    assert (
        package_name_from_pyproject_toml(
            example / "pyproject_toml_project" / "pyproject.toml",
        )
        == "pyproject_toml_project"
    )
    assert package_name_from_path(example / "setup_py_project") == "setup_py_project"
    assert (
        package_name_from_setup_py(example / "setup_py_project" / "setup.py")
        == "setup_py_project"
    )
    assert (
        package_name_from_path(example / "setuptools_project") == "setuptools_project"
    )
    assert (
        package_name_from_pyproject_toml(
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
    assert package_name_from_path(tmp_path) == "setup_cfg_project"
    assert package_name_from_setup_cfg(setup_cfg) == "setup_cfg_project"
    missing = tmp_path / "missing" / "setup.cfg"
    assert not missing.exists()
    with pytest.raises(KeyError):
        package_name_from_setup_cfg(missing)

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
        package_name_from_setup_cfg(setup_cfg2)


def test_package_name_from_setup_py_requires_literal_name(tmp_path: Path) -> None:
    setup_py = tmp_path / "setup.py"
    setup_py.write_text(
        textwrap.dedent(
            """\
            from setuptools import setup
            NAME = "dynamic_name"
            setup(name=NAME)
            """,
        ),
    )

    with pytest.raises(
        KeyError,
        match=r"Could not find the package name in the setup.py",
    ):
        package_name_from_setup_py(setup_py)


def test_package_name_from_path_falls_back_on_invalid_pyproject(tmp_path: Path) -> None:
    pyproject_toml = tmp_path / "pyproject.toml"
    pyproject_toml.write_text("this is not valid toml = [")

    assert package_name_from_path(tmp_path) == tmp_path.name


def test_package_name_from_path_falls_back_on_invalid_setup_py(tmp_path: Path) -> None:
    setup_py = tmp_path / "setup.py"
    setup_py.write_text("from setuptools import setup\nsetup(name='missing'")

    assert package_name_from_path(tmp_path) == tmp_path.name


def test_package_name_from_path_does_not_suppress_unexpected_errors(
    tmp_path: Path,
) -> None:
    setup_py = tmp_path / "setup.py"
    setup_py.write_text("from setuptools import setup\nsetup(name='pkg')")

    with patch(
        "unidep.utils.package_name_from_setup_py",
        side_effect=RuntimeError("boom"),
    ), pytest.raises(RuntimeError, match="boom"):
        package_name_from_path(tmp_path)


def test_write_unidep_metadata_egg_info(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pip: requests >=2
            platforms:
              - linux-64
            """,
        ),
    )

    class _Metadata:
        @staticmethod
        def get_name() -> str:
            return "demo-package"

        @staticmethod
        def get_version() -> str:
            return "1.2.3"

    class _Distribution:
        metadata = _Metadata()

    class _Cmd:
        distribution = _Distribution()
        written: str | None = None

        def write_or_delete_file(
            self,
            what: str,  # noqa: ARG002
            filename: str,  # noqa: ARG002
            data: str,
            force: bool,  # noqa: FBT001, ARG002
        ) -> None:
            self.written = data

    monkeypatch.chdir(tmp_path)
    cmd = _Cmd()
    _write_unidep_metadata_egg_info(cmd, "unidep.json", str(tmp_path / "unidep.json"))
    assert cmd.written is not None
    payload = json.loads(cmd.written)
    assert payload["schema_version"] == 1
    assert payload["project"] == "demo-package"
    assert payload["version"] == "1.2.3"


def test_setuptools_metadata_written_to_wheel_is_used_by_package_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: setuptools metadata writer output is consumed by index install."""
    (tmp_path / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pip: requests >=2
            platforms:
              - linux-64
            """,
        ),
    )

    class _Metadata:
        @staticmethod
        def get_name() -> str:
            return "demo-package"

        @staticmethod
        def get_version() -> str:
            return "1.2.3"

    class _Distribution:
        metadata = _Metadata()

    class _Cmd:
        distribution = _Distribution()
        written: str | None = None

        def write_or_delete_file(
            self,
            what: str,  # noqa: ARG002
            filename: str,  # noqa: ARG002
            data: str,
            force: bool,  # noqa: FBT001, ARG002
        ) -> None:
            self.written = data

    monkeypatch.chdir(tmp_path)
    cmd = _Cmd()
    _write_unidep_metadata_egg_info(cmd, "unidep.json", str(tmp_path / "unidep.json"))
    assert cmd.written is not None

    wheel = tmp_path / "demo_package-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("demo_package-1.2.3.dist-info/unidep.json", cmd.written)

    calls: list[list[str]] = []

    def _run_capture(cmd: list[str] | tuple[str, ...], **_: Any) -> None:
        calls.append([str(c) for c in cmd])

    runtime = InstallRuntime(
        maybe_conda_executable=lambda: None,
        maybe_conda_run=lambda *_args, **_kwargs: [],
        python_executable=lambda *_args, **_kwargs: "python",
        maybe_create_conda_env_args=lambda *_args, **_kwargs: [],
        maybe_exe=lambda conda_executable: conda_executable,
        format_inline_conda_package=lambda pkg: pkg,
        use_uv=lambda _no_uv: False,
        identify_current_platform=lambda: "linux-64",
        run_subprocess=_run_capture,
    )

    with patch(
        "unidep._index_install._download_package_artifact",
        return_value=wheel,
    ):
        install_package_specs_command(
            "demo-package==1.2.3",
            conda_executable=None,
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=False,
            editable=False,
            runtime=runtime,
        )

    assert ["python", "-m", "pip", "install", "requests >=2"] in calls
    assert [
        "python",
        "-m",
        "pip",
        "install",
        "--no-deps",
        "demo-package==1.2.3",
    ] in calls
