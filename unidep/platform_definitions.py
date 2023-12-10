"""Definitions for platforms, selectors, and markers."""
from __future__ import annotations

import sys
from typing import NamedTuple

if sys.version_info >= (3, 8):
    from typing import Literal
else:  # pragma: no cover
    from typing_extensions import Literal

CondaPlatform = Literal["unix", "linux", "osx", "win"]
Platform = Literal[
    "linux-64",
    "linux-aarch64",
    "linux-ppc64le",
    "osx-64",
    "osx-arm64",
    "win-64",
]
Selector = Literal[
    "linux64",
    "aarch64",
    "ppc64le",
    "osx64",
    "arm64",
    "win64",
    "win",
    "unix",
    "linux",
    "osx",
    "macos",
]
CondaPip = Literal["conda", "pip"]

PEP508_MARKERS = {
    "linux-64": "sys_platform == 'linux' and platform_machine == 'x86_64'",
    "linux-aarch64": "sys_platform == 'linux' and platform_machine == 'aarch64'",
    "linux-ppc64le": "sys_platform == 'linux' and platform_machine == 'ppc64le'",
    "osx-64": "sys_platform == 'darwin' and platform_machine == 'x86_64'",
    "osx-arm64": "sys_platform == 'darwin' and platform_machine == 'arm64'",
    "win-64": "sys_platform == 'win32' and platform_machine == 'AMD64'",
    ("linux-64", "linux-aarch64", "linux-ppc64le"): "sys_platform == 'linux'",
    ("osx-64", "osx-arm64"): "sys_platform == 'darwin'",
    (
        "linux-64",
        "linux-aarch64",
        "linux-ppc64le",
        "osx-64",
        "osx-arm64",
    ): "sys_platform == 'linux' or sys_platform == 'darwin'",
}


# The first element of each tuple is the only unique selector
PLATFORM_SELECTOR_MAP: dict[Platform, list[Selector]] = {
    "linux-64": ["linux64", "unix", "linux"],
    "linux-aarch64": ["aarch64", "unix", "linux"],
    "linux-ppc64le": ["ppc64le", "unix", "linux"],
    # "osx64" is a selector unique to conda-build referring to
    # platforms on macOS and the Python architecture is x86-64
    "osx-64": ["osx64", "osx", "macos", "unix"],
    "osx-arm64": ["arm64", "osx", "macos", "unix"],
    "win-64": ["win64", "win"],
}

PLATFORM_SELECTOR_MAP_REVERSE: dict[Selector, set[Platform]] = {}
for _platform, _selectors in PLATFORM_SELECTOR_MAP.items():
    for _selector in _selectors:
        PLATFORM_SELECTOR_MAP_REVERSE.setdefault(_selector, set()).add(_platform)


class Meta(NamedTuple):
    """Metadata for a dependency."""

    name: str
    which: CondaPip
    comment: str | None = None
    pin: str | None = None
    identifier: str | None = None

    def platforms(self) -> list[Platform] | None:
        """Return the platforms for this dependency."""
        from unidep.utils import extract_matching_platforms

        if self.comment is None:
            return None
        return extract_matching_platforms(self.comment) or None

    def pprint(self) -> str:
        """Pretty print the dependency."""
        result = f"{self.name}"
        if self.pin is not None:
            result += f" {self.pin}"
        if self.comment is not None:
            result += f" {self.comment}"
        return result

    def name_with_pin(self) -> str:
        """Return the name with the pin."""
        result = f"{self.name}"
        if self.pin is not None:
            result += f" {self.pin}"
        return result
