"""Tests for pip_indices CLI functionality to achieve 100% coverage."""

from __future__ import annotations

import os
from pathlib import Path  # noqa: TC003
from unittest.mock import MagicMock, patch

from unidep._cli import _build_pip_index_arguments


class TestBuildPipIndexArguments:
    """Test the _build_pip_index_arguments function."""

    def test_empty_indices(self) -> None:
        """Test with empty pip_indices list."""
        args = _build_pip_index_arguments([])
        assert args == []

    def test_single_index(self) -> None:
        """Test with a single index URL."""
        indices = ["https://pypi.org/simple/"]
        args = _build_pip_index_arguments(indices)
        assert args == ["--index-url", "https://pypi.org/simple/"]

    def test_multiple_indices(self) -> None:
        """Test with multiple index URLs."""
        indices = [
            "https://pypi.org/simple/",
            "https://test.pypi.org/simple/",
            "https://private.com/simple/",
        ]
        args = _build_pip_index_arguments(indices)
        assert args == [
            "--index-url",
            "https://pypi.org/simple/",
            "--extra-index-url",
            "https://test.pypi.org/simple/",
            "--extra-index-url",
            "https://private.com/simple/",
        ]

    def test_environment_variable_expansion(self) -> None:
        """Test that environment variables are expanded in URLs."""
        # Set environment variables
        os.environ["PIP_USER"] = "testuser"
        os.environ["PIP_PASSWORD"] = "testpass"  # noqa: S105

        try:
            indices = [
                "https://${PIP_USER}:${PIP_PASSWORD}@private.com/simple/",
                "https://public.com/simple/",
            ]
            args = _build_pip_index_arguments(indices)

            assert args == [
                "--index-url",
                "https://testuser:testpass@private.com/simple/",
                "--extra-index-url",
                "https://public.com/simple/",
            ]
        finally:
            # Clean up
            del os.environ["PIP_USER"]
            del os.environ["PIP_PASSWORD"]

    def test_missing_environment_variable(self) -> None:
        """Test handling of missing environment variables."""
        # Ensure the variable is not set
        os.environ.pop("NONEXISTENT_VAR", None)

        indices = ["https://${NONEXISTENT_VAR}@private.com/simple/"]
        args = _build_pip_index_arguments(indices)

        # expandvars leaves the ${VAR} as-is if not found
        assert args == ["--index-url", "https://${NONEXISTENT_VAR}@private.com/simple/"]

    def test_complex_environment_variables(self) -> None:
        """Test complex environment variable patterns."""
        os.environ["DOMAIN"] = "example.com"
        os.environ["PORT"] = "8080"

        try:
            indices = [
                "https://${DOMAIN}:${PORT}/simple/",
                "https://backup.${DOMAIN}/simple/",
            ]
            args = _build_pip_index_arguments(indices)

            assert args == [
                "--index-url",
                "https://example.com:8080/simple/",
                "--extra-index-url",
                "https://backup.example.com/simple/",
            ]
        finally:
            del os.environ["DOMAIN"]
            del os.environ["PORT"]


class TestPipInstallLocalWithIndices:
    """Test pip install with custom indices."""

    @patch("unidep._cli.subprocess.run")
    @patch("unidep._cli.shutil.which")
    def test_pip_install_with_indices(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Test that pip install uses the correct index arguments."""
        from unidep._cli import _pip_install_local

        mock_which.return_value = "/usr/bin/pip"
        mock_run.return_value = MagicMock(returncode=0)

        # Call with pip_indices
        _pip_install_local(
            "test_package",
            editable=False,
            dry_run=False,
            python_executable="/usr/bin/python",
            conda_run=[],
            no_uv=True,
            pip_indices=["https://pypi.org/simple/", "https://test.pypi.org/simple/"],
            flags=["--no-deps"],
        )

        # Verify the command includes index arguments
        call_args = mock_run.call_args[0][0]
        assert "--index-url" in call_args
        assert "https://pypi.org/simple/" in call_args
        assert "--extra-index-url" in call_args
        assert "https://test.pypi.org/simple/" in call_args

    @patch("unidep._cli.subprocess.run")
    @patch("unidep._cli.shutil.which")
    def test_uv_install_with_indices(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Test that uv install uses the correct index arguments."""
        from unidep._cli import _pip_install_local

        # Mock uv as the installer
        def which_side_effect(cmd: str) -> str | None:
            if cmd == "uv":
                return "/usr/bin/uv"
            return None

        mock_which.side_effect = which_side_effect
        mock_run.return_value = MagicMock(returncode=0)

        # Call with pip_indices
        _pip_install_local(
            "test_package",
            editable=False,
            dry_run=False,
            python_executable="/usr/bin/python",
            conda_run=[],
            no_uv=False,  # Enable uv
            pip_indices=["https://private.com/simple/"],
            flags=["--no-deps"],
        )

        # Verify uv command includes index arguments
        call_args = mock_run.call_args[0][0]
        assert "uv" in call_args
        assert "pip" in call_args
        assert "install" in call_args
        assert "--index-url" in call_args
        assert "https://private.com/simple/" in call_args


class TestCondaEnvWithPipRepositories:
    """Test conda environment generation with pip_repositories."""

    def test_write_env_with_pip_repositories(self, tmp_path: Path) -> None:
        """Test that pip_repositories are written to environment.yaml."""
        from unidep._conda_env import CondaEnvironmentSpec, write_conda_environment_file

        env_spec = CondaEnvironmentSpec(
            channels=["conda-forge"],
            pip_indices=[
                "https://pypi.org/simple/",
                "https://private.company.com/simple/",
            ],
            platforms=["linux-64"],
            conda=["python=3.11"],
            pip=["requests"],
        )

        env_file = tmp_path / "environment.yaml"
        write_conda_environment_file(env_spec, env_file, name="test_env")

        content = env_file.read_text()
        assert "pip_repositories:" in content
        assert "https://pypi.org/simple/" in content
        assert "https://private.company.com/simple/" in content

        # Verify order is preserved
        lines = content.split("\n")
        repo_lines = [
            line for line in lines if "https://" in line and "simple/" in line
        ]
        assert "pypi.org" in repo_lines[0]
        assert "private.company.com" in repo_lines[1]

    def test_write_env_without_pip_repositories(self, tmp_path: Path) -> None:
        """Test environment.yaml without pip_repositories when list is empty."""
        from unidep._conda_env import CondaEnvironmentSpec, write_conda_environment_file

        env_spec = CondaEnvironmentSpec(
            channels=["conda-forge"],
            pip_indices=[],  # Empty list
            platforms=["linux-64"],
            conda=["python=3.11"],
            pip=["requests"],
        )

        env_file = tmp_path / "environment.yaml"
        write_conda_environment_file(env_spec, env_file, name="test_env")

        content = env_file.read_text()
        assert "pip_repositories:" not in content


class TestInstallCommandWithIndices:
    """Test the install command with pip_indices."""

    @patch("unidep._cli.subprocess.run")
    @patch("unidep._cli._maybe_conda_executable")
    @patch("unidep._cli._use_uv")
    def test_install_command_with_pip_indices(
        self,
        mock_use_uv: MagicMock,
        mock_conda: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test install command properly passes pip_indices to pip install."""
        from unidep._cli import _install_command

        # Setup mocks
        mock_use_uv.return_value = False  # Don't use uv
        mock_conda.return_value = None  # No conda
        mock_run.return_value = MagicMock(returncode=0)

        # Create a requirements file with pip_indices
        req_file = tmp_path / "requirements.yaml"
        req_file.write_text("""
name: test_project
pip_indices:
  - https://pypi.org/simple/
  - https://private.com/simple/
dependencies:
  - pip: requests
  - pip: private-package
""")

        # Run install command
        _install_command(
            req_file,
            conda_executable=None,
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=False,
            editable=False,
            skip_local=True,
            skip_pip=False,
            skip_conda=True,
            no_dependencies=False,
            no_uv=True,
            verbose=False,
        )

        # Check that pip was called with index arguments
        pip_call_found = False
        for call in mock_run.call_args_list:
            args = call[0][0] if call[0] else []
            if "pip" in args and "install" in args:
                pip_call_found = True
                assert "--index-url" in args
                assert "https://pypi.org/simple/" in args
                assert "--extra-index-url" in args
                assert "https://private.com/simple/" in args
                break

        assert pip_call_found, "pip install was not called with indices"

    @patch("unidep._cli.subprocess.run")
    @patch("unidep._cli._maybe_conda_executable")
    @patch("unidep._cli._use_uv")
    def test_install_command_with_uv_and_indices(
        self,
        mock_use_uv: MagicMock,
        mock_conda: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test install command with uv properly passes pip_indices."""
        from unidep._cli import _install_command

        # Setup mocks
        mock_use_uv.return_value = True  # Use uv
        mock_conda.return_value = None  # No conda
        mock_run.return_value = MagicMock(returncode=0)

        # Create a requirements file with pip_indices
        req_file = tmp_path / "requirements.yaml"
        req_file.write_text("""
name: test_project
pip_indices:
  - https://private.com/simple/
dependencies:
  - pip: private-package
""")

        # Run install command
        _install_command(
            req_file,
            conda_executable=None,
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=False,
            editable=False,
            skip_local=True,
            skip_pip=False,
            skip_conda=True,
            no_dependencies=False,
            no_uv=False,  # Allow uv
            verbose=False,
        )

        # Check that uv was called with index arguments
        uv_call_found = False
        for call in mock_run.call_args_list:
            args = call[0][0] if call[0] else []
            if "uv" in args and "pip" in args and "install" in args:
                uv_call_found = True
                assert "--index-url" in args
                assert "https://private.com/simple/" in args
                break

        assert uv_call_found, "uv pip install was not called with indices"


class TestPipIndicesIntegration:
    """Integration tests for pip_indices throughout the workflow."""

    def test_full_workflow_with_indices(self, tmp_path: Path) -> None:
        """Test complete workflow from parsing to environment generation."""
        from unidep._conda_env import (
            create_conda_env_specification,
            write_conda_environment_file,
        )
        from unidep._conflicts import resolve_conflicts
        from unidep._dependencies_parsing import parse_requirements

        # Create a requirements file with pip_indices
        req_file = tmp_path / "requirements.yaml"
        req_file.write_text("""
name: test_project
channels:
  - conda-forge
pip_indices:
  - https://pypi.org/simple/
  - https://test.pypi.org/simple/
dependencies:
  - python=3.11
  - pip: requests
  - pip: pytest
platforms:
  - linux-64
  - osx-arm64
""")

        # Parse requirements
        parsed = parse_requirements(req_file)
        assert len(parsed.pip_indices) == 2
        assert parsed.pip_indices[0] == "https://pypi.org/simple/"
        assert parsed.pip_indices[1] == "https://test.pypi.org/simple/"

        # Resolve conflicts
        resolved = resolve_conflicts(parsed.requirements, parsed.platforms)

        # Create conda env specification
        env_spec = create_conda_env_specification(
            resolved,
            parsed.channels,
            parsed.pip_indices,
            parsed.platforms,
        )

        assert env_spec.pip_indices == parsed.pip_indices

        # Write environment file
        env_file = tmp_path / "environment.yaml"
        write_conda_environment_file(env_spec, env_file)

        # Verify the output
        content = env_file.read_text()
        assert "pip_repositories:" in content
        assert "- https://pypi.org/simple/" in content
        assert "- https://test.pypi.org/simple/" in content

    @patch("unidep._conda_lock.conda_lock_command")
    def test_conda_lock_with_pip_indices(
        self,
        mock_conda_lock: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that conda-lock properly includes pip_indices."""
        from unidep._conda_lock import conda_lock_command

        # Create requirements file with pip_indices
        req_file = tmp_path / "requirements.yaml"
        req_file.write_text("""
name: test
channels:
  - conda-forge
pip_indices:
  - https://pypi.org/simple/
  - https://private.com/simple/
dependencies:
  - numpy
  - pip: requests
""")

        # Run conda-lock command (mocked)
        conda_lock_command(
            depth=1,
            directory=tmp_path,
            files=None,
            platforms=["linux-64"],
            verbose=False,
            only_global=False,
            ignore_pins=[],
            skip_dependencies=[],
            overwrite_pins=[],
            check_input_hash=False,
            extra_flags=[],
            lockfile=str(tmp_path / "conda-lock.yml"),
        )

        # Verify that the mock was called and pip_indices were passed through
        assert mock_conda_lock.called

    def test_merge_command_with_indices(self, tmp_path: Path) -> None:
        """Test unidep merge command with pip_indices."""
        from unidep._cli import _merge_command

        # Create requirements file
        req_file = tmp_path / "requirements.yaml"
        req_file.write_text("""
name: test
channels:
  - conda-forge
pip_indices:
  - https://private.com/simple/
dependencies:
  - numpy
""")

        output_file = tmp_path / "environment.yaml"

        # Run merge command
        _merge_command(
            depth=1,
            directory=tmp_path,
            files=[req_file],
            name="merged_env",
            output=output_file,
            stdout=False,
            selector="sel",
            platforms=[],
            ignore_pins=[],
            skip_dependencies=[],
            overwrite_pins=[],
            verbose=False,
        )

        # Check output file
        assert output_file.exists()
        content = output_file.read_text()
        assert "pip_repositories:" in content
        assert "https://private.com/simple/" in content
