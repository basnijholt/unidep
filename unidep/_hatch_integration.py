"""unidep - Unified Conda and Pip requirements management.

This module contains the Hatchling integration.
"""

from __future__ import annotations

from pathlib import Path

from hatchling.metadata.plugin.interface import MetadataHookInterface
from hatchling.plugin import hookimpl

from unidep._setuptools_integration import _deps
from unidep.utils import (
    parse_folder_or_filename,
)

__all__ = ["UnidepRequirementsMetadataHook"]


class UnidepRequirementsMetadataHook(MetadataHookInterface):
    """Hatch hook to populate ``'project.depencencies'`` from ``requirements.yaml`` or ``pyproject.toml``."""  # noqa: E501

    PLUGIN_NAME = "unidep"

    def _skip_local_dependencies(self) -> bool | None:
        """Return whether local direct references should be omitted from metadata."""
        for key in ("skip-local-dependencies", "skip_local_dependencies"):
            value = self.config.get(key)
            if value is not None:
                return bool(value)
        return None

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

        skip_local_dependencies = self._skip_local_dependencies()
        include_local_dependencies = None
        if skip_local_dependencies is not None:
            include_local_dependencies = not skip_local_dependencies

        deps = _deps(
            requirements_file,
            include_local_dependencies=include_local_dependencies,
        )
        metadata["dependencies"] = deps.dependencies
        if "optional-dependencies" not in metadata.get("dynamic", []):
            return
        metadata["optional-dependencies"] = deps.extras


@hookimpl
def hatch_register_metadata_hook() -> type[UnidepRequirementsMetadataHook]:
    return UnidepRequirementsMetadataHook
