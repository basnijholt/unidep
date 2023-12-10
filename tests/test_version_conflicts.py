"""Tests for the version conflict resolution logic."""
import pytest

from unidep._conflicts import (
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


def test_single_pinning() -> None:
    assert combine_version_pinnings([">1"]) == ">1"


def test_multiple_non_redundant_pinnings() -> None:
    assert combine_version_pinnings([">1", "<=3"]) == ">1,<=3"
    assert combine_version_pinnings([">1", "<2"]) == ">1,<2"
    assert combine_version_pinnings(["<2", ">1"]) == "<2,>1"


def test_redundant_pinning() -> None:
    assert combine_version_pinnings([">1", "<=3", "<4"]) == ">1,<=3"
    assert combine_version_pinnings([">=1", ">0", "<=3", "<4"]) == ">=1,<=3"
    assert combine_version_pinnings(["=3", ">2", "<4"]) == "=3"
    assert combine_version_pinnings(["<3", "<=3", "<4"]) == "<3"
    assert combine_version_pinnings([">1", ">=1", "<3", "<=3"]) == ">1,<3"


def test_empty_list() -> None:
    assert combine_version_pinnings([]) == ""


def test_invalid_pinnings() -> None:
    assert combine_version_pinnings(["abc", "def"]) == ""
    assert combine_version_pinnings(["==abc"]) == ""
    assert combine_version_pinnings(["<=>abc"]) == ""


def test_mixed_valid_and_invalid_pinnings() -> None:
    assert combine_version_pinnings([">1", "abc", "<=3", ""]) == ">1,<=3"
    assert combine_version_pinnings(["abc", ">=1", "<=2"]) == ">=1,<=2"


def test_overlapping_pinnings() -> None:
    assert combine_version_pinnings([">=2", "<=2"]) == ">=2,<=2"


def test_contradictory_pinnings() -> None:
    with pytest.raises(
        ValueError,
        match="Contradictory version pinnings found: >2 and <1",
    ):
        combine_version_pinnings([">2", "<1"])

    with pytest.raises(
        ValueError,
        match="Contradictory version pinnings found: <1 and >2",
    ):
        combine_version_pinnings(["<1", ">2"])

    with pytest.raises(
        ValueError,
        match="Contradictory version pinnings found: >1 and <1",
    ):
        combine_version_pinnings([">1", "<1"])

    with pytest.raises(
        ValueError,
        match="Contradictory version pinnings found: <=1 and >1",
    ):
        combine_version_pinnings(["<=1", ">1"])

    with pytest.raises(
        ValueError,
        match="Contradictory version pinnings found: >1 and <=1",
    ):
        combine_version_pinnings([">1", "<=1"])


def test_exact_pinning_with_contradictory_ranges() -> None:
    with pytest.raises(
        ValueError,
        match="Contradictory version pinnings found: =3 and <2",
    ):
        combine_version_pinnings(["=3", "<2", ">4"])


def test_multiple_exact_pinnings() -> None:
    with pytest.raises(
        ValueError,
        match="Multiple exact version pinnings found: =2, =3",
    ):
        combine_version_pinnings(["=2", "=3"])


def test_exact_pinning() -> None:
    assert combine_version_pinnings(["=3", ">=2", "<=4"]) == "=3"
    assert combine_version_pinnings(["=3", ">1", "<4"]) == "=3"
    assert combine_version_pinnings(["=2", ">1", "<3"]) == "=2"
    assert combine_version_pinnings(["=2", "<3", "<=3", "<4"]) == "=2"
    assert combine_version_pinnings([">=1", "<=1"]) == ">=1,<=1"


def test_exact_pinning_with_irrelevant_ranges() -> None:
    with pytest.raises(
        ValueError,
        match="Contradictory version pinnings found: =3 and <1",
    ):
        assert combine_version_pinnings(["=3", "<1", ">4"])


def test_combine_version_pinnings_with_no_operator() -> None:
    # This should hit the case where _parse_pinning returns "", 0
    assert combine_version_pinnings(["3"]) == ""


def test_combine_version_pinnings_with_non_redundant_pinnings() -> None:
    # Non-redundant cases
    assert combine_version_pinnings([">=2", "<3"]) == ">=2,<3"


def test_combine_version_pinnings_with_multiple_exact_pinnings() -> None:
    # This should raise an error due to multiple exact pinnings
    with pytest.raises(
        ValueError,
        match="Multiple exact version pinnings found: =2, =3",
    ):
        combine_version_pinnings(["=2", "=3"])


def test_general_contradictory_pinnings() -> None:
    # This test ensures that contradictory non-exact pinnings raise a ValueError
    with pytest.raises(
        ValueError,
        match="Contradictory version pinnings found: >=2 and <1",
    ):
        combine_version_pinnings([">=2", "<1"])


def test_full_versions_and_major_only() -> None:
    assert combine_version_pinnings([">0.0.1", "<2", "=1.0.0"]) == "=1.0.0"
    assert combine_version_pinnings([" > 0.0.1", " < 2", " = 1.0.0"]) == "=1.0.0"


def test_is_redundant() -> None:
    assert _is_redundant(">2", [">5"])
    assert not _is_redundant(">5", [">2"])
    assert _is_redundant("<5", ["<2"])
    assert _is_redundant(">=2", [">2"])
    assert not _is_redundant(">2", [">=2"])


def test_invalid_parse_pinning() -> None:
    with pytest.raises(ValueError, match="Invalid version pinning:"):
        _parse_pinning("<<1")
    with pytest.raises(ValueError, match="Invalid version pinning:"):
        _parse_pinning(">>1")
    with pytest.raises(ValueError, match="Invalid version pinning:"):
        _parse_pinning("=<1")
    with pytest.raises(ValueError, match="Invalid version pinning:"):
        _parse_pinning("=>1")


def test_duplicate_pinning() -> None:
    assert combine_version_pinnings(["=1", "=1"]) == "=1"
    assert combine_version_pinnings([">=1", ">=1", "=1"]) == "=1"
