"""unidep tests."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from unidep import (
    create_conda_env_specification,
    filter_python_dependencies,
    find_requirements_files,
    get_python_dependencies,
    parse_yaml_requirements,
    resolve_conflicts,
    write_conda_environment_file,
)
from unidep._conda_env import CondaEnvironmentSpec
from unidep.platform_definitions import Meta, Platform

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture()
def setup_test_files(tmp_path: Path) -> tuple[Path, Path]:
    d1 = tmp_path / "dir1"
    d1.mkdir()
    f1 = d1 / "requirements.yaml"
    f1.write_text("dependencies:\n  - numpy\n  - conda: mumps")

    d2 = tmp_path / "dir2"
    d2.mkdir()
    f2 = d2 / "requirements.yaml"
    f2.write_text("dependencies:\n  - pip: pandas")

    return (f1, f2)


def test_find_requirements_files(
    tmp_path: Path,
    setup_test_files: tuple[Path, Path],
) -> None:
    # Make sure to pass the depth argument correctly if your function expects it.
    found_files = find_requirements_files(tmp_path, depth=1, verbose=True)

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


def test_parse_requirements(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
    assert requirements.requirements == {
        "foo": [
            Meta(
                name="foo",
                which="conda",
                comment="# [linux64]",
                pin=">1",
                identifier="c292b98a",
            ),
            Meta(
                name="foo",
                which="pip",
                comment="# [linux64]",
                pin=">1",
                identifier="c292b98a",
            ),
            Meta(
                name="foo",
                which="conda",
                comment="# [unix]",
                pin=None,
                identifier="530d9eaa",
            ),
            Meta(
                name="foo",
                which="pip",
                comment="# [unix]",
                pin=None,
                identifier="530d9eaa",
            ),
        ],
        "bar": [
            Meta(
                name="bar",
                which="conda",
                comment=None,
                pin=">1",
                identifier="08fd8713",
            ),
            Meta(
                name="bar",
                which="pip",
                comment=None,
                pin=">1",
                identifier="08fd8713",
            ),
            Meta(
                name="bar",
                which="conda",
                comment=None,
                pin=None,
                identifier="9e467fa1",
            ),
            Meta(
                name="bar",
                which="pip",
                comment=None,
                pin=None,
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
    requirements = parse_yaml_requirements(*setup_test_files, verbose=verbose)
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
    requirements = parse_yaml_requirements(*setup_test_files)
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


def test_create_conda_env_specification_platforms(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p)
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

    parse_yaml_requirements(f, verbose=True)
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
    assert requires1 == ["numpy"]
    requires2 = get_python_dependencies(str(f2))
    assert requires2 == ["pandas"]

    # Test with a file that doesn't exist
    with pytest.raises(FileNotFoundError):
        get_python_dependencies("nonexistent_file.yaml", raises_if_missing=True)
    assert (
        get_python_dependencies("nonexistent_file.yaml", raises_if_missing=False) == []
    )


def test_channels(tmp_path: Path) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text("channels:\n  - conda-forge\n  - defaults")
    requirements = parse_yaml_requirements(p, verbose=False)
    assert requirements.channels == ["conda-forge", "defaults"]


def test_surrounding_comments(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
    assert requirements.requirements == {
        "yolo": [
            Meta(
                name="yolo",
                which="conda",
                comment="# [osx]",
                pin=None,
                identifier="8b0c4c31",
            ),
            Meta(
                name="yolo",
                which="pip",
                comment="# [osx]",
                pin=None,
                identifier="8b0c4c31",
            ),
        ],
        "foo": [
            Meta(
                name="foo",
                which="conda",
                comment="# [linux]",
                pin=None,
                identifier="ecd4baa6",
            ),
            Meta(
                name="foo",
                which="pip",
                comment="# [linux]",
                pin=None,
                identifier="ecd4baa6",
            ),
        ],
        "bar": [
            Meta(
                name="bar",
                which="conda",
                comment="# [win]",
                pin=None,
                identifier="8528de75",
            ),
            Meta(
                name="bar",
                which="pip",
                comment="# [win]",
                pin=None,
                identifier="8528de75",
            ),
        ],
        "baz": [
            Meta(
                name="baz",
                which="conda",
                comment="#",
                pin=None,
                identifier="fce1baee",
            ),
            Meta(name="baz", which="pip", comment="#", pin=None, identifier="fce1baee"),
        ],
        "pip-package": [
            Meta(
                name="pip-package",
                which="pip",
                comment=None,
                pin=None,
                identifier="5813b64a",
            ),
        ],
        "pip-package2": [
            Meta(
                name="pip-package2",
                which="pip",
                comment="# [osx]",
                pin=None,
                identifier="1c0fa4c4",
            ),
        ],
    }


def test_filter_pip_and_conda(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
    assert requirements.requirements == {
        "package1": [
            Meta(
                name="package1",
                which="conda",
                comment="# [linux64]",
                pin=None,
                identifier="c292b98a",
            ),
        ],
        "package2": [
            Meta(
                name="package2",
                which="conda",
                comment="# [osx64]",
                pin=None,
                identifier="b2ac468f",
            ),
        ],
        "package3": [
            Meta(
                name="package3",
                which="pip",
                comment=None,
                pin=None,
                identifier="08fd8713",
            ),
        ],
        "package4": [
            Meta(
                name="package4",
                which="pip",
                comment="# [unix]",
                pin=None,
                identifier="1d5d7757",
            ),
        ],
        "common_package": [
            Meta(
                name="common_package",
                which="conda",
                comment="# [unix]",
                pin=None,
                identifier="f78244dc",
            ),
            Meta(
                name="common_package",
                which="pip",
                comment="# [unix]",
                pin=None,
                identifier="f78244dc",
            ),
        ],
        "shared_package": [
            Meta(
                name="shared_package",
                which="conda",
                comment="# [linux64]",
                pin=None,
                identifier="1599d575",
            ),
            Meta(
                name="shared_package",
                which="pip",
                comment="# [win64]",
                pin=None,
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
                "conda": Meta(
                    name="package1",
                    which="conda",
                    comment="# [linux64]",
                    pin=None,
                    identifier="c292b98a",
                ),
            },
        },
        "package2": {
            "osx-64": {
                "conda": Meta(
                    name="package2",
                    which="conda",
                    comment="# [osx64]",
                    pin=None,
                    identifier="b2ac468f",
                ),
            },
        },
        "package3": {
            None: {
                "pip": Meta(
                    name="package3",
                    which="pip",
                    comment=None,
                    pin=None,
                    identifier="08fd8713",
                ),
            },
        },
        "package4": {
            "linux-64": {
                "pip": Meta(
                    name="package4",
                    which="pip",
                    comment="# [unix]",
                    pin=None,
                    identifier="1d5d7757",
                ),
            },
            "linux-aarch64": {
                "pip": Meta(
                    name="package4",
                    which="pip",
                    comment="# [unix]",
                    pin=None,
                    identifier="1d5d7757",
                ),
            },
            "linux-ppc64le": {
                "pip": Meta(
                    name="package4",
                    which="pip",
                    comment="# [unix]",
                    pin=None,
                    identifier="1d5d7757",
                ),
            },
            "osx-64": {
                "pip": Meta(
                    name="package4",
                    which="pip",
                    comment="# [unix]",
                    pin=None,
                    identifier="1d5d7757",
                ),
            },
            "osx-arm64": {
                "pip": Meta(
                    name="package4",
                    which="pip",
                    comment="# [unix]",
                    pin=None,
                    identifier="1d5d7757",
                ),
            },
        },
        "common_package": {
            "linux-64": {
                "conda": Meta(
                    name="common_package",
                    which="conda",
                    comment="# [unix]",
                    pin=None,
                    identifier="f78244dc",
                ),
                "pip": Meta(
                    name="common_package",
                    which="pip",
                    comment="# [unix]",
                    pin=None,
                    identifier="f78244dc",
                ),
            },
            "linux-aarch64": {
                "conda": Meta(
                    name="common_package",
                    which="conda",
                    comment="# [unix]",
                    pin=None,
                    identifier="f78244dc",
                ),
                "pip": Meta(
                    name="common_package",
                    which="pip",
                    comment="# [unix]",
                    pin=None,
                    identifier="f78244dc",
                ),
            },
            "linux-ppc64le": {
                "conda": Meta(
                    name="common_package",
                    which="conda",
                    comment="# [unix]",
                    pin=None,
                    identifier="f78244dc",
                ),
                "pip": Meta(
                    name="common_package",
                    which="pip",
                    comment="# [unix]",
                    pin=None,
                    identifier="f78244dc",
                ),
            },
            "osx-64": {
                "conda": Meta(
                    name="common_package",
                    which="conda",
                    comment="# [unix]",
                    pin=None,
                    identifier="f78244dc",
                ),
                "pip": Meta(
                    name="common_package",
                    which="pip",
                    comment="# [unix]",
                    pin=None,
                    identifier="f78244dc",
                ),
            },
            "osx-arm64": {
                "conda": Meta(
                    name="common_package",
                    which="conda",
                    comment="# [unix]",
                    pin=None,
                    identifier="f78244dc",
                ),
                "pip": Meta(
                    name="common_package",
                    which="pip",
                    comment="# [unix]",
                    pin=None,
                    identifier="f78244dc",
                ),
            },
        },
        "shared_package": {
            "linux-64": {
                "conda": Meta(
                    name="shared_package",
                    which="conda",
                    comment="# [linux64]",
                    pin=None,
                    identifier="1599d575",
                ),
            },
            "win-64": {
                "pip": Meta(
                    name="shared_package",
                    which="pip",
                    comment="# [win64]",
                    pin=None,
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


def test_duplicates_with_version(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
    assert requirements.requirements == {
        "foo": [
            Meta(
                name="foo",
                which="conda",
                comment="# [linux64]",
                pin=">1",
                identifier="c292b98a",
            ),
            Meta(
                name="foo",
                which="pip",
                comment="# [linux64]",
                pin=">1",
                identifier="c292b98a",
            ),
            Meta(
                name="foo",
                which="conda",
                comment="# [linux64]",
                pin=None,
                identifier="dd6a8aaf",
            ),
            Meta(
                name="foo",
                which="pip",
                comment="# [linux64]",
                pin=None,
                identifier="dd6a8aaf",
            ),
        ],
        "bar": [
            Meta(
                name="bar",
                which="conda",
                comment=None,
                pin=None,
                identifier="08fd8713",
            ),
            Meta(
                name="bar",
                which="pip",
                comment=None,
                pin=None,
                identifier="08fd8713",
            ),
        ],
    }
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    assert resolved == {
        "foo": {
            "linux-64": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment="# [linux64]",
                    pin=">1",
                    identifier="c292b98a",
                ),
                "pip": Meta(
                    name="foo",
                    which="pip",
                    comment="# [linux64]",
                    pin=">1",
                    identifier="c292b98a",
                ),
            },
        },
        "bar": {
            None: {
                "conda": Meta(
                    name="bar",
                    which="conda",
                    comment=None,
                    pin=None,
                    identifier="08fd8713",
                ),
                "pip": Meta(
                    name="bar",
                    which="pip",
                    comment=None,
                    pin=None,
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


def test_duplicates_different_platforms(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
    assert requirements.requirements == {
        "foo": [
            Meta(
                name="foo",
                which="conda",
                comment="# [linux64]",
                pin=">1",
                identifier="c292b98a",
            ),
            Meta(
                name="foo",
                which="pip",
                comment="# [linux64]",
                pin=">1",
                identifier="c292b98a",
            ),
            Meta(
                name="foo",
                which="conda",
                comment="# [linux]",
                pin="<=2",
                identifier="ecd4baa6",
            ),
            Meta(
                name="foo",
                which="pip",
                comment="# [linux]",
                pin="<=2",
                identifier="ecd4baa6",
            ),
        ],
    }
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    assert resolved == {
        "foo": {
            "linux-64": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment=None,
                    pin=">1,<=2",
                    identifier="c292b98a",
                ),
                "pip": Meta(
                    name="foo",
                    which="pip",
                    comment=None,
                    pin=">1,<=2",
                    identifier="c292b98a",
                ),
            },
            "linux-aarch64": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment="# [linux]",
                    pin="<=2",
                    identifier="ecd4baa6",
                ),
                "pip": Meta(
                    name="foo",
                    which="pip",
                    comment="# [linux]",
                    pin="<=2",
                    identifier="ecd4baa6",
                ),
            },
            "linux-ppc64le": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment="# [linux]",
                    pin="<=2",
                    identifier="ecd4baa6",
                ),
                "pip": Meta(
                    name="foo",
                    which="pip",
                    comment="# [linux]",
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


def test_expand_none_with_different_platforms(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
    assert requirements.requirements == {
        "foo": [
            Meta(
                name="foo",
                which="conda",
                comment="# [linux64]",
                pin=">1",
                identifier="c292b98a",
            ),
            Meta(
                name="foo",
                which="pip",
                comment="# [linux64]",
                pin=">1",
                identifier="c292b98a",
            ),
            Meta(
                name="foo",
                which="conda",
                comment=None,
                pin="<3",
                identifier="5eb93b8c",
            ),
            Meta(
                name="foo",
                which="pip",
                comment=None,
                pin="<3",
                identifier="5eb93b8c",
            ),
        ],
    }
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    assert resolved == {
        "foo": {
            "linux-64": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment=None,
                    pin=">1,<3",
                    identifier="c292b98a",
                ),
                "pip": Meta(
                    name="foo",
                    which="pip",
                    comment=None,
                    pin=">1,<3",
                    identifier="c292b98a",
                ),
            },
            "linux-aarch64": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment=None,
                    pin="<3",
                    identifier="5eb93b8c",
                ),
                "pip": Meta(
                    name="foo",
                    which="pip",
                    comment=None,
                    pin="<3",
                    identifier="5eb93b8c",
                ),
            },
            "linux-ppc64le": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment=None,
                    pin="<3",
                    identifier="5eb93b8c",
                ),
                "pip": Meta(
                    name="foo",
                    which="pip",
                    comment=None,
                    pin="<3",
                    identifier="5eb93b8c",
                ),
            },
            "osx-64": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment=None,
                    pin="<3",
                    identifier="5eb93b8c",
                ),
                "pip": Meta(
                    name="foo",
                    which="pip",
                    comment=None,
                    pin="<3",
                    identifier="5eb93b8c",
                ),
            },
            "osx-arm64": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment=None,
                    pin="<3",
                    identifier="5eb93b8c",
                ),
                "pip": Meta(
                    name="foo",
                    which="pip",
                    comment=None,
                    pin="<3",
                    identifier="5eb93b8c",
                ),
            },
            "win-64": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment=None,
                    pin="<3",
                    identifier="5eb93b8c",
                ),
                "pip": Meta(
                    name="foo",
                    which="pip",
                    comment=None,
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


def test_different_pins_on_conda_and_pip(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
    assert requirements.requirements == {
        "foo": [
            Meta(
                name="foo",
                which="conda",
                comment=None,
                pin="<1",
                identifier="17e5d607",
            ),
            Meta(
                name="foo",
                which="pip",
                comment=None,
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
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment=None,
                    pin="<1",
                    identifier="17e5d607",
                ),
                "pip": Meta(
                    name="foo",
                    which="pip",
                    comment=None,
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


def test_pip_pinned_conda_not(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
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


def test_conda_pinned_pip_not(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
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


def test_filter_python_dependencies_with_platforms(tmp_path: Path) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo # [unix]
            """,
        ),
    )
    requirements = parse_yaml_requirements(p, verbose=False)
    resolved = resolve_conflicts(requirements.requirements, ["linux-64"])
    python_deps = filter_python_dependencies(resolved)
    assert python_deps == [
        "foo; sys_platform == 'linux' and platform_machine == 'x86_64'",
    ]


def test_conda_with_comments(tmp_path: Path) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - adaptive # [linux64]
            """,
        ),
    )
    requirements = parse_yaml_requirements(p, verbose=False)
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


def test_duplicate_names(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
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


def test_conflicts_when_selector_comment(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
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
    requirements = parse_yaml_requirements(p, verbose=False)
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


def test_platforms_section_in_yaml(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
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


def test_platforms_section_in_yaml_similar_platforms(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
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


def test_conda_with_non_platform_comment(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
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


def test_pip_and_conda_different_name_on_linux64(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=True)
    expected = {
        "cuquantum-python": [
            Meta(
                name="cuquantum-python",
                which="conda",
                comment="# [linux64]",
                pin=None,
                identifier="c292b98a",
            ),
        ],
        "cuquantum": [
            Meta(
                name="cuquantum",
                which="pip",
                comment="# [linux64]",
                pin=None,
                identifier="c292b98a",
            ),
        ],
    }
    assert requirements.requirements == expected
    resolved = resolve_conflicts(requirements.requirements, requirements.platforms)
    expected_resolved = {
        "cuquantum-python": {
            "linux-64": {
                "conda": Meta(
                    name="cuquantum-python",
                    which="conda",
                    comment="# [linux64]",
                    pin=None,
                    identifier="c292b98a",
                ),
            },
        },
        "cuquantum": {
            "linux-64": {
                "pip": Meta(
                    name="cuquantum",
                    which="pip",
                    comment="# [linux64]",
                    pin=None,
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


def test_parse_requirements_with_ignore_pin(tmp_path: Path) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo >1
            """,
        ),
    )
    requirements = parse_yaml_requirements(p, ignore_pins=["foo"], verbose=False)
    assert requirements.requirements == {
        "foo": [
            Meta(
                name="foo",
                which="conda",
                comment=None,
                pin=None,
                identifier="17e5d607",
            ),
            Meta(
                name="foo",
                which="pip",
                comment=None,
                pin=None,
                identifier="17e5d607",
            ),
        ],
    }


def test_parse_requirements_with_skip_dependency(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(
        p,
        skip_dependencies=["foo", "bar"],
        verbose=False,
    )
    assert requirements.requirements == {
        "baz": [
            Meta(
                name="baz",
                which="conda",
                comment=None,
                pin=None,
                identifier="08fd8713",
            ),
            Meta(
                name="baz",
                which="pip",
                comment=None,
                pin=None,
                identifier="08fd8713",
            ),
        ],
    }


def test_pin_star_cuda(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p)
    assert requirements.requirements == {
        "qsimcirq": [
            Meta(
                name="qsimcirq",
                which="conda",
                comment="# [linux64]",
                pin="* cuda*",
                identifier="c292b98a",
            ),
            Meta(
                name="qsimcirq",
                which="conda",
                comment="# [arm64]",
                pin="* cpu*",
                identifier="489f33e0",
            ),
        ],
    }


def test_parse_requirements_with_overwrite_pins(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(
        p,
        overwrite_pins=["foo=1", "bar * cpu*"],
        verbose=False,
    )
    assert requirements.requirements == {
        "foo": [
            Meta(
                name="foo",
                which="conda",
                comment=None,
                pin="=1",
                identifier="17e5d607",
            ),
            Meta(
                name="foo",
                which="pip",
                comment=None,
                pin="=1",
                identifier="17e5d607",
            ),
        ],
        "bar": [
            Meta(
                name="bar",
                which="conda",
                comment=None,
                pin="* cpu*",
                identifier="5eb93b8c",
            ),
        ],
    }


def test_duplicate_names_different_platforms(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(
        p,
        overwrite_pins=["foo=1", "bar * cpu*"],
        verbose=False,
    )
    assert requirements.requirements == {
        "ray": [
            Meta(
                name="ray",
                which="pip",
                comment="# [arm64]",
                pin=None,
                identifier="1b26c5b2",
            ),
            Meta(
                name="ray",
                which="pip",
                comment="# [linux64]",
                pin=None,
                identifier="dd6a8aaf",
            ),
        ],
        "ray-core": [
            Meta(
                name="ray-core",
                which="conda",
                comment="# [linux64]",
                pin=None,
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


def test_with_unused_platform(tmp_path: Path) -> None:
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
    requirements = parse_yaml_requirements(p, verbose=False)
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
