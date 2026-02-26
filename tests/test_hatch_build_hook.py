"""Tests for the Hatch build hook that embeds UniDep metadata."""

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING

from unidep._artifact_metadata import UNIDEP_METADATA_FILENAME
from unidep._hatch_integration import UnidepBuildHook

if TYPE_CHECKING:
    from pathlib import Path


class _StubHatchMetadata:
    """Minimal stand-in for ``hatchling.metadata.core.ProjectMetadata``."""

    version = "1.2.3"


def _make_hook(
    root: str,
    directory: str,
) -> UnidepBuildHook:
    """Construct a ``UnidepBuildHook`` with stubbed-out hatch internals."""
    hook = object.__new__(UnidepBuildHook)
    # Set private attrs that the base ``BuildHookInterface.__init__`` normally
    # sets.  We bypass ``__init__`` to avoid requiring a full hatch plugin
    # manager.
    hook._BuildHookInterface__root = root  # type: ignore[attr-defined]
    hook._BuildHookInterface__config = {}  # type: ignore[attr-defined]
    hook._BuildHookInterface__build_config = None  # type: ignore[attr-defined]
    hook._BuildHookInterface__metadata = _StubHatchMetadata()  # type: ignore[attr-defined]
    hook._BuildHookInterface__directory = directory  # type: ignore[attr-defined]
    hook._BuildHookInterface__target_name = "wheel"  # type: ignore[attr-defined]
    hook._BuildHookInterface__app = None  # type: ignore[attr-defined]
    return hook


def test_unidep_build_hook_writes_metadata(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            channels:
              - conda-forge
            dependencies:
              - pip: requests >=2
            platforms:
              - linux-64
            """,
        ),
    )
    (project_root / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "demo-package"
            version = "1.2.3"
            """,
        ),
    )

    output_dir = tmp_path / "build_output"
    output_dir.mkdir()

    hook = _make_hook(root=str(project_root), directory=str(output_dir))
    build_data: dict = {"extra_metadata": {}}

    hook.initialize("standard", build_data)

    # The hook should have written the metadata file under `output_dir/.unidep/`
    metadata_file = output_dir / ".unidep" / UNIDEP_METADATA_FILENAME
    assert metadata_file.exists()

    payload = json.loads(metadata_file.read_text())
    assert payload["schema_version"] == 1
    assert payload["project"] == "demo-package"
    assert payload["version"] == "1.2.3"
    assert payload["channels"] == ["conda-forge"]
    assert "linux-64" in payload["platforms"]

    # build_data must have been updated with the extra_metadata mapping
    assert len(build_data["extra_metadata"]) == 1
    assert build_data["extra_metadata"][str(metadata_file)] == UNIDEP_METADATA_FILENAME


def test_unidep_build_hook_no_requirements_file(tmp_path: Path) -> None:
    """Hook should silently return when no requirements file exists."""
    project_root = tmp_path / "empty_project"
    project_root.mkdir()

    output_dir = tmp_path / "build_output"
    output_dir.mkdir()

    hook = _make_hook(root=str(project_root), directory=str(output_dir))
    build_data: dict = {"extra_metadata": {}}

    hook.initialize("standard", build_data)

    # No metadata should be written
    assert not (output_dir / ".unidep").exists()
    assert build_data["extra_metadata"] == {}
