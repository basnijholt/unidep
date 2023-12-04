"""Tests for the version conflict resolution logic."""
import pytest

from unidep._conflicts import (
    _select_preferred_version_within_platform,
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
    _select_preferred_version_within_platform(data)  # type: ignore[arg-type]


def test_single_pinning() -> None:
    assert combine_version_pinnings([">1"]) == ">1"


def test_multiple_non_redundant_pinnings() -> None:
    assert combine_version_pinnings([">1", "<=3"]) == ">1,<=3"


def test_redundant_pinning() -> None:
    assert combine_version_pinnings([">1", "<=3", "<4"]) == ">1,<=3"


def test_all_redundant_pinnings() -> None:
    assert combine_version_pinnings(["<3", "<=3", "<4"]) == "<=3"


def test_empty_list() -> None:
    assert combine_version_pinnings([]) == ""


def test_invalid_pinnings() -> None:
    assert combine_version_pinnings(["abc", "def"]) == ""


def test_mixed_valid_and_invalid_pinnings() -> None:
    assert combine_version_pinnings([">1", "abc", "<=3", ""]) == ">1,<=3"


def test_overlapping_pinnings() -> None:
    assert combine_version_pinnings([">=2", "<=2"]) == ">=2,<=2"


def test_contradictory_pinnings() -> None:
    with pytest.raises(ValueError):
        combine_version_pinnings([">2", "<1"])


def test_equals() -> None:
    assert combine_version_pinnings(["=2", "<3", "<=3", "<4"]) == "=2"


def test_exact_pinning_with_redundant_ranges() -> None:
    assert combine_version_pinnings(["=3", ">2", "<4"]) == "=3"


def test_exact_pinning_with_contradictory_ranges() -> None:
    with pytest.raises(ValueError):
        combine_version_pinnings(["=3", "<2", ">4"])


def test_multiple_exact_pinnings() -> None:
    assert (
        combine_version_pinnings(["=2", "=3"]) == "=2"
    )  # Assuming the function picks the first exact pinning


def test_exact_pinning_with_overlapping_ranges() -> None:
    assert combine_version_pinnings(["=3", ">=2", "<=4"]) == "=3"


def test_exact_pinning_with_irrelevant_ranges() -> None:
    assert combine_version_pinnings(["=3", ">1", "<4"]) == "=3"


def test_exact_pinning_with_irrelevant_ranges() -> None:
    with pytest.raises(
        ValueError,
        match="Contradictory version pinnings found: =3 and <1",
    ):
        assert combine_version_pinnings(["=3", "<1", ">4"])
