"""Test PyPI alternatives for local dependencies."""

from __future__ import annotations

import sys
import textwrap
from typing import TYPE_CHECKING

import pytest
from ruamel.yaml import YAML, YAMLError

from unidep import parse_local_dependencies, parse_requirements
from unidep._dependencies_parsing import (
    LocalDependency,
    _parse_local_dependency_item,
    get_local_dependencies,
    yaml_to_toml,
)
from unidep._setuptools_integration import get_python_dependencies

from .helpers import maybe_as_toml

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

if TYPE_CHECKING:
    from pathlib import Path

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:  # pragma: no cover
        from typing_extensions import Literal


def test_parse_local_dependency_item_string() -> None:
    """Test parsing string format local dependency."""
    item = "../foo"
    result = _parse_local_dependency_item(item)
    assert result == LocalDependency(local="../foo", pypi=None)


def test_parse_local_dependency_item_dict() -> None:
    """Test parsing dict format local dependency."""
    item = {"local": "../foo", "pypi": "company-foo"}
    result = _parse_local_dependency_item(item)
    assert result == LocalDependency(local="../foo", pypi="company-foo")


def test_parse_local_dependency_item_dict_with_use() -> None:
    """Test parsing dict format with explicit `use`."""
    item = {"local": "../foo", "pypi": "company-foo", "use": "pypi"}
    result = _parse_local_dependency_item(item)
    assert result == LocalDependency(
        local="../foo",
        pypi="company-foo",
        use="pypi",
    )


def test_parse_local_dependency_item_dict_no_pypi() -> None:
    """Test parsing dict format without pypi key."""
    item = {"local": "../foo"}
    result = _parse_local_dependency_item(item)
    assert result == LocalDependency(local="../foo", pypi=None)


def test_parse_local_dependency_item_invalid_dict() -> None:
    """Test parsing dict without local key raises error."""
    item = {"pypi": "company-foo"}
    with pytest.raises(
        ValueError,
        match="Dictionary-style local dependency must have a 'local' key",
    ):
        _parse_local_dependency_item(item)


def test_parse_local_dependency_item_invalid_type() -> None:
    """Test parsing invalid type raises error."""
    item = 123
    with pytest.raises(TypeError, match="Invalid local dependency format"):
        _parse_local_dependency_item(item)  # type: ignore[arg-type]


def test_parse_local_dependency_item_invalid_use() -> None:
    """Invalid `use` value raises an error."""
    item = {"local": "../foo", "use": "invalid"}
    with pytest.raises(ValueError, match="Invalid `use` value"):
        _parse_local_dependency_item(item)


def test_parse_local_dependency_item_use_pypi_requires_pypi() -> None:
    """`use: pypi` must provide a PyPI alternative."""
    item = {"local": "../foo", "use": "pypi"}
    with pytest.raises(ValueError, match="must specify a `pypi` alternative"):
        _parse_local_dependency_item(item)


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_get_local_dependencies_mixed_format(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    """Test parsing mixed string and dict format local dependencies."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True, parents=True)

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - ../foo
                - local: ../bar
                  pypi: company-bar
                - local: ../baz
                  pypi: company-baz
                - ../qux
            """,
        ),
    )
    req_file = maybe_as_toml(toml_or_yaml, req_file)

    # Load the file to get the data dict

    yaml = YAML(typ="rt")
    with req_file.open() as f:
        if req_file.suffix == ".toml":
            with req_file.open("rb") as fb:
                pyproject = tomllib.load(fb)
                data = pyproject["tool"]["unidep"]
        else:
            data = yaml.load(f)

    local_deps = get_local_dependencies(data)

    assert len(local_deps) == 4
    assert local_deps[0] == LocalDependency(local="../foo", pypi=None)
    assert local_deps[1] == LocalDependency(local="../bar", pypi="company-bar")
    assert local_deps[2] == LocalDependency(local="../baz", pypi="company-baz")
    assert local_deps[3] == LocalDependency(local="../qux", pypi=None)


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_setuptools_integration_with_pypi_alternatives(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ARG001
) -> None:
    """Test setuptools integration uses local paths when they exist."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True, parents=True)

    # Create local dependency projects
    foo = tmp_path / "foo"
    foo.mkdir(exist_ok=True)
    (foo / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "foo-pkg"
            version = "0.1.0"
            """,
        ),
    )
    # Create a Python module to make it a valid package
    (foo / "foo_pkg").mkdir(exist_ok=True)
    (foo / "foo_pkg" / "__init__.py").write_text("")

    bar = tmp_path / "bar"
    bar.mkdir(exist_ok=True)
    (bar / "setup.py").write_text(
        textwrap.dedent(
            """\
            from setuptools import setup
            setup(name="bar-pkg", version="0.1.0")
            """,
        ),
    )
    # Create a Python module to make it a valid package
    (bar / "bar_pkg").mkdir(exist_ok=True)
    (bar / "bar_pkg" / "__init__.py").write_text("")

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - ../foo
                - local: ../bar
                  pypi: company-bar
            """,
        ),
    )
    req_file = maybe_as_toml(toml_or_yaml, req_file)

    # Test with local paths existing (development mode) - should use file:// URLs
    deps = get_python_dependencies(
        req_file,
        include_local_dependencies=True,
    )

    assert "numpy" in deps.dependencies
    # Both should use file:// URLs since local paths exist
    assert any("foo-pkg @ file://" in dep for dep in deps.dependencies)
    assert any("bar-pkg @ file://" in dep for dep in deps.dependencies)
    # Should NOT use PyPI alternative when local exists
    assert not any("company-bar" in dep for dep in deps.dependencies)


def test_local_dependency_use_pypi_injects_dependency(tmp_path: Path) -> None:
    """`use: pypi` should add the PyPI requirement as a normal dependency."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """
            dependencies: []
            local_dependencies:
              - local: ./dep
                pypi: company-dep>=1.0
                use: pypi
            """,
        ),
    )
    (tmp_path / "project" / "dep").mkdir()

    reqs = parse_requirements(project / "requirements.yaml")
    assert "company-dep" in reqs.requirements
    specs = reqs.requirements["company-dep"]
    assert specs[0].which == "pip"


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_standard_string_format(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    """Test that standard string format for local dependencies works."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True, parents=True)

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - ../foo
                - ../bar
                - ../baz
            """,
        ),
    )
    req_file = maybe_as_toml(toml_or_yaml, req_file)

    # This should work without errors
    requirements = parse_requirements(req_file)
    assert "numpy" in requirements.requirements


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_yaml_to_toml_with_pypi_alternatives(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    """Test that yaml_to_toml preserves PyPI alternatives."""
    if toml_or_yaml == "toml":
        # Skip for TOML as yaml_to_toml only works on YAML files
        return

    project = tmp_path / "project"
    project.mkdir(exist_ok=True, parents=True)

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            name: test-project
            dependencies:
                - numpy
            local_dependencies:
                - ../foo
                - local: ../bar
                  pypi: company-bar
            """,
        ),
    )

    # Convert to TOML
    toml_content = yaml_to_toml(req_file)

    # Check that the structure is preserved
    assert "[tool.unidep]" in toml_content
    assert '"../foo"' in toml_content
    assert '{ local = "../bar", pypi = "company-bar" }' in toml_content


def test_edge_cases(tmp_path: Path) -> None:  # noqa: ARG001
    """Test edge cases and error conditions."""
    # Test empty dict
    with pytest.raises(
        ValueError,
        match="Dictionary-style local dependency must have a 'local' key",
    ):
        _parse_local_dependency_item({})

    # Test dict with only pypi key
    with pytest.raises(
        ValueError,
        match="Dictionary-style local dependency must have a 'local' key",
    ):
        _parse_local_dependency_item({"pypi": "some-package"})

    # Test None value
    with pytest.raises(TypeError, match="Invalid local dependency format"):
        _parse_local_dependency_item(None)  # type: ignore[arg-type]

    # Test list value
    with pytest.raises(TypeError, match="Invalid local dependency format"):
        _parse_local_dependency_item(["foo", "bar"])  # type: ignore[arg-type]


def test_local_dependency_with_extras(tmp_path: Path) -> None:
    """Test that local dependencies with extras work with PyPI alternatives."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True, parents=True)

    # Create a local dependency with optional dependencies
    dep = tmp_path / "dep"
    dep.mkdir(exist_ok=True)
    (dep / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "my-dep"
            version = "0.1.0"

            [tool.unidep]
            dependencies = ["requests"]
            optional_dependencies = {test = ["pytest"]}
            """,
        ),
    )

    # Main project references the local dependency with extras
    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../dep[test]
                  pypi: company-dep[test]
            """,
        ),
    )

    # Parse to ensure no errors
    requirements = parse_requirements(req_file)
    assert "numpy" in requirements.requirements


def test_recursive_local_dependencies_with_pypi_alternatives(tmp_path: Path) -> None:
    """Test that PyPI alternatives work with nested local dependencies."""
    # Create project structure: main -> dep1 -> dep2
    main = tmp_path / "main"
    main.mkdir(exist_ok=True)

    dep1 = tmp_path / "dep1"
    dep1.mkdir(exist_ok=True)

    dep2 = tmp_path / "dep2"
    dep2.mkdir(exist_ok=True)

    # dep2 has no dependencies
    (dep2 / "requirements.yaml").write_text("dependencies: [pandas]")

    # dep1 depends on dep2 with PyPI alternative
    (dep1 / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../dep2
                  pypi: company-dep2
            """,
        ),
    )

    # main depends on dep1 with PyPI alternative
    (main / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - scipy
            local_dependencies:
                - local: ../dep1
                  pypi: company-dep1
            """,
        ),
    )

    # Parse and check
    requirements = parse_requirements(main / "requirements.yaml")
    assert "scipy" in requirements.requirements
    assert "numpy" in requirements.requirements  # From dep1
    assert "pandas" in requirements.requirements  # From dep2


def test_empty_local_dependencies_list(tmp_path: Path) -> None:
    """Test handling of empty local_dependencies list."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies: []
            """,
        ),
    )

    # Test setuptools integration
    deps = get_python_dependencies(
        req_file,
        include_local_dependencies=True,
    )
    assert "numpy" in deps.dependencies
    assert len([d for d in deps.dependencies if "file://" in d]) == 0


def test_local_dependencies_with_extras(tmp_path: Path) -> None:
    """Test local dependencies with extras notation work with PyPI alternatives."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    # Create a local dependency with optional dependencies
    dep = tmp_path / "dep"
    dep.mkdir(exist_ok=True)
    (dep / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools", "unidep"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "my-dep"
            version = "0.1.0"
            dynamic = ["dependencies"]

            [tool.unidep]
            dependencies = ["requests"]
            optional_dependencies = {test = ["pytest"], dev = ["black"]}
            """,
        ),
    )
    # Make it a valid package
    (dep / "my_dep").mkdir(exist_ok=True)
    (dep / "my_dep" / "__init__.py").write_text("")

    # Main project references the local dependency with extras
    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../dep[test,dev]
                  pypi: company-dep[test,dev]
            """,
        ),
    )

    # Test setuptools integration
    deps = get_python_dependencies(
        req_file,
        include_local_dependencies=True,
    )
    assert "numpy" in deps.dependencies
    # Should use file:// URL since local path exists
    assert any("my-dep[test,dev] @ file://" in dep for dep in deps.dependencies)
    assert not any("company-dep" in dep for dep in deps.dependencies)


def test_complex_path_structures(tmp_path: Path) -> None:
    """Test complex path structures including nested dirs and parent refs."""
    # Create complex directory structure
    root = tmp_path / "workspace"
    root.mkdir(exist_ok=True)

    project = root / "apps" / "main"
    project.mkdir(exist_ok=True, parents=True)

    shared = root / "libs" / "shared"
    shared.mkdir(exist_ok=True, parents=True)

    utils = root / "libs" / "utils"
    utils.mkdir(exist_ok=True, parents=True)

    # Create valid packages
    for pkg_dir, name in [(shared, "shared"), (utils, "utils")]:
        (pkg_dir / "setup.py").write_text(
            f'from setuptools import setup; setup(name="{name}", version="1.0")',
        )
        (pkg_dir / name).mkdir(exist_ok=True)
        (pkg_dir / name / "__init__.py").write_text("")

    # Project with complex relative paths
    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pandas
            local_dependencies:
                - local: ../../libs/shared
                  pypi: company-shared>=1.0
                - local: ../../libs/utils
                  pypi: company-utils~=2.0
            """,
        ),
    )

    # Test setuptools integration
    deps = get_python_dependencies(
        req_file,
        include_local_dependencies=True,
    )
    assert "pandas" in deps.dependencies
    # Should use file:// URLs since local paths exist
    assert any("shared @ file://" in dep for dep in deps.dependencies)
    assert any("utils @ file://" in dep for dep in deps.dependencies)
    assert not any("company-shared" in dep for dep in deps.dependencies)
    assert not any("company-utils" in dep for dep in deps.dependencies)


def test_invalid_yaml_handling(tmp_path: Path) -> None:
    """Test handling of invalid YAML in requirements file."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    req_file = project / "requirements.yaml"
    req_file.write_text(
        """\
dependencies:
  - numpy
local_dependencies:
  - local: ../foo
    pypi: company-foo
  this is invalid yaml
    - more invalid
        """,
    )

    # Should raise an error when parsing

    with pytest.raises((YAMLError, ValueError)):
        parse_requirements(req_file)


def test_pypi_alternatives_with_absolute_paths(tmp_path: Path) -> None:
    """Test that absolute paths in local dependencies are handled correctly."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    # Create a dependency with absolute path
    dep = tmp_path / "absolute_dep"
    dep.mkdir(exist_ok=True)
    (dep / "setup.py").write_text(
        'from setuptools import setup; setup(name="abs-dep", version="1.0")',
    )
    (dep / "abs_dep").mkdir(exist_ok=True)
    (dep / "abs_dep" / "__init__.py").write_text("")

    req_file = project / "requirements.yaml"
    # Note: Using absolute path to trigger the assertion
    abs_path = str(dep.resolve())
    req_file.write_text(
        textwrap.dedent(
            f"""\
            dependencies:
                - numpy
            local_dependencies:
                - local: {abs_path}
                  pypi: company-abs-dep
            """,
        ),
    )

    # This should fail because absolute paths are not allowed

    with pytest.raises(AssertionError):
        parse_local_dependencies(req_file)


def test_pypi_alternatives_when_local_missing(tmp_path: Path) -> None:
    """Test that PyPI alternatives are used when local paths don't exist."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - ../missing1
                - local: ../missing2
                  pypi: company-missing2
            """,
        ),
    )

    # Test with missing local paths - should use PyPI alternatives when available
    deps = get_python_dependencies(
        req_file,
        include_local_dependencies=True,
    )

    assert "numpy" in deps.dependencies
    # missing1 has no PyPI alternative and doesn't exist - should be skipped
    assert not any("missing1" in dep for dep in deps.dependencies)
    # missing2 should use PyPI alternative since local doesn't exist
    assert any("company-missing2" in dep for dep in deps.dependencies)
    # Should NOT have file:// URLs for missing paths
    assert not any("file://" in dep for dep in deps.dependencies)


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_mixed_string_and_dict_in_toml(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    """Test that mixed string and dict formats work in TOML."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    # Create dependencies
    for name in ["dep1", "dep2", "dep3"]:
        dep = tmp_path / name
        dep.mkdir(exist_ok=True)
        (dep / "setup.py").write_text(
            f'from setuptools import setup; setup(name="{name}", version="1.0")',
        )
        (dep / name).mkdir(exist_ok=True)
        (dep / name / "__init__.py").write_text("")

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - ../dep1
                - local: ../dep2
                  pypi: company-dep2
                - local: ../dep3
            """,
        ),
    )
    req_file = maybe_as_toml(toml_or_yaml, req_file)

    # Test parsing
    requirements = parse_requirements(req_file)
    assert "numpy" in requirements.requirements


def test_wheel_file_with_pypi_alternatives(tmp_path: Path) -> None:
    """Test handling of .whl files with PyPI alternatives."""
    project = tmp_path / "project"
    project.mkdir()

    # Test 1: Wheel exists - should use it
    wheel_path = tmp_path / "dep.whl"
    wheel_path.touch()  # Create dummy wheel file

    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            f"""\
            dependencies:
                - numpy
            local_dependencies:
                - local: {wheel_path}
                  pypi: company-dep>=1.0
            """,
        ),
    )

    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=True,
    )

    assert "numpy" in deps.dependencies
    assert any("dep.whl @ file://" in dep for dep in deps.dependencies)
    assert not any("company-dep" in dep for dep in deps.dependencies)

    # Test 2: Wheel doesn't exist - should use PyPI alternative
    wheel_path.unlink()  # Remove wheel file

    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=True,
    )

    assert "numpy" in deps.dependencies
    assert "company-dep>=1.0" in deps.dependencies
    assert not any("file://" in dep for dep in deps.dependencies)

    # Test 3: Wheel with UNIDEP_SKIP_LOCAL_DEPS - should use PyPI
    wheel_path.touch()  # Recreate wheel

    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=False,  # UNIDEP_SKIP_LOCAL_DEPS=1
    )

    assert "numpy" in deps.dependencies
    assert "company-dep>=1.0" in deps.dependencies
    assert not any("file://" in dep for dep in deps.dependencies)


def test_skip_local_deps_with_pypi_alternatives(tmp_path: Path) -> None:
    """Test that UNIDEP_SKIP_LOCAL_DEPS uses PyPI alternatives when available."""
    project = tmp_path / "project"
    project.mkdir()

    # Create local dependencies
    dep1 = tmp_path / "dep1"
    dep1.mkdir()
    (dep1 / "setup.py").write_text(
        'from setuptools import setup; setup(name="dep1-local", version="0.1.0")',
    )

    dep2 = tmp_path / "dep2"
    dep2.mkdir()
    (dep2 / "setup.py").write_text(
        'from setuptools import setup; setup(name="dep2-local", version="0.1.0")',
    )

    # Create project with mixed local dependencies
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - ../dep1  # String format - no PyPI alternative
                - local: ../dep2
                  pypi: company-dep2>=1.0  # Has PyPI alternative
            """,
        ),
    )

    # Test with include_local_dependencies=False (UNIDEP_SKIP_LOCAL_DEPS=1)
    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=False,
    )

    # Check results
    assert "numpy" in deps.dependencies
    # dep1 should be completely skipped (no PyPI alternative)
    assert not any("dep1" in dep for dep in deps.dependencies)
    # dep2 should use PyPI alternative
    assert "company-dep2>=1.0" in deps.dependencies
    # No file:// URLs should be present
    assert not any("file://" in dep for dep in deps.dependencies)


def test_regular_local_deps_with_existing_paths(tmp_path: Path) -> None:
    """Test regular (non-wheel) local dependencies that exist and are pip-installable."""
    project = tmp_path / "project"
    project.mkdir()

    # Create local dependency with different package structures
    # Test 1: pyproject.toml
    dep1 = tmp_path / "dep1"
    dep1.mkdir()
    (dep1 / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "my-dep1"
            version = "0.1.0"
            """,
        ),
    )
    (dep1 / "my_dep1").mkdir()
    (dep1 / "my_dep1" / "__init__.py").write_text("")

    # Test 2: setup.cfg
    dep2 = tmp_path / "dep2"
    dep2.mkdir()
    (dep2 / "setup.cfg").write_text(
        textwrap.dedent(
            """\
            [metadata]
            name = my-dep2
            version = 0.1.0
            """,
        ),
    )
    (dep2 / "setup.py").write_text("from setuptools import setup; setup()")
    (dep2 / "my_dep2").mkdir()
    (dep2 / "my_dep2" / "__init__.py").write_text("")

    # Test 3: setup.py
    dep3 = tmp_path / "dep3"
    dep3.mkdir()
    (dep3 / "setup.py").write_text(
        'from setuptools import setup; setup(name="my-dep3", version="0.1.0")',
    )
    (dep3 / "my_dep3").mkdir()
    (dep3 / "my_dep3" / "__init__.py").write_text("")

    # Project with PyPI alternatives
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../dep1
                  pypi: company-dep1>=1.0
                - local: ../dep2
                  pypi: company-dep2>=2.0
                - local: ../dep3
                  pypi: company-dep3>=3.0
            """,
        ),
    )

    # Test with local paths existing
    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=True,
    )

    # All should use file:// URLs since local paths exist
    assert "numpy" in deps.dependencies
    assert any("my-dep1 @ file://" in dep for dep in deps.dependencies)
    assert any("my-dep2 @ file://" in dep for dep in deps.dependencies)
    assert any("my-dep3 @ file://" in dep for dep in deps.dependencies)
    # Should NOT use PyPI alternatives
    assert not any("company-dep" in dep for dep in deps.dependencies)


def test_local_deps_with_extras_and_pypi_alternatives(tmp_path: Path) -> None:
    """Test local dependencies with extras notation and PyPI alternatives."""
    project = tmp_path / "project"
    project.mkdir()

    # Create dependency with extras
    dep = tmp_path / "dep"
    dep.mkdir()
    (dep / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "my-dep-extras"
            version = "0.1.0"
            dependencies = ["requests"]

            [project.optional-dependencies]
            test = ["pytest"]
            dev = ["black", "ruff"]
            """,
        ),
    )
    (dep / "my_dep_extras").mkdir()
    (dep / "my_dep_extras" / "__init__.py").write_text("")

    # Test various extras notations
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../dep[test]
                  pypi: company-dep[test]>=1.0
                - local: ../dep[dev]
                  pypi: company-dep[dev]>=1.0
                - local: ../dep[test,dev]
                  pypi: company-dep[test,dev]>=1.0
            """,
        ),
    )

    # Test with local paths existing
    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=True,
    )

    # Should use file:// URLs with extras preserved
    assert "numpy" in deps.dependencies
    assert any("my-dep-extras[test] @ file://" in dep for dep in deps.dependencies)
    assert any("my-dep-extras[dev] @ file://" in dep for dep in deps.dependencies)
    assert any("my-dep-extras[test,dev] @ file://" in dep for dep in deps.dependencies)
    # Should NOT use PyPI alternatives
    assert not any("company-dep" in dep for dep in deps.dependencies)


def test_local_deps_missing_with_pypi_fallback(tmp_path: Path) -> None:
    """Test regular local dependencies that don't exist fall back to PyPI alternatives."""
    project = tmp_path / "project"
    project.mkdir()

    # Create project with non-existent local dependencies
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../missing-dep1
                  pypi: company-dep1>=1.0
                - local: ../missing-dep2[extras]
                  pypi: company-dep2[extras]>=2.0
                - ../missing-dep3  # No PyPI alternative
            """,
        ),
    )

    # Test with missing local paths
    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=True,
    )

    # Should use PyPI alternatives when available
    assert "numpy" in deps.dependencies
    assert "company-dep1>=1.0" in deps.dependencies
    assert "company-dep2[extras]>=2.0" in deps.dependencies
    # missing-dep3 should be skipped (no PyPI alternative)
    assert not any("missing-dep3" in dep for dep in deps.dependencies)
    # No file:// URLs since paths don't exist
    assert not any("file://" in dep for dep in deps.dependencies)


def test_missing_requirements_file_handling(tmp_path: Path) -> None:
    """Test handling when requirements.yaml doesn't exist."""
    # Test 1: raises_if_missing=True (default) - should raise
    with pytest.raises(FileNotFoundError):
        get_python_dependencies(
            tmp_path / "non_existent.yaml",
            raises_if_missing=True,
        )

    # Test 2: raises_if_missing=False - should return empty
    deps = get_python_dependencies(
        tmp_path / "non_existent.yaml",
        raises_if_missing=False,
    )
    assert deps.dependencies == []
    assert deps.extras == {}


def test_package_name_extraction_edge_cases(tmp_path: Path) -> None:
    """Test edge cases for package name extraction from various file formats."""
    project = tmp_path / "project"
    project.mkdir()

    # Test 1: setup.cfg without name
    dep1 = tmp_path / "dep1"
    dep1.mkdir()
    (dep1 / "setup.cfg").write_text(
        textwrap.dedent(
            """\
            [metadata]
            version = 0.1.0
            # Missing name field
            """,
        ),
    )
    (dep1 / "setup.py").write_text("from setuptools import setup; setup()")
    (dep1 / "dep1").mkdir()
    (dep1 / "dep1" / "__init__.py").write_text("")

    # Test 2: setup.py without name
    dep2 = tmp_path / "dep2"
    dep2.mkdir()
    (dep2 / "setup.py").write_text(
        textwrap.dedent(
            """\
            from setuptools import setup
            setup(version="0.1.0")  # Missing name
            """,
        ),
    )
    (dep2 / "dep2").mkdir()
    (dep2 / "dep2" / "__init__.py").write_text("")

    # Test 3: pyproject.toml with Poetry format
    dep3 = tmp_path / "dep3"
    dep3.mkdir()
    (dep3 / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["poetry-core"]
            build-backend = "poetry.core.masonry.api"

            [tool.poetry]
            name = "poetry-dep"
            version = "0.1.0"
            """,
        ),
    )
    (dep3 / "poetry_dep").mkdir()
    (dep3 / "poetry_dep" / "__init__.py").write_text("")

    # Test 4: pyproject.toml without name anywhere
    dep4 = tmp_path / "dep4"
    dep4.mkdir()
    (dep4 / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["setuptools"]
            build-backend = "setuptools.build_meta"

            # No project section, no name anywhere
            [tool.setuptools]
            packages = ["dep4"]
            """,
        ),
    )
    (dep4 / "dep4").mkdir()
    (dep4 / "dep4" / "__init__.py").write_text("")

    # Test 5: Minimal setup.py - fallback to folder name
    dep5 = tmp_path / "folder-name-dep"
    dep5.mkdir()
    # Minimal setup.py to make it pip-installable
    (dep5 / "setup.py").write_text("from setuptools import setup; setup()")
    (dep5 / "folder_name_dep").mkdir()
    (dep5 / "folder_name_dep" / "__init__.py").write_text("")

    # Create project referencing these deps
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - local: ../dep1
                  pypi: company-dep1
                - local: ../dep2
                  pypi: company-dep2
                - local: ../dep3
                  pypi: company-dep3
                - local: ../dep4
                  pypi: company-dep4
                - local: ../folder-name-dep
                  pypi: company-dep5
            """,
        ),
    )

    # Test with local paths existing
    deps = get_python_dependencies(
        project / "requirements.yaml",
        include_local_dependencies=True,
    )

    # Check that all dependencies were processed
    assert "numpy" in deps.dependencies
    # dep1: falls back to folder name "dep1"
    assert any("dep1 @ file://" in dep for dep in deps.dependencies)
    # dep2: falls back to folder name "dep2"
    assert any("dep2 @ file://" in dep for dep in deps.dependencies)
    # dep3: uses poetry name "poetry-dep"
    assert any("poetry-dep @ file://" in dep for dep in deps.dependencies)
    # dep4: falls back to folder name "dep4"
    assert any("dep4 @ file://" in dep for dep in deps.dependencies)
    # dep5: uses folder name "folder-name-dep"
    assert any("folder-name-dep @ file://" in dep for dep in deps.dependencies)
