"""unidep's YAML parsing of the `local_dependencies` list."""

from __future__ import annotations

import shutil
import textwrap
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from ruamel.yaml import YAML

from unidep import (
    find_requirements_files,
    parse_local_dependencies,
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
def test_circular_local_dependencies(
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
            local_dependencies:
                - ../project2
                - ../project2  # duplicate include (shouldn't affect the result)
            """,
        ),
    )
    # Test with old `includes` name
    r2.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - adaptive
            includes:  # `local_dependencies` was called `includes` in <=0.41.0
                - ../project1
            """,
        ),
    )
    r1 = maybe_as_toml(toml_or_yaml, r1)
    # Only convert r1 to toml, not r2, because we want to test that
    with pytest.warns(DeprecationWarning, match="is deprecated since 0.42.0"):
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
def test_parse_local_dependencies(
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
            local_dependencies:
                - ../project2
                - ../project2  # duplicate include (shouldn't affect the result)
            """,
        ),
    )
    r2.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../project1
            """,
        ),
    )
    r2 = maybe_as_toml(toml_or_yaml, r2)
    # Only convert r2 to toml, not r1, because we want to test that
    local_dependencies = parse_local_dependencies(
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
def test_nested_local_dependencies(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
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
            local_dependencies:
                - ../project2
            """,
        ),
    )
    p2.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../project3
            """,
        ),
    )
    p3.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
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
    local_dependencies = parse_local_dependencies(
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


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_nonexistent_local_dependencies(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../nonexistent_project
            """,
        ),
    )
    r1 = maybe_as_toml(toml_or_yaml, r1)
    with pytest.raises(FileNotFoundError, match="not found."):
        parse_local_dependencies(r1, verbose=False, check_pip_installable=False)


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_no_local_dependencies(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
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
    r1 = maybe_as_toml(toml_or_yaml, r1)
    local_dependencies = parse_local_dependencies(
        r1,
        verbose=False,
        check_pip_installable=False,
    )
    assert local_dependencies == {}


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_mixed_real_and_placeholder_dependencies(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - scipy
            local_dependencies:
                - ../project1  # Self include (circular dependency)
            """,
        ),
    )
    r1 = maybe_as_toml(toml_or_yaml, r1)
    local_dependencies = parse_local_dependencies(
        r1,
        verbose=False,
        check_pip_installable=False,
    )
    assert local_dependencies == {}


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_parse_local_dependencies_pip_installable(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    example_folder = tmp_path / "example"
    shutil.copytree(REPO_ROOT / "example", example_folder)

    # Add an extra project
    extra_project = example_folder / "extra_project"
    extra_project.mkdir(exist_ok=True, parents=True)
    (extra_project / "requirements.yaml").write_text(
        "local_dependencies: [../setup_py_project]",
    )

    # Add a line to project1 local_dependencies
    setup_py_project_req = example_folder / "setup_py_project" / "requirements.yaml"
    yaml = YAML(typ="safe")
    with setup_py_project_req.open("r") as f:
        requirements = yaml.load(f)
    requirements["local_dependencies"].append("../extra_project")
    with setup_py_project_req.open("w") as f:
        yaml.dump(requirements, f)

    setup_py_project_req = maybe_as_toml(toml_or_yaml, setup_py_project_req)
    found_files = find_requirements_files(example_folder)
    assert len(found_files) == 6

    # Add a common requirements file
    common_requirements = example_folder / "common-requirements.yaml"
    common_requirements.write_text("local_dependencies: [./setup_py_project]")
    common_requirements = maybe_as_toml(toml_or_yaml, common_requirements)
    found_files.append(common_requirements)

    local_dependencies = parse_local_dependencies(
        *found_files,
        check_pip_installable=True,
        verbose=True,
    )
    assert local_dependencies
    # extra_project is not `pip installable` so it should not be included in the values()
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
        example_folder / "extra_project": [
            example_folder / "hatch_project",
            example_folder / "setup_py_project",
            example_folder / "setuptools_project",
        ],
        example_folder: [
            example_folder / "hatch_project",
            example_folder / "setup_py_project",
            example_folder / "setuptools_project",
        ],
    }


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_parse_local_dependencies_pip_installable_with_non_installable_project(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    example_folder = tmp_path / "example"
    shutil.copytree(REPO_ROOT / "example", example_folder)

    # Add an extra project
    extra_project = example_folder / "extra_project"
    extra_project.mkdir(exist_ok=True, parents=True)
    r_extra = extra_project / "requirements.yaml"
    r_extra.write_text("local_dependencies: [../setup_py_project]")
    r_extra = maybe_as_toml(toml_or_yaml, r_extra)

    # Add a line to hatch_project local_dependencies which should
    # make hatch_project depend on setup_py_project, via extra_project! However, extra_project is
    # not `pip installable` so we're testing that path.
    setup_py_project_req = example_folder / "hatch_project" / "requirements.yaml"
    yaml = YAML(typ="safe")
    with setup_py_project_req.open("r") as f:
        requirements = yaml.load(f)
    requirements["local_dependencies"] = ["../extra_project"]
    with setup_py_project_req.open("w") as f:
        yaml.dump(requirements, f)

    found_files = find_requirements_files(example_folder)
    assert len(found_files) == 6

    local_dependencies = parse_local_dependencies(
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
        example_folder / "extra_project": [
            example_folder / "hatch_project",
            example_folder / "setup_py_project",
            example_folder / "setuptools_project",
        ],
    }


def test_local_non_unidep_managed_dependency(tmp_path: Path) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    project2 = tmp_path / "project2"
    project2.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../project2  # is not managed by unidep
            """,
        ),
    )
    r2 = project2 / "setup.py"  # not managed by unidep
    r2.touch()

    requirements = parse_requirements(r1, verbose=True)  # This should not raise
    assert requirements.requirements == {}

    with pytest.warns(UserWarning, match="not managed by unidep"):
        data = parse_local_dependencies(r1, verbose=True)
    assert data == {project1.resolve(): [project2.resolve()]}


def test_local_non_unidep_and_non_installable_managed_dependency(
    tmp_path: Path,
) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    project2 = tmp_path / "project2"
    project2.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../project2  # is not managed by unidep and not installable
            """,
        ),
    )
    with pytest.raises(RuntimeError, match="is not pip installable"):
        parse_local_dependencies(r1, verbose=True)


def test_parse_local_dependencies_missing(
    tmp_path: Path,
) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../does-not-exist
            """,
        ),
    )
    with pytest.raises(FileNotFoundError, match="not found."):
        parse_local_dependencies(r1, verbose=True, raise_if_missing=True)

    local_dependencies = parse_local_dependencies(
        r1,
        verbose=True,
        raise_if_missing=False,
    )
    assert local_dependencies == {}


@pytest.mark.parametrize("unidep_managed", [True, False])
def test_parse_local_dependencies_without_local_deps_themselves(
    tmp_path: Path,
    unidep_managed: bool,  # noqa: FBT001
) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../project2
            """,
        ),
    )

    project2 = tmp_path / "project2"
    project2.mkdir(exist_ok=True, parents=True)
    r2 = project2 / "pyproject.toml"
    txt = textwrap.dedent(
        """\
            [build-system]
            requires = ["setuptools", "wheel"]
            """,
    )
    if unidep_managed:
        txt += '[tool.unidep]\ndependencies = ["numpy"]'
    r2.write_text(txt)
    ctx = (
        pytest.warns(UserWarning, match="not managed by unidep")
        if not unidep_managed
        else nullcontext()
    )
    with ctx:
        local_dependencies = parse_local_dependencies(
            r1,
            verbose=True,
            raise_if_missing=True,
        )
    assert local_dependencies == {project1: [project2]}

    r2.write_text("")
    with pytest.raises(RuntimeError, match="is not pip installable"):
        parse_local_dependencies(r1, verbose=True, raise_if_missing=True)
