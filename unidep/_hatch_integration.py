"""unidep - Unified Conda and Pip requirements management.

This module contains the Hatchling integration.
"""
from __future__ import annotations

import os
from pathlib import Path

from hatchling.metadata.plugin.interface import MetadataHookInterface
from hatchling.plugin import hookimpl

from unidep._setuptools_integration import get_python_dependencies
from unidep.utils import (
    UnsupportedPlatformError,
    identify_current_platform,
    parse_folder_or_filename,
)

__all__ = ["UnidepRequirementsMetadataHook"]


class UnidepRequirementsMetadataHook(MetadataHookInterface):
    """Hatch hook to populate ``'project.depencencies'`` from ``requirements.yaml`` or ``pyproject.toml``."""  # noqa: E501

    PLUGIN_NAME = "unidep"

    def update(self, metadata: dict) -> None:
        """Update the project table's metadata."""
        if "dependencies" not in metadata.get("dynamic", []):
            return
        project_root = Path().resolve()
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

        try:
            platforms = [identify_current_platform()]
        except UnsupportedPlatformError:
            # We don't know the current platform, so we can't filter out.
            # This will result in selecting all platforms. But this is better
            # than failing.
            platforms = None

        skip_local_dependencies = bool(os.getenv("UNIDEP_SKIP_LOCAL_DEPS"))
        verbose = bool(os.getenv("UNIDEP_VERBOSE"))
        deps = get_python_dependencies(
            requirements_file,
            platforms=platforms,
            raises_if_missing=False,
            verbose=verbose,
            include_local_dependencies=not skip_local_dependencies,
        )
        metadata["dependencies"] = deps.dependencies
        if "optional-dependencies" not in metadata.get("dynamic", []):
            return
        metadata["optional-dependencies"] = deps.extras


@hookimpl
def hatch_register_metadata_hook() -> type[UnidepRequirementsMetadataHook]:
    return UnidepRequirementsMetadataHook
