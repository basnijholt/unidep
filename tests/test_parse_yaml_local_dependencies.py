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
from unidep._dependencies_parsing import _get_local_deps_from_optional_section

from .helpers import maybe_as_toml

if TYPE_CHECKING:
    import sys

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:  # pragma: no cover
        from typing_extensions import Literal


REPO_ROOT = Path(__file__).parent.parent


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


def test_local_empty_git_submodule_dependency(
    tmp_path: Path,
) -> None:
    project1 = tmp_path / "project1"
    project1.mkdir(exist_ok=True, parents=True)
    project2 = tmp_path / "project2"
    project2.mkdir(exist_ok=True, parents=True)
    (project2 / ".git").touch()

    r1 = project1 / "requirements.yaml"
    r1.write_text(
        textwrap.dedent(
            """\
            local_dependencies:
                - ../project2  # has only `.git` file
            """,
        ),
    )
    with pytest.raises(RuntimeError, match="is an empty Git submodule"):
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


def test_local_dependency_with_extras(tmp_path: Path) -> None:
    """Test that local dependencies with extras are properly installed."""
    # Set up the directory structure
    package1_dir = tmp_path / "package1"
    my_package_dir = tmp_path / "my_package"
    my_package2_dir = tmp_path / "my_package2"

    package1_dir.mkdir()
    my_package_dir.mkdir()
    my_package2_dir.mkdir()

    # Create requirements.yaml for package1
    (package1_dir / "requirements.yaml").write_text(
        """
        dependencies:
          - common-dep
        local_dependencies:
          - ../my_package[my-extra]
        """,
    )

    # Create requirements.yaml for my_package
    (my_package_dir / "requirements.yaml").write_text(
        """
        dependencies:
          - my-package-dep
        optional_dependencies:
          my-extra:
            - ../my_package2
        """,
    )

    # Make my_package pip installable
    (my_package_dir / "setup.py").write_text(
        """
        from setuptools import setup
        setup(name="my_package", version="0.1.0")
        """,
    )

    # Create requirements.yaml for my_package2
    (my_package2_dir / "requirements.yaml").write_text(
        """
        dependencies:
          - my-package2-dep
        """,
    )

    # Make my_package2 pip installable
    (my_package2_dir / "setup.py").write_text(
        """
        from setuptools import setup
        setup(name="my_package2", version="0.1.0")
        """,
    )
    local_dependencies = parse_local_dependencies(
        package1_dir / "requirements.yaml",
        verbose=True,
    )
    assert local_dependencies == {
        package1_dir.absolute(): [
            my_package_dir.absolute(),
            my_package2_dir.absolute(),
        ],
    }


def test_nested_extras_in_local_dependencies(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Test local dependencies with nested extras chains.

    main_package
    -> lib_package[extra1,another-extra]
        -> utility_package[extra2] (from extra1)
            -> base_package (from extra2)
    """
    # Create a complex dependency structure:
    # main_package -> lib_package[extra1] -> utility_package[extra2] -> base_package

    main_dir = tmp_path / "main_package"
    lib_dir = tmp_path / "lib_package"
    utility_dir = tmp_path / "utility_package"
    base_dir = tmp_path / "base_package"

    for dir_path in [main_dir, lib_dir, utility_dir, base_dir]:
        dir_path.mkdir()
        # Make all packages pip-installable
        (dir_path / "setup.py").write_text(
            f"""
            from setuptools import setup
            setup(name="{dir_path.name}", version="0.1.0")
            """,
        )

    # Main package depends on lib_package with extra1
    (main_dir / "requirements.yaml").write_text(
        """
        dependencies:
          - main-dependency
        local_dependencies:
          - ../lib_package[extra1,another-extra]
        """,
    )

    # Lib package has optional dependency on utility_package with extra2
    (lib_dir / "requirements.yaml").write_text(
        """
        dependencies:
          - lib-dependency
        optional_dependencies:
          extra1:
            - lib-extra1-dependency
            - ../utility_package[extra2]
          another-extra:
            - another-extra-dependency
        """,
    )

    # Utility package has optional dependency on base_package
    (utility_dir / "requirements.yaml").write_text(
        """
        dependencies:
          - utility-dependency
        optional_dependencies:
          extra2:
            - utility-extra2-dependency
            - ../base_package
          other-extra:
            - not-included-dependency
        """,
    )

    # Base package has standard dependencies
    (base_dir / "requirements.yaml").write_text(
        """
        dependencies:
          - base-dependency
        """,
    )

    # Parse dependencies with verbose output to capture logs
    local_dependencies = parse_local_dependencies(
        main_dir / "requirements.yaml",
        verbose=True,
    )

    # Capture and print the output to help with debugging
    output = capsys.readouterr().out
    print(output)

    # Check that all packages are correctly included in dependencies
    assert local_dependencies == {
        main_dir.absolute(): sorted(
            [
                lib_dir.absolute(),
                utility_dir.absolute(),
                base_dir.absolute(),
            ],
        ),
    }

    # Verify that extras were processed correctly through verbose output
    assert "Processing `../lib_package[extra1,another-extra]`" in output

    yaml = YAML(typ="safe")
    extras = ["extra1"]

    # Test the function directly to verify non-empty nested extras
    deps_from_extras = _get_local_deps_from_optional_section(
        req_path=lib_dir / "requirements.yaml",
        extras_list=extras,
        yaml=yaml,
        verbose=True,
    )

    # We expect a tuple with utility_package path and ["extra2"] as nested extras
    assert len(deps_from_extras) == 1
    path, extra, nested_extras = deps_from_extras[0]
    assert extra == "../utility_package[extra2]"
    assert path.name == "utility_package"
    assert nested_extras == ["extra2"]

    # Also test with "*" to ensure it handles all extras
    all_extras_deps = _get_local_deps_from_optional_section(
        req_path=lib_dir / "requirements.yaml",
        extras_list=["*"],
        yaml=yaml,
        verbose=True,
    )

    # Should include dependencies from both extra1 and another-extra
    assert len(all_extras_deps) == 1  # Only one is a path
    assert all_extras_deps[0][0].name == "utility_package"


def test_wildcard_extras_processing(tmp_path: Path) -> None:
    """Test handling of wildcard extras."""
    package_dir = tmp_path / "package"
    package_dir.mkdir()

    # Create a requirements file with multiple extras
    (package_dir / "requirements.yaml").write_text(
        """
        dependencies:
          - main-dep
        optional_dependencies:
          extra1:
            - ../dep1
          extra2:
            - ../dep2
          extra3:
            - not-a-path
        """,
    )

    yaml = YAML(typ="safe")

    # Test with wildcard
    wildcard_deps = _get_local_deps_from_optional_section(
        req_path=package_dir / "requirements.yaml",
        extras_list=["*"],
        yaml=yaml,
        verbose=True,
    )

    # Should find both path dependencies from all extras
    assert len(wildcard_deps) == 2
    paths = {dep[0].name for dep in wildcard_deps}
    assert paths == {"dep1", "dep2"}

    # Test with specific extras
    specific_deps = _get_local_deps_from_optional_section(
        req_path=package_dir / "requirements.yaml",
        extras_list=["extra1"],
        yaml=yaml,
        verbose=True,
    )

    # Should only find the dependency from extra1
    assert len(specific_deps) == 1
    assert specific_deps[0][0].name == "dep1"
