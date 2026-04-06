"""Direct tests for the shared dependency-selection helpers."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import cast

import pytest
from packaging.version import Version

from unidep._conflicts import VersionConflictError
from unidep._dependencies_parsing import DependencyOrigin
from unidep._dependency_selection import (
    SourceRequirement,
    _bump_release_prefix,
    _canonicalize_joined_pinnings,
    _exact_pinning_version_text,
    _joined_pinnings_are_safely_satisfiable,
    _merge_source_requirements,
    _normalize_pinning_token_for_satisfiability,
    _normalized_pinnings_are_satisfiable,
    _origin_to_text,
    _parse_supported_pinning,
    _stricter_lower_bound,
    _stricter_upper_bound,
    collapse_selected_universals,
)
from unidep.platform_definitions import Spec


def test_canonicalize_joined_pinnings_deduplicates_and_orders() -> None:
    assert _canonicalize_joined_pinnings([">1, <2", "<2", "!=1.5"]) == "!=1.5,>1,<2"


def test_origin_to_text_includes_optional_group_and_local_chain() -> None:
    origin = DependencyOrigin(
        source_file=Path("requirements.yaml"),
        dependency_index=3,
        optional_group="dev",
        local_dependency_chain=(Path("libs/a"), Path("libs/b")),
    )
    assert _origin_to_text(origin) == (
        "requirements.yaml, item 3, group dev, via libs/a -> libs/b"
    )


def test_origin_to_text_normalizes_windows_style_local_chain() -> None:
    origin = DependencyOrigin(
        source_file=Path("requirements.yaml"),
        dependency_index=3,
        optional_group="dev",
        local_dependency_chain=cast(
            tuple[Path, ...],
            (
                PureWindowsPath("libs\\a"),
                PureWindowsPath("libs\\b"),
            ),
        ),
    )
    assert _origin_to_text(origin) == (
        "requirements.yaml, item 3, group dev, via libs/a -> libs/b"
    )


@pytest.mark.parametrize(
    ("pinning", "expected"),
    [
        (">=1", [">=1"]),
        ("not-a-spec", None),
        (">=notaversion", None),
        ("!=1.*", None),
        ("!=notaversion", None),
        ("==1.*", [">=1", "<2"]),
        ("==notaversion", None),
        ("~=1.4", [">=1.4", "<2"]),
        ("~=1", None),
        ("===1", None),
    ],
)
def test_normalize_pinning_token_for_satisfiability(
    pinning: str,
    expected: list[str] | None,
) -> None:
    assert _normalize_pinning_token_for_satisfiability(pinning) == expected


def test_parse_supported_pinning_requires_operator() -> None:
    with pytest.raises(ValueError, match="Missing operator"):
        _parse_supported_pinning("1.2.3")


def test_bump_release_prefix_rejects_invalid_prefix_lengths() -> None:
    assert _bump_release_prefix((1, 2), 0) is None
    assert _bump_release_prefix((1, 2), 3) is None


def test_stricter_bound_helpers_cover_all_orderings() -> None:
    assert _stricter_lower_bound(None, (Version("1"), True)) == (Version("1"), True)
    assert _stricter_lower_bound((Version("1"), True), (Version("2"), False)) == (
        Version("2"),
        False,
    )
    assert _stricter_lower_bound((Version("2"), True), (Version("1"), False)) == (
        Version("2"),
        True,
    )
    assert _stricter_lower_bound((Version("2"), True), (Version("2"), False)) == (
        Version("2"),
        False,
    )

    assert _stricter_upper_bound(None, (Version("2"), True)) == (Version("2"), True)
    assert _stricter_upper_bound((Version("3"), True), (Version("2"), False)) == (
        Version("2"),
        False,
    )
    assert _stricter_upper_bound((Version("2"), True), (Version("3"), False)) == (
        Version("2"),
        True,
    )
    assert _stricter_upper_bound((Version("2"), True), (Version("2"), False)) == (
        Version("2"),
        False,
    )


@pytest.mark.parametrize(
    ("pinnings", "is_satisfiable"),
    [
        (["=1", "=2"], False),
        (["=1", "!=1"], False),
        ([">=2", "<1"], False),
        ([">=1", "<=1"], True),
        ([">=1", "<=1", "!=1"], False),
        (["=4", "<3"], False),
        (["=2", "<3"], True),
    ],
)
def test_normalized_pinnings_are_satisfiable(
    pinnings: list[str],
    is_satisfiable: object,
) -> None:
    assert _normalized_pinnings_are_satisfiable(pinnings) is is_satisfiable


def test_joined_pinnings_are_safely_satisfiable() -> None:
    assert _joined_pinnings_are_safely_satisfiable([">1, ,<2"])
    assert not _joined_pinnings_are_safely_satisfiable(["!=1.*"])


def test_exact_pinning_version_text_handles_supported_exact_forms() -> None:
    assert _exact_pinning_version_text("=1") == "1"
    assert _exact_pinning_version_text("==1") == "1"
    assert _exact_pinning_version_text("===1") == "1"
    assert _exact_pinning_version_text(">=1") is None


def test_renderer_exact_pin_conflict_uses_exact_pin_message() -> None:
    origin = DependencyOrigin(Path("requirements.yaml"), 0)
    requirements = [
        SourceRequirement(
            source="pip",
            spec=Spec(name="pkg", which="pip", pin="==1"),
            family_key=(None, "pkg"),
            base_name="pkg",
            normalized_name="pkg",
            extras=(),
            declared_platforms=None,
            origin=origin,
        ),
        SourceRequirement(
            source="pip",
            spec=Spec(name="pkg", which="pip", pin="==2"),
            family_key=(None, "pkg"),
            base_name="pkg",
            normalized_name="pkg",
            extras=(),
            declared_platforms=None,
            origin=origin,
        ),
    ]

    with pytest.raises(
        VersionConflictError,
        match="Multiple exact version pinnings found",
    ):
        _merge_source_requirements("pip", requirements)


def test_collapse_selected_universals_preserves_existing_universal_bucket() -> None:
    origin = DependencyOrigin(Path("requirements.yaml"), 0)
    candidate = _merge_source_requirements(
        "conda",
        [
            SourceRequirement(
                source="conda",
                spec=Spec(name="numpy", which="conda", pin=">=1"),
                family_key=("numpy", None),
                base_name="numpy",
                normalized_name="numpy",
                extras=(),
                declared_platforms=None,
                origin=origin,
            ),
        ],
    )

    assert collapse_selected_universals({None: [candidate]}) == {None: [candidate]}
