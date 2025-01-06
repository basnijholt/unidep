"""Tests for parsing local dependencies from wheels and zips."""

import textwrap
from pathlib import Path
from typing import Literal

import pytest

from unidep import parse_local_dependencies, parse_requirements

from .helpers import maybe_as_toml


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_local_wheel(tmp_path: Path, toml_or_yaml: Literal["toml", "yaml"]) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../example.whl
            """,
        ),
    )
    r1 = maybe_as_toml(toml_or_yaml, r1)
    r1 = maybe_as_toml(toml_or_yaml, r1)

    local_dep = tmp_path / "example.whl"
    local_dep.touch()  # Create a dummy .whl file

    dependencies = parse_local_dependencies(
        r1,
        check_pip_installable=False,
        verbose=True,
    )
    assert dependencies[project1.resolve()] == [local_dep.resolve()]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_local_zip(tmp_path: Path, toml_or_yaml: Literal["toml", "yaml"]) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../example.zip
            """,
        ),
    )
    r1 = maybe_as_toml(toml_or_yaml, r1)

    local_dep = tmp_path / "example.zip"
    local_dep.touch()  # Create a dummy .zip file

    dependencies = parse_local_dependencies(r1, check_pip_installable=False)
    assert dependencies[project1.resolve()] == [local_dep.resolve()]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_local_wheel_and_folder(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    project2 = tmp_path / "project2"
    project2.mkdir(exist_ok=True, parents=True)
    (project2 / "setup.py").touch()  # Make project2 pip installable
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../example.whl
                - ../project2
            """,
        ),
    )
    r1 = maybe_as_toml(toml_or_yaml, r1)

    local_dep = tmp_path / "example.whl"
    local_dep.touch()  # Create a dummy .whl file
    with pytest.warns(UserWarning, match="is not managed by unidep"):
        dependencies = parse_local_dependencies(r1, check_pip_installable=False)
    assert dependencies[project1.resolve()] == [
        local_dep.resolve(),
        project2.resolve(),
    ]

    requirements = parse_requirements(r1, verbose=True)
    assert requirements.requirements == {}


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_local_wheel_with_extras(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../example.whl[extra1,extra2]
            """,
        ),
    )
    r1 = maybe_as_toml(toml_or_yaml, r1)

    local_dep = tmp_path / "example.whl"
    local_dep.touch()  # Create a dummy .whl file

    dependencies = parse_local_dependencies(r1, check_pip_installable=False)
    assert dependencies[project1.resolve()] == [local_dep.resolve()]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_local_wheel_in_dependencies(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../example.whl
            """,
        ),
    )
    r1 = maybe_as_toml(toml_or_yaml, r1)

    local_dep = tmp_path / "example.whl"
    local_dep.touch()  # Create a dummy .whl file

    dependencies = parse_local_dependencies(r1, check_pip_installable=False)
    assert dependencies[project1.resolve()] == [local_dep.resolve()]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_nested_local_dependencies_with_wheel(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    project1 = tmp_path / "project1"
    project2 = tmp_path / "project2"
    project3 = tmp_path / "project3"
    for project in [project1, project2, project3]:
        project.mkdir(exist_ok=True, parents=True)
        (project / "setup.py").touch()  # Make projects pip installable

    wheel_dep = tmp_path / "example.whl"
    wheel_dep.touch()  # Create a dummy .whl file

    r1 = project1 / "requirements.yaml"
    r2 = project2 / "requirements.yaml"
    r3 = project3 / "requirements.yaml"

    r1.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../project2
            """,
        ),
    )

    r2.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../project3
                - ../example.whl
            """,
        ),
    )

    r3.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pytest
            """,
        ),
    )

    r1 = maybe_as_toml(toml_or_yaml, r1)
    r2 = maybe_as_toml(toml_or_yaml, r2)
    r3 = maybe_as_toml(toml_or_yaml, r3)

    local_dependencies = parse_local_dependencies(r1, verbose=True)

    assert local_dependencies == {
        project1.resolve(): [
            wheel_dep.resolve(),
            project2.resolve(),
            project3.resolve(),
        ],
    }
