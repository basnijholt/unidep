"""Tests for UniDep artifact metadata helpers."""

from __future__ import annotations

import json
import zipfile
from typing import TYPE_CHECKING

import pytest

from unidep._artifact_metadata import (
    UNIDEP_METADATA_FILENAME,
    UnidepMetadataError,
    build_unidep_metadata,
    extract_unidep_metadata_from_wheel,
    parse_unidep_metadata,
    select_unidep_dependencies,
)

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
