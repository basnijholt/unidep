"""Tests for user-shaped dependency selection behavior."""

from __future__ import annotations

import textwrap
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Tuple, cast

import pytest

from unidep._conflicts import VersionConflictError
from unidep._dependencies_parsing import DependencyOrigin, parse_requirements
from unidep._dependency_selection import (
    MergedSourceCandidate,
    _joined_pinnings_are_safely_satisfiable,
    _origin_to_text,
    collapse_selected_universals,
    select_conda_like_requirements,
    select_pip_requirements,
)

if TYPE_CHECKING:
    from unidep.platform_definitions import Platform


def _write_requirements(tmp_path: Path, content: str) -> Path:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(textwrap.dedent(content))
    return req_file


def _selected_summary(
    selected: dict[Platform | None, list[MergedSourceCandidate]],
) -> dict[Platform | None, list[tuple[str, str, str | None]]]:
    return {
        platform: [
            (candidate.source, candidate.spec.name, candidate.spec.pin)
            for candidate in candidates
        ]
        for platform, candidates in selected.items()
    }


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
            Tuple[Path, ...],
            (
                PureWindowsPath("libs\\a"),
                PureWindowsPath("libs\\b"),
            ),
        ),
    )
    assert _origin_to_text(origin) == (
        "requirements.yaml, item 3, group dev, via libs/a -> libs/b"
    )


def test_joined_pinnings_are_safely_satisfiable_for_user_shaped_pin_strings() -> None:
    assert _joined_pinnings_are_safely_satisfiable(
        [">=2", ">=1", ">2", "<=3", "<4"],
    )
    assert _joined_pinnings_are_safely_satisfiable(["==1", "~=1.0"])
    assert not _joined_pinnings_are_safely_satisfiable(["==2.*", "<=1"])
    assert not _joined_pinnings_are_safely_satisfiable(["==1.*", "<=1", "!=1"])
    assert not _joined_pinnings_are_safely_satisfiable(["!=1.*"])
    assert not _joined_pinnings_are_safely_satisfiable(["===1"])
    assert not _joined_pinnings_are_safely_satisfiable(
        ["@ git+https://example.com/example.git"],
    )


def test_select_conda_like_requirements_prefers_pinned_conda_over_unpinned_pip(
    tmp_path: Path,
) -> None:
    req_file = _write_requirements(
        tmp_path,
        """\
        platforms:
          - linux-64
        dependencies:
          - conda: click >=8
            pip: click
        """,
    )

    requirements = parse_requirements(req_file)
    selected = select_conda_like_requirements(
        requirements.dependency_entries,
        requirements.platforms,
    )

    assert _selected_summary(selected) == {
        "linux-64": [("conda", "click", ">=8")],
    }


def test_select_conda_like_requirements_prefers_pip_extras_over_conda(
    tmp_path: Path,
) -> None:
    req_file = _write_requirements(
        tmp_path,
        """\
        platforms:
          - linux-64
        dependencies:
          - conda: adaptive
            pip: adaptive[notebook]
        """,
    )

    requirements = parse_requirements(req_file)
    selected = select_conda_like_requirements(
        requirements.dependency_entries,
        requirements.platforms,
    )

    assert _selected_summary(selected) == {
        "linux-64": [("pip", "adaptive[notebook]", None)],
    }


def test_select_conda_like_requirements_prefers_narrower_pinned_selector_scope(
    tmp_path: Path,
) -> None:
    req_file = _write_requirements(
        tmp_path,
        """\
        platforms:
          - linux-64
          - osx-64
          - osx-arm64
        dependencies:
          - conda: click >=8
          - pip: click >1  # [osx]
        """,
    )

    requirements = parse_requirements(req_file)
    selected = select_conda_like_requirements(
        requirements.dependency_entries,
        requirements.platforms,
    )

    assert _selected_summary(selected) == {
        "linux-64": [("conda", "click", ">=8")],
        "osx-64": [("pip", "click", ">1")],
        "osx-arm64": [("pip", "click", ">1")],
    }


def test_select_conda_like_requirements_reports_final_collisions_with_origins(
    tmp_path: Path,
) -> None:
    req_file = _write_requirements(
        tmp_path,
        """\
        platforms:
          - linux-64
        dependencies:
          - conda: foo
          - conda: python-foo
            pip: foo >1
        """,
    )

    requirements = parse_requirements(req_file)
    match = (
        r"(?s)Final Dependency Collision:"
        r".*'foo' on platform 'linux-64'"
        r".*conda: foo \("
        r".*requirements\.yaml, item 1"
        r".*pip: foo >1 \("
        r".*requirements\.yaml, item 2"
    )
    with pytest.raises(ValueError, match=match):
        select_conda_like_requirements(
            requirements.dependency_entries,
            requirements.platforms,
        )


def test_select_pip_requirements_merges_supported_wildcard_pinnings(
    tmp_path: Path,
) -> None:
    req_file = _write_requirements(
        tmp_path,
        """\
        platforms:
          - linux-64
        dependencies:
          - conda: numpy
          - pip: foo ==1.*
          - pip: foo >=1.5
        """,
    )

    requirements = parse_requirements(req_file)
    selected = select_pip_requirements(
        requirements.dependency_entries,
        requirements.platforms,
    )

    assert _selected_summary(selected) == {
        "linux-64": [("pip", "foo", "==1.*,>=1.5")],
    }


def test_select_pip_requirements_merges_compatible_compatible_release_pinnings(
    tmp_path: Path,
) -> None:
    req_file = _write_requirements(
        tmp_path,
        """\
        platforms:
          - linux-64
        dependencies:
          - pip: foo ~=1.4
          - pip: foo <2
        """,
    )

    requirements = parse_requirements(req_file)
    selected = select_pip_requirements(
        requirements.dependency_entries,
        requirements.platforms,
    )

    assert _selected_summary(selected) == {
        "linux-64": [("pip", "foo", "~=1.4,<2")],
    }


def test_select_pip_requirements_rejects_unsafely_merged_wildcard_pinnings(
    tmp_path: Path,
) -> None:
    req_file = _write_requirements(
        tmp_path,
        """\
        platforms:
          - linux-64
        dependencies:
          - pip: foo ==1.*
          - pip: foo >2
        """,
    )

    requirements = parse_requirements(req_file)
    with pytest.raises(VersionConflictError, match="Invalid version pinning '==1."):
        select_pip_requirements(
            requirements.dependency_entries,
            requirements.platforms,
        )


def test_select_pip_requirements_rejects_multiple_exact_pinnings(
    tmp_path: Path,
) -> None:
    req_file = _write_requirements(
        tmp_path,
        """\
        platforms:
          - linux-64
        dependencies:
          - pip: foo ==1
          - pip: foo ==2
        """,
    )

    requirements = parse_requirements(req_file)
    with pytest.raises(
        VersionConflictError,
        match="Multiple exact version pinnings found: ==1, ==2 for `foo`",
    ):
        select_pip_requirements(
            requirements.dependency_entries,
            requirements.platforms,
        )


def test_collapse_selected_universals_collapses_user_declared_universal_dependencies(
    tmp_path: Path,
) -> None:
    req_file = _write_requirements(
        tmp_path,
        """\
        platforms:
          - linux-64
          - osx-arm64
        dependencies:
          - conda: numpy >=1
        """,
    )

    requirements = parse_requirements(req_file)
    selected = select_conda_like_requirements(
        requirements.dependency_entries,
        requirements.platforms,
    )
    collapsed = collapse_selected_universals(selected, requirements.platforms)

    assert _selected_summary(collapsed) == {
        None: [("conda", "numpy", ">=1")],
    }
