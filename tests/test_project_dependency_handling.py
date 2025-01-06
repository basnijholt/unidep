"""Tests for the `project_dependency_handling` feature."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING, Literal

import pytest

from unidep._dependencies_parsing import (
    _add_project_dependencies,
    parse_requirements,
)
from unidep.platform_definitions import Spec

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("project_dependencies", "handling_mode", "expected"),
    [
        # Test same-name
        (
            ["pandas", "requests"],
            "same-name",
            ["pandas", "requests"],
        ),
        # Test pip-only
        (
            ["pandas", "requests"],
            "pip-only",
            [{"pip": "pandas"}, {"pip": "requests"}],
        ),
        # Test ignore
        (["pandas", "requests"], "ignore", []),
        # Test invalid handling mode
        (["pandas", "requests"], "invalid", []),
    ],
)
def test_project_dependency_handling(
    project_dependencies: list[str],
    handling_mode: Literal["same-name", "pip-only", "ignore", "invalid"],
    expected: list[dict[str, str] | str],
) -> None:
    valid_unidep_dependencies: list[dict[str, str] | str] = [
        {"conda": "pandas", "pip": "pandas"},
        "requests",
        {"conda": "zstd", "pip": "zstandard"},
    ]
    unidep_dependencies = valid_unidep_dependencies.copy()
    if handling_mode == "invalid":
        with pytest.raises(ValueError, match="Invalid `project_dependency_handling`"):
            _add_project_dependencies(
                project_dependencies,
                unidep_dependencies,
                handling_mode,  # type: ignore[arg-type]
            )
    else:
        _add_project_dependencies(
            project_dependencies,
            unidep_dependencies,
            handling_mode,  # type: ignore[arg-type]
        )
        assert unidep_dependencies == valid_unidep_dependencies + expected


@pytest.mark.parametrize(
    "project_dependency_handling",
    ["same-name", "pip-only", "ignore"],
)
def test_project_dependency_handling_in_pyproject_toml(
    tmp_path: Path,
    project_dependency_handling: Literal["same-name", "pip-only", "ignore"],
) -> None:
    p = tmp_path / "pyproject.toml"
    p.write_text(
        textwrap.dedent(
            f"""\
            [build-system]
            requires = ["hatchling", "unidep"]
            build-backend = "hatchling.build"

            [project]
            name = "my-project"
            version = "0.1.0"
            dependencies = [
                "requests",
                "pandas",
            ]

            [tool.unidep]
            project_dependency_handling = "{project_dependency_handling}"
            dependencies = [
                {{ conda = "python-graphviz", pip = "graphviz" }},
                {{ conda = "graphviz" }},
            ]
            """,
        ),
    )

    requirements = parse_requirements(p)

    expected = {
        "python-graphviz": [
            Spec(name="python-graphviz", which="conda", identifier="17e5d607"),
        ],
        "graphviz": [
            Spec(name="graphviz", which="pip", identifier="17e5d607"),
            Spec(name="graphviz", which="conda", identifier="5eb93b8c"),
        ],
    }
    if project_dependency_handling == "pip-only":
        expected.update(
            {
                "requests": [Spec(name="requests", which="pip", identifier="08fd8713")],
                "pandas": [Spec(name="pandas", which="pip", identifier="9e467fa1")],
            },
        )
    elif project_dependency_handling == "same-name":
        expected.update(
            {
                "requests": [
                    Spec(name="requests", which="conda", identifier="08fd8713"),
                    Spec(name="requests", which="pip", identifier="08fd8713"),
                ],
                "pandas": [
                    Spec(name="pandas", which="conda", identifier="9e467fa1"),
                    Spec(name="pandas", which="pip", identifier="9e467fa1"),
                ],
            },
        )
    else:
        assert project_dependency_handling == "ignore"
    assert requirements.requirements == expected
