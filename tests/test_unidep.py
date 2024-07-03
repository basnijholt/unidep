"""unidep tests."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from ruamel.yaml import YAML

from unidep import (
    create_conda_env_specification,
    filter_python_dependencies,
    find_requirements_files,
    get_python_dependencies,
    parse_requirements,
    resolve_conflicts,
    write_conda_environment_file,
)
from unidep._conda_env import CondaEnvironmentSpec
from unidep._conflicts import VersionConflictError
from unidep._dependencies_parsing import yaml_to_toml
from unidep.platform_definitions import Platform, Spec
from unidep.utils import is_pip_installable

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


@pytest.fixture(params=["toml", "yaml"])
def setup_test_files(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> tuple[Path, Path]:
    d1 = tmp_path / "dir1"
    d1.mkdir()
    f1 = d1 / "requirements.yaml"
    f1.write_text("dependencies:\n  - numpy\n  - conda: mumps")

    d2 = tmp_path / "dir2"
    d2.mkdir()
    f2 = d2 / "requirements.yaml"
    f2.write_text("dependencies:\n  - pip: pandas")
    f1 = maybe_as_toml(request.param, f1)
    f2 = maybe_as_toml(request.param, f2)
    return (f1, f2)


def test_find_requirements_files(
    tmp_path: Path,
    setup_test_files: tuple[Path, Path],
) -> None:
    # Make sure to pass the depth argument correctly if your function expects it.
    found_files = find_requirements_files(
        tmp_path,
        depth=1,
        verbose=True,
    )

    # Convert found_files to absolute paths for comparison
    absolute_results = sorted(str(p.resolve()) for p in found_files)
    absolute_test_files = sorted(str(p.resolve()) for p in setup_test_files)

    assert absolute_results == absolute_test_files


def test_find_requirements_files_depth(tmp_path: Path) -> None:
    # Create a nested directory structure
    (tmp_path / "dir1").mkdir()
    (tmp_path / "dir1/dir2").mkdir()
    (tmp_path / "dir1/dir2/dir3").mkdir()

    # Create test files
    (tmp_path / "requirements.yaml").touch()
    (tmp_path / "dir1/requirements.yaml").touch()
    (tmp_path / "dir1/dir2/requirements.yaml").touch()
    (tmp_path / "dir1/dir2/dir3/requirements.yaml").touch()

    # Test depth=0
    assert len(find_requirements_files(tmp_path, depth=0)) == 1

    # Test depth=1
    assert len(find_requirements_files(tmp_path, depth=1)) == 2

    # Test depth=2
    assert len(find_requirements_files(tmp_path, depth=2)) == 3

    # Test depth=3
    assert len(find_requirements_files(tmp_path, depth=3)) == 4

    # Test depth=4 (or more)
    assert len(find_requirements_files(tmp_path, depth=4)) == 4


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_parse_requirements(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo >1 # [linux64]
                - foo # [unix]
                - bar >1
                - bar
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    assert requirements.requirements == {
        "foo": [
            Spec(
                name="foo",
                which="conda",
                selector="linux64",
                pin=">1",
                identifier="c292b98a",
            ),
            Spec(
                name="foo",
                which="pip",
                selector="linux64",
                pin=">1",
                identifier="c292b98a",
            ),
            Spec(
                name="foo",
                which="conda",
                selector="unix",
                identifier="530d9eaa",
            ),
            Spec(
                name="foo",
                which="pip",
                selector="unix",
                identifier="530d9eaa",
            ),
        ],
        "bar": [
            Spec(
                name="bar",
                which="conda",
                pin=">1",
                identifier="08fd8713",
            ),
            Spec(
                name="bar",
                which="pip",
                pin=">1",
                identifier="08fd8713",
            ),
            Spec(
                name="bar",
                which="conda",
                identifier="9e467fa1",
            ),
            Spec(
                name="bar",
                which="pip",
                identifier="9e467fa1",
            ),
        ],
    }


@pytest.mark.parametrize("verbose", [True, False])
def test_generate_conda_env_file(
    tmp_path: Path,
    verbose: bool,  # noqa: FBT001
    setup_test_files: tuple[Path, Path],
) -> None:
    output_file = tmp_path / "environment.yaml"
    requirements = parse_requirements(*setup_test_files, verbose=verbose)
    resolved = resolve_conflicts(
        requirements.requirements,
        requirements.platforms,
    )
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )

    write_conda_environment_file(env_spec, str(output_file), verbose=verbose)

    with output_file.open() as f, YAML(typ="safe") as yaml:
        env_data = yaml.load(f)
        assert "dependencies" in env_data
        assert "numpy" in env_data["dependencies"]
        assert {"pip": ["pandas"]} in env_data["dependencies"]


def test_generate_conda_env_stdout(
    setup_test_files: tuple[Path, Path],
    capsys: pytest.CaptureFixture,
) -> None:
    requirements = parse_requirements(*setup_test_files)
    resolved = resolve_conflicts(
        requirements.requirements,
        requirements.platforms,
    )
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )
    write_conda_environment_file(env_spec, output_file=None)
    captured = capsys.readouterr()
    assert "dependencies" in captured.out
    assert "numpy" in captured.out
    assert "- pandas" in captured.out


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_create_conda_env_specification_platforms(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - yolo  # [arm64]
                - foo  # [linux64]
                - conda: bar  # [win]
                - pip: pip-package
                - pip: pip-package2  # [arm64]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p)
    resolved = resolve_conflicts(
        requirements.requirements,
        requirements.platforms,
    )
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )
    assert env_spec.conda == [
        {"sel(osx)": "yolo"},
        {"sel(linux)": "foo"},
        {"sel(win)": "bar"},
    ]
    expected_pip = [
        "pip-package",
        "pip-package2; sys_platform == 'darwin' and platform_machine == 'arm64'",
    ]
    assert env_spec.pip == expected_pip

    # Test on two platforms
    platforms: list[Platform] = ["osx-arm64", "win-64"]
    resolved = resolve_conflicts(
        requirements.requirements,
        platforms,
    )

    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        platforms,
    )
    assert env_spec.conda == [{"sel(osx)": "yolo"}, {"sel(win)": "bar"}]
    assert env_spec.pip == expected_pip

    # Test with comment selector
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        platforms,
        selector="comment",
    )
    assert env_spec.conda == ["yolo", "bar"]
    assert env_spec.pip == ["pip-package", "pip-package2"]
    write_conda_environment_file(env_spec, str(tmp_path / "environment.yaml"))
    with (tmp_path / "environment.yaml").open() as f:
        text = "".join(f.readlines())
        assert "- yolo  # [arm64]" in text
        assert "- bar # [win64]" in text

    with pytest.raises(ValueError, match="Invalid platform"):
        resolve_conflicts(
            requirements.requirements,
            ["unknown-platform"],  # type: ignore[list-item]
        )


def test_verbose_output(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    f = tmp_path / "dir3" / "requirements.yaml"
    f.parent.mkdir()
    f.write_text("dependencies:\n  - scipy")

    find_requirements_files(tmp_path, verbose=True)
    captured = capsys.readouterr()
    assert "Scanning in" in captured.out
    assert str(tmp_path / "dir3") in captured.out

    parse_requirements(f, verbose=True)
    captured = capsys.readouterr()
    assert "Parsing" in captured.out
    assert str(f) in captured.out

    write_conda_environment_file(
        CondaEnvironmentSpec(channels=[], platforms=[], conda=[], pip=[]),
        verbose=True,
    )
    captured = capsys.readouterr()
    assert "Generating environment file at" in captured.out
    assert "Environment file generated successfully." in captured.out


def test_extract_python_requires(setup_test_files: tuple[Path, Path]) -> None:
    f1, f2 = setup_test_files
    requires1 = get_python_dependencies(str(f1))
    assert requires1.dependencies == ["numpy"]
    requires2 = get_python_dependencies(str(f2))
    assert requires2.dependencies == ["pandas"]

    # Test with a file that doesn't exist
    with pytest.raises(FileNotFoundError):
        get_python_dependencies("nonexistent_file.yaml", raises_if_missing=True)
    assert (
        get_python_dependencies(
            "nonexistent_file.yaml",
            raises_if_missing=False,
        ).dependencies
        == []
    )


def test_pip_install_local_dependencies(tmp_path: Path) -> None:
    p = tmp_path / "pkg" / "requirements.yaml"
    p.parent.mkdir(exist_ok=True)
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo
            local_dependencies:
                - ../local_package
            """,
        ),
    )
    deps = get_python_dependencies(p, raises_if_missing=False)
    assert deps.dependencies == ["foo"]

    deps = get_python_dependencies(p, include_local_dependencies=True)
    assert deps.dependencies == ["foo"]  # because the local package doesn't exist

    local_package = tmp_path / "local_package"
    local_package.mkdir(exist_ok=True, parents=True)
    assert not is_pip_installable(local_package)
    (local_package / "setup.py").touch()
    assert is_pip_installable(local_package)
    deps = get_python_dependencies(p, include_local_dependencies=True)
    assert deps.dependencies == [
        "foo",
        f"local_package @ file://{local_package.as_posix()}",
    ]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_channels(toml_or_yaml: Literal["toml", "yaml"], tmp_path: Path) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text("channels:\n  - conda-forge\n  - defaults")
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    assert requirements.channels == ["conda-forge", "defaults"]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_surrounding_comments(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
            # This is a comment before
                - yolo  # [osx]
            # This is a comment after
                # This is another comment
                - foo  # [linux]
                # And this is a comment after
                - bar  # [win]
                # Next is an empty comment
                - baz  #
                - pip: pip-package
                #
                - pip: pip-package2  # [osx]
                #
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    assert requirements.requirements == {
        "yolo": [
            Spec(
                name="yolo",
                which="conda",
                selector="osx",
                identifier="8b0c4c31",
            ),
            Spec(
                name="yolo",
                which="pip",
                selector="osx",
                identifier="8b0c4c31",
            ),
        ],
        "foo": [
            Spec(
                name="foo",
                which="conda",
                selector="linux",
                identifier="ecd4baa6",
            ),
            Spec(
                name="foo",
                which="pip",
                selector="linux",
                identifier="ecd4baa6",
            ),
        ],
        "bar": [
            Spec(
                name="bar",
                which="conda",
                selector="win",
                identifier="8528de75",
            ),
            Spec(
                name="bar",
                which="pip",
                selector="win",
                identifier="8528de75",
            ),
        ],
        "baz": [
            Spec(
                name="baz",
                which="conda",
                identifier="9e467fa1",
            ),
            Spec(name="baz", which="pip", identifier="9e467fa1"),
        ],
        "pip-package": [
            Spec(
                name="pip-package",
                which="pip",
                identifier="5813b64a",
            ),
        ],
        "pip-package2": [
            Spec(
                name="pip-package2",
                which="pip",
                selector="osx",
                identifier="1c0fa4c4",
            ),
        ],
    }


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_filter_pip_and_conda(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    # Setup a sample ParsedRequirements instance with platform selectors
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
              - conda: package1  # [linux64]
              - conda: package2  # [osx64]
              - pip: package3
              - pip: package4  # [unix]
              - common_package  # [unix]
              - conda: shared_package  # [linux64]
                pip: shared_package  # [win64]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    assert requirements.requirements == {
        "package1": [
            Spec(
                name="package1",
                which="conda",
                selector="linux64",
                identifier="c292b98a",
            ),
        ],
        "package2": [
            Spec(
                name="package2",
                which="conda",
                selector="osx64",
                identifier="b2ac468f",
            ),
        ],
        "package3": [
            Spec(
                name="package3",
                which="pip",
                identifier="08fd8713",
            ),
        ],
        "package4": [
            Spec(
                name="package4",
                which="pip",
                selector="unix",
                identifier="1d5d7757",
            ),
        ],
        "common_package": [
            Spec(
                name="common_package",
                which="conda",
                selector="unix",
                identifier="f78244dc",
            ),
            Spec(
                name="common_package",
                which="pip",
                selector="unix",
                identifier="f78244dc",
            ),
        ],
        "shared_package": [
            Spec(
                name="shared_package",
                which="conda",
                selector="linux64",
                identifier="1599d575",
            ),
            Spec(
                name="shared_package",
                which="pip",
                selector="win64",
                identifier="46630b59",
            ),
        ],
    }

    resolved = resolve_conflicts(
        requirements.requirements,
        requirements.platforms,
    )
    assert resolved == {
        "package1": {
            "linux-64": {
                "conda": Spec(
                    name="package1",
                    which="conda",
                    selector="linux64",
                    identifier="c292b98a",
                ),
            },
        },
        "package2": {
            "osx-64": {
                "conda": Spec(
                    name="package2",
                    which="conda",
                    selector="osx64",
                    identifier="b2ac468f",
                ),
            },
        },
        "package3": {
            None: {
                "pip": Spec(
                    name="package3",
                    which="pip",
                    identifier="08fd8713",
                ),
            },
        },
        "package4": {
            "linux-64": {
                "pip": Spec(
                    name="package4",
                    which="pip",
                    selector="unix",
                    identifier="1d5d7757",
                ),
            },
            "linux-aarch64": {
                "pip": Spec(
                    name="package4",
                    which="pip",
                    selector="unix",
                    identifier="1d5d7757",
                ),
            },
            "linux-ppc64le": {
                "pip": Spec(
                    name="package4",
                    which="pip",
                    selector="unix",
                    identifier="1d5d7757",
                ),
            },
            "osx-64": {
                "pip": Spec(
                    name="package4",
                    which="pip",
                    selector="unix",
                    identifier="1d5d7757",
                ),
            },
            "osx-arm64": {
                "pip": Spec(
                    name="package4",
                    which="pip",
                    selector="unix",
                    identifier="1d5d7757",
                ),
            },
        },
        "common_package": {
            "linux-64": {
                "conda": Spec(
                    name="common_package",
                    which="conda",
                    selector="unix",
                    identifier="f78244dc",
                ),
                "pip": Spec(
                    name="common_package",
                    which="pip",
                    selector="unix",
                    identifier="f78244dc",
                ),
            },
            "linux-aarch64": {
                "conda": Spec(
                    name="common_package",
                    which="conda",
                    selector="unix",
                    identifier="f78244dc",
                ),
                "pip": Spec(
                    name="common_package",
                    which="pip",
                    selector="unix",
                    identifier="f78244dc",
                ),
            },
            "linux-ppc64le": {
                "conda": Spec(
                    name="common_package",
                    which="conda",
                    selector="unix",
                    identifier="f78244dc",
                ),
                "pip": Spec(
                    name="common_package",
                    which="pip",
                    selector="unix",
                    identifier="f78244dc",
                ),
            },
            "osx-64": {
                "conda": Spec(
                    name="common_package",
                    which="conda",
                    selector="unix",
                    identifier="f78244dc",
                ),
                "pip": Spec(
                    name="common_package",
                    which="pip",
                    selector="unix",
                    identifier="f78244dc",
                ),
            },
            "osx-arm64": {
                "conda": Spec(
                    name="common_package",
                    which="conda",
                    selector="unix",
                    identifier="f78244dc",
                ),
                "pip": Spec(
                    name="common_package",
                    which="pip",
                    selector="unix",
                    identifier="f78244dc",
                ),
            },
        },
        "shared_package": {
            "linux-64": {
                "conda": Spec(
                    name="shared_package",
                    which="conda",
                    selector="linux64",
                    identifier="1599d575",
                ),
            },
            "win-64": {
                "pip": Spec(
                    name="shared_package",
                    which="pip",
                    selector="win64",
                    identifier="46630b59",
                ),
            },
        },
    }
    # Pip
    python_deps = filter_python_dependencies(resolved)
    assert python_deps == [
        "common_package; sys_platform == 'linux' or sys_platform == 'darwin'",
        "package3",
        "package4; sys_platform == 'linux' or sys_platform == 'darwin'",
        "shared_package; sys_platform == 'win32' and platform_machine == 'AMD64'",
    ]

    # Conda
    conda_env_spec = create_conda_env_specification(
        resolved,
        channels=requirements.channels,
        platforms=requirements.platforms,
    )

    def sort(x: list[dict[str, str]]) -> list[dict[str, str]]:
        return sorted(x, key=lambda x: tuple(x.items()))

    assert sort(conda_env_spec.conda) == sort(  # type: ignore[arg-type]
        [
            {"sel(linux)": "package1"},
            {"sel(osx)": "package2"},
            {"sel(osx)": "common_package"},
            {"sel(linux)": "common_package"},
            {"sel(linux)": "shared_package"},
        ],
    )
    assert conda_env_spec.pip == [
        "package3",
        "package4; sys_platform == 'linux' or sys_platform == 'darwin'",
        "shared_package; sys_platform == 'win32' and platform_machine == 'AMD64'",
    ]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_duplicates_with_version(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo >1 # [linux64]
                - foo # [linux64]
                - bar
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    assert requirements.requirements == {
        "foo": [
            Spec(
                name="foo",
                which="conda",
                selector="linux64",
                pin=">1",
                identifier="c292b98a",
            ),
            Spec(
                name="foo",
                which="pip",
                selector="linux64",
                pin=">1",
                identifier="c292b98a",
            ),
            Spec(
                name="foo",
                which="conda",
                selector="linux64",
                identifier="dd6a8aaf",
            ),
            Spec(
                name="foo",
                which="pip",
                selector="linux64",
                identifier="dd6a8aaf",
            ),
        ],
        "bar": [
            Spec(
                name="bar",
                which="conda",
                identifier="08fd8713",
            ),
            Spec(
                name="bar",
                which="pip",
                identifier="08fd8713",
            ),
        ],
    }
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    assert resolved == {
        "foo": {
            "linux-64": {
                "conda": Spec(
                    name="foo",
                    which="conda",
                    selector="linux64",
                    pin=">1",
                    identifier="c292b98a",
                ),
                "pip": Spec(
                    name="foo",
                    which="pip",
                    selector="linux64",
                    pin=">1",
                    identifier="c292b98a",
                ),
            },
        },
        "bar": {
            None: {
                "conda": Spec(
                    name="bar",
                    which="conda",
                    identifier="08fd8713",
                ),
                "pip": Spec(
                    name="bar",
                    which="pip",
                    identifier="08fd8713",
                ),
            },
        },
    }
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )
    assert env_spec.conda == [{"sel(linux)": "foo >1"}, "bar"]
    assert env_spec.pip == []

    python_deps = filter_python_dependencies(resolved)
    assert python_deps == [
        "bar",
        "foo >1; sys_platform == 'linux' and platform_machine == 'x86_64'",
    ]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_duplicates_different_platforms(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo >1 # [linux64]
                - foo <=2 # [linux]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    assert requirements.requirements == {
        "foo": [
            Spec(
                name="foo",
                which="conda",
                selector="linux64",
                pin=">1",
                identifier="c292b98a",
            ),
            Spec(
                name="foo",
                which="pip",
                selector="linux64",
                pin=">1",
                identifier="c292b98a",
            ),
            Spec(
                name="foo",
                which="conda",
                selector="linux",
                pin="<=2",
                identifier="ecd4baa6",
            ),
            Spec(
                name="foo",
                which="pip",
                selector="linux",
                pin="<=2",
                identifier="ecd4baa6",
            ),
        ],
    }
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    assert resolved == {
        "foo": {
            "linux-64": {
                "conda": Spec(
                    name="foo",
                    which="conda",
                    pin=">1,<=2",
                    identifier="c292b98a",
                ),
                "pip": Spec(
                    name="foo",
                    which="pip",
                    pin=">1,<=2",
                    identifier="c292b98a",
                ),
            },
            "linux-aarch64": {
                "conda": Spec(
                    name="foo",
                    which="conda",
                    selector="linux",
                    pin="<=2",
                    identifier="ecd4baa6",
                ),
                "pip": Spec(
                    name="foo",
                    which="pip",
                    selector="linux",
                    pin="<=2",
                    identifier="ecd4baa6",
                ),
            },
            "linux-ppc64le": {
                "conda": Spec(
                    name="foo",
                    which="conda",
                    selector="linux",
                    pin="<=2",
                    identifier="ecd4baa6",
                ),
                "pip": Spec(
                    name="foo",
                    which="pip",
                    selector="linux",
                    pin="<=2",
                    identifier="ecd4baa6",
                ),
            },
        },
    }
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )
    assert env_spec.conda == [{"sel(linux)": "foo >1,<=2"}]
    assert env_spec.pip == []

    python_deps = filter_python_dependencies(resolved)
    assert python_deps == [
        "foo <=2; sys_platform == 'linux' and platform_machine == 'aarch64'",
        "foo <=2; sys_platform == 'linux' and platform_machine == 'ppc64le'",
        "foo >1,<=2; sys_platform == 'linux' and platform_machine == 'x86_64'",
    ]

    # now only use linux-64
    platforms: list[Platform] = ["linux-64"]
    resolved = resolve_conflicts(requirements.requirements, platforms)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        platforms,
    )
    assert env_spec.conda == ["foo >1,<=2"]
    assert env_spec.pip == []


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_expand_none_with_different_platforms(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo >1 # [linux64]
                - foo <3
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    assert requirements.requirements == {
        "foo": [
            Spec(
                name="foo",
                which="conda",
                selector="linux64",
                pin=">1",
                identifier="c292b98a",
            ),
            Spec(
                name="foo",
                which="pip",
                selector="linux64",
                pin=">1",
                identifier="c292b98a",
            ),
            Spec(
                name="foo",
                which="conda",
                pin="<3",
                identifier="5eb93b8c",
            ),
            Spec(
                name="foo",
                which="pip",
                pin="<3",
                identifier="5eb93b8c",
            ),
        ],
    }
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    assert resolved == {
        "foo": {
            "linux-64": {
                "conda": Spec(
                    name="foo",
                    which="conda",
                    pin=">1,<3",
                    identifier="c292b98a",
                ),
                "pip": Spec(
                    name="foo",
                    which="pip",
                    pin=">1,<3",
                    identifier="c292b98a",
                ),
            },
            "linux-aarch64": {
                "conda": Spec(
                    name="foo",
                    which="conda",
                    pin="<3",
                    identifier="5eb93b8c",
                ),
                "pip": Spec(
                    name="foo",
                    which="pip",
                    pin="<3",
                    identifier="5eb93b8c",
                ),
            },
            "linux-ppc64le": {
                "conda": Spec(
                    name="foo",
                    which="conda",
                    pin="<3",
                    identifier="5eb93b8c",
                ),
                "pip": Spec(
                    name="foo",
                    which="pip",
                    pin="<3",
                    identifier="5eb93b8c",
                ),
            },
            "osx-64": {
                "conda": Spec(
                    name="foo",
                    which="conda",
                    pin="<3",
                    identifier="5eb93b8c",
                ),
                "pip": Spec(
                    name="foo",
                    which="pip",
                    pin="<3",
                    identifier="5eb93b8c",
                ),
            },
            "osx-arm64": {
                "conda": Spec(
                    name="foo",
                    which="conda",
                    pin="<3",
                    identifier="5eb93b8c",
                ),
                "pip": Spec(
                    name="foo",
                    which="pip",
                    pin="<3",
                    identifier="5eb93b8c",
                ),
            },
            "win-64": {
                "conda": Spec(
                    name="foo",
                    which="conda",
                    pin="<3",
                    identifier="5eb93b8c",
                ),
                "pip": Spec(
                    name="foo",
                    which="pip",
                    pin="<3",
                    identifier="5eb93b8c",
                ),
            },
        },
    }
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )
    assert env_spec.conda == [
        {"sel(linux)": "foo >1,<3"},
        {"sel(osx)": "foo <3"},
        {"sel(win)": "foo <3"},
    ]

    assert env_spec.pip == []

    python_deps = filter_python_dependencies(resolved)
    assert python_deps == [
        "foo <3; sys_platform == 'darwin' and platform_machine == 'arm64'",
        "foo <3; sys_platform == 'darwin' and platform_machine == 'x86_64'",
        "foo <3; sys_platform == 'linux' and platform_machine == 'aarch64'",
        "foo <3; sys_platform == 'linux' and platform_machine == 'ppc64le'",
        "foo <3; sys_platform == 'win32' and platform_machine == 'AMD64'",
        "foo >1,<3; sys_platform == 'linux' and platform_machine == 'x86_64'",
    ]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_different_pins_on_conda_and_pip(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pip: foo >1
                  conda: foo <1
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    assert requirements.requirements == {
        "foo": [
            Spec(
                name="foo",
                which="conda",
                pin="<1",
                identifier="17e5d607",
            ),
            Spec(
                name="foo",
                which="pip",
                pin=">1",
                identifier="17e5d607",
            ),
        ],
    }
    with pytest.warns(UserWarning, match="Version Pinning Conflict"):
        resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    assert resolved == {
        "foo": {
            None: {
                "conda": Spec(
                    name="foo",
                    which="conda",
                    pin="<1",
                    identifier="17e5d607",
                ),
                "pip": Spec(
                    name="foo",
                    which="pip",
                    pin=">1",
                    identifier="17e5d607",
                ),
            },
        },
    }
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )
    assert env_spec.conda == ["foo <1"]

    assert env_spec.pip == []

    python_deps = filter_python_dependencies(resolved)
    assert python_deps == ["foo >1"]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_pip_pinned_conda_not(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pip: foo >1
                  conda: foo
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )
    assert env_spec.conda == []

    assert env_spec.pip == ["foo >1"]

    python_deps = filter_python_dependencies(resolved)
    assert python_deps == ["foo >1"]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_conda_pinned_pip_not(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pip: foo
                  conda: foo >1
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )
    assert env_spec.conda == ["foo >1"]

    assert env_spec.pip == []

    python_deps = filter_python_dependencies(resolved)
    assert python_deps == []


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_filter_python_dependencies_with_platforms(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo # [unix]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, ["linux-64"])
    python_deps = filter_python_dependencies(resolved)
    assert python_deps == [
        "foo; sys_platform == 'linux' and platform_machine == 'x86_64'",
    ]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_conda_with_comments(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - adaptive # [linux64]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
        selector="comment",
    )
    assert env_spec.conda == ["adaptive"]
    assert env_spec.pip == []
    write_conda_environment_file(env_spec, str(tmp_path / "environment.yaml"))
    with (tmp_path / "environment.yaml").open() as f:
        lines = f.readlines()
        dependency_line = next(line for line in lines if "adaptive" in line)
        assert "- adaptive  # [linux64]" in dependency_line


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_duplicate_names(toml_or_yaml: Literal["toml", "yaml"], tmp_path: Path) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - conda: flatbuffers
                - pip: flatbuffers
                  conda: python-flatbuffers
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )
    assert env_spec.conda == ["flatbuffers", "python-flatbuffers"]
    assert env_spec.pip == []

    python_deps = filter_python_dependencies(resolved)
    assert python_deps == ["flatbuffers"]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_conflicts_when_selector_comment(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo >1 # [linux64]
                - foo <2 # [linux]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
        selector="comment",
    )
    assert env_spec.conda == ["foo >1,<2", "foo <2", "foo <2"]
    assert env_spec.pip == []

    write_conda_environment_file(env_spec, str(tmp_path / "environment.yaml"))

    with (tmp_path / "environment.yaml").open() as f:
        text = "".join(f.readlines())
        assert "- foo >1,<2  # [linux64]" in text
        assert "- foo <2 # [aarch64]" in text
        assert "- foo <2 # [ppc64le]" in text

    # With just [unix]
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo >1
                - foo <2 # [unix]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
        selector="comment",
    )
    assert env_spec.conda == [
        "foo <2,>1",
        "foo <2,>1",
        "foo <2,>1",
        "foo <2,>1",
        "foo <2,>1",
        "foo >1",
    ]
    assert env_spec.pip == []

    write_conda_environment_file(env_spec, str(tmp_path / "environment.yaml"))

    with (tmp_path / "environment.yaml").open() as f:
        text = "".join(f.readlines())
        assert "- foo <2,>1  # [linux64]" in text
        assert "- foo <2,>1 # [osx64]" in text
        assert "- foo <2,>1 # [arm64]" in text
        assert "- foo <2,>1 # [aarch64]" in text
        assert "- foo <2,>1 # [ppc64le]" in text
        assert "- foo >1 # [win64]" in text


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_platforms_section_in_yaml(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            platforms:
                - linux-64
                - osx-arm64
            dependencies:
                - foo
                - bar # [win]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
        selector="sel",
    )
    assert env_spec.conda == ["foo"]
    assert env_spec.pip == []
    assert env_spec.platforms == ["linux-64", "osx-arm64"]
    python_deps = filter_python_dependencies(resolved)
    assert python_deps == ["foo"]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_platforms_section_in_yaml_similar_platforms(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            channels:
                - conda-forge
            platforms:
                - linux-64
                - linux-aarch64
            dependencies:
                - foo
                - bar # [win]
                - yolo <1 # [aarch64]
                - yolo >1 # [linux64]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    with pytest.warns(UserWarning, match="Dependency Conflict on"):
        env_spec = create_conda_env_specification(
            resolved,
            requirements.channels,
            requirements.platforms,
            selector="sel",
        )
    assert env_spec.conda == ["foo", {"sel(linux)": "yolo <1"}]
    assert env_spec.pip == []
    assert env_spec.platforms == ["linux-64", "linux-aarch64"]
    python_deps = filter_python_dependencies(resolved)
    assert python_deps == [
        "foo",
        "yolo <1; sys_platform == 'linux' and platform_machine == 'aarch64'",
        "yolo >1; sys_platform == 'linux' and platform_machine == 'x86_64'",
    ]

    # Test with comment selector
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
        selector="comment",
    )
    assert env_spec.conda == ["foo", "yolo >1", "yolo <1"]
    assert env_spec.pip == []

    write_conda_environment_file(env_spec, str(tmp_path / "environment.yaml"))

    with (tmp_path / "environment.yaml").open() as f:
        text = "".join(f.readlines())
        assert "- yolo >1  # [linux64]" in text
        assert "- yolo <1 # [aarch64]" in text
        assert "platforms:" in text
        assert "- linux-64" in text
        assert "- linux-aarch64" in text


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_conda_with_non_platform_comment(
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
                - pip: qsimcirq  # [linux64]
                - pip: slurm-usage  # added to avoid https://github.com/conda/conda-lock/pull/564
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
        selector="comment",
    )
    assert env_spec.conda == []
    assert env_spec.pip == ["qsimcirq", "slurm-usage"]
    write_conda_environment_file(env_spec, str(tmp_path / "environment.yaml"))
    with (tmp_path / "environment.yaml").open() as f:
        lines = "".join(f.readlines())
    assert "- qsimcirq  # [linux64]" in lines
    assert "- slurm-usage" in lines
    assert "  - pip:" in lines


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_pip_and_conda_different_name_on_linux64(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    # On linux64, the conda package is called "cuquantum-python" and
    # the pip package is called "cuquantum". We test that not both
    # packages are in the final environment file.
    p.write_text(
        textwrap.dedent(
            """\
            name: test
            channels:
              - conda-forge
            dependencies:
              - conda: cuquantum-python  # [linux64]
                pip: cuquantum  # [linux64]
            platforms:
              - linux-64
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=True)
    expected = {
        "cuquantum-python": [
            Spec(
                name="cuquantum-python",
                which="conda",
                selector="linux64",
                identifier="c292b98a",
            ),
        ],
        "cuquantum": [
            Spec(
                name="cuquantum",
                which="pip",
                selector="linux64",
                identifier="c292b98a",
            ),
        ],
    }
    assert requirements.requirements == expected
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    expected_resolved = {
        "cuquantum-python": {
            "linux-64": {
                "conda": Spec(
                    name="cuquantum-python",
                    which="conda",
                    selector="linux64",
                    identifier="c292b98a",
                ),
            },
        },
        "cuquantum": {
            "linux-64": {
                "pip": Spec(
                    name="cuquantum",
                    which="pip",
                    selector="linux64",
                    identifier="c292b98a",
                ),
            },
        },
    }
    assert resolved == expected_resolved
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )
    assert env_spec.conda == ["cuquantum-python"]
    assert env_spec.pip == []


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_parse_requirements_with_ignore_pin(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo >1
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, ignore_pins=["foo"], verbose=False)
    assert requirements.requirements == {
        "foo": [
            Spec(
                name="foo",
                which="conda",
                identifier="17e5d607",
            ),
            Spec(
                name="foo",
                which="pip",
                identifier="17e5d607",
            ),
        ],
    }


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_parse_requirements_with_skip_dependency(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo >1
                - bar
                - baz
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(
        p,
        skip_dependencies=["foo", "bar"],
        verbose=False,
    )
    assert requirements.requirements == {
        "baz": [
            Spec(
                name="baz",
                which="conda",
                identifier="08fd8713",
            ),
            Spec(
                name="baz",
                which="pip",
                identifier="08fd8713",
            ),
        ],
    }


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_pin_star_cuda(toml_or_yaml: Literal["toml", "yaml"], tmp_path: Path) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - conda: qsimcirq * cuda*  # [linux64]
                - conda: qsimcirq * cpu*  # [arm64]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p)
    assert requirements.requirements == {
        "qsimcirq": [
            Spec(
                name="qsimcirq",
                which="conda",
                selector="linux64",
                pin="* cuda*",
                identifier="c292b98a",
            ),
            Spec(
                name="qsimcirq",
                which="conda",
                selector="arm64",
                pin="* cpu*",
                identifier="489f33e0",
            ),
        ],
    }


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_parse_requirements_with_overwrite_pins(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo >1
                - conda: bar * cuda*
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(
        p,
        overwrite_pins=["foo=1", "bar * cpu*"],
        verbose=False,
    )
    assert requirements.requirements == {
        "foo": [
            Spec(
                name="foo",
                which="conda",
                pin="=1",
                identifier="17e5d607",
            ),
            Spec(
                name="foo",
                which="pip",
                pin="=1",
                identifier="17e5d607",
            ),
        ],
        "bar": [
            Spec(
                name="bar",
                which="conda",
                pin="* cpu*",
                identifier="5eb93b8c",
            ),
        ],
    }


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_duplicate_names_different_platforms(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pip: ray  # [arm64]
                - conda: ray-core  # [linux64]
                  pip: ray # [linux64]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(
        p,
        overwrite_pins=["foo=1", "bar * cpu*"],
        verbose=False,
    )
    assert requirements.requirements == {
        "ray": [
            Spec(
                name="ray",
                which="pip",
                selector="arm64",
                identifier="1b26c5b2",
            ),
            Spec(
                name="ray",
                which="pip",
                selector="linux64",
                identifier="dd6a8aaf",
            ),
        ],
        "ray-core": [
            Spec(
                name="ray-core",
                which="conda",
                selector="linux64",
                identifier="dd6a8aaf",
            ),
        ],
    }
    platforms_arm64: list[Platform] = ["osx-arm64"]
    resolved = resolve_conflicts(requirements.requirements, platforms_arm64)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        platforms_arm64,
    )
    assert env_spec.conda == []
    assert env_spec.pip == ["ray"]

    platforms_linux64: list[Platform] = ["linux-64"]
    resolved = resolve_conflicts(requirements.requirements, platforms_linux64)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        platforms_linux64,
    )
    assert env_spec.conda == ["ray-core"]
    assert env_spec.pip == []


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_with_unused_platform(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - adaptive # [linux64]
                - rsync-time-machine >0.1 # [osx64]
                - rsync-time-machine <3
                - rsync-time-machine >1 # [linux64]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    requirements = parse_requirements(p, verbose=False)
    platforms: list[Platform] = ["linux-64"]
    resolved = resolve_conflicts(requirements.requirements, platforms)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        platforms,
        selector="comment",
    )
    assert env_spec.conda == ["adaptive", "rsync-time-machine >1,<3"]
    assert env_spec.pip == []


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_pip_with_pinning(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    p1 = tmp_path / "p1" / "requirements.yaml"
    p1.parent.mkdir()
    p1.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pip: qiskit-terra ==0.25.2.1
                - pip: qiskit-terra ==0.25.2.2
            """,
        ),
    )
    p1 = maybe_as_toml(toml_or_yaml, p1)

    requirements = parse_requirements(p1, verbose=False)
    with pytest.raises(
        VersionConflictError,
        match="Invalid version pinning '==0.25.2.1' for 'qiskit-terra'",
    ):
        resolve_conflicts(requirements.requirements, requirements.platforms)

    p2 = tmp_path / "p2" / "requirements.yaml"
    p2.parent.mkdir()
    p2.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pip: qiskit-terra =0.25.2.1
                - pip: qiskit-terra =0.25.2.1
            """,
        ),
    )
    p2 = maybe_as_toml(toml_or_yaml, p2)

    requirements = parse_requirements(p2, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )
    assert env_spec.conda == []
    assert env_spec.pip == ["qiskit-terra ==0.25.2.1"]


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_pip_with_pinning_special_case_wildcard(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    p1 = tmp_path / "p1" / "requirements.yaml"
    p1.parent.mkdir()
    p1.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pip: qsimcirq * cuda*
                - pip: qsimcirq * cuda*
            """,
        ),
    )
    p1 = maybe_as_toml(toml_or_yaml, p1)
    requirements = parse_requirements(p1, verbose=False)

    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    assert resolved == {
        "qsimcirq": {
            None: {
                "pip": Spec(
                    name="qsimcirq",
                    which="pip",
                    pin="* cuda*",
                    identifier="17e5d607",
                ),
            },
        },
    }

    p2 = tmp_path / "p2" / "requirements.yaml"
    p2.parent.mkdir()
    p2.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pip: qsimcirq * cuda*
                - pip: qsimcirq * cpu*
            """,
        ),
    )
    p2 = maybe_as_toml(toml_or_yaml, p2)

    requirements = parse_requirements(p2, verbose=False)

    with pytest.raises(
        VersionConflictError,
        match="['* cuda*', '* cpu*']",
    ):
        resolve_conflicts(requirements.requirements, requirements.platforms)


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_pip_with_pinning_special_case_git_repo(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    p1 = tmp_path / "p1" / "requirements.yaml"
    p1.parent.mkdir()
    p1.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - pip: adaptive @ git+https://github.com/python-adaptive/adaptive.git@main
                - pip: adaptive @ git+https://github.com/python-adaptive/adaptive.git@main
            """,
        ),
    )
    p1 = maybe_as_toml(toml_or_yaml, p1)

    requirements = parse_requirements(p1, verbose=False)

    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    assert resolved == {
        "adaptive": {
            None: {
                "pip": Spec(
                    name="adaptive",
                    which="pip",
                    pin="@ git+https://github.com/python-adaptive/adaptive.git@main",
                    identifier="17e5d607",
                ),
            },
        },
    }


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_not_equal(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    p1 = tmp_path / "p1" / "requirements.yaml"
    p1.parent.mkdir()
    p1.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - adaptive != 1.0.0
                - adaptive <2
            """,
        ),
    )
    p1 = maybe_as_toml(toml_or_yaml, p1)

    requirements = parse_requirements(p1, verbose=False)

    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    assert resolved == {
        "adaptive": {
            None: {
                "conda": Spec(
                    name="adaptive",
                    which="conda",
                    pin="!=1.0.0,<2",
                    identifier="17e5d607",
                ),
                "pip": Spec(
                    name="adaptive",
                    which="pip",
                    pin="!=1.0.0,<2",
                    identifier="17e5d607",
                ),
            },
        },
    }


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_dot_in_package_name(
    toml_or_yaml: Literal["toml", "yaml"],
    tmp_path: Path,
) -> None:
    p1 = tmp_path / "p1" / "requirements.yaml"
    p1.parent.mkdir()
    p1.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - ruamel.yaml
            """,
        ),
    )
    p1 = maybe_as_toml(toml_or_yaml, p1)

    requirements = parse_requirements(p1, verbose=False)
    assert requirements.requirements == {
        "ruamel.yaml": [
            Spec(name="ruamel.yaml", which="conda", identifier="17e5d607"),
            Spec(name="ruamel.yaml", which="pip", identifier="17e5d607"),
        ],
    }


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_optional_dependencies(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    p = tmp_path / "p" / "requirements.yaml"
    p.parent.mkdir()
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - adaptive != 1.0.0
                - adaptive <2
            optional_dependencies:
                test:
                    - pytest
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)

    requirements = parse_requirements(p, verbose=False, extras="*")
    assert requirements.optional_dependencies.keys() == {"test"}
    assert requirements.optional_dependencies["test"].keys() == {"pytest"}

    requirements = parse_requirements(p, verbose=False, extras=[["test"]])
    with pytest.raises(ValueError, match="Cannot specify `extras` list"):
        parse_requirements(Path(f"{p}[test]"), verbose=False, extras=[["test"]])
    with pytest.raises(ValueError, match="Length of `extras`"):
        parse_requirements(p, verbose=False, extras=[[], []])
    requirements2 = parse_requirements(Path(f"{p}[test]"), verbose=False)
    assert requirements2.optional_dependencies == requirements.optional_dependencies
    resolved = resolve_conflicts(
        requirements.requirements,
        requirements.platforms,
        optional_dependencies=requirements.optional_dependencies,
    )
    assert resolved.keys() == {"adaptive", "pytest"}


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_optional_dependencies_multiple_sections(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    p = tmp_path / "p" / "requirements.yaml"
    p.parent.mkdir()
    p.write_text(
        textwrap.dedent(
            """\
            optional_dependencies:
                test:
                    - pytest
                lint:
                    - flake8
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)

    requirements = parse_requirements(p, verbose=False, extras=[["test"]])
    assert requirements.optional_dependencies.keys() == {"test"}

    requirements = parse_requirements(p, verbose=False, extras=[["lint"]])
    assert requirements.optional_dependencies.keys() == {"lint"}

    requirements = parse_requirements(p, verbose=False, extras=[["test", "lint"]])
    assert requirements.optional_dependencies.keys() == {"test", "lint"}


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_optional_dependencies_get_python_dependencies(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    p = tmp_path / "p" / "requirements.yaml"
    p.parent.mkdir()
    p.write_text(
        textwrap.dedent(
            """\
            optional_dependencies:
                test:
                    - pytest
                lint:
                    - flake8
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)

    deps = get_python_dependencies(f"{p}[test]", verbose=False)
    assert deps.dependencies == []
    assert deps.extras == {"test": ["pytest"], "lint": ["flake8"]}


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_pip_dep_with_extras(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    p = tmp_path / "p" / "requirements.yaml"
    p.parent.mkdir()
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - conda: adaptive
                  pip: adaptive[notebook]
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)

    requirements = parse_requirements(p, verbose=False, extras="*")
    assert requirements.optional_dependencies == {}
    resolved = resolve_conflicts(
        requirements.requirements,
        requirements.platforms,
        optional_dependencies=requirements.optional_dependencies,
    )
    assert resolved == {
        "adaptive": {
            None: {
                "conda": Spec(
                    name="adaptive",
                    which="conda",
                    pin=None,
                    identifier="17e5d607",
                    selector=None,
                ),
            },
        },
        "adaptive[notebook]": {
            None: {
                "pip": Spec(
                    name="adaptive[notebook]",
                    which="pip",
                    pin=None,
                    identifier="17e5d607",
                    selector=None,
                ),
            },
        },
    }


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_local_dependency_in_dependencies_list(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    p = tmp_path / "p" / "requirements.yaml"
    p.parent.mkdir()
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - ../p  # self
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)
    with pytest.raises(ValueError, match=r"Use the `local_dependencies` section"):
        parse_requirements(p, verbose=False)


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_optional_dependencies_with_local_dependencies(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    p1 = tmp_path / "p1" / "requirements.yaml"
    p1.parent.mkdir()
    p1.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - adaptive
            optional_dependencies:
                test:
                    - pytest
            """,
        ),
    )
    p1 = maybe_as_toml(toml_or_yaml, p1)

    p2 = tmp_path / "p2" / "requirements.yaml"
    p2.parent.mkdir()
    p2.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numthreads
            optional_dependencies:
                local:
                    - ../p1
                    - black
            """,
        ),
    )
    p2 = maybe_as_toml(toml_or_yaml, p2)

    requirements = parse_requirements(p2, verbose=True, extras="*")
    assert requirements.optional_dependencies.keys() == {"local"}
    assert requirements.optional_dependencies["local"].keys() == {"black"}
    assert requirements.requirements.keys() == {"adaptive", "numthreads"}
    resolved = resolve_conflicts(
        requirements.requirements,
        requirements.platforms,
        optional_dependencies=requirements.optional_dependencies,
    )
    assert resolved.keys() == {"adaptive", "numthreads", "black"}


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_optional_dependencies_with_local_dependencies_with_extras(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
    capsys: pytest.CaptureFixture,
) -> None:
    p1 = tmp_path / "p1" / "requirements.yaml"
    p1.parent.mkdir()
    p1.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - adaptive
            optional_dependencies:
                test:
                    - pytest
            """,
        ),
    )
    p1 = maybe_as_toml(toml_or_yaml, p1)

    p2 = tmp_path / "p2" / "requirements.yaml"
    p2.parent.mkdir()
    p2.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - numthreads
            optional_dependencies:
                local:
                    - ../p1[test]
            """,
        ),
    )
    p2 = maybe_as_toml(toml_or_yaml, p2)
    requirements = parse_requirements(p2, verbose=True, extras="*")
    assert "Removing empty" in capsys.readouterr().out
    assert requirements.optional_dependencies.keys() == {"test"}
    assert requirements.optional_dependencies["test"].keys() == {"pytest"}

    resolved = resolve_conflicts(
        requirements.requirements,
        requirements.platforms,
        optional_dependencies=requirements.optional_dependencies,
    )
    assert resolved.keys() == {"adaptive", "numthreads", "pytest"}


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_optional_dependencies_with_dicts(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    p1 = tmp_path / "p1" / "requirements.yaml"
    p1.parent.mkdir()
    p1.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - adaptive
            optional_dependencies:
                flat:
                    - conda: python-flatbuffers
                      pip: flatbuffers
            """,
        ),
    )
    p1 = maybe_as_toml(toml_or_yaml, p1)

    requirements = parse_requirements(p1, verbose=True, extras="*")
    assert requirements.optional_dependencies.keys() == {"flat"}
    assert requirements.optional_dependencies["flat"].keys() == {
        "python-flatbuffers",
        "flatbuffers",
    }

    resolved = resolve_conflicts(
        requirements.requirements,
        requirements.platforms,
        optional_dependencies=requirements.optional_dependencies,
    )
    assert resolved.keys() == {"adaptive", "python-flatbuffers", "flatbuffers"}


@pytest.mark.parametrize("toml_or_yaml", ["toml", "yaml"])
def test_optional_dependencies_with_version_specifier(
    tmp_path: Path,
    toml_or_yaml: Literal["toml", "yaml"],
) -> None:
    p = tmp_path / "p" / "requirements.yaml"
    p.parent.mkdir()
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - adaptive
            optional_dependencies:
                specific:
                    - adaptive =0.13.2
            """,
        ),
    )
    p = maybe_as_toml(toml_or_yaml, p)

    requirements = parse_requirements(p, verbose=False, extras="*")
    assert requirements.optional_dependencies.keys() == {"specific"}
    assert requirements.optional_dependencies["specific"].keys() == {"adaptive"}
    assert (
        requirements.optional_dependencies["specific"]["adaptive"][0].pin == "=0.13.2"
    )

    requirements = parse_requirements(p, verbose=False, extras=[["specific"]])
    requirements2 = parse_requirements(Path(f"{p}[specific]"), verbose=False)
    assert requirements2.optional_dependencies == requirements.optional_dependencies
    resolved = resolve_conflicts(
        requirements.requirements,
        requirements.platforms,
        optional_dependencies=requirements.optional_dependencies,
    )
    assert resolved.keys() == {"adaptive"}
    assert resolved["adaptive"][None]["conda"].pin == "=0.13.2"
