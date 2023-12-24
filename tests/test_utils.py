"""Tests for the unidep.utils module."""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from unidep._setuptools_integration import (
    identify_current_platform,
)
from unidep.platform_definitions import Selector
from unidep.utils import (
    build_pep508_environment_marker,
    escape_unicode,
    extract_matching_platforms,
    parse_package_str,
)

if sys.version_info >= (3, 8):
    from typing import get_args
else:  # pragma: no cover
    from typing_extensions import get_args


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


def test_parse_package_str() -> None:
    # Test with version pin
    assert parse_package_str("numpy >=1.20.0") == ("numpy", ">=1.20.0", None)
    assert parse_package_str("pandas<2.0,>=1.1.3") == ("pandas", "<2.0,>=1.1.3", None)

    # Test with multiple version conditions
    assert parse_package_str("scipy>=1.2.3, <1.3") == ("scipy", ">=1.2.3, <1.3", None)

    # Test with no version pin
    assert parse_package_str("matplotlib") == ("matplotlib", None, None)

    # Test with whitespace variations
    assert parse_package_str("requests >= 2.25") == ("requests", ">= 2.25", None)

    # Test when installing from a URL
    url = "https://github.com/python-adaptive/adaptive.git@main"
    pin = f"@ git+{url}"
    assert parse_package_str(f"adaptive {pin}") == ("adaptive", pin, None)

    # Test with invalid input
    with pytest.raises(ValueError, match="Invalid package string"):
        parse_package_str(">=1.20.0 numpy")


def test_parse_package_str_with_selector() -> None:
    # Test with version pin
    assert parse_package_str("numpy >=1.20.0:linux64") == (
        "numpy",
        ">=1.20.0",
        "linux64",
    )
    assert parse_package_str("pandas<2.0,>=1.1.3:osx") == (
        "pandas",
        "<2.0,>=1.1.3",
        "osx",
    )

    # Test with multiple version conditions
    assert parse_package_str("scipy>=1.2.3, <1.3:win") == (
        "scipy",
        ">=1.2.3, <1.3",
        "win",
    )

    # Test with no version pin
    assert parse_package_str("matplotlib:win") == ("matplotlib", None, "win")

    # Test with whitespace variations
    assert parse_package_str("requests >= 2.25:win") == ("requests", ">= 2.25", "win")

    # Test when installing from a URL
    url = "https://github.com/python-adaptive/adaptive.git@main"
    pin = f"@ git+{url}"
    assert parse_package_str(f"adaptive {pin}:win") == ("adaptive", pin, "win")

    for sel in get_args(Selector):
        assert parse_package_str(f"numpy:{sel}") == ("numpy", None, sel)


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
