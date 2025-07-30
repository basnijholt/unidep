"""Test PyPI alternatives for local dependencies."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from unidep import parse_requirements
from unidep._dependencies_parsing import (
    LocalDependency,
    _get_local_dependencies,
    _parse_local_dependency_item,
    get_pypi_alternatives,
)
from unidep._setuptools_integration import get_python_dependencies

from .helpers import maybe_as_toml

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

if TYPE_CHECKING:
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
    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")
    with req_file.open() as f:
        if req_file.suffix == ".toml":
            with req_file.open("rb") as fb:
                pyproject = tomllib.load(fb)
                data = pyproject["tool"]["unidep"]
        else:
            data = yaml.load(f)

    local_deps = _get_local_dependencies(data)

    assert len(local_deps) == 4
    assert local_deps[0] == LocalDependency(local="../foo", pypi=None)
    assert local_deps[1] == LocalDependency(local="../bar", pypi="company-bar")
    assert local_deps[2] == LocalDependency(local="../baz", pypi="company-baz")
    assert local_deps[3] == LocalDependency(local="../qux", pypi=None)


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_get_pypi_alternatives(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    """Test extracting PyPI alternatives mapping."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True, parents=True)

    # Create dummy directories for local dependencies
    (tmp_path / "foo").mkdir(exist_ok=True)
    (tmp_path / "bar").mkdir(exist_ok=True)
    (tmp_path / "baz").mkdir(exist_ok=True)

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
            """,
        ),
    )
    req_file = maybe_as_toml(toml_or_yaml, req_file)

    # Load the file to get the data dict
    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")
    with req_file.open() as f:
        if req_file.suffix == ".toml":
            with req_file.open("rb") as fb:
                pyproject = tomllib.load(fb)
                data = pyproject["tool"]["unidep"]
        else:
            data = yaml.load(f)

    pypi_alts = get_pypi_alternatives(data, project)

    # Convert to relative paths for easier testing
    pypi_alts_rel = {
        str(Path(k).relative_to(tmp_path)): v for k, v in pypi_alts.items()
    }

    assert len(pypi_alts_rel) == 2
    assert pypi_alts_rel["bar"] == "company-bar"
    assert pypi_alts_rel["baz"] == "company-baz"
    assert "foo" not in pypi_alts_rel  # No PyPI alternative


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


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_backwards_compatibility(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    """Test that pure string format still works."""
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

    from unidep._dependencies_parsing import yaml_to_toml

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
    from unidep._dependencies_parsing import _parse_local_dependency_item

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

    # Test get_pypi_alternatives with empty list
    from ruamel.yaml import YAML

    from unidep._dependencies_parsing import _load, get_pypi_alternatives

    yaml = YAML(typ="rt")
    data = _load(req_file, yaml)
    pypi_alts = get_pypi_alternatives(data, project)
    assert pypi_alts == {}

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

    # Test get_pypi_alternatives - should preserve extras
    from ruamel.yaml import YAML

    from unidep._dependencies_parsing import _load, get_pypi_alternatives

    yaml = YAML(typ="rt")
    data = _load(req_file, yaml)
    pypi_alts = get_pypi_alternatives(data, project)

    # The key should be the resolved path without extras
    dep_path = str((tmp_path / "dep").resolve())
    assert dep_path in pypi_alts
    assert pypi_alts[dep_path] == "company-dep[test,dev]"

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

    # Test path resolution
    from ruamel.yaml import YAML

    from unidep._dependencies_parsing import _load, get_pypi_alternatives

    yaml = YAML(typ="rt")
    data = _load(req_file, yaml)
    pypi_alts = get_pypi_alternatives(data, project)

    # Check paths are correctly resolved
    shared_path = str(shared.resolve())
    utils_path = str(utils.resolve())
    assert shared_path in pypi_alts
    assert utils_path in pypi_alts
    assert pypi_alts[shared_path] == "company-shared>=1.0"
    assert pypi_alts[utils_path] == "company-utils~=2.0"

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
    from ruamel.yaml import YAMLError

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
    from unidep import parse_local_dependencies

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

    # Test get_pypi_alternatives
    from ruamel.yaml import YAML

    from unidep._dependencies_parsing import get_pypi_alternatives

    yaml = YAML(typ="rt")
    with req_file.open() as f:
        if req_file.suffix == ".toml":
            with req_file.open("rb") as fb:
                pyproject = tomllib.load(fb)
                data = pyproject["tool"]["unidep"]
        else:
            data = yaml.load(f)

    pypi_alts = get_pypi_alternatives(data, project)

    # Only dep2 should have PyPI alternative
    dep2_path = str((tmp_path / "dep2").resolve())
    assert dep2_path in pypi_alts
    assert pypi_alts[dep2_path] == "company-dep2"
    assert len(pypi_alts) == 1
