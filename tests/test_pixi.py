"""unidep tests."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from unidep import (
    parse_requirements,
    resolve_conflicts,
)
from unidep._dependencies_parsing import yaml_to_toml
from unidep._pixi import generate_pixi_toml

if TYPE_CHECKING:
    import sys
    from pathlib import Path

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:  # pragma: no cover
        from typing_extensions import Literal


def maybe_as_toml(toml_or_yaml: Literal["toml", "yaml"], p: Path) -> Path:
    if toml_or_yaml == "toml":
        toml = yaml_to_toml(p)
        p.unlink()
        p = p.with_name("pyproject.toml")
        p.write_text(toml)
    return p


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_filter_python_dependencies_with_platforms(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            channels:
                - conda-forge
            dependencies:
                - foo # [unix]
            platforms:
                - linux-64
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, ["linux-64"])
    output_file = tmp_path / "pixi.toml"
    generate_pixi_toml(
        resolved,
        project_name=None,
        channels=requirements.channels,
        platforms=requirements.platforms,
        output_file=output_file,
        verbose=False,
    )
    assert output_file.read_text() == textwrap.dedent(
        """\
        [project]
        name = "unidep"
        platforms = [
            "linux-64",
        ]
        channels = [
            "conda-forge",
        ]

        [target.linux-64.dependencies]
        foo = "*"
        """,
    )
