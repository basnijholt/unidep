"""Test parsing nested local dependencies from YAML files."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from unidep import (
    parse_local_dependencies,
    parse_requirements,
)
from unidep._dependencies_parsing import yaml_to_toml

if TYPE_CHECKING:
    import sys

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:  # pragma: no cover
        from typing_extensions import Literal


REPO_ROOT = Path(__file__).parent.parent


def maybe_as_toml(toml_or_yaml: Literal["toml", "yaml"], p: Path) -> Path:
    if toml_or_yaml == "toml":
        toml = yaml_to_toml(p)
        p.unlink()
        p = p.with_name("pyproject.toml")
        p.write_text(toml)
    return p


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_nested_local_dependencies_multiple_levels(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    project1 = tmp_path / "project1"
    project2 = tmp_path / "project2"
    project3 = tmp_path / "project3"
    project4 = tmp_path / "project4"
    for project in [project1, project2, project3, project4]:
        project.mkdir(exist_ok=True, parents=True)
        (project / "setup.py").touch()  # Make projects pip installable

    r1 = project1 / "requirements.yaml"
    r2 = project2 / "requirements.yaml"
    r3 = project3 / "requirements.yaml"
    r4 = project4 / "requirements.yaml"

    r1.write_text(
        textwrap.dedent("""
        dependencies:
          - package1
        local_dependencies:
          - ../project2
    """),
    )

    r2.write_text(
        textwrap.dedent("""
        dependencies:
          - package2
        local_dependencies:
          - ../project3
    """),
    )

    r3.write_text(
        textwrap.dedent("""
        dependencies:
          - package3
        local_dependencies:
          - ../project4
    """),
    )

    r4.write_text(
        textwrap.dedent("""
        dependencies:
          - package4
    """),
    )

    r1 = maybe_as_toml(toml_or_yaml, r1)
    r2 = maybe_as_toml(toml_or_yaml, r2)
    r3 = maybe_as_toml(toml_or_yaml, r3)
    r4 = maybe_as_toml(toml_or_yaml, r4)

    local_dependencies = parse_local_dependencies(
        r1,
        verbose=True,
        check_pip_installable=True,
    )

    assert local_dependencies == {
        project1.resolve(): [
            project2.resolve(),
            project3.resolve(),
            project4.resolve(),
        ],
    }

    requirements = parse_requirements(r1, verbose=True)
    assert set(requirements.requirements.keys()) == {
        "package1",
        "package2",
        "package3",
        "package4",
    }


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_nested_local_dependencies_with_circular_reference(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    project1 = tmp_path / "project1"
    project2 = tmp_path / "project2"
    project3 = tmp_path / "project3"
    for project in [project1, project2, project3]:
        project.mkdir(exist_ok=True, parents=True)
        (project / "setup.py").touch()  # Make projects pip installable

    r1 = project1 / "requirements.yaml"
    r2 = project2 / "requirements.yaml"
    r3 = project3 / "requirements.yaml"

    r1.write_text(
        textwrap.dedent("""
        dependencies:
          - package1
        local_dependencies:
          - ../project2
    """),
    )

    r2.write_text(
        textwrap.dedent("""
        dependencies:
          - package2
        local_dependencies:
          - ../project3
    """),
    )

    r3.write_text(
        textwrap.dedent("""
        dependencies:
          - package3
        local_dependencies:
          - ../project1
    """),
    )

    r1 = maybe_as_toml(toml_or_yaml, r1)
    r2 = maybe_as_toml(toml_or_yaml, r2)
    r3 = maybe_as_toml(toml_or_yaml, r3)

    local_dependencies = parse_local_dependencies(
        r1,
        verbose=True,
        check_pip_installable=True,
    )

    assert local_dependencies == {
        project1.resolve(): [project2.resolve(), project3.resolve()],
    }

    requirements = parse_requirements(r1, verbose=True)
    assert set(requirements.requirements.keys()) == {"package1", "package2", "package3"}


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_nested_local_dependencies_with_non_unidep_managed_project(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    project1 = tmp_path / "project1"
    project2 = tmp_path / "project2"
    project3 = tmp_path / "project3"
    for project in [project1, project2]:
        project.mkdir(exist_ok=True, parents=True)
        (project / "setup.py").touch()  # Make projects pip installable

    # Create project3 as a non-unidep managed project
    project3.mkdir(exist_ok=True, parents=True)
    (project3 / "setup.py").touch()  # Make it pip installable but not unidep managed

    r1 = project1 / "requirements.yaml"
    r2 = project2 / "requirements.yaml"

    r1.write_text(
        textwrap.dedent("""
        dependencies:
          - package1
        local_dependencies:
          - ../project2
    """),
    )

    r2.write_text(
        textwrap.dedent("""
        dependencies:
          - package2
        local_dependencies:
          - ../project3
    """),
    )

    r1 = maybe_as_toml(toml_or_yaml, r1)
    r2 = maybe_as_toml(toml_or_yaml, r2)

    # project3 is non-unidep managed but pip installable

    with pytest.warns(UserWarning, match="not managed by unidep"):
        local_dependencies = parse_local_dependencies(
            r1,
            verbose=True,
            check_pip_installable=True,
            warn_non_managed=True,
        )

    assert local_dependencies == {
        project1.resolve(): [project2.resolve(), project3.resolve()],
    }

    # We don't expect a warning here anymore, as it should have been raised in parse_local_dependencies
    requirements = parse_requirements(r1, verbose=True)

    assert set(requirements.requirements.keys()) == {"package1", "package2"}
