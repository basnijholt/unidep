"""Tests for UniDep artifact metadata helpers."""

from __future__ import annotations

import json
import zipfile
from typing import TYPE_CHECKING, Any, get_args

import pytest

from unidep._artifact_metadata import (
    UNIDEP_METADATA_FILENAME,
    UnidepMetadataError,
    build_unidep_metadata,
    extract_unidep_metadata_from_wheel,
    parse_unidep_metadata,
    select_unidep_dependencies,
)
from unidep.platform_definitions import Platform

if TYPE_CHECKING:
    from pathlib import Path


def _sample_metadata() -> dict[str, object]:
    return {
        "schema_version": 1,
        "project": "demo-package",
        "version": "1.2.3",
        "channels": ["conda-forge"],
        "platforms": {
            "linux-64": {"conda": ["qsimcirq * cuda*"], "pip": ["requests>=2"]},
            "osx-arm64": {"conda": [], "pip": ["requests>=2"]},
        },
        "extras": {
            "dev": {
                "linux-64": {"conda": [], "pip": ["pytest>=8"]},
                "osx-arm64": {"conda": [], "pip": ["pytest>=8"]},
            },
        },
    }


def test_parse_and_select_unidep_metadata() -> None:
    metadata = parse_unidep_metadata(_sample_metadata())
    selected = select_unidep_dependencies(
        metadata,
        platform="linux-64",
        extras=["dev", "missing-extra"],
    )
    assert selected.channels == ["conda-forge"]
    assert selected.conda == ["qsimcirq * cuda*"]
    assert selected.pip == ["requests>=2", "pytest>=8"]
    assert selected.missing_extras == ["missing-extra"]


def test_parse_unidep_metadata_rejects_invalid_schema() -> None:
    bad = _sample_metadata()
    bad["schema_version"] = 999
    with pytest.raises(UnidepMetadataError, match="Unsupported UniDep metadata schema"):
        parse_unidep_metadata(bad)


def test_parse_unidep_metadata_rejects_non_object() -> None:
    with pytest.raises(UnidepMetadataError, match="must be a JSON object"):
        parse_unidep_metadata([])


@pytest.mark.parametrize(
    ("mutate", "error_match"),
    [
        (
            lambda data: data.update({"project": ""}),
            r"`project` must be a non-empty string",
        ),
        (
            lambda data: data.update({"version": ""}),
            r"`version` must be a non-empty string",
        ),
        (
            lambda data: data.update({"channels": [1]}),
            r"`channels` must be a list of strings",
        ),
        (
            lambda data: data.update({"platforms": []}),
            r"`platforms` must be a mapping of platforms",
        ),
        (
            lambda data: data.update(
                {"platforms": {"beos-1": {"conda": [], "pip": []}}},
            ),
            r"Unsupported platform `beos-1` in `platforms`",
        ),
        (
            lambda data: data.update({"platforms": {"linux-64": []}}),
            r"must be an object",
        ),
        (
            lambda data: data.update(
                {"platforms": {"linux-64": {"conda": [1], "pip": []}}},
            ),
            r"`platforms\.linux-64\.conda` must be a list of strings",
        ),
        (
            lambda data: data.update({"extras": []}),
            r"`extras` must be an object mapping extra names to platforms",
        ),
        (
            lambda data: data.update(
                {"extras": {"": {"linux-64": {"conda": [], "pip": []}}}},
            ),
            r"`extras` keys must be non-empty strings",
        ),
    ],
)
def test_parse_unidep_metadata_rejects_invalid_fields(
    mutate: Any,
    error_match: str,
) -> None:
    bad = _sample_metadata()
    mutate(bad)
    with pytest.raises(UnidepMetadataError, match=error_match):
        parse_unidep_metadata(bad)


def test_extract_unidep_metadata_from_wheel(tmp_path: Path) -> None:
    wheel = tmp_path / "demo_package-1.2.3-py3-none-any.whl"
    metadata_path = "demo_package-1.2.3.dist-info/unidep.json"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr(metadata_path, json.dumps(_sample_metadata()))

    metadata = extract_unidep_metadata_from_wheel(wheel)
    assert metadata is not None
    assert metadata.project == "demo-package"
    assert metadata.version == "1.2.3"


def test_extract_unidep_metadata_from_hatch_extra_metadata(tmp_path: Path) -> None:
    wheel = tmp_path / "demo_package-1.2.3-py3-none-any.whl"
    metadata_path = (
        f"demo_package-1.2.3.dist-info/extra_metadata/{UNIDEP_METADATA_FILENAME}"
    )
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr(metadata_path, json.dumps(_sample_metadata()))

    metadata = extract_unidep_metadata_from_wheel(wheel)
    assert metadata is not None
    assert metadata.project == "demo-package"


def test_extract_unidep_metadata_from_wheel_returns_none_when_missing(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "demo_package-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("demo_package-1.2.3.dist-info/METADATA", "Name: demo-package")
    assert extract_unidep_metadata_from_wheel(wheel) is None


def test_select_unidep_dependencies_missing_base_platform_raises() -> None:
    metadata = parse_unidep_metadata(_sample_metadata())
    with pytest.raises(UnidepMetadataError, match="is not present in UniDep metadata"):
        select_unidep_dependencies(metadata, platform="linux-aarch64")


def test_select_unidep_dependencies_marks_extra_missing_platform() -> None:
    raw = _sample_metadata()
    raw["extras"] = {
        "dev": {
            "osx-arm64": {"conda": [], "pip": ["pytest>=8"]},
        },
    }
    metadata = parse_unidep_metadata(raw)
    selected = select_unidep_dependencies(
        metadata,
        platform="linux-64",
        extras=["dev"],
    )
    assert selected.missing_extras == ["dev"]


def test_build_unidep_metadata(tmp_path: Path) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        """\
channels:
  - conda-forge
dependencies:
  - conda: qsimcirq * cuda*
  - pip: requests >=2
optional_dependencies:
  dev:
    - pip: pytest >=8
platforms:
  - linux-64
  - osx-arm64
""",
    )

    metadata = build_unidep_metadata(
        req_file,
        project="demo-package",
        version="1.2.3",
    )

    assert metadata["schema_version"] == 1
    assert metadata["project"] == "demo-package"
    assert metadata["version"] == "1.2.3"
    assert metadata["channels"] == ["conda-forge"]
    assert set(metadata["platforms"]) == {"linux-64", "osx-arm64"}
    assert "dev" in metadata.get("extras", {})


def test_build_unidep_metadata_defaults_to_all_platforms(tmp_path: Path) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        """\
channels:
  - conda-forge
dependencies:
  - pip: requests >=2
""",
    )

    metadata = build_unidep_metadata(
        req_file,
        project="demo-package",
        version="1.2.3",
    )

    assert set(metadata["platforms"]) == set(get_args(Platform))
