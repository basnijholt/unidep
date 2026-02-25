"""unidep - Unified Conda and Pip requirements management.

This module contains the Hatchling integration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.metadata.plugin.interface import MetadataHookInterface
from hatchling.plugin import hookimpl

from unidep._artifact_metadata import (
    UNIDEP_METADATA_FILENAME,
    build_unidep_metadata,
)
from unidep._setuptools_integration import _deps
from unidep.utils import (
    package_name_from_path,
    parse_folder_or_filename,
)

__all__ = ["UnidepBuildHook", "UnidepRequirementsMetadataHook"]


class UnidepRequirementsMetadataHook(MetadataHookInterface):
    """Hatch hook to populate ``'project.depencencies'`` from ``requirements.yaml`` or ``pyproject.toml``."""  # noqa: E501

    PLUGIN_NAME = "unidep"

    def update(self, metadata: dict) -> None:
        """Update the project table's metadata."""
        if "dependencies" not in metadata.get("dynamic", []):
            return
        project_root = Path.cwd()
        try:
            requirements_file = parse_folder_or_filename(project_root).path
        except FileNotFoundError:
            return
        if "dependencies" in metadata:
            error_msg = (
                "You have a `requirements.yaml` file in your project root or"
                " configured unidep in `pyproject.toml` with `[tool.unidep]`,"
                " but you are also using `[project.dependencies]`."
                " Please remove `[project.dependencies]`, you cannot use both."
            )
            raise RuntimeError(error_msg)

        deps = _deps(requirements_file)
        metadata["dependencies"] = deps.dependencies
        if "optional-dependencies" not in metadata.get("dynamic", []):
            return
        metadata["optional-dependencies"] = deps.extras


class UnidepBuildHook(BuildHookInterface):
    """Hatch build hook that embeds UniDep metadata in wheel dist-info."""

    PLUGIN_NAME = "unidep"

    def initialize(self, version: str, build_data: dict) -> None:
        project_root = Path(self.root)
        try:
            requirements_file = parse_folder_or_filename(project_root).path
        except FileNotFoundError:
            return

        payload = build_unidep_metadata(
            requirements_file,
            project=package_name_from_path(project_root),
            version=version,
            verbose=bool(os.getenv("UNIDEP_VERBOSE")),
        )
        output_dir = Path(self.directory) / ".unidep"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / UNIDEP_METADATA_FILENAME
        output_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

        # Hatch maps these files to `.dist-info/extra_metadata/<relative path>`.
        build_data.setdefault("extra_metadata", {})
        build_data["extra_metadata"][str(output_file)] = UNIDEP_METADATA_FILENAME


@hookimpl
def hatch_register_metadata_hook() -> type[UnidepRequirementsMetadataHook]:
    return UnidepRequirementsMetadataHook


@hookimpl
def hatch_register_build_hook() -> type[UnidepBuildHook]:
    return UnidepBuildHook
