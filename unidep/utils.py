"""Utility functions for `unidep`."""
from __future__ import annotations

import codecs
import platform
import re
import sys
import warnings
from pathlib import Path
from typing import cast

from unidep._version import __version__
from unidep.platform_definitions import (
    PEP508_MARKERS,
    PLATFORM_SELECTOR_MAP_REVERSE,
    Platform,
    Selector,
)


def add_comment_to_file(
    filename: str | Path,
    extra_lines: list[str] | None = None,
) -> None:
    """Add a comment to the top of a file."""
    if extra_lines is None:
        extra_lines = []
    with open(filename, "r+") as f:  # noqa: PTH123
        content = f.read()
        f.seek(0, 0)
        command_line_args = " ".join(sys.argv[1:])
        txt = [
            f"# This file is created and managed by `unidep` {__version__}.",
            "# For details see https://github.com/basnijholt/unidep",
            f"# File generated with: `unidep {command_line_args}`",
            *extra_lines,
        ]
        content = "\n".join(txt) + "\n\n" + content
        f.write(content)


def remove_top_comments(filename: str | Path) -> None:
    """Removes the top comments (lines starting with '#') from a file."""
    with open(filename) as file:  # noqa: PTH123
        lines = file.readlines()

    first_non_comment = next(
        (i for i, line in enumerate(lines) if not line.strip().startswith("#")),
        len(lines),
    )
    content_without_comments = lines[first_non_comment:]
    with open(filename, "w") as file:  # noqa: PTH123
        file.writelines(content_without_comments)


def escape_unicode(string: str) -> str:
    """Escape unicode characters."""
    return codecs.decode(string, "unicode_escape")


def is_pip_installable(folder: str | Path) -> bool:  # pragma: no cover
    """Determine if the project is pip installable.

    Checks for existence of setup.py or [build-system] in pyproject.toml.
    """
    path = Path(folder)
    if (path / "setup.py").exists():
        return True

    # When toml makes it into the standard library, we can use that instead
    # For now this is good enough, except it doesn't handle the case where
    # [build-system] is inside of a multi-line literal string.
    pyproject_path = path / "pyproject.toml"
    if pyproject_path.exists():
        with pyproject_path.open("r") as file:
            for line in file:
                if line.strip().startswith("[build-system]"):
                    return True
    return False


def identify_current_platform() -> Platform:
    """Detect the current platform."""
    system = platform.system().lower()
    architecture = platform.machine().lower()

    if system == "linux":
        if architecture == "x86_64":
            return "linux-64"
        if architecture == "aarch64":
            return "linux-aarch64"
        if architecture == "ppc64le":
            return "linux-ppc64le"
        msg = "Unsupported Linux architecture"
        raise ValueError(msg)
    if system == "darwin":
        if architecture == "x86_64":
            return "osx-64"
        if architecture == "arm64":
            return "osx-arm64"
        msg = "Unsupported macOS architecture"
        raise ValueError(msg)
    if system == "windows":
        if "64" in architecture:
            return "win-64"
        msg = "Unsupported Windows architecture"
        raise ValueError(msg)
    msg = "Unsupported operating system"
    raise ValueError(msg)


def build_pep508_environment_marker(
    platforms: list[Platform | tuple[Platform, ...]],
) -> str:
    """Generate a PEP 508 selector for a list of platforms."""
    sorted_platforms = tuple(sorted(platforms))
    if sorted_platforms in PEP508_MARKERS:
        return PEP508_MARKERS[sorted_platforms]  # type: ignore[index]
    environment_markers = [
        PEP508_MARKERS[platform]
        for platform in sorted(sorted_platforms)
        if platform in PEP508_MARKERS
    ]
    return " or ".join(environment_markers)


def extract_name_and_pin(package_str: str) -> tuple[str, str | None]:
    """Splits a string into package name and version pinning."""
    # Regular expression to match package name and version pinning
    match = re.match(r"([a-zA-Z0-9_-]+)\s*(.*)", package_str)
    if match:
        package_name = match.group(1).strip()
        version_pin = match.group(2).strip()

        # Return None if version pinning is missing or empty
        if not version_pin:
            return package_name, None
        return package_name, version_pin

    msg = f"Invalid package string: '{package_str}'"
    raise ValueError(msg)


def _simple_warning_format(
    message: Warning | str,
    category: type[Warning],  # noqa: ARG001
    filename: str,
    lineno: int,
    line: str | None = None,  # noqa: ARG001
) -> str:  # pragma: no cover
    """Format warnings without code context."""
    return (
        f"---------------------\n"
        f"⚠️  *** WARNING *** ⚠️\n"
        f"{message}\n"
        f"Location: {filename}:{lineno}\n"
        f"---------------------\n"
    )


def warn(
    message: str | Warning,
    category: type[Warning] = UserWarning,
    stacklevel: int = 1,
) -> None:
    """Emit a warning with a custom format specific to this package."""
    original_format = warnings.formatwarning
    warnings.formatwarning = _simple_warning_format
    try:
        warnings.warn(message, category, stacklevel=stacklevel + 1)
    finally:
        warnings.formatwarning = original_format


def extract_matching_platforms(comment: str) -> list[Platform]:
    """Filter out lines from a requirements file that don't match the platform."""
    # we support a very limited set of selectors that adhere to platform only
    # refs:
    # https://docs.conda.io/projects/conda-build/en/latest/resources/define-metadata.html#preprocessing-selectors
    # https://github.com/conda/conda-lock/blob/3d2bf356e2cf3f7284407423f7032189677ba9be/conda_lock/src_parser/selectors.py

    sel_pat = re.compile(r"#\s*\[([^\[\]]+)\]")
    multiple_brackets_pat = re.compile(r"#.*\].*\[")  # Detects multiple brackets

    filtered_platforms = set()

    for line in comment.splitlines(keepends=False):
        if multiple_brackets_pat.search(line):
            msg = f"Multiple bracketed selectors found in line: '{line}'"
            raise ValueError(msg)

        m = sel_pat.search(line)
        if m:
            conds = m.group(1).split()
            for cond in conds:
                if cond not in PLATFORM_SELECTOR_MAP_REVERSE:
                    valid = list(PLATFORM_SELECTOR_MAP_REVERSE.keys())
                    msg = f"Unsupported platform specifier: '{comment}' use one of {valid}"  # noqa: E501
                    raise ValueError(msg)
                cond = cast(Selector, cond)
                for _platform in PLATFORM_SELECTOR_MAP_REVERSE[cond]:
                    filtered_platforms.add(_platform)

    return sorted(filtered_platforms)
