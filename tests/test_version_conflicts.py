"""Tests for the version conflict resolution logic."""
from __future__ import annotations

import pytest

from unidep._conflicts import (
    VersionConflictError,
    _combine_pinning_within_platform,
    _is_redundant,
    _is_valid_pinning,
    _parse_pinning,
    combine_version_pinnings,
)
from unidep.platform_definitions import Meta


def test_combining_versions() -> None:
    data = {
        None: {
            "conda": [
                Meta(name="numpy", which="conda", pin=">1"),
                Meta(name="numpy", which="conda", pin="<2"),
            ],
        },
    }
    resolved = _combine_pinning_within_platform(data)  # type: ignore[arg-type]
    assert resolved == {
        None: {
            "conda": Meta(name="numpy", which="conda", pin=">1,<2"),
        },
    }


@pytest.mark.parametrize("operator", ["<", "<=", ">", ">=", "="])
@pytest.mark.parametrize("version", ["1", "1.0", "1.0.0", "1.0.0rc1"])
def test_is_valid_pinning(operator: str, version: str) -> None:
    assert _is_valid_pinning(f"{operator}{version}")


@pytest.mark.parametrize(
    ("pinnings", "expected"),
    [
        ([" > 0.0.1", " < 2", " = 1.0.0"], "=1.0.0"),
        (["<2", ">1"], "<2,>1"),
        ([">1", "<2"], ">1,<2"),
        (["<3", "<=3", "<4"], "<3"),
        (["=1", "=1"], "=1"),
        (["=2", "<3", "<=3", "<4"], "=2"),
        (["=2", ">1", "<3"], "=2"),
        (["=3", ">=2", "<=4"], "=3"),
        (["=3", ">1", "<4"], "=3"),
        (["=3", ">2", "<4"], "=3"),
        ([">=1", "<=1"], ">=1,<=1"),
        ([">=1", ">=1", "=1"], "=1"),
        ([">=1", ">0", "<=3", "<4"], ">=1,<=3"),
        ([">=2", "<=2"], ">=2,<=2"),
        ([">=2", "<3"], ">=2,<3"),
        ([">0.0.1", "<2", "=1.0.0"], "=1.0.0"),
        ([">1", "<=3", "<4"], ">1,<=3"),
        ([">1", "<=3"], ">1,<=3"),
        ([">1", ">=1", "<3", "<=3"], ">1,<3"),
        ([">1"], ">1"),
        (["3"], ""),
        ([], ""),
        ([">"], ""),
    ],
)
def test_combine_version_pinnings(pinnings: list[str], expected: str) -> None:
    assert combine_version_pinnings(pinnings) == expected
    # Try reversing the order of the pinnings
    if "," not in expected:
        assert combine_version_pinnings(pinnings[::-1]) == expected
    else:
        parts = expected.split(",")
        assert combine_version_pinnings(pinnings[::-1]) == ",".join(parts[::-1])


def test_invalid_pinnings() -> None:
    assert combine_version_pinnings(["abc", "def"]) == ""
    assert combine_version_pinnings(["==abc"]) == ""
    assert combine_version_pinnings(["<=>abc"]) == ""


def test_mixed_valid_and_invalid_pinnings() -> None:
    assert combine_version_pinnings([">1", "abc", "<=3", ""]) == ">1,<=3"
    assert combine_version_pinnings(["abc", ">=1", "<=2"]) == ">=1,<=2"


@pytest.mark.parametrize(
    "pinnings",
    [[">2", "<1"], ["<1", ">2"], [">1", "<1"], ["<=1", ">1"], [">1", "<=1"]],
)
def test_contradictory_pinnings(pinnings: list[str]) -> None:
    p1, p2 = pinnings
    with pytest.raises(
        VersionConflictError,
        match=f"Contradictory version pinnings found for `None`: {p1} and {p2}",
    ):
        combine_version_pinnings(pinnings)


def test_exact_pinning_with_contradictory_ranges() -> None:
    with pytest.raises(
        VersionConflictError,
        match="Contradictory version pinnings found for `None`: =3 and <2",
    ):
        combine_version_pinnings(["=3", "<2", ">4"])

    with pytest.raises(
        VersionConflictError,
        match="Contradictory version pinnings found for `None`: =3 and <1",
    ):
        assert combine_version_pinnings(["=3", "<1", ">4"])


def test_multiple_exact_pinnings() -> None:
    with pytest.raises(
        VersionConflictError,
        match="Multiple exact version pinnings found: =2, =3",
    ):
        combine_version_pinnings(["=2", "=3"])


def test_general_contradictory_pinnings() -> None:
    # This test ensures that contradictory non-exact pinnings raise a VersionConflictError
    with pytest.raises(
        VersionConflictError,
        match="Contradictory version pinnings found for `None`: >=2 and <1",
    ):
        combine_version_pinnings([">=2", "<1"])


def test_is_redundant() -> None:
    assert _is_redundant(">2", [">5"])
    assert not _is_redundant(">5", [">2"])
    assert _is_redundant("<5", ["<2"])
    assert _is_redundant(">=2", [">2"])
    assert not _is_redundant(">2", [">=2"])


@pytest.mark.parametrize("pinning", ["<<1", ">>1", "=<1", "=>1"])
def test_invalid_parse_pinning(pinning: str) -> None:
    with pytest.raises(
        VersionConflictError,
        match=f"Invalid version pinning: '{pinning}'",
    ):
        _parse_pinning(pinning)
