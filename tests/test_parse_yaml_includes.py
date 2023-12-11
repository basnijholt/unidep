"""unidep's YAML parsing of the `includes` list."""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from unidep import (
    find_requirements_files,
    parse_project_dependencies,
    parse_yaml_requirements,
    resolve_conflicts,
)

REPO_ROOT = Path(__file__).parent.parent


def test_circular_includes(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(r1, r2, verbose=False)
    # Both will be duplicated because of the circular dependency
    # but `resolve_conflicts` will remove the duplicates
    assert len(requirements.requirements["adaptive"]) == 4
    assert len(requirements.requirements["adaptive-scheduler"]) == 2
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    assert len(resolved["adaptive"]) == 1
    assert len(resolved["adaptive"][None]) == 2
    assert len(resolved["adaptive-scheduler"]) == 1
    assert len(resolved["adaptive-scheduler"][None]) == 2


def test_parse_project_dependencies(tmp_path: Path) -> None:
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


def test_nested_includes(tmp_path: Path) -> None:
    project1 = tmp_path / "project1"
    project2 = tmp_path / "project2"
    project3 = tmp_path / "project3"
    project4 = tmp_path / "project4"
    for project in [project1, project2, project3, project4]:
        project.mkdir(exist_ok=True, parents=True)

    (project1 / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            includes:
                - ../project2
            """,
        ),
    )
    (project2 / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            includes:
                - ../project3
            """,
        ),
    )
    (project3 / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            includes:
                - ../project4
            """,
        ),
    )
    (project4 / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            """,
        ),
    )
    local_dependencies = parse_project_dependencies(
        project1 / "requirements.yaml",
        project2 / "requirements.yaml",
        project3 / "requirements.yaml",
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
    with pytest.raises(FileNotFoundError, match="Include file"):
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
    (extra_project / "requirements.yaml").write_text("includes: [../project1]")

    # Add a line to project1 includes
    project1_req = example_folder / "project1" / "requirements.yaml"
    yaml = YAML(typ="safe")
    with project1_req.open("r") as f:
        requirements = yaml.load(f)
    requirements["includes"].append("../project69")
    with project1_req.open("w") as f:
        yaml.dump(requirements, f)

    found_files = find_requirements_files(example_folder)
    assert len(found_files) == 4

    # Add a common requirements file
    common_requirements = example_folder / "common-requirements.yaml"
    common_requirements.write_text("includes: [project1]")
    found_files.append(common_requirements)

    local_dependencies = parse_project_dependencies(
        *found_files,
        check_pip_installable=True,
        verbose=True,
    )
    assert local_dependencies
    assert local_dependencies == {
        example_folder / "project1": [
            example_folder / "project2",
            example_folder / "project3",
        ],
        example_folder / "project2": [
            example_folder / "project3",
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
    (extra_project / "requirements.yaml").write_text("includes: [../project1]")

    # Add a line to project3 includes which should
    # make project3 depend on project1, via project4! However, project4 is
    # not `pip installable` so we're testing that path.
    project1_req = example_folder / "project3" / "requirements.yaml"
    yaml = YAML(typ="safe")
    with project1_req.open("r") as f:
        requirements = yaml.load(f)
    requirements["includes"] = ["../project4"]
    with project1_req.open("w") as f:
        yaml.dump(requirements, f)

    found_files = find_requirements_files(example_folder)
    assert len(found_files) == 4

    requirements = parse_project_dependencies(
        *found_files,
        check_pip_installable=True,
        verbose=True,
    )
    assert requirements
    assert requirements == {
        example_folder / "project1": [
            example_folder / "project2",
            example_folder / "project3",
        ],
        example_folder / "project2": [
            example_folder / "project1",
            example_folder / "project3",
        ],
        example_folder / "project3": [
            example_folder / "project1",
            example_folder / "project2",
        ],
    }
