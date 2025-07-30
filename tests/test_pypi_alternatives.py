"""Test PyPI alternatives for local dependencies."""

from __future__ import annotations

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

if TYPE_CHECKING:
    import sys

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
            import tomllib

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
            import tomllib

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
    """Test setuptools integration uses PyPI alternatives when available."""
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

    # Test without UNIDEP_SKIP_LOCAL_DEPS (should use file:// URLs but with PyPI for bar)
    deps = get_python_dependencies(
        req_file,
        include_local_dependencies=True,
    )

    assert "numpy" in deps.dependencies
    # Check that bar uses PyPI alternative
    assert any("company-bar" in dep for dep in deps.dependencies)
    # Check that foo uses file:// URL
    assert any("foo-pkg @ file://" in dep for dep in deps.dependencies)


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


def test_collect_pypi_alternatives_function(tmp_path: Path) -> None:
    """Test the _collect_pypi_alternatives function."""
    from unidep._setuptools_integration import _collect_pypi_alternatives

    project = tmp_path / "project"
    project.mkdir(exist_ok=True, parents=True)

    # Create local deps
    (tmp_path / "foo").mkdir(exist_ok=True)
    (tmp_path / "bar").mkdir(exist_ok=True)

    req_file = project / "requirements.yaml"
    req_file.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numpy
            local_dependencies:
                - ../foo
                - local: ../bar
                  pypi: company-bar==1.0.0
            """,
        ),
    )

    pypi_alts = _collect_pypi_alternatives(req_file)

    # Check the mapping
    bar_path = str((tmp_path / "bar").resolve())
    assert bar_path in pypi_alts
    assert pypi_alts[bar_path] == "company-bar==1.0.0"

    # foo should not be in the mapping
    foo_path = str((tmp_path / "foo").resolve())
    assert foo_path not in pypi_alts


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
