"""Tests for the pixi_to_conda_lock.py script."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

import unidep.pixi_to_conda_lock as ptcl


@pytest.fixture
def sample_pixi_lock() -> dict[str, Any]:
    """Sample pixi.lock data for testing."""
    return {
        "version": 6,
        "environments": {
            "default": {
                "channels": [{"url": "https://conda.anaconda.org/conda-forge/"}],
                "indexes": ["https://pypi.org/simple"],
                "packages": {
                    "osx-arm64": [
                        {
                            "conda": "https://conda.anaconda.org/conda-forge/osx-arm64/python-3.13.2-hfd29fff_1_cp313t.conda",
                        },
                        {
                            "pypi": "https://files.pythonhosted.org/packages/04/27/8739697a1d77f972feee90b844786b893217a133941477570d161de2750f/numthreads-0.5.0-py3-none-any.whl",
                        },
                    ],
                },
            },
        },
        "packages": [
            {
                "conda": "https://conda.anaconda.org/conda-forge/osx-arm64/python-3.13.2-hfd29fff_1_cp313t.conda",
                "sha256": "a64466b8f65b77604c3c87092c65d9e51e7db44b11eaa6c469894f0b88b1af5a",
                "md5": "9d0ae3f3e43c192a992827c0abffe284",
                "depends": {"bzip2": ">=1.0.8,<2.0a0", "libexpat": ">=2.6.4,<3.0a0"},
            },
            {
                "pypi": "https://files.pythonhosted.org/packages/04/27/8739697a1d77f972feee90b844786b893217a133941477570d161de2750f/numthreads-0.5.0-py3-none-any.whl",
                "name": "numthreads",
                "version": "0.5.0",
                "sha256": "e56e83cbccef103901e678aa014d64b203cdb1b3a3be7cdedb2516ef62ec8fa1",
            },
        ],
    }


@pytest.fixture
def sample_repodata() -> dict[str, Any]:
    """Sample repodata for testing."""
    return {
        "repo1": {
            "info": {"subdir": "osx-arm64"},
            "packages": {
                "python-3.13.2-hfd29fff_1_cp313t.conda": {
                    "name": "python",
                    "version": "3.13.2",
                    "build": "hfd29fff_1_cp313t",
                    "build_number": 1,
                    "depends": ["bzip2 >=1.0.8,<2.0a0", "libexpat >=2.6.4,<3.0a0"],
                    "md5": "9d0ae3f3e43c192a992827c0abffe284",
                    "sha256": "a64466b8f65b77604c3c87092c65d9e51e7db44b11eaa6c469894f0b88b1af5a",
                },
            },
        },
    }


def test_read_yaml_file() -> None:
    """Test reading a YAML file."""
    mock_yaml_content = """
    key1: value1
    key2: value2
    """
    with patch("builtins.open", mock_open(read_data=mock_yaml_content)):
        result = ptcl.read_yaml_file(Path("test.yaml"))
        assert result == {"key1": "value1", "key2": "value2"}


def test_write_yaml_file() -> None:
    """Test writing a YAML file."""
    data = {"key1": "value1", "key2": "value2"}
    mock_file = mock_open()
    with patch("builtins.open", mock_file):
        ptcl.write_yaml_file(Path("test.yaml"), data)
        mock_file.assert_called_once_with(Path("test.yaml"), "w")
        mock_file().write.assert_called()


def test_find_repodata_cache_dir() -> None:
    """Test finding the repodata cache directory."""
    with patch("pathlib.Path.exists") as mock_exists, patch(
        "pathlib.Path.is_dir",
    ) as mock_is_dir:
        # Test when directory exists
        mock_exists.return_value = True
        mock_is_dir.return_value = True
        result = ptcl.find_repodata_cache_dir()
        assert result is not None

        # Test when directory doesn't exist
        mock_exists.return_value = False
        result = ptcl.find_repodata_cache_dir()
        assert result is None


def test_load_json_file() -> None:
    """Test loading a JSON file."""
    mock_json_content = '{"key1": "value1", "key2": "value2"}'
    with patch("builtins.open", mock_open(read_data=mock_json_content)):
        result = ptcl.load_json_file(Path("test.json"))
        assert result == {"key1": "value1", "key2": "value2"}


def test_load_repodata_files() -> None:
    """Test loading repodata files."""
    mock_dir = MagicMock()
    mock_file1 = MagicMock()
    mock_file1.name = "file1.json"
    mock_file1.stem = "file1"
    mock_file2 = MagicMock()
    mock_file2.name = "file2.info.json"

    mock_dir.glob.return_value = [mock_file1, mock_file2]

    with patch("unidep.pixi_to_conda_lock.load_json_file") as mock_load:
        mock_load.return_value = {"key": "value"}
        result = ptcl.load_repodata_files(mock_dir)

        assert "file1" in result
        assert "file2" not in result
        assert result["file1"] == {"key": "value"}


def test_extract_filename_from_url() -> None:
    """Test extracting filename from URL."""
    url = "https://conda.anaconda.org/conda-forge/osx-arm64/python-3.13.2-hfd29fff_1_cp313t.conda"
    result = ptcl.extract_filename_from_url(url)
    assert result == "python-3.13.2-hfd29fff_1_cp313t.conda"


def test_find_package_in_repodata(sample_repodata: dict[str, Any]) -> None:
    """Test finding a package in repodata."""
    url = "https://conda.anaconda.org/conda-forge/osx-arm64/python-3.13.2-hfd29fff_1_cp313t.conda"
    result = ptcl.find_package_in_repodata(sample_repodata, url)
    assert result is not None
    assert result["name"] == "python"
    assert result["version"] == "3.13.2"

    # Test package not found
    url_not_found = "https://conda.anaconda.org/conda-forge/osx-arm64/nonexistent-1.0.0-abc123.conda"
    result_not_found = ptcl.find_package_in_repodata(sample_repodata, url_not_found)
    assert result_not_found is None


def test_extract_platform_from_url() -> None:
    """Test extracting platform from URL."""
    # Test different platforms
    assert (
        ptcl.extract_platform_from_url(
            "https://conda.anaconda.org/conda-forge/noarch/pkg-1.0.0.conda",
        )
        == "noarch"
    )
    assert (
        ptcl.extract_platform_from_url(
            "https://conda.anaconda.org/conda-forge/osx-arm64/pkg-1.0.0.conda",
        )
        == "osx-arm64"
    )
    assert (
        ptcl.extract_platform_from_url(
            "https://conda.anaconda.org/conda-forge/osx-64/pkg-1.0.0.conda",
        )
        == "osx-64"
    )
    assert (
        ptcl.extract_platform_from_url(
            "https://conda.anaconda.org/conda-forge/linux-64/pkg-1.0.0.conda",
        )
        == "linux-64"
    )
    assert (
        ptcl.extract_platform_from_url(
            "https://conda.anaconda.org/conda-forge/linux-aarch64/pkg-1.0.0.conda",
        )
        == "linux-aarch64"
    )
    assert (
        ptcl.extract_platform_from_url(
            "https://conda.anaconda.org/conda-forge/win-64/pkg-1.0.0.conda",
        )
        == "win-64"
    )
    assert (
        ptcl.extract_platform_from_url(
            "https://conda.anaconda.org/conda-forge/unknown/pkg-1.0.0.conda",
        )
        == "unknown"
    )


def test_extract_name_version_from_url() -> None:
    """Test extracting name and version from URL."""
    # Test standard package
    url = "https://conda.anaconda.org/conda-forge/osx-arm64/python-3.13.2-hfd29fff_1_cp313t.conda"
    name, version = ptcl.extract_name_version_from_url(url)
    assert name == "python"
    assert version == "3.13.2"

    # Test package with tar.bz2 extension
    url_tar = "https://conda.anaconda.org/conda-forge/osx-arm64/python-3.13.2-hfd29fff_1_cp313t.tar.bz2"
    name_tar, version_tar = ptcl.extract_name_version_from_url(url_tar)
    assert name_tar == "python"
    assert version_tar == "3.13.2"

    # Test package with no version
    url_no_version = "https://conda.anaconda.org/conda-forge/osx-arm64/python.conda"
    name_no_version, version_no_version = ptcl.extract_name_version_from_url(
        url_no_version,
    )
    assert name_no_version == "python"
    assert version_no_version == ""


def test_parse_dependencies_from_repodata() -> None:
    """Test parsing dependencies from repodata."""
    depends_list = ["python >=3.8", "numpy", "pandas >=1.0.0,<2.0.0"]
    result = ptcl.parse_dependencies_from_repodata(depends_list)
    assert result == {"python": ">=3.8", "numpy": "", "pandas": ">=1.0.0,<2.0.0"}


def test_create_conda_package_entry() -> None:
    """Test creating a conda package entry."""
    url = "https://conda.anaconda.org/conda-forge/osx-arm64/python-3.13.2-hfd29fff_1_cp313t.conda"
    repodata_info = {
        "name": "python",
        "version": "3.13.2",
        "build": "hfd29fff_1_cp313t",
        "build_number": 1,
        "depends": ["bzip2 >=1.0.8,<2.0a0", "libexpat >=2.6.4,<3.0a0"],
        "md5": "9d0ae3f3e43c192a992827c0abffe284",
        "sha256": "a64466b8f65b77604c3c87092c65d9e51e7db44b11eaa6c469894f0b88b1af5a",
    }

    result = ptcl.create_conda_package_entry(url, repodata_info)

    assert result["name"] == "python"
    assert result["version"] == "3.13.2"
    assert result["manager"] == "conda"
    assert result["platform"] == "osx-arm64"
    assert result["dependencies"] == {
        "bzip2": ">=1.0.8,<2.0a0",
        "libexpat": ">=2.6.4,<3.0a0",
    }
    assert result["url"] == url
    assert result["hash"]["md5"] == "9d0ae3f3e43c192a992827c0abffe284"
    assert (
        result["hash"]["sha256"]
        == "a64466b8f65b77604c3c87092c65d9e51e7db44b11eaa6c469894f0b88b1af5a"
    )
    assert result["build"] == "hfd29fff_1_cp313t"
    assert result["build_number"] == 1


def test_create_conda_package_entry_fallback() -> None:
    """Test creating a conda package entry using fallback."""
    url = "https://conda.anaconda.org/conda-forge/osx-arm64/python-3.13.2-hfd29fff_1_cp313t.conda"
    package_info = {
        "depends": {"bzip2": ">=1.0.8,<2.0a0", "libexpat": ">=2.6.4,<3.0a0"},
        "md5": "9d0ae3f3e43c192a992827c0abffe284",
        "sha256": "a64466b8f65b77604c3c87092c65d9e51e7db44b11eaa6c469894f0b88b1af5a",
    }

    result = ptcl.create_conda_package_entry_fallback(url, package_info)

    assert result["name"] == "python"
    assert result["version"] == "3.13.2"
    assert result["manager"] == "conda"
    assert result["platform"] == "osx-arm64"
    assert result["dependencies"] == {
        "bzip2": ">=1.0.8,<2.0a0",
        "libexpat": ">=2.6.4,<3.0a0",
    }
    assert result["url"] == url
    assert result["hash"]["md5"] == "9d0ae3f3e43c192a992827c0abffe284"
    assert (
        result["hash"]["sha256"]
        == "a64466b8f65b77604c3c87092c65d9e51e7db44b11eaa6c469894f0b88b1af5a"
    )
