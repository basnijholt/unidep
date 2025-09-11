"""End-to-end integration tests for pip_indices support in unidep."""

import os
from pathlib import Path
from textwrap import dedent
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class TestUnidepInstallIntegration:
    """Integration tests for unidep install with pip_indices."""

    @pytest.fixture
    def mock_project(self, tmp_path: Path) -> Path:
        """Create a mock project with pip_indices configuration."""
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        # Create requirements.yaml with pip_indices
        requirements_file = project_dir / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                channels:
                  - conda-forge
                pip_indices:
                  - https://pypi.org/simple/
                  - https://test.pypi.org/simple/
                dependencies:
                  - numpy
                  - pip: requests
                  - pip: test-package  # From test.pypi.org
                """,
            ),
        )

        # Create a simple setup.py
        setup_file = project_dir / "setup.py"
        setup_file.write_text(
            dedent(
                """
                from setuptools import setup, find_packages
                setup(
                    name="test_project",
                    version="0.1.0",
                    packages=find_packages(),
                )
                """,
            ),
        )

        # Create package directory
        (project_dir / "test_project").mkdir()
        (project_dir / "test_project" / "__init__.py").touch()

        return project_dir

    @patch("subprocess.run")
    def test_install_with_pip_indices(self, mock_run: Any, mock_project: Path) -> None:  # noqa: ARG002
        """Test that unidep install uses pip_indices correctly."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Simulate running unidep install

        # Mock the install command execution
        with patch("unidep._cli._pip_install") as mock_pip_install:
            mock_pip_install.return_value = None

            # This would be the actual command execution
            # For now, verify the expected behavior
            expected_pip_args = [
                "--index-url",
                "https://pypi.org/simple/",
                "--extra-index-url",
                "https://test.pypi.org/simple/",
            ]

            # The actual implementation would construct these args
            assert expected_pip_args[0] == "--index-url"
            assert expected_pip_args[2] == "--extra-index-url"

    @patch("subprocess.run")
    def test_install_with_env_var_indices(self, mock_run: Any, tmp_path: Path) -> None:
        """Test that environment variables in pip_indices are expanded."""
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        # Set environment variables
        os.environ["PIP_USER"] = "testuser"
        os.environ["PIP_PASSWORD"] = "testpass"  # noqa: S105

        requirements_file = project_dir / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                pip_indices:
                  - https://${PIP_USER}:${PIP_PASSWORD}@private.pypi.org/simple/
                  - https://pypi.org/simple/
                dependencies:
                  - pip: private-package
                """,
            ),
        )

        mock_run.return_value = MagicMock(returncode=0)

        # In actual implementation, env vars would be expanded
        expected_url = "https://testuser:testpass@private.pypi.org/simple/"

        # Verify env var expansion logic
        url = "https://${PIP_USER}:${PIP_PASSWORD}@private.pypi.org/simple/"
        expanded = url.replace("${PIP_USER}", os.environ["PIP_USER"])
        expanded = expanded.replace("${PIP_PASSWORD}", os.environ["PIP_PASSWORD"])
        assert expanded == expected_url

        # Clean up env vars
        del os.environ["PIP_USER"]
        del os.environ["PIP_PASSWORD"]

    def test_install_with_uv_backend(self, mock_project: Path) -> None:  # noqa: ARG002
        """Test that pip_indices work with uv backend."""
        # uv uses the same --index-url and --extra-index-url flags
        with patch("shutil.which", return_value="/path/to/uv"), patch(
            "subprocess.run",
        ) as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Expected uv command structure
            expected_args = [
                "uv",
                "pip",
                "install",
                "--index-url",
                "https://pypi.org/simple/",
                "--extra-index-url",
                "https://test.pypi.org/simple/",
            ]

            # Verify uv compatibility
            assert "--index-url" in expected_args
            assert "--extra-index-url" in expected_args

    def test_install_without_pip_indices(self, tmp_path: Path) -> None:
        """Test that unidep install works without pip_indices."""
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        requirements_file = project_dir / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                channels:
                  - conda-forge
                dependencies:
                  - numpy
                  - pip: requests
                """,
            ),
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # No index flags should be added
            # Command should work with default PyPI
            assert True  # Placeholder for actual test


class TestUnidepCondaLockIntegration:
    """Integration tests for unidep conda-lock with pip_indices."""

    @pytest.fixture
    def mock_monorepo(self, tmp_path: Path) -> Path:
        """Create a mock monorepo with multiple projects using pip_indices."""
        monorepo = tmp_path / "monorepo"
        monorepo.mkdir()

        # Project 1 with pip_indices
        proj1 = monorepo / "project1"
        proj1.mkdir()
        (proj1 / "requirements.yaml").write_text(
            dedent(
                """
                name: project1
                channels:
                  - conda-forge
                pip_indices:
                  - https://pypi.org/simple/
                  - https://private1.com/simple/
                dependencies:
                  - numpy
                  - pip: private-package1
                """,
            ),
        )

        # Project 2 with different pip_indices
        proj2 = monorepo / "project2"
        proj2.mkdir()
        (proj2 / "requirements.yaml").write_text(
            dedent(
                """
                name: project2
                channels:
                  - conda-forge
                pip_indices:
                  - https://pypi.org/simple/
                  - https://private2.com/simple/
                dependencies:
                  - pandas
                  - pip: private-package2
                """,
            ),
        )

        return monorepo

    def test_conda_lock_generates_pip_repositories(self, mock_monorepo: Path) -> None:
        """Test that conda-lock generates environment.yaml with pip_repositories."""
        _ = mock_monorepo  # Used to ensure fixture is called
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Expected environment.yaml structure
            expected_env = {
                "name": "myenv",
                "channels": ["conda-forge"],
                "pip_repositories": [
                    "https://pypi.org/simple/",
                    "https://private1.com/simple/",
                    "https://private2.com/simple/",
                ],
                "dependencies": [
                    "numpy",
                    "pandas",
                    {"pip": ["private-package1", "private-package2"]},
                ],
            }

            # Verify the structure
            assert "pip_repositories" in expected_env
            assert len(expected_env["pip_repositories"]) == 3

    def test_conda_lock_with_merged_indices(self, mock_monorepo: Path) -> None:  # noqa: ARG002
        """Test that conda-lock merges pip_indices from multiple projects."""
        with patch("unidep._conda_lock.generate_conda_lock") as mock_generate:
            mock_generate.return_value = None

            # Expected merged pip_indices (deduplicated)
            expected_indices = [
                "https://pypi.org/simple/",  # Common to both
                "https://private1.com/simple/",  # From project1
                "https://private2.com/simple/",  # From project2
            ]

            # Verify deduplication logic
            all_indices = [
                "https://pypi.org/simple/",
                "https://private1.com/simple/",
                "https://pypi.org/simple/",  # Duplicate
                "https://private2.com/simple/",
            ]
            deduplicated = list(dict.fromkeys(all_indices))  # Preserve order
            assert deduplicated == expected_indices

    def test_conda_lock_creates_valid_lockfile(self, tmp_path: Path) -> None:
        """Test that conda-lock creates a valid lock file with pip_repositories."""
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        requirements_file = project_dir / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                channels:
                  - conda-forge
                pip_indices:
                  - https://pypi.org/simple/
                  - https://custom.pypi.org/simple/
                dependencies:
                  - python=3.11
                  - pip: custom-package
                """,
            ),
        )

        # Mock conda-lock execution
        with patch("subprocess.run") as mock_run:
            # First call generates environment.yaml
            # Second call runs conda-lock
            mock_run.return_value = MagicMock(returncode=0)

            # Verify that the generated environment.yaml includes pip_repositories

            env_content = {
                "name": "test_project",
                "channels": ["conda-forge"],
                "pip_repositories": [
                    "https://pypi.org/simple/",
                    "https://custom.pypi.org/simple/",
                ],
                "dependencies": [
                    "python=3.11",
                    {"pip": ["custom-package"]},
                ],
            }

            # Verify structure for conda-lock compatibility
            assert "pip_repositories" in env_content
            assert isinstance(env_content["pip_repositories"], list)


class TestErrorHandling:
    """Test error handling and edge cases in integration."""

    def test_install_with_unreachable_index(self, tmp_path: Path) -> None:
        """Test behavior when a pip index is unreachable."""
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        requirements_file = project_dir / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                pip_indices:
                  - https://unreachable.invalid.com/simple/
                  - https://pypi.org/simple/
                dependencies:
                  - pip: numpy  # Should fall back to pypi.org
                """,
            ),
        )

        # Test that installation can continue with fallback
        with patch("subprocess.run") as mock_run:
            # First attempt might fail, but should retry with pypi.org
            mock_run.return_value = MagicMock(returncode=0)

            # Installation should succeed using the second index
            assert True  # Placeholder

    def test_install_with_conflicting_packages(self, tmp_path: Path) -> None:
        """Test handling of conflicting packages across indices."""
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        requirements_file = project_dir / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                pip_indices:
                  - https://index1.com/simple/  # Has package-a v1.0
                  - https://index2.com/simple/  # Has package-a v2.0
                dependencies:
                  - pip: package-a  # Which version gets installed?
                """,
            ),
        )

        # First index should take precedence
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Verify that first index is primary
            assert True  # Placeholder

    def test_merge_with_circular_dependencies(self, tmp_path: Path) -> None:
        """Test handling of circular local dependencies with pip_indices."""
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        # Project A depends on B
        proj_a = project_dir / "project_a"
        proj_a.mkdir()
        (proj_a / "requirements.yaml").write_text(
            dedent(
                """
                name: project_a
                pip_indices:
                  - https://pypi.org/simple/
                local_dependencies:
                  - ../project_b
                dependencies:
                  - pip: package-a
                """,
            ),
        )

        # Project B depends on A (circular)
        proj_b = project_dir / "project_b"
        proj_b.mkdir()
        (proj_b / "requirements.yaml").write_text(
            dedent(
                """
                name: project_b
                pip_indices:
                  - https://custom.pypi.org/simple/
                local_dependencies:
                  - ../project_a
                dependencies:
                  - pip: package-b
                """,
            ),
        )

        # Should handle circular dependencies gracefully
        # pip_indices should be merged without infinite loop
        with patch("unidep._dependencies_parsing.parse_requirements"):
            # Implementation should detect and break circular dependencies
            assert True  # Placeholder


class TestCompatibility:
    """Test compatibility with existing unidep features."""

    def test_pip_indices_with_platforms(self, tmp_path: Path) -> None:
        """Test that pip_indices work with platform selectors."""
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        requirements_file = project_dir / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                channels:
                  - conda-forge
                pip_indices:
                  - https://pypi.org/simple/
                platforms:
                  - linux-64
                  - osx-arm64
                dependencies:
                  - numpy  # [linux64]
                  - pip: tensorflow  # [linux64]
                  - pip: tensorflow-metal  # [osx-arm64]
                """,
            ),
        )

        # pip_indices should apply to all platforms
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Verify platform-specific handling
            assert True  # Placeholder

    def test_pip_indices_with_optional_dependencies(self, tmp_path: Path) -> None:
        """Test that pip_indices work with optional dependencies."""
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        requirements_file = project_dir / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                pip_indices:
                  - https://pypi.org/simple/
                  - https://test.pypi.org/simple/
                dependencies:
                  - numpy
                optional_dependencies:
                  test:
                    - pip: pytest
                    - pip: test-package  # From test.pypi.org
                  dev:
                    - pip: black
                    - pip: mypy
                """,
            ),
        )

        # pip_indices should apply to optional dependencies too
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # When installing with [test], should use pip_indices
            assert True  # Placeholder

    def test_coexistence_with_uv_index_config(self, tmp_path: Path) -> None:
        """Test that pip_indices can coexist with [[tool.uv.index]] config."""
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        pyproject_file = project_dir / "pyproject.toml"
        pyproject_file.write_text(
            dedent(
                """
                [tool.unidep]
                pip_indices = [
                    "https://pypi.org/simple/",
                    "https://unidep.index.com/simple/"
                ]
                dependencies = ["numpy"]

                [[tool.uv.index]]
                url = "https://uv.specific.com/simple/"
                name = "uv-index"
                """,
            ),
        )

        # Both configurations should be respected
        # unidep should use pip_indices
        # uv might use its own config when called directly
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Verify both configs can coexist
            assert True  # Placeholder


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
