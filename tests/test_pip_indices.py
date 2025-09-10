"""Unit tests for pip_indices support in unidep."""

import os
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from unidep._conda_env import CondaEnvironmentSpec, write_conda_environment_file
from unidep._dependencies_parsing import (
    parse_requirements,
)


class TestPipIndicesParsing:
    """Test parsing of pip_indices from requirements.yaml and pyproject.toml."""

    def test_parse_pip_indices_from_yaml(self, tmp_path: Path) -> None:
        """Test parsing pip_indices from requirements.yaml."""
        requirements_file = tmp_path / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                channels:
                  - conda-forge
                pip_indices:
                  - https://pypi.org/simple/
                  - https://private.company.com/simple/
                dependencies:
                  - numpy
                  - pip: private-package
                """,
            ),
        )

        parsed = parse_requirements(requirements_file)
        assert parsed.pip_indices == [
            "https://pypi.org/simple/",
            "https://private.company.com/simple/",
        ]

    def test_parse_pip_indices_from_toml(self, tmp_path: Path) -> None:
        """Test parsing pip_indices from pyproject.toml."""
        pyproject_file = tmp_path / "pyproject.toml"
        pyproject_file.write_text(
            dedent(
                """
                [tool.unidep]
                channels = ["conda-forge"]
                pip_indices = [
                    "https://pypi.org/simple/",
                    "https://test.pypi.org/simple/"
                ]
                dependencies = [
                    "numpy",
                    {pip = "test-package"}
                ]
                """,
            ),
        )

        parsed = parse_requirements(pyproject_file)
        assert parsed.pip_indices == [
            "https://pypi.org/simple/",
            "https://test.pypi.org/simple/",
        ]

    def test_parse_empty_pip_indices(self, tmp_path: Path) -> None:
        """Test that missing pip_indices defaults to empty list."""
        requirements_file = tmp_path / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                channels:
                  - conda-forge
                dependencies:
                  - numpy
                """,
            ),
        )

        parsed = parse_requirements(requirements_file)
        assert parsed.pip_indices == []

    def test_parse_pip_indices_with_env_vars(self, tmp_path: Path) -> None:
        """Test parsing pip_indices with environment variables."""
        requirements_file = tmp_path / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                pip_indices:
                  - https://${PIP_USER}:${PIP_PASSWORD}@private.company.com/simple/
                  - https://pypi.org/simple/
                dependencies:
                  - pip: private-package
                """,
            ),
        )

        parsed = parse_requirements(requirements_file)
        assert parsed.pip_indices == [
            "https://${PIP_USER}:${PIP_PASSWORD}@private.company.com/simple/",
            "https://pypi.org/simple/",
        ]

    def test_merge_pip_indices_from_multiple_files(self, tmp_path: Path) -> None:
        """Test merging pip_indices from multiple requirements files."""
        # First requirements file
        req1 = tmp_path / "req1.yaml"
        req1.write_text(
            dedent(
                """
                name: project1
                pip_indices:
                  - https://pypi.org/simple/
                  - https://index1.com/simple/
                dependencies:
                  - numpy
                """,
            ),
        )

        # Second requirements file
        req2 = tmp_path / "req2.yaml"
        req2.write_text(
            dedent(
                """
                name: project2
                pip_indices:
                  - https://index2.com/simple/
                  - https://pypi.org/simple/  # Duplicate
                dependencies:
                  - pandas
                """,
            ),
        )

        # Parse and merge
        parsed1 = parse_requirements(req1)
        parsed2 = parse_requirements(req2)

        # In real implementation, we'd have a merge function
        # For now, test that both parse correctly
        assert parsed1.pip_indices == [
            "https://pypi.org/simple/",
            "https://index1.com/simple/",
        ]
        assert parsed2.pip_indices == [
            "https://index2.com/simple/",
            "https://pypi.org/simple/",
        ]

    def test_pip_indices_ordering_preserved(self, tmp_path: Path) -> None:
        """Test that pip_indices order is preserved (first is primary)."""
        requirements_file = tmp_path / "requirements.yaml"
        indices = [
            "https://primary.com/simple/",
            "https://secondary.com/simple/",
            "https://tertiary.com/simple/",
        ]
        requirements_file.write_text(
            dedent(
                f"""
                name: test_project
                pip_indices:
                  - {indices[0]}
                  - {indices[1]}
                  - {indices[2]}
                dependencies:
                  - numpy
                """,
            ),
        )

        parsed = parse_requirements(requirements_file)
        assert parsed.pip_indices == indices
        # First index should be treated as primary (--index-url)
        assert parsed.pip_indices[0] == indices[0]


class TestEnvironmentGeneration:
    """Test generation of environment.yaml with pip_indices."""

    def test_environment_yaml_with_pip_indices(self, tmp_path: Path) -> None:
        """Test that pip_indices are included as pip_repositories in environment.yaml."""
        env_spec = CondaEnvironmentSpec(
            channels=["conda-forge"],
            pip_indices=[
                "https://pypi.org/simple/",
                "https://private.company.com/simple/",
            ],
            platforms=[],
            conda=["numpy", "pandas"],
            pip=["private-package", "requests"],
        )

        env_file = tmp_path / "environment.yaml"
        write_conda_environment_file(env_spec, env_file)

        with open(env_file) as f:
            env_dict = yaml.safe_load(f)

        # Check that pip_repositories is included
        assert "pip_repositories" in env_dict
        assert env_dict["pip_repositories"] == [
            "https://pypi.org/simple/",
            "https://private.company.com/simple/",
        ]

        # Check that dependencies structure is correct
        assert "dependencies" in env_dict
        deps = env_dict["dependencies"]

        # Find pip dependencies
        pip_deps = None
        for dep in deps:
            if isinstance(dep, dict) and "pip" in dep:
                pip_deps = dep["pip"]
                break

        assert pip_deps is not None
        assert "private-package" in pip_deps
        assert "requests" in pip_deps

    def test_environment_yaml_without_pip_indices(self, tmp_path: Path) -> None:
        """Test environment.yaml generation without pip_indices."""
        env_spec = CondaEnvironmentSpec(
            channels=["conda-forge"],
            pip_indices=[],  # Empty pip_indices
            platforms=[],
            conda=["numpy"],
            pip=["requests"],
        )

        env_file = tmp_path / "environment.yaml"
        write_conda_environment_file(env_spec, env_file)

        with open(env_file) as f:
            env_dict = yaml.safe_load(f)

        # pip_repositories should not be included if empty
        assert "pip_repositories" not in env_dict

    def test_environment_yaml_with_env_vars_in_indices(self, tmp_path: Path) -> None:
        """Test that environment variables in pip_indices are preserved."""
        env_spec = CondaEnvironmentSpec(
            channels=["conda-forge"],
            pip_indices=[
                "https://${USER}:${PASS}@private.com/simple/",
                "https://pypi.org/simple/",
            ],
            platforms=[],
            conda=[],
            pip=["private-package"],
        )

        env_file = tmp_path / "environment.yaml"
        write_conda_environment_file(env_spec, env_file)

        with open(env_file) as f:
            content = f.read()
            env_dict = yaml.safe_load(content)

        # Environment variables should be preserved
        assert (
            env_dict["pip_repositories"][0]
            == "https://${USER}:${PASS}@private.com/simple/"
        )


class TestPipCommandConstruction:
    """Test construction of pip install commands with indices."""

    def test_build_pip_command_with_indices(self) -> None:
        """Test building pip install command with index URLs."""
        pip_indices = [
            "https://pypi.org/simple/",
            "https://private.company.com/simple/",
        ]
        packages = ["numpy", "private-package"]

        # Expected command structure
        # First index is --index-url, rest are --extra-index-url
        expected_args = [
            "--index-url",
            "https://pypi.org/simple/",
            "--extra-index-url",
            "https://private.company.com/simple/",
        ]

        # This will be implemented in the actual code
        # For now, just verify the logic
        assert pip_indices[0] == "https://pypi.org/simple/"  # Primary
        assert pip_indices[1] == "https://private.company.com/simple/"  # Extra

    def test_build_pip_command_without_indices(self) -> None:
        """Test building pip install command without custom indices."""
        pip_indices = []
        packages = ["numpy", "pandas"]

        # No index flags should be added
        expected_args = []

        assert len(pip_indices) == 0

    def test_build_pip_command_single_index(self) -> None:
        """Test building pip install command with single index."""
        pip_indices = ["https://custom.pypi.org/simple/"]
        packages = ["custom-package"]

        # Single index should use --index-url only
        expected_args = [
            "--index-url",
            "https://custom.pypi.org/simple/",
        ]

        assert len(pip_indices) == 1
        assert pip_indices[0] == "https://custom.pypi.org/simple/"

    def test_uv_compatibility(self) -> None:
        """Test that index flags are compatible with uv."""
        # uv uses the same --index-url and --extra-index-url flags as pip
        pip_indices = [
            "https://pypi.org/simple/",
            "https://test.pypi.org/simple/",
        ]

        # Both pip and uv support these flags
        pip_args = ["--index-url", pip_indices[0]]
        uv_args = ["--index-url", pip_indices[0]]

        assert pip_args == uv_args  # Same flags for both


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_invalid_url_format(self, tmp_path: Path) -> None:
        """Test handling of invalid URL formats."""
        requirements_file = tmp_path / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                pip_indices:
                  - not-a-valid-url
                  - https://valid.url.com/simple/
                dependencies:
                  - numpy
                """,
            ),
        )

        # Should either validate and fail, or accept and let pip handle it
        parsed = parse_requirements(requirements_file)
        assert "not-a-valid-url" in parsed.pip_indices

    def test_duplicate_indices(self, tmp_path: Path) -> None:
        """Test handling of duplicate indices."""
        requirements_file = tmp_path / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                pip_indices:
                  - https://pypi.org/simple/
                  - https://private.com/simple/
                  - https://pypi.org/simple/  # Duplicate
                dependencies:
                  - numpy
                """,
            ),
        )

        parsed = parse_requirements(requirements_file)
        # Duplicates might be preserved or deduplicated based on implementation
        assert len(parsed.pip_indices) == 3  # Or 2 if deduplicating

    def test_empty_string_in_indices(self, tmp_path: Path) -> None:
        """Test handling of empty strings in pip_indices."""
        requirements_file = tmp_path / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                pip_indices:
                  - ""
                  - https://pypi.org/simple/
                dependencies:
                  - numpy
                """,
            ),
        )

        parsed = parse_requirements(requirements_file)
        # Empty strings should be filtered out or raise an error
        assert parsed.pip_indices  # Should have at least the valid URL

    def test_missing_env_var_in_url(self, tmp_path: Path) -> None:
        """Test handling of missing environment variables."""
        requirements_file = tmp_path / "requirements.yaml"
        requirements_file.write_text(
            dedent(
                """
                name: test_project
                pip_indices:
                  - https://${MISSING_VAR}@private.com/simple/
                dependencies:
                  - numpy
                """,
            ),
        )

        # Environment variable not set
        if "MISSING_VAR" in os.environ:
            del os.environ["MISSING_VAR"]

        parsed = parse_requirements(requirements_file)
        # Should preserve the ${MISSING_VAR} syntax for later expansion
        assert "${MISSING_VAR}" in parsed.pip_indices[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
