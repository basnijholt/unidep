"""unidep tests."""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from ruamel.yaml import YAML

from unidep import (
    CondaEnvironmentSpec,
    Meta,
    _build_pep508_environment_marker,
    _conda_lock_command,
    _extract_name_and_pin,
    _identify_current_platform,
    _install_command,
    _remove_top_comments,
    create_conda_env_specification,
    escape_unicode,
    extract_matching_platforms,
    filter_python_dependencies,
    find_requirements_files,
    get_python_dependencies,
    parse_yaml_requirements,
    resolve_conflicts,
    write_conda_environment_file,
)


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
    results = find_requirements_files(tmp_path, depth=1, verbose=True)

    # Convert results to absolute paths for comparison
    absolute_results = sorted(str(p.resolve()) for p in results)
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
    assert len(find_requirements_files(tmp_path, depth=1)) == 2  # noqa: PLR2004

    # Test depth=2
    assert len(find_requirements_files(tmp_path, depth=2)) == 3  # noqa: PLR2004

    # Test depth=3
    assert len(find_requirements_files(tmp_path, depth=3)) == 4  # noqa: PLR2004

    # Test depth=4 (or more)
    assert len(find_requirements_files(tmp_path, depth=4)) == 4  # noqa: PLR2004


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
    requirements = parse_yaml_requirements([p], verbose=False)
    assert requirements.requirements == {
        "foo": [
            Meta(name="foo", which="conda", comment="# [linux64]", pin=">1"),
            Meta(name="foo", which="pip", comment="# [linux64]", pin=">1"),
            Meta(name="foo", which="conda", comment="# [unix]"),
            Meta(name="foo", which="pip", comment="# [unix]"),
        ],
        "bar": [
            Meta(name="bar", which="conda", comment=None, pin=">1"),
            Meta(name="bar", which="pip", comment=None, pin=">1"),
            Meta(name="bar", which="conda", comment=None),
            Meta(name="bar", which="pip", comment=None),
        ],
    }


@pytest.mark.parametrize("verbose", [True, False])
def test_generate_conda_env_file(
    tmp_path: Path,
    verbose: bool,  # noqa: FBT001
    setup_test_files: tuple[Path, Path],
) -> None:
    output_file = tmp_path / "environment.yaml"
    requirements = parse_yaml_requirements(setup_test_files, verbose=verbose)
    resolved_requirements = resolve_conflicts(requirements.requirements)
    env_spec = create_conda_env_specification(
        resolved_requirements,
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
    requirements = parse_yaml_requirements(setup_test_files)
    resolved_requirements = resolve_conflicts(requirements.requirements)
    env_spec = create_conda_env_specification(
        resolved_requirements,
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
    requirements = parse_yaml_requirements([p])
    resolved_requirements = resolve_conflicts(requirements.requirements)
    env = create_conda_env_specification(
        resolved_requirements,
        requirements.channels,
        requirements.platforms,
    )
    assert env.conda == [
        {"sel(osx)": "yolo"},
        {"sel(linux)": "foo"},
        {"sel(win)": "bar"},
    ]
    expected_pip = [
        "pip-package",
        "pip-package2; sys_platform == 'darwin' and platform_machine == 'arm64'",
    ]
    assert env.pip == expected_pip

    # Test on two platforms
    env = create_conda_env_specification(
        resolved_requirements,
        requirements.channels,
        ["osx-arm64", "win-64"],
    )
    assert env.conda == [{"sel(osx)": "yolo"}, {"sel(win)": "bar"}]
    assert env.pip == expected_pip

    # Test with comment selector
    env = create_conda_env_specification(
        resolved_requirements,
        requirements.channels,
        ["osx-arm64", "win-64"],
        selector="comment",
    )
    assert env.conda == ["yolo", "bar"]
    assert env.pip == expected_pip
    write_conda_environment_file(env, str(tmp_path / "environment.yaml"))
    with (tmp_path / "environment.yaml").open() as f:
        text = "".join(f.readlines())
        assert "- yolo  # [arm64]" in text
        assert "- bar # [win64]" in text

    with pytest.raises(ValueError, match="Invalid platform"):
        create_conda_env_specification(
            resolved_requirements,
            requirements.channels,
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

    parse_yaml_requirements([f], verbose=True)
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
    requirements_with_comments = parse_yaml_requirements([p], verbose=False)
    assert requirements_with_comments.channels == ["conda-forge", "defaults"]


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
    requirements_with_comments = parse_yaml_requirements([p], verbose=False)
    assert requirements_with_comments.requirements == {
        "yolo": [
            Meta(name="yolo", which="conda", comment="# [osx]"),
            Meta(name="yolo", which="pip", comment="# [osx]"),
        ],
        "foo": [
            Meta(name="foo", which="conda", comment="# [linux]"),
            Meta(name="foo", which="pip", comment="# [linux]"),
        ],
        "bar": [
            Meta(name="bar", which="conda", comment="# [win]"),
            Meta(name="bar", which="pip", comment="# [win]"),
        ],
        "baz": [
            Meta(name="baz", which="conda", comment="#"),
            Meta(name="baz", which="pip", comment="#"),
        ],
        "pip-package": [Meta(name="pip-package", which="pip")],
        "pip-package2": [
            Meta(name="pip-package2", which="pip", comment="# [osx]"),
        ],
    }


def test_extract_matching_platforms() -> None:
    # Test with a line having a linux selector
    content_linux = "dependency1  # [linux]"
    assert set(extract_matching_platforms(content_linux)) == {
        "linux-64",
        "linux-aarch64",
        "linux-ppc64le",
    }

    # Test with a line having a win selector
    content_win = "dependency2  # [win]"
    assert set(extract_matching_platforms(content_win)) == {"win-64"}

    # Test with a line having an osx64 selector
    content_osx64 = "dependency3  # [osx64]"
    assert set(extract_matching_platforms(content_osx64)) == {"osx-64"}

    # Test with a line having no selector
    content_none = "dependency4"
    assert extract_matching_platforms(content_none) == []

    # Test with a comment line
    content_comment = "# This is a comment"
    assert extract_matching_platforms(content_comment) == []

    # Test with a line having a unix selector
    content_unix = "dependency5  # [unix]"
    expected_unix = {
        "linux-64",
        "linux-aarch64",
        "linux-ppc64le",
        "osx-64",
        "osx-arm64",
    }
    assert set(extract_matching_platforms(content_unix)) == expected_unix

    # Test with a line having multiple selectors
    content_multi = "dependency7  # [linux64 unix]"
    expected_multi = {
        "linux-64",
        "linux-aarch64",
        "linux-ppc64le",
        "osx-64",
        "osx-arm64",
    }
    assert set(extract_matching_platforms(content_multi)) == expected_multi

    # Test with a line having multiple []
    content_multi = "dependency7  # [linux64] [win]"
    with pytest.raises(ValueError, match="Multiple bracketed selectors"):
        extract_matching_platforms(content_multi)

    incorrect_platform = "dependency8  # [unknown-platform]"
    with pytest.raises(ValueError, match="Unsupported platform"):
        extract_matching_platforms(incorrect_platform)


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
    sample_requirements = parse_yaml_requirements([p], verbose=False)
    package1 = Meta(name="package1", which="conda", comment="# [linux64]")
    package2 = Meta(name="package2", which="conda", comment="# [osx64]")
    package3 = Meta(name="package3", which="pip", comment=None)
    package4_pip = Meta(name="package4", which="pip", comment="# [unix]")
    common_package_conda = Meta(
        name="common_package",
        which="conda",
        comment="# [unix]",
    )
    common_package_pip = Meta(name="common_package", which="pip", comment="# [unix]")
    shared_package_win = Meta(name="shared_package", which="pip", comment="# [win64]")
    shared_package_linux = Meta(
        name="shared_package",
        which="conda",
        comment="# [linux64]",
    )

    assert sample_requirements.requirements == {
        "package1": [package1],
        "package2": [package2],
        "package3": [package3],
        "package4": [package4_pip],
        "common_package": [common_package_conda, common_package_pip],
        "shared_package": [shared_package_linux, shared_package_win],
    }

    resolved = resolve_conflicts(sample_requirements.requirements)
    assert resolved == {
        "package1": {"linux-64": {"conda": package1}},
        "package2": {"osx-64": {"conda": package2}},
        "package3": {None: {"pip": package3}},
        "package4": {
            "osx-64": {"pip": package4_pip},
            "linux-64": {"pip": package4_pip},
            "linux-aarch64": {"pip": package4_pip},
            "linux-ppc64le": {"pip": package4_pip},
            "osx-arm64": {"pip": package4_pip},
        },
        "common_package": {
            "osx-64": {"conda": common_package_conda, "pip": common_package_pip},
            "linux-64": {"conda": common_package_conda, "pip": common_package_pip},
            "linux-aarch64": {"conda": common_package_conda, "pip": common_package_pip},
            "linux-ppc64le": {"conda": common_package_conda, "pip": common_package_pip},
            "osx-arm64": {"conda": common_package_conda, "pip": common_package_pip},
        },
        "shared_package": {
            "win-64": {"pip": shared_package_win},
            "linux-64": {"conda": shared_package_linux},
        },
    }
    # Pip
    pip_deps = filter_python_dependencies(resolved)
    assert pip_deps == [
        "common_package; sys_platform == 'linux' or sys_platform == 'darwin'",
        "package3",
        "package4; sys_platform == 'linux' or sys_platform == 'darwin'",
        "shared_package; sys_platform == 'win32' and platform_machine == 'AMD64'",
    ]

    # Conda
    conda_env_spec = create_conda_env_specification(
        resolved,
        channels=sample_requirements.channels,
        platforms=sample_requirements.platforms,
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


def test__build_pep508_environment_marker() -> None:
    # Test with a single platform
    assert (
        _build_pep508_environment_marker(["linux-64"])
        == "sys_platform == 'linux' and platform_machine == 'x86_64'"
    )

    # Test with multiple platforms
    assert (
        _build_pep508_environment_marker(["linux-64", "osx-64"])
        == "sys_platform == 'linux' and platform_machine == 'x86_64' or sys_platform == 'darwin' and platform_machine == 'x86_64'"
    )

    # Test with an empty list
    assert not _build_pep508_environment_marker([])

    # Test with a platform not in PEP508_MARKERS
    assert not _build_pep508_environment_marker(["unknown-platform"])  # type: ignore[list-item]

    # Test with a mix of valid and invalid platforms
    assert (
        _build_pep508_environment_marker(["linux-64", "unknown-platform"])  # type: ignore[list-item]
        == "sys_platform == 'linux' and platform_machine == 'x86_64'"
    )


def test_detect_platform() -> None:
    with patch("platform.system", return_value="Linux"), patch(
        "platform.machine",
        return_value="x86_64",
    ):
        assert _identify_current_platform() == "linux-64"

    with patch("platform.system", return_value="Linux"), patch(
        "platform.machine",
        return_value="aarch64",
    ):
        assert _identify_current_platform() == "linux-aarch64"

    with patch("platform.system", return_value="Darwin"), patch(
        "platform.machine",
        return_value="x86_64",
    ):
        assert _identify_current_platform() == "osx-64"

    with patch("platform.system", return_value="Darwin"), patch(
        "platform.machine",
        return_value="arm64",
    ):
        assert _identify_current_platform() == "osx-arm64"

    with patch("platform.system", return_value="Windows"), patch(
        "platform.machine",
        return_value="AMD64",
    ):
        assert _identify_current_platform() == "win-64"

    with patch("platform.system", return_value="Linux"), patch(
        "platform.machine",
        return_value="unknown",
    ), pytest.raises(ValueError, match="Unsupported Linux architecture"):
        _identify_current_platform()

    with patch("platform.system", return_value="Darwin"), patch(
        "platform.machine",
        return_value="unknown",
    ), pytest.raises(ValueError, match="Unsupported macOS architecture"):
        _identify_current_platform()

    with patch("platform.system", return_value="Windows"), patch(
        "platform.machine",
        return_value="unknown",
    ), pytest.raises(ValueError, match="Unsupported Windows architecture"):
        _identify_current_platform()

    with patch("platform.system", return_value="Linux"), patch(
        "platform.machine",
        return_value="ppc64le",
    ):
        assert _identify_current_platform() == "linux-ppc64le"

    with patch("platform.system", return_value="Unknown"), patch(
        "platform.machine",
        return_value="x86_64",
    ), pytest.raises(ValueError, match="Unsupported operating system"):
        _identify_current_platform()


def test_extract_name_and_pin() -> None:
    # Test with version pin
    assert _extract_name_and_pin("numpy >=1.20.0") == ("numpy", ">=1.20.0")
    assert _extract_name_and_pin("pandas<2.0,>=1.1.3") == ("pandas", "<2.0,>=1.1.3")

    # Test with multiple version conditions
    assert _extract_name_and_pin("scipy>=1.2.3, <1.3") == ("scipy", ">=1.2.3, <1.3")

    # Test with no version pin
    assert _extract_name_and_pin("matplotlib") == ("matplotlib", None)

    # Test with whitespace variations
    assert _extract_name_and_pin("requests >= 2.25") == ("requests", ">= 2.25")

    # Test when installing from a URL
    url = "https://github.com/python-adaptive/adaptive.git@main"
    pin = f"@ git+{url}"
    assert _extract_name_and_pin(f"adaptive {pin}") == ("adaptive", pin)

    # Test with invalid input
    with pytest.raises(ValueError, match="Invalid package string"):
        _extract_name_and_pin(">=1.20.0 numpy")


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
    requirements = parse_yaml_requirements([p], verbose=False)
    assert requirements.requirements == {
        "foo": [
            Meta(name="foo", which="conda", comment="# [linux64]", pin=">1"),
            Meta(name="foo", which="pip", comment="# [linux64]", pin=">1"),
            Meta(name="foo", which="conda", comment="# [linux64]", pin=None),
            Meta(name="foo", which="pip", comment="# [linux64]", pin=None),
        ],
        "bar": [
            Meta(name="bar", which="conda", comment=None, pin=None),
            Meta(name="bar", which="pip", comment=None, pin=None),
        ],
    }
    resolved = resolve_conflicts(requirements.requirements)
    assert resolved == {
        "foo": {
            "linux-64": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment="# [linux64]",
                    pin=">1",
                ),
                "pip": Meta(name="foo", which="pip", comment="# [linux64]", pin=">1"),
            },
        },
        "bar": {
            None: {
                "conda": Meta(name="bar", which="conda", comment=None, pin=None),
                "pip": Meta(name="bar", which="pip", comment=None, pin=None),
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
                - foo <1 # [linux]
            """,
        ),
    )
    requirements = parse_yaml_requirements([p], verbose=False)
    assert requirements.requirements == {
        "foo": [
            Meta(name="foo", which="conda", comment="# [linux64]", pin=">1"),
            Meta(name="foo", which="pip", comment="# [linux64]", pin=">1"),
            Meta(name="foo", which="conda", comment="# [linux]", pin="<1"),
            Meta(name="foo", which="pip", comment="# [linux]", pin="<1"),
        ],
    }
    resolved = resolve_conflicts(requirements.requirements)
    assert resolved == {
        "foo": {
            "linux-64": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment="# [linux64]",
                    pin=">1",
                ),
                "pip": Meta(name="foo", which="pip", comment="# [linux64]", pin=">1"),
            },
            "linux-aarch64": {
                "conda": Meta(name="foo", which="conda", comment="# [linux]", pin="<1"),
                "pip": Meta(name="foo", which="pip", comment="# [linux]", pin="<1"),
            },
            "linux-ppc64le": {
                "conda": Meta(name="foo", which="conda", comment="# [linux]", pin="<1"),
                "pip": Meta(name="foo", which="pip", comment="# [linux]", pin="<1"),
            },
        },
    }
    with pytest.warns(UserWarning, match="Dependency Conflict on"):
        env_spec = create_conda_env_specification(
            resolved,
            requirements.channels,
            requirements.platforms,
        )
    assert env_spec.conda == [{"sel(linux)": "foo >1"}]
    assert env_spec.pip == []

    python_deps = filter_python_dependencies(resolved)
    assert python_deps == [
        "foo <1; sys_platform == 'linux' and platform_machine == 'aarch64'",
        "foo <1; sys_platform == 'linux' and platform_machine == 'ppc64le'",
        "foo >1; sys_platform == 'linux' and platform_machine == 'x86_64'",
    ]


def test_expand_none_with_different_platforms(tmp_path: Path) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo >1 # [linux64]
                - foo >2
            """,
        ),
    )
    requirements = parse_yaml_requirements([p], verbose=False)
    assert requirements.requirements == {
        "foo": [
            Meta(name="foo", which="conda", comment="# [linux64]", pin=">1"),
            Meta(name="foo", which="pip", comment="# [linux64]", pin=">1"),
            Meta(name="foo", which="conda", comment=None, pin=">2"),
            Meta(name="foo", which="pip", comment=None, pin=">2"),
        ],
    }
    resolved = resolve_conflicts(requirements.requirements)
    assert resolved == {
        "foo": {
            "linux-64": {
                "conda": Meta(
                    name="foo",
                    which="conda",
                    comment="# [linux64]",
                    pin=">1",
                ),
                "pip": Meta(name="foo", which="pip", comment="# [linux64]", pin=">1"),
            },
            None: {
                "conda": Meta(name="foo", which="conda", comment=None, pin=">2"),
                "pip": Meta(name="foo", which="pip", comment=None, pin=">2"),
            },
        },
    }
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
    )
    assert env_spec.conda == [
        {"sel(linux)": "foo >1"},
        {"sel(osx)": "foo >2"},
        {"sel(win)": "foo >2"},
    ]

    assert env_spec.pip == []

    python_deps = filter_python_dependencies(resolved)
    assert python_deps == [
        "foo >1; sys_platform == 'linux' and platform_machine == 'x86_64'",
        "foo >2; sys_platform == 'darwin' and platform_machine == 'arm64'",
        "foo >2; sys_platform == 'darwin' and platform_machine == 'x86_64'",
        "foo >2; sys_platform == 'linux' and platform_machine == 'aarch64'",
        "foo >2; sys_platform == 'linux' and platform_machine == 'ppc64le'",
        "foo >2; sys_platform == 'win32' and platform_machine == 'AMD64'",
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
    requirements = parse_yaml_requirements([p], verbose=False)
    assert requirements.requirements == {
        "foo": [
            Meta(name="foo", which="conda", comment=None, pin="<1"),
            Meta(name="foo", which="pip", comment=None, pin=">1"),
        ],
    }
    resolved = resolve_conflicts(requirements.requirements)
    assert resolved == {
        "foo": {
            None: {
                "conda": Meta(name="foo", which="conda", comment=None, pin="<1"),
                "pip": Meta(name="foo", which="pip", comment=None, pin=">1"),
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
    requirements = parse_yaml_requirements([p], verbose=False)
    resolved = resolve_conflicts(requirements.requirements)
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
    requirements = parse_yaml_requirements([p], verbose=False)
    resolved = resolve_conflicts(requirements.requirements)
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
    requirements = parse_yaml_requirements([p], verbose=False)
    resolved = resolve_conflicts(requirements.requirements)
    python_deps = filter_python_dependencies(resolved, platforms=["linux-64"])
    assert python_deps == [
        "foo; sys_platform == 'linux' and platform_machine == 'x86_64'",
    ]


def test_escape_unicode() -> None:
    assert escape_unicode("foo\\n") == "foo\n"
    assert escape_unicode("foo\\t") == "foo\t"


def test_install_command(capsys: pytest.CaptureFixture) -> None:
    root = Path(__file__).parent.parent
    _install_command(
        conda_executable="",
        dry_run=True,
        editable=False,
        file=root / "example" / "project1" / "requirements.yaml",
        verbose=False,
    )
    captured = capsys.readouterr()
    assert "Installing conda dependencies" in captured.out
    assert "Installing pip dependencies" in captured.out


@pytest.mark.parametrize("project", ["project1", "project2", "project3"])
def test_unidep_install_dry_run(project: str) -> None:
    # Path to the requirements file
    root = Path(__file__).parent.parent
    requirements_path = root / "example" / project

    # Ensure the requirements file exists
    assert requirements_path.exists(), "Requirements file does not exist"

    # Run the unidep install command
    result = subprocess.run(
        [  # noqa: S607, S603
            "unidep",
            "install",
            "--dry-run",
            str(requirements_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    # Check the output
    assert result.returncode == 0, "Command failed to execute successfully"
    if project in ("project1", "project2"):
        assert "📦 Installing conda dependencies with" in result.stdout
    assert "📦 Installing pip dependencies with" in result.stdout
    assert "📦 Installing project with" in result.stdout


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
    requirements = parse_yaml_requirements([p], verbose=False)
    resolved = resolve_conflicts(requirements.requirements)
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
    requirements = parse_yaml_requirements([p], verbose=False)
    resolved = resolve_conflicts(requirements.requirements)
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
                - foo <1 # [linux]
            """,
        ),
    )
    requirements = parse_yaml_requirements([p], verbose=False)
    resolved = resolve_conflicts(requirements.requirements)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
        selector="comment",
    )
    assert env_spec.conda == ["foo >1", "foo <1", "foo <1"]
    assert env_spec.pip == []

    write_conda_environment_file(env_spec, str(tmp_path / "environment.yaml"))

    with (tmp_path / "environment.yaml").open() as f:
        text = "".join(f.readlines())
        assert "- foo >1  # [linux64]" in text
        assert "- foo <1 # [aarch64]" in text
        assert "- foo <1 # [ppc64le]" in text

    # With just [unix]
    p = tmp_path / "requirements.yaml"
    p.write_text(
        textwrap.dedent(
            """\
            dependencies:
                - foo >1
                - foo <1 # [unix]
            """,
        ),
    )
    requirements = parse_yaml_requirements([p], verbose=False)
    resolved = resolve_conflicts(requirements.requirements)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
        selector="comment",
    )
    assert env_spec.conda == [
        "foo <1",
        "foo <1",
        "foo <1",
        "foo <1",
        "foo <1",
        "foo >1",
    ]
    assert env_spec.pip == []

    write_conda_environment_file(env_spec, str(tmp_path / "environment.yaml"))

    with (tmp_path / "environment.yaml").open() as f:
        text = "".join(f.readlines())
        assert "- foo <1  # [linux64]" in text
        assert "- foo <1 # [osx64]" in text
        assert "- foo <1 # [arm64]" in text
        assert "- foo <1 # [aarch64]" in text
        assert "- foo <1 # [ppc64le]" in text
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
    requirements = parse_yaml_requirements([p], verbose=False)
    resolved = resolve_conflicts(requirements.requirements)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
        selector="sel",
    )
    assert env_spec.conda == ["foo"]
    assert env_spec.pip == []
    assert env_spec.platforms == ["linux-64", "osx-arm64"]
    python_deps = filter_python_dependencies(resolved, platforms=requirements.platforms)
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
    requirements = parse_yaml_requirements([p], verbose=False)
    resolved = resolve_conflicts(requirements.requirements)
    env_spec = create_conda_env_specification(
        resolved,
        requirements.channels,
        requirements.platforms,
        selector="sel",
    )
    assert env_spec.conda == ["foo", {"sel(linux)": "yolo <1"}]
    assert env_spec.pip == []
    assert env_spec.platforms == ["linux-64", "linux-aarch64"]
    python_deps = filter_python_dependencies(resolved, platforms=requirements.platforms)
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


def test_conda_lock_command() -> None:
    simple_monorepo = Path(__file__).parent / "simple_monorepo"
    with patch("unidep._run_conda_lock", return_value=None):
        _conda_lock_command(
            depth=1,
            directory=simple_monorepo,
            platform=["linux-64", "osx-arm64"],
            verbose=False,
            only_global=False,
        )
    with YAML(typ="safe") as yaml:
        with (simple_monorepo / "project1" / "tmp.environment.yaml").open() as f:
            env1_tmp = yaml.load(f)
        with (simple_monorepo / "project2" / "tmp.environment.yaml").open() as f:
            env2_tmp = yaml.load(f)
    assert len(env1_tmp["dependencies"]) == 1
    assert len(env2_tmp["dependencies"]) == 1
    assert env1_tmp["dependencies"][0].split("=")[0] == "networkx"
    assert env2_tmp["dependencies"][0].split("=")[0] == "psutil"


def test_remove_top_comments(tmp_path: Path) -> None:
    test_file = tmp_path / "test_file.txt"
    test_file.write_text(
        "# Comment line 1\n# Comment line 2\nActual content line 1\nActual content line 2",
    )

    _remove_top_comments(test_file)

    with test_file.open("r") as file:
        content = file.read()

    assert content == "Actual content line 1\nActual content line 2"
