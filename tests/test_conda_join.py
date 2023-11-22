"""conda_join tests."""
from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import yaml

from conda_join import (
    CondaEnvironmentSpec,
    Meta,
    ParsedRequirements,
    _build_pep508_environment_marker,
    _convert_to_commented_requirements,
    _extract_name_and_pin,
    _identify_current_platform,
    _remove_unsupported_platform_dependencies,
    _segregate_pip_conda_dependencies,
    create_conda_env_specification,
    extract_matching_platforms,
    find_requirements_files,
    get_python_dependencies,
    parse_requirements_deduplicate,
    parse_yaml_requirements,
    write_conda_environment_file,
)

if TYPE_CHECKING:
    from pathlib import Path


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


@pytest.mark.parametrize("verbose", [True, False])
def test_parse_requirements(
    verbose: bool,  # noqa: FBT001
    setup_test_files: tuple[Path, Path],
) -> None:
    combined_deps = parse_requirements_deduplicate(
        setup_test_files,
        verbose=verbose,
    )
    assert "numpy" in combined_deps.conda
    assert "mumps" in combined_deps.conda
    assert len(combined_deps.conda) == 2  # noqa: PLR2004
    assert len(combined_deps.pip) == 1
    assert "pandas" in combined_deps.pip


@pytest.mark.parametrize("verbose", [True, False])
def test_generate_conda_env_file(
    tmp_path: Path,
    verbose: bool,  # noqa: FBT001
    setup_test_files: tuple[Path, Path],
) -> None:
    output_file = tmp_path / "environment.yaml"
    combined_deps = parse_yaml_requirements(setup_test_files, verbose=verbose)
    env_spec = create_conda_env_specification(combined_deps)

    write_conda_environment_file(env_spec, str(output_file), verbose=verbose)

    with output_file.open() as f:
        env_data = yaml.safe_load(f)
        assert "dependencies" in env_data
        assert "numpy" in env_data["dependencies"]
        assert {"pip": ["pandas"]} in env_data["dependencies"]


def test_generate_conda_env_stdout(
    setup_test_files: tuple[Path, Path],
    capsys: pytest.CaptureFixture,
) -> None:
    combined_deps = parse_yaml_requirements(setup_test_files)
    env_spec = create_conda_env_specification(combined_deps)
    write_conda_environment_file(env_spec, None)

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
    env = create_conda_env_specification(requirements)
    assert env.conda == [
        {"sel(osx)": "yolo"},
        {"sel(linux)": "foo"},
        {"sel(win)": "bar"},
    ]
    assert env.pip == [
        "yolo; sys_platform == 'darwin' and platform_machine == 'arm64'",
        "foo; sys_platform == 'linux' and platform_machine == 'x86_64'",
        "pip-package",
        "pip-package2; sys_platform == 'darwin' and platform_machine == 'arm64'",
    ]


def test_verbose_output(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    f = tmp_path / "dir3" / "requirements.yaml"
    f.parent.mkdir()
    f.write_text("dependencies:\n  - scipy")

    find_requirements_files(tmp_path, verbose=True)
    captured = capsys.readouterr()
    assert "Scanning in" in captured.out
    assert str(tmp_path / "dir3") in captured.out

    parse_requirements_deduplicate([f], verbose=True)
    captured = capsys.readouterr()
    assert "Parsing" in captured.out
    assert str(f) in captured.out

    write_conda_environment_file(
        CondaEnvironmentSpec(channels=[], conda=[], pip=[]),
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


def test_extract_comment(tmp_path: Path) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text("dependencies:\n  - numpy # [osx]\n  - conda: mumps  # [linux]")

    requirements_with_comments = parse_yaml_requirements([p], verbose=False)
    reqs = _segregate_pip_conda_dependencies(requirements_with_comments)
    assert reqs.conda == {
        "numpy": Meta(name="numpy", comment="# [osx]"),
        "mumps": Meta(name="mumps", comment="# [linux]"),
    }
    commented_map = _convert_to_commented_requirements(reqs)
    assert commented_map.conda == ["numpy", "mumps"]


def test_channels(tmp_path: Path) -> None:
    p = tmp_path / "requirements.yaml"
    p.write_text("channels:\n  - conda-forge\n  - defaults")
    requirements_with_comments = parse_yaml_requirements([p], verbose=False)
    reqs = _segregate_pip_conda_dependencies(requirements_with_comments)
    assert reqs.conda == {}
    assert reqs.pip == {}
    assert reqs.channels == {"conda-forge", "defaults"}


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
    reqs = _segregate_pip_conda_dependencies(requirements_with_comments)
    assert reqs.conda == {
        "yolo": Meta(name="yolo", comment="# [osx]"),
        "foo": Meta(name="foo", comment="# [linux]"),
        "bar": Meta(name="bar", comment="# [win]"),
        "baz": Meta(name="baz", comment="#"),
    }
    assert reqs.pip == {
        "pip-package": Meta(name="pip-package", comment=None),
        "pip-package2": Meta(name="pip-package2", comment="# [osx]"),
    }
    _convert_to_commented_requirements(reqs)


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


def test_filter_pip_and_conda() -> None:
    # Setup a sample ParsedRequirements instance with platform selectors
    sample_requirements = ParsedRequirements(
        channels={"some-channel"},
        conda={
            "package1": Meta("package1", "# [linux]"),
            "package2": Meta("package2", "# [osx]"),
            "common_package": Meta("common_package", "# [unix]"),
            "shared_package": Meta(
                "shared_package",
                "# [linux]",
            ),  # Appears in both conda and pip with different selectors
        },
        pip={
            "package3": Meta("package3", "# [win]"),
            "package4": Meta("package4", None),
            "common_package": Meta("common_package", "# [unix]"),
            "shared_package": Meta(
                "shared_package",
                "# [win]",
            ),  # Appears in both conda and pip with different selectors
        },
    )

    assert _remove_unsupported_platform_dependencies(
        sample_requirements.conda,
        "linux-64",
    ) == {
        "package1": Meta("package1", "# [linux]"),
        "common_package": Meta("common_package", "# [unix]"),
        "shared_package": Meta("shared_package", "# [linux]"),
    }
    assert _remove_unsupported_platform_dependencies(
        sample_requirements.pip,
        "linux-64",
    ) == {
        "common_package": Meta("common_package", "# [unix]"),
        "package4": Meta("package4", None),
    }

    # Test filtering for pip on linux-64 platform
    expected_pip_linux = ParsedRequirements(
        channels={"some-channel"},
        conda={
            "package1": Meta("package1", "# [linux]"),
            "shared_package": Meta("shared_package", "# [linux]"),
        },
        pip={
            "common_package": Meta("common_package", "# [unix]"),
            "package4": Meta("package4", None),
        },
    )

    assert (
        _segregate_pip_conda_dependencies(sample_requirements, "pip", "linux-64")
        == expected_pip_linux
    )

    # Test filtering for conda on linux-64 platform
    expected_conda_linux = ParsedRequirements(
        channels={"some-channel"},
        conda={
            "package1": Meta("package1", "# [linux]"),
            "common_package": Meta("common_package", "# [unix]"),
            "shared_package": Meta("shared_package", "# [linux]"),
        },
        pip={"package4": Meta("package4", None)},
    )
    assert (
        _segregate_pip_conda_dependencies(sample_requirements, "conda", "linux-64")
        == expected_conda_linux
    )

    # Test with invalid pip_or_conda value
    with pytest.raises(ValueError, match="Invalid value for `pip_or_conda`"):
        _segregate_pip_conda_dependencies(sample_requirements, "invalid_value", "linux-64")  # type: ignore[arg-type]


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
                - baz
                - pip: pip-package
                - pip: pip-package2
            """,
        ),
    )
    requirements = parse_yaml_requirements([p], verbose=False)
    env_spec = create_conda_env_specification(requirements)
    assert env_spec.conda == [
        {"sel(linux)": "foo >1"},
        {"sel(linux)": "foo"},
        "bar",
        "baz",
    ]
    assert env_spec.pip == [
        "foo >1; sys_platform == 'linux' and platform_machine == 'x86_64'",
        "foo; sys_platform == 'linux' and platform_machine == 'x86_64'",
        "pip-package",
        "pip-package2",
    ]
