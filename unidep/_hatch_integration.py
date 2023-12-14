from __future__ import annotations

from pathlib import Path

from hatchling.metadata.plugin.interface import MetadataHookInterface
from hatchling.plugin import hookimpl

from unidep._setuptools_integration import get_python_dependencies
from unidep.utils import identify_current_platform

__all__ = ["UnidepRequirementsMetadataHook"]


class UnidepRequirementsMetadataHook(MetadataHookInterface):
    """Hatch hook to populate ``'project.depencencies'`` from ``requirements.yaml``."""

    PLUGIN_NAME = "unidep"

    def update(self, metadata: dict) -> None:
        """Update the project table's metadata."""
        project_root = Path().resolve()
        requirements_file = project_root / "requirements.yaml"
        if "dependencies" not in metadata.get("dynamic", []):
            return
        if not requirements_file.exists():
            return
        if "dependencies" in metadata:
            error_msg = (
                "You have a requirements.yaml file in your project root,"
                " but you are also using [project.dependencies]."
                " Please choose either requirements.yaml or"
                " [project.dependencies], but not both."
            )
            raise RuntimeError(error_msg)
        metadata["dependencies"] = get_python_dependencies(
            requirements_file,
            platforms=[identify_current_platform()],
            raises_if_missing=False,
        )


@hookimpl
def hatch_register_metadata_hook() -> type[UnidepRequirementsMetadataHook]:
    return UnidepRequirementsMetadataHook
