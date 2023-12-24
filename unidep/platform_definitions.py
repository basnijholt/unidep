"""unidep - Unified Conda and Pip requirements management.

Types and definitions for platforms, selectors, and markers.
"""
from __future__ import annotations

import sys
from typing import NamedTuple

if sys.version_info >= (3, 8):
    from typing import Literal, get_args
else:  # pragma: no cover
    from typing_extensions import Literal, get_args

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

VALID_SELECTORS = get_args(Selector)

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


def validate_selector(selector: Selector) -> None:
    """Check if a selector is valid."""
    valid_selectors = VALID_SELECTORS
    if selector not in VALID_SELECTORS:
        msg = f"Invalid selector: `{selector}`, use one of `{valid_selectors}`"
        raise ValueError(msg)


class Spec(NamedTuple):
    """A dependency specification."""

    name: str
    which: CondaPip
    comment: str | None = None
    pin: str | None = None
    identifier: str | None = None
    selector: Selector | None = None  # can be specified instead of via comment

    def platforms(self) -> list[Platform] | None:
        """Return the platforms for this dependency."""
        from unidep.utils import extract_matching_platforms

        assert not (self.comment and self.selector), "Cannot specify both"

        if self.comment is None and self.selector is None:
            return None
        if self.comment is not None:
            return extract_matching_platforms(self.comment) or None
        assert self.selector is not None
        validate_selector(self.selector)
        return list(PLATFORM_SELECTOR_MAP_REVERSE[self.selector])

    def pprint(self) -> str:
        """Pretty print the dependency."""
        result = f"{self.name}"
        if self.pin is not None:
            result += f" {self.pin}"
        if self.comment is not None:
            result += f" {self.comment}"
        return result

    def name_with_pin(self, *, is_pip: bool = False) -> str:
        """Return the name with the pin."""
        result = f"{self.name}"
        if self.pin is not None:
            pin = self.pin
            if (
                is_pip
                and "=" in pin
                and not (">=" in pin or "<=" in pin or "==" in pin)
            ):
                # Replace `=` with `==` for pip
                pin = pin.replace("=", "==")
            result += f" {pin}"
        return result
