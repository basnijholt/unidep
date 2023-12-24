"""unidep's YAML parsing of the `includes` list."""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from ruamel.yaml import YAML

from unidep import (
    find_requirements_files,
    parse_project_dependencies,
    parse_requirements,
    resolve_conflicts,
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
def test_circular_includes(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    project2 = tmp_path / "project2"
    project2.mkdir(exist_ok=True, parents=True)

    r1 = project1 / "requirements.yaml"
    r2 = project2 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - adaptive-scheduler
            includes:
                - ../project2
                - ../project2  # duplicate include (shouldn't affect the result)
            """,
        ),
    )
    r2.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - adaptive
            includes:
                - ../project1
            """,
        ),
    )
    r1 = maybe_as_toml(toml_or_yaml, r1)
    # Only convert r1 to toml, not r2, because we want to test that
    requirements = parse_requirements(r1, r2, verbose=False)
    # Both will be duplicated because of the circular dependency
    # but `resolve_conflicts` will remove the duplicates
    assert len(requirements.requirements["adaptive"]) == 4
    assert len(requirements.requirements["adaptive-scheduler"]) == 2
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    assert len(resolved["adaptive"]) == 1
    assert len(resolved["adaptive"][None]) == 2
    assert len(resolved["adaptive-scheduler"]) == 1
    assert len(resolved["adaptive-scheduler"][None]) == 2


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_parse_project_dependencies(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    project2 = tmp_path / "project2"
    project2.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r2 = project2 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            includes:
                - ../project2
                - ../project2  # duplicate include (shouldn't affect the result)
            """,
        ),
    )
    r2.write_text(
        textwrap.dedent(
            """\
            includes:
                - ../project1
            """,
        ),
    )
    r2 = maybe_as_toml(toml_or_yaml, r2)
    # Only convert r2 to toml, not r1, because we want to test that
    local_dependencies = parse_project_dependencies(
        r1,
        r2,
        verbose=False,
        check_pip_installable=False,
    )
    expected_dependencies = {
        project1.resolve(): [project2.resolve()],
        project2.resolve(): [project1.resolve()],
    }
    assert local_dependencies == expected_dependencies


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_nested_includes(toml_or_yaml: Literal["toml", "yaml"], tmp_path: Path) -> None:
    project1 = tmp_path / "project1"
    project2 = tmp_path / "project2"
    project3 = tmp_path / "project3"
    project4 = tmp_path / "project4"
    for project in [project1, project2, project3, project4]:
        project.mkdir(exist_ok=True, parents=True)

    p1 = project1 / "requirements.yaml"
    p2 = project2 / "requirements.yaml"
    p3 = project3 / "requirements.yaml"
    p4 = project4 / "requirements.yaml"
    p1.write_text(
        textwrap.dedent(
            """\
            includes:
                - ../project2
            """,
        ),
    )
    p2.write_text(
        textwrap.dedent(
            """\
            includes:
                - ../project3
            """,
        ),
    )
    p3.write_text(
        textwrap.dedent(
            """\
            includes:
                - ../project4
            """,
        ),
    )
    p4.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            """,
        ),
    )
    p1 = maybe_as_toml(toml_or_yaml, p1)
    p2 = maybe_as_toml(toml_or_yaml, p2)
    p3 = maybe_as_toml(toml_or_yaml, p3)
    p4 = maybe_as_toml(toml_or_yaml, p4)
    local_dependencies = parse_project_dependencies(
        p1,
        p2,
        p3,
        verbose=False,
        check_pip_installable=False,
    )
    expected_dependencies = {
        project1.resolve(): [
            project2.resolve(),
            project3.resolve(),
            project4.resolve(),
        ],
        project2.resolve(): [project3.resolve(), project4.resolve()],
        project3.resolve(): [project4.resolve()],
    }
    assert local_dependencies == expected_dependencies


def test_nonexistent_includes(tmp_path: Path) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            includes:
                - ../nonexistent_project
            """,
        ),
    )
    with pytest.raises(FileNotFoundError, match="not found."):
        parse_project_dependencies(r1, verbose=False, check_pip_installable=False)


def test_no_includes(tmp_path: Path) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pandas
            """,
        ),
    )
    local_dependencies = parse_project_dependencies(
        r1,
        verbose=False,
        check_pip_installable=False,
    )
    assert local_dependencies == {}


def test_mixed_real_and_placeholder_dependencies(tmp_path: Path) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - scipy
            includes:
                - ../project1  # Self include (circular dependency)
            """,
        ),
    )
    local_dependencies = parse_project_dependencies(
        r1,
        verbose=False,
        check_pip_installable=False,
    )
    assert local_dependencies == {}


def test_parse_project_dependencies_pip_installable(tmp_path: Path) -> None:
    example_folder = tmp_path / "example"
    shutil.copytree(REPO_ROOT / "example", example_folder)

    # Add an extra project
    extra_project = example_folder / "project69"
    extra_project.mkdir(exist_ok=True, parents=True)
    (extra_project / "requirements.yaml").write_text("includes: [../setup_py_project]")

    # Add a line to project1 includes
    setup_py_project_req = example_folder / "setup_py_project" / "requirements.yaml"
    yaml = YAML(typ="safe")
    with setup_py_project_req.open("r") as f:
        requirements = yaml.load(f)
    requirements["includes"].append("../project69")
    with setup_py_project_req.open("w") as f:
        yaml.dump(requirements, f)

    found_files = find_requirements_files(example_folder)
    assert len(found_files) == 6

    # Add a common requirements file
    common_requirements = example_folder / "common-requirements.yaml"
    common_requirements.write_text("includes: [./setup_py_project]")
    found_files.append(common_requirements)

    local_dependencies = parse_project_dependencies(
        *found_files,
        check_pip_installable=True,
        verbose=True,
    )
    assert local_dependencies
    assert local_dependencies == {
        example_folder / "setup_py_project": [
            example_folder / "hatch_project",
            example_folder / "setuptools_project",
        ],
        example_folder / "setuptools_project": [
            example_folder / "hatch_project",
        ],
        example_folder / "pyproject_toml_project": [
            example_folder / "hatch_project",
        ],
    }


def test_parse_project_dependencies_pip_installable_with_non_installable_project(
    tmp_path: Path,
) -> None:
    example_folder = tmp_path / "example"
    shutil.copytree(REPO_ROOT / "example", example_folder)

    # Add an extra project
    extra_project = example_folder / "project4"
    extra_project.mkdir(exist_ok=True, parents=True)
    (extra_project / "requirements.yaml").write_text("includes: [../setup_py_project]")

    # Add a line to hatch_project includes which should
    # make hatch_project depend on setup_py_project, via project4! However, project4 is
    # not `pip installable` so we're testing that path.
    setup_py_project_req = example_folder / "hatch_project" / "requirements.yaml"
    yaml = YAML(typ="safe")
    with setup_py_project_req.open("r") as f:
        requirements = yaml.load(f)
    requirements["includes"] = ["../project4"]
    with setup_py_project_req.open("w") as f:
        yaml.dump(requirements, f)

    found_files = find_requirements_files(example_folder)
    assert len(found_files) == 6

    local_dependencies = parse_project_dependencies(
        *found_files,
        check_pip_installable=True,
        verbose=True,
    )
    assert local_dependencies
    assert local_dependencies == {
        example_folder / "hatch_project": [
            example_folder / "setup_py_project",
            example_folder / "setuptools_project",
        ],
        example_folder / "setup_py_project": [
            example_folder / "hatch_project",
            example_folder / "setuptools_project",
        ],
        example_folder / "pyproject_toml_project": [
            example_folder / "hatch_project",
            example_folder / "setup_py_project",
            example_folder / "setuptools_project",
        ],
        example_folder / "setuptools_project": [
            example_folder / "hatch_project",
            example_folder / "setup_py_project",
        ],
    }
