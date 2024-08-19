"""Tests for the `unidep._cli` module (installing conda environment from lock file)."""

import subprocess
from pathlib import Path
from unittest.mock import Mock, call, patch

import pytest

from unidep._cli import (
    CondaExecutable,
    _create_env_from_lock,
    _verify_conda_lock_installed,
)


@pytest.fixture
def mock_subprocess_run(monkeypatch: pytest.MonkeyPatch) -> Mock:
    mock = Mock()
    monkeypatch.setattr("subprocess.run", mock)
    return mock


@pytest.fixture
def mock_print(monkeypatch: pytest.MonkeyPatch) -> Mock:
    mock = Mock()
    monkeypatch.setattr("builtins.print", mock)
    return mock


@pytest.mark.parametrize("conda_executable", ["conda", "mamba", "micromamba"])
@pytest.mark.parametrize(
    "env_spec",
    [
        {"conda_env_name": "test_env", "conda_env_prefix": None},
        {"conda_env_name": None, "conda_env_prefix": Path("/path/to/env")},
    ],
)
def test_create_env_from_lock_dry_run(
    conda_executable: CondaExecutable,
    env_spec: dict,
    mock_subprocess_run: Mock,
    mock_print: Mock,
) -> None:
    conda_lock_file = Path("conda-lock.yml")

    with patch("unidep._cli._verify_conda_lock_installed"):
        _create_env_from_lock(
            conda_lock_file=conda_lock_file,
            conda_executable=conda_executable,
            **env_spec,
            dry_run=True,
            verbose=True,
        )

    # Check that subprocess.run was not called
    mock_subprocess_run.assert_not_called()

    # Check that appropriate messages were printed
    env_identifier = (
        f"'{env_spec['conda_env_name']}'"
        if env_spec["conda_env_name"]
        else f"at '{env_spec['conda_env_prefix']}'"
    )

    assert len(mock_print.call_args_list) == 2

    # Check the first message (creating environment)
    first_call = mock_print.call_args_list[0]
    assert first_call.args[0].startswith(
        f"📦 Creating conda environment {env_identifier} with ",
    )

    # Check the command string separately
    cmd_str = first_call.args[0]
    if conda_executable == "micromamba":
        assert "micromamba create" in cmd_str or "micromamba.exe create" in cmd_str
        assert "-f conda-lock.yml" in cmd_str
        assert "--yes" in cmd_str
        assert "--verbose" in cmd_str
    else:
        assert "conda-lock install" in cmd_str
        assert "--log-level=DEBUG" in cmd_str
        if conda_executable == "mamba":
            assert "--mamba" in cmd_str
        elif conda_executable == "conda":
            assert "--conda conda" in cmd_str

    if env_spec["conda_env_name"]:
        assert f"--name {env_spec['conda_env_name']}" in cmd_str
    elif env_spec["conda_env_prefix"]:
        assert f"--prefix {env_spec['conda_env_prefix']}" in cmd_str

    # Check the second message (dry run completed)
    assert mock_print.call_args_list[1] == call(
        "🏁 Dry run completed. No environment was created.",
    )


def test_create_env_from_lock_no_env_specified(mock_print: Mock) -> None:
    conda_lock_file = Path("conda-lock.yml")

    with pytest.raises(SystemExit):
        _create_env_from_lock(
            conda_lock_file=conda_lock_file,
            conda_executable="conda",
            conda_env_name=None,
            conda_env_prefix=None,
            dry_run=True,
            verbose=True,
        )

    mock_print.assert_called_once_with(
        "❌ Please provide either `--conda-env-name` or"
        " `--conda-env-prefix` when using `--conda-lock-file`.",
    )


def test_verify_conda_lock_installed_not_found(
    monkeypatch: pytest.MonkeyPatch,
    mock_print: Mock,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)

    with pytest.raises(SystemExit):
        _verify_conda_lock_installed()

    assert (
        "❌ conda-lock is not installed or not found in PATH."
        in mock_print.call_args[0][0]
    )


def test_verify_conda_lock_installed_not_working(
    monkeypatch: pytest.MonkeyPatch,
    mock_print: Mock,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/path/to/conda-lock")
    monkeypatch.setattr(
        subprocess,
        "run",
        Mock(side_effect=subprocess.CalledProcessError(1, "conda-lock")),
    )

    with pytest.raises(SystemExit):
        _verify_conda_lock_installed()

    assert (
        "❌ conda-lock is installed but not working correctly."
        in mock_print.call_args[0][0]
    )
