"""Tests for the unidep.utils module."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from unidep._setuptools_integration import (
    identify_current_platform,
)
from unidep.utils import (
    build_pep508_environment_marker,
    escape_unicode,
    extract_matching_platforms,
    extract_name_and_pin,
)


def test_escape_unicode() -> None:
    assert escape_unicode("foo\\n") == "foo\n"
    assert escape_unicode("foo\\t") == "foo\t"


def test_build_pep508_environment_marker() -> None:
    # Test with a single platform
    assert (
        build_pep508_environment_marker(["linux-64"])
        == "sys_platform == 'linux' and platform_machine == 'x86_64'"
    )

    # Test with multiple platforms
    assert (
        build_pep508_environment_marker(["linux-64", "osx-64"])
        == "sys_platform == 'linux' and platform_machine == 'x86_64' or sys_platform == 'darwin' and platform_machine == 'x86_64'"
    )

    # Test with an empty list
    assert not build_pep508_environment_marker([])

    # Test with a platform not in PEP508_MARKERS
    assert not build_pep508_environment_marker(["unknown-platform"])  # type: ignore[list-item]

    # Test with a mix of valid and invalid platforms
    assert (
        build_pep508_environment_marker(["linux-64", "unknown-platform"])  # type: ignore[list-item]
        == "sys_platform == 'linux' and platform_machine == 'x86_64'"
    )


def test_detect_platform() -> None:
    with patch("platform.system", return_value="Linux"), patch(
        "platform.machine",
        return_value="x86_64",
    ):
        assert identify_current_platform() == "linux-64"

    with patch("platform.system", return_value="Linux"), patch(
        "platform.machine",
        return_value="aarch64",
    ):
        assert identify_current_platform() == "linux-aarch64"

    with patch("platform.system", return_value="Darwin"), patch(
        "platform.machine",
        return_value="x86_64",
    ):
        assert identify_current_platform() == "osx-64"

    with patch("platform.system", return_value="Darwin"), patch(
        "platform.machine",
        return_value="arm64",
    ):
        assert identify_current_platform() == "osx-arm64"

    with patch("platform.system", return_value="Windows"), patch(
        "platform.machine",
        return_value="AMD64",
    ):
        assert identify_current_platform() == "win-64"

    with patch("platform.system", return_value="Linux"), patch(
        "platform.machine",
        return_value="unknown",
    ), pytest.raises(ValueError, match="Unsupported Linux architecture"):
        identify_current_platform()

    with patch("platform.system", return_value="Darwin"), patch(
        "platform.machine",
        return_value="unknown",
    ), pytest.raises(ValueError, match="Unsupported macOS architecture"):
        identify_current_platform()

    with patch("platform.system", return_value="Windows"), patch(
        "platform.machine",
        return_value="unknown",
    ), pytest.raises(ValueError, match="Unsupported Windows architecture"):
        identify_current_platform()

    with patch("platform.system", return_value="Linux"), patch(
        "platform.machine",
        return_value="ppc64le",
    ):
        assert identify_current_platform() == "linux-ppc64le"

    with patch("platform.system", return_value="Unknown"), patch(
        "platform.machine",
        return_value="x86_64",
    ), pytest.raises(ValueError, match="Unsupported operating system"):
        identify_current_platform()


def test_extract_name_and_pin() -> None:
    # Test with version pin
    assert extract_name_and_pin("numpy >=1.20.0") == ("numpy", ">=1.20.0")
    assert extract_name_and_pin("pandas<2.0,>=1.1.3") == ("pandas", "<2.0,>=1.1.3")

    # Test with multiple version conditions
    assert extract_name_and_pin("scipy>=1.2.3, <1.3") == ("scipy", ">=1.2.3, <1.3")

    # Test with no version pin
    assert extract_name_and_pin("matplotlib") == ("matplotlib", None)

    # Test with whitespace variations
    assert extract_name_and_pin("requests >= 2.25") == ("requests", ">= 2.25")

    # Test when installing from a URL
    url = "https://github.com/python-adaptive/adaptive.git@main"
    pin = f"@ git+{url}"
    assert extract_name_and_pin(f"adaptive {pin}") == ("adaptive", pin)

    # Test with invalid input
    with pytest.raises(ValueError, match="Invalid package string"):
        extract_name_and_pin(">=1.20.0 numpy")


def test_extract_matching_platforms() -> None:
    # Test with a line having a linux selector
    content_linux = "dependency1  # [linux]"
    assert set(extract_matching_platforms(content_linux)) == {
        "linux-64",
        "linux-aarch64",
        "linux-ppc64le",
    }

    # Test with a line having a win selector
    content_win = "dependency2  # [win]"
    assert set(extract_matching_platforms(content_win)) == {"win-64"}

    # Test with a line having an osx64 selector
    content_osx64 = "dependency3  # [osx64]"
    assert set(extract_matching_platforms(content_osx64)) == {"osx-64"}

    # Test with a line having no selector
    content_none = "dependency4"
    assert extract_matching_platforms(content_none) == []

    # Test with a comment line
    content_comment = "# This is a comment"
    assert extract_matching_platforms(content_comment) == []

    # Test with a line having a unix selector
    content_unix = "dependency5  # [unix]"
    expected_unix = {
        "linux-64",
        "linux-aarch64",
        "linux-ppc64le",
        "osx-64",
        "osx-arm64",
    }
    assert set(extract_matching_platforms(content_unix)) == expected_unix

    # Test with a line having multiple selectors
    content_multi = "dependency7  # [linux64 unix]"
    expected_multi = {
        "linux-64",
        "linux-aarch64",
        "linux-ppc64le",
        "osx-64",
        "osx-arm64",
    }
    assert set(extract_matching_platforms(content_multi)) == expected_multi

    # Test with a line having multiple []
    content_multi = "dependency7  # [linux64] [win]"
    with pytest.raises(ValueError, match="Multiple bracketed selectors"):
        extract_matching_platforms(content_multi)

    incorrect_platform = "dependency8  # [unknown-platform]"
    with pytest.raises(ValueError, match="Unsupported platform"):
        extract_matching_platforms(incorrect_platform)
