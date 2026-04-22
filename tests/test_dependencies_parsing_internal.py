"""Focused tests for active internal dependency-parsing helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from unidep._dependencies_parsing import (
    _is_empty_git_submodule,
    _move_optional_dependencies_to_dependencies,
    parse_requirements,
)
from unidep.utils import PathWithExtras


def test_move_optional_dependencies_star_promotes_all_groups(
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = {
        "dependencies": ["numpy"],
        "optional_dependencies": {
            "dev": ["pytest"],
            "docs": ["sphinx"],
        },
    }

    _move_optional_dependencies_to_dependencies(
        data,
        PathWithExtras(Path("requirements.yaml"), ["*"]),
        verbose=True,
    )

    assert data["dependencies"] == ["numpy", "pytest", "sphinx"]
    assert "optional_dependencies" not in data
    assert "Moving all optional dependencies" in capsys.readouterr().out


def test_parse_requirements_skips_empty_paired_dependency_after_filtering(
    tmp_path: Path,
) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        """\
        dependencies:
          - conda: numpy
            pip: numpy
        """,
    )

    requirements = parse_requirements(req_file, skip_dependencies=["numpy"])

    assert requirements.requirements == {}
    assert requirements.dependency_entries == []


def test_is_empty_git_submodule_false_for_non_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("not a directory")
    assert _is_empty_git_submodule(file_path) is False
